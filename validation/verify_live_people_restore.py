"""Read-only verification of the active 8765 people/passersby UI."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


URL = "http://127.0.0.1:8765"


def req(path: str):
    with urllib.request.urlopen(URL + path, timeout=30) as response:
        return json.load(response)


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def main() -> int:
    status = req("/api/status")
    data_dir = Path(status["capabilities"]["data_dir"])
    check(data_dir.name == "data", "8765 指向正式 data")
    check(status["job"]["status"] not in {"running", "pausing"}, "正式扫描未运行；验证全程只读")

    regular = req("/api/people?ignored=0&limit=1")
    passersby = req("/api/people?ignored=1&limit=1")
    check(regular["total"] > 0 and passersby["total"] > 0, "正式人物和路人列表均可读取")
    sample = passersby["items"][0]
    detail = req(f"/api/people/{sample['id']}?limit=48")
    check(detail["face_count"] == sample["face_count"], "正式路人卡片与详情使用同一有效人脸数量")
    check(all(isinstance(face.get("asset_id"), int) for face in detail["faces"]), "正式路人详情只返回可打开的数字 asset_id")

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.evaluate("setView('passersby')")
        page.wait_for_selector("#passersby-grid .person-card")
        card = page.locator("#passersby-grid .person-card").first
        restore = card.locator("[data-restore-person]")
        check(restore.is_visible() and restore.inner_text() == "恢复到人物档案", "正式路人卡片显示直接恢复入口")
        card.locator("[data-open-person]").click()
        page.wait_for_selector("#person-dialog[open]")
        check(page.locator("#ignore-person").inner_text() == "恢复到人物档案", "正式路人详情显示整组恢复入口")
        page.locator("[data-face-photo]").first.click()
        page.wait_for_selector("#detail-dialog[open]")
        page.wait_for_function("document.querySelector('#detail-img').naturalWidth>0")
        check(page.locator("#detail-img").is_visible(), "正式路人详情中的照片可打开")
        page.keyboard.press("Escape")
        page.evaluate("() => { const dialog=document.querySelector('#person-dialog'); if(dialog.open)dialog.close(); }")
        for width in (760, 390):
            page.set_viewport_size({"width": width, "height": 844})
            check(restore.is_visible(), f"{width}px 正式路人卡片恢复入口仍可见")
            check(
                not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
                f"{width}px 正式路人页无横向溢出",
            )
        check(not errors, "正式路人只读路径无 JavaScript 错误")
        browser.close()

    print(json.dumps({
        "passed": True,
        "regular_total": regular["total"],
        "passersby_total": passersby["total"],
        "sample_person": sample["id"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
