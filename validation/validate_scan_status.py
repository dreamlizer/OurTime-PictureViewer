"""Browser regression checks for scan-page session state and status copy."""
from __future__ import annotations

import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "scan-status-20260911.json"


def job(status: str) -> dict:
    return {
        "id": "ui-scan-status", "status": status, "roots": json.dumps([str(ROOT / "web")]),
        "processed": 27, "discovered": 81, "metadata_reads": 9, "skipped": 18,
        "errors": 0, "current_path": str(ROOT / "web" / "app.js"),
        "message": "正在扫描照片" if status in {"running", "pausing", "paused"} else "扫描结束",
        "workers": 1, "auxiliary": 0,
    }


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        base = page.request.get(URL + "/api/status").json()
        current = {"job": job("completed")}
        base["job"] = current["job"]
        page.route("**/api/**", lambda route: route.fulfill(status=200, content_type="application/json",
            body=json.dumps({**base, "job": current["job"]})) if route.request.url.endswith("/api/status") else
            (route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": "ui-scan-status"}))
             if route.request.url.endswith("/api/scan") else route.continue_()))
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)
        page.click('#add-folder')
        page.wait_for_timeout(300)
        assert page.locator("#scan-progress").is_hidden()
        assert page.locator(".add-photos-card").is_visible()
        assert page.locator("#last-scan-details").count() == 0

        for status in ("running", "pausing", "paused"):
            current["job"] = job(status)
            page.evaluate("window.__ourTimeApp.refreshStatus?.()")
            page.wait_for_timeout(100)
            assert page.locator("#scan-progress").is_visible(), status
            if status == "paused":
                assert page.locator(".add-photos-card").is_visible()
                assert page.locator("#scan-folder-picker").is_visible()
                assert page.locator("#scan-progress-title").inner_text() == "上一次添加已暂停"
                assert page.locator("#resume-scan").inner_text() == "继续上次扫描"
                assert "选择新文件夹" in page.locator("#scan-current").inner_text()

        # Compatibility check against the currently running backend: the picker
        # obtains drive roots from /api/drives even before a service restart.
        page.click("#scan-folder-picker")
        page.wait_for_selector("#folder-dialog[open]")
        roots = page.locator("#folder-list [data-folder]").evaluate_all(
            "nodes => nodes.map(node => node.dataset.folder)"
        )
        assert roots and any(not root.upper().startswith("I:") for root in roots)
        page.click('[data-close="folder-dialog"]')
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(REPORT.parent / "scan-paused-allows-new-folder.png"),
            full_page=True,
        )

        # Start a scan through the page, then simulate its completion in this visit.
        current["job"] = job("completed")
        page.evaluate("window.__ourTimeApp.refreshStatus?.()")
        page.wait_for_timeout(100)
        page.evaluate("path => window.__ourTimeApp.openFolder(path)", str(ROOT / "web"))
        page.wait_for_selector("#folder-dialog[open]")
        page.click("#use-folder")
        page.click("#start-scan")
        current["job"] = job("running")
        page.evaluate("window.__ourTimeApp.refreshStatus?.()")
        page.wait_for_timeout(100)
        current["job"] = job("completed")
        page.evaluate("window.__ourTimeApp.refreshStatus?.()")
        page.wait_for_timeout(150)
        assert page.locator("#scan-progress").is_visible()
        assert page.locator("#scan-progress-title").inner_text() == "照片已经添加"
        page.click('[data-view="timeline"]')
        page.click('#add-folder')
        page.wait_for_timeout(150)
        assert page.locator("#scan-progress").is_hidden()
        assert page.locator(".add-photos-card").is_visible()
        browser.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"status": "PASS"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS scan session status regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
