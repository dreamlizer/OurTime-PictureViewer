"""本地照片资料库。原照片只读，人工信息与原始信息分别保存。"""
import os
os.environ['NO_ALBUMENTATIONS_UPDATE'] = '1'
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent / 'runtime'))
import argparse
import hashlib
import io
import json
import csv
import logging
import math
import mimetypes
import sqlite3
import threading
import time
import uuid
import re
import zipfile
import subprocess
from contextlib import ExitStack, asynccontextmanager, contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, ExifTags, IptcImagePlugin
from pydantic import BaseModel, Field
from metadata_reader import read_metadata, close_readers
from geo_labels import PlaceIndex
from object_labels import classify_image, classify_images, model_ready, runtime_name as object_runtime, BATCH_SIZE
from library_db import (
    ACTIVE_ASSET, ASSET_LIST_COLUMNS, init_schema, load_place_rules,
    migrate_place_overrides, place_rules_version, recover_interrupted_jobs,
    register_collations, upsert_place_rule,
)
from browse_queries import directory_predicate as directory_clause, fetch_people, fetch_photos, nearby_photo_spec

mimetypes.add_type('font/ttf', '.ttf')

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('PHOTO_LIBRARY_DATA', str(BASE / 'data'))).resolve()
FACE_LABEL_DIR = DATA / '人名标签'
FACE_LABEL_FILES = {f'{i}.png' for i in range(1, 10)}
MODEL_ROOT = Path(os.environ.get('PHOTO_MODEL_ROOT', 'G:/CodexModels/insightface'))
GEO_ROOT = Path(os.environ.get('PHOTO_GEO_ROOT', 'G:/CodexModels/geo'))
PLACES = PlaceIndex(GEO_ROOT)
PREVIEW_LOCK = threading.BoundedSemaphore(2)
DB = DATA / 'library.sqlite3'
EXTENSIONS = {'.jpg','.jpeg','.png','.webp','.bmp','.gif','.tif','.tiff','.heic','.heif','.avif','.dng','.cr2','.cr3','.nef','.arw','.raf','.rw2'}
EXCLUDE = {'$recycle.bin','system volume information','windows','program files','program files (x86)','programdata','appdata','node_modules','.git','.venv','venv','__pycache__','$windows.~bt'}
SCAN_LOCK = threading.Lock()
STOP = threading.Event()
FACE_ENGINE = None
FACE_RUNTIME = None
FACE_INDEX = None
FACE_INDEX_LOCK = threading.Lock()
FACE_INDEX_GEN = 0
FACE_GROUP_THRESHOLD = 0.52
FACE_MATCH_MARGIN = 0.08
OBJECT_JOB={'status':'idle','processed':0,'limit':0,'tagged':0,'errors':0,'message':'','started_at':None,'finished_at':None}
OBJECT_JOB_LOCK=threading.Lock()
GEO = None
EXIFTOOL = next((BASE/'tools'/'exiftool').glob('*/exiftool.exe'),None)
ASSET_LOCKS=[threading.RLock() for _ in range(128)]
OPERATION_LOCKS=[threading.RLock() for _ in range(128)]
GEO_LOCK=threading.Lock()
RULE_LOCK=threading.RLock()
RULES=[]
PROGRESS_LOCK=threading.Lock()
PROGRESS={
    'job_id':None,'samples':deque(maxlen=2000),'metadata_reads':0,
    'phase':'','current_stage':'','current_path':'','total':0,
    'discovered':0,'processed':0,'face_photos':0,'faces_found':0,'face_seconds':0.0,
}
STATUS_CACHE={'at':0,'payload':None}
STATUS_CACHE_LOCK=threading.Lock()
APP_OWNER = None
APP_INITIALIZED = False
APP_INITIALIZE_LOCK = threading.RLock()
APP_PORT = int(os.environ.get('PHOTO_LIBRARY_PORT', '8765'))
RUNTIME_VERSION = 'not-started'
SCAN_THREAD = None
UVICORN_SERVER = None
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF = True
except ImportError:
    HEIF = False

@contextmanager
def db():
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    register_collations(conn)
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=60000')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA temp_store=MEMORY')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db() as c:
        init_schema(c)
        migrate_place_overrides(c, DATA)

def current_place_rules():
    with db() as c:
        return load_place_rules(c), place_rules_version(c)

def attach_place_rules():
    PLACES.overrides_loader = current_place_rules
    PLACES.overrides = None
    PLACES.override_version = None
    PLACES.nearest.cache_clear()

class DataOwner:
    """One OS-level writer lease for one canonical data directory."""
    def __init__(self, data_dir):
        self.data_dir=Path(data_dir).resolve()
        self.path=self.data_dir/'writer.owner.lock'
        self.handle=None

    def acquire(self):
        self.data_dir.mkdir(parents=True,exist_ok=True)
        handle=open(self.path,'a+b')
        handle.seek(0,os.SEEK_END)
        if handle.tell()==0:
            handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,IOError) as exc:
            handle.close()
            raise RuntimeError(f'资料库正在由另一个拾光实例使用：{self.data_dir}') from exc
        self.handle=handle

    def release(self):
        handle=self.handle
        if not handle:return
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        finally:
            handle.close();self.handle=None


