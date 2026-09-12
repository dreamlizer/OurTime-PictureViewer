"""Real-browser checks for returning to the last viewed waterfall photo."""
from __future__ import annotations

import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "viewer-return-position-20260911.json"


def visible_card(page, photo_id: int) -> bool:
    return page.evaluate("""id => {
      const card = document.querySelector(`[data-photo="${id}"]`);
      if (!card) return false;
      const rect = card.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < innerHeight;
    }""", photo_id)


def close_and_check(page, photo_id: int) -> bool:
    page.locator('[data-close="detail-dialog"]').click()
    page.wait_for_function("!document.querySelector('#detail-dialog').open")
    page.wait_for_timeout(1000)
    page.wait_for_function("id => document.querySelector(`[data-photo=\"${id}\"]`) !== null", arg=photo_id)
    return visible_card(page, photo_id)


def open_first(page) -> int:
    page.wait_for_selector("#photo-grid [data-photo]", timeout=30000)
    photo_id = page.evaluate("""() => {
      const card = [...document.querySelectorAll('#photo-grid [data-photo]')]
        .find(item => { const r = item.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; });
      return Number(card?.dataset.photo || document.querySelector('#photo-grid [data-photo]')?.dataset.photo || 0);
    }""")
    page.locator(f'#photo-grid [data-photo="{photo_id}"]').dispatch_event("click")
    page.wait_for_selector("#detail-dialog[open]", timeout=15000)
    return photo_id


def flip(page, count: int) -> int:
    for _ in range(count):
        page.wait_for_function("!document.querySelector('#detail-dialog').classList.contains('is-loading')")
        before = page.locator("#viewer-position").inner_text()
        page.locator("#viewer-next").click()
        page.wait_for_function("old => document.querySelector('#viewer-position').textContent !== old", arg=before)
        page.wait_for_function("!document.querySelector('#detail-dialog').classList.contains('is-loading')")
    return int(page.evaluate("window.__ourTimeApp.state.detail.id"))


def context_close(page, label: str) -> dict:
    photo_id = open_first(page)
    visible = close_and_check(page, photo_id)
    return {"context": label, "photo_id": photo_id, "card_visible": visible}


def main() -> int:
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)
        page.wait_for_selector("#photo-grid [data-photo]", timeout=30000)

        page.wait_for_function("document.querySelector('#photo-grid').getBoundingClientRect().height > 2000", timeout=15000)
        page.evaluate("scrollTo(0, 979)")
        page.wait_for_function("scrollY >= 900")
        first = open_first(page)
        initial_scroll = page.evaluate("scrollY")
        no_flip_visible = close_and_check(page, first)
        after_no_flip = page.evaluate("scrollY")
        results.append({"case": "no_flip", "same_scroll": abs(after_no_flip - initial_scroll) <= 2,
                        "card_visible": no_flip_visible, "initial_scroll": initial_scroll,
                        "after_scroll": after_no_flip})

        open_first(page)
        five_last = flip(page, 5)
        results.append({"case": "flip_5", "last_photo_id": five_last,
                        "card_visible": close_and_check(page, five_last)})

        open_first(page)
        thirty_last = flip(page, 30)
        results.append({"case": "flip_30_cross_page", "last_photo_id": thirty_last,
                        "card_visible": close_and_check(page, thirty_last)})

        open_first(page)
        fifty_last = flip(page, 50)
        results.append({"case": "flip_50_cross_two_pages", "last_photo_id": fifty_last,
                        "card_visible": close_and_check(page, fifty_last)})

        first_item = page.request.get(URL + "/api/photos?filter=timeline&sort=date_desc&limit=1&offset=0").json()["items"][0]
        query = Path(first_item["path"]).stem
        page.evaluate("query => { window.__ourTimeApp.state.q = query; return window.__ourTimeApp.setView('timeline'); }", query)
        page.wait_for_function("q => window.__ourTimeApp.state.q === q", arg=query, timeout=15000)
        page.wait_for_selector("#photo-grid [data-photo]", timeout=30000)
        results.append(context_close(page, "q_search"))

        page.evaluate("() => { window.__ourTimeApp.state.q = ''; return window.__ourTimeApp.setView('timeline'); }")
        page.wait_for_function("window.__ourTimeApp.state.q === ''", timeout=15000)
        year = str(first_item.get("effective_date") or "")[:4]
        if year.isdigit():
            page.evaluate("year => window.__ourTimeApp.setView('year:' + year)", year)
            page.wait_for_selector("#photo-grid [data-photo]", timeout=30000)
            results.append(context_close(page, "year"))

        page.evaluate("window.__ourTimeApp.setView('groups')")
        page.wait_for_selector("#groups-view:not([hidden])", timeout=15000)
        if page.locator("#groups-list [data-group]:not([disabled])").count():
            page.locator("#groups-list [data-group]:not([disabled])").first.click()
            page.wait_for_selector("#photo-grid [data-photo]", timeout=30000)
            results.append(context_close(page, "group"))

        browser.close()
    passed = not errors and all(item.get("card_visible", True) and item.get("same_scroll", True) for item in results)
    report = {"status": "PASS" if passed else "FAIL", "results": results, "page_errors": errors}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
