"""隔离资料库验证：真实 HTTP、真实浏览器、模型推理与持久化。"""
import os
os.environ['NO_ALBUMENTATIONS_UPDATE']='1'
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
import json,time,subprocess,urllib.request,urllib.error,shutil,hashlib,sqlite3
from PIL import Image
from playwright.sync_api import sync_playwright

WORK=ROOT/'validation'/'work'
REPORTS=ROOT/'validation'/'reports'
WORK.mkdir(parents=True,exist_ok=True);REPORTS.mkdir(parents=True,exist_ok=True)
RUN=WORK/time.strftime('%Y%m%d-%H%M%S')
PHOTOS=RUN/'测试照片';PHOTOS.mkdir(parents=True)
DATA=RUN/'database'
URL='http://127.0.0.1:8766'
evidence=[]
def check(condition,message):
    if not condition: raise AssertionError(message)
    evidence.append(message);print('PASS',message,flush=True)
def req(path,body=None,method=None):
    r=urllib.request.Request(URL+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(r,timeout=90) as response:
        return json.load(response)
def wait_job():
    for _ in range(180):
        job=req('/api/status')['job']
        if job['status'] not in ('running','pausing'): return job
        time.sleep(.5)
    raise AssertionError('scan did not complete within 90 seconds')
def start_server():
    env=os.environ.copy();env['PHOTO_LIBRARY_DATA']=str(DATA)
    log=open(RUN/'server.log','ab')
    p=subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--port','8766'],env=env,cwd=ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(40):
        try:
            req('/api/status');return p,log
        except Exception:
            if p.poll() is not None: raise RuntimeError((RUN/'server.log').read_text(errors='replace'))
            time.sleep(.25)
    raise RuntimeError('server start timeout')

exif=Image.Exif();exif[271]='Test camera';exif[272]='Fixture One';exif[36867]='2018:05:06 12:34:56'
exif[34853]={1:'N',2:(40,0,0),3:'E',4:(116,0,0)}
xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:description>fixture-xmp-description</dc:description></rdf:Description></rdf:RDF></x:xmpmeta>'
Image.new('RGB',(960,640),'#aab48c').save(PHOTOS/'相机原图.jpg',exif=exif,xmp=xmp)
shutil.copy2(PHOTOS/'相机原图.jpg',PHOTOS/'完全相同的副本.jpg')
Image.new('RGB',(800,500),'#b7cad2').save(PHOTOS/'IMG_20120721_123456.jpg')
Image.new('RGB',(600,800),'#d6b996').save(PHOTOS/'老照片.png')
Image.new('RGB',(500,300),'#728a91').save(PHOTOS/'Screenshot_20240203.png')
(PHOTOS/'损坏照片.jpg').write_bytes(b'this is not an image')
from pillow_heif import register_heif_opener
register_heif_opener()
Image.new('RGB',(400,300),'#869c76').save(PHOTOS/'手机照片.heic')
sample=Path(sys.executable).parent.parent/'Lib/site-packages/insightface/data/images/t1.jpg'
if not sample.exists(): raise RuntimeError('InsightFace bundled t1.jpg missing')
shutil.copy2(sample,PHOTOS/'人脸测试合照.jpg')
with Image.open(sample) as im: im.save(PHOTOS/'人脸测试合照压缩版.jpg',quality=82)
before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in PHOTOS.iterdir()}
p,log=start_server()
try:
    # Real browser: empty entry, folder browsing, start scan, actual face inference.
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(URL);page.wait_for_selector('#empty:not([hidden])')
        page.screenshot(path=str(REPORTS/'01-empty-library.png'),full_page=True)
        page.click('#empty-add');page.click('#choose-folder');page.wait_for_selector('#folder-list button');page.click('[data-close="folder-dialog"]')
        page.fill('#scan-roots',str(PHOTOS));page.check('#with-faces');page.click('#start-scan')
        page.wait_for_function("document.querySelector('#job-status').textContent.includes('扫描') || document.querySelector('#job-status').textContent.includes('完成')")
        job=wait_job()
        check(job['processed']==9,'真实页面启动扫描，处理 9 个文件')
        check(job['errors']==1 and job['status']=='completed_with_errors','损坏照片明确列为读取问题，任务没有假报全通过')
        stats=req('/api/status')['stats'];check(stats['assets']==8 and stats['duplicates']==1,'9 个文件归为 8 份内容档案，完全重复副本正确关联')
        data=req('/api/photos?limit=100')['items']; by_name={Path(a['path']).name:a for a in data}
        original=next(a for a in data if a['camera'])
        check(original['captured_at']=='2018-05-06T12:34:56','EXIF 拍摄时间与设备被正确采集')
        check(original['latitude']==40 and original['longitude']==116 and original['place'] is not None,'GPS 坐标正确读取，并生成带来源说明的离线附近地名')
        raw=req('/api/photos/'+str(original['id']))['metadata']
        check(raw['ExifTool']['XMP-dc:Description']=='fixture-xmp-description','ExifTool 扩展元数据包含 XMP 内容')
        filename=by_name['IMG_20120721_123456.jpg']
        check(filename['captured_at']=='2012-07-21T12:34:56' and filename['date_source']=='文件名推测','文件名时间作为推测保存')
        check(by_name['老照片.png']['date_source']=='文件修改时间参考','无元数据照片明确标记修改时间参考')
        check(by_name['手机照片.heic']['error'] is None,'HEIC 真实解码与缩略图生成')
        people=req('/api/people?limit=20')['items'];check(len(people)>=2,'InsightFace CPU 在真实测试合照上完成人脸检测与候选分组')
        check(any(x['photo_count']>=2 for x in people),'同一人的原图与压缩版人脸被关联到候选分组')
        detail=req('/api/people/'+str(people[0]['id']))
        check(detail['faces'] and all(isinstance(f.get('asset_id'),int) for f in detail['faces']),'人物详情每张脸都带数字 asset_id，避免点开原图变成 undefined')
        photo=req('/api/photos/'+str(detail['faces'][0]['asset_id']))
        check(photo.get('files') and 'effective_place' in photo,'人物页对应原图详情可读取，含文件位置和地点字段')
        page.click('[data-view="all"]');page.wait_for_selector('.photo-card');page.wait_for_function("document.querySelector('#s-assets').textContent==='8'");page.screenshot(path=str(REPORTS/'02-populated-library.png'),full_page=True,animations='disabled')
        page.click(f'[data-photo="{filename["id"]}"]');page.wait_for_selector('#detail-dialog[open]')
        page.get_by_role('button', name='详细资料').click()
        page.get_by_text('补录时间、地点与备注').click()
        page.wait_for_selector('#edit-date', state='visible')
        page.fill('#edit-date','2012 年夏季');page.select_option('#edit-precision','范围 / 描述');page.fill('#edit-place','北京 · 家庭旅行');page.fill('#edit-notes','仅供验收的测试标注');page.click('#detail-form button[type="submit"]');page.wait_for_function("document.querySelector('#detail-facts').textContent.includes('人工确认')")
        edited=req('/api/photos/'+str(filename['id']));check(edited['manual_date']=='2012 年夏季' and edited['captured_at']=='2012-07-21T12:34:56','真实页面保存大致时间地点，原始时间保持不变')
        page.screenshot(path=str(REPORTS/'03-photo-detail.png'),full_page=True)
        page.click('[data-close="detail-dialog"]');page.fill('#search','家庭旅行');page.wait_for_function("document.querySelector('#result-count').textContent==='1 张'");check(page.locator('.photo-card').count()==1,'真实页面按补录地点搜索定位照片')
        page.fill('#search','');page.wait_for_function("document.querySelector('#result-count').textContent==='8 张'")
        page.click('#select-mode');page.click(f'[data-photo="{by_name["老照片.png"]["id"]}"]');page.click('#batch-edit');page.check('#batch-place-enabled');page.fill('#batch-place','测试批量地点');page.click('#batch-form button[type="submit"]');page.wait_for_function("!document.querySelector('#batch-dialog').open")
        check(req('/api/photos/'+str(by_name['老照片.png']['id']))['manual_place']=='测试批量地点','真实页面批量补录仅更新勾选字段')
        page.click('[data-view="people"]');page.wait_for_selector('.person-card');pid=people[0]['id'];page.click(f'[data-person="{pid}"]');page.wait_for_selector('[data-face-photo]');page.locator('[data-face-photo]').first.click();page.wait_for_selector('#detail-dialog[open]');check(not page.evaluate("document.body.innerText.includes('undefined')"),'人物页点人脸打开原图时，页面可见文本不含 undefined');page.click('[data-close="detail-dialog"]');page.click(f'[data-person="{pid}"]');page.fill('#person-name','测试人物甲');page.click('#person-form button');page.wait_for_function("document.querySelector('#person-title').textContent==='测试人物甲'")
        check(req('/api/people/'+str(pid))['confirmed']==1,'真实页面人物核对命名持久保存')
        details=req('/api/people/'+str(pid));fid=details['faces'][0]['id']
        page.click(f'[data-split="{fid}"]');page.wait_for_function(f"!document.querySelector('[data-split=\"{fid}\"]')")
        check(len(req('/api/people/'+str(pid))['faces'])==len(details['faces'])-1,'真实页面可将误归人脸移出')
        page.click('[data-close="person-dialog"]');page.wait_for_timeout(200)
        groups=req('/api/people?limit=50')['items'];newgroup=next(g for g in groups if g['id'] not in [x['id'] for x in people])
        page.click(f'[data-person="{newgroup["id"]}"]');page.select_option('#merge-target',str(pid));page.click('#merge-person');page.wait_for_function("document.querySelector('#person-title').textContent==='测试人物甲'")
        check(len(req('/api/people/'+str(pid))['faces'])==len(details['faces']),'真实页面人物分组可合并，恢复正确关联')
        page.click('[data-close="person-dialog"]');page.screenshot(path=str(REPORTS/'04-people.png'),full_page=True)
        page.set_viewport_size({'width':760,'height':900});page.click('[data-view="all"]');page.wait_for_selector('.photo-card')
        check(page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),'窄屏页面无横向溢出')
        page.screenshot(path=str(REPORTS/'05-narrow-layout.png'),full_page=True)
        check(not errors,'真实浏览器无 JavaScript 运行错误')
        check('undefined' not in page.inner_text('body'),'真实页面可见文本不含 undefined')
        browser.close()
    # API, persistence and provenance checks.
    try:
        req('/api/photos',{'ids':[filename['id']],'manual_date':'2012-02-30','manual_precision':'日'},'PATCH')
        raise AssertionError('invalid date accepted')
    except urllib.error.HTTPError as e: check(e.code==400,'无效日期被拒绝，不写入错误标注')
    req('/api/scan',{'roots':[str(PHOTOS)],'with_faces':True});second=wait_job()
    check(second['skipped']==8 and req('/api/status')['stats']['assets']==8,'增量重扫跳过 8 个未变化文件，仅重试损坏图片')
    check(req('/api/photos/'+str(filename['id']))['manual_place']=='北京 · 家庭旅行','重新扫描不覆盖人工补录')
    before_faces=len(req('/api/people/'+str(pid))['faces'])
    check(before_faces==len(details['faces']),'重扫不重复生成人脸或重置已确认人物')
    # Changed path and missing file.
    renamed=PHOTOS/'搬家后的照片.jpg';(PHOTOS/'IMG_20120721_123456.jpg').rename(renamed)
    req('/api/scan',{'roots':[str(PHOTOS)],'with_faces':False});wait_job()
    moved=req('/api/photos/'+str(filename['id']))
    check(len(moved['files'])==2 and sum(x['exists_now'] for x in moved['files'])==1 and moved['manual_date']=='2012 年夏季','原图改名后按内容关联，标注保留，旧路径标记缺失')
    backup=req('/api/backup',{},'POST');check(Path(backup['path']).is_file(),'在线 SQLite 备份成功生成')
    with sqlite3.connect(backup['path']) as c: check(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','备份数据库完整性验证通过')
    exported=req('/api/export');check(any(x['name']=='测试人物甲' for x in exported['people']),'导出清单包含已确认人物与标注')
    # Pause immediately; resume reuses unchanged paths.
    pause_dir=RUN/'pause-fixtures';pause_dir.mkdir()
    for i in range(80): shutil.copy2(PHOTOS/'老照片.png',pause_dir/f'暂停测试_{i}.png')
    req('/api/scan',{'roots':[str(pause_dir)],'with_faces':False});req('/api/scan/pause',{},'POST');paused=wait_job()
    check(paused['status']=='paused' and paused['processed']<80,'扫描在完成全量前实际暂停，保持可恢复状态')
    req('/api/scan/'+paused['id']+'/resume',{},'POST');continued=wait_job();check(continued['status']=='completed' and continued['processed']==80,'暂停后继续扫描，全部 80 个文件完成入库')
    p.terminate();p.wait(timeout=10);log.close();p,log=start_server()
    check(req('/api/photos/'+str(filename['id']))['manual_date']=='2012 年夏季' and req('/api/people/'+str(pid))['name']=='测试人物甲','后台重启后照片补录与人物姓名仍然存在')
    after={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in PHOTOS.iterdir()}
    after['IMG_20120721_123456.jpg']=after.pop('搬家后的照片.jpg')
    check(before==after,'全流程原始图片内容 SHA256 未改变')
    # Cross-origin writes are refused.
    r=urllib.request.Request(URL+'/api/backup',data=b'{}',headers={'Origin':'https://example.com','Content-Type':'application/json'})
    try: urllib.request.urlopen(r);raise AssertionError('cross origin accepted')
    except urllib.error.HTTPError as e: check(e.code==403,'跨站写入请求被拒绝')
    (REPORTS/'validation.json').write_text(json.dumps({'passed':True,'checks':evidence,'test_data':str(RUN),'source':'InsightFace bundled t1.jpg and generated test images; not user photos'},ensure_ascii=False,indent=2),encoding='utf-8')
finally:
    p.terminate();p.wait(timeout=10);log.close()
