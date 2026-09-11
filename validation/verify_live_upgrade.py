import json,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8765'
def status():
    with urllib.request.urlopen(URL+'/api/status',timeout=15) as r:return json.load(r)
before=status();assert before['capabilities']['version']=='0.2'
assert before['job']['status']=='running' and before['job']['workers']==4
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1050})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(URL);page.wait_for_selector('.photo-card',timeout=30000)
    assert '500,783' in page.locator('#candidate-summary').inner_text()
    page.click('[data-view="scan"]');page.wait_for_selector('#scan-workers')
    assert page.locator('#scan-workers').input_value()=='4'
    assert '常驻进程' in page.locator('#capabilities').inner_text()
    page.screenshot(path=str(ROOT/'validation/reports/08-live-accelerated-scan.png'),full_page=True,animations='disabled')
    page.click('[data-view="excluded"]');page.wait_for_selector('#exclusion-panel:not([hidden])')
    page.screenshot(path=str(ROOT/'validation/reports/09-live-exclusion-page.png'),full_page=True,animations='disabled')
    assert not errors,errors
    browser.close()
after=status()
result={'passed':True,'version':'0.2','time':time.strftime('%Y-%m-%dT%H:%M:%S'),'checks':['正式后台已切换 0.2 并继续 4 路扫描','正式页面显示候选文件与已登记档案的不同口径','扫描页实际显示 4 路与常驻进程','正式排除管理页面可用','无浏览器 JavaScript 错误'],'job':after['job'],'stats':after['stats'],'metadata_per_second':after['metadata_per_second']}
(ROOT/'validation/reports/live-upgrade-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'files':after['stats']['files'],'metadata_per_second':after['metadata_per_second'],'workers':after['job']['workers']},ensure_ascii=False))
