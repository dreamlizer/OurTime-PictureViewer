"""Read-only acceptance against the deployed service; never changes library data."""
import json,time,urllib.request,urllib.parse,hashlib,sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8765';checks=[]
def req(path):
    with urllib.request.urlopen(URL+path,timeout=60) as r:return json.load(r)
def check(ok,name):
    if not ok:raise AssertionError(name)
    checks.append(name);print('PASS',name,flush=True)
s=req('/api/status');check(s['capabilities']['version']=='0.3' and s['job']['workers']==4 and s['job']['status']=='running','正式后台 0.3 已继续 4 路扫描')
start=time.perf_counter();seq=req('/api/photos?sequence=true');seconds=time.perf_counter()-start
check(len(seq['ids'])>40000,'正式四万张资料库的浏览序列可加载')
found=req('/api/photos?'+urllib.parse.urlencode({'q':'望京','limit':60}))
check(found['total']>0 and all('望京' in a['effective_place'] for a in found['items']),'旧照片可以按中文地点“望京”搜索')
one=next(a for a in found['items'] if not a['error'])
folder=str(Path(one['path']).parent);original=Path(one['path']);before=hashlib.sha256(original.read_bytes()).hexdigest()
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1500,'height':1000});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(URL);page.wait_for_selector('.photo-card')
    page.fill('#directory-filter',folder);page.click('#apply-directory')
    page.wait_for_function("document.querySelector('#sort-order').value==='name_asc'")
    page.wait_for_function('(p)=>state.directory===p && state.items.length>1',arg=folder)
    # Wait for the resulting response, not just the input value changing.
    page.wait_for_timeout(500)
    page.locator('.photo-card').first.dblclick();page.wait_for_selector('#detail-dialog[open]')
    page.wait_for_function("document.querySelector('#detail-img').src.includes('/api/preview/') && document.querySelector('#detail-img').naturalWidth>0")
    total=page.locator('#viewer-position').inner_text().split(' / ')[1]
    page.keyboard.press('ArrowRight');page.wait_for_function('(t)=>document.querySelector("#viewer-position").textContent==="2 / "+t',arg=total)
    page.keyboard.press('ArrowDown');page.wait_for_function('(t)=>document.querySelector("#viewer-position").textContent==="3 / "+t',arg=total)
    page.keyboard.press('ArrowUp');page.wait_for_function('(t)=>document.querySelector("#viewer-position").textContent==="2 / "+t',arg=total)
    check(folder.split('\\')[-1] in page.locator('#viewer-context').inner_text(),'正式目录内四方向键与大图预览可用')
    page.click('#zoom-in');page.click('#zoom-fit')
    page.click('#viewer-fullscreen');page.wait_for_function("!!document.fullscreenElement && document.querySelector('#viewer-fullscreen').textContent==='退出全屏'")
    page.click('#viewer-fullscreen');page.wait_for_function('!document.fullscreenElement')
    check(True,'正式页面可全屏并返回')
    page.select_option('#slide-delay','3');page.click('#viewer-play')
    page.wait_for_function('(t)=>document.querySelector("#viewer-position").textContent==="3 / "+t',arg=total,timeout=15000)
    page.click('#viewer-play');page.wait_for_timeout(3300)
    check(page.locator('#viewer-position').inner_text()=='3 / '+total,'正式页面自动播放并暂停后保持当前照片')
    page.screenshot(path=str(ROOT/'validation/reports/12-live-browse-viewer.png'),full_page=False)
    page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('#detail-dialog').open")
    check(not errors,'正式浏览核心路径无 JavaScript 错误')
    browser.close()
check(hashlib.sha256(original.read_bytes()).hexdigest()==before,'正式照片原文件内容保持不变')
with sqlite3.connect('file:'+str(ROOT/'data/library.sqlite3')+'?mode=ro',uri=True) as c:
    legacy=c.execute("SELECT count(*) FROM assets WHERE place_source LIKE 'GeoNames %'").fetchone()[0]
    domestic=[r[0] for r in c.execute("SELECT place FROM assets WHERE place LIKE '中国%'")]
check(legacy==0 and not any(any(ch.isascii() and ch.isalpha() for ch in label) for label in domestic),'正式库旧自动地名已转换，国内标签无拼音')
result={'passed':True,'checks':checks,'sequence_seconds':round(seconds,3),'sequence_total':seq['total'],'domestic_labels':len(domestic),'status':req('/api/status'),'time':time.strftime('%Y-%m-%dT%H:%M:%S')}
(ROOT/'validation/reports/live-browse-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'checks':len(checks),'sequence_seconds':round(seconds,3),'files':result['status']['stats']['files']},ensure_ascii=False))
