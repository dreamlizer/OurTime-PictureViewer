"""Real-library scrolling, bounded live images, selection and viewer checks."""
import argparse,json,time,urllib.parse,urllib.request,subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8765'
parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args();checks=[]
def check(ok,text):
    if not ok:raise AssertionError(text)
    checks.append(text);print('PASS',text,flush=True)
def status():
    with urllib.request.urlopen(URL+'/api/status',timeout=60) as r:return json.load(r)
before=status()
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1500,'height':1000});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    if not args.live:
        def serve(route):
            path=urllib.parse.urlparse(route.request.url).path
            name='index.html' if path=='/' else path.lstrip('/')
            if name in ('index.html','app.js','style.css','viewer.js','waterfall.js'):route.fulfill(path=str(ROOT/'validation/web-waterfall'/name))
            else:route.continue_()
        page.route(URL+'/**',serve)
    cdp=page.context.new_cdp_session(page);cdp.send('Performance.enable')
    system=browser.new_browser_cdp_session()
    def metrics():
        ids=[int(p['id']) for p in system.send('SystemInfo.getProcessInfo')['processInfo']]
        cmd='(Get-Process -Id '+','.join(map(str,ids))+' -ErrorAction SilentlyContinue | Measure-Object PrivateMemorySize64 -Sum).Sum'
        private=int(subprocess.check_output(['powershell','-NoProfile','-Command',cmd],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).strip())
        values={m['name']:m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}
        return {'browser_private_mb':round(private/1048576,1),'js_heap_mb':round(values['JSHeapUsedSize']/1048576,1),'live_thumbnails':page.locator('#photo-grid img').count(),'cached_pages':page.evaluate('waterfall.cache.size'),'expanded_pages':page.evaluate('waterfall.heights.length')}
    page.goto(URL);page.wait_for_selector('.photo-card');page.wait_for_function('waterfall.pending.size===0')
    first=page.locator('.photo-card').first.get_attribute('data-photo')
    check(page.locator('.pagination').count()==0,'翻页按钮已替换为连续滚动入口')
    initial=metrics();max_live=0
    for i in range(20):
        count=page.evaluate('waterfall.heights.length')
        page.evaluate('scrollTo(0,document.documentElement.scrollHeight)')
        page.wait_for_function('(n)=>waterfall.heights.length>n',arg=count,timeout=30000)
        page.wait_for_function('waterfall.pending.size===0',timeout=30000)
        page.wait_for_timeout(80)
        max_live=max(max_live,page.locator('#photo-grid img').count())
    later=metrics()
    print(json.dumps({'initial':initial,'after_scrolling':later,'max_live':max_live}),flush=True)
    check(later['expanded_pages']>=21,'实际连续滚动加载超过 500 张照片')
    check(max_live<=144 and later['cached_pages']<=6,'滚动后缩略图节点和资料缓存保持设定上限')
    check(page.locator(f'[data-photo="{first}"]').count()==0,'远离屏幕的旧照片节点已被回收')
    # Choose a genuinely visible tile, not an off-screen buffered tile.
    aid=page.evaluate('streamVisibleIds()[0]')
    tile=page.locator(f'[data-photo="{aid}"]').bounding_box()
    scroll=page.evaluate('scrollY');page.mouse.dblclick(tile['x']+tile['width']/2,max(10,min(990,tile['y']+tile['height']/2)))
    page.wait_for_selector('#detail-dialog[open]');page.wait_for_function('(id)=>state.detail?.id===id',arg=aid)
    page.keyboard.press('ArrowRight');page.wait_for_function('(id)=>state.detail?.id!==id',arg=aid)
    page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('#detail-dialog').open")
    page.wait_for_timeout(150);end_scroll=page.evaluate('scrollY')
    check(abs(end_scroll-scroll)<30,f'瀑布流打开大图可继续翻图，关闭后保留滚动位置（{scroll} → {end_scroll}）')
    page.evaluate('scrollTo(0,0)');page.wait_for_selector(f'[data-photo="{first}"]',timeout=30000)
    check(True,'向上滚动可以重新加载已回收的照片')
    page.click('#select-mode');page.locator(f'[data-photo="{first}"]').click()
    page.evaluate('scrollTo(0,document.documentElement.scrollHeight)');page.wait_for_function('waterfall.pending.size===0')
    page.evaluate('scrollTo(0,0)');page.wait_for_selector(f'[data-photo="{first}"]',timeout=30000)
    check(page.locator(f'[data-photo="{first}"]').evaluate('(e)=>e.classList.contains("selected")'),'回收并重新加载后，批量选择状态仍保留')
    page.click('#select-mode');page.fill('#search','2014-04-22 18-09-34-HTC ONE.jpg')
    page.wait_for_function("waterfall.query.q.includes('HTC ONE') && waterfall.pending.size===0")
    check(page.locator('[data-photo="10"]').count()==1 and page.evaluate('waterfall.heights.length')<=2,'改变搜索条件重置瀑布流，不混入旧结果')
    page.fill('#search','');page.wait_for_function("waterfall.query.q==='' && waterfall.pending.size===0")
    page.wait_for_timeout(200);page.screenshot(path=str(ROOT/'validation/reports/19-waterfall-desktop.png'))
    page.set_viewport_size({'width':390,'height':844});page.wait_for_function("waterfall.width===document.querySelector('#photo-grid').clientWidth")
    page.wait_for_timeout(150)
    if not page.evaluate('document.documentElement.scrollWidth<=innerWidth'):
        print(json.dumps(page.evaluate("()=>({dialog:document.querySelector('#detail-dialog').open,width:innerWidth,scroll:document.documentElement.scrollWidth,offenders:[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,20)})")),flush=True)
        page.screenshot(path=str(ROOT/'validation/reports/waterfall-overflow-diagnostic.png'))
    check(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'窄屏瀑布流无横向页面溢出')
    page.screenshot(path=str(ROOT/'validation/reports/20-waterfall-narrow.png'))
    check(not errors,'真实 Chrome 连续滚动与大图操作无 JavaScript 错误')
    final=metrics();browser.close()
after=status();check(before['capabilities']['pid']==after['capabilities']['pid'],'界面更新没有重启扫描后台')
report={'passed':True,'live':args.live,'checks':checks,'initial':initial,'after_scrolling':later,'final':final,'max_live_thumbnails':max_live,'time':time.strftime('%Y-%m-%dT%H:%M:%S')}
(ROOT/'validation/reports'/('waterfall-live.json' if args.live else 'waterfall-validation.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
