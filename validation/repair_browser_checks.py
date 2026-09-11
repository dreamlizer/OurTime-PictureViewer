"""Current-web browser checks against an isolated repair server."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'validation' / 'reports' / 'code-quality-repair-20260910-r2'
SHOTS = REPORT / 'screenshots' / 'b5'
URL = 'http://127.0.0.1:8780'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 800}, locale='zh-CN')
        page = context.new_page()
        console = []
        page.on('console', lambda msg: console.append({'type': msg.type, 'text': msg.text}))
        page.on('pageerror', lambda exc: console.append({'type': 'pageerror', 'text': str(exc)}))
        page.goto(URL, wait_until='networkidle', timeout=20000)
        status = page.evaluate("async () => (await fetch('/api/status')).json()")
        data_dir = (status.get('capabilities') or {}).get('data_dir')
        pid = (status.get('capabilities') or {}).get('pid')
        ok = str(data_dir).endswith('repair-20260910\\browser') or str(data_dir).replace('/', '\\').endswith('repair-20260910\\browser')
        results.append({'id': 'R10-identity', 'status': 'PASS' if 'repair-20260910' in str(data_dir) and '照片浏览器\\data' not in str(data_dir).replace('/', '\\') else 'FAIL', 'actual': {'data_dir': data_dir, 'pid': pid}})
        page.wait_for_selector('.photo-card, #no-results, #stream-status', timeout=10000)
        page.screenshot(path=str(SHOTS / '1280-library.png'), full_page=True)
        # open first photo
        card = page.locator('[data-photo]').first
        if card.count():
            card.click()
            page.wait_for_selector('#detail-dialog[open]', timeout=8000)
            page.screenshot(path=str(SHOTS / '1280-viewer.png'))
            page.keyboard.press('ArrowRight')
            page.wait_for_timeout(300)
            pos = page.locator('#viewer-position').inner_text()
            page.keyboard.press('Home')
            page.wait_for_timeout(300)
            home = page.locator('#viewer-position').inner_text()
            page.keyboard.press('End')
            page.wait_for_timeout(300)
            end = page.locator('#viewer-position').inner_text()
            results.append({'id': 'R10-viewer', 'status': 'PASS' if home.strip().startswith('1 /') and end.strip().endswith('/ 24') and end.strip().startswith('24 /') else 'FAIL', 'actual': {'pos': pos, 'home': home, 'end': end}})
            page.keyboard.press('Escape')
        else:
            results.append({'id': 'R10-viewer', 'status': 'FAIL', 'actual': 'no photo cards'})
        # people page
        page.locator('[data-view="people"]').click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / '1280-people.png'))
        merge = page.locator('#merge-selected-people')
        results.append({'id': 'R10-people-merge-type', 'status': 'PASS' if merge.get_attribute('type') == 'button' else 'FAIL', 'actual': merge.get_attribute('type')})
        # objects hidden
        hidden = page.locator('[data-view="objects"]').get_attribute('hidden')
        results.append({'id': 'R10-objects-hidden', 'status': 'PASS' if hidden is not None else 'FAIL', 'actual': hidden})
        # responsive
        for width, name in [(768, '768-library.png'), (390, '390-library.png')]:
            page.set_viewport_size({'width': width, 'height': 800})
            page.locator('[data-view="timeline"]').click()
            page.wait_for_timeout(400)
            page.screenshot(path=str(SHOTS / name), full_page=True)
        undefined = page.locator('text=undefined')
        results.append({'id': 'R10-no-undefined', 'status': 'PASS' if undefined.count() == 0 else 'FAIL', 'actual': undefined.count()})
        js_errors = [c for c in console if c['type'] in {'error', 'pageerror'}]
        results.append({'id': 'R10-console', 'status': 'PASS' if not js_errors else 'FAIL', 'actual': js_errors[:10]})
        browser.close()
    payload = {'verified_at': now(), 'url': URL, 'results': results, 'screenshots': [str(p) for p in sorted(SHOTS.glob('*.png'))]}
    out = REPORT / 'logs' / 'repair_browser_checks.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(0 if all(r['status'] == 'PASS' for r in results) else 1)


if __name__ == '__main__':
    main()
