"""v0.2: real browser exclusions, cache lifecycle, concurrent scans and migration."""
import os,sys,json,time,sqlite3,subprocess,urllib.request,urllib.error,hashlib,shutil
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'validation/work'/('upgrade-'+time.strftime('%Y%m%d-%H%M%S'))
DATA=RUN/'data'; PHOTOS=RUN/'photos'; TARGET=PHOTOS/'素材_%测试'; OTHER=PHOTOS/'保留'
TARGET.mkdir(parents=True);OTHER.mkdir()
REPORT=ROOT/'validation/reports';REPORT.mkdir(exist_ok=True)
URL='http://127.0.0.1:8767'
checks=[]
def check(value,message):
    if not value: raise AssertionError(message)
    checks.append(message);print('PASS',message,flush=True)
def req(path,body=None,method=None):
    request=urllib.request.Request(URL+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)
def finish():
    for _ in range(300):
        job=req('/api/status')['job']
        if job['status'] not in ('running','pausing'):return job
        time.sleep(.15)
    raise AssertionError('Scan timeout')
def scan(roots,workers=4,faces=False):
    req('/api/scan',{'roots':[str(p) for p in roots],'workers':workers,'with_faces':faces})
    return finish()
def all_photos(filter='all'):
    return req('/api/photos?filter='+filter+'&limit=120')['items']
def start():
    env={**os.environ,'PHOTO_LIBRARY_DATA':str(DATA),'PHOTO_WEB_ROOT':os.environ.get('PHOTO_WEB_ROOT',str(ROOT/'validation/web-next')),'NO_ALBUMENTATIONS_UPDATE':'1'}
    log=open(RUN/'server.log','ab')
    process=subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--port','8767'],env=env,cwd=ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(100):
        try:req('/api/status');return process,log
        except Exception:time.sleep(.1)
    raise RuntimeError('Server did not start')

for i in range(16):
    exif=Image.Exif();exif[36867]=f'2018:05:{i+1:02} 12:34:56'
    Image.new('RGB',(960+i*5,640),(50+i*10,100+i*4,160-i*3)).save(TARGET/f'照片_{i}.jpg',exif=exif)
