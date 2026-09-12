"""Real-browser checks for the adaptive Viewer metadata caption."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
DB = ROOT / "data" / "library.sqlite3"
REPORT_DIR = ROOT / "validation" / "reports" / "viewer-signature-20260912"
REPORT = REPORT_DIR / "viewer-signature-validation.json"
checks: list[str] = []


def request_json(path: str) -> dict:
    with urllib.request.urlopen(URL + path, timeout=60) as response:
        return json.load(response)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def tag_names(metadata: dict) -> dict[str, object]:
    return metadata.get("ExifTool", {}) if isinstance(metadata, dict) else {}


def has_tag(tags: dict[str, object], name: str) -> bool:
    return any(key.split(":")[-1] == name and str(value).strip() for key, value in tags.items())


def candidates() -> list[dict]:
    rows = []
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as connection:
        query = """
            SELECT a.id, a.width, a.height, a.metadata, a.captured_at, a.place, a.camera, a.format
            FROM assets a
            JOIN files f ON f.asset_id = a.id
            WHERE a.excluded = 0 AND a.error IS NULL AND f.excluded = 0 AND f.exists_now = 1
            GROUP BY a.id
        """
        for row in connection.execute(query):
            try:
                metadata = json.loads(row[3] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            rows.append({
                "id": row[0], "width": row[1] or 0, "height": row[2] or 0,
                "tags": tag_names(metadata), "date": row[4] or "", "place": row[5] or "",
                "camera": row[6] or "", "format": row[7] or "",
            })
    return rows


def open_photo(page: Page, asset_id: int) -> dict:
    detail = request_json(f"/api/photos/{asset_id}")
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_function("typeof openPhoto === 'function'", timeout=15000)
    page.evaluate("async id => { await openPhoto(id); return true; }", asset_id)
    page.wait_for_selector("#detail-dialog[open]", timeout=15000)
    page.wait_for_function(
        "document.querySelector('#detail-img').complete && document.querySelector('#detail-img').naturalWidth > 0",
        timeout=30000,
    )
    page.wait_for_function("!document.querySelector('#detail-dialog').classList.contains('is-loading')")
    return detail


def caption_state(page: Page) -> dict:
    return page.evaluate(
        """() => {
          const dialog = document.querySelector('#detail-dialog');
          const signature = document.querySelector('#photo-signature');
          const mat = document.querySelector('#photo-mat');
          const img = document.querySelector('#detail-img');
          const tokens = [...document.querySelectorAll('.signature-exposure-token,.signature-file-token')];
          return {
            mode: dialog.dataset.signatureMode || '',
            height: signature.getBoundingClientRect().height,
            width: img.getBoundingClientRect().width,
            matWidth: mat.getBoundingClientRect().width,
            text: signature.textContent,
            title: signature.title,
            scrollWidth: signature.scrollWidth,
             clientWidth: signature.clientWidth,
             viewportWidth: innerWidth,
             groups: Object.fromEntries(['signature-seal-v3','signature-memory','signature-capture','signature-file'].map(cls => {
               const node = document.querySelector('.' + cls);
               const rect = node?.getBoundingClientRect();
               return [cls, rect ? {top:rect.top,bottom:rect.bottom,left:rect.left,width:rect.width,height:rect.height} : null];
             })),
             tokens: tokens.map(token => ({
              text: token.textContent,
              scrollWidth: token.scrollWidth,
              clientWidth: token.clientWidth,
              overflow: getComputedStyle(token).textOverflow,
            })),
            faceInsetBottom: getComputedStyle(document.querySelector('#face-name-layer')).bottom,
          };
        }"""
    )


def assert_caption_fits(page: Page, label: str) -> dict:
    state = caption_state(page)
    check(state["width"] > 0 and state["matWidth"] > 0, f"{label} 照片已完成真实渲染")
    check(state["scrollWidth"] <= state["clientWidth"] + 1, f"{label} 题注栏没有横向溢出")
    check(all(token["scrollWidth"] <= token["clientWidth"] + 1 for token in state["tokens"]), f"{label} 所有元数据 token 完整可见")
    check(all(token["overflow"] != "ellipsis" for token in state["tokens"]), f"{label} 元数据 token 未使用省略号")
    return state


def main() -> int:
    rows = candidates()
    capture_names = {"Make", "Model", "FocalLengthIn35mmFormat", "FocalLength", "FNumber", "ExposureTime", "ISO"}
    wide_rows = [
        row for row in rows
        if row["width"] > row["height"] and row["date"] and row["place"]
        and row["camera"] and sum(has_tag(row["tags"], name) for name in capture_names) >= 4
    ]
    medium_rows = [row for row in rows if row["height"] > row["width"] and row["height"] < row["width"] * 1.45]
    sparse_rows = [
        row for row in rows
        if not row["date"] and not row["place"] and not row["camera"]
        and not any(has_tag(row["tags"], name) for name in capture_names)
    ]
    check(bool(wide_rows), "正式库存在完整横向 metadata 候选")
    check(bool(medium_rows), "正式库存在中等宽度竖图候选")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, locale="zh-CN")
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        wide_detail = None
        wide_state = None
        for row in wide_rows[:40]:
            wide_detail = open_photo(page, row["id"])
            wide_state = caption_state(page)
            if wide_state["mode"] == "wide":
                break
        check(wide_state is not None and wide_state["mode"] == "wide", "完整横图按实际渲染宽度进入 wide")
        wide_state = assert_caption_fits(page, "Wide")
        check("ISO" in wide_state["text"] and "ƒ/" in wide_state["text"] and "mm" in wide_state["text"], "Wide 摄影参数包含焦距、光圈和 ISO")
        check("\n" in wide_state["title"] and "文件名：" in wide_state["title"], "Wide title 保留完整题注和文件名")
        page.screenshot(path=str(REPORT_DIR / "01-wide.png"))
        results["wide"] = {"asset_id": wide_detail["id"], "state": wide_state}

        page.set_viewport_size({"width": 1200, "height": 900})
        medium_detail = None
        medium_state = None
        for row in medium_rows[:60]:
            medium_detail = open_photo(page, row["id"])
            medium_state = caption_state(page)
            if medium_state["mode"] == "medium":
                break
        check(medium_state is not None and medium_state["mode"] == "medium", "竖图按实际渲染宽度进入 medium")
        medium_state = assert_caption_fits(page, "Medium")
        check(medium_state["height"] >= 84 and medium_state["height"] <= 88, "Medium 题注高度约为 86px")
        check("..." not in medium_state["text"], "Medium 摄影参数没有被截断")
        memory_rect = medium_state["groups"]["signature-memory"]
        capture_rect = medium_state["groups"]["signature-capture"]
        file_rect = medium_state["groups"]["signature-file"]
        seal_rect = medium_state["groups"]["signature-seal-v3"]
        check(medium_state["viewportWidth"] > 979, "Medium 结构测试使用宽桌面 viewport")
        check(capture_rect["top"] > memory_rect["top"], "Medium Capture 位于 Memory 下方第二带")
        check(file_rect["top"] < capture_rect["top"], "Medium File 保持在第一带右上")
        seal_center = (seal_rect["top"] + seal_rect["bottom"]) / 2
        content_top = min(memory_rect["top"], file_rect["top"])
        content_bottom = max(capture_rect["bottom"], file_rect["bottom"])
        content_center = (content_top + content_bottom) / 2
        check(abs(seal_center - content_center) <= 4, "Medium 拾印章跨两带居中")
        check(capture_rect["width"] > 300, "Medium Capture 使用跨列后的完整宽度")
        page.screenshot(path=str(REPORT_DIR / "02-medium.png"))
        results["medium"] = {"asset_id": medium_detail["id"], "state": medium_state}

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        narrow_state = caption_state(page)
        check(narrow_state["mode"] == "narrow", "390px 窗口按实际渲染宽度进入 narrow")
        narrow_state = assert_caption_fits(page, "Narrow")
        check(100 <= narrow_state["height"] <= 108, "Narrow 题注高度约为 104px")
        page.screenshot(path=str(REPORT_DIR / "03-narrow.png"))
        results["narrow"] = {"asset_id": medium_detail["id"], "state": narrow_state}

        synthetic = page.evaluate(
            """() => {
              renderSignature({effective_date:'',effective_place:'',camera:'',format:'PNG',width:1080,height:1920,metadata:{},files:[]},{path:'screenshot.png',size:2150400});
              const sparse = {
                text: document.querySelector('#photo-signature').textContent,
                captureHidden: document.querySelector('#signature-settings').hidden,
                memoryHidden: document.querySelector('#signature-primary').hidden,
                file: document.querySelector('#signature-format').textContent,
                title: document.querySelector('#photo-signature').title,
              };
              renderSignature({effective_date:'2025-05-26T13:56',effective_place:'',camera:'',format:'JPEG',width:1080,height:1920,metadata:{},files:[]},{path:'ordinary.jpg',size:1000});
              const ordinary = {
                defaultKind: Boolean(document.querySelector('#signature-primary-kind')),
                title: document.querySelector('#photo-signature').title,
              };
              renderSignature({effective_date:'2025-05-26T13:56',effective_place:'',camera:'',format:'JPEG',width:1080,height:1920,effective_source:'人工确认',metadata:{},files:[]},{path:'confirmed.jpg',size:1000});
              return {
                text: sparse.text,
                captureHidden: sparse.captureHidden,
                memoryHidden: sparse.memoryHidden,
                file: sparse.file,
                title: sparse.title,
                sparseText: sparse.text,
                defaultKind: ordinary.defaultKind,
                ordinaryTitle: ordinary.title,
                confirmedKind: Boolean(document.querySelector('#signature-primary-kind')),
              };
            }"""
        )
        check(synthetic["captureHidden"] and synthetic["memoryHidden"], "只有文件信息时不生成空的 Memory / Capture")
        check("1080 × 1920" in synthetic["file"] and "PNG" in synthetic["file"] and "2.1 MB" in synthetic["file"], "只有文件信息时只显示实际 File 信息")
        check("未知" not in synthetic["text"] and "暂无" not in synthetic["text"], "缺少时间地点时不显示 placeholder")
        check(not synthetic["defaultKind"] and "拍摄时间：" in synthetic["ordinaryTitle"], "普通拍摄时间不占可见题注但保留在 title")
        check(synthetic["confirmedKind"], "非默认时间来源仍显示时间来源")

        long_fixture = page.evaluate(
            """() => {
              renderSignature({effective_date:'2025-05-26T13:56',effective_place:'中国 · 北京市海淀区一个非常长的地点名称用于验证完整 title',camera:'Sony ILCE-7RM5 / FE 24-70mm F2.8 GM II',format:'JPEG',width:3060,height:4080,metadata:{ExifTool:{FocalLengthIn35mmFormat:129,FNumber:1.9,ExposureTime:0.005747,ISO:100}}},{path:'long.jpg',size:3900000});
              return {
                title: document.querySelector('#photo-signature').title,
                cameraOverflow: getComputedStyle(document.querySelector('.signature-camera')).textOverflow,
                tokenTexts: [...document.querySelectorAll('.signature-exposure-token')].map(node => node.textContent),
              };
            }"""
        )
        check("Sony ILCE-7RM5 / FE 24-70mm F2.8 GM II" in long_fixture["title"], "长 Camera 的完整值保留在 title")
        check("129mm" in long_fixture["title"] and "ISO 100" in long_fixture["title"], "长 Camera 场景的摄影 token 仍完整")
        check(long_fixture["cameraOverflow"] == "ellipsis", "Camera model 允许单独 ellipsis")

        page.goto(URL, wait_until="domcontentloaded")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_selector("#photo-grid [data-photo]", timeout=30000)
        first = page.locator("#photo-grid [data-photo]").first
        first.click()
        page.wait_for_selector("#detail-dialog[open]", timeout=15000)
        page.wait_for_function("!document.querySelector('#detail-dialog').classList.contains('is-loading')")
        before = caption_state(page)
        if not page.locator("#viewer-next").is_disabled():
            old_position = page.locator("#viewer-position").inner_text()
            page.locator("#viewer-next").click()
            page.wait_for_function("old => document.querySelector('#viewer-position').textContent !== old", arg=old_position)
            page.wait_for_function("!document.querySelector('#detail-dialog').classList.contains('is-loading')")
            after = caption_state(page)
            check(after["mode"] == ("wide" if after["width"] >= 980 else "medium" if after["width"] >= 620 else "narrow"), "连续翻页后题注模式跟随新照片实际宽度")
            check(abs(after["height"] - (68 if after["mode"] == "wide" else 86 if after["mode"] == "medium" else 104)) <= 1, "连续翻页后题注高度同步")
            results["next"] = {"before": before, "after": after}
        else:
            check(False, "连续翻页场景存在下一张照片")

        page.click("#viewer-info")
        page.wait_for_timeout(250)
        drawer_state = caption_state(page)
        check(drawer_state["mode"] in {"wide", "medium", "narrow"}, "打开详细资料后题注模式仍有效")
        check(page.evaluate("getComputedStyle(document.querySelector('#face-name-layer')).bottom") == f"{drawer_state['height']}px", "人名层 bottom inset 跟随题注高度")
        page.click("#close-info")
        check(not errors, "真实浏览器无 JavaScript 错误")
        browser.close()

    report = {"status": "PASS", "checks": checks, "results": results, "page_errors": errors, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