def process_command_line(pid):
    if pid<=0:return ''
    if os.name!='nt':
        try:return (Path('/proc')/str(pid)/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
        except OSError:return ''
    command=(
        "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d' "
        "-ErrorAction SilentlyContinue;if($p){[Console]::OutputEncoding="
        "[Text.UTF8Encoding]::new();$p.CommandLine}" % pid
    )
    try:
        return subprocess.run(
            ['powershell','-NoProfile','-Command',command],
            capture_output=True,text=True,encoding='utf-8',errors='replace',
            timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
        ).stdout.strip()
    except (OSError,subprocess.SubprocessError):
        return ''


def refuse_unlocked_legacy_owner():
    """Do not run beside an older app process that predates the writer lock."""
    pid_file=DATA/'server.pid'
    if not pid_file.is_file():return
    try:pid=int(pid_file.read_text(encoding='ascii').strip())
    except (OSError,ValueError):return
    if pid==os.getpid():return
    command=process_command_line(pid)
    if command and str((BASE/'app.py').resolve()).casefold() in command.casefold():
        raise RuntimeError(
            f'检测到仍在使用此资料库的旧拾光进程（PID {pid}）；未启动第二个写服务'
        )


def capture_runtime_version():
    try:
        return subprocess.run(
            ['git','rev-parse','HEAD'],cwd=BASE,capture_output=True,text=True,
            encoding='ascii',errors='replace',timeout=3,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
        ).stdout.strip() or 'working-tree'
    except (OSError,subprocess.SubprocessError):
        return 'working-tree'


def initialize_application():
    """Explicit startup boundary. Importing this module remains read-only."""
    global APP_OWNER,APP_INITIALIZED,RUNTIME_VERSION
    with APP_INITIALIZE_LOCK:
        if APP_INITIALIZED:return
        refuse_unlocked_legacy_owner()
        owner=DataOwner(DATA)
        owner.acquire()
        try:
            (DATA/'thumbs').mkdir(exist_ok=True)
            (DATA/'faces').mkdir(exist_ok=True)
            init_db()
            with db() as c:recover_interrupted_jobs(c)
            attach_place_rules()
            refresh_rules()
            RUNTIME_VERSION=capture_runtime_version()
        except Exception:
            owner.release()
            raise
        APP_OWNER=owner
        APP_INITIALIZED=True


def shutdown_application(timeout=15.0):
    """Stop writers before releasing the data owner; never claim a timed-out stop."""
    global APP_OWNER,APP_INITIALIZED
    with APP_INITIALIZE_LOCK:
        if not APP_INITIALIZED:return True
        STOP.set()
        thread=SCAN_THREAD
        if thread and thread.is_alive():
            thread.join(max(0,float(timeout)))
            if thread.is_alive():
                return False
        close_readers()
        if APP_OWNER:APP_OWNER.release()
        APP_OWNER=None
        APP_INITIALIZED=False
        return True


@asynccontextmanager
async def app_lifespan(_application):
    initialize_application()
    try:
        yield
    finally:
        if not shutdown_application():
            raise RuntimeError('后台任务未在安全时限内停止；写入owner未释放')


app = FastAPI(
    title='拾光 · 本地照片资料库',docs_url=None,redoc_url=None,
    lifespan=app_lifespan,
)

@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    if host not in {'127.0.0.1', 'localhost', 'testserver'}:
        return JSONResponse({'detail':'仅允许本机访问'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin not in {f'http://{request.headers.get("host")}', f'https://{request.headers.get("host")}'}:
        return JSONResponse({'detail':'不允许跨站请求'}, status_code=403)
    return await call_next(request)

class ApiProblem(HTTPException):
    def __init__(self,status_code,detail,error_code,**extra):
        super().__init__(status_code=status_code,detail=detail)
        self.error_code=error_code
        self.extra=extra


@app.exception_handler(HTTPException)
async def structured_http_error(_request,exc):
    code=getattr(exc,'error_code',None)
    if not code:
        code={
            400:'invalid_request',404:'not_found',409:'conflict',
            422:'invalid_request',423:'busy',
        }.get(exc.status_code,'request_failed')
    payload={'detail':exc.detail,'error_code':code}
    payload.update(getattr(exc,'extra',{}))
    return JSONResponse(payload,status_code=exc.status_code,headers=getattr(exc,'headers',None))


@app.exception_handler(RequestValidationError)
async def structured_validation_error(_request,exc):
    return JSONResponse(
        {'detail':exc.errors(),'error_code':'invalid_request'},status_code=422
    )

@app.exception_handler(Exception)
async def structured_server_error(_request,exc):
    logging.getLogger('ourtime').exception('Unhandled API error',exc_info=exc)
    return JSONResponse(
        {'detail':'服务器未能完成请求','error_code':'server_error'},
        status_code=500,
    )


def now():
    return datetime.now().isoformat(timespec='seconds')

def invalidate_status_cache():
    with STATUS_CACHE_LOCK:
        STATUS_CACHE['payload']=None
        STATUS_CACHE['at']=0

def asset_lock_index(digest):
    return int(digest[:8],16)%len(ASSET_LOCKS)

def asset_lock_indices(digests):
    return sorted({asset_lock_index(digest) for digest in digests})

def asset_lock(digest):
    return ASSET_LOCKS[asset_lock_index(digest)]

def operation_lock(operation_id):
    digest=hashlib.sha256(str(operation_id).encode('utf-8')).digest()
    return OPERATION_LOCKS[int.from_bytes(digest[:4],'big')%len(OPERATION_LOCKS)]

def path_key(path):
    return os.path.normcase(os.path.abspath(path))

def refresh_rules():
    global RULES
    with db() as c:
        rules=[path_key(r[0]) for r in c.execute('SELECT path FROM excluded_roots')]
    with RULE_LOCK:
        RULES=rules

def path_excluded(path):
    key=path_key(path)
    with RULE_LOCK:
        return any(key==root or key.startswith(root.rstrip(os.sep)+os.sep) for root in RULES)

def thumbnail_path(digest):
    return DATA/'thumbs'/f'{digest}.jpg'

def make_thumbnail(path,digest):
    with Image.open(path) as original:
        thumbnail=ImageOps.exif_transpose(original).convert('RGB')
        thumbnail.thumbnail((640,640))
        thumbnail.save(thumbnail_path(digest),quality=84)

def remove_generated(path,folder):
    # Only application-generated cache files in the exact managed folder.
    managed=(DATA/folder)
    if managed.is_symlink() or path.is_symlink() or path.parent.resolve()!=managed.resolve():
        raise RuntimeError('缓存路径不在本项目指定目录内')
    try:
        size=path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0

def index_face_derivatives(face_dir=None):
    """Enumerate a managed face folder once and group only exact encoded owners."""
    face_dir=Path(face_dir or (DATA/'faces'))
    inventory={}
    try:entries=list(face_dir.iterdir())
    except FileNotFoundError:entries=[]
    patterns=(
        re.compile(r'^(\d+)\.jpg$',re.I),
        re.compile(r'^\.pending-(\d+)\.jpg$',re.I),
        re.compile(r'^\.recover-(\d+)-[^\\/]+\.jpg$',re.I),
    )
    for path in entries:
        for pattern in patterns:
            match=pattern.fullmatch(path.name)
            if match:
                inventory.setdefault(int(match.group(1)),[]).append(path)
                break
    return inventory


def owned_face_derivative_paths(face,inventory=None):
    """Return only managed crop files whose ownership is encoded by one face row."""
    face_id=int(face['id'])
    face_dir=DATA/'faces'
    inventory=inventory if inventory is not None else index_face_derivatives(face_dir)
    candidates=list(inventory.get(face_id,[]))
    crop=str(face['crop'] or '').strip()
    if crop and Path(crop).name==crop and '/' not in crop and '\\' not in crop:
        candidates=[path for path in candidates if path.name.lower()!=f'{face_id}.jpg']
        candidates.append(face_dir/crop)
    elif crop:
        raise RuntimeError('缓存路径不在本项目指定目录内')
    else:
        candidates.append(face_dir/f'{face_id}.jpg')
    unique=[]
    seen=set()
    for path in candidates:
        key=str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique

def cleanup_asset_cache(aid,operation_id=None):
    with db() as c:
        row=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
    if not row:
        return 0
    with asset_lock(row['sha256']):
        with db() as c:
            policy=c.execute(
                'SELECT derivative_policy,derivative_operation_id FROM assets WHERE id=?',(aid,)
            ).fetchone()
            if not policy or policy['derivative_policy']!='purge':
                return 0
            if operation_id and policy['derivative_operation_id']!=operation_id:
                return 0
            active=c.execute('SELECT 1 FROM assets a WHERE a.id=? AND '+ACTIVE_ASSET,(aid,)).fetchone()
            if active:
                return 0
            faces=c.execute('SELECT id,crop FROM faces WHERE asset_id=?',(aid,)).fetchall()
        released=remove_generated(thumbnail_path(row['sha256']),'thumbs')
        inventory=index_face_derivatives(DATA/'faces')
        for face in faces:
            for path in owned_face_derivative_paths(face,inventory):
                released+=remove_generated(path,'faces')
        with db() as c:
            current=c.execute(
                'SELECT derivative_policy,derivative_operation_id FROM assets WHERE id=?',(aid,)
            ).fetchone()
            active=c.execute('SELECT 1 FROM assets a WHERE a.id=? AND '+ACTIVE_ASSET,(aid,)).fetchone()
            if (
                not current or current['derivative_policy']!='purge' or active
                or (operation_id and current['derivative_operation_id']!=operation_id)
            ):
                return released
            face_ids=[int(face[0]) for face in faces]
            if face_ids:
                placeholders=','.join('?' for _ in face_ids)
                c.execute(
                    f'UPDATE people SET cover_face_id=NULL WHERE cover_face_id IN ({placeholders})',
                    face_ids,
                )
            c.execute('DELETE FROM faces WHERE asset_id=?',(aid,))
            c.execute(
                'UPDATE assets SET face_state=0,face_error=NULL,derivative_operation_id=NULL WHERE id=?',
                (aid,),
            )
        invalidate_face_index()
        invalidate_status_cache()
        return released

def jsonable(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, bytes):
        if len(value) < 32768:
            try:
                return value.decode('utf-8').rstrip('\x00')
            except UnicodeDecodeError:
                pass
        return {'binary_bytes':len(value), 'sha256':hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):
        return {str(k):jsonable(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)):
        return [jsonable(x) for x in value]
    try:
        f = float(value)
        return f if math.isfinite(f) else str(value)
    except (TypeError,ValueError,ZeroDivisionError):
        return str(value)


def canonical_request_hash(payload):
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest(),encoded


def operation_receipt(row):
    result=json.loads(row['result_json']) if row['result_json'] else {}
    return {
        **result,
        'operation_id':row['operation_id'],
        'kind':row['kind'],
        'status':row['status'],
        'state_committed':bool(row['state_committed']),
        'cleanup_status':row['cleanup_status'],
        'error_code':row['error_code'],
        'error_detail':row['error_detail'],
    }


def prepare_operation(conn,kind,operation_id,payload):
    operation_id=str(operation_id or uuid.uuid4())
    request_hash,request_json=canonical_request_hash(payload)
    stamp=now()
    inserted=conn.execute(
        '''INSERT OR IGNORE INTO operations(
             operation_id,kind,request_hash,status,request_json,created_at,updated_at
           ) VALUES (?,?,?,'pending',?,?,?)''',
        (operation_id,kind,request_hash,request_json,stamp,stamp),
    ).rowcount
    if not inserted:
        row=conn.execute(
            'SELECT * FROM operations WHERE operation_id=?',(operation_id,)
        ).fetchone()
        if row['kind']!=kind or row['request_hash']!=request_hash:
            raise ApiProblem(
                409,'同一操作编号不能用于不同请求','idempotency_conflict',
                operation_id=operation_id,
            )
        return operation_id,operation_receipt(row)
    return operation_id,None


def finish_operation(conn,operation_id,result,*,status='committed',
                     state_committed=True,cleanup_status='not_applicable',
                     undo=None,error_code=None,error_detail=None):
    payload=dict(result)
    conn.execute(
        '''UPDATE operations
           SET status=?,state_committed=?,cleanup_status=?,result_json=?,
               undo_json=?,error_code=?,error_detail=?,updated_at=?
           WHERE operation_id=?''',
        (
            status,int(state_committed),cleanup_status,
            json.dumps(payload,ensure_ascii=False,sort_keys=True),
            json.dumps(undo,ensure_ascii=False,sort_keys=True) if undo is not None else None,
            error_code,error_detail,now(),operation_id,
        ),
    )
    row=conn.execute(
        'SELECT * FROM operations WHERE operation_id=?',(operation_id,)
    ).fetchone()
    return operation_receipt(row)


def operation_id_from(body,request=None):
    value=getattr(body,'operation_id',None)
    if not value and request is not None:
        value=request.headers.get('Idempotency-Key')
    value=str(value or uuid.uuid4()).strip()
    if not value or len(value)>128 or not re.fullmatch(r'[A-Za-z0-9._:-]+',value):
        raise ApiProblem(400,'操作编号无效','invalid_request')
    return value

def parse_date(value):
    if not value:
        return None
    value = str(value).strip().replace('\x00','')
    for fmt in ['%Y:%m:%d %H:%M:%S','%Y-%m-%d %H:%M:%S','%Y-%m-%dT%H:%M:%S']:
        try:
            return datetime.strptime(value[:19], fmt).isoformat(timespec='seconds')
        except ValueError:
            pass
    return None

def filename_date(path):
    # Only explicit date tokens; arbitrary serial numbers are not dates.
    match = re.search(r'(?<!\d)((?:19|20)\d{2})[-_.]?([01]\d)[-_.]?([0-3]\d)(?:[_T -]?([0-2]\d)([0-5]\d)([0-5]\d))?(?!\d)', path.stem)
    if match:
        y,m,d,hh,mm,ss = match.groups()
        try:
            dt = datetime(int(y),int(m),int(d),int(hh or 0),int(mm or 0),int(ss or 0))
            return (dt.isoformat(timespec='seconds') if hh else dt.date().isoformat(), '文件名推测', '秒' if hh else '日')
        except ValueError:
            pass
    return None, None, None

def gps_degrees(value, ref):
    if not value or len(value) != 3:
        return None
    try:
        number = float(value[0]) + float(value[1])/60 + float(value[2])/3600
        if str(ref).upper() in {'S','W',"B'S'","B'W'"}:
            number *= -1
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None

def nearest_place(lat, lon):
    return PLACES.nearest(lat, lon)

def load_place_catalog():
    names=[]
    osm=GEO_ROOT/'osm'/'beijing-places.json'
    if osm.exists():
        try:
            payload=json.loads(osm.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            payload={}
        for el in payload.get('elements',[]):
            tags=el.get('tags') or {}
            name=tags.get('name:zh') or tags.get('name') or ''
            if name: names.append(name)
    scenic=GEO_ROOT/'scenic'/'scenic_areas_metadata.csv'
    if scenic.exists():
        raw=scenic.read_bytes()
        text=''
        for enc in ('utf-8-sig','gb18030','utf-8'):
            try:
                text=raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        for row in csv.DictReader(text.splitlines()):
            name=(row.get('name') or '').strip()
            if name: names.append(name)
    seen=set(); catalog=[]
    for name in names:
        key=name.casefold()
        if key in seen: continue
        seen.add(key); catalog.append(name)
    return catalog

PLACE_CATALOG=None
def place_catalog():
    global PLACE_CATALOG
    if PLACE_CATALOG is None: PLACE_CATALOG=load_place_catalog()
    return PLACE_CATALOG


def metadata_for(path, stat, digest):
    result = dict(width=None,height=None,format=path.suffix[1:].upper(),metadata={},captured_at=None,date_source=None,date_precision=None,
                  latitude=None,longitude=None,place=None,place_source=None,camera=None,category='照片',error=None)
    full_metadata={}
    if EXIFTOOL:
        try:
            full_metadata=read_metadata(EXIFTOOL,path)
        except Exception as e:
            full_metadata={'_read_error':str(e)}
    try:
        with Image.open(path) as image:
            result.update(width=image.width,height=image.height,format=image.format)
            exif = image.getexif()
            raw = {'IFD0':{ExifTags.TAGS.get(k,str(k)):jsonable(v) for k,v in exif.items()}}
            groups = {}
            for label,code in [('EXIF',34665),('GPS',34853),('Interop',40965)]:
                try:
                    values=exif.get_ifd(code)
                    groups[label]=values
                    names=ExifTags.GPSTAGS if label == 'GPS' else ExifTags.TAGS
                    raw[label]={names.get(k,str(k)):jsonable(v) for k,v in values.items()}
                except Exception as e:
                    raw[label+'_read_error']=str(e)
            raw['image_info']=jsonable(image.info)
            try:
                raw['IPTC']=jsonable(IptcImagePlugin.getiptcinfo(image) or {})
                raw['XMP']=jsonable(image.getxmp())
            except Exception as e:
                raw['extra_read_error']=str(e)
            raw['reader']='Pillow + ExifTool' if EXIFTOOL else 'Pillow；相机私有 MakerNotes 保留二进制摘要，未逐项解码'
            result['metadata']=raw
            ex=groups.get('EXIF',{})
            for code,source in [(36867,'EXIF 原始拍摄时间'),(36868,'EXIF 数字化时间')]:
                parsed=parse_date(ex.get(code) or exif.get(code))
                if parsed:
                    result.update(captured_at=parsed,date_source=source,date_precision='秒')
                    break
            gps=groups.get('GPS',{})
            lat=gps_degrees(gps.get(2),gps.get(1)); lon=gps_degrees(gps.get(4),gps.get(3))
            if lat is not None and lon is not None and -90<=lat<=90 and -180<=lon<=180:
                result.update(latitude=lat,longitude=lon)
                result['place'],result['place_source']=nearest_place(lat,lon)
            result['camera']=' '.join(dict.fromkeys(str(x).strip() for x in [exif.get(271),exif.get(272)] if x)) or None
            if re.search(r'screenshot|屏幕截图|截屏',str(path),re.I):
                result['category']='截图'
            elif min(image.size)<160:
                result['category']='小图 / 素材'
            thumb=ImageOps.exif_transpose(image).convert('RGB')
            thumb.thumbnail((640,640))
            thumb.save(DATA/'thumbs'/f'{digest}.jpg',quality=84)
    except Exception as e:
        result['error']=f'{type(e).__name__}: {e}'
    if EXIFTOOL:
        result['metadata']['ExifTool']=full_metadata
        def tag(name):
            return next((v for k,v in full_metadata.items() if k.split(':')[-1]==name),None)
        for name,source in [('DateTimeOriginal','EXIF 原始拍摄时间'),('CreateDate','元数据数字化时间')]:
            parsed=parse_date(tag(name))
            if parsed:
                result.update(captured_at=parsed,date_source=source,date_precision='秒');break
        lat=tag('GPSLatitude');lon=tag('GPSLongitude')
        if isinstance(lat,(int,float)) and isinstance(lon,(int,float)):
            if tag('GPSLatitudeRef')=='S': lat=-abs(lat)
            if tag('GPSLongitudeRef')=='W': lon=-abs(lon)
            if -90<=lat<=90 and -180<=lon<=180:
                result.update(latitude=lat,longitude=lon)
                result['place'],result['place_source']=nearest_place(lat,lon)
        if not result['camera']:
            result['camera']=' '.join(dict.fromkeys(str(x) for x in [tag('Make'),tag('Model')] if x)) or None
        if not result['width']: result['width']=tag('ImageWidth')
        if not result['height']: result['height']=tag('ImageHeight')
        if '_read_error' in full_metadata and not result['error']:
            result['error']='扩展元数据读取失败：'+full_metadata['_read_error']
    if not result['captured_at']:
        dt,source,precision=filename_date(path)
        if dt:
            result.update(captured_at=dt,date_source=source,date_precision=precision)
        else:
            result.update(captured_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds'),date_source='文件修改时间参考',date_precision='秒')
    return result

def get_face_engine():
    global FACE_ENGINE
    global FACE_RUNTIME
    if FACE_ENGINE is None:
        import onnxruntime as ort
        from insightface.app import FaceAnalysis
        root=MODEL_ROOT/'models'/'buffalo_l'
        for name in ['det_10g.onnx','w600k_r50.onnx']:
            if not (root/name).exists():
                raise RuntimeError(f'缺少本地模型：{root/name}')
        options=ort.SessionOptions(); options.intra_op_num_threads=2
        available=list(ort.get_available_providers())
        preferred=[name for name in ('CUDAExecutionProvider','DmlExecutionProvider') if name in available]
        preferred.append('CPUExecutionProvider')
        last_error=None
        for index,provider in enumerate(preferred):
            chain=preferred[index:]
            try:
                engine=FaceAnalysis(name='buffalo_l',root=str(MODEL_ROOT),allowed_modules=['detection','recognition'],providers=chain,sess_options=options)
                engine.prepare(ctx_id=(-1 if chain[0]=='CPUExecutionProvider' else 0),det_size=(640,640))
                used=[]
                for model in engine.models.values():
                    session=getattr(model,'session',None)
                    if session is not None:
                        used=list(session.get_providers())
                        break
                FACE_ENGINE=engine
                FACE_RUNTIME=describe_face_runtime(used or chain)
                break
            except Exception as error:
                last_error=error
                FACE_ENGINE=None
        if FACE_ENGINE is None:
            raise RuntimeError(f'人脸模型无法启动：{last_error}')
    return FACE_ENGINE

def describe_face_runtime(providers):
    mapping={'CUDAExecutionProvider':'GPU (CUDA)','DmlExecutionProvider':'GPU (DirectML)','CPUExecutionProvider':'CPU'}
    return mapping.get(providers[0], providers[0]) if providers else '未加载'

def current_face_runtime():
    if FACE_RUNTIME:
        return FACE_RUNTIME
    try:
        import onnxruntime as ort
        available=list(ort.get_available_providers())
        preferred=[name for name in ('CUDAExecutionProvider','DmlExecutionProvider') if name in available]
        preferred.append('CPUExecutionProvider')
        return describe_face_runtime(preferred)
    except Exception:
        return 'CPU'

def invalidate_face_index():
    global FACE_INDEX, FACE_INDEX_GEN
    with FACE_INDEX_LOCK:
        FACE_INDEX=None
        FACE_INDEX_GEN += 1

def face_index_revision():
    with db() as c:
        return int(c.execute(
            'SELECT revision FROM face_index_state WHERE id=1'
        ).fetchone()[0])

def build_face_index(known,revision=0):
    import numpy as np
    def value(row,key,default=None):
        try:
            result=row[key]
        except (KeyError,IndexError,TypeError):
            return default
        return default if result is None else result

    count=len(known)
    dim=512
    if known:
        first=np.frombuffer(known[0]['embedding'],dtype=np.float32)
        dim=int(first.shape[0])
    capacity=max(count*2,1024)
    vectors=np.zeros((capacity,dim),dtype=np.float32)
    person_slots=np.zeros(capacity,dtype=np.int32)
    person_slot_by_id={}
    unique_person_ids=[]
    person_states=[]
    canonical_person_ids=[]
    if known:
        vectors[:count]=np.stack([np.frombuffer(r['embedding'],dtype=np.float32) for r in known])
        for face_index,row in enumerate(known):
            source_person_id=int(row['person_id'])
            suggested_person_id=int(value(row,'suggested_person_id',0) or 0)
            suggested_confirmed=bool(value(row,'suggested_confirmed',0))
            suggested_ignored=bool(value(row,'suggested_ignored',0))
            if suggested_person_id and (suggested_confirmed or suggested_ignored):
                person_id=suggested_person_id
                state='ignored' if suggested_ignored else 'confirmed'
            else:
                person_id=source_person_id
                state='ignored' if row['ignored'] else 'confirmed' if row['confirmed'] else 'pending'
            canonical_person_ids.append(person_id)
            slot=person_slot_by_id.get(person_id)
            if slot is None:
                slot=len(unique_person_ids)
                person_slot_by_id[person_id]=slot
                unique_person_ids.append(person_id)
                person_states.append(state)
            elif state=='ignored' or (state=='confirmed' and person_states[slot]=='pending'):
                person_states[slot]=state
            person_slots[face_index]=slot
    return {
        'vectors':vectors,
        'size':count,
        'person_ids':canonical_person_ids,
        'person_slots':person_slots,
        'person_slot_by_id':person_slot_by_id,
        'unique_person_ids':unique_person_ids,
        'person_states':person_states,
        'revision':int(revision),
    }

def load_face_index():
    global FACE_INDEX, FACE_INDEX_GEN
    attempts=0
    while True:
        attempts+=1
        with FACE_INDEX_LOCK:
            cached=FACE_INDEX
            generation=FACE_INDEX_GEN
        if cached is not None:
            current_revision=face_index_revision()
            with FACE_INDEX_LOCK:
                if (
                    FACE_INDEX is cached
                    and int(cached.get('revision',-1))==current_revision
                ):
                    return cached
                if FACE_INDEX is cached:
                    FACE_INDEX=None
                    FACE_INDEX_GEN+=1
                generation=FACE_INDEX_GEN
        with db() as c:
            c.execute('BEGIN')
            revision=int(c.execute(
                'SELECT revision FROM face_index_state WHERE id=1'
            ).fetchone()[0])
            known=c.execute(
                '''SELECT f.embedding,f.person_id,p.confirmed,p.ignored,p.suggested_person_id,
                          target.confirmed AS suggested_confirmed,
                          target.ignored AS suggested_ignored
                   FROM faces f
                   JOIN people p ON p.id=f.person_id
                   LEFT JOIN people target ON target.id=p.suggested_person_id
                   WHERE f.embedding IS NOT NULL
                     AND (coalesce(p.ignored,0)=1 OR coalesce(f.ignored,0)=0)
                     AND NOT (
                         coalesce(p.confirmed,0)=0
                         AND coalesce(p.ignored,0)=0
                         AND p.suggested_person_id IS NOT NULL
                     )
                    ORDER BY f.id'''
            ).fetchall()
        index=build_face_index(known,revision)
        current_revision=face_index_revision()
        with FACE_INDEX_LOCK:
            if generation==FACE_INDEX_GEN and revision==current_revision:
                if FACE_INDEX is None:
                    FACE_INDEX=index
                return FACE_INDEX
            if FACE_INDEX is not None:
                return FACE_INDEX
        if attempts>=8:
            raise RuntimeError('人脸索引正在更新，请稍后重试')

def remember_face(
    person_id,embedding,confirmed=False,ignored=False,expected_revision=None
):
    import numpy as np
    with FACE_INDEX_LOCK:
        if FACE_INDEX is None:
            return False
        if (
            expected_revision is not None
            and int(FACE_INDEX.get('revision',-1))!=int(expected_revision)
        ):
            return False
        vector=np.asarray(embedding,dtype=np.float32).reshape(-1)
        if FACE_INDEX['size']==0 and vector.shape[0]!=FACE_INDEX['vectors'].shape[1]:
            FACE_INDEX['vectors']=np.zeros((len(FACE_INDEX['vectors']), vector.shape[0]), dtype=np.float32)
        if FACE_INDEX['size']>=len(FACE_INDEX['vectors']):
            extra=np.zeros_like(FACE_INDEX['vectors'])
            FACE_INDEX['vectors']=np.vstack([FACE_INDEX['vectors'],extra])
            FACE_INDEX['person_slots']=np.concatenate([FACE_INDEX['person_slots'],np.zeros_like(FACE_INDEX['person_slots'])])
        position=FACE_INDEX['size']
        slot=FACE_INDEX['person_slot_by_id'].get(int(person_id))
        state='ignored' if ignored else 'confirmed' if confirmed else 'pending'
        if slot is None:
            slot=len(FACE_INDEX['unique_person_ids'])
            FACE_INDEX['person_slot_by_id'][int(person_id)]=slot
            FACE_INDEX['unique_person_ids'].append(int(person_id))
            FACE_INDEX['person_states'].append(state)
        else:
            FACE_INDEX['person_states'][slot]=state
        FACE_INDEX['vectors'][position]=vector
        FACE_INDEX['person_slots'][position]=slot
        FACE_INDEX['size']+=1
        FACE_INDEX['person_ids'].append(person_id)
        return True

def choose_face_person(index,embedding,used_people=None):
    """Route one face to the closest canonical person at the shared threshold."""
    import numpy as np
    used={int(pid) for pid in (used_people or ())}
    if not index or not index.get('size') or not index.get('unique_person_ids'):
        return {'route':'new','person_id':None,'suggested_person_id':None,'best_score':None,'second_score':None,'margin':None}
    emb=np.asarray(embedding,dtype=np.float32).reshape(-1)
    similarities=index['vectors'][:index['size']]@emb
    person_scores=np.full(len(index['unique_person_ids']),-np.inf,dtype=np.float32)
    np.maximum.at(person_scores,index['person_slots'][:index['size']],similarities)
    ranked=[int(slot) for slot in np.argsort(-person_scores)
            if int(index['unique_person_ids'][int(slot)]) not in used]
    if not ranked:
        return {'route':'new','person_id':None,'suggested_person_id':None,'best_score':None,'second_score':None,'margin':None}
    best_slot=ranked[0]
    best_person=int(index['unique_person_ids'][best_slot])
    best_score=float(person_scores[best_slot])
    second_score=float(person_scores[ranked[1]]) if len(ranked)>1 else -1.0
    margin=best_score-second_score
    state=index['person_states'][best_slot]
    base={'person_id':None,'suggested_person_id':None,'best_score':best_score,'second_score':second_score,'margin':margin}
    if best_score<FACE_GROUP_THRESHOLD:
        return {**base,'route':'new'}
    if state in {'confirmed','ignored'}:
        if margin<FACE_MATCH_MARGIN:
            return {**base,'route':'suggested','suggested_person_id':best_person}
        return {**base,'route':state,'person_id':best_person}
    if state=='pending':
        return {**base,'route':'pending','person_id':best_person}
    return {**base,'route':'new'}

def crop_face_image(pic,bbox):
    x1,y1,x2,y2=[int(x) for x in bbox[:4]]
    pad=int(max(x2-x1,y2-y1)*.25)
    crop=pic.crop((
        max(0,x1-pad),max(0,y1-pad),
        min(pic.width,x2+pad),min(pic.height,y2+pad),
    ))
    crop.thumbnail((200,200))
    return crop

class FacePublishPending(RuntimeError):
    """Face rows committed; only derivative crop publication needs recovery."""

class FaceIndexChanged(RuntimeError):
    """The matching index changed before its decision could be committed."""

def recover_asset_face_crops(asset_id,path):
    """Finish a committed crop publication without rerunning face inference."""
    with db() as c:
        asset=c.execute(
            'SELECT face_state FROM assets WHERE id=?',
            (asset_id,),
        ).fetchone()
        rows=c.execute(
            'SELECT id,bbox,crop FROM faces WHERE asset_id=? ORDER BY id',
            (asset_id,),
        ).fetchall()
    if not rows:
        if not asset or int(asset['face_state'] or 0)!=2:
            return False
        with db() as c:
            c.execute(
                'UPDATE assets SET face_state=1,face_error=NULL WHERE id=? AND face_state=2',
                (asset_id,),
            )
        invalidate_status_cache()
        return True
    pic=None
    recovery_files=[]
    try:
        for row in rows:
            final_name=row['crop'] or f"{row['id']}.jpg"
            final_path=DATA/'faces'/final_name
            pending_path=DATA/'faces'/f".pending-{row['id']}.jpg"
            if final_path.exists():
                continue
            if pending_path.exists():
                os.replace(pending_path,final_path)
                continue
            if pic is None:
                with Image.open(path) as original:
                    pic=ImageOps.exif_transpose(original).convert('RGB')
                    pic.thumbnail((2400,2400))
            bbox=json.loads(row['bbox'])
            staged=DATA/'faces'/f".recover-{row['id']}-{uuid.uuid4().hex}.jpg"
            recovery_files.append(staged)
            crop_face_image(pic,bbox).save(staged,format='JPEG',quality=88)
            os.replace(staged,final_path)
        with db() as c:
            c.execute(
                'UPDATE assets SET face_state=1,face_error=NULL WHERE id=?',
                (asset_id,),
            )
        invalidate_face_index()
        invalidate_status_cache()
        return True
    finally:
        for staged in recovery_files:
            try:
                staged.unlink()
            except (FileNotFoundError,OSError):
                pass

def person_accepts_route(conn,person_id,route):
    row=conn.execute(
        'SELECT confirmed,ignored,suggested_person_id FROM people WHERE id=?',
        (person_id,),
    ).fetchone()
    if not row:
        return False
    if route=='confirmed':
        return bool(row['confirmed']) and not bool(row['ignored'])
    if route=='ignored':
        return bool(row['ignored'])
    if route=='pending':
        return not bool(row['confirmed']) and not bool(row['ignored'])
    return True

def route_staged_faces(index,staged):
    used_people=set()
    for item in staged:
        decision=choose_face_person(index,item['emb'],used_people)
        item['person_id']=decision['person_id']
        item['suggestion']=decision['suggested_person_id']
        item['route']=decision['route']
        if item['person_id']:
            used_people.add(item['person_id'])

def process_faces(asset_id,path):
    import numpy as np
    with db() as c:
        row=c.execute(
            'SELECT sha256,face_state FROM assets WHERE id=?',(asset_id,)
        ).fetchone()
    if not row:
        raise RuntimeError('照片档案不存在')
    with asset_lock(row['sha256']):
        with db() as c:
            state_row=c.execute(
                '''SELECT face_state,
                          EXISTS(SELECT 1 FROM faces WHERE asset_id=?) has_faces
                   FROM assets WHERE id=?''',
                (asset_id,asset_id),
            ).fetchone()
        state=int(state_row['face_state'] or 0)
        if state==1:
            return None
        if state==2 or state_row['has_faces']:
            try:
                if recover_asset_face_crops(asset_id,path):
                    return None
            except Exception as error:
                with db() as c:
                    c.execute(
                        'UPDATE assets SET face_state=2,face_error=? WHERE id=?',
                        (
                            (
                                '人脸裁剪待恢复：'
                                if state_row['has_faces']
                                else '人脸识别结果已提交，完成状态待恢复：'
                            )
                            +str(error),
                            asset_id,
                        ),
                    )
                invalidate_status_cache()
                raise FacePublishPending(
                    '人脸已提交，裁剪发布待恢复'
                ) from error
        engine=get_face_engine()
        with Image.open(path) as original:
            pic=ImageOps.exif_transpose(original).convert('RGB')
            pic.thumbnail((2400,2400))
            detections=engine.get(np.asarray(pic)[:,:,::-1].copy())
            staged=[]
            for face in detections:
                x1,y1,x2,y2=[int(x) for x in face.bbox]
                if x2-x1<28 or y2-y1<28 or float(face.det_score)<0.65:
                    continue
                emb=np.asarray(face.normed_embedding,dtype=np.float32)
                staged.append({
                    'emb':emb,
                    'bbox':[x1,y1,x2,y2,pic.width,pic.height],
                    'score':float(face.det_score),
                    'crop':crop_face_image(pic,[x1,y1,x2,y2]),
                })
        committed_revision=None
        for decision_attempt in range(3):
            index=load_face_index()
            index_revision=int(index.get('revision',face_index_revision()))
            route_staged_faces(index,staged)
            pending_crops=[]
            try:
                with db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    write_revision=int(c.execute(
                        'SELECT revision FROM face_index_state WHERE id=1'
                    ).fetchone()[0])
                    if write_revision!=index_revision:
                        raise FaceIndexChanged(
                            f'index {index_revision}, database {write_revision}'
                        )
                    for item in staged:
                        person_id=item['person_id']
                        if person_id and not person_accepts_route(
                            c,person_id,item['route']
                        ):
                            person_id=None
                            item['person_id']=None
                            item['suggestion']=None
                            item['route']='new'
                        if not person_id:
                            if item['suggestion']:
                                suggestion_exists=c.execute(
                                    '''SELECT 1 FROM people
                                       WHERE id=?
                                         AND (confirmed=1 OR ignored=1)''',
                                    (item['suggestion'],),
                                ).fetchone()
                                if not suggestion_exists:
                                    item['suggestion']=None
                            person_id=c.execute('INSERT INTO people(suggested_person_id) VALUES (?)',(item['suggestion'],)).lastrowid
                            item['person_id']=person_id
                        reviewed=int(item['route'] in {'confirmed','ignored'})
                        ignored=int(item['route']=='ignored')
                        fid=c.execute('INSERT INTO faces(asset_id,person_id,bbox,embedding,score,reviewed,ignored) VALUES (?,?,?,?,?,?,?)',
                            (asset_id,person_id,json.dumps(item['bbox']),item['emb'].tobytes(),item['score'],reviewed,ignored)).lastrowid
                        item['fid']=fid
                        crop_path=DATA/'faces'/f".pending-{fid}.jpg"
                        pending_crops.append(crop_path)
                        item['crop'].save(crop_path,format='JPEG',quality=88)
                        c.execute('UPDATE faces SET crop=? WHERE id=?',(f'{fid}.jpg',fid))
                    c.execute('UPDATE assets SET face_state=2,face_error=NULL WHERE id=?',(asset_id,))
                    committed_revision=int(c.execute(
                        'SELECT revision FROM face_index_state WHERE id=1'
                    ).fetchone()[0])
                break
            except FaceIndexChanged:
                invalidate_face_index()
                if decision_attempt==2:
                    raise RuntimeError(
                        '人脸索引持续更新，未提交过期匹配结果'
                    )
                continue
            except Exception:
                for crop_path in pending_crops:
                    try:
                        if crop_path.exists():
                            crop_path.unlink()
                    except OSError:
                        pass
                raise
        try:
            for item,crop_path in zip(staged,pending_crops):
                os.replace(crop_path,DATA/'faces'/f"{item['fid']}.jpg")
            with db() as c:
                c.execute('UPDATE assets SET face_state=1,face_error=NULL WHERE id=?',(asset_id,))
            invalidate_status_cache()
        except Exception as error:
            with db() as c:
                c.execute(
                    'UPDATE assets SET face_state=2,face_error=? WHERE id=?',
                    (
                        ('人脸裁剪待恢复：' if staged else '人脸识别结果已提交，完成状态待恢复：')
                        +str(error),
                        asset_id,
                    ),
                )
            invalidate_status_cache()
            invalidate_face_index()
            raise FacePublishPending('人脸已提交，裁剪发布待恢复') from error

        try:
            current_revision=face_index_revision()
            remembered=True
            if current_revision!=committed_revision:
                remembered=False
            else:
                for item in staged:
                    if item['route']!='suggested':
                        remembered=bool(remember_face(
                            item['person_id'],item['emb'],
                            confirmed=item['route']=='confirmed',
                            ignored=item['route']=='ignored',
                            expected_revision=index_revision,
                        )) and remembered
            with FACE_INDEX_LOCK:
                if (
                    remembered
                    and FACE_INDEX is not None
                    and int(FACE_INDEX.get('revision',-1))==index_revision
                ):
                    FACE_INDEX['revision']=committed_revision
                elif FACE_INDEX is not None:
                    # A concurrent write or build means the incremental delta
                    # cannot prove it represents the current database.
                    raise RuntimeError('人脸索引版本已变化')
        except Exception:
            invalidate_face_index()
        return len(staged)

def ingest(path,with_faces,stage=None):
    notify = stage or (lambda _name: None)
    notify('checking')
    if path_excluded(path):
        return 'excluded',None,False,False,0,0.0
    stat=path.stat()
    if not path.is_file():
        return 'skipped',None,False,False,0,0.0
    with db() as c:
        old=c.execute('SELECT f.*,a.sha256,a.face_state,a.error,a.excluded content_excluded FROM files f JOIN assets a ON a.id=f.asset_id WHERE path=?',(str(path),)).fetchone()
    unchanged=old and old['size']==stat.st_size and old['mtime_ns']==stat.st_mtime_ns
    if unchanged:
        digest=old['sha256']
    else:
        notify('fingerprint')
        digest_hash=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest_hash.update(chunk)
        after=path.stat()
        if (stat.st_size,stat.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise RuntimeError('文件在读取期间发生变化，请下次扫描重试')
        digest=digest_hash.hexdigest()
    with asset_lock(digest):
        if path_excluded(path):
            return 'excluded',None,False,False,0,0.0
        with db() as c:
            asset=c.execute('SELECT * FROM assets WHERE sha256=?',(digest,)).fetchone()
        metadata_read=False
        if asset and asset['excluded']:
            aid=asset['id'];state=asset['face_state'];error=None
        elif asset and not asset['error']:
            aid=asset['id']; state=asset['face_state']; error=None
            if not thumbnail_path(digest).exists():
                notify('thumbnail')
                make_thumbnail(path,digest)
        else:
            notify('metadata')
            metadata_read=True
            meta=metadata_for(path,stat,digest)
            error=meta['error']; state=asset['face_state'] if asset else 0
            with db() as c:
                values=dict(meta); values['metadata']=json.dumps(meta['metadata'],ensure_ascii=False)
                if asset:
                    aid=asset['id']
                    c.execute('UPDATE assets SET '+','.join(f'{k}=?' for k in values)+' WHERE id=?',[*values.values(),aid])
                else:
                    values.update(sha256=digest,created_at=now())
                    aid=c.execute('INSERT INTO assets('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values())).lastrowid
        with RULE_LOCK:
            path_is_excluded=int(path_excluded(path))
            with db() as c:
                if not unchanged or old['excluded']!=path_is_excluded or not old['exists_now']:
                    c.execute('''INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,excluded) VALUES (?,?,?,?,?,?)
                        ON CONFLICT(path) DO UPDATE SET asset_id=excluded.asset_id,size=excluded.size,mtime_ns=excluded.mtime_ns,
                        modified_at=excluded.modified_at,exists_now=1,excluded=excluded.excluded''',
                        (aid,str(path),stat.st_size,stat.st_mtime_ns,datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds'),path_is_excluded))
                active=c.execute('SELECT 1 FROM assets a WHERE id=? AND '+ACTIVE_ASSET,(aid,)).fetchone()
        if not active:
            cleanup_asset_cache(aid)
            return 'excluded',error,metadata_read,False,0,0.0
        status='skipped' if unchanged and not metadata_read else 'added'
        face_processed=False
        faces_found=0
        face_seconds=0.0
        if with_faces and state!=1 and not error:
            try:
                notify('faces')
                face_started=time.perf_counter()
                found=process_faces(aid,path)
                if found is not None:
                    face_processed=True
                    faces_found=int(found)
                    face_seconds=time.perf_counter()-face_started
            except FacePublishPending as e:
                error='人脸处理：'+str(e)
            except Exception as e:
                with db() as c:
                    c.execute('UPDATE assets SET face_state=-1,face_error=? WHERE id=?',(str(e),aid))
                error='人脸处理：'+str(e)
        return status,error,metadata_read,face_processed,faces_found,face_seconds

def job_error(jid,path,stage,message):
    with db() as c:
        c.execute('INSERT INTO job_errors(job_id,path,stage,message) VALUES (?,?,?,?)',(jid,str(path),stage,str(message)))
        c.execute('UPDATE jobs SET errors=errors+1 WHERE id=?',(jid,))

def reconcile_missing_files(root, *, stop_event=STOP, stat_path=None,
                            error_callback=None, batch_size=250):
    root_path=Path(root)
    stat_path=stat_path or (lambda path:path.stat())
    result={'checked':0,'missing':0,'errors':0,'root_accessible':False}
    try:
        root_path.stat()
        if not root_path.is_dir():
            raise NotADirectoryError(str(root_path))
    except OSError as exc:
        result['errors']=1
        if error_callback:error_callback(root_path,exc)
        return result
    result['root_accessible']=True
    where,values=directory_predicate(root_path)
    last_id=0
    page=max(1,min(int(batch_size),1000))
    while not stop_event.is_set():
        with db() as c:
            rows=c.execute(
                'SELECT id,path,size,mtime_ns FROM files '
                'WHERE id>? AND '+where+' ORDER BY id LIMIT ?',
                [last_id,*values,page],
            ).fetchall()
        if not rows:break
        missing=[]
        for row in rows:
            path=Path(row['path'])
            result['checked']+=1
            try:
                stat_path(path)
            except FileNotFoundError:
                missing.append((row['id'],row['path'],row['size'],row['mtime_ns']))
            except OSError as exc:
                result['errors']+=1
                if error_callback:error_callback(path,exc)
        if missing:
            with db() as c:
                for file_id,path,size,mtime_ns in missing:
                    result['missing']+=c.execute(
                        '''UPDATE files SET exists_now=0
                           WHERE id=? AND path=? AND size IS ? AND mtime_ns IS ?
                             AND exists_now<>0''',
                        (file_id,path,size,mtime_ns),
                    ).rowcount
        last_id=rows[-1]['id']
    return result

def run_scan(jid):
    try:
        with db() as c:
            job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        roots=json.loads(job['roots'])
        workers=1 if job['with_faces'] else job['workers']
        with PROGRESS_LOCK:
            PROGRESS.update(
                job_id=jid,samples=deque([(time.monotonic(),0)],maxlen=2000),metadata_reads=0,
                phase='inventory',current_stage='inventory',current_path='',total=0,
                discovered=0,processed=0,face_photos=0,faces_found=0,face_seconds=0.0,
            )
        progress={
            'discovered':0,'total':0,'processed':0,'added':0,'skipped':0,'excluded':0,
            'auxiliary':0,'metadata_reads':0,'face_photos':0,'faces_found':0,'face_seconds':0.0,
            'phase':'inventory','current_stage':'inventory','current_path':'','dirty':0,'last_flush':0.0,
        }
        progress_lock=threading.Lock()
        def publish_live(payload):
            with PROGRESS_LOCK:
                if PROGRESS['job_id']==jid:
                    for key in ('phase','current_stage','current_path','total','discovered','processed','face_photos','faces_found','face_seconds'):
                        PROGRESS[key]=payload[key]
        def flush_job_progress(force=False):
            with progress_lock:
                if not force and progress['dirty']<25 and time.monotonic()-progress['last_flush']<1:
                    return
                payload=dict(progress)
                progress['dirty']=0
                progress['last_flush']=time.monotonic()
            publish_live(payload)
            with db() as c:
                c.execute('''UPDATE jobs SET discovered=?,total=?,processed=?,added=?,skipped=?,excluded=?,auxiliary=?,metadata_reads=?,
                          face_photos=?,faces_found=?,face_seconds=?,phase=?,current_stage=?,current_path=? WHERE id=?''',
                          (payload['discovered'],payload['total'],payload['processed'],payload['added'],payload['skipped'],payload['excluded'],
                           payload['auxiliary'],payload['metadata_reads'],payload['face_photos'],payload['faces_found'],payload['face_seconds'],
                           payload['phase'],payload['current_stage'],payload['current_path'],jid))
        def set_stage(stage,path):
            with progress_lock:
                progress['current_stage']=stage
                progress['current_path']=str(path)
                progress['dirty']+=1
                payload=dict(progress)
            publish_live(payload)
        def candidates(root,visited,record_errors):
            def walk_error(err):
                if record_errors:
                    job_error(jid,err.filename,'目录读取',err)
            for folder,dirs,files in os.walk(root,topdown=True,onerror=walk_error,followlinks=False):
                if STOP.is_set(): break
                current=Path(folder)
                if path_excluded(current):
                    dirs[:]=[]
                    continue
                retained=[]
                for d in dirs:
                    child=current/d
                    if not job['include_system'] and d.lower() in EXCLUDE: continue
                    try:
                        if child.is_symlink() or (getattr(child.stat(),'st_file_attributes',0)&0x400): continue
                        if child.resolve() in {DATA,BASE}: continue
                        retained.append(d)
                    except OSError as e:
                        if record_errors:
                            job_error(jid,child,'目录读取',e)
                dirs[:]=retained
                for name in files:
                    if STOP.is_set(): break
                    path=current/name
                    if path.suffix.lower() not in EXTENSIONS or path.is_symlink(): continue
                    key=os.path.normcase(str(path))
                    if key in visited: continue
                    visited.add(key)
                    yield path
        def process_one(path):
            metadata_read=False
            try:
                set_stage('checking',path)
                attributes=getattr(path.stat(),'st_file_attributes',0)
                if attributes & (0x1000|0x400000|0x400):
                    raise RuntimeError('跳过离线占位文件或文件链接；请先在本机下载原文件再扫描')
                auxiliary=False
                if path.name.startswith('._'):
                    with path.open('rb') as stream:
                        auxiliary=stream.read(4)==b'\x00\x05\x16\x07'
                if auxiliary:
                    status='auxiliary';error=None
                    with db() as c:
                        old=c.execute('SELECT asset_id FROM files WHERE path=?',(str(path),)).fetchone()
                        if old:
                            c.execute("UPDATE assets SET excluded=1,exclude_reason='已核实 AppleDouble 辅助文件',category='系统辅助文件' WHERE id=?",(old[0],))
                    if old:
                        cleanup_asset_cache(old[0])
                else:
                    status,error,metadata_read,face_processed,faces_found,face_seconds=ingest(
                        path,bool(job['with_faces']),lambda name:set_stage(name,path)
                    )
                if error:
                    job_error(jid,path,'图片读取 / 人脸',error)
                with progress_lock:
                    progress['processed']+=1
                    if status in progress:
                        progress[status]+=1
                    progress['metadata_reads']+=int(metadata_read)
                    if not auxiliary and face_processed:
                        progress['face_photos']+=1
                        progress['faces_found']+=faces_found
                        progress['face_seconds']+=face_seconds
                    progress['current_path']=str(path)
                    progress['dirty']+=1
                flush_job_progress()
                if metadata_read:
                    with PROGRESS_LOCK:
                        PROGRESS['metadata_reads']+=1
                        PROGRESS['samples'].append((time.monotonic(),PROGRESS['metadata_reads']))
            except Exception as e:
                job_error(jid,path,'文件读取',e)
                with progress_lock:
                    progress['processed']+=1
                    progress['current_path']=str(path)
                    progress['dirty']+=1
                flush_job_progress()

        inventory_seen=set()
        for root in roots:
            for path in candidates(root,inventory_seen,True):
                with progress_lock:
                    progress['discovered']+=1
                    progress['current_path']=str(path)
                    progress['dirty']+=1
                flush_job_progress()
            if STOP.is_set(): break
        if not STOP.is_set():
            with progress_lock:
                progress['total']=progress['discovered']
                progress['phase']='processing'
                progress['current_stage']='checking'
                progress['current_path']=''
                progress['dirty']+=1
            flush_job_progress(force=True)

        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='photo-scan') as pool:
          visited=set()
          for root in roots:
            if STOP.is_set(): break
            root_path=Path(root)
            pending=set()
            for path in candidates(root,visited,False):
                if STOP.is_set(): break
                pending.add(pool.submit(process_one,path))
                if len(pending)>=workers*2:
                    finished,pending=wait(pending,return_when=FIRST_COMPLETED)
                    for future in finished: future.result()
            if pending:
                for future in pending: future.result()
            flush_job_progress(force=True)
            if not STOP.is_set():
                reconcile_missing_files(
                    root_path,
                    error_callback=lambda path,exc:job_error(
                        jid,path,'缺失文件核对',exc
                    ),
                )
        with progress_lock:
            if not STOP.is_set():
                progress['total']=progress['processed']
                progress['phase']='complete'
                progress['current_stage']='complete'
            progress['current_path']=''
            progress['dirty']+=1
        flush_job_progress(force=True)
        close_readers()
        with db() as c:
            count=c.execute('SELECT errors FROM jobs WHERE id=?',(jid,)).fetchone()[0]
            status='paused' if STOP.is_set() else ('completed_with_errors' if count else 'completed')
            c.execute('UPDATE jobs SET status=?,finished_at=?,current_path=?,message=? WHERE id=?',
                      (status,now(),'','扫描已暂停，继续时会跳过已完成文件' if STOP.is_set() else '扫描结束；请查看读取问题' if count else '扫描完成',jid))
    except Exception as e:
        job_error(jid,'','扫描任务',e)
        with db() as c:
            c.execute("UPDATE jobs SET status='failed',phase='failed',current_stage='failed',message=?,finished_at=? WHERE id=?",(str(e),now(),jid))
    finally:
        close_readers()
        SCAN_LOCK.release()

PositiveId = Annotated[int, Field(strict=True,gt=0)]


class ScanRequest(BaseModel):
    roots: list[Annotated[str, Field(min_length=1,max_length=32760)]] = Field(min_length=1,max_length=30)
    with_faces: bool = False
    include_system: bool = False
    workers:int=Field(default=2,ge=1,le=4)
    operation_id:str|None=Field(default=None,max_length=128)

def begin_scan(body,request=None):
    operation_id=operation_id_from(body,request)
    with operation_lock(operation_id):
        return begin_scan_locked(body,operation_id)

def begin_scan_locked(body,operation_id):
    roots=[]
    seen_roots=set()
    for raw in body.roots:
        p=Path(raw.strip().strip('"')).expanduser().resolve()
        if not p.is_dir(): raise HTTPException(400,f'目录不存在或无法读取：{p}')
        if p==DATA or DATA in p.parents: raise HTTPException(400,'不能扫描资料库自己的缓存目录')
        if str(p).startswith('\\\\'): raise HTTPException(400,'第一版只扫描本机磁盘目录')
        resolved=str(p)
        key=os.path.normcase(resolved)
        if key not in seen_roots:
            seen_roots.add(key)
            roots.append(resolved)
    request_payload={
        'roots':roots,'with_faces':bool(body.with_faces),
        'include_system':bool(body.include_system),'workers':int(body.workers),
    }
    with db() as c:
        operation_id,existing=prepare_operation(c,'scan',operation_id,request_payload)
    if existing:return existing
    if not SCAN_LOCK.acquire(blocking=False):
        with db() as c:
            finish_operation(
                c,operation_id,{},status='rejected',state_committed=False,
                error_code='busy',error_detail='已有扫描正在运行',
            )
        raise ApiProblem(409,'已有扫描正在运行，请先暂停或等它完成','busy',operation_id=operation_id)
    STOP.clear(); jid=uuid.uuid4().hex
    with STATUS_CACHE_LOCK:
        STATUS_CACHE['payload']=None; STATUS_CACHE['at']=0
    try:
        with db() as c:
            c.execute('INSERT INTO jobs(id,roots,with_faces,include_system,status,started_at,workers,phase,current_stage) VALUES (?,?,?,?,?,?,?,?,?)',
                      (jid,json.dumps(roots,ensure_ascii=False),body.with_faces,body.include_system,'running',now(),1 if body.with_faces else body.workers,'inventory','inventory'))
            receipt=finish_operation(
                c,operation_id,{'id':jid},status='committed',
                state_committed=True,
            )
        global SCAN_THREAD
        SCAN_THREAD=threading.Thread(target=run_scan,args=(jid,),daemon=True)
        SCAN_THREAD.start()
    except Exception:
        SCAN_LOCK.release(); raise
    return receipt

@app.post('/api/scan')
def scan(body:ScanRequest,request:Request): return begin_scan(body,request)

@app.post('/api/scan/pause')
def pause():
    STOP.set()
    with db() as c: c.execute("UPDATE jobs SET status='pausing',message='当前文件处理完后暂停' WHERE status='running'")
    return {'ok':True}

@app.post('/api/scan/{jid}/cancel')
def cancel_scan(jid:str):
    with db() as c:
        row=c.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row: raise HTTPException(404,'任务不存在')
        if row['status']!='paused': raise HTTPException(409,'只有已暂停的扫描可以取消')
        c.execute("UPDATE jobs SET status='cancelled',finished_at=?,current_path='',message='已取消继续扫描' WHERE id=?",(now(),jid))
    with STATUS_CACHE_LOCK:
        STATUS_CACHE['payload']=None; STATUS_CACHE['at']=0
    return {'ok':True,'id':jid,'status':'cancelled'}

@app.post('/api/scan/{jid}/resume')
def resume(jid:str):
    with db() as c: row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not row: raise HTTPException(404,'任务不存在')
    if row['status']!='paused': raise HTTPException(409,'只有已暂停的扫描可以继续')
    return begin_scan(ScanRequest(
        roots=json.loads(row['roots']),with_faces=bool(row['with_faces']),
        include_system=bool(row['include_system']),workers=row['workers'],
        operation_id=uuid.uuid4().hex,
    ))

@app.get('/api/health')
def health():
    return {
        'ok':True,'data_dir':str(DATA),'pid':os.getpid(),
        'data_key':hashlib.sha256(
            os.path.normcase(str(DATA)).encode('utf-8')
        ).hexdigest(),
        'runtime_version':RUNTIME_VERSION,'owner':bool(APP_OWNER),
    }


@app.post('/api/shutdown')
def request_shutdown():
    if SCAN_THREAD and SCAN_THREAD.is_alive():
        raise ApiProblem(409,'后台任务仍在安全停止过程中','busy')
    if not UVICORN_SERVER:
        raise ApiProblem(409,'当前运行方式不支持远程关闭','state_conflict')
    def signal_shutdown():
        time.sleep(.1)
        UVICORN_SERVER.should_exit=True
    threading.Thread(target=signal_shutdown,daemon=True).start()
    return {'ok':True,'message':'正在安全关闭'}

@app.get('/api/status')
def status():
    with STATUS_CACHE_LOCK:
        cached=STATUS_CACHE['payload']
        if cached and time.monotonic()-STATUS_CACHE['at']<1.5:
            return cached
    with db() as c:
        stats=dict(c.execute('''SELECT count(*) assets,
          sum(CASE WHEN manual_date IS NULL AND (date_source NOT LIKE 'EXIF%' OR date_source IS NULL) THEN 1 ELSE 0 END) uncertain_dates,
          sum(CASE WHEN latitude IS NULL AND coalesce(manual_place,'')='' THEN 1 ELSE 0 END) no_place,
          sum(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) unreadable,
          sum(CASE WHEN face_state=0 THEN 1 ELSE 0 END) faces_pending,
          sum(CASE WHEN face_state=2 THEN 1 ELSE 0 END) face_publish_pending,
          sum(CASE WHEN face_state=2 AND face_error IS NOT NULL THEN 1 ELSE 0 END) face_publish_errors
          FROM assets a
          WHERE '''+ACTIVE_ASSET).fetchone())
        stats['files']=c.execute('SELECT count(*) FROM files').fetchone()[0]
        stats['active_files']=c.execute('SELECT count(*) FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.excluded=0 AND a.excluded=0').fetchone()[0]
        stats['excluded_assets']=c.execute('SELECT count(*) FROM assets a WHERE NOT ('+ACTIVE_ASSET+') AND EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id)').fetchone()[0]
        stats['favorite_photos']=c.execute('SELECT count(*) FROM assets a WHERE coalesce(a.favorite,0)=1 AND '+ACTIVE_ASSET).fetchone()[0]
        stats['duplicates']=c.execute('SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.exists_now=1 AND f.excluded=0 AND a.excluded=0 GROUP BY asset_id HAVING n>1)').fetchone()[0]
        active_person='EXISTS(SELECT 1 FROM faces pf JOIN assets a ON a.id=pf.asset_id WHERE pf.person_id=p.id AND '+ACTIVE_ASSET+')'
        stats['people']=c.execute('SELECT count(*) FROM people p WHERE coalesce(p.ignored,0)=0 AND '+active_person).fetchone()[0]
        stats['named_people']=c.execute('SELECT count(*) FROM people p WHERE confirmed=1 AND coalesce(p.ignored,0)=0 AND '+active_person).fetchone()[0]
        stats['group_photos']=c.execute('''SELECT count(*) FROM (
            SELECT f.asset_id
            FROM faces f JOIN assets a ON a.id=f.asset_id
            WHERE '''+ACTIVE_ASSET+'''
            GROUP BY f.asset_id HAVING count(*)>=2
        )''').fetchone()[0]
        row=c.execute('SELECT * FROM jobs ORDER BY rowid DESC LIMIT 1').fetchone()
        job=dict(row) if row else None
        errors=[dict(r) for r in c.execute('SELECT path,stage,message FROM job_errors WHERE job_id=? ORDER BY id DESC LIMIT 30',(job['id'],))] if job else []
        previous_face_totals=c.execute(
            'SELECT coalesce(sum(face_photos),0),coalesce(sum(face_seconds),0) FROM jobs WHERE id<>?',
            ((job['id'] if job else ''),),
        ).fetchone()
    speed=0
    live=None
    with PROGRESS_LOCK:
        if job and job['id']==PROGRESS['job_id'] and job['status'] in {'running','pausing'}:
            live={key:PROGRESS[key] for key in ('phase','current_stage','current_path','total','discovered','processed','face_photos','faces_found','face_seconds')}
            current=time.monotonic()
            samples=[s for s in PROGRESS['samples'] if current-s[0]<=60]
            if len(samples)>1 and current-samples[0][0]>=2:
                speed=(samples[-1][1]-samples[0][1])/(current-samples[0][0])
    if job and live:
        job.update(live)
    face_average_seconds=0
    if job:
        face_sample_count=int(previous_face_totals[0] or 0)+int(job.get('face_photos') or 0)
        face_sample_seconds=float(previous_face_totals[1] or 0)+float(job.get('face_seconds') or 0)
        if face_sample_count:
            face_average_seconds=face_sample_seconds/face_sample_count
    inventory=None
    inventory_path=DATA/'inventory-estimate.json'
    if inventory_path.exists():
        try:
            stored=json.loads(inventory_path.read_text(encoding='utf-8-sig'))
            inventory={'candidate_files':stored['total'],'status':stored['status'],'as_of':stored.get('updated_at'),'note':'原始候选文件计数，包含副本和素材，不等于个人照片数量'}
        except (OSError,ValueError,KeyError):
            pass
    payload={'stats':{k:v or 0 for k,v in stats.items()},'job':job,'errors':errors,'metadata_per_second':round(speed,2),
            'face_average_seconds':round(face_average_seconds,3),'inventory':inventory,
            'capabilities':{'heif':HEIF,'exiftool':bool(EXIFTOOL),'exiftool_mode':'常驻进程','face_model':(MODEL_ROOT/'models/buffalo_l/w600k_r50.onnx').exists(),'geo':(GEO_ROOT/'geonames/cities500.zip').exists(),'data_dir':str(DATA),'face_runtime':current_face_runtime(),'object_model':model_ready(),'object_runtime':object_runtime() if model_ready() else '未找到','version':'0.3','pid':os.getpid()}}
    with STATUS_CACHE_LOCK:
        STATUS_CACHE['at']=time.monotonic()
        STATUS_CACHE['payload']=payload
    return payload

class ExclusionRequest(BaseModel):
    ids:list[PositiveId]=Field(min_length=1,max_length=1000)
    excluded:bool=True
    reason:str=Field(default='手工排除',max_length=200)
    display_only:bool=False
    operation_id:str|None=Field(default=None,max_length=128)

@app.post('/api/exclusions/assets')
def exclude_assets(body:ExclusionRequest,request:Request):
    operation_id=operation_id_from(body,request)
    with operation_lock(operation_id):
        return exclude_assets_locked(body,request,operation_id)

def exclude_assets_locked(body,request,operation_id):
    released=0
    unique_ids=list(dict.fromkeys(body.ids))
    display_only_restores=0
    request_payload={
        'ids':unique_ids,'excluded':bool(body.excluded),'reason':body.reason,
        'display_only':bool(body.display_only),
    }
    placeholders=','.join('?' for _ in unique_ids)
    with db() as c:
        rows=c.execute(
            f'SELECT id,sha256 FROM assets WHERE id IN ({placeholders})',unique_ids
        ).fetchall()
    found_digest={int(row['id']):row['sha256'] for row in rows}
    missing=[aid for aid in unique_ids if aid not in found_digest]
    if missing:
        raise ApiProblem(
            404,f'照片 {missing[0]} 不存在','not_found',
            operation_id=operation_id,
        )
    with ExitStack() as held_locks:
        for lock_index in asset_lock_indices(found_digest.values()):
            held_locks.enter_context(ASSET_LOCKS[lock_index])
        with db() as c:
            operation_id,existing=prepare_operation(
                c,'asset_exclusion',operation_id,request_payload
            )
            if existing:return existing
            rows=c.execute(
                f'SELECT * FROM assets WHERE id IN ({placeholders})',unique_ids
            ).fetchall()
            found={int(row['id']):row for row in rows}
            missing=[aid for aid in unique_ids if aid not in found]
            if missing:
                raise ApiProblem(
                    404,f'照片 {missing[0]} 不存在','not_found',
                    operation_id=operation_id,
                )
            policy='preserve' if not body.excluded or body.display_only else 'purge'
            cleanup_owner=operation_id if policy=='purge' else None
            for aid in unique_ids:
                old=found[aid]
                if not body.excluded and old['derivative_policy']=='preserve':
                    display_only_restores+=1
                c.execute(
                    '''UPDATE assets
                       SET excluded=?,exclude_reason=?,derivative_policy=?,
                           derivative_operation_id=?
                       WHERE id=?''',
                    (
                        int(body.excluded),body.reason if body.excluded else '',
                        policy,cleanup_owner,aid,
                    ),
                )
                c.execute(
                    'INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',
                    (
                        now(),f'asset:{aid}',
                        json.dumps(
                            {
                                'excluded':old['excluded'],
                                'exclude_reason':old['exclude_reason'],
                                'derivative_policy':old['derivative_policy'],
                                'derivative_operation_id':old['derivative_operation_id'],
                            },ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                'excluded':body.excluded,'reason':body.reason,
                                'display_only':bool(body.excluded and body.display_only),
                                'derivative_policy':policy,
                                'derivative_operation_id':cleanup_owner,
                            },ensure_ascii=False,
                        ),
                    ),
                )
                if body.excluded and not body.display_only:
                    c.execute(
                        '''INSERT INTO operation_items(
                             operation_id,item_kind,item_id,status,updated_at
                           ) VALUES (?,'asset_cleanup',?,'pending',?)''',
                        (operation_id,str(aid),now()),
                    )
            preliminary={
                'updated':len(unique_ids),'released_bytes':0,
                'display_only':bool(body.excluded and body.display_only),
            }
            cleanup_status='pending' if body.excluded and not body.display_only else 'not_applicable'
            finish_operation(
                c,operation_id,preliminary,status='committed',
                state_committed=True,cleanup_status=cleanup_status,
            )
    cleanup_errors=[]
    if body.excluded and not body.display_only:
        for aid in unique_ids:
            try:
                amount=cleanup_asset_cache(aid,operation_id)
                released+=amount
                with db() as c:
                    c.execute(
                        '''UPDATE operation_items SET status='completed',detail=?,updated_at=?
                           WHERE operation_id=? AND item_kind='asset_cleanup' AND item_id=?''',
                        (json.dumps({'released_bytes':amount}),now(),operation_id,str(aid)),
                    )
            except Exception as exc:
                cleanup_errors.append({'asset_id':aid,'detail':str(exc)})
                with db() as c:
                    c.execute(
                        '''UPDATE operation_items SET status='failed',detail=?,updated_at=?
                           WHERE operation_id=? AND item_kind='asset_cleanup' AND item_id=?''',
                        (str(exc),now(),operation_id,str(aid)),
                    )
    with STATUS_CACHE_LOCK:
        STATUS_CACHE['payload']=None; STATUS_CACHE['at']=0
    if body.excluded and body.display_only:
        message='已排除显示，原照片和识别信息保留'
    elif body.excluded:
        message='排除已记住，原文件保留'
    elif display_only_restores==len(unique_ids):
        message='已恢复显示，原照片和识别信息仍然保留'
    else:
        message='已撤销单张排除；若仍受目录规则影响，需要同时恢复目录。缩略图可按需重建，人脸需重扫。'
    cleanup_status='failed' if cleanup_errors else (
        'completed' if body.excluded and not body.display_only else 'not_applicable'
    )
    with db() as c:
        return finish_operation(
            c,operation_id,{
                'updated':len(unique_ids),'released_bytes':released,
                'display_only':bool(body.excluded and body.display_only),
                'message':message,'cleanup_errors':cleanup_errors,
            },status='committed',state_committed=True,
            cleanup_status=cleanup_status,
            error_code='cleanup_incomplete' if cleanup_errors else None,
            error_detail='状态已提交，但部分缓存清理可重试' if cleanup_errors else None,
        )


@app.get('/api/operations/{operation_id}')
def get_operation(operation_id:str):
    with db() as c:
        row=c.execute(
            'SELECT * FROM operations WHERE operation_id=?',(operation_id,)
        ).fetchone()
        if not row:raise ApiProblem(404,'操作不存在','not_found')
        receipt=operation_receipt(row)
        receipt['items']=[
            dict(item) for item in c.execute(
                '''SELECT item_kind,item_id,status,detail,updated_at
                   FROM operation_items WHERE operation_id=?
                   ORDER BY item_kind,item_id''',(operation_id,)
            )
        ]
    return receipt


@app.post('/api/operations/{operation_id}/retry-cleanup')
def retry_operation_cleanup(operation_id:str):
    if not operation_id or len(operation_id)>128:
        raise ApiProblem(400,'操作编号无效','invalid_request')
    with operation_lock(operation_id):
        return retry_operation_cleanup_locked(operation_id)

def retry_operation_cleanup_locked(operation_id):
    with db() as c:
        operation=c.execute(
            'SELECT * FROM operations WHERE operation_id=?',(operation_id,)
        ).fetchone()
        if not operation:raise ApiProblem(404,'操作不存在','not_found')
        if operation['kind']!='asset_exclusion' or not operation['state_committed']:
            raise ApiProblem(409,'这个操作没有可重试的缓存清理','state_conflict')
        ids=[
            int(row['item_id']) for row in c.execute(
                '''SELECT item_id FROM operation_items
                   WHERE operation_id=? AND item_kind='asset_cleanup'
                     AND status!='completed' ORDER BY CAST(item_id AS INTEGER)''',
                (operation_id,),
            )
        ]
    released=0
    failures=[]
    for aid in ids:
        try:
            amount=cleanup_asset_cache(aid,operation_id);released+=amount
            with db() as c:
                c.execute(
                    '''UPDATE operation_items SET status='completed',detail=?,updated_at=?
                       WHERE operation_id=? AND item_kind='asset_cleanup' AND item_id=?''',
                    (json.dumps({'released_bytes':amount}),now(),operation_id,str(aid)),
                )
        except Exception as exc:
            failures.append({'asset_id':aid,'detail':str(exc)})
            with db() as c:
                c.execute(
                    '''UPDATE operation_items SET status='failed',detail=?,updated_at=?
                       WHERE operation_id=? AND item_kind='asset_cleanup' AND item_id=?''',
                    (str(exc),now(),operation_id,str(aid)),
                )
    with db() as c:
        row=c.execute(
            'SELECT * FROM operations WHERE operation_id=?',(operation_id,)
        ).fetchone()
        result=json.loads(row['result_json'] or '{}')
        result['released_bytes']=int(result.get('released_bytes') or 0)+released
        result['cleanup_errors']=failures
        return finish_operation(
            c,operation_id,result,status='committed',state_committed=True,
            cleanup_status='failed' if failures else 'completed',
            error_code='cleanup_incomplete' if failures else None,
            error_detail='状态已提交，但部分缓存清理可重试' if failures else None,
        )


def directory_predicate(path):
    return directory_clause(path)

@app.get('/api/exclusions')
def list_exclusions():
    with db() as c:
        rules=[dict(r) for r in c.execute('SELECT * FROM excluded_roots ORDER BY id DESC')]
    return {'roots':rules}

@app.get('/api/exclusions/preview')
def preview_exclusion(path:str):
    p=Path(path.strip().strip('"')).expanduser().resolve()
    if not p.is_dir() or str(p).startswith('\\\\'):
        raise HTTPException(400,'请选择可访问的本机文件夹')
    where,values=directory_predicate(p)
    with db() as c:
        row=c.execute('SELECT count(*) files,count(DISTINCT asset_id) assets FROM files WHERE '+where,values).fetchone()
    return {'path':str(p),**dict(row),'scope':'包含全部子目录；尚未扫描的内容将直接跳过；其他目录中的保留副本不受影响'}

class DirectoryExclusion(BaseModel):
    path:str=Field(min_length=1,max_length=32760)

@app.post('/api/exclusions/roots')
def exclude_directory(body:DirectoryExclusion):
    preview=preview_exclusion(body.path)
    where,values=directory_predicate(preview['path'])
    with RULE_LOCK:
        with db() as c:
            c.execute('INSERT OR IGNORE INTO excluded_roots(path,created_at) VALUES (?,?)',(preview['path'],now()))
            c.execute('UPDATE files SET excluded=1 WHERE '+where,values)
            c.execute('INSERT INTO edits(created_at,target,after_json) VALUES (?,?,?)',(now(),'excluded_root',json.dumps(preview,ensure_ascii=False)))
        refresh_rules()
    return {**preview,'released_bytes':0}

@app.delete('/api/exclusions/roots/{rid}')
def restore_directory(rid:int):
    with RULE_LOCK:
        with db() as c:
            rule=c.execute('SELECT * FROM excluded_roots WHERE id=?',(rid,)).fetchone()
            if not rule:
                raise HTTPException(404,'排除规则不存在')
            c.execute('DELETE FROM excluded_roots WHERE id=?',(rid,))
            c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(now(),'excluded_root',json.dumps(dict(rule),ensure_ascii=False),'"restored"'))
        refresh_rules()
        where,values=directory_predicate(rule['path'])
        with db() as c:
            files=c.execute('SELECT id,path FROM files WHERE '+where,values).fetchall()
            for f in files:
                c.execute('UPDATE files SET excluded=? WHERE id=?',(int(path_excluded(f['path'])),f['id']))
    return {'restored':True,'message':'目录规则已移除；仍保留其他目录规则及单张排除。未入库内容需重新扫描。'}

@app.get('/api/drives')
def drives():
    if os.name=='nt':
        import ctypes
        mask=ctypes.windll.kernel32.GetLogicalDrives()
        roots=[f'{chr(65+i)}:\\' for i in range(26) if mask&(1<<i) and ctypes.windll.kernel32.GetDriveTypeW(f'{chr(65+i)}:\\') in {2,3}]
    else: roots=[str(Path.home())]
    return {'roots':roots}

@app.get('/api/folders')
def folders(path:str='',scope:str='browse'):
    if scope not in {'browse','scan'}:
        raise HTTPException(400,'未知的目录选择用途')
    if not path:
        roots=drives()['roots']
        if scope=='browse':
            roots=[p for p in roots if str(p).upper().startswith('I:')]
        return {'path':'','parent':None,'scope':scope,'items':[{'name':p,'path':p} for p in roots]}
    p=Path(path).expanduser().resolve()
    if not p.is_dir(): raise HTTPException(400,'文件夹不存在或无法访问')
    if str(p).startswith('\\\\'): raise HTTPException(400,'第一版只浏览本机目录')
    try:
        items=[]
        for child in p.iterdir():
            try:
                if (child.is_dir() and not child.is_symlink()
                    and not getattr(child.stat(),'st_file_attributes',0)&0x400
                    and not (scope=='scan' and child.resolve()==DATA)):
                    items.append({'name':child.name,'path':str(child)})
            except OSError:
                continue
        return {'path':str(p),'parent':str(p.parent) if p.parent!=p else '', 'scope':scope,'items':sorted(items,key=lambda x:x['name'].lower())}
    except OSError as e: raise HTTPException(400,str(e))

def asset_dict(row):
    d=dict(row)
    d['face_status']='committed_pending_publish' if d.get('face_state')==2 else None
    if d.get('face_state')==2:
        d['face_status_message']=(
            '人脸结果已提交，裁剪发布失败，等待隔离恢复'
            if d.get('face_error')
            else '人脸结果已提交，裁剪文件待恢复'
        )
    else:
        d['face_status_message']=None
    d['effective_date']=d['manual_date'] or d['captured_at']
    d['effective_place']=(d['manual_place'] or d['place'] or '').replace('附近','') or None
    d['effective_precision']=d['manual_precision'] if d['manual_date'] else d['date_precision']
    d['effective_source']='人工确认' if d['manual_date'] else d['date_source']
    d.pop('metadata',None)
    return d

@app.get('/api/photos')
def photos(q:str='',filter:str='all',person:str='',offset:int=0,limit:int=60,directory:str='',sort:str='date_desc',sequence:bool=False,max_id:int=0,around:int=0,tail:bool=False,date_from:str='',date_to:str='',place:str='',nearby:int=0,radius_m:int=100,map_cell:float=0,map_lat_bucket:float|None=None,map_lng_bucket:float|None=None,map_west:float|None=None,map_south:float|None=None,map_east:float|None=None,map_north:float|None=None):
    if offset<0 or limit<1 or limit>500 or max_id<0 or around<0 or nearby<0:
        raise ApiProblem(400,'分页或照片编号参数无效','invalid_request')
    try:
        with db() as c:
            result=fetch_photos(
                c, q=q, filter=filter, person=person, offset=offset, limit=limit,
                directory=directory, sort=sort, sequence=sequence, max_id=max_id, around=around, tail=tail,
                date_from=date_from, date_to=date_to, place=place, nearby=nearby, radius_m=radius_m,
                map_cell=map_cell, map_lat_bucket=map_lat_bucket, map_lng_bucket=map_lng_bucket,
                map_west=map_west, map_south=map_south, map_east=map_east, map_north=map_north,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if sequence:
        payload={'ids':result['ids'],'total':result['total'],'offset':result['offset'],'max_id':result['max_id'],'has_more':result['has_more']}
        if result.get('missing'):
            payload['missing']=True
            payload['around']=result.get('around')
        return payload
    items=[dict(asset_dict(item), path=item.get('path')) for item in result.get('items') or []]
    return {'total':result['total'],'items':items,'max_id':result['max_id']}


def nearby_distance_m(latitude, longitude, anchor):
    latitude_scale=111_132.0
    longitude_scale=max(1.0,111_320.0*abs(math.cos(math.radians(anchor['latitude']))))
    north=(float(latitude)-anchor['latitude'])*latitude_scale
    east=(float(longitude)-anchor['longitude'])*longitude_scale
    return round(math.hypot(north,east),1)


@app.get('/api/photos/{aid}/nearby')
def nearby_photos(aid:int, radius_m:int=100):
    try:
        with db() as c:
            clause,values,anchor=nearby_photo_spec(c,aid,radius_m)
            where=ACTIVE_ASSET+' AND '+clause
            total=c.execute('SELECT count(*) FROM assets a WHERE '+where,values).fetchone()[0]
            rows=c.execute(
                '''SELECT a.id,a.latitude,a.longitude,a.width,a.height,
                          coalesce(nullif(a.manual_place,''),a.place) effective_place,
                          coalesce(a.manual_date,a.captured_at) effective_date
                   FROM assets a WHERE '''+where+
                ''' ORDER BY CASE WHEN a.id=? THEN 0 ELSE 1 END,
                            coalesce(a.manual_date,a.captured_at) DESC,a.id DESC LIMIT 500''',
                values+[aid],
            ).fetchall()
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    items=[]
    for row in rows:
        item=dict(row)
        item['distance_m']=nearby_distance_m(row['latitude'],row['longitude'],anchor)
        items.append(item)
    return {
        'anchor':anchor,
        'radius_m':int(radius_m),
        'total':int(total),
        'points':items,
        'samples':items[:9],
        'points_truncated':total>len(items),
    }


class NearbyPlaceRequest(BaseModel):
    place:str=Field(min_length=1,max_length=200)
    radius_m:int=Field(default=100,ge=1,le=500)


@app.post('/api/photos/{aid}/nearby-place')
def set_nearby_place(aid:int, body:NearbyPlaceRequest):
    place=(body.place or '').strip()
    if not place:
        raise HTTPException(400,'请填写地点名称')
    try:
        with db() as c:
            clause,values,anchor=nearby_photo_spec(c,aid,body.radius_m)
            rows=c.execute(
                'SELECT a.id,a.manual_place FROM assets a WHERE '+ACTIVE_ASSET+' AND '+clause,
                values,
            ).fetchall()
            changed=0
            for row in rows:
                before=row['manual_place']
                if (before or '')==place:
                    continue
                c.execute('UPDATE assets SET manual_place=? WHERE id=?',(place,row['id']))
                c.execute(
                    'INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',
                    (now(),f'asset:{row["id"]}',json.dumps({'manual_place':before},ensure_ascii=False),json.dumps({
                        'manual_place':place,
                        'source_photo_id':aid,
                        'radius_m':body.radius_m,
                    },ensure_ascii=False)),
                )
                changed+=1
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    return {
        'name':place,
        'radius_m':body.radius_m,
        'matched':len(rows),
        'updated':changed,
        'anchor':anchor,
        'message':f'已把 {len(rows)} 张照片标为“{place}”',
    }



class ObjectProbeRequest(BaseModel):
    limit:int=Field(default=24,ge=1,le=2000)

def _open_asset_image(aid):
    with db() as c:
        row=c.execute('SELECT path FROM files WHERE asset_id=? AND exists_now=1 AND excluded=0 ORDER BY id LIMIT 1',(aid,)).fetchone()
    if not row:
        raise HTTPException(404,'没有可读取的原图')
    path=Path(row['path'])
    if not path.exists():
        raise HTTPException(404,'原文件缺失')
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert('RGB'), str(path)

def save_object_tags(aid, tags):
    with db() as c:
        c.execute('DELETE FROM object_tags WHERE asset_id=?',(aid,))
        c.executemany('INSERT INTO object_tags(asset_id,label,score) VALUES (?,?,?)',[(aid,item['label'],item['score']) for item in tags])
        c.execute('UPDATE assets SET object_state=1, object_error=NULL WHERE id=?',(aid,))

@app.get('/api/objects')
def list_objects(limit:int=40):
    with db() as c:
        rows=c.execute('SELECT label, count(*) n, avg(score) score FROM object_tags GROUP BY label ORDER BY n DESC, score DESC LIMIT ?',(min(max(limit,1),80),)).fetchall()
        tagged=c.execute('SELECT count(DISTINCT asset_id) FROM object_tags').fetchone()[0]
        pending=c.execute('SELECT count(*) FROM assets a WHERE '+ACTIVE_ASSET+' AND coalesce(object_state,0)=0 AND a.error IS NULL').fetchone()[0]
    return {'items':[dict(r) for r in rows],'tagged':tagged,'pending':pending,'ready':model_ready(),'runtime':object_runtime() if model_ready() else '未找到'}

@app.post('/api/objects/probe')
def probe_objects(body:ObjectProbeRequest):
    if not model_ready():
        raise HTTPException(400,'未找到本地 SigLIP2 模型')
    with db() as c:
        rows=c.execute('SELECT a.id FROM assets a WHERE '+ACTIVE_ASSET+' AND a.error IS NULL AND coalesce(a.object_state,0)=0 AND EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.exists_now=1 AND f.excluded=0) ORDER BY a.id DESC LIMIT ?',(body.limit,)).fetchall()
    started=time.perf_counter()
    items=[]
    for row in rows:
        aid=row['id']
        try:
            image, path=_open_asset_image(aid)
            tags=classify_image(image)
            save_object_tags(aid, tags)
            items.append({'id':aid,'path':path,'tags':tags})
        except Exception as exc:
            with db() as c:
                c.execute('UPDATE assets SET object_state=-1, object_error=? WHERE id=?',(str(exc),aid))
            items.append({'id':aid,'error':str(exc)})
    elapsed=round(time.perf_counter()-started, 3)
    return {'items':items,'seconds':elapsed,'per_image':round(elapsed/max(len(items),1),3),'runtime':object_runtime(),'count':len(items)}


@app.post("/api/objects/scan")
def start_object_scan(body:ObjectProbeRequest):
    if not model_ready():
        raise HTTPException(400,"未找到本地 SigLIP2 模型")
    with OBJECT_JOB_LOCK:
        if OBJECT_JOB.get("status") in {"running","pausing"}:
            raise HTTPException(409,"物体扫描正在进行")
        OBJECT_JOB.update(status="running",processed=0,limit=body.limit,tagged=0,errors=0,message="正在加载物体模型",started_at=now(),finished_at=None)
    threading.Thread(target=run_object_scan,args=(body.limit,),daemon=True).start()
    return {"ok":True,"limit":body.limit}

def run_object_scan(limit):
    try:
        with db() as c:
            rows=c.execute("SELECT a.id FROM assets a WHERE "+ACTIVE_ASSET+" AND a.error IS NULL AND coalesce(a.object_state,0)=0 AND EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.exists_now=1 AND f.excluded=0) ORDER BY a.id DESC LIMIT ?",(limit,)).fetchall()
        ids=[r["id"] for r in rows]
        with OBJECT_JOB_LOCK:
            OBJECT_JOB.update(limit=len(ids),message="开始识别")
        batch=[]
        def flush(items):
            if not items: return
            tags_list=classify_images([image for image,_aid in items])
            for (image, aid), tags in zip(items, tags_list):
                save_object_tags(aid, tags)
                with OBJECT_JOB_LOCK:
                    OBJECT_JOB["tagged"]=OBJECT_JOB.get("tagged",0)+1
                    OBJECT_JOB["processed"]=OBJECT_JOB.get("processed",0)+1
        for aid in ids:
            with OBJECT_JOB_LOCK:
                if OBJECT_JOB.get("status")!="running":
                    break
            try:
                image, path=_open_asset_image(aid)
                batch.append((image, aid))
                if len(batch)>=BATCH_SIZE:
                    flush(batch); batch=[]
            except Exception as exc:
                with db() as c:
                    c.execute("UPDATE assets SET object_state=-1, object_error=? WHERE id=?",(str(exc),aid))
                with OBJECT_JOB_LOCK:
                    OBJECT_JOB["errors"]=OBJECT_JOB.get("errors",0)+1
                    OBJECT_JOB["processed"]=OBJECT_JOB.get("processed",0)+1
                    OBJECT_JOB["message"]=str(exc)
        flush(batch)
        with OBJECT_JOB_LOCK:
            OBJECT_JOB.update(status="completed",finished_at=now(),message="本批完成")
    except Exception as exc:
        with OBJECT_JOB_LOCK:
            OBJECT_JOB.update(status="failed",finished_at=now(),message=str(exc))

@app.get("/api/objects/job")
def object_job():
    with OBJECT_JOB_LOCK:
        job=dict(OBJECT_JOB)
    with db() as c:
        tagged=c.execute("SELECT count(DISTINCT asset_id) FROM object_tags").fetchone()[0]
        pending=c.execute("SELECT count(*) FROM assets a WHERE "+ACTIVE_ASSET+" AND coalesce(object_state,0)=0 AND a.error IS NULL").fetchone()[0]
    job.update(tagged_total=tagged,pending=pending,ready=model_ready(),runtime=object_runtime() if model_ready() else "未找到")
    return job

@app.get('/api/photos/{aid}')
def photo_detail(aid:int):
    if aid<=0:raise ApiProblem(422,'照片编号必须为正整数','invalid_request')
    with db() as c:
        row=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
        if not row: raise HTTPException(404,'照片不存在')
        result=asset_dict(row);result['metadata']=json.loads(row['metadata'])
        result['files']=[dict(x) for x in c.execute('SELECT * FROM files WHERE asset_id=? ORDER BY exists_now DESC,id',(aid,))]
        result['faces']=[dict(x) for x in c.execute('SELECT f.id,f.person_id,f.bbox,p.name,p.alias,p.confirmed,p.ignored FROM faces f JOIN people p ON p.id=f.person_id WHERE asset_id=?',(aid,))]
        result['objects']=[dict(x) for x in c.execute('SELECT label,score FROM object_tags WHERE asset_id=? ORDER BY score DESC',(aid,))]
        result['history']=[dict(x) for x in c.execute('SELECT * FROM edits WHERE target=? ORDER BY id DESC LIMIT 20',(f'asset:{aid}',))]
        result['in_library']=bool(c.execute('SELECT 1 FROM assets a WHERE id=? AND '+ACTIVE_ASSET,(aid,)).fetchone())
    return result

class FavoriteRequest(BaseModel):
    favorite:bool=True

@app.put('/api/photos/{aid}/favorite')
def set_photo_favorite(aid:int, body:FavoriteRequest):
    if aid<=0:raise ApiProblem(422,'照片编号必须为正整数','invalid_request')
    with db() as c:
        row=c.execute('SELECT favorite FROM assets WHERE id=?',(aid,)).fetchone()
        if not row:
            raise HTTPException(404,'照片不存在')
        before=bool(row['favorite'])
        favorite=bool(body.favorite)
        if before!=favorite:
            c.execute('UPDATE assets SET favorite=? WHERE id=?',(int(favorite),aid))
            c.execute(
                'INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',
                (now(),f'asset:{aid}',json.dumps({'favorite':before},ensure_ascii=False),json.dumps({'favorite':favorite},ensure_ascii=False)),
            )
        count=c.execute('SELECT count(*) FROM assets a WHERE coalesce(a.favorite,0)=1 AND '+ACTIVE_ASSET).fetchone()[0]
    with STATUS_CACHE_LOCK:
        STATUS_CACHE['payload']=None; STATUS_CACHE['at']=0
    return {'id':aid,'favorite':favorite,'favorite_count':count,'changed':before!=favorite}

@app.get('/api/timeline')
def timeline():
    with db() as c:
        rows=c.execute('''SELECT substr(coalesce(a.manual_date,a.captured_at),1,4) year, count(*) n
            FROM assets a WHERE '''+ACTIVE_ASSET+'''
            GROUP BY year ORDER BY year DESC''').fetchall()
        month_rows=c.execute('''SELECT substr(coalesce(a.manual_date,a.captured_at),1,7) month, count(*) n
            FROM assets a WHERE '''+ACTIVE_ASSET+'''
            AND substr(coalesce(a.manual_date,a.captured_at),1,7) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
            GROUP BY month ORDER BY month DESC''').fetchall()
    years=[{'year':r['year'] or 'unknown','count':r['n']} for r in rows]
    months=[{'month':r['month'],'count':r['n']} for r in month_rows]
    return {'years':years,'months':months,'dated':sum(x['count'] for x in years if x['year']!='unknown')}

@app.get('/api/groups')
def groups():
    buckets={n:{'people':n,'key':str(n),'count':0,'cover':None} for n in range(2,10)}
    buckets[10]={'people':10,'key':'10plus','count':0,'cover':None}
    with db() as c:
        rows=c.execute('''SELECT CASE WHEN n>=10 THEN 10 ELSE n END bucket, count(*) photos, min(asset_id) cover FROM (
            SELECT f.asset_id, count(*) n
            FROM faces f JOIN assets a ON a.id=f.asset_id
            WHERE '''+ACTIVE_ASSET+'''
            GROUP BY f.asset_id HAVING n>=2
        ) GROUP BY bucket ORDER BY bucket''').fetchall()
    for row in rows:
        bucket=buckets.get(row['bucket'])
        if not bucket: continue
        bucket['count']=row['photos']
        bucket['cover']=row['cover']
    items=[buckets[n] for n in range(2,11)]
    return {'items':items,'photos':sum(x['count'] for x in items)}

@app.get('/api/places/suggest')
def suggest_places(q:str='', place:str=''):
    needle=(q or place or '').strip()
    names=place_catalog()
    hits=[]
    if needle:
        for name in names:
            if needle in name:
                hits.append({'name':name,'kind':'catalog'})
            if len(hits)>=12: break
    matched=bool(hits)
    if needle and not any(h['name']==needle for h in hits):
        hits.append({'name':needle,'kind':'custom'})
    return {'query':needle,'matched':matched,'items':hits}

class PlaceRenameRequest(BaseModel):
    from_place:str=Field(min_length=1,max_length=200)
    to_place:str=Field(min_length=1,max_length=200)
    remember:bool=True

@app.post('/api/places/rename')
def rename_place(body:PlaceRenameRequest):
    source=(body.from_place or '').strip()
    target=(body.to_place or '').strip()
    if not source or not target: raise HTTPException(400,'请填写原地点和新地点')
    if source==target: raise HTTPException(400,'新地点与原地点相同，没有需要保存的修改')
    with db() as c:
        rows=c.execute(
            "SELECT a.id, a.manual_place, a.latitude, a.longitude FROM assets a WHERE "+ACTIVE_ASSET+" AND coalesce(nullif(a.manual_place, ''), a.place)=?",
            (source,)
        ).fetchall()
        ids=[]
        coords=[]
        for row in rows:
            ids.append(row['id'])
            if row['latitude'] is not None and row['longitude'] is not None:
                coords.append((row['latitude'], row['longitude']))
            c.execute('UPDATE assets SET manual_place=? WHERE id=?',(target,row['id']))
            c.execute(
                'INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',
                (now(), f'asset:{row["id"]}', json.dumps({'manual_place':row['manual_place']}, ensure_ascii=False), json.dumps({'manual_place':target}, ensure_ascii=False))
            )
        remembered=False
        if body.remember and ids and coords:
            lat=sum(x[0] for x in coords)/len(coords)
            lon=sum(x[1] for x in coords)/len(coords)
            upsert_place_rule(c, target, lat, lon, 0.35, '用户确认：'+target)
            remembered=True
    if body.remember:
        attach_place_rules()
    catalog=place_catalog()
    matched=target in catalog
    return {'updated':len(ids),'name':target,'matched':bool(matched or remembered), 'remembered':remembered}

@app.get('/api/places')
def places(q:str='', offset:int=0, limit:int=80, west:float|None=None, south:float|None=None, east:float|None=None, north:float|None=None, zoom:float=11):
    values=[]
    having="HAVING place IS NOT NULL AND place!=''"
    needle=(q or '').strip()
    if needle:
        having += ' AND place LIKE ?'
        values.append('%'+needle+'%')
    grouped='SELECT coalesce(nullif(a.manual_place,\'\'), a.place) place, count(*) n, avg(a.latitude) latitude, avg(a.longitude) longitude FROM assets a WHERE '+ACTIVE_ASSET+' GROUP BY place '
    with db() as c:
        if None not in (west,south,east,north):
            cell=max(0.02, min(8.0, 360/(2**max(1,min(float(zoom),18)))))
            grouped_map='''SELECT CAST(floor(a.latitude/?) AS INTEGER) lat_bucket,
                                  CAST(floor(a.longitude/?) AS INTEGER) lng_bucket,
                                  avg(a.latitude) lat,avg(a.longitude) lon,
                                  count(*) n,min(a.id) anchor_id,
                                  max(coalesce(nullif(a.manual_place,''),a.place)) place
                           FROM assets a WHERE '''+ACTIVE_ASSET+'''
                             AND a.latitude BETWEEN ? AND ?
                             AND a.longitude BETWEEN ? AND ?
                           GROUP BY lat_bucket,lng_bucket'''
            map_values=(cell,cell,south,north,west,east)
            total_clusters=c.execute(
                'SELECT count(*) FROM ('+grouped_map+') grouped',map_values
            ).fetchone()[0]
            rows=c.execute(
                grouped_map+' ORDER BY n DESC,lat_bucket,lng_bucket LIMIT 400',
                map_values,
            ).fetchall()
            return {
                'mode':'map','zoom':zoom,
                'clusters':[{
                    'latitude':r['lat'],'longitude':r['lon'],'count':r['n'],
                    'place':r['place'],'anchor_id':r['anchor_id'],'cell':cell,
                    'lat_bucket':r['lat_bucket'],'lng_bucket':r['lng_bucket'],
                } for r in rows],
                'total_clusters':total_clusters,
                'truncated':total_clusters>len(rows),
            }
        total=c.execute('SELECT count(*) FROM ('+grouped+having+') t',values).fetchone()[0]
        rows=c.execute(grouped+having+' ORDER BY n DESC, place LIMIT ? OFFSET ?',values+[min(max(limit,1),200),max(offset,0)]).fetchall()
    items=[{'place':r['place'],'count':r['n'],'latitude':r['latitude'],'longitude':r['longitude']} for r in rows]
    return {'places':items,'total':total,'offset':max(offset,0),'limit':min(max(limit,1),200)}

class EditRequest(BaseModel):
    ids:list[PositiveId]=Field(min_length=1,max_length=1000)
    manual_date:str|None=None
    manual_precision:str|None=None
    manual_place:str|None=Field(default=None,max_length=200)
    notes:str|None=Field(default=None,max_length=5000)

@app.patch('/api/photos')
def edit_photos(body:EditRequest):
    changes=body.model_dump(exclude_unset=True); ids=list(dict.fromkeys(changes.pop('ids')))
    if not changes: raise HTTPException(400,'没有修改内容')
    for key in ['manual_date','manual_place','manual_precision']:
        if key in changes: changes[key]=(changes[key] or '').strip() or None
    if 'notes' in changes: changes['notes']=changes['notes'] or ''
    if changes.get('manual_precision') not in {None,'年','月','日','范围 / 描述'}:
        raise HTTPException(400,'日期精度无效')
    date=changes.get('manual_date')
    if date:
        precision=changes.get('manual_precision')
        patterns={'年':r'\d{4}','月':r'\d{4}-\d{2}','日':r'\d{4}-\d{2}-\d{2}','范围 / 描述':r'.{1,100}'}
        if precision not in patterns or not re.fullmatch(patterns[precision],date):
            raise HTTPException(400,'日期格式应与精度一致：年 2012、月 2012-07、日 2012-07-21；大致范围请选择“范围 / 描述”')
        try:
            if precision in {'年','月','日'}: datetime.strptime(date,{'年':'%Y','月':'%Y-%m','日':'%Y-%m-%d'}[precision])
        except ValueError: raise HTTPException(400,'日期不存在，请检查年月日')
    with db() as c:
        for aid in ids:
            before=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
            if not before: raise HTTPException(404,f'照片 {aid} 不存在')
            c.execute('UPDATE assets SET '+','.join(f'{k}=?' for k in changes)+' WHERE id=?',list(changes.values())+[aid])
            c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',
                      (now(),f'asset:{aid}',json.dumps({k:before[k] for k in changes},ensure_ascii=False),json.dumps(changes,ensure_ascii=False)))
    return {'updated':len(ids)}

@app.get('/api/people')
def people(ignored:int=0, q:str='', offset:int=0, limit:int=48, ids:str='', named:int=0, sort:str='photos'):
    try:
        with db() as c:
            result=fetch_people(c, ignored=ignored, q=q, offset=offset, limit=limit, ids=ids, named=named, sort=sort)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'total':result['total'],'items':result['items'],'offset':result['offset'],'limit':result['limit']}

def normalize_person_text(value):
    """Normalize only surrounding and repeated ordinary spaces for name checks."""
    return re.sub(r' {2,}', ' ', str(value or '').strip())

@app.get('/api/people/matches')
def people_matches(name:str='', alias:str='', exclude_id:int=0):
    name_key=normalize_person_text(name)
    alias_key=normalize_person_text(alias)
    if not name_key:
        return {'exact':[], 'same_name':[]}
    with db() as c:
        rows=c.execute(
            '''SELECT p.id,p.name,p.alias,p.confirmed,p.ignored,
                      count(f.id) face_count,count(DISTINCT f.asset_id) photo_count,
                      coalesce(max(CASE WHEN f.id=p.cover_face_id THEN f.id END),min(f.id)) cover
               FROM people p JOIN faces f ON f.person_id=p.id
               JOIN assets a ON a.id=f.asset_id
               WHERE p.confirmed=1 AND coalesce(p.ignored,0)=0 AND p.id<>? AND '''+ACTIVE_ASSET+'''
               GROUP BY p.id ORDER BY photo_count DESC,p.id''',
            (int(exclude_id or 0),),
        ).fetchall()
    same_name=[]
    exact=[]
    for row in rows:
        item=dict(row)
        item_name=normalize_person_text(item.get('name'))
        item_alias=normalize_person_text(item.get('alias'))
        if item_name!=name_key:
            continue
        same_name.append(item)
        if item_alias==alias_key:
            exact.append(item)
    return {'exact':exact, 'same_name':same_name}

@app.get('/api/people/{pid}')
def person_detail(pid:int, offset:int=0, limit:int=48):
    with db() as c:
        row=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not row: raise HTTPException(404,'人物不存在')
        active_faces='FROM faces f JOIN assets a ON a.id=f.asset_id WHERE f.person_id=? AND '+ACTIVE_ASSET
        face_count=c.execute('SELECT count(*) '+active_faces,(pid,)).fetchone()[0]
        photo_count=c.execute('SELECT count(DISTINCT f.asset_id) '+active_faces,(pid,)).fetchone()[0]
        cover=c.execute(
            'SELECT coalesce(max(CASE WHEN f.id=? THEN f.id END),min(f.id)) '+active_faces,
            (row['cover_face_id'],pid),
        ).fetchone()[0]
        page=min(max(limit,1),100)
        start=max(offset,0)
        faces=[dict(x) for x in c.execute(
            'SELECT f.id,f.asset_id,f.score,f.reviewed '+active_faces+' ORDER BY f.id LIMIT ? OFFSET ?',
            (pid,page,start),
        )]
    return {**dict(row),'faces':faces,'face_count':face_count,'photo_count':photo_count,'cover':cover,'offset':start,'limit':page,'more':start+len(faces)<face_count}

class PersonEdit(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    alias:str=Field(default='',max_length=100)

@app.patch('/api/people/{pid}')
def name_person(pid:int,body:PersonEdit):
    if pid<=0:raise ApiProblem(422,'人物编号必须为正整数','invalid_request')
    name=normalize_person_text(body.name)
    if not name: raise HTTPException(400,'请输入姓名')
    alias=normalize_person_text(body.alias)
    with db() as c:
        old=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not old: raise HTTPException(404,'人物不存在')
        c.execute('UPDATE people SET name=?,alias=?,confirmed=1,ignored=0,suggested_person_id=NULL WHERE id=?',(name,alias,pid))
        c.execute('UPDATE faces SET reviewed=1,ignored=0 WHERE person_id=?',(pid,))
        c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(now(),f'person:{pid}',json.dumps(dict(old),ensure_ascii=False),json.dumps({'name':name,'alias':alias},ensure_ascii=False)))
    invalidate_face_index()
    return {'ok':True}

class PersonCoverEdit(BaseModel):
    face_id:PositiveId

@app.put('/api/people/{pid}/cover')
def set_person_cover(pid:int,body:PersonCoverEdit):
    if pid<=0:raise ApiProblem(422,'人物编号必须为正整数','invalid_request')
    with db() as c:
        person=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not person: raise HTTPException(404,'人物不存在')
        if not int(person['confirmed'] or 0) or int(person['ignored'] or 0) or not normalize_person_text(person['name']):
            raise HTTPException(400,'请先给人物确认姓名，再选择首页头像')
        face=c.execute(
            'SELECT f.id FROM faces f JOIN assets a ON a.id=f.asset_id '
            'WHERE f.id=? AND f.person_id=? AND coalesce(f.ignored,0)=0 AND '+ACTIVE_ASSET,
            (body.face_id,pid),
        ).fetchone()
        if not face: raise HTTPException(400,'这张人脸不属于当前人物，或照片已不在资料库中')
        before=person['cover_face_id']
        c.execute('UPDATE people SET cover_face_id=? WHERE id=?',(body.face_id,pid))
        c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(
            now(),f'person:{pid}:cover',
            json.dumps({'cover_face_id':before},ensure_ascii=False),
            json.dumps({'cover_face_id':body.face_id},ensure_ascii=False),
        ))
    return {'ok':True,'cover':body.face_id}

class MergeRequest(BaseModel):
    target_id:PositiveId
    operation_id:str|None=Field(default=None,max_length=128)
    confirmation_token:str|None=Field(default=None,max_length=128)

@app.post('/api/people/{pid}/merge')
def merge_person(pid:int,body:MergeRequest,request:Request):
    if pid<=0:raise ApiProblem(422,'人物编号必须为正整数','invalid_request')
    if pid==body.target_id: raise ApiProblem(400,'不能合并到自己','invalid_request')
    operation_id=operation_id_from(body,request)
    with operation_lock(operation_id):
        return merge_person_locked(pid,body,operation_id)

def merge_person_locked(pid,body,operation_id):
    request_payload={'source_id':pid,'target_id':body.target_id}
    with db() as c:
        operation_id,existing=prepare_operation(
            c,'person_merge',operation_id,request_payload
        )
        if existing:return existing
        target=c.execute('SELECT * FROM people WHERE id=?',(body.target_id,)).fetchone()
        source=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not target or not source:
            raise ApiProblem(404,'人物不存在','not_found',operation_id=operation_id)
        if int(source['confirmed'] or 0) and not int(target['confirmed'] or 0):
            raise ApiProblem(
                409,'不能把已确认人物合并到待确认候选','relationship_conflict',
                operation_id=operation_id,
            )
        seen={pid}
        cursor=target
        while cursor and cursor['suggested_person_id'] is not None:
            suggested=int(cursor['suggested_person_id'])
            if suggested in seen:
                raise ApiProblem(
                    409,'人物候选关系会形成循环','relationship_conflict',
                    operation_id=operation_id,
                )
            seen.add(suggested)
            cursor=c.execute('SELECT * FROM people WHERE id=?',(suggested,)).fetchone()
            if not cursor:
                raise ApiProblem(
                    409,'人物候选关系指向不存在对象','relationship_conflict',
                    operation_id=operation_id,
                )
        faces=[dict(row) for row in c.execute(
            'SELECT id,person_id,reviewed,ignored FROM faces WHERE person_id=? ORDER BY id',(pid,)
        )]
        conflicting=[dict(row) for row in c.execute(
            '''SELECT s.id source_face_id,t.id target_face_id,s.asset_id
               FROM faces s JOIN faces t ON t.asset_id=s.asset_id
               WHERE s.person_id=? AND t.person_id=?
               ORDER BY s.asset_id,s.id,t.id''',(pid,body.target_id)
        )]
        token_payload={
            'source_id':pid,'target_id':body.target_id,
            'pairs':conflicting,
            'source_faces':[row['id'] for row in faces],
        }
        confirmation_token=canonical_request_hash(token_payload)[0]
        if conflicting and body.confirmation_token!=confirmation_token:
            raise ApiProblem(
                409,'同一张照片里两个人物都有脸，需确认后再合并',
                'same_photo_confirmation_required',
                operation_id=operation_id,
                confirmation_token=confirmation_token,
                conflicts=conflicting,
            )
        old_count=len(faces)
        target_ignored=int(target['ignored'] or 0) if 'ignored' in target.keys() else 0
        source_ignored=int(source['ignored'] or 0) if 'ignored' in source.keys() else 0
        if target_ignored and not source_ignored:
            raise ApiProblem(
                409,'请先把路人组恢复为可识别，再合并到日常人物',
                'relationship_conflict',operation_id=operation_id,
            )
        reference_rows=[
            {'id':int(row['id']),'suggested_person_id':row['suggested_person_id']}
            for row in c.execute(
                'SELECT id,suggested_person_id FROM people WHERE suggested_person_id=? ORDER BY id',
                (pid,),
            )
        ]
        target_cover_before=target['cover_face_id']
        c.execute('UPDATE faces SET person_id=?,reviewed=?,ignored=? WHERE person_id=?',(body.target_id,target['confirmed'],target_ignored,pid))
        if target['cover_face_id'] is None and source['cover_face_id'] is not None:
            c.execute('UPDATE people SET cover_face_id=? WHERE id=?',(source['cover_face_id'],body.target_id))
        c.execute('UPDATE people SET suggested_person_id=? WHERE suggested_person_id=?',(body.target_id,pid))
        c.execute('DELETE FROM people WHERE id=?',(pid,))
        c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(now(),f'person:{pid}',json.dumps({'person':dict(source),'face_count':old_count},ensure_ascii=False),json.dumps({'merged_into':body.target_id,'moved_faces':old_count})))
        target_after=c.execute(
            'SELECT * FROM people WHERE id=?',(body.target_id,)
        ).fetchone()
        undo={
            'source':dict(source),'target_id':body.target_id,
            'target_cover_before':target_cover_before,
            'target_cover_after':target_after['cover_face_id'],
            'face_target_state':{
                'reviewed':int(target['confirmed'] or 0),
                'ignored':target_ignored,
            },
            'faces':faces,'references':reference_rows,
        }
        receipt=finish_operation(
            c,operation_id,{'ok':True,'moved_faces':old_count},
            status='committed',state_committed=True,undo=undo,
        )
    invalidate_face_index()
    return receipt


class UndoRequest(BaseModel):
    dry_run:bool=True


@app.post('/api/operations/{operation_id}/undo')
def undo_operation(operation_id:str,body:UndoRequest):
    if not operation_id or len(operation_id)>128:
        raise ApiProblem(400,'操作编号无效','invalid_request')
    with operation_lock(operation_id):
        return undo_operation_locked(operation_id,body)

def undo_operation_locked(operation_id,body):
    with db() as c:
        row=c.execute(
            'SELECT * FROM operations WHERE operation_id=?',(operation_id,)
        ).fetchone()
        if not row:raise ApiProblem(404,'操作不存在','not_found')
        if row['kind']!='person_merge' or not row['state_committed'] or not row['undo_json']:
            raise ApiProblem(409,'这个操作不能撤销','state_conflict',operation_id=operation_id)
        if row['status']=='undone':
            return operation_receipt(row)
        undo=json.loads(row['undo_json'])
        source=undo['source']
        source_id=int(source['id'])
        target_id=int(undo['target_id'])
        conflicts=[]
        if c.execute('SELECT 1 FROM people WHERE id=?',(source_id,)).fetchone():
            conflicts.append('源人物编号已被重新使用')
        target=c.execute('SELECT * FROM people WHERE id=?',(target_id,)).fetchone()
        if not target:
            conflicts.append('目标人物已不存在')
        elif target['cover_face_id']!=undo['target_cover_after']:
            conflicts.append('目标人物封面已在合并后改变')
        for face in undo['faces']:
            current=c.execute(
                'SELECT person_id,reviewed,ignored FROM faces WHERE id=?',(face['id'],)
            ).fetchone()
            expected=undo.get('face_target_state')
            if (
                not current
                or int(current['person_id'])!=target_id
                or (
                    expected is not None
                    and (
                        int(current['reviewed'] or 0)!=int(expected['reviewed'])
                        or int(current['ignored'] or 0)!=int(expected['ignored'])
                    )
                )
            ):
                conflicts.append(f"人脸 {face['id']} 已在合并后改变")
        for ref in undo['references']:
            current=c.execute(
                'SELECT suggested_person_id FROM people WHERE id=?',(ref['id'],)
            ).fetchone()
            if not current or current['suggested_person_id']!=target_id:
                conflicts.append(f"候选引用 {ref['id']} 已在合并后改变")
        preview={
            'operation_id':operation_id,'dry_run':bool(body.dry_run),
            'can_undo':not conflicts,'conflicts':conflicts,
            'faces':len(undo['faces']),'source_id':source_id,'target_id':target_id,
        }
        if body.dry_run:return preview
        if conflicts:
            raise ApiProblem(
                409,'合并后的对象已变化，未执行撤销','undo_conflict',
                operation_id=operation_id,conflicts=conflicts,
            )
        columns=[
            name for name in (
                'id','name','confirmed','suggested_person_id','alias','ignored',
                'cover_face_id'
            ) if name in source
        ]
        c.execute(
            f"INSERT INTO people({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [source[name] for name in columns],
        )
        for face in undo['faces']:
            c.execute(
                '''UPDATE faces SET person_id=?,reviewed=?,ignored=? WHERE id=?''',
                (
                    source_id,face['reviewed'],face.get('ignored',0),face['id'],
                ),
            )
        c.execute(
            'UPDATE people SET cover_face_id=? WHERE id=?',
            (undo['target_cover_before'],target_id),
        )
        for ref in undo['references']:
            c.execute(
                'UPDATE people SET suggested_person_id=? WHERE id=?',
                (ref['suggested_person_id'],ref['id']),
            )
        result=json.loads(row['result_json'] or '{}')
        result.update({'undone':True,'undo_faces':len(undo['faces'])})
        receipt=finish_operation(
            c,operation_id,result,status='undone',state_committed=True,undo=undo,
        )
    invalidate_face_index()
    return receipt

@app.post('/api/faces/{fid}/split')
def split_face(fid:int,request:Request):
    if fid<=0:raise ApiProblem(422,'人脸编号必须为正整数','invalid_request')
    operation_id=operation_id_from(None,request)
    with operation_lock(operation_id):
        return split_face_locked(fid,operation_id)

def split_face_locked(fid,operation_id):
    with db() as c:
        operation_id,existing=prepare_operation(
            c,'face_split',operation_id,{'face_id':fid}
        )
        if existing:return existing
        row=c.execute('SELECT person_id,ignored,reviewed FROM faces WHERE id=?',(fid,)).fetchone()
        if not row: raise ApiProblem(404,'人脸不存在','not_found',operation_id=operation_id)
        pid=c.execute('INSERT INTO people DEFAULT VALUES').lastrowid
        c.execute('UPDATE faces SET person_id=?,reviewed=0,ignored=0 WHERE id=?',(pid,fid))
        c.execute('UPDATE people SET cover_face_id=NULL WHERE id=? AND cover_face_id=?',(row['person_id'],fid))
        c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(
            now(),f'face:{fid}',
            json.dumps({'person_id':row['person_id'],'ignored':row['ignored'],'reviewed':row['reviewed']}),
            json.dumps({'person_id':pid,'ignored':0,'reviewed':0}),
        ))
        receipt=finish_operation(
            c,operation_id,{'person_id':pid},status='committed',
            state_committed=True,
        )
    invalidate_face_index()
    return receipt

