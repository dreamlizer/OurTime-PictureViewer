"""Read-only live-browser checks for quick open and natural keyboard switching."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "viewer-flow-polish-live.json"


def req(path):
    with urllib.request.urlopen(URL + path, timeout=30) as response:
        return json.load(response)


def check(value, message, checks):
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def main():
    checks = []
    before = req("/api/status")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#photo-grid .photo-card")
        page.wait_for_function("waterfall.pending.size===0")
        card = page.locator("#photo-grid .photo-card").first

        started = time.perf_counter()
        card.click()
        page.wait_for_selector("#detail-dialog[open]")
        dialog_seconds = time.perf_counter() - started
        check(dialog_seconds < 1.0, f"点击后 {dialog_seconds:.3f} 秒内打开大图层，不等待完整浏览序列", checks)
        page.wait_for_function("document.querySelector('#detail-img').naturalWidth>0", timeout=10000)
        first_src = page.locator("#detail-img").get_attribute("src")
        first_position = page.locator("#viewer-position").inner_text()

        def delay_image(route):
            time.sleep(0.35)
            route.continue_()

        page.route("**/api/original/**", delay_image)
        page.route("**/api/preview/**", delay_image)
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(80)
        check(page.locator("#detail-dialog").is_visible(), "方向键加载下一张时大图层保持打开", checks)
        check(page.locator("#detail-img").get_attribute("src") == first_src, "新图准备好前保留上一张，不闪空白", checks)
        check(page.locator("#viewer-loading").is_hidden(), "逐张切换不覆盖灰色加载框", checks)
        check(not page.locator("#photo-signature").is_hidden(), "逐张切换期间照片白边不会残留为空框或被强制误显", checks)
        page.wait_for_function("(src)=>document.querySelector('#detail-img').src!==src", arg=first_src, timeout=10000)
        animation = page.locator("#detail-img").evaluate("(e)=>getComputedStyle(e).animationName")
        check(animation in ("viewer-photo-forward", "viewer-photo-backward"), "新图以带方向的短过渡自然显现", checks)
        page.wait_for_function("(p)=>document.querySelector('#viewer-position').textContent!==p", arg=first_position)

        page.keyboard.press("Escape")
        page.wait_for_timeout(40)
        check(page.locator("#detail-dialog").evaluate("(e)=>e.classList.contains('viewer-closing')"), "关闭时先播放轻量收拢动画", checks)
        page.wait_for_function("!document.querySelector('#detail-dialog').open")
        check(not errors, "真实浏览没有 JavaScript 错误", checks)
        browser.close()

    after = req("/api/status")
    check(
        before["capabilities"]["pid"] == after["capabilities"]["pid"],
        "只读浏览没有重启或打断正式后台",
        checks,
    )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "passed": True,
                "checks": checks,
                "dialog_seconds": round(dialog_seconds, 3),
                "pid": before["capabilities"]["pid"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"passed": True, "checks": len(checks), "dialog_seconds": round(dialog_seconds, 3)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
