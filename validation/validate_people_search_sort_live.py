"""Read-only live-browser validation for people search and sort controls."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "people-search-sort-live.json"
SCREENSHOT = ROOT / "validation" / "reports" / "people-search-sort-live.png"


def request_json(path: str):
    with urllib.request.urlopen(URL + path, timeout=30) as response:
        return json.load(response)


def people(query: str = "", sort: str = "photos"):
    params = {"ignored": 0, "named": 1, "q": query, "sort": sort, "offset": 0, "limit": 48}
    return request_json("/api/people?" + urllib.parse.urlencode(params))


def check(value, message: str, checks: list[str]) -> None:
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def card_ids(page):
    return page.locator("#people-grid .person-card").evaluate_all(
        "cards => cards.map(card => Number(card.dataset.person))"
    )


def main() -> int:
    checks: list[str] = []
    before = request_json("/api/status")
    expected_search = people("郑")
    expected_asc = people(sort="name_asc")
    expected_desc = people(sort="name_desc")
    expected_photos = people(sort="photos")
    check(expected_search["total"] > 0, "正式库存在可用于单字搜索的已命名人物", checks)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, locale="zh-CN")
        errors: list[str] = []
        search_requests: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: search_requests.append(request.url)
            if "/api/people?" in request.url and "q=" in request.url
            else None,
        )
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#library-view.is-ready", timeout=30000)
        page.locator('[data-view="people"]').click()
        page.wait_for_function(
            "state.view==='people' && !state.peopleStream.people.loading && document.querySelectorAll('#people-grid .person-card').length>0",
            timeout=30000,
        )

        search = page.locator("#people-search")
        check(search.bounding_box()["width"] <= 190, "桌面端姓名搜索框已缩短到约十个汉字宽度", checks)
        check(
            page.locator("#people-sort-photos").is_visible()
            and page.locator("#people-sort-name").is_visible()
            and page.locator("#people-merge-toggle").is_visible(),
            "照片数量、姓名排序和合并人物三个按钮同时可见",
            checks,
        )

        search.fill("徐")
        page.wait_for_timeout(300)
        search.fill("郑")
        page.wait_for_function(
            "state.peopleStream.people.q==='郑' && !state.peopleStream.people.loading",
            timeout=30000,
        )
        check(any("q=%E5%BE%90" in url for url in search_requests), "第一笔单字搜索请求确实已经发出", checks)
        check(any("q=%E9%83%91" in url for url in search_requests), "后输入的单字搜索没有被前一笔请求吞掉", checks)
        check(
            card_ids(page) == [item["id"] for item in expected_search["items"]],
            "单字搜索只显示姓名或别名匹配的已命名人物",
            checks,
        )

        search.fill("")
        page.wait_for_function(
            "state.peopleStream.people.q==='' && !state.peopleStream.people.loading",
            timeout=30000,
        )
        page.locator("#people-sort-name").click()
        page.wait_for_function(
            "state.peopleSort==='name_asc' && !state.peopleStream.people.loading",
            timeout=30000,
        )
        check(page.locator("#people-sort-name").inner_text() == "姓名 A→Z", "第一次点击姓名按钮切换为 A→Z", checks)
        check(card_ids(page) == [item["id"] for item in expected_asc["items"]], "A→Z 页面顺序与拼音排序接口一致", checks)

        page.locator("#people-sort-name").click()
        page.wait_for_function(
            "state.peopleSort==='name_desc' && !state.peopleStream.people.loading",
            timeout=30000,
        )
        check(page.locator("#people-sort-name").inner_text() == "姓名 Z→A", "再次点击姓名按钮切换为 Z→A", checks)
        check(card_ids(page) == [item["id"] for item in expected_desc["items"]], "Z→A 页面顺序与拼音倒序接口一致", checks)

        page.locator("#people-sort-photos").click()
        page.wait_for_function(
            "state.peopleSort==='photos' && !state.peopleStream.people.loading",
            timeout=30000,
        )
        check(page.locator("#people-sort-photos").get_attribute("aria-pressed") == "true", "照片数量按钮可恢复默认排序", checks)
        check(card_ids(page) == [item["id"] for item in expected_photos["items"]], "默认排序仍按已命名人物照片数量", checks)
        page.screenshot(path=str(SCREENSHOT), full_page=False)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(150)
        check(
            page.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth"),
            "390px 窄屏下搜索和排序按钮不会造成横向溢出",
            checks,
        )
        check(not errors, "人物搜索与排序过程没有 JavaScript 错误", checks)
        browser.close()

    after = request_json("/api/status")
    check(before["capabilities"]["pid"] == after["capabilities"]["pid"], "只读复核没有打断正式后台", checks)
    REPORT.write_text(
        json.dumps(
            {
                "passed": True,
                "checks": checks,
                "search_total": expected_search["total"],
                "screenshot": str(SCREENSHOT),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"passed": True, "checks": len(checks)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