class IgnoreRequest(BaseModel):
    ignored:bool=True

class PhotoPassersbyRequest(BaseModel):
    ignored:bool=True
    person_ids:list[PositiveId]=Field(default_factory=list,max_length=250)

@app.post('/api/photos/{aid}/passersby')
def mark_photo_passersby(aid:int,body:PhotoPassersbyRequest):
    """Mark only this photo's still-unnamed people as passersby, with a bounded undo."""
    if aid<=0:raise ApiProblem(422,'照片编号必须为正整数','invalid_request')
    with db() as c:
        asset=c.execute('SELECT 1 FROM assets a WHERE a.id=? AND '+ACTIVE_ASSET,(aid,)).fetchone()
        if not asset: raise HTTPException(404,'照片不存在或已不在资料库中')
        if body.ignored:
            rows=c.execute(
                '''SELECT DISTINCT p.id
                   FROM faces f JOIN people p ON p.id=f.person_id
                   WHERE f.asset_id=? AND coalesce(p.ignored,0)=0
                     AND coalesce(trim(p.alias),'')=''
                     AND (coalesce(trim(p.name),'')='' OR trim(p.name) IN ('待核对','命名'))''',
                (aid,),
            ).fetchall()
            person_ids=[int(row['id']) for row in rows]
        else:
            requested=[]
            seen=set()
            for raw in body.person_ids[:250]:
                pid=int(raw)
                if pid>0 and pid not in seen:
                    seen.add(pid);requested.append(pid)
            if requested:
                placeholders=','.join('?' for _ in requested)
                rows=c.execute(
                    f'''SELECT DISTINCT p.id
                        FROM faces f JOIN people p ON p.id=f.person_id
                        WHERE f.asset_id=? AND coalesce(p.ignored,0)=1
                          AND p.id IN ({placeholders})''',
                    (aid,*requested),
                ).fetchall()
                person_ids=[int(row['id']) for row in rows]
            else:
                person_ids=[]
        for pid in person_ids:
            before=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
            if not before: continue
            if body.ignored:
                c.execute('UPDATE people SET ignored=1 WHERE id=?',(pid,))
                c.execute('UPDATE faces SET ignored=1 WHERE person_id=?',(pid,))
            else:
                synthetic=not int(before['confirmed'] or 0) and normalize_person_text(before['name'])=='路人'
                if synthetic:
                    c.execute("UPDATE people SET ignored=0,name=NULL,alias='' WHERE id=?",(pid,))
                    c.execute('UPDATE faces SET ignored=0,reviewed=0 WHERE person_id=?',(pid,))
                else:
                    c.execute('UPDATE people SET ignored=0 WHERE id=?',(pid,))
                    c.execute('UPDATE faces SET ignored=0 WHERE person_id=?',(pid,))
            after=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
            c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(
                now(),f'person:{pid}:photo-passersby:{aid}',
                json.dumps(dict(before),ensure_ascii=False),json.dumps(dict(after),ensure_ascii=False),
            ))
    if person_ids: invalidate_face_index()
    return {'ok':True,'ignored':body.ignored,'count':len(person_ids),'person_ids':person_ids}

