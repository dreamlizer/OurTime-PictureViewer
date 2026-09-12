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
import math
import mimetypes
import sqlite3
import threading
import time
import uuid
import re
import zipfile
import subprocess
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, ExifTags, IptcImagePlugin
from pydantic import BaseModel, Field
from metadata_reader import read_metadata, close_readers
from geo_labels import PlaceIndex
from object_labels import classify_image, classify_images, model_ready, runtime_name as object_runtime, BATCH_SIZE
from library_db import (
    ACTIVE_ASSET, ASSET_LIST_COLUMNS, init_schema, load_place_rules,
    migrate_place_overrides, place_rules_version, register_collations, upsert_place_rule,
)
from browse_queries import directory_predicate as directory_clause, fetch_people, fetch_photos

mimetypes.add_type('font/ttf', '.ttf')

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('PHOTO_LIBRARY_DATA', str(BASE / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / 'thumbs').mkdir(exist_ok=True)
(DATA / 'faces').mkdir(exist_ok=True)
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
FACE_GROUP_THRESHOLD = 0.50
FACE_AUTO_MATCH_THRESHOLD = 0.68
FACE_AUTO_MATCH_MARGIN = 0.08
OBJECT_JOB={'status':'idle','processed':0,'limit':0,'tagged':0,'errors':0,'message':'','started_at':None,'finished_at':None}
OBJECT_JOB_LOCK=threading.Lock()
GEO = None
EXIFTOOL = next((BASE/'tools'/'exiftool').glob('*/exiftool.exe'),None)
ASSET_LOCKS=[threading.RLock() for _ in range(128)]
GEO_LOCK=threading.Lock()
RULE_LOCK=threading.RLock()
RULES=[]
PROGRESS_LOCK=threading.Lock()
PROGRESS={'job_id':None,'samples':deque(maxlen=2000),'metadata_reads':0}
STATUS_CACHE={'at':0,'payload':None}
STATUS_CACHE_LOCK=threading.Lock()
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

init_db()
attach_place_rules()
app = FastAPI(title='拾光 · 本地照片资料库', docs_url=None, redoc_url=None)

@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    if host not in {'127.0.0.1', 'localhost', 'testserver'}:
        return JSONResponse({'detail':'仅允许本机访问'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin not in {f'http://{request.headers.get("host")}', f'https://{request.headers.get("host")}'}:
        return JSONResponse({'detail':'不允许跨站请求'}, status_code=403)
    return await call_next(request)

def now():
    return datetime.now().isoformat(timespec='seconds')

def asset_lock(digest):
    return ASSET_LOCKS[int(digest[:8],16)%len(ASSET_LOCKS)]

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

refresh_rules()

def thumbnail_path(digest):
    return DATA/'thumbs'/f'{digest}.jpg'

def make_thumbnail(path,digest):
    with Image.open(path) as original:
        thumbnail=ImageOps.exif_transpose(original).convert('RGB')
        thumbnail.thumbnail((640,640))
        thumbnail.save(thumbnail_path(digest),quality=84)

def remove_generated(path,folder):
    # Only application-generated cache files in the exact managed folder.
    if path.is_symlink() or path.resolve().parent!=(DATA/folder).resolve():
        raise RuntimeError('缓存路径不在本项目指定目录内')
    try:
        size=path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0

def cleanup_asset_cache(aid):
    with db() as c:
        row=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
    if not row:
        return 0
    with asset_lock(row['sha256']):
        with db() as c:
            active=c.execute('SELECT 1 FROM assets a WHERE a.id=? AND '+ACTIVE_ASSET,(aid,)).fetchone()
            if active:
                return 0
            faces=c.execute('SELECT id FROM faces WHERE asset_id=?',(aid,)).fetchall()
        released=remove_generated(thumbnail_path(row['sha256']),'thumbs')
        for face in faces:
            released+=remove_generated(DATA/'faces'/f'{face[0]}.jpg','faces')
        with db() as c:
            c.execute('DELETE FROM faces WHERE asset_id=?',(aid,))
            c.execute('UPDATE assets SET face_state=0,face_error=NULL WHERE id=?',(aid,))
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

def build_face_index(known):
    import numpy as np
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
    if known:
        vectors[:count]=np.stack([np.frombuffer(r['embedding'],dtype=np.float32) for r in known])
        for face_index,row in enumerate(known):
            person_id=int(row['person_id'])
            slot=person_slot_by_id.get(person_id)
            if slot is None:
                slot=len(unique_person_ids)
                person_slot_by_id[person_id]=slot
                unique_person_ids.append(person_id)
                person_states.append('ignored' if row['ignored'] else 'confirmed' if row['confirmed'] else 'pending')
            person_slots[face_index]=slot
    return {
        'vectors':vectors,
        'size':count,
        'person_ids':[int(r['person_id']) for r in known],
        'confirmed':[bool(r['confirmed']) for r in known],
        'ignored':[bool(r['ignored']) for r in known],
        'person_slots':person_slots,
        'person_slot_by_id':person_slot_by_id,
        'unique_person_ids':unique_person_ids,
        'person_states':person_states,
    }

def load_face_index():
    global FACE_INDEX
    attempts=0
    while True:
        attempts+=1
        with FACE_INDEX_LOCK:
            if FACE_INDEX is not None:
                return FACE_INDEX
            generation=FACE_INDEX_GEN
        with db() as c:
            known=c.execute(
                '''SELECT f.embedding,f.person_id,p.confirmed,p.ignored
                   FROM faces f JOIN people p ON p.id=f.person_id
                   WHERE f.embedding IS NOT NULL
                     AND (coalesce(p.ignored,0)=1 OR coalesce(f.ignored,0)=0)
                   ORDER BY f.id'''
            ).fetchall()
        index=build_face_index(known)
        with FACE_INDEX_LOCK:
            if generation==FACE_INDEX_GEN:
                if FACE_INDEX is None:
                    FACE_INDEX=index
                return FACE_INDEX
            if FACE_INDEX is not None:
                return FACE_INDEX
        if attempts>=8:
            raise RuntimeError('人脸索引正在更新，请稍后重试')

def remember_face(person_id,embedding,confirmed=False,ignored=False):
    import numpy as np
    with FACE_INDEX_LOCK:
        if FACE_INDEX is None:
            return
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
        FACE_INDEX['confirmed'].append(bool(confirmed))
        FACE_INDEX['ignored'].append(bool(ignored))

def choose_face_person(index,embedding,used_people=None):
    """Choose a conservative person-level route for one normalized embedding."""
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
    if state in {'confirmed','ignored'} and best_score>=FACE_AUTO_MATCH_THRESHOLD and margin>=FACE_AUTO_MATCH_MARGIN:
        return {**base,'route':state,'person_id':best_person}
    if state=='confirmed' and best_score>=FACE_GROUP_THRESHOLD:
        return {**base,'route':'suggested','suggested_person_id':best_person}
    if state=='pending' and best_score>=FACE_GROUP_THRESHOLD:
        return {**base,'route':'pending','person_id':best_person}
    return {**base,'route':'new'}

def process_faces(asset_id,path):
    import numpy as np
    with db() as c:
        row=c.execute('SELECT face_state FROM assets WHERE id=?',(asset_id,)).fetchone()
        if row and int(row['face_state'] or 0)==1:
            return
    engine=get_face_engine()
    with Image.open(path) as original:
        pic=ImageOps.exif_transpose(original).convert('RGB')
        pic.thumbnail((2400,2400))
        detections=engine.get(np.asarray(pic)[:,:,::-1].copy())
        index=load_face_index()
        used_people=set()
        staged=[]
        for face in detections:
            x1,y1,x2,y2=[int(x) for x in face.bbox]
            if x2-x1<28 or y2-y1<28 or float(face.det_score)<0.65:
                continue
            emb=np.asarray(face.normed_embedding,dtype=np.float32)
            decision=choose_face_person(index,emb,used_people)
            person_id=decision['person_id']
            suggestion=decision['suggested_person_id']
            pad=int(max(x2-x1,y2-y1)*.25)
            crop=pic.crop((max(0,x1-pad),max(0,y1-pad),min(pic.width,x2+pad),min(pic.height,y2+pad)))
            crop.thumbnail((200,200))
            staged.append({'emb':emb,'person_id':person_id,'suggestion':suggestion,'route':decision['route'],'bbox':[x1,y1,x2,y2,pic.width,pic.height],'score':float(face.det_score),'crop':crop})
            if person_id:
                used_people.add(person_id)
        created_people=[]
        created_crops=[]
        try:
            with db() as c:
                for item in staged:
                    person_id=item['person_id']
                    if not person_id:
                        person_id=c.execute('INSERT INTO people(suggested_person_id) VALUES (?)',(item['suggestion'],)).lastrowid
                        created_people.append(person_id)
                        item['person_id']=person_id
                    reviewed=int(item['route'] in {'confirmed','ignored'})
                    ignored=int(item['route']=='ignored')
                    fid=c.execute('INSERT INTO faces(asset_id,person_id,bbox,embedding,score,reviewed,ignored) VALUES (?,?,?,?,?,?,?)',
                        (asset_id,person_id,json.dumps(item['bbox']),item['emb'].tobytes(),item['score'],reviewed,ignored)).lastrowid
                    item['fid']=fid
                    crop_path=DATA/'faces'/f'{fid}.jpg'
                    created_crops.append(crop_path)
                    item['crop'].save(crop_path,quality=88)
                    c.execute('UPDATE faces SET crop=? WHERE id=?',(f'{fid}.jpg',fid))
                c.execute('UPDATE assets SET face_state=1,face_error=NULL WHERE id=?',(asset_id,))
            for item in staged:
                remember_face(
                    item['person_id'],item['emb'],
                    confirmed=item['route']=='confirmed',
                    ignored=item['route']=='ignored',
                )
        except Exception:
            invalidate_face_index()
            for crop_path in created_crops:
                try:
                    if crop_path.exists():
                        crop_path.unlink()
                except OSError:
                    pass
            raise

def ingest(path,with_faces):
    if path_excluded(path):
        return 'excluded',None,False
    stat=path.stat()
    if not path.is_file():
        return 'skipped',None,False
    with db() as c:
        old=c.execute('SELECT f.*,a.sha256,a.face_state,a.error,a.excluded content_excluded FROM files f JOIN assets a ON a.id=f.asset_id WHERE path=?',(str(path),)).fetchone()
    unchanged=old and old['size']==stat.st_size and old['mtime_ns']==stat.st_mtime_ns
    if unchanged:
        digest=old['sha256']
    else:
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
            return 'excluded',None,False
        with db() as c:
            asset=c.execute('SELECT * FROM assets WHERE sha256=?',(digest,)).fetchone()
        metadata_read=False
        if asset and asset['excluded']:
            aid=asset['id'];state=asset['face_state'];error=None
        elif asset and not asset['error']:
            aid=asset['id']; state=asset['face_state']; error=None
            if not thumbnail_path(digest).exists():
                make_thumbnail(path,digest)
        else:
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
            return 'excluded',error,metadata_read
        status='skipped' if unchanged and not metadata_read else 'added'
        if with_faces and state!=1 and not error:
            try:
                process_faces(aid,path)
            except Exception as e:
                with db() as c:
                    c.execute('UPDATE assets SET face_state=-1,face_error=? WHERE id=?',(str(e),aid))
                error='人脸处理：'+str(e)
        return status,error,metadata_read

def job_error(jid,path,stage,message):
    with db() as c:
        c.execute('INSERT INTO job_errors(job_id,path,stage,message) VALUES (?,?,?,?)',(jid,str(path),stage,str(message)))
        c.execute('UPDATE jobs SET errors=errors+1 WHERE id=?',(jid,))

def run_scan(jid):
    try:
        with db() as c:
            job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        roots=json.loads(job['roots'])
        workers=1 if job['with_faces'] else job['workers']
        with PROGRESS_LOCK:
            PROGRESS.update(job_id=jid,samples=deque([(time.monotonic(),0)],maxlen=2000),metadata_reads=0)
        visited=set()
        progress={'discovered':0,'processed':0,'added':0,'skipped':0,'excluded':0,'auxiliary':0,'metadata_reads':0,'current_path':'','dirty':0,'last_flush':0.0}
        progress_lock=threading.Lock()
        def flush_job_progress(force=False):
            with progress_lock:
                if not force and progress['dirty']<25 and time.monotonic()-progress['last_flush']<1:
                    return
                payload=dict(progress)
                progress['dirty']=0
                progress['last_flush']=time.monotonic()
            with db() as c:
                c.execute('UPDATE jobs SET discovered=?,processed=?,added=?,skipped=?,excluded=?,auxiliary=?,metadata_reads=?,current_path=? WHERE id=?',
                          (payload['discovered'],payload['processed'],payload['added'],payload['skipped'],payload['excluded'],payload['auxiliary'],payload['metadata_reads'],payload['current_path'],jid))
        def process_one(path):
            metadata_read=False
            try:
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
                    status,error,metadata_read=ingest(path,bool(job['with_faces']))
                if error:
                    job_error(jid,path,'图片读取 / 人脸',error)
                with progress_lock:
                    progress['processed']+=1
                    if status in progress:
                        progress[status]+=1
                    progress['metadata_reads']+=int(metadata_read)
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

        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='photo-scan') as pool:
          for root in roots:
            if STOP.is_set(): break
            root_path=Path(root)
            pending=set()
            def walk_error(err):
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
                        job_error(jid,child,'目录读取',e)
                dirs[:]=retained
                for name in files:
                    if STOP.is_set(): break
                    path=current/name
                    if path.suffix.lower() not in EXTENSIONS or path.is_symlink(): continue
                    key=os.path.normcase(str(path))
                    if key in visited: continue
                    visited.add(key)
                    with progress_lock:
                        progress['discovered']+=1
                        progress['current_path']=str(path)
                        progress['dirty']+=1
                    flush_job_progress()
                    pending.add(pool.submit(process_one,path))
                    if len(pending)>=workers*2:
                        finished,pending=wait(pending,return_when=FIRST_COMPLETED)
                        for future in finished: future.result()
            if pending:
                for future in pending: future.result()
            flush_job_progress(force=True)
            if not STOP.is_set():
                with db() as c:
                    rows=c.execute('SELECT id,path FROM files').fetchall()
                    for row in rows:
                        try:
                            Path(row['path']).relative_to(root_path)
                        except ValueError:
                            continue
                        # Only a confirmed absent path is marked missing; access errors stay in the error list.
                        try:
                            Path(row['path']).stat()
                        except FileNotFoundError:
                            c.execute('UPDATE files SET exists_now=0 WHERE id=?',(row['id'],))
                        except OSError:
                            pass
        close_readers()
        with db() as c:
            count=c.execute('SELECT errors FROM jobs WHERE id=?',(jid,)).fetchone()[0]
            status='paused' if STOP.is_set() else ('completed_with_errors' if count else 'completed')
            c.execute('UPDATE jobs SET status=?,finished_at=?,current_path=?,message=? WHERE id=?',
                      (status,now(),'','扫描已暂停，继续时会跳过已完成文件' if STOP.is_set() else '扫描结束；请查看读取问题' if count else '扫描完成',jid))
    except Exception as e:
        job_error(jid,'','扫描任务',e)
        with db() as c:
            c.execute("UPDATE jobs SET status='failed',message=?,finished_at=? WHERE id=?",(str(e),now(),jid))
    finally:
        close_readers()
        SCAN_LOCK.release()

class ScanRequest(BaseModel):
    roots: list[str] = Field(min_length=1,max_length=30)
    with_faces: bool = False
    include_system: bool = False
    workers:int=Field(default=2,ge=1,le=4)

def begin_scan(body):
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
    if not SCAN_LOCK.acquire(blocking=False): raise HTTPException(409,'已有扫描正在运行，请先暂停或等它完成')
    STOP.clear(); jid=uuid.uuid4().hex
    with STATUS_CACHE_LOCK:
        STATUS_CACHE['payload']=None; STATUS_CACHE['at']=0
    try:
        with db() as c:
            c.execute('INSERT INTO jobs(id,roots,with_faces,include_system,status,started_at,workers) VALUES (?,?,?,?,?,?,?)',
                      (jid,json.dumps(roots,ensure_ascii=False),body.with_faces,body.include_system,'running',now(),1 if body.with_faces else body.workers))
        threading.Thread(target=run_scan,args=(jid,),daemon=True).start()
    except Exception:
        SCAN_LOCK.release(); raise
    return {'id':jid}

@app.post('/api/scan')
def scan(body:ScanRequest): return begin_scan(body)

@app.post('/api/scan/pause')
def pause():
    STOP.set()
    with db() as c: c.execute("UPDATE jobs SET status='pausing',message='当前文件处理完后暂停' WHERE status='running'")
    return {'ok':True}

@app.post('/api/scan/{jid}/resume')
def resume(jid:str):
    with db() as c: row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not row: raise HTTPException(404,'任务不存在')
    return begin_scan(ScanRequest(roots=json.loads(row['roots']),with_faces=bool(row['with_faces']),include_system=bool(row['include_system']),workers=row['workers']))

@app.get('/api/health')
def health():
    return {'ok': True, 'data_dir': str(DATA)}

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
          sum(CASE WHEN face_state=0 THEN 1 ELSE 0 END) faces_pending FROM assets a
          WHERE '''+ACTIVE_ASSET).fetchone())
        stats['files']=c.execute('SELECT count(*) FROM files').fetchone()[0]
        stats['active_files']=c.execute('SELECT count(*) FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.excluded=0 AND a.excluded=0').fetchone()[0]
        stats['excluded_assets']=c.execute('SELECT count(*) FROM assets a WHERE NOT ('+ACTIVE_ASSET+') AND EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id)').fetchone()[0]
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
    speed=0
    with PROGRESS_LOCK:
        if job and job['id']==PROGRESS['job_id'] and job['status'] in {'running','pausing'}:
            current=time.monotonic()
            samples=[s for s in PROGRESS['samples'] if current-s[0]<=60]
            if len(samples)>1 and current-samples[0][0]>=2:
                speed=(samples[-1][1]-samples[0][1])/(current-samples[0][0])
    inventory=None
    inventory_path=DATA/'inventory-estimate.json'
    if inventory_path.exists():
        try:
            stored=json.loads(inventory_path.read_text(encoding='utf-8-sig'))
            inventory={'candidate_files':stored['total'],'status':stored['status'],'as_of':stored.get('updated_at'),'note':'原始候选文件计数，包含副本和素材，不等于个人照片数量'}
        except (OSError,ValueError,KeyError):
            pass
    payload={'stats':{k:v or 0 for k,v in stats.items()},'job':job,'errors':errors,'metadata_per_second':round(speed,2),'inventory':inventory,
            'capabilities':{'heif':HEIF,'exiftool':bool(EXIFTOOL),'exiftool_mode':'常驻进程','face_model':(MODEL_ROOT/'models/buffalo_l/w600k_r50.onnx').exists(),'geo':(GEO_ROOT/'geonames/cities500.zip').exists(),'data_dir':str(DATA),'face_runtime':current_face_runtime(),'object_model':model_ready(),'object_runtime':object_runtime() if model_ready() else '未找到','version':'0.3','pid':os.getpid()}}
    with STATUS_CACHE_LOCK:
        STATUS_CACHE['at']=time.monotonic()
        STATUS_CACHE['payload']=payload
    return payload

class ExclusionRequest(BaseModel):
    ids:list[int]=Field(min_length=1,max_length=1000)
    excluded:bool=True
    reason:str=Field(default='手工排除',max_length=200)

@app.post('/api/exclusions/assets')
def exclude_assets(body:ExclusionRequest):
    released=0
    for aid in dict.fromkeys(body.ids):
        with db() as c:
            row=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
        if not row:
            raise HTTPException(404,f'照片 {aid} 不存在')
        with asset_lock(row['sha256']):
            with db() as c:
                old=c.execute('SELECT excluded,exclude_reason FROM assets WHERE id=?',(aid,)).fetchone()
                c.execute('UPDATE assets SET excluded=?,exclude_reason=? WHERE id=?',(int(body.excluded),body.reason if body.excluded else '',aid))
                c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',
                          (now(),f'asset:{aid}',json.dumps(dict(old),ensure_ascii=False),json.dumps({'excluded':body.excluded,'reason':body.reason},ensure_ascii=False)))
            if body.excluded:
                released+=cleanup_asset_cache(aid)
    return {'updated':len(set(body.ids)),'released_bytes':released,'message':'排除已记住，原文件保留' if body.excluded else '已撤销单张排除；若仍受目录规则影响，需要同时恢复目录。缩略图可按需重建，人脸需重扫。'}

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
    d['effective_date']=d['manual_date'] or d['captured_at']
    d['effective_place']=(d['manual_place'] or d['place'] or '').replace('附近','') or None
    d['effective_precision']=d['manual_precision'] if d['manual_date'] else d['date_precision']
    d['effective_source']='人工确认' if d['manual_date'] else d['date_source']
    d.pop('metadata',None)
    return d

@app.get('/api/photos')
def photos(q:str='',filter:str='all',person:str='',offset:int=0,limit:int=60,directory:str='',sort:str='date_desc',sequence:bool=False,max_id:int=0,around:int=0,tail:bool=False,date_from:str='',date_to:str='',place:str=''):
    try:
        with db() as c:
            result=fetch_photos(
                c, q=q, filter=filter, person=person, offset=offset, limit=limit,
                directory=directory, sort=sort, sequence=sequence, max_id=max_id, around=around, tail=tail,
                date_from=date_from, date_to=date_to, place=place,
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
def places(q:str='', offset:int=0, limit:int=80, west:float|None=None, south:float|None=None, east:float|None=None, north:float|None=None, zoom:int=11):
    values=[]
    having="HAVING place IS NOT NULL AND place!=''"
    needle=(q or '').strip()
    if needle:
        having += ' AND place LIKE ?'
        values.append('%'+needle+'%')
    grouped='SELECT coalesce(nullif(a.manual_place,\'\'), a.place) place, count(*) n, avg(a.latitude) latitude, avg(a.longitude) longitude FROM assets a WHERE '+ACTIVE_ASSET+' GROUP BY place '
    with db() as c:
        if None not in (west,south,east,north):
            cell=max(0.02, min(8.0, 360/(2**max(1,min(int(zoom),18)))))
            rows=c.execute('SELECT round(a.latitude/?,4)*? lat, round(a.longitude/?,4)*? lon, count(*) n, min(coalesce(nullif(a.manual_place,\'\'), a.place)) place FROM assets a WHERE '+ACTIVE_ASSET+' AND a.latitude BETWEEN ? AND ? AND a.longitude BETWEEN ? AND ? GROUP BY 1,2 ORDER BY n DESC LIMIT 400',(cell,cell,cell,cell,south,north,west,east)).fetchall()
            return {'mode':'map','zoom':zoom,'clusters':[{'latitude':r['lat'],'longitude':r['lon'],'count':r['n'],'place':r['place']} for r in rows]}
        total=c.execute('SELECT count(*) FROM ('+grouped+having+') t',values).fetchone()[0]
        rows=c.execute(grouped+having+' ORDER BY n DESC, place LIMIT ? OFFSET ?',values+[min(max(limit,1),200),max(offset,0)]).fetchall()
    items=[{'place':r['place'],'count':r['n'],'latitude':r['latitude'],'longitude':r['longitude']} for r in rows]
    return {'places':items,'total':total,'offset':max(offset,0),'limit':min(max(limit,1),200)}

class EditRequest(BaseModel):
    ids:list[int]=Field(min_length=1,max_length=1000)
    manual_date:str|None=None
    manual_precision:str|None=None
    manual_place:str|None=None
    notes:str|None=None

@app.patch('/api/photos')
def edit_photos(body:EditRequest):
    changes=body.model_dump(exclude_unset=True); ids=changes.pop('ids')
    if not changes: raise HTTPException(400,'没有修改内容')
    for key in ['manual_date','manual_place','manual_precision']:
        if key in changes: changes[key]=(changes[key] or '').strip() or None
    if 'notes' in changes: changes['notes']=changes['notes'] or ''
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
    alias:str=''

@app.patch('/api/people/{pid}')
def name_person(pid:int,body:PersonEdit):
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
    face_id:int

@app.put('/api/people/{pid}/cover')
def set_person_cover(pid:int,body:PersonCoverEdit):
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
    target_id:int

@app.post('/api/people/{pid}/merge')
def merge_person(pid:int,body:MergeRequest):
    if pid==body.target_id: raise HTTPException(400,'不能合并到自己')
    with db() as c:
        target=c.execute('SELECT * FROM people WHERE id=?',(body.target_id,)).fetchone()
        source=c.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not target or not source: raise HTTPException(404,'人物不存在')
        old_count=c.execute('SELECT count(*) FROM faces WHERE person_id=?',(pid,)).fetchone()[0]
        target_ignored=int(target['ignored'] or 0) if 'ignored' in target.keys() else 0
        source_ignored=int(source['ignored'] or 0) if 'ignored' in source.keys() else 0
        if target_ignored and not source_ignored:
            raise HTTPException(400,'请先把路人组恢复为可识别，再合并到日常人物')
        c.execute('UPDATE faces SET person_id=?,reviewed=?,ignored=? WHERE person_id=?',(body.target_id,target['confirmed'],target_ignored,pid))
        if target['cover_face_id'] is None and source['cover_face_id'] is not None:
            c.execute('UPDATE people SET cover_face_id=? WHERE id=?',(source['cover_face_id'],body.target_id))
        c.execute('UPDATE people SET suggested_person_id=? WHERE suggested_person_id=?',(body.target_id,pid))
        c.execute('DELETE FROM people WHERE id=?',(pid,))
        c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(now(),f'person:{pid}',json.dumps({'person':dict(source),'face_count':old_count},ensure_ascii=False),json.dumps({'merged_into':body.target_id,'moved_faces':old_count})))
    invalidate_face_index()
    return {'ok':True}

@app.post('/api/faces/{fid}/split')
def split_face(fid:int):
    with db() as c:
        row=c.execute('SELECT person_id,ignored,reviewed FROM faces WHERE id=?',(fid,)).fetchone()
        if not row: raise HTTPException(404,'人脸不存在')
        pid=c.execute('INSERT INTO people DEFAULT VALUES').lastrowid
        c.execute('UPDATE faces SET person_id=?,reviewed=0,ignored=0 WHERE id=?',(pid,fid))
        c.execute('UPDATE people SET cover_face_id=NULL WHERE id=? AND cover_face_id=?',(row['person_id'],fid))
        c.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)',(
            now(),f'face:{fid}',
            json.dumps({'person_id':row['person_id'],'ignored':row['ignored'],'reviewed':row['reviewed']}),
            json.dumps({'person_id':pid,'ignored':0,'reviewed':0}),
        ))
    invalidate_face_index()
    return {'person_id':pid}

class IgnoreRequest(BaseModel):
    ignored:bool=True

class PhotoPassersbyRequest(BaseModel):
    ignored:bool=True
    person_ids:list[int]=Field(default_factory=list)

@app.post('/api/photos/{aid}/passersby')
def mark_photo_passersby(aid:int,body:PhotoPassersbyRequest):
    """Mark only this photo's still-unnamed people as passersby, with a bounded undo."""
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
def ignore_face(fid:int,body:IgnoreRequest):
    with db() as c:
        row=c.execute('SELECT * FROM faces WHERE id=?',(fid,)).fetchone()
        if not row: raise HTTPException(404,'人脸不存在')
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
    invalidate_face_index()
    return after

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

@app.get('/api/export')
def export():
    with db() as c:
        result={'exported_at':now(),'format_version':2,
                'assets':[dict(r) for r in c.execute('SELECT * FROM assets')],
                'files':[dict(r) for r in c.execute('SELECT * FROM files')],
                'people':[dict(r) for r in c.execute('SELECT * FROM people')],
                'faces':[dict(r) for r in c.execute('SELECT id,asset_id,person_id,bbox,score,reviewed FROM faces')],
                'edits':[dict(r) for r in c.execute('SELECT * FROM edits')],
                'excluded_roots':[dict(r) for r in c.execute('SELECT * FROM excluded_roots')]}
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
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
