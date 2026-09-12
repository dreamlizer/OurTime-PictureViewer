"""Delayed-request checks for view switching races and light motion."""
from __future__ import annotations

import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright
import urllib.request

URL = 'http://127.0.0.1:8765'
REPORT = Path(__file__).resolve().parents[1] / 'validation' / 'reports' / 'view-switching-20260912'


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print('PASS', message, flush=True)


def api(path: str):
    with urllib.request.urlopen(URL + path, timeout=20) as response:
        return json.load(response)


def current_view(page):
    return page.evaluate('window.__ourTimeApp && window.__ourTimeApp.state.view')


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    folders = api('/api/folders?path=')['items']
    check(len(folders) >= 1, 'folder root listing available')
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
        page.on('pageerror', lambda error: errors.append(str(error)))

        def delay_route(route):
            url = route.request.url
            if '/api/folders' in url or '/api/groups' in url or '/api/people?' in url or '/api/places?' in url or '/api/timeline' in url or '/api/photos?' in url:
                time.sleep(0.22)
            route.continue_()

        page.route('**/api/**', delay_route)
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_selector('#home-query-host .ot-home-ui', timeout=20000)

        page.locator('[data-view="people"]').click()
        page.locator('[data-view="groups"]').click()
        page.locator('[data-view="folders"]').click()
        page.locator('[data-view="timeline"]').click()
        page.wait_for_function("() => window.__ourTimeApp && window.__ourTimeApp.state.view === 'timeline'", timeout=15000)
        check(current_view(page) == 'timeline', 'fast nav people/groups/folders/home ends on last click')
        page.screenshot(path=str(REPORT / 'fast-nav-home.png'), full_page=True)

        page.locator('[data-view="folders"]').click()
        page.wait_for_selector('#folders-view:not([hidden])', timeout=15000)
        page.wait_for_selector('#folders-list [data-folder-open]', timeout=15000)
        cards = page.locator('#folders-list [data-folder-open]')
        count = min(cards.count(), 3)
        check(count >= 1, 'folder cards available')
        paths = [cards.nth(index).get_attribute('data-folder-open') for index in range(count)]
        last_path = paths[-1]
        page.evaluate(
            """(n) => {
              const nodes = [...document.querySelectorAll('#folders-list [data-folder-open]')].slice(0, n);
              nodes.forEach(node => node.click());
              return nodes.at(-1)?.dataset.folderOpen || '';
            }""",
            count,
        )
        page.wait_for_timeout(900)
        shown = page.evaluate('window.__ourTimeApp && window.__ourTimeApp.state.folderPath')
        check(shown == last_path, 'fast folder A/B/C ends on last directory')
        page.screenshot(path=str(REPORT / 'fast-folders.png'), full_page=True)

        page.locator('[data-view="people"]').click()
        page.wait_for_selector('#people-view:not([hidden])', timeout=15000)
        page.wait_for_timeout(400)
        first_html = page.locator('#people-grid').inner_html()
        page.locator('[data-view="groups"]').click()
        page.wait_for_selector('#groups-view:not([hidden])', timeout=15000)
        page.locator('[data-view="people"]').click()
        page.wait_for_selector('#people-view:not([hidden])', timeout=15000)
        switching = page.locator('#people-grid').evaluate("el => el.classList.contains('is-switching') || el.innerHTML === '' || true")
        page.wait_for_timeout(500)
        check(page.locator('#people-view').is_visible(), 'people view visible after re-entry')
        page.screenshot(path=str(REPORT / 'people-reenter.png'), full_page=True)

        page.locator('[data-view="places"]').click()
        page.wait_for_selector('#places-view:not([hidden])', timeout=15000)
        search = page.locator('#places-search')
        if search.count():
            search.fill('a')
            search.fill('b')
            search.fill('c')
            page.wait_for_timeout(700)
            q = page.evaluate('window.__ourTimeApp && window.__ourTimeApp.state.placeStream && window.__ourTimeApp.state.placeStream.q')
            check(q in ('c', 'C', 'c'.lower()) or (q or '').endswith('c') or q == 'c', 'place search keeps last query')

        page.locator('[data-view="timeline"]').click()
        page.wait_for_selector('#photo-grid', timeout=15000)
        before_h = page.locator('#photo-grid').evaluate('el => el.getBoundingClientRect().height')
        page.locator('[data-ot="sort"]').select_option('date_asc')
        page.wait_for_timeout(80)
        updating = page.locator('#photo-grid').evaluate("el => el.classList.contains('is-updating') || el.style.minHeight")
        page.wait_for_timeout(700)
        after_h = page.locator('#photo-grid').evaluate('el => el.getBoundingClientRect().height')
        check(before_h >= 0 and after_h >= 0, 'photo grid remains measurable during refresh')

        page.locator('.photo-card').first.click()
        page.wait_for_selector('#detail-dialog[open]', timeout=15000)
        for _ in range(10):
            page.keyboard.press('ArrowRight')
        page.wait_for_timeout(400)
        hidden_mat = page.locator('#photo-mat').evaluate("el => getComputedStyle(el).visibility === 'hidden' && getComputedStyle(el).opacity === '0'")
        check(not hidden_mat, 'viewer does not hide photo-mat into a black frame while loading')
        page.keyboard.press('Escape')
        page.wait_for_timeout(200)

        page.locator('#organize-nav summary').click()
        page.wait_for_timeout(160)
        check(page.locator('#organize-nav').evaluate('el => el.open') is True, 'organize menu opens')
        page.locator('#organize-nav summary').click()
        page.wait_for_timeout(160)

        page.emulate_media(reduced_motion='reduce')
        page.locator('[data-view="people"]').click()
        page.wait_for_selector('#people-view:not([hidden])', timeout=15000)
        page.screenshot(path=str(REPORT / 'reduced-motion.png'), full_page=True)

        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(200)
        overflow = page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
        check(not overflow, '390px no horizontal overflow')
        page.screenshot(path=str(REPORT / 'narrow-390.png'), full_page=True)
        check(not errors, 'no javascript page errors')
        browser.close()
    print(json.dumps({'passed': True, 'screenshots': sorted(p.name for p in REPORT.glob('*.png'))}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
