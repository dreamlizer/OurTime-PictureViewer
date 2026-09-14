"""Isolated contract checks for people search and pinyin sorting."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browse_queries import fetch_people
from library_db import init_schema, person_pinyin_key


def seed(conn: sqlite3.Connection) -> None:
    people = [
        (1, "张三丰", "", 1, 4),
        (2, "陈一", "老陈", 1, 3),
        (3, "王五", "", 1, 2),
        (4, "李四", "", 1, 1),
        (5, "曾明", "", 1, 1),
        (6, "", "", 0, 5),
    ]
    asset_id = 0
    face_id = 0
    for person_id, name, alias, confirmed, photo_count in people:
        conn.execute(
            "INSERT INTO people(id,name,alias,confirmed,ignored) VALUES(?,?,?,?,0)",
            (person_id, name, alias, confirmed),
        )
        for _ in range(photo_count):
            asset_id += 1
            face_id += 1
            conn.execute(
                "INSERT INTO assets(id,sha256,width,height,format,metadata,created_at) VALUES(?,?,?,?,?,?,?)",
                (asset_id, f"people-sort-{asset_id}", 100, 100, "JPEG", "{}", "2026-09-12T00:00:00"),
            )
            conn.execute(
                "INSERT INTO files(id,asset_id,path,size,mtime_ns,exists_now,excluded) VALUES(?,?,?,?,?,?,0)",
                (asset_id, asset_id, f"X:\\people-sort\\{asset_id}.jpg", 1, asset_id, 1),
            )
            conn.execute(
                "INSERT INTO faces(id,asset_id,person_id,bbox,embedding) VALUES(?,?,?,?,?)",
                (face_id, asset_id, person_id, "[0,0,1,1]", b"x"),
            )
    conn.commit()


def names(result):
    return [item["name"] for item in result["items"]]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shiguang-people-sort-") as folder:
        conn = sqlite3.connect(Path(folder) / "library.sqlite3")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        seed(conn)

        default = fetch_people(conn, limit=20)
        assert names(default) == ["张三丰", "陈一", "王五", "李四", "曾明", ""], default

        ascending = fetch_people(conn, sort="name_asc", limit=20)
        expected_ascending = ["陈一", "李四", "王五", "曾明", "张三丰", ""]
        assert names(ascending) == expected_ascending, ascending
        assert [person_pinyin_key(name) for name in expected_ascending[:-1]] == sorted(
            person_pinyin_key(name) for name in expected_ascending[:-1]
        )

        descending = fetch_people(conn, sort="name_desc", limit=20)
        assert names(descending) == list(reversed(expected_ascending[:-1])) + [""], descending

        one_character = fetch_people(conn, q="张", named=1, limit=20)
        assert names(one_character) == ["张三丰"], one_character
        alias = fetch_people(conn, q="老", named=1, limit=20)
        assert names(alias) == ["陈一"], alias
        full_pinyin = fetch_people(conn, q="zhangsanfeng", named=1, limit=20)
        assert names(full_pinyin) == ["张三丰"], full_pinyin
        spaced_pinyin = fetch_people(conn, q="ZHANG SAN FENG", named=1, limit=20)
        assert names(spaced_pinyin) == ["张三丰"], spaced_pinyin
        pinyin_fragment = fetch_people(conn, q="sanfeng", named=1, limit=20)
        assert names(pinyin_fragment) == ["张三丰"], pinyin_fragment
        initials = fetch_people(conn, q="zsf", named=1, limit=20)
        assert names(initials) == ["张三丰"], initials
        alias_pinyin = fetch_people(conn, q="laochen", named=1, limit=20)
        assert names(alias_pinyin) == ["陈一"], alias_pinyin
        polyphonic_surname = fetch_people(conn, q="zengming", named=1, limit=20)
        assert names(polyphonic_surname) == ["曾明"], polyphonic_surname
        assert all(item["confirmed"] for item in one_character["items"] + alias["items"])
        conn.execute("UPDATE people SET name='赵六' WHERE id=4")
        conn.commit()
        renamed = fetch_people(conn, q="zhaoliu", named=1, limit=20)
        assert names(renamed) == ["赵六"], renamed

        try:
            fetch_people(conn, sort="unknown", limit=20)
        except ValueError as exc:
            assert "人物排序" in str(exc)
        else:
            raise AssertionError("unknown people sort must be rejected")
        conn.close()

    print("PEOPLE_SEARCH_SORT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