@app.post('/api/people/{pid}/ignore')
def ignore_person(pid:int,body:IgnoreRequest):
    if pid<=0:raise ApiProblem(422,'人物编号必须为正整数','invalid_request')
    with db() as c:
        row=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not row: raise HTTPException(404,'人物不存在')
        synthetic=not body.ignored and not int(row['confirmed'] or 0) and normalize_person_text(row['name'])=='路人'
        if synthetic:
            c.execute("UPDATE people SET ignored=0,name=NULL,alias='' WHERE id=?",(pid,))
            c.execute('UPDATE faces SET ignored=0,reviewed=0 WHERE person_id=?',(pid,))
        else:
            c.execute('UPDATE people SET ignored=? WHERE id=?',(int(body.ignored),pid))
            c.execute('UPDATE faces SET ignored=? WHERE person_id=?',(int(body.ignored),pid))
        after=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(
            now(),f'person:{pid}',json.dumps(dict(row),ensure_ascii=False),json.dumps(dict(after),ensure_ascii=False),
        ))
    invalidate_face_index()
    return {'ok':True,'ignored':body.ignored}

@app.post('/api/faces/{fid}/ignore')
def ignore_face(fid:int,body:IgnoreRequest,request:Request):
    if fid<=0:raise ApiProblem(422,'人脸编号必须为正整数','invalid_request')
    operation_id=operation_id_from(None,request)
    with operation_lock(operation_id):
        return ignore_face_locked(fid,body,operation_id)

