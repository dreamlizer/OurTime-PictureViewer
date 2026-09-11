"""Current web/, 500 synthetic photos, real 200-window crossing on an isolated server."""
from __future__ import annotations
import hashlib, json, sys
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'validation/reports/code-quality-repair-20260910-r3/runs/c2-browser-500'
SHOTS = ROOT / 'validation/reports/code-quality-repair-20260910-r3/screenshots/c2-500'
URL = 'http://127.0.0.1:8781'
WORK = ROOT / 'validation/work/repair-20260910-r3/browser500'
OUT.mkdir(parents=True, exist_ok=True)
SHOTS.mkdir(parents=True, exist_ok=True)


def parse_pos(text):
    left, right = text.replace(',', '').split('/')
    return int(left.strip()), int(right.strip())


def main():
    results = []
    before = json.loads((WORK / 'original-sha256.json').read_text(encoding='utf-8'))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        console = []
        page.on('console', lambda m: console.append({'type': m.type, 'text': m.text}))
        page.on('pageerror', lambda e: console.append({'type': 'pageerror', 'text': str(e)}))
        status = page.evaluate("async () => (await (await fetch('/api/status')).json())" ) if False else None
        page.goto(URL, wait_until='networkidle', timeout=30000)
        status = page.evaluate("async () => await (await fetch('/api/status')).json()")
        data_dir = (status.get('capabilities') or {}).get('data_dir')
        pid = (status.get('capabilities') or {}).get('pid')
        results.append({'id': 'identity', 'expected': 'isolated browser500', 'actual': {'data_dir': data_dir, 'pid': pid}, 'passed': 'browser500' in str(data_dir) and '照片浏览器\\data' not in str(data_dir).replace('/', '\\')})
        # Independent expected date_desc order: captured 2499-01-01 for id=1 ... so date_desc is id 1 first? 2500-i so id1 captured 2499, id500 captured 2000; date_desc => id1 first.
        oracle = list(range(1, 501))
        seq = page.evaluate("async () => await (await fetch('/api/photos?sequence=true&limit=200&around=199&sort=date_desc')).json()")
        results.append({'id': 'around199-contains-and-window', 'expected': '199 in window, offset not 0 if centered', 'actual': {'offset': seq.get('offset'), 'has199': 199 in (seq.get('ids') or []), 'n': len(seq.get('ids') or []), 'total': seq.get('total')}, 'passed': 199 in (seq.get('ids') or []) and seq.get('total') == 500 and len(seq.get('ids') or []) == 200})
        page.wait_for_function("() => typeof openPhoto === 'function'", timeout=20000)
        page.evaluate("() => openPhoto(199, currentBrowseContext())")
        page.wait_for_selector('#detail-dialog[open]', timeout=20000)
        page.wait_for_function("() => state.detail && state.detail.id===199", timeout=20000)
        # record window requests
        window_calls = []
        page.on('request', lambda req: window_calls.append(req.url) if '/api/photos?' in req.url and 'sequence=true' in req.url else None)
        start_pos = page.locator('#viewer-position').inner_text()
        # step across 199->202
        seen = []
        for _ in range(3):
            page.keyboard.press('ArrowRight')
            page.wait_for_timeout(250)
            pos, total = parse_pos(page.locator('#viewer-position').inner_text())
            photo_id = int(page.locator('#detail-dialog').get_attribute('data-photo-id') or page.evaluate("() => window.state && state.detail && state.detail.id"))
            seen.append({'pos': pos, 'id': photo_id})
        expected_ids = oracle[199:202]  # after opening 199 (1-based pos 199), three rights -> 200,201,202
        # open 199 means 1-based position 199 if oracle starts at id1
        results.append({'id': 'cross-199-202', 'expected': [{'pos': 200, 'id': 200}, {'pos': 201, 'id': 201}, {'pos': 202, 'id': 202}], 'actual': {'start': start_pos, 'seen': seen, 'calls': window_calls[-6:]}, 'passed': [x['id'] for x in seen] == [200, 201, 202] and [x['pos'] for x in seen] == [200, 201, 202]})
        # continue until a new sequence request with changed offset happens: jump near window edge 349 from around199 window 99-298? around199 offset=max(0,198-100)=99, ids 100-299. Need to go to 300.
        # from current 202, press right until 301
        before_calls = len(window_calls)
        current_id = seen[-1]['id']
        while current_id < 301:
            page.keyboard.press('ArrowRight')
            page.wait_for_timeout(80)
            current_id = int(page.evaluate("() => state.detail.id"))
        pos, total = parse_pos(page.locator('#viewer-position').inner_text())
        new_seq = [u for u in window_calls[before_calls:] if 'offset=' in u]
        results.append({'id': 'crossed-200-window-to-301', 'expected': 'id 301 pos 301 and extra sequence request', 'actual': {'id': current_id, 'pos': pos, 'new_seq': new_seq[-3:], 'extra_calls': len(window_calls) - before_calls}, 'passed': current_id == 301 and pos == 301 and (len(window_calls) > before_calls)})
        page.keyboard.press('Home')
        page.wait_for_timeout(400)
        home_pos, home_total = parse_pos(page.locator('#viewer-position').inner_text())
        home_id = int(page.evaluate("() => state.detail.id"))
        page.keyboard.press('End')
        page.wait_for_timeout(400)
        end_pos, end_total = parse_pos(page.locator('#viewer-position').inner_text())
        end_id = int(page.evaluate("() => state.detail.id"))
        results.append({'id': 'home-end', 'expected': {'home': (1, 1), 'end': (500, 500)}, 'actual': {'home': (home_pos, home_id), 'end': (end_pos, end_id), 'total': home_total}, 'passed': home_pos == 1 and home_id == 1 and end_pos == 500 and end_id == 500})
        page.screenshot(path=str(SHOTS / 'end-500.png'))
        page.keyboard.press('Escape')
        # directory filter range: use parent folder of id 1
        # person/filter not seeded; directory all photos folder
        page.locator('#directory-filter').fill(str(WORK / 'photos'))
        page.locator('#apply-directory').click()
        page.wait_for_timeout(800)
        page.locator('[data-photo]').first.click()
        page.wait_for_selector('#detail-dialog[open]')
        page.keyboard.press('End')
        page.wait_for_timeout(400)
        dpos, dtotal = parse_pos(page.locator('#viewer-position').inner_text())
        results.append({'id': 'directory-range-end', 'expected': 'still 500 in this folder', 'actual': {'pos': dpos, 'total': dtotal}, 'passed': dtotal == 500 and dpos == 500})
        errors = [c for c in console if c['type'] in {'error', 'pageerror'}]
        results.append({'id': 'no-js-error', 'expected': [], 'actual': errors[:8], 'passed': not errors})
        objects_hidden = page.locator('[data-view="objects"]').get_attribute('hidden')
        results.append({'id': 'objects-hidden', 'expected': 'hidden', 'actual': objects_hidden, 'passed': objects_hidden is not None})
        browser.close()
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (WORK / 'photos').glob('*.png')}
    results.append({'id': 'sha256-unchanged', 'expected': 500, 'actual': sum(before[k] == after.get(k) for k in before), 'passed': before == after})
    payload = {'verified_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'url': URL, 'results': results}
    (OUT / 'browser500.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
    sys.exit(0 if all(r['passed'] for r in results) else 1)


if __name__ == '__main__':
    main()
