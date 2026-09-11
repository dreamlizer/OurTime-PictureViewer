"""Read-only browser checks for caption layout and outside-photo dismissal."""
import json,urllib.request,sqlite3,time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8765';checks=[]
def req(path):
    with urllib.request.urlopen(URL+path,timeout=40) as r:return json.load(r)
def check(ok,text):
    if not ok:raise AssertionError(text)
    checks.append(text);print('PASS',text,flush=True)
def open_photo(page,aid=10):
    a=req('/api/photos/'+str(aid));page.goto(URL);page.fill('#search',Path(a['files'][0]['path']).name)
    page.locator(f'[data-photo="{aid}"]').dblclick()
    page.wait_for_function("document.querySelector('#detail-img').complete && document.querySelector('#detail-img').naturalWidth>0")
    return a
def closed(page):page.wait_for_function("!document.querySelector('#detail-dialog').open")
start=req('/api/status')
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1500,'height':1000})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    a=open_photo(page)
    check('JPEG' in page.locator('#signature-format').inner_text() and 'KB' in page.locator('#signature-format').inner_text(),'右侧显示基本文件格式和大小')
    check('拍摄时间' in page.locator('#signature-primary-kind').inner_text() and '2014.04.22' in page.locator('#signature-primary-value').inner_text(),'时间语义和具体日期在主区稳定槽位显示')
    check(page.locator('#photo-signature').inner_text().count('JPEG')==1 and page.locator('#photo-signature').inner_text().count('KB')==1,'同一 metadata 项在底栏最多显示一次')
    page.screenshot(path=str(ROOT/'validation/reports/17-caption-desktop.png'))
    page.locator('#detail-img').click();check(page.locator('#detail-dialog').is_visible(),'点击照片本身保持打开')
    page.click('#zoom-in');page.click('#zoom-fit');page.click('#viewer-info');page.click('#detail-name')
    check(page.locator('#detail-dialog').is_visible(),'缩放按钮和详细资料操作不会误关闭')
    page.click('#close-info');page.click('#zoom-in')
    box=page.locator('#image-viewport').bounding_box();page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
    page.mouse.down();page.mouse.move(box['x']+box['width']/2,20,steps=8);page.mouse.up()
    check(page.locator('#detail-dialog').is_visible(),'从照片开始拖动到外部不会误退出')
    page.click('#zoom-fit');page.click('#signature-format')
    check(page.locator('#detail-dialog').is_visible(),'点击底部 metadata 槽不会误关闭大图')
    page.mouse.click(2,2);closed(page)
    check(True,'点击遮罩可关闭大图')
    open_photo(page);page.mouse.click(2,2);closed(page);check(True,'点击对话框外遮罩可关闭大图')
    with sqlite3.connect('file:'+str(ROOT/'data/library.sqlite3')+'?mode=ro',uri=True) as c:
        aid=c.execute('SELECT a.id FROM assets a JOIN files f ON a.id=f.asset_id WHERE a.height>a.width*1.35 AND a.width>800 AND a.error IS NULL AND a.excluded=0 AND f.excluded=0 AND f.exists_now=1 AND f.size>=1048576 LIMIT 1').fetchone()[0]
    a=open_photo(page,aid)
    check('MB' in page.locator('#signature-format').inner_text(),'较大的原文件自动以 MB 显示')
    area=page.locator('#image-viewport').bounding_box();page.mouse.click(area['x']+3,area['y']+area['height']/2);closed(page)
    check(True,'点击竖向照片旁的空白画布可关闭大图')
    open_photo(page,aid);page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
    check(page.locator('#photo-signature').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'),'窄屏底部信息不横向溢出')
    check(page.locator('#signature-primary').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'),'窄屏主区时间语义槽不横向溢出')
    page.screenshot(path=str(ROOT/'validation/reports/18-caption-narrow.png'))
    page.mouse.click(2,2);closed(page)
    check(not errors,'正式浏览器无 JavaScript 错误')
    browser.close()
end=req('/api/status')
check(start['capabilities']['pid']==end['capabilities']['pid'],'界面验证未重启本地后台')
report={'passed':True,'checks':checks,'time':time.strftime('%Y-%m-%dT%H:%M:%S')}
(ROOT/'validation/reports/caption-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