def ignore_face_locked(fid,body,operation_id):
    with db() as c:
        operation_id,existing=prepare_operation(
            c,'face_ignore',operation_id,
            {'face_id':fid,'ignored':bool(body.ignored)},
        )
        if existing:return existing
        row=c.execute('SELECT * FROM faces WHERE id=?',(fid,)).fetchone()
        if not row: raise ApiProblem(404,'人脸不存在','not_found',operation_id=operation_id)
        old_person=row['person_id']
        if body.ignored:
            pid=c.execute('INSERT INTO people(name,ignored) VALUES (?,1)',('路人',)).lastrowid
            c.execute('UPDATE faces SET person_id=?,ignored=1,reviewed=1 WHERE id=?',(pid,fid))
            c.execute('UPDATE people SET cover_face_id=NULL WHERE id=? AND cover_face_id=?',(old_person,fid))
            leftover=c.execute('SELECT count(*) FROM faces WHERE person_id=?',(old_person,)).fetchone()[0]
            if leftover==0:
                c.execute('UPDATE people SET suggested_person_id=NULL WHERE suggested_person_id=?',(old_person,))
                c.execute('DELETE FROM people WHERE id=?',(old_person,))
            after={'person_id':pid,'ignored':1}
        else:
            pid=c.execute('INSERT INTO people DEFAULT VALUES').lastrowid
            c.execute('UPDATE faces SET person_id=?,ignored=0,reviewed=0 WHERE id=?',(pid,fid))
            c.execute('UPDATE people SET cover_face_id=NULL WHERE id=? AND cover_face_id=?',(old_person,fid))
            leftover=c.execute('SELECT count(*) FROM faces WHERE person_id=?',(old_person,)).fetchone()[0]
            if leftover==0:
                c.execute('UPDATE people SET suggested_person_id=NULL WHERE suggested_person_id=?',(old_person,))
                c.execute('DELETE FROM people WHERE id=?',(old_person,))
            after={'person_id':pid,'ignored':0}
        c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(now(),f'face:{fid}',json.dumps({'person_id':old_person}),json.dumps(after)))
        receipt=finish_operation(
            c,operation_id,after,status='committed',state_committed=True,
        )
    invalidate_face_index()
    return receipt

