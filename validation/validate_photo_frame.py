"""Compare old/new viewer geometry on real photos without modifying the library."""
import json,time,urllib.request,urllib.parse,sqlite3,argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8765';checks=[]
parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
def req(path):
    with urllib.request.urlopen(URL+path,timeout=60) as r:return json.load(r)
def check(ok,text):
    if not ok:raise AssertionError(text)
    checks.append(text);print('PASS',text,flush=True)
def open_id(page,aid):
    detail=req('/api/photos/'+str(aid));name=Path(detail['files'][0]['path']).name
    page.goto(URL);page.wait_for_selector('#search');page.fill('#search',name)
    page.locator(f'[data-photo="{aid}"]').dblclick(timeout=30000)
    page.wait_for_function("document.querySelector('#detail-img').src.includes('/api/preview/') && document.querySelector('#detail-img').complete && document.querySelector('#detail-img').naturalWidth>0")
    return detail
def route_static(page,root):
    def serve(route):
        path=urllib.parse.urlparse(route.request.url).path
        name='index.html' if path=='/' else path.lstrip('/')
        if name in ('index.html','app.js','style.css','viewer.js'):
            route.fulfill(path=str(root/name))
        else:route.continue_()
    page.route(URL+'/**',serve)
def geometry(page):
    return page.locator('#detail-img').bounding_box()
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    old=browser.new_page(viewport={'width':1500,'height':1000});route_static(old,ROOT/'validation/frame-backup');open_id(old,10);before=geometry(old);old.close()
    page=browser.new_page(viewport={'width':1500,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    if not args.live:route_static(page,ROOT/'validation/web-frame')
    detail=open_id(page,10);after=geometry(page)
    ratio=after['width']*after['height']/(before['width']*before['height'])
    check(ratio>1.3,'同一横向照片的默认显示面积比旧版增加至少 30%')
    check(page.locator('.detail-info').is_hidden(),'双击后详细资料默认收起，照片占据主区域')
    check(page.locator('#signature-camera').inner_text()=='HTC 801e','白边设备名称取自元数据，并避免重复品牌名')
    check('1/100 秒' in page.locator('#signature-settings').inner_text() and '125' in page.locator('#signature-settings').inner_text(),'白边曝光参数与实际元数据一致')
    check('望京' in page.locator('#signature-place').inner_text(),'白边采用中文地点并保留附近参考表述')
    page.screenshot(path=str(ROOT/'validation/reports'/('14-frame-live.png' if args.live else '13-frame-landscape.png')))
    page.click('#viewer-info');page.wait_for_selector('.detail-info:visible')
    drawer=geometry(page)
    check(abs(drawer['width']-after['width'])<2,'展开详细资料采用浮层，照片不会被挤小')
    page.click('.info-edit > summary');check(page.locator('#edit-notes').is_visible(),'原有补录保留在折叠区，需要时可打开')
    page.click('#close-info');page.click('#zoom-in');check(geometry(page)['width']>after['width'],'放大操作仍然可用')
    page.click('#zoom-fit');page.click('#viewer-fullscreen');page.wait_for_function('!!document.fullscreenElement')
    page.click('#viewer-fullscreen');page.wait_for_function('!document.fullscreenElement')
    check(True,'全屏与退出全屏仍然可用')
    page.keyboard.press('Escape');page.fill('#search','');page.fill('#directory-filter',str(Path(detail['files'][0]['path']).parent));page.click('#apply-directory')
    page.wait_for_timeout(500);page.locator('.photo-card').first.dblclick();page.wait_for_selector('#detail-dialog[open]')
    page.wait_for_function("document.querySelector('#viewer-position').textContent.startsWith('1 / ')")
    page.keyboard.press('ArrowRight');page.wait_for_function("document.querySelector('#viewer-position').textContent.startsWith('2 / ')")
    check(page.locator('.detail-info').is_hidden(),'重新打开仍默认大图，方向键继续在当前范围翻图')
    with sqlite3.connect('file:'+str(ROOT/'data/library.sqlite3')+'?mode=ro',uri=True) as c:
        portrait=c.execute('SELECT a.id FROM assets a WHERE a.height>a.width*1.35 AND a.width>800 AND a.error IS NULL AND a.excluded=0 AND EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.excluded=0 AND f.exists_now=1) LIMIT 1').fetchone()[0]
    open_id(page,portrait)
    mat=page.locator('#photo-mat').bounding_box();area=page.locator('#image-viewport').bounding_box()
    check(mat['height']<=area['height']+1 and mat['width']<=area['width']+1,'竖向照片连同白边完整显示，不裁切照片')
    page.screenshot(path=str(ROOT/'validation/reports/15-frame-portrait.png'))
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250)
    mat=page.locator('#photo-mat').bounding_box();area=page.locator('#image-viewport').bounding_box()
    check(mat['height']<=area['height']+1 and mat['width']<=area['width']+1,'窄窗口白边自动换行，整张图片完整显示')
    check(page.locator('#signature-settings').evaluate('(e)=>e.scrollWidth<=e.clientWidth+2'),'窄窗口曝光参数没有横向溢出')
    page.screenshot(path=str(ROOT/'validation/reports/16-frame-narrow.png'))
    check(not errors,'真实浏览器无 JavaScript 错误')
    browser.close()
report={'passed':True,'live':args.live,'checks':checks,'before':before,'after':after,'image_area_ratio':round(ratio,3),'time':time.strftime('%Y-%m-%dT%H:%M:%S')}
(ROOT/'validation/reports'/('photo-frame-live.json' if args.live else 'photo-frame-validation.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
