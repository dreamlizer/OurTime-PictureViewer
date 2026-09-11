"""Real-browser scope, paging, keyboard, preview and localization acceptance."""
import os,sys,json,time,sqlite3,subprocess,urllib.request,urllib.parse,hashlib
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from geo_labels import PlaceIndex
RUN=ROOT/'validation/work'/('browse-'+time.strftime('%Y%m%d-%H%M%S'))
DATA=RUN/'data';PHOTOS=RUN/'photos';FOLDER=PHOTOS/'假期_%相册';OTHER=PHOTOS/'其他相册'
FOLDER.mkdir(parents=True);OTHER.mkdir()
URL='http://127.0.0.1:8768';checks=[]
def check(value,message):
    if not value:raise AssertionError(message)
    checks.append(message);print('PASS',message,flush=True)
def req(path,body=None,method=None):
    r=urllib.request.Request(URL+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(r,timeout=90) as response:return json.load(response)
for i in range(75):
    e=Image.Exif();e[36867]=f'2020:07:{i%28+1:02} 10:00:00'
    Image.new('RGB',(1400+i,950),(i*3%255,90,i*7%255)).save((FOLDER if i<70 else OTHER)/f'照片{i+1}.jpg',exif=e)
Image.new('RGB',(3600,2400),'#678b79').save(FOLDER/'大图测试.jpg')
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in PHOTOS.rglob('*.jpg')}
env={**os.environ,'PHOTO_LIBRARY_DATA':str(DATA),'PHOTO_WEB_ROOT':str(ROOT/'validation/web-browse')}
log=open(RUN/'server.log','w',encoding='utf-8')
process=subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--port','8768'],env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
try:
    for _ in range(100):
        try:req('/api/status');break
        except Exception:time.sleep(.1)
    req('/api/scan',{'roots':[str(PHOTOS)],'workers':4,'with_faces':False})
    for _ in range(400):
        job=req('/api/status')['job']
        if job['status'] not in ('running','pausing'):break
        time.sleep(.1)
    check(job['status']=='completed' and job['errors']==0,'新版本 4 路真实扫描完成，无读取错误')
    query=urllib.parse.urlencode({'directory':str(FOLDER),'sort':'name_asc'})
    records=req('/api/photos?'+query+'&limit=120');ids=[a['id'] for a in records['items']]
    check(len(ids)==71 and [Path(a['path']).name for a in records['items'][:3]]==['大图测试.jpg','照片1.jpg','照片2.jpg'],'目录筛选处理特殊字符，按文件名数字自然顺序排列')
    sequence=req('/api/photos?'+query+'&sequence=true')['ids']
    check(sequence==ids,'跨页浏览序列与目录列表排序完全一致')
    check(req('/api/photos?'+query.replace('name_asc','name_desc')+'&sequence=true')['ids']==ids[::-1],'文件名倒序与顺序一致')
    with sqlite3.connect(DATA/'library.sqlite3') as c:
        c.execute("INSERT INTO people(id,name,confirmed) VALUES(1,'小明',1)")
        for aid in ids[1:5]:
            c.execute("INSERT INTO faces(asset_id,person_id,bbox,embedding,score,reviewed) VALUES(?,1,'[0,0,100,100]',?,0.9,1)",(aid,b'\0'*2048))
        face_ids=[r[0] for r in c.execute('SELECT id FROM faces')]
    for fid in face_ids:Image.new('RGB',(100,100),'#568978').save(DATA/'faces'/f'{fid}.jpg')
    check(set(req('/api/photos?person=1&sequence=true')['ids'])==set(ids[1:5]),'人物浏览序列仅包含该人物关联照片')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1500,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(URL);page.wait_for_selector('.photo-card')
        page.fill('#directory-filter',str(FOLDER));page.click('#apply-directory')
        page.wait_for_function("document.querySelector('#result-count').textContent==='71 张'")
        page.locator('.photo-card').first.dblclick()
        page.wait_for_selector('#detail-dialog[open]')
        page.wait_for_function("document.querySelector('#detail-img').naturalWidth===2560")
        check(page.locator('#viewer-position').inner_text()=='1 / 71','双击打开清晰大图，显示目录范围及张数')
        page.keyboard.press('ArrowRight');page.wait_for_function("document.querySelector('#viewer-position').textContent==='2 / 71'")
        page.keyboard.press('ArrowDown');page.wait_for_function("document.querySelector('#viewer-position').textContent==='3 / 71'")
        page.keyboard.press('ArrowLeft');page.wait_for_function("document.querySelector('#viewer-position').textContent==='2 / 71'")
        page.keyboard.press('ArrowUp');page.wait_for_function("document.querySelector('#viewer-position').textContent==='1 / 71'")
        check(page.locator('#viewer-prev').is_disabled(),'四个方向键翻图，第一张不跳出范围')
        page.keyboard.press('End');page.wait_for_function("document.querySelector('#viewer-position').textContent==='71 / 71'")
        check(page.locator('#viewer-next').is_disabled() and page.locator('#detail-name').inner_text()=='照片70.jpg','可跨越 60 张分页到末张，末张不串到其他文件夹')
        page.keyboard.press('Home');page.wait_for_function("document.querySelector('#viewer-position').textContent==='1 / 71'")
        # Freeze IDs despite new scanned files sorting ahead of the current page.
        Image.new('RGB',(800,500),'#919984').save(FOLDER/'插入照片.jpg')
        req('/api/scan',{'roots':[str(PHOTOS)],'workers':4})
        for _ in range(150):
            if req('/api/status')['job']['status'] not in ('running','pausing'):break
            time.sleep(.1)
        page.keyboard.press('ArrowRight');page.wait_for_function("document.querySelector('#viewer-position').textContent==='2 / 71'")
        check(page.locator('#detail-name').inner_text()=='照片1.jpg','边扫描边看图，新增照片不插入正在浏览的序列')
        for _ in range(6):page.keyboard.press('ArrowRight')
        page.wait_for_function("document.querySelector('#viewer-position').textContent==='8 / 71'")
        check(page.locator('#detail-name').inner_text()=='照片7.jpg','快速连续按键不丢失翻图步数')
        page.fill('#edit-notes','未保存的线索');page.keyboard.press('ArrowLeft')
        check(page.locator('#viewer-position').inner_text()=='8 / 71','在补录输入框使用方向键不会误翻照片')
        page.click('#viewer-next');page.wait_for_function("document.querySelector('#viewer-position').textContent==='9 / 71'")
        page.click('#viewer-prev');page.wait_for_function("document.querySelector('#viewer-position').textContent==='8 / 71'")
        check(page.locator('#edit-notes').input_value()=='未保存的线索','翻图再返回保留未保存的补录草稿')
        page.click('#detail-form button[type=submit]');page.wait_for_function("document.querySelector('#toast').textContent.includes('已保存')")
        check(req('/api/photos/'+str(ids[7]))['notes']=='未保存的线索','原有资料补录保存功能仍正常')
        page.click('#zoom-actual');page.wait_for_function("document.querySelector('#zoom-level').textContent==='100%'")
        check(page.locator('#image-viewport').evaluate('(e)=>e.scrollWidth>e.clientWidth'),'预览原尺寸可放大，超出部分可滚动拖看')
        page.click('#zoom-fit');page.click('#viewer-info');check(page.locator('.detail-info').is_hidden(),'可收起信息面板扩大看图区域')
        page.click('#viewer-fullscreen');page.wait_for_function("!!document.fullscreenElement && document.querySelector('#viewer-fullscreen').textContent==='退出全屏'")
        check(page.locator('#viewer-fullscreen').inner_text()=='退出全屏','全屏按钮进入浏览器真实全屏')
        page.click('#viewer-fullscreen');page.wait_for_function('!document.fullscreenElement')
        page.select_option('#slide-delay','3');page.click('#viewer-play')
        page.wait_for_function("document.querySelector('#viewer-position').textContent==='9 / 71'",timeout=10000)
        page.click('#viewer-play');check(page.locator('#viewer-play').inner_text()=='自动播放','自动播放按选择间隔翻图，并可暂停')
        page.screenshot(path=str(ROOT/'validation/reports/10-browse-viewer.png'),full_page=True)
        page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('#detail-dialog').open")
        page.click('#clear-directory');page.select_option('#person-filter','1');page.wait_for_function("document.querySelector('#result-count').textContent==='4 张'")
        page.locator('.photo-card').first.click();page.wait_for_selector('#detail-dialog[open]')
        page.keyboard.press('End');page.wait_for_function("document.querySelector('#viewer-position').textContent==='4 / 4'")
        check('小明' in page.locator('#viewer-context').inner_text() and page.locator('#viewer-next').is_disabled(),'人物筛选中翻图始终留在该人物的 4 张照片内')
        page.keyboard.press('Escape');page.click('[data-view=people]');page.click('#people-grid [data-person="1"]');page.locator('[data-face-photo]').first.click()
        page.wait_for_selector('#detail-dialog[open]');check(page.locator('#viewer-position').inner_text().endswith('/ 4'),'从人物档案里的脸部小图进入也保持人物范围')
        page.keyboard.press('Escape');page.set_viewport_size({'width':390,'height':844})
        page.click('[data-view=all]');page.locator('.photo-card').first.click();page.wait_for_selector('#detail-dialog[open]')
        check(page.locator('#viewer-next').is_visible() and page.locator('#viewer-play').is_visible(),'窄窗口仍能翻图和使用浏览工具')
        page.screenshot(path=str(ROOT/'validation/reports/11-browse-narrow.png'),full_page=True)
        check(not errors,'真实 Chrome 浏览核心路径无 JavaScript 错误')
        browser.close()
    geo=PlaceIndex(Path('G:/CodexModels/geo'))
    examples={name:geo.nearest(lat,lon) for name,lat,lon in [('北京',39.9,116.4),('上海',31.23,121.47),('香港',22.28,114.16),('台北',25.03,121.56),('巴黎',48.85,2.35),('东京',35.68,139.69)]}
    for name in ('北京','上海','香港','台北'):
        check(examples[name][0] and not any(c.isascii() and c.isalpha() for c in examples[name][0]),name+'地点显示中文，无拼音及国家代码')
    check('France' in examples['巴黎'][0] and 'Japan' in examples['东京'][0],'国外地点采用拉丁字母名称及英文国家名')
    check(all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items()),'浏览、缩放和地名处理没有修改任何原照片')
    check(not (DATA/'previews').exists(),'清晰大图预览不产生额外磁盘缓存目录')
    result={'passed':True,'checks':checks,'geo_examples':examples,'run':str(RUN)}
    (ROOT/'validation/reports/browse-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(checks),'geo':examples},ensure_ascii=False))
finally:
    process.terminate();process.wait(timeout=20);log.close()