@app.get('/api/thumb/{aid}')
def thumbnail(aid:int):
    with db() as c:
        row=c.execute('SELECT sha256,error FROM assets WHERE id=?',(aid,)).fetchone()
    if not row: raise HTTPException(404,'照片不存在')
    path=thumbnail_path(row[0])
    with asset_lock(row[0]):
        with db() as c:
            active=c.execute('SELECT 1 FROM assets a WHERE id=? AND '+ACTIVE_ASSET,(aid,)).fetchone()
        if active and not path.exists() and not row['error']:
            try:
                make_thumbnail(original_path(aid),row[0])
            except (OSError,HTTPException):
                pass
        if not active:
            return Response('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="100%" height="100%" fill="#e9e5dc"/><text x="50%" y="50%" text-anchor="middle" fill="#78776c" font-size="24">已排除 · 缓存已清理</text></svg>',media_type='image/svg+xml')
    if not path.exists():
        return Response('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="100%" height="100%" fill="#e9e5dc"/><text x="50%" y="50%" text-anchor="middle" fill="#78776c" font-size="24">预览不可用</text></svg>',media_type='image/svg+xml')
    return FileResponse(path,media_type='image/jpeg')

@app.get('/api/face/{fid}')
def face_crop(fid:int):
    path=DATA/'faces'/f'{fid}.jpg'
    if not path.exists(): raise HTTPException(404,'人脸缩略图不存在')
    return FileResponse(path,media_type='image/jpeg')

