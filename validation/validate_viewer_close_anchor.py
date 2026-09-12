"""Read-only live check that the photo close button follows the displayed frame."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "viewer-close-anchor-live.json"
LANDSCAPE = ROOT / "validation" / "reports" / "viewer-close-anchor-landscape.png"
PORTRAIT = ROOT / "validation" / "reports" / "viewer-close-anchor-portrait.png"


def request_json(path: str):
    with urllib.request.urlopen(URL + path, timeout=30) as response:
        return json.load(response)


def check(value, message: str, checks: list[str]) -> None:
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def main() -> int:
    checks: list[str] = []
    before = request_json("/api/status")
    photos = request_json("/api/photos?filter=timeline&sort=date_desc&limit=100")["items"]
    landscape = next(item for item in photos if (item.get("width") or 0) > (item.get("height") or 0) * 1.15)
    portrait = next(item for item in photos if (item.get("height") or 0) > (item.get("width") or 0) * 1.15)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, locale="zh-CN")
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#library-view.is-ready", timeout=30000)

        def inspect(photo_id: int, screenshot: Path, label: str):
            page.evaluate("(id)=>openPhoto(id)", photo_id)
            page.wait_for_function(
                "(id)=>document.querySelector('#detail-dialog').open && state.detail?.id===id && document.querySelector('#detail-img').naturalWidth>0",
                arg=photo_id,
                timeout=30000,
            )
            page.wait_for_timeout(120)
            geometry = page.evaluate(
                """() => {
                    const image=document.querySelector('#detail-img').getBoundingClientRect();
                    const close=document.querySelector('.viewer-photo-close').getBoundingClientRect();
                    const position=document.querySelector('#viewer-position').getBoundingClientRect();
                    const style=getComputedStyle(document.querySelector('.viewer-photo-close'));
                    return {
                        image:{left:image.left,top:image.top,right:image.right,bottom:image.bottom},
                        close:{left:close.left,top:close.top,right:close.right,bottom:close.bottom},
                        position:{left:position.left,top:position.top,right:position.right,bottom:position.bottom},
                        opacity:Number(style.opacity),
                        background:style.backgroundColor,
                        width:close.width,
                        height:close.height
                    };
                }"""
            )
            check(abs(geometry["close"]["right"] - geometry["image"]["right"]) <= 12, f"{label}关闭按钮贴着照片右边缘", checks)
            check(abs(geometry["close"]["top"] - geometry["image"]["top"]) <= 12, f"{label}关闭按钮贴着照片上边缘", checks)
            check(
                28 <= geometry["width"] <= 32
                and 28 <= geometry["height"] <= 32
                and geometry["opacity"] < 0.9,
                f"{label}关闭按钮尺寸克制、默认显示淡雅",
                checks,
            )
            overlaps = not (
                geometry["close"]["right"] <= geometry["position"]["left"]
                or geometry["close"]["left"] >= geometry["position"]["right"]
                or geometry["close"]["bottom"] <= geometry["position"]["top"]
                or geometry["close"]["top"] >= geometry["position"]["bottom"]
            )
            check(not overlaps, f"{label}关闭按钮不遮挡照片序号", checks)
            page.screenshot(path=str(screenshot), full_page=False)
            page.locator(".viewer-photo-close").click()
            page.wait_for_function("!document.querySelector('#detail-dialog').open")
            return geometry

        landscape_geometry = inspect(landscape["id"], LANDSCAPE, "横图")
        portrait_geometry = inspect(portrait["id"], PORTRAIT, "竖图")
        check(not errors, "横图和竖图查看过程没有 JavaScript 错误", checks)
        browser.close()

    after = request_json("/api/status")
    check(before["capabilities"]["pid"] == after["capabilities"]["pid"], "只读复核没有打断正式后台", checks)
    REPORT.write_text(
        json.dumps(
            {
                "passed": True,
                "checks": checks,
                "landscape_id": landscape["id"],
                "portrait_id": portrait["id"],
                "landscape_geometry": landscape_geometry,
                "portrait_geometry": portrait_geometry,
                "screenshots": [str(LANDSCAPE), str(PORTRAIT)],
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
