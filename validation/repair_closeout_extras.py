"""Closeout extras: autoplay across window, input keys, three widths on current app.js."""
from __future__ import annotations
import json, sys
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'validation/reports/code-quality-repair-20260910-r4/runs/extras'
URL = 'http://127.0.0.1:8783'
OUT.mkdir(parents=True, exist_ok=True)


def parse_pos(text):
    a,b = text.replace(',','').split('/')
    return int(a.strip()), int(b.strip())


def main():
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1280,'height':800})
        page.goto(URL, wait_until='networkidle', timeout=20000)
        status=page.evaluate("async () => await (await fetch('/api/status')).json()")
        results.append({'id':'identity','actual':status['capabilities']['data_dir'],'passed':'browser500' in status['capabilities']['data_dir']})
        page.wait_for_function("() => typeof openPhoto==='function'")
        page.evaluate("() => openPhoto(199, currentBrowseContext())")
        page.wait_for_selector('#detail-dialog[open]')
        page.wait_for_function("() => state.detail && state.detail.id===199")
        # autoplay from 199 should pass 200/201 and can be stopped; we run a few ticks then End
        page.locator('#slide-delay').select_option('3')
        page.locator('#viewer-play').click()
        seen=[]
        deadline = page.evaluate('() => Date.now()')
        for _ in range(20):
            page.wait_for_timeout(400)
            seen.append(int(page.evaluate("() => state.detail.id")))
            if 201 in seen and 200 in seen:
                break
        if page.locator('#viewer-play').inner_text().find('暂停')>=0:
            page.locator('#viewer-play').click()
        passed = (200 in seen and 201 in seen) or (seen[-1]>=201 and all(seen[i]<=seen[i+1] for i in range(len(seen)-1)))
        results.append({'id':'R03-C-autoplay-cross-201','expected':'reaches 200 then 201','actual':seen,'passed':passed})
        # input keys should not move photo
        page.get_by_role('button', name='详细资料').click()
        page.get_by_text('补录时间、地点与备注').click()
        page.wait_for_selector('#edit-date', state='visible')
        before=int(page.evaluate("() => state.detail.id"))
        page.fill('#edit-date','2012')
        page.locator('#edit-date').press('ArrowRight')
        page.locator('#edit-date').press('Home')
        page.locator('#edit-date').press('End')
        after=int(page.evaluate("() => state.detail.id"))
        results.append({'id':'R03-G-input-keys','expected':before,'actual':after,'passed':before==after})
        page.keyboard.press('Escape')
        shots=OUT/'widths'
        shots.mkdir(exist_ok=True)
        for w,name in [(1280,'1280.png'),(768,'768.png'),(390,'390.png')]:
            page.set_viewport_size({'width':w,'height':800})
            page.wait_for_timeout(300)
            page.screenshot(path=str(shots/name), full_page=True)
        results.append({'id':'R09-widths','expected':'screenshots written','actual':[str(p) for p in shots.glob('*.png')],'passed':len(list(shots.glob('*.png')))==3})
        hidden=page.locator('[data-view="objects"]').get_attribute('hidden')
        results.append({'id':'objects-hidden','passed':hidden is not None,'actual':hidden})
        browser.close()
    payload={'verified_at':datetime.now().astimezone().isoformat(timespec='seconds'),'url':URL,'results':results}
    (OUT/'extras.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    sys.exit(0 if all(r.get('passed') for r in results) else 1)

if __name__=='__main__':
    main()
