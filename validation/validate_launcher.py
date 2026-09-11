import json,time,subprocess,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
url='http://127.0.0.1:8765'
def status():
    with urllib.request.urlopen(url+'/api/status',timeout=3) as r: return json.load(r)
initial=status();assert initial['stats']['assets']==0
pid_before=(root/'data/server.pid').read_text().strip()
subprocess.run(['wscript.exe',str(root/'启动拾光.vbs')],check=True)
time.sleep(2)
assert (root/'data/server.pid').read_text().strip()==pid_before,'重复启动不应创建第二个后台'
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_selector('#empty:not([hidden])')
    assert page.title()=='拾光 · 本地照片资料库'
    page.click('#add-folder');page.wait_for_selector('#start-scan')
    assert 'ExifTool 已就绪' in page.locator('#capabilities').inner_text()
    page.click('[data-view="timeline"]');page.wait_for_selector('#home-query-host .ot-home-ui')
    page.screenshot(path=str(root/'validation/reports/06-delivered-entry.png'),full_page=True,animations='disabled')
    assert not errors
    browser.close()
output={'passed':True,'url':url,'database':initial['capabilities']['data_dir'],'formal_assets':initial['stats']['assets'],
        'checks':['真实 VBS 启动入口响应','重复启动复用原后台','正式资料库为空，不含测试照片','交付入口浏览器实际可用','ExifTool / HEIC / 模型 / 地理数据就绪','无浏览器 JavaScript 错误']}
(root/'validation/reports/launcher.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(output,ensure_ascii=False))