@app.get('/api/face-label-bg/{filename}')
def face_label_background(filename:str):
    if filename not in FACE_LABEL_FILES:
        raise HTTPException(404,'标签底图不存在')
    path=FACE_LABEL_DIR/filename
    if not path.is_file():
        raise HTTPException(404,'标签底图不存在')
    return FileResponse(path,media_type='image/png',headers={'Cache-Control':'public, max-age=86400'})

def original_path(aid):
    with db() as c: rows=c.execute('SELECT path FROM files WHERE asset_id=? ORDER BY excluded,exists_now DESC,id',(aid,)).fetchall()
    for row in rows:
        if Path(row[0]).is_file(): return Path(row[0])
    raise HTTPException(404,'原文件已移动或磁盘未连接，请重新扫描')

@app.get('/api/preview/{aid}')
def large_preview(aid:int):
    with db() as c:
        active=c.execute('SELECT 1 FROM assets a WHERE id=? AND '+ACTIVE_ASSET,(aid,)).fetchone()
    if not active:
        return thumbnail(aid)
    # Browser cache only: larger browsing previews never accumulate on disk.
    with PREVIEW_LOCK:
        try:
            with Image.open(original_path(aid)) as source:
                source.draft('RGB',(2560,2560))
                pic=ImageOps.exif_transpose(source).convert('RGB')
                pic.thumbnail((2560,2560))
                output=io.BytesIO();pic.save(output,format='JPEG',quality=90)
        except OSError:
            raise HTTPException(422,'这张图片暂时无法生成大图预览，可尝试打开原图')
    return Response(output.getvalue(),media_type='image/jpeg',headers={'Cache-Control':'private, max-age=3600'})

