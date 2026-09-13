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
        "workers": 1, "auxiliary": 0, "with_faces": 1, "total": 81,
        "phase": "processing", "current_stage": "faces", "face_photos": 9,
        "faces_found": 17, "face_seconds": 7.2,
    }


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        base = page.request.get(URL + "/api/status").json()
        base["face_average_seconds"] = 0.8
        current = {"job": job("completed")}
        cancel_requests: list[str] = []
        base["job"] = current["job"]

        def route_api(route) -> None:
            if route.request.url.endswith("/api/status"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({**base, "job": current["job"]}))
                return
            if route.request.url.endswith("/cancel"):
                cancel_requests.append(route.request.url)
                current["job"] = job("cancelled")
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "status": "cancelled"}))
                return
            if route.request.url.endswith("/api/scan"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": "ui-scan-status"}))
                return
            route.continue_()

        page.route("**/api/**", route_api)
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)
        page.click('#add-folder')
        page.wait_for_timeout(300)
        assert page.locator("#scan-progress").is_hidden()
        assert page.locator(".add-photos-card").is_visible()
        assert page.locator("#last-scan-details").count() == 0
        assert page.locator(".add-photos-card h2, .add-photos-card .panel-number, .folder-picker-card small").count() == 0
        card_box = page.locator(".add-photos-card").bounding_box()
        title_box = page.locator("#page-title").bounding_box()
        picker_box = page.locator("#scan-folder-picker").bounding_box()
        start_box = page.locator("#start-scan").bounding_box()
        assert card_box and title_box and abs(card_box["x"] - title_box["x"]) < 3
        assert card_box["width"] <= 722 and picker_box and picker_box["width"] < 210 and picker_box["height"] < 50
        assert start_box and start_box["width"] < 180

        for status in ("running", "pausing", "paused"):
            current["job"] = job(status)
            page.evaluate("window.__ourTimeApp.refreshStatus?.()")
            page.wait_for_timeout(100)
            assert page.locator("#scan-progress").is_visible(), status
            assert page.locator("#scan-fraction").inner_text() == "27 / 81"
            assert page.locator("#scan-remaining").inner_text() == "54"
            assert page.locator("#scan-skipped").inner_text() == "18"
            assert page.locator("#scan-face-photos").inner_text() == "9"
            assert page.locator("#scan-faces-found").inner_text() == "17"
            assert page.locator("#scan-face-average").inner_text() == "0.80 秒/张"
            if status == "paused":
                assert page.locator(".add-photos-card").is_visible()
                assert page.locator("#scan-folder-picker").is_visible()
                assert page.locator("#scan-progress-title").inner_text() == "上次扫描已暂停"
                assert page.locator("#resume-scan").inner_text() == "继续上次扫描"
                assert page.locator("#cancel-scan").inner_text() == "取消"
                assert page.locator("#scan-current").is_hidden()

        inventory = job("running")
        inventory.update(phase="inventory", current_stage="inventory", total=0, processed=0, discovered=46)
        current["job"] = inventory
        page.evaluate("window.__ourTimeApp.refreshStatus?.()")
        page.wait_for_timeout(100)
        assert page.locator("#scan-progress-title").inner_text() == "正在清点照片"
        assert page.locator("#scan-stage").inner_text() == "正在清点文件，已发现 46 张照片"
        assert page.locator("#scan-fraction").inner_text() == "46 张"
        assert page.locator("#scan-progress-track").get_attribute("class").endswith("is-inventory")
        current["job"] = job("paused")
        page.evaluate("window.__ourTimeApp.refreshStatus?.()")
        page.wait_for_timeout(100)

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
        page.click("#cancel-scan")
        page.wait_for_timeout(150)
        assert cancel_requests and current["job"]["status"] == "cancelled"
        assert page.locator("#scan-progress").is_hidden()
        assert page.locator(".add-photos-card").is_visible()

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
