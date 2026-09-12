"""Isolated browser acceptance for the unified add-photos flow."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "validation" / "work" / ("add-photos-" + time.strftime("%Y%m%d-%H%M%S"))
DATA = RUN / "data"
URL = "http://127.0.0.1:8794"
REPORT = ROOT / "validation" / "reports" / "add-photos-20260913"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def request_json(path: str) -> dict:
    with urllib.request.urlopen(URL + path, timeout=20) as response:
        return json.load(response)


def main() -> int:
    RUN.mkdir(parents=True)
    DATA.mkdir()
    env = {**os.environ, "PHOTO_LIBRARY_DATA": str(DATA), "PHOTO_WEB_ROOT": str(ROOT / "web")}
    log_path = RUN / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", "8794"],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        for _ in range(160):
            try:
                request_json("/api/status")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            log.flush()
            raise RuntimeError("隔离服务未启动\n" + log_path.read_text(encoding="utf-8", errors="replace"))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(URL, wait_until="domcontentloaded")
            page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)

            browse = page.request.get(URL + "/api/folders?scope=browse").json()
            scan = page.request.get(URL + "/api/folders?scope=scan").json()
            browse_roots = [item["path"] for item in browse["items"]]
            scan_roots = [item["path"] for item in scan["items"]]
            check(browse_roots and all(path.upper().startswith("I:") for path in browse_roots), "文件夹浏览用途继续只从 I 盘进入")
            check(any(not path.upper().startswith("I:") for path in scan_roots), "添加照片目录选择器列出 I 盘以外的本机磁盘")
            bad_scope = page.request.get(URL + "/api/folders?scope=unknown")
            check(bad_scope.status == 400, "目录接口拒绝未知用途")
            parent_listing = page.request.get(URL + "/api/folders?scope=scan&path=" + urllib.parse.quote(str(RUN))).json()
            check(str(DATA) not in [item["path"] for item in parent_listing["items"]], "添加照片选择器不列出资料库缓存目录")

            page.click("#add-folder")
            page.wait_for_timeout(250)
            REPORT.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(REPORT / "add-photos-empty.png"), full_page=True)
            check(page.locator("#page-title").inner_text() == "添加照片", "添加照片页面标题统一")
            check(page.locator("#start-scan").is_disabled() and page.locator(".scan-root").count() == 0, "没有目录时不能开始扫描")
            check(page.locator("#start-scan").inner_text() == "添加并扫描", "主操作使用添加并扫描文案")
            check(not page.locator("#scan-roots").is_visible() and not page.locator("#with-faces").is_visible(), "旧技术扫描控件不向普通用户显示")
            check(page.locator("#last-scan-details").count() == 0, "普通添加照片页面不再显示最近一次扫描")

            page.click("#scan-folder-picker")
            page.wait_for_selector("#folder-dialog[open]")
            dialog_paths = page.locator("#folder-list [data-folder]").evaluate_all("nodes => nodes.map(node => node.dataset.folder)")
            check(set(dialog_paths) == set(scan_roots), "页面目录选择器实际使用 scan 用途磁盘列表")
            page.locator('[data-close="folder-dialog"]').click()

            normalized = page.evaluate("""() => {
              window.__ourTimeApp.addScanRoot('Z:\\\\');
              return window.__ourTimeApp.state.scanRoots[0];
            }""")
            check(normalized == "Z:\\", "磁盘根目录保留反斜杠，不会误变成盘符相对路径")
            page.evaluate("window.__ourTimeApp.state.scanRoots=[]; window.__ourTimeApp.renderScanRoots()")

            payloads: list[dict] = []

            def intercept_scan(route):
                payloads.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json", body='{"id":"ui-only-progress"}')

            page.route("**/api/scan", intercept_scan)

            page.click('[data-view="folders"]')
            page.wait_for_function("window.__ourTimeApp?.state.view === 'folders'")
            page.wait_for_timeout(200)
            page.click("#folders-scan-new")
            page.wait_for_function("window.__ourTimeApp?.state.view === 'scan'")
            check(payloads == [] and len(page.evaluate("window.__ourTimeApp.state.scanRoots")) == 1, "文件夹页扫描入口只预填添加照片页，不直接提交")

            page.evaluate("window.__ourTimeApp.openAddPhotos()")
            for folder in (ROOT / "web", ROOT / "validation"):
                page.evaluate("path => window.__ourTimeApp.openFolder(path)", str(folder))
                page.wait_for_selector("#folder-dialog[open]")
                page.click("#use-folder")
                page.wait_for_timeout(100)
            page.evaluate("path => window.__ourTimeApp.openFolder(path)", str(ROOT / "web"))
            page.wait_for_selector("#folder-dialog[open]")
            page.click("#use-folder")
            check(page.locator(".scan-root").count() == 2, "可连续添加两个文件夹且重复目录自动去重")
            page.locator("[data-remove-scan-root]").first.click()
            check(page.locator(".scan-root").count() == 1, "已选择目录可以移除")
            page.evaluate("path => window.__ourTimeApp.addScanRoot(path)", str(ROOT / "web"))
            page.screenshot(path=str(REPORT / "add-photos-two-folders.png"), full_page=True)

            base_status = request_json("/api/status")
            base_status["job"] = {
                "id": "ui-only-progress", "status": "running",
                "roots": json.dumps([str(ROOT / "web")]), "processed": 27,
                "discovered": 81, "metadata_reads": 9, "skipped": 18, "errors": 0,
                "current_path": str(ROOT / "web" / "app.js"), "message": "正在扫描照片",
                "workers": 1, "auxiliary": 0,
            }
            page.route("**/api/status", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(base_status)
            ))
            page.click("#start-scan")
            page.wait_for_timeout(350)
            page.screenshot(path=str(REPORT / "add-photos-progress.png"), full_page=True)
            check(len(payloads) == 1, "一次操作只提交一个扫描请求")
            check(set(payloads[0]["roots"]) == {str(ROOT / "web"), str(ROOT / "validation")}, "扫描 payload 包含全部选定目录")
            check(payloads[0]["with_faces"] is True and payloads[0]["include_system"] is False and payloads[0]["workers"] == 1, "扫描 payload 自动使用安全默认参数")
            check(page.locator("#scan-progress-title").inner_text() == "正在添加照片" and page.locator("#scan-current").inner_text() == "正在处理照片……", "扫描中只显示简洁进度，不突出完整路径")

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(REPORT / "add-photos-390.png"), full_page=True)
            check(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "390px 添加照片页面无横向溢出")
            check(not errors, "真实浏览器页面无 JavaScript 错误")
            browser.close()

        log.flush()
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        check("Traceback" not in log_text and "ERROR" not in log_text, "隔离服务日志没有错误诊断")
        print(json.dumps({"passed": True, "scan_roots": scan_roots, "screenshots": sorted(p.name for p in REPORT.glob("*.png"))}, ensure_ascii=False))
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log.close()
        run_resolved = RUN.resolve()
        work_resolved = (ROOT / "validation" / "work").resolve()
        if work_resolved in run_resolved.parents:
            shutil.rmtree(run_resolved, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
