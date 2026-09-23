"""Read-only visual check against the running production service.

All non-GET/HEAD browser requests are aborted before reaching port 8765.
This script never starts, stops, or restarts the production process.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Route, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
STAMP = time.strftime("%Y%m%d-%H%M%S")
REPORT = ROOT / "validation" / "reports" / "design-experience-20260922" / f"formal-readonly-{STAMP}"
URL = "http://127.0.0.1:8765"


def check(condition: bool, message: str, checks: list[str]) -> None:
    if not condition:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def main() -> None:
    REPORT.mkdir(parents=True)
    with urllib.request.urlopen(URL + "/api/health", timeout=8) as response:
        health = json.loads(response.read().decode("utf-8"))
    expected_data = str((ROOT / "data").resolve()).casefold()
    actual_data = str(Path(health.get("data_dir") or "").resolve()).casefold()
    if actual_data != expected_data or not health.get("owner"):
        raise RuntimeError(f"8765 不是预期正式服务：{health}")

    checks: list[str] = []
    blocked: list[str] = []
    blocked_expected: list[str] = []
    blocked_unexpected: list[str] = []
    page_errors: list[str] = []
    console_errors: list[str] = []
    console_errors_expected: list[str] = []
    console_errors_unexpected: list[str] = []
    screenshots: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def readonly(route: Route) -> None:
            method = route.request.method.upper()
            if method in {"GET", "HEAD", "OPTIONS"}:
                route.continue_()
            else:
                request_label = f"{method} {route.request.url}"
                blocked.append(request_label)
                if method == "POST" and route.request.url == URL + "/api/ui-ping":
                    blocked_expected.append(request_label)
                else:
                    blocked_unexpected.append(request_label)
                route.abort("blockedbyclient")

        page.route("**/*", readonly)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        def record_console(message) -> None:
            if message.type != "error":
                return
            label = f"{message.type}: {message.text}"
            console_errors.append(label)
            if "ERR_BLOCKED_BY_CLIENT" in message.text:
                console_errors_expected.append(label)
            else:
                console_errors_unexpected.append(label)

        page.on("console", record_console)
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_function(
            "state.view==='home' && !document.querySelector('#home-view').hidden"
            " && document.querySelector('.home-memory, .home-card, .home-hero')"
        )
        home_shot = REPORT / "formal-home-1440.png"
        page.screenshot(path=str(home_shot), full_page=True)
        screenshots.append(str(home_shot))
        check(page.locator(".home-card-more").count() >= 1, "正式首页已加载新的“更多”入口", checks)

        page.locator("[data-view='people']").click()
        page.wait_for_function("state.view==='people' && document.querySelector('.person-card')")
        named = page.locator(".person-card.person-named").first
        if named.count() and named.is_visible():
            named.click()
            page.wait_for_selector("#person-dialog[open]")
            page.wait_for_timeout(300)
            check(
                page.locator("#show-person-photos").is_visible()
                and not page.locator("#person-organize").evaluate("el => el.open"),
                "正式已命名人物首屏突出照片且默认收起整理",
                checks,
            )
            person_shot = REPORT / "formal-person-named-1440.png"
            page.screenshot(path=str(person_shot))
            screenshots.append(str(person_shot))
            page.locator("#person-dialog [data-close='person-dialog']").click()
        else:
            checks.append("SKIP 正式人物列表当前无可见已命名人物卡")

        page.locator("[data-view='timeline']").click()
        page.wait_for_function("state.view==='timeline' && document.querySelector('#photo-grid [data-photo]')")
        page.locator("#photo-grid [data-photo]").first.click()
        page.wait_for_selector("#detail-dialog[open]")
        page.wait_for_function("state.detail && document.querySelector('#detail-img').src")
        page.locator("#face-style-button").click()
        page.wait_for_selector("#face-style-popover:not([hidden])")
        check(
            page.locator("[data-face-theme-choice]").count() == 4
            and page.locator("#reveal-button .viewer-action-label").is_visible(),
            "正式大图显示四主题和“定位原文件”",
            checks,
        )
        viewer_shot = REPORT / "formal-viewer-themes-1440.png"
        page.screenshot(path=str(viewer_shot))
        screenshots.append(str(viewer_shot))
        page.locator("#face-style-close").click()
        page.locator("#detail-dialog [data-close='detail-dialog']").click()

        page.locator("[data-view='home']").click()
        page.wait_for_function("state.view==='home'")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(250)
        check(
            not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"),
            "正式 390px 首页无页面级横向溢出",
            checks,
        )
        narrow_shot = REPORT / "formal-home-390.png"
        page.screenshot(path=str(narrow_shot), full_page=True)
        screenshots.append(str(narrow_shot))
        browser.close()

    check(
        not blocked_unexpected,
        "正式只读浏览未尝试业务写请求：" + "; ".join(blocked_unexpected[:3]),
        checks,
    )
    check(
        bool(blocked_expected),
        "正式页面心跳 POST 已由浏览器层拦截，未到达后端",
        checks,
    )
    check(not page_errors, "正式只读浏览无 pageerror：" + "; ".join(page_errors[:3]), checks)
    check(
        not console_errors_unexpected,
        "正式只读浏览无非预期 console error：" + "; ".join(console_errors_unexpected[:3]),
        checks,
    )
    (REPORT / "summary.json").write_text(
        json.dumps(
            {
                "checks": checks,
                "health": health,
                "blocked_requests": blocked,
                "blocked_expected": blocked_expected,
                "blocked_unexpected": blocked_unexpected,
                "page_errors": page_errors,
                "console_errors": console_errors,
                "console_errors_expected": console_errors_expected,
                "console_errors_unexpected": console_errors_unexpected,
                "screenshots": screenshots,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
