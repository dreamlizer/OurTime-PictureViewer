"""Isolated contract check for the group-photo recognized-people sort."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browse_queries import fetch_photos
from library_db import init_schema
from playwright.sync_api import sync_playwright

REPORT = ROOT / "validation" / "reports" / "group-recognized-sort.json"
SCREENSHOT = ROOT / "validation" / "reports" / "group-recognized-sort-live.png"
URL = "http://127.0.0.1:8765"


def check(value, message, checks):
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def add_asset(conn, aid, named_people, unnamed_people, captured_at):
    conn.execute(
        "INSERT INTO assets(id,sha256,width,height,format,captured_at,date_source,date_precision,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (aid, f"sha-{aid}", 1200, 800, "JPEG", captured_at, "EXIF", "日", captured_at),
    )
    conn.execute(
        "INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES(?,?,?,?,?)",
        (aid, f"C:/fixture/{aid}.jpg", 1000, aid, captured_at),
    )
    for person_id in [*named_people, *unnamed_people]:
        conn.execute(
            "INSERT INTO faces(asset_id,person_id,bbox,embedding,score,reviewed) VALUES(?,?,?,?,?,?)",
            (aid, person_id, "[0,0,10,10]", b"x", 0.9, 1),
        )


def request_json(path):
    with urllib.request.urlopen(URL + path, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    checks = []
    with tempfile.TemporaryDirectory(prefix="shiguang-group-sort-") as temp:
        db_path = Path(temp) / "library.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        for person_id in range(1, 9):
            conn.execute(
                "INSERT INTO people(id,name,confirmed) VALUES(?,?,1)",
                (person_id, f"人物{person_id}"),
            )
        for person_id in range(9, 40):
            conn.execute(
                "INSERT INTO people(id,name,confirmed) VALUES(?,?,0)",
                (person_id, ""),
            )
        add_asset(conn, 1, range(1, 9), range(9, 13), "2020-01-01T10:00:00")
        add_asset(conn, 2, range(1, 7), range(13, 27), "2023-01-01T10:00:00")
        add_asset(conn, 3, range(1, 9), range(27, 34), "2021-01-01T10:00:00")
        add_asset(conn, 4, [1, 1], range(9, 17), "2024-01-01T10:00:00")
        conn.commit()

        page = fetch_photos(conn, filter="group:10plus", sort="recognized_desc", limit=20)
        conn.commit()
        ids = [item["id"] for item in page["items"]]
        recognized = [item["recognized_people"] for item in page["items"]]
        visible = [item["visible_faces"] for item in page["items"]]
        check(ids == [3, 1, 2, 4], "先按已识别人物数，再按照片中可见人脸数排序", checks)
        check(recognized == [8, 8, 6, 1], "已识别人数按不同人物去重，不把同一人重复脸算多次", checks)
        check(visible == [15, 12, 20, 10], "排序结果返回可核对的人脸计数", checks)

        sequence = fetch_photos(
            conn,
            filter="group:10plus",
            sort="recognized_desc",
            sequence=True,
            limit=20,
        )
        conn.commit()
        check(sequence["ids"] == ids, "照片列表和大图浏览序列使用同一排序", checks)
        conn.close()

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    query_ui = (ROOT / "web" / "home-query-ui.js").read_text(encoding="utf-8")
    query_bridge = (ROOT / "web" / "home-query-bridge.js").read_text(encoding="utf-8")
    check(
        'value="recognized_desc"' in html and "['recognized_desc', '已识别人物最多']" in query_ui,
        "合影页提供“已识别人物最多”选项",
        checks,
    )
    check(
        "recognizedSort.hidden=!grouped" in query_ui
        and "sort === 'recognized_desc' && !scope().startsWith('group:')" in query_bridge
        and "groupSort.hidden=!groupDetail" in app,
        "该排序只在合影详情显示",
        checks,
    )
    live = None
    if args.live:
        before = request_json("/api/status")
        query = urllib.parse.urlencode(
            {"filter": "group:10plus", "sort": "recognized_desc", "limit": 24}
        )
        expected = request_json("/api/photos?" + query)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(URL, wait_until="domcontentloaded")
            page.evaluate("window.__ourTimeApp.setView('group:10plus')")
            page.wait_for_selector("#photo-grid .photo-card")
            option = page.locator('[data-ot="sort"] option[value="recognized_desc"]')
            check(not option.evaluate("(e)=>e.hidden"), "正式合影详情显示新增排序选项", checks)
            page.select_option('[data-ot="sort"]', "recognized_desc")
            page.wait_for_function(
                "waterfall.query?.sort==='recognized_desc' && waterfall.pending.size===0",
                timeout=30000,
            )
            first_id = int(page.locator("#photo-grid .photo-card").first.get_attribute("data-photo"))
            check(first_id == expected["items"][0]["id"], "正式页面首张照片与排序接口第一名一致", checks)
            page.screenshot(path=str(SCREENSHOT), full_page=False)
            check(not errors, "正式合影排序页面没有 JavaScript 错误", checks)
            browser.close()
        after = request_json("/api/status")
        check(before["capabilities"]["pid"] == after["capabilities"]["pid"], "正式排序复核没有打断后台", checks)
        live = {
            "total": expected["total"],
            "first_id": expected["items"][0]["id"],
            "top": [
                [item["recognized_people"], item["visible_faces"]]
                for item in expected["items"][:10]
            ],
            "screenshot": str(SCREENSHOT),
        }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps({"passed": True, "checks": checks, "live": live}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"passed": True, "checks": len(checks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
