"""Isolated contract check for the group-photo recognized-people sort."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browse_queries import fetch_photos
from library_db import init_schema

REPORT = ROOT / "validation" / "reports" / "group-recognized-sort.json"


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


def main():
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
    check('value="recognized_desc"' in html and "data-group-sort" in html, "合影页提供“已识别人物最多”选项", checks)
    check("groupSort.hidden=!groupDetail" in app, "该排序只在合影详情显示", checks)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps({"passed": True, "checks": checks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"passed": True, "checks": len(checks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
