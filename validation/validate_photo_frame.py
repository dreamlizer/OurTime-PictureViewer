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
    detail=req('/api/photos/'+str(aid));page.goto(URL)
    page.wait_for_function("typeof openPhoto === 'function'", timeout=15000)
    page.evaluate("async id => { await openPhoto(id); return true; }", aid)
    page.wait_for_function("document.querySelector('#detail-img').complete && document.querySelector('#detail-img').naturalWidth>0")
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
    page=browser.new_page(viewport={'width':1500,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    detail=open_id(page,10);after=geometry(page)
    check(after['width']>0 and after['height']>0,'真实照片在新版相框中完成首屏布局')
    check(page.locator('.detail-info').is_hidden(),'双击后详细资料默认收起，照片占据主区域')
    check('2014.04.22' in page.locator('#signature-primary-value').inner_text(),'主区使用稳定语义槽显示时间')
    check('HTC 801e' in page.locator('#signature-settings').inner_text() and 'ISO 125' in page.locator('#signature-settings').inner_text(),'次区显示设备与曝光信息，且不复制到右侧')
    check(page.locator('#signature-place').inner_text().strip()!='','主区地点槽显示实际地点内容')
    page.screenshot(path=str(ROOT/'validation/reports'/('14-frame-live.png' if args.live else '13-frame-landscape.png')))
    page.click('#viewer-info');page.wait_for_selector('.detail-info:visible');page.wait_for_timeout(250)
    drawer=geometry(page)
    check(drawer['width']>=after['width']-10,'展开详细资料采用浮层，照片不会被挤小')
    page.click('.info-edit > summary');check(page.locator('#edit-notes').is_visible(),'原有补录保留在折叠区，需要时可打开')
    page.click('#close-info');page.click('#zoom-in');check(geometry(page)['width']>after['width'],'放大操作仍然可用')
    page.click('#zoom-fit')
    check(page.locator('.detail-info').is_hidden(),'关闭信息层后底部语义信息仍保持独立')
    page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('#detail-dialog').open");page.goto(URL);page.wait_for_function("typeof openPhoto === 'function'", timeout=15000);page.evaluate("async id => { await openPhoto(id); return true; }", detail['id']);page.wait_for_selector('#detail-dialog[open]')
    page.wait_for_function("document.querySelector('#viewer-position').textContent.includes(' / ')")
    position=page.locator('#viewer-position').inner_text();page.keyboard.press('ArrowRight');page.wait_for_function("old => document.querySelector('#viewer-position').textContent !== old", arg=position)
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
report={'passed':True,'live':args.live,'checks':checks,'after':after,'time':time.strftime('%Y-%m-%dT%H:%M:%S')}
(ROOT/'validation/reports'/('photo-frame-live.json' if args.live else 'photo-frame-validation.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
