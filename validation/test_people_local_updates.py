"""Read-only browser checks for local people-page mutations.

All mutating requests are intercepted, so the production photo database is not changed.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from playwright.sync_api import Route, sync_playwright


URL = "http://127.0.0.1:8765"
REPORT = Path(__file__).resolve().parent / "reports" / "people-local-updates-20260912"
FAKE_PERSON_ID = 999_999_991


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def main() -> int:
    with urlopen(URL + "/api/status", timeout=20) as response:
        status = json.load(response)
    data_dir = str((status.get("capabilities") or {}).get("data_dir") or "")
    check(data_dir.replace("/", "\\").lower().endswith("\\data"), "8765 指向正式 data；写请求将由浏览器拦截")

    REPORT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    list_requests: list[str] = []
    fake_cover = {"id": 1}

    def route_api(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        query = parse_qs(parsed.query)
        if request.method == "POST" and parsed.path.endswith("/ignore"):
            route.fulfill(status=200, content_type="application/json", body='{"ok":true,"ignored":true}')
            return
        if request.method == "POST" and parsed.path.endswith("/split"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"person_id": FAKE_PERSON_ID}),
            )
            return
        if parsed.path == "/api/people" and query.get("ids") == [str(FAKE_PERSON_ID)]:
            item = {
                "id": FAKE_PERSON_ID,
                "name": "",
                "alias": "",
                "confirmed": 0,
                "ignored": 0,
                "cover": fake_cover["id"],
                "photo_count": 1,
                "face_count": 1,
            }
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"items": [item], "total": 1}),
            )
            return
        route.continue_()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", route_api)

        def record_request(request) -> None:
            parsed = urlparse(request.url)
            if parsed.path != "/api/people":
                return
            query = parse_qs(parsed.query)
            if "ids" not in query:
                list_requests.append(request.url)

        page.on("request", record_request)

        def open_people() -> None:
            page.goto(URL + "/?people-local-test=1", wait_until="domcontentloaded")
            page.locator('[data-view="people"]').click()
            page.wait_for_selector("#people-view:not([hidden]) #people-grid .person-card", timeout=20000)
            page.wait_for_timeout(250)

        open_people()
        initial_ids = page.locator("#people-grid .person-card").evaluate_all(
            "cards => cards.map(card => Number(card.dataset.person))"
        )
        check(len(initial_ids) >= 3, "人物页有足够卡片执行局部更新检查")
        page.evaluate(
            """() => {
                window.__peopleNodeMap = new Map(
                    [...document.querySelectorAll('#people-grid .person-card')]
                        .map(card => [Number(card.dataset.person), card])
                );
            }"""
        )
        before_requests = len(list_requests)
        page.locator("#people-merge-toggle").click()
        page.wait_for_selector("#people-grid .person-check")
        page.locator("#clear-people-selection").click()
        check(page.locator("#people-grid .person-check").count() == 0, "退出合并模式后 checkbox 消失")
        check(
            page.evaluate(
                """ids => ids.every(id =>
                    window.__peopleNodeMap.get(id) ===
                    document.querySelector(`#people-grid [data-person="${id}"]`)
                )""",
                initial_ids,
            ),
            "进入和退出合并模式不重建人物卡片",
        )
        check(len(list_requests) == before_requests, "合并模式切换不重新请求人物列表")

        page.reload(wait_until="domcontentloaded")
        page.locator('[data-view="people"]').click()
        page.wait_for_selector("#people-grid .person-card", timeout=20000)
        page.wait_for_timeout(250)
        split_ids = page.locator("#people-grid .person-card").evaluate_all(
            "cards => cards.map(card => Number(card.dataset.person))"
        )
        page.evaluate(
            """() => {
                window.__peopleNodeMap = new Map(
                    [...document.querySelectorAll('#people-grid .person-card')]
                        .map(card => [Number(card.dataset.person), card])
                );
            }"""
        )
        source_id = split_ids[0]
        page.locator(f'#people-grid [data-person="{source_id}"] .person-open').click()
        page.wait_for_selector("#person-dialog[open] [data-split]", timeout=15000)
        fake_cover["id"] = int(page.locator("#person-dialog [data-split]").first.get_attribute("data-split"))
        before_face_count = page.locator("#person-dialog [data-face-id]").count()
        before_requests = len(list_requests)
        page.locator("#person-dialog [data-split]").first.click()
        page.wait_for_selector(f'#people-grid [data-person="{FAKE_PERSON_ID}"]', timeout=15000)
        after_split_ids = page.locator("#people-grid .person-card").evaluate_all(
            "cards => cards.map(card => Number(card.dataset.person))"
        )
        check(after_split_ids == split_ids + [FAKE_PERSON_ID], "移出的人物分组只追加到列表末尾")
        check(
            page.evaluate(
                """ids => ids.slice(1).every(id =>
                    window.__peopleNodeMap.get(id) ===
                    document.querySelector(`#people-grid [data-person="${id}"]`)
                )""",
                split_ids,
            ),
            "移出单张脸不会重建或重排其他人物卡片",
        )
        check(page.locator("#person-dialog").evaluate("dialog => dialog.open"), "移出后人物详情保持打开")
        check(page.locator("#person-dialog [data-face-id]").count() == before_face_count - 1, "详情中只移除当前人脸")
        check(len(list_requests) == before_requests, "移出后不重新请求整个人物列表")
        page.screenshot(path=str(REPORT / "split-appended.png"), full_page=True)

        page.reload(wait_until="domcontentloaded")
        page.locator('[data-view="people"]').click()
        page.wait_for_selector("#people-grid .person-card", timeout=20000)
        page.wait_for_timeout(250)
        ignore_ids = page.locator("#people-grid .person-card").evaluate_all(
            "cards => cards.map(card => Number(card.dataset.person))"
        )
        ignored_id = ignore_ids[0]
        page.evaluate(
            """() => {
                window.__peopleNodeMap = new Map(
                    [...document.querySelectorAll('#people-grid .person-card')]
                        .map(card => [Number(card.dataset.person), card])
                );
            }"""
        )
        page.locator(f'#people-grid [data-person="{ignored_id}"] .person-open').click()
        page.wait_for_selector("#person-dialog[open]", timeout=15000)
        before_requests = len(list_requests)
        page.locator("#ignore-person").click()
        page.wait_for_function(
            "id => !document.querySelector(`#people-grid [data-person=\"${id}\"]`)",
            arg=ignored_id,
        )
        remaining_ids = page.locator("#people-grid .person-card").evaluate_all(
            "cards => cards.map(card => Number(card.dataset.person))"
        )
        check(remaining_ids == ignore_ids[1:], "标为路人后仅移除当前人物卡片")
        check(
            page.evaluate(
                """ids => ids.every(id =>
                    window.__peopleNodeMap.get(id) ===
                    document.querySelector(`#people-grid [data-person="${id}"]`)
                )""",
                ignore_ids[1:],
            ),
            "标为路人不会重建或重排其余人物卡片",
        )
        check(page.locator("#page-title").inner_text() == "人物档案", "标为路人后仍停留在人物档案")
        check(len(list_requests) == before_requests, "标为路人后不重新请求整个人物列表")
        page.screenshot(path=str(REPORT / "ignore-local.png"), full_page=True)

        check(not errors, "浏览器没有 JavaScript 错误")
        browser.close()

    print(json.dumps({"passed": True, "screenshots": sorted(path.name for path in REPORT.glob("*.png"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
