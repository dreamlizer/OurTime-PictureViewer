"""Read-only browser checks for the fixed header stack on primary browse views."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import Page, sync_playwright


URL = "http://127.0.0.1:8765"
REPORT = Path(__file__).resolve().parent / "reports" / "sticky-browse-headers-20260912"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def check_sticky(page: Page, label: str, chrome_selector: str) -> None:
    page.evaluate(
        """selector => {
            document.querySelector('main').style.minHeight = '2400px';
            document.querySelector(selector).parentElement.style.minHeight = '2200px';
            window.scrollTo(0, 900);
        }""",
        chrome_selector,
    )
    page.wait_for_timeout(180)
    geometry = page.evaluate(
        """selector => {
            const rect = value => {
                const box = value.getBoundingClientRect();
                return {top: box.top, right: box.right, bottom: box.bottom, left: box.left, width: box.width, height: box.height};
            };
            return {
                topbar: rect(document.querySelector('.topbar')),
                masthead: rect(document.querySelector('.masthead')),
                chrome: rect(document.querySelector(selector)),
                viewport: {width: innerWidth, height: innerHeight},
                sticky: document.body.classList.contains('is-sticky-browser-view')
            };
        }""",
        chrome_selector,
    )
    print(json.dumps({"view": label, "geometry": geometry}, ensure_ascii=False), flush=True)
    check(geometry["sticky"], f"{label} 启用统一顶部固定状态")
    check(abs(geometry["topbar"]["top"]) <= 1, f"{label} 顶栏固定在窗口顶部")
    check(
        abs(geometry["masthead"]["top"] - geometry["topbar"]["bottom"]) <= 2,
        f"{label} 大标题紧接顶栏且不重叠",
    )
    check(
        abs(geometry["chrome"]["top"] - geometry["masthead"]["bottom"]) <= 2,
        f"{label} 页面工具区紧接大标题且不重叠",
    )
    check(geometry["chrome"]["bottom"] < geometry["viewport"]["height"], f"{label} 固定工具区没有占满窗口")
    safe_name = (
        label.replace("：", "-")
        .replace("人", "person-")
        .replace(" ", "-")
        .replace("/", "-")
    )
    page.screenshot(path=str(REPORT / f"{safe_name}.png"))
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(80)


def content_gap(page: Page, controls: str, content: str) -> float:
    return page.evaluate(
        """selectors => {
            const controls = document.querySelector(selectors.controls).getBoundingClientRect();
            const content = document.querySelector(selectors.content).getBoundingClientRect();
            return content.top - controls.bottom;
        }""",
        {"controls": controls, "content": content},
    )


def main() -> int:
    with urlopen(URL + "/api/status", timeout=20) as response:
        status = json.load(response)
    data_dir = str((status.get("capabilities") or {}).get("data_dir") or "")
    check(data_dir.replace("/", "\\").lower().endswith("\\data"), "8765 指向正式 data；本测试只读")

    REPORT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL + "/?sticky-header-test=1", wait_until="domcontentloaded")
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=20000)
        page.wait_for_function(
            "document.body.classList.contains('is-sticky-browser-view')",
            timeout=30000,
        )

        check_sticky(page, "全部照片", "#library-view > .view-chrome")

        page.locator('[data-view="folders"]').click()
        page.wait_for_selector("#folders-view:not([hidden])", timeout=15000)
        check_sticky(page, "文件夹", "#folders-view > .view-chrome")

        page.locator('[data-view="places"]').click()
        page.wait_for_selector("#places-view:not([hidden])", timeout=15000)
        check_sticky(page, "按地点", "#places-view > .view-chrome")
        place = page.locator("#places-list [data-place]").first
        if place.count():
            place.click()
            page.wait_for_selector("#library-view:not([hidden])", timeout=15000)
            check_sticky(page, "地点详情", "#library-view > .view-chrome")

        page.locator('[data-view="groups"]').click()
        page.wait_for_selector("#groups-view:not([hidden]) [data-group]", timeout=15000)
        check_sticky(page, "合影总览", "#groups-view > .view-chrome")
        page.locator("#groups-list [data-group]").first.click()
        page.wait_for_selector("#library-view:not([hidden]) #home-query-host .ot-home-ui", timeout=15000)
        page.wait_for_selector("#photo-grid [data-photo]", timeout=30000)
        check_sticky(page, "合影详情", "#library-view > .view-chrome")
        group_gap = content_gap(page, "#home-query-host .ot-home-query", "#photo-grid")

        page.locator('[data-view="people"]').click()
        page.wait_for_selector("#people-grid .person-card", timeout=30000)
        people_gap = content_gap(page, "#people-view .toolbar", "#people-grid")
        check(abs(group_gap - people_gap) <= 4, "合影详情与人物档案的标题栏下间距一致")

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('[data-view="timeline"]').click()
        page.wait_for_selector("#library-view:not([hidden]) #home-query-host .ot-home-ui", timeout=15000)
        check_sticky(page, "390px 全部照片", "#library-view > .view-chrome")
        check(
            not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
            "390px 全部照片没有横向溢出",
        )

        check(not errors, "各页面固定标题检查没有 JavaScript 错误")
        browser.close()

    print(json.dumps({"passed": True, "screenshots": sorted(path.name for path in REPORT.glob("*.png"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