for i in range(16):shutil.copy2(TARGET/f'照片_{i}.jpg',OTHER/f'重复副本_{i}.jpg')
Image.new('RGB',(700,500),'#557788').save(OTHER/'只在保留目录.jpg')
(PHOTOS/'._系统辅助.jpg').write_bytes(b'\x00\x05\x16\x07'+b'\x00'*22)
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in PHOTOS.rglob('*') if p.is_file()}
process,log=start()
try:
    job=scan([PHOTOS])
    check(job['status']=='completed' and job['errors']==0,'4 路并发扫描完成，无数据库锁或重复写入错误')
    check(job['workers']==4 and job['metadata_reads']==17,'34 个候选文件只对 17 份独立图片读取元数据')
    check(job['auxiliary']==1 and req('/api/status')['stats']['files']==33,'AppleDouble 辅助文件单独略过，不伪报损坏照片')
    check(req('/api/status')['stats']['duplicates']==16,'并发遇到相同内容，正确归为 16 个额外副本')
    unique=next(a for a in all_photos() if a['copies']==1)
    duplicate=next(a for a in all_photos() if a['copies']==2)
    thumb=lambda a:DATA/'thumbs'/(a['sha256']+'.jpg')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1050})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(URL);page.wait_for_selector('.photo-card')
        page.click(f'[data-photo="{unique["id"]}"]');page.fill('#edit-notes','排除后仍应保留的人工备注');page.click('#detail-form button[type="submit"]')
        page.wait_for_function("document.querySelector('#toast').textContent.includes('已保存')")
        page.click('#exclude-photo');page.wait_for_selector('#exclude-dialog[open]')
        check('全部重复副本' in page.locator('#exclude-impact').inner_text(),'排除确认页明确作用于整份内容及其全部副本')
        page.click('#confirm-exclusion');page.wait_for_function("!document.querySelector('#exclude-dialog').open")
        check(not thumb(unique).exists(),'真实页面排除图片后删除其生成的缩略图')
        check(req('/api/photos/'+str(unique['id']))['notes']=='排除后仍应保留的人工备注','排除保留人工备注与档案')
        page.click('[data-view="excluded"]');page.wait_for_selector(f'[data-photo="{unique["id"]}"]')
        time.sleep(.2);check(not thumb(unique).exists(),'浏览已排除页面不会自动重建其缓存')
        page.click(f'[data-photo="{unique["id"]}"]');page.click('#restore-photo')
        page.wait_for_function("document.querySelector('#exclude-photo').hidden===false")
        page.wait_for_function("document.querySelector('#detail-img').complete && document.querySelector('#detail-img').naturalWidth>0")
        check(thumb(unique).exists(),'恢复后真实详情页按需重建缩略图')
        page.click('[data-close="detail-dialog"]')
        page.fill('#exclude-root',str(TARGET));page.click('#preview-root-exclusion');page.wait_for_selector('#exclude-dialog[open]')
        check('16 个文件' in page.locator('#exclude-impact').inner_text(),'含百分号与下划线的中文目录，影响预览准确')
        page.click('#confirm-exclusion');page.wait_for_function("!document.querySelector('#exclude-dialog').open")
        check(thumb(duplicate).exists(),'只排除一个副本目录，另一个目录仍保留时不删共用缓存')
        check(req('/api/status')['stats']['assets']==17,'目录排除不误排其他目录里的保留副本')
        page.screenshot(path=str(REPORT/'07-exclusion-rules.png'),full_page=True,animations='disabled')
        page.click('[data-view="all"]');page.fill('#directory-filter',str(OTHER));page.click('#apply-directory')
        page.wait_for_function("document.querySelector('#result-count').textContent==='17 张'")
        page.click('#select-mode');page.click(f'[data-photo="{duplicate["id"]}"]');page.click('#batch-exclude');page.click('#confirm-exclusion')
        page.wait_for_function("!document.querySelector('#exclude-dialog').open")
        check(not thumb(duplicate).exists(),'批量排除内容后，其所有副本共用缓存被清理')
        page.click('[data-view="excluded"]');page.wait_for_selector('.photo-card')
        page.set_viewport_size({'width':760,'height':900})
        check(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'新版排除页窄屏不横向溢出')
        check(not errors,'新版真实浏览器没有 JavaScript 运行错误')
        browser.close()
    recheck=scan([PHOTOS])
    check(recheck['metadata_reads']==0 and recheck['errors']==0,'重扫记住目录与内容排除，不重复读取已完成元数据')
    check(not thumb(duplicate).exists(),'重扫不会重新生成已排除图片的缓存')
    process.terminate();process.wait(timeout=10);log.close();process,log=start()
    check(len(req('/api/exclusions')['roots'])==1 and req('/api/photos/'+str(duplicate['id']))['excluded']==1,'排除规则和单张决定在后台重启后保留')
    # Overlapping directory rules: removing child does not override its parent.
    req('/api/exclusions/roots',{'path':str(PHOTOS)})
    child=next(r for r in req('/api/exclusions')['roots'] if r['path']==str(TARGET))
    req('/api/exclusions/roots/'+str(child['id']),method='DELETE')
    check(req('/api/status')['stats']['assets']==0,'恢复子目录不会绕过仍生效的父目录排除')
    parent=req('/api/exclusions')['roots'][0]
    req('/api/exclusions/roots/'+str(parent['id']),method='DELETE')
    check(req('/api/status')['stats']['assets']==16,'恢复父目录后保留单张图片的独立排除决定')
    # A new path with already excluded content stays excluded.
    moved=OTHER/'新名字的已排除副本.jpg';shutil.copy2(Path(duplicate['path']),moved)
    scan([OTHER])
    check(not thumb(duplicate).exists() and not req('/api/photos/'+str(duplicate['id']))['in_library'],'已排除内容换一个新路径，也不会重新进入照片库')
    # Exclude a directory while workers are actively decoding.
    concurrent=RUN/'边扫边排除';concurrent.mkdir()
    for i in range(70):Image.new('RGB',(2100,1500),(i,140,170)).save(concurrent/f'{i}.jpg')
    req('/api/scan',{'roots':[str(concurrent)],'workers':4})
    for _ in range(100):
        if req('/api/status')['job']['processed']>=4:break
        time.sleep(.03)
    req('/api/exclusions/roots',{'path':str(concurrent)})
    finish()
    with sqlite3.connect(DATA/'library.sqlite3') as c:
        affected=c.execute('SELECT DISTINCT a.sha256 FROM assets a JOIN files f ON f.asset_id=a.id WHERE f.path LIKE ?',(str(concurrent)+'%',)).fetchall()
        check(all(not (DATA/'thumbs'/(r[0]+'.jpg')).exists() for r in affected),'扫描与目录排除同时执行，完成后没有被重建的漏清缓存')
        check(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','并发扫描与排除后的数据库完整性通过')
    # Face results are cleared along with crops, while names and manual notes survive.
    face_dir=RUN/'face-fixture';face_dir.mkdir()
    sample=Path(sys.executable).parent.parent/'Lib/site-packages/insightface/data/images/t1.jpg'
    shutil.copy2(sample,face_dir/'人脸照片.jpg')
    scan([face_dir],faces=True)
    face_asset=next(a for a in all_photos() if Path(a['path']).parent==face_dir)
    faces=req('/api/photos/'+str(face_asset['id']))['faces']
    check(len(faces)>0,'待排除的人脸测试照片实际完成识别')
    req('/api/exclusions/assets',{'ids':[face_asset['id']]})
    check(not req('/api/photos/'+str(face_asset['id']))['faces'] and all(not (DATA/'faces'/f'{f["id"]}.jpg').exists() for f in faces),'排除清理人脸特征记录与人脸小图')
    after={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in before}
    check(before==after,'全部排除恢复流程不改变原始图片内容')
    report={'passed':True,'checks':checks,'test_data':str(RUN),'date':time.strftime('%Y-%m-%dT%H:%M:%S')}
    (REPORT/'upgrade-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
finally:
    process.terminate();process.wait(timeout=10);log.close()
