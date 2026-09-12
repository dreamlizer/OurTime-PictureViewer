"""Read-only live checks for group-result-count lifecycle."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright
import urllib.request

URL = 'http://127.0.0.1:8765'
REPORT = Path(__file__).resolve().parents[1] / 'validation' / 'reports' / 'group-result-count-20260912'


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print('PASS', message, flush=True)


def api(path: str):
    with urllib.request.urlopen(URL + path, timeout=20) as response:
        return json.load(response)


def count_state(page):
    return page.evaluate("""() => {
      const el = document.querySelector('#group-result-count');
      const back = document.querySelector('#groups-back');
      return {
        view: window.__ourTimeApp && window.__ourTimeApp.state.view,
        title: document.querySelector('#page-title') && document.querySelector('#page-title').textContent,
        text: el ? (el.textContent || '').trim() : '',
        hidden: el ? el.hidden : true,
        backHidden: back ? back.hidden : true,
      };
    }""")


def wait_count(page):
    page.wait_for_function("""() => {
      const el = document.querySelector('#group-result-count');
      return el && !el.hidden && /\d/.test(el.textContent || '');
    }""", timeout=20000)
    return count_state(page)


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    people = api('/api/people?named=1&limit=8')['items']
    check(len(people) >= 1, 'has named people for group filter')
    person = people[0]
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(URL, wait_until='domcontentloaded')
        page.wait_for_selector('#home-query-host .ot-home-ui', timeout=20000)

        page.locator('[data-view="groups"]').click()
        page.wait_for_selector('#groups-view:not([hidden])', timeout=15000)
        overview = count_state(page)
        check(overview['hidden'] and overview['text'] == '' and overview['backHidden'], 'group overview hides detail count and back button')

        page.locator('[data-group="5"]').click()
        page.wait_for_selector('#home-query-host .ot-home-ui', timeout=15000)
        check(page.locator('#page-title').inner_text() == '5人合影', 'group:5 title is 5人合影')
        before = wait_count(page)
        check(not before['hidden'] and any(ch.isdigit() for ch in before['text']) and not before['backHidden'], 'group:5 shows count after load')
        original = before['text']
        page.screenshot(path=str(REPORT / 'group-5.png'), full_page=True)

        page.locator('[data-ot="person"]').click()
        page.wait_for_selector('.ot-home-popover:not([hidden])', timeout=8000)
        page.locator('.ot-home-popover input[type="checkbox"]').first.check()
        page.wait_for_function(
            """old => {
              const el = document.querySelector('#group-result-count');
              return el && (el.hidden || !(el.textContent || '').trim() || (el.textContent || '').trim() !== old);
            }""",
            arg=original,
            timeout=8000,
        )
        mid = count_state(page)
        check(mid['hidden'] or mid['text'] == '' or mid['text'] != original, 'refilter hides old group count before new total')
        after = wait_count(page)
        check(not after['hidden'] and any(ch.isdigit() for ch in after['text']), 'refilter shows new group count')

        page.locator('[data-view="people"]').click()
        page.wait_for_selector('#people-view:not([hidden])', timeout=15000)
        people_state = count_state(page)
        check(people_state['title'] == '人物档案' and people_state['hidden'] and people_state['text'] == '', 'leaving group to people hides count immediately')

        page.locator('[data-view="groups"]').click()
        page.wait_for_selector('#groups-view:not([hidden])', timeout=15000)
        page.locator('[data-group="5"]').click()
        wait_count(page)
        page.locator('#groups-back').click()
        page.wait_for_selector('#groups-view:not([hidden])', timeout=15000)
        back_state = count_state(page)
        check(back_state['hidden'] and back_state['text'] == '' and back_state['backHidden'], 'returning to group overview hides count immediately')

        page.locator('[data-group="3"]').click()
        wait_count(page)
        page.locator('[data-view="groups"]').click()
        page.wait_for_selector('#groups-view:not([hidden])', timeout=15000)
        page.locator('[data-group="5"]').click()
        five = wait_count(page)
        page.locator('[data-view="groups"]').click()
        page.wait_for_selector('#groups-view:not([hidden])', timeout=15000)
        ten = page.locator('[data-group="10plus"]')
        if ten.count():
            ten.click()
            wait_count(page)
        page.locator('[data-view="people"]').click()
        page.wait_for_selector('#people-view:not([hidden])', timeout=15000)
        final_people = count_state(page)
        check(final_people['hidden'] and final_people['text'] == '', 'group:3/5/10plus then people leaves no leftover count')

        page.locator('[data-view="timeline"]').click()
        page.wait_for_selector('#home-query-host .ot-home-ui', timeout=15000)
        home = count_state(page)
        check(home['hidden'] and home['text'] == '' and page.locator('#stats').is_visible(), 'home hides group count and keeps stats')

        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('[data-view="groups"]').click()
        page.wait_for_selector('#groups-view:not([hidden])', timeout=15000)
        page.locator('[data-group="5"]').click()
        wait_count(page)
        overflow = page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
        check(not overflow, '390px group detail has no horizontal overflow')
        page.screenshot(path=str(REPORT / 'group-5-narrow.png'), full_page=True)
        check(not errors, 'no javascript page errors')
        browser.close()
    print(json.dumps({'passed': True, 'person': person.get('name'), 'original': original, 'filtered': after.get('text')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
