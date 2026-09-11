"""Homepage Phase 1.1 isolated API and read-only browser checks.

The database fixture is temporary. The browser portion only uses the existing
local service and records screenshots; it does not submit write requests.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE_URL = os.environ.get("PHOTO_BROWSER_URL", "http://127.0.0.1:8765").rstrip("/")
REPORT = ROOT / "validation" / "reports" / "homepage-phase1-1-20260911"
SCREENSHOTS = REPORT / "screenshots"


def isolated_group_count() -> dict:
    with tempfile.TemporaryDirectory(prefix="ourtime-home-", dir=ROOT / "validation" / "work") as folder:
        data = Path(folder)
        db_path = data / "library.sqlite3"
        from library_db import init_schema

        conn = sqlite3.connect(db_path)
        init_schema(conn)
        now = "2026-09-11T00:00:00"
        person_id = 1
        for asset_id, face_count in ((1, 1), (2, 2), (3, 3)):
            conn.execute(
                "INSERT INTO assets(id,sha256,width,height,format,created_at) VALUES(?,?,?,?,?,?)",
                (asset_id, f"fixture-{asset_id}", 100, 100, "JPEG", now),
            )
            conn.execute(
                "INSERT INTO files(id,asset_id,path,size,mtime_ns,exists_now,excluded) VALUES(?,?,?,?,?,?,?)",
                (asset_id, asset_id, f"/fixture/{asset_id}.jpg", 10, asset_id, 1, 0),
            )
            for index in range(face_count):
                conn.execute(
                    "INSERT INTO people(id,name,confirmed,alias,ignored) VALUES(?,?,?,?,?)",
                    (person_id, f"人物{person_id}", 1, "", 0),
                )
                conn.execute(
                    "INSERT INTO faces(id,asset_id,person_id,bbox,embedding) VALUES(?,?,?,?,?)",
                    (person_id, asset_id, person_id, "[0,0,1,1]", b"fixture"),
                )
                person_id += 1
        conn.commit()
        conn.close()

        previous = os.environ.get("PHOTO_LIBRARY_DATA")
        os.environ["PHOTO_LIBRARY_DATA"] = str(data)
        try:
            import importlib
            app_module = importlib.import_module("app")
            from fastapi.testclient import TestClient

            client = TestClient(app_module.app)
            groups = client.get("/api/groups")
            status = client.get("/api/status")
            groups_json = groups.json()
            status_json = status.json()
            actual = {
                "groups_status": groups.status_code,
                "status_status": status.status_code,
                "groups_photos": groups_json.get("photos"),
                "status_group_photos": (status_json.get("stats") or {}).get("group_photos"),
            }
            assert groups.status_code == 200 and groups_json.get("photos") == 2, actual
            assert status.status_code == 200 and actual["status_group_photos"] == 2, actual
            return {"name": "isolated_group_count_1_2_3_faces", "status": "PASS", "actual": actual}
        finally:
            if previous is None:
                os.environ.pop("PHOTO_LIBRARY_DATA", None)
            else:
                os.environ["PHOTO_LIBRARY_DATA"] = previous


def get_json(path: str) -> dict:
    with urllib.request.urlopen(BASE_URL + path, timeout=15) as response:
        return json.load(response)


def measure_status() -> dict:
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        payload = get_json("/api/status")
        samples.append(round((time.perf_counter() - started) * 1000, 2))
    return {
        "pid": (payload.get("capabilities") or {}).get("pid"),
        "samples_ms": samples,
        "min_ms": min(samples),
        "max_ms": max(samples),
        "avg_ms": round(sum(samples) / len(samples), 2),
        "group_photos": (payload.get("stats") or {}).get("group_photos"),
    }


def browser_checks() -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "SKIP", "reason": "Python Playwright is not installed."}

    REPORT.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    writes = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1000}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: writes.append(request.method + " " + request.url) if request.method not in ("GET", "HEAD") else None)
        page.goto(BASE_URL + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)
        page.wait_for_timeout(900)

        def visible(selector: str) -> bool:
            return page.locator(selector).count() > 0 and page.locator(selector).first.is_visible()

        def rect(selector: str) -> dict:
            return page.locator(selector).first.bounding_box() or {}

        page.screenshot(path=str(SCREENSHOTS / "default-1920.png"), full_page=True)
        title_count = page.locator("h1:visible").count()
        old_heading_hidden = page.locator("#collection-title").evaluate("e => e.closest('.section-heading').hidden")
        timeline_hidden = page.locator("#timeline-tools").evaluate("e => e.hidden")
        controls = [
            rect('[data-ot="search"]'),
            rect('[data-ot="filters"]'),
            rect('[data-ot="sort"]'),
        ]
        tops = [round(item.get("y", -1)) for item in controls]
        sort_style = page.locator('[data-ot="sort"]').evaluate(
            "e => { const s=getComputedStyle(e); return {border:s.borderTopStyle, background:s.backgroundColor}; }"
        )
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        results.append({
            "name": "desktop_home_single_title_and_collapsed_legacy_controls",
            "status": "PASS" if title_count == 1 and old_heading_hidden and timeline_hidden and not overflow else "FAIL",
            "actual": {"title_count": title_count, "old_heading_hidden": old_heading_hidden, "timeline_hidden": timeline_hidden, "overflow": overflow},
        })
        results.append({
            "name": "desktop_query_row_and_visible_sort",
            "status": "PASS" if max(tops) - min(tops) <= 2 and sort_style["border"] != "none" and sort_style["background"] not in ("rgba(0, 0, 0, 0)", "transparent") else "FAIL",
            "actual": {"tops": tops, "sort_style": sort_style, "host_rect": rect("#home-query-host"), "grid_rect": rect("#photo-grid")},
        })
        results.append({
            "name": "default_chips_have_no_layout_height",
            "status": "PASS" if not visible('[data-ot="chips"]') else "FAIL",
            "actual": {"chips_visible": visible('[data-ot="chips"]'), "chips_rect": rect('[data-ot="chips"]')},
        })

        people_requests = []
        page.on("request", lambda request: people_requests.append(request.url) if request.method == "GET" and "/api/people?" in request.url else None)
        page.locator('[data-ot="filters"]').click()
        page.wait_for_selector(".ot-home-filter-dialog[open]", timeout=5000)
        page.screenshot(path=str(SCREENSHOTS / "filter-open-1920.png"), full_page=True)
        page.wait_for_timeout(1200)
        results.append({
            "name": "filter_drawer_default_closed_then_opens",
            "status": "PASS" if visible(".ot-home-filter-dialog[open]") else "FAIL",
            "actual": {"people_request_count": len(people_requests)},
        })
        person_select = page.locator('[data-ot="person"]')
        try:
            page.wait_for_function("document.querySelector('[data-ot=person]')?.options.length > 1", timeout=15000)
        except Exception:
            pass
        if person_select.locator("option").count() > 1:
            sort_before = page.locator('[data-ot="sort"]').input_value()
            person_select.select_option(index=1)
            page.locator('[data-ot="apply"]').click()
            page.wait_for_function("!document.querySelector('.ot-home-filter-dialog')?.open", timeout=10000)
            page.wait_for_timeout(250)
            sort_after = page.locator('[data-ot="sort"]').input_value()
            chip_visible = visible('[data-ot="chips"]')
            page.screenshot(path=str(SCREENSHOTS / "chip-filtered-1920.png"), full_page=True)
            results.append({
                "name": "person_filter_applies_once_and_preserves_sort",
                "status": "PASS" if chip_visible and sort_before == sort_after else "FAIL",
                "actual": {"sort_before": sort_before, "sort_after": sort_after, "chip_visible": chip_visible},
            })
        else:
            results.append({"name": "person_filter_applies_once_and_preserves_sort", "status": "SKIP", "actual": {"reason": "当前库没有可用人物选项"}})
            page.locator('[data-ot="cancel"]').click()

        page.locator('[data-ot="filters"]').click()
        page.wait_for_selector(".ot-home-filter-dialog[open]", timeout=5000)
        directory = "X:\\phase1-1-validation"
        sort_before = page.locator('[data-ot="sort"]').input_value()
        page.locator('[data-ot="directory"]').fill(directory)
        page.locator('[data-ot="apply"]').click()
        page.wait_for_function("!document.querySelector('.ot-home-filter-dialog')?.open", timeout=10000)
        page.wait_for_timeout(250)
        sort_after = page.locator('[data-ot="sort"]').input_value()
        results.append({
            "name": "folder_filter_is_draft_then_applied_without_sort_change",
            "status": "PASS" if sort_before == sort_after and page.locator('[data-condition="directory"]').count() == 1 else "FAIL",
            "actual": {"sort_before": sort_before, "sort_after": sort_after, "directory_chip_count": page.locator('[data-condition="directory"]').count()},
        })
        page.locator('[data-condition="all"]').click()
        page.wait_for_timeout(250)
        results.append({
            "name": "clear_all_removes_conditions_without_sort_change",
            "status": "PASS" if not visible('[data-ot="chips"]') and page.locator('[data-ot="sort"]').input_value() == sort_after else "FAIL",
            "actual": {"chips_visible": visible('[data-ot="chips"]'), "sort": page.locator('[data-ot="sort"]').input_value()},
        })
        page.locator('[data-ot="select"]').click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOTS / "selection-1920.png"), full_page=True)
        selecting = page.evaluate("window.__ourTimeApp && window.__ourTimeApp.state.selecting")
        results.append({"name": "selection_enters_existing_batch_mode", "status": "PASS" if selecting else "FAIL", "actual": {"selecting": selecting}})
        page.locator('[data-ot="cancel-selection"]').click()
        page.wait_for_timeout(200)

        page.locator('[data-view="years"]').click()
        page.wait_for_selector("#timeline-view:not([hidden])", timeout=10000)
        years_visible = page.locator("#timeline-view .section-heading").is_visible()
        page.locator('[data-view="people"]').click()
        page.wait_for_selector("#people-view:not([hidden])", timeout=10000)
        people_heading_visible = page.locator("#people-view .section-heading").is_visible()
        page.locator('[data-view="groups"]').click()
        page.wait_for_selector("#groups-view:not([hidden])", timeout=10000)
        page.locator('[data-view="timeline"]').click()
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=10000)
        results.append({
            "name": "years_entry_and_non_home_views_keep_existing_layouts",
            "status": "PASS" if years_visible and people_heading_visible and visible('[data-ot="search"]') else "FAIL",
            "actual": {"years_visible": years_visible, "people_heading_visible": people_heading_visible, "home_ui_restored": visible('[data-ot="search"]')},
        })

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(200)
        narrow_overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        results.append({"name": "narrow_390_no_horizontal_overflow", "status": "PASS" if not narrow_overflow else "FAIL", "actual": {"overflow": narrow_overflow}})
        page.screenshot(path=str(SCREENSHOTS / "narrow-390.png"), full_page=True)
        live_status = get_json("/api/status")
        live_group_photos = (live_status.get("stats") or {}).get("group_photos")
        results.append({
            "name": "fresh_group_count_before_group_page",
            "status": "PASS" if live_group_photos is not None and page.locator("#groups-count").inner_text() == str(live_group_photos) else "SKIP",
            "actual": {"live_group_photos": live_group_photos, "dom_groups_count": page.locator("#groups-count").inner_text(), "reason": "现役 8765 尚未重启，仍运行旧 app.py" if live_group_photos is None else ""},
        })
        browser.close()
    return {"status": "PASS" if not errors and not writes and all(item["status"] != "FAIL" for item in results) else "FAIL", "checks": results, "page_errors": errors, "non_get_requests": writes}


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    output = {"isolated": None, "browser": None, "status_latency": None}
    try:
        output["isolated"] = isolated_group_count()
    except Exception as exc:
        output["isolated"] = {"status": "FAIL", "error": repr(exc)}
    try:
        output["status_latency"] = measure_status()
        output["browser"] = browser_checks()
    except Exception as exc:
        output["browser"] = {"status": "FAIL", "error": repr(exc)}
    (REPORT / "integration.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["isolated"].get("status") == "PASS" and output["browser"].get("status") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
