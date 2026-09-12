"""Read-only live visual check for named/unnamed people sections."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "people-sections-live.json"
SCREENSHOT = ROOT / "validation" / "reports" / "people-sections-boundary.png"


def status():
    with urllib.request.urlopen(URL + "/api/status", timeout=30) as response:
        return json.load(response)


def check(value, message, checks):
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def main():
    checks = []
    before = status()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.evaluate("setView('people')")
        page.wait_for_selector("#people-grid .person-card")
        for _ in range(8):
            if page.locator(".people-section-divider").count():
                break
            previous = page.locator("#people-grid .person-card").count()
            page.evaluate("scrollTo(0,document.documentElement.scrollHeight)")
            page.wait_for_function(
                "(n)=>document.querySelectorAll('#people-grid .person-card').length>n || !state.peopleStream.people.more",
                arg=previous,
                timeout=30000,
            )
        check(page.locator(".people-section-divider").count() == 1, "已命名与待命名人物之间只有一个清楚分界", checks)
        divider = page.locator(".people-section-divider")
        divider.scroll_into_view_if_needed()
        page.wait_for_timeout(120)
        check(divider.locator("span").inner_text() == "待命名人物", "分界使用直白的“待命名人物”标题", checks)
        named = page.locator(".person-card.person-named").first
        unnamed = page.locator(".person-card.person-unnamed").first
        named_bg = named.evaluate("(e)=>getComputedStyle(e).backgroundColor")
        unnamed_bg = unnamed.evaluate("(e)=>getComputedStyle(e).backgroundColor")
        check(named_bg != unnamed_bg, "已命名卡片底色更深，待命名区域更浅", checks)
        avatar_size = named.locator("img").evaluate("(e)=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height})")
        top_padding = named.evaluate("(e)=>parseFloat(getComputedStyle(e).paddingTop)")
        check(avatar_size["height"] <= 90 and top_padding <= 24, "人物头像和上下留白已经收紧", checks)
        page.screenshot(path=str(SCREENSHOT), full_page=False)
        check(not errors, "人物档案真实页面没有 JavaScript 错误", checks)
        browser.close()
    after = status()
    check(before["capabilities"]["pid"] == after["capabilities"]["pid"], "只读人物复核没有打断正式后台", checks)
    REPORT.write_text(
        json.dumps(
            {
                "passed": True,
                "checks": checks,
                "named_background": named_bg,
                "unnamed_background": unnamed_bg,
                "avatar": avatar_size,
                "top_padding": top_padding,
                "screenshot": str(SCREENSHOT),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"passed": True, "checks": len(checks), "screenshot": str(SCREENSHOT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
