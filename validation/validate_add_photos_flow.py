"""Read-only browser checks for the final homepage and add-photos flow.

The scan POST is intercepted in the browser and never reaches the formal
8765 service. Folder selection uses the real local folder-picker API.
"""
from __future__ import annotations

import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "add-photos-20260911"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(REPORT / "homepage-default.png"), full_page=True)
        check(page.locator("#page-title").inner_text() == "全部照片", "首页主标题为全部照片")
        check(page.locator(".breadcrumb").is_hidden() and page.locator("#home-years-link").is_hidden(), "首页无重复面包屑和按年份入口")
        order = page.locator("[data-ot]").evaluate_all("els => els.filter(e => ['search','filters','select','sort'].includes(e.dataset.ot)).map(e => e.dataset.ot)")
        check(order == ["search", "filters", "select", "sort"], "首页工具栏顺序为搜索、筛选、选择、排序")
        status = page.request.get(URL + "/api/status").json()
        expected_groups = str((status.get("stats") or {}).get("group_photos", "")).replace(",", "")
        page.wait_for_function("expected => document.querySelector('#groups-count')?.textContent.replaceAll(',', '') === expected", arg=expected_groups, timeout=10000)
        check(page.locator("#groups-count").inner_text().replace(",", "") == expected_groups, "首页首次显示正确合影数量")

        page.click('[data-view="scan"]')
        page.wait_for_timeout(500)
        page.screenshot(path=str(REPORT / "add-photos-empty.png"), full_page=True)
        check(page.locator("#page-title").inner_text() == "添加照片", "添加照片页面标题统一")
        check(page.locator("#start-scan").is_disabled() and page.locator(".scan-root").count() == 0, "添加照片空状态不能开始扫描")
        check(not page.locator("#scan-roots").is_visible() and not page.locator("#with-faces").is_visible(), "旧技术扫描控件不向普通用户显示")

        for folder in (ROOT / "web", ROOT / "validation"):
            page.evaluate("path => window.__ourTimeApp.openFolder(path)", str(folder))
            page.wait_for_selector("#folder-dialog[open]")
            page.click("#use-folder")
            page.wait_for_timeout(150)
        page.evaluate("path => window.__ourTimeApp.openFolder(path)", str(ROOT / "web"))
        page.wait_for_selector("#folder-dialog[open]")
        page.click("#use-folder")
        check(page.locator(".scan-root").count() == 2, "多个文件夹只进入草稿且自动去重")
        page.screenshot(path=str(REPORT / "add-photos-two-folders.png"), full_page=True)

        base_status = page.request.get(URL + "/api/status").json()
        base_status["job"] = {
            "id": "ui-only-progress", "status": "running",
            "roots": json.dumps([str(ROOT / "web")]), "processed": 27,
            "discovered": 81, "metadata_reads": 9, "skipped": 18, "errors": 0,
            "current_path": str(ROOT / "web" / "app.js"), "message": "正在扫描照片",
            "workers": 1, "auxiliary": 0,
        }
        payloads: list[dict] = []

        def route_handler(route):
            if route.request.url.endswith("/api/scan"):
                payloads.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": "ui-only-progress"}))
            elif route.request.url.endswith("/api/status"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps(base_status))
            else:
                route.continue_()

        page.route("**/api/**", route_handler)
        page.click("#start-scan")
        page.wait_for_timeout(500)
        page.screenshot(path=str(REPORT / "add-photos-progress.png"), full_page=True)
        check(len(payloads) == 1, "一次应用只提交一次扫描请求")
        check(payloads[0]["roots"] == [str(ROOT / "web"), str(ROOT / "validation")] and payloads[0]["with_faces"] is True and payloads[0]["include_system"] is False and payloads[0]["workers"] == 1, "扫描请求携带全部目录和安全默认参数")
        check(page.locator("#scan-progress-title").inner_text() == "正在扫描照片" and page.locator("#scan-checked").inner_text() == "27", "扫描中显示真实状态字段")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(200)
        check(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "390px 添加照片页面无横向溢出")
        check(not errors, "真实浏览器页面无 JavaScript 错误")
        browser.close()
    print(json.dumps({"passed": True, "intercepted_scan_requests": 1, "screenshots": sorted(p.name for p in REPORT.glob("*.png"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