@app.get('/api/original/{aid}')
def original(aid:int):
    return FileResponse(original_path(aid))

@app.post('/api/reveal/{aid}')
def reveal(aid:int):
    path=original_path(aid)
    if os.name!='nt': raise HTTPException(400,'在文件夹中定位仅支持 Windows')
    import subprocess
    subprocess.Popen(['explorer.exe','/select,',str(path)])
    return {'ok':True}

def collect_export_snapshot(conn, after_table=None):
    conn.execute('BEGIN')
    result={'exported_at':now(),'format_version':2}
    queries=(
        ('assets','SELECT * FROM assets'),
        ('files','SELECT * FROM files'),
        ('people','SELECT * FROM people'),
        ('faces','SELECT id,asset_id,person_id,bbox,score,reviewed FROM faces'),
        ('edits','SELECT * FROM edits'),
        ('excluded_roots','SELECT * FROM excluded_roots'),
    )
    for name,query in queries:
        result[name]=[dict(row) for row in conn.execute(query)]
        if after_table:after_table(name)
    return result

@app.get('/api/export')
def export():
    with db() as c:
        result=collect_export_snapshot(c)
    return Response(json.dumps(result,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="photo-library.json"'})

@app.post('/api/backup')
def backup():
    folder=DATA/'backups';folder.mkdir(exist_ok=True)
    path=folder/f'library-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.sqlite3'
    source=sqlite3.connect(DB);target=sqlite3.connect(path)
    try: source.backup(target)
    finally: target.close();source.close()
    return {'path':str(path),'message':'数据库备份包含人物特征和全部标注；原照片仍在原位置，缩略图可重建'}

app.mount('/',StaticFiles(directory=Path(os.environ.get('PHOTO_WEB_ROOT',str(BASE/'web'))),html=True),name='web')

if __name__=='__main__':
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=APP_PORT)
    args=parser.parse_args()
    APP_PORT=args.port
    config=uvicorn.Config(app,host='127.0.0.1',port=args.port,access_log=False)
    UVICORN_SERVER=uvicorn.Server(config)
    UVICORN_SERVER.run()
    if not UVICORN_SERVER.started:
        raise SystemExit(1)
