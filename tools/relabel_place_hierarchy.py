"""Rebuild stored place text from existing GPS without reading original photos."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geo_labels import PlaceIndex, administrative_prefix  # noqa: E402


def active_scan(connection):
    return connection.execute(
        "SELECT id,status FROM jobs WHERE status IN ('running','pausing') "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


def backup_database(source: Path) -> Path:
    target_dir = source.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"library-{stamp}-before-place-hierarchy.sqlite3"
    with sqlite3.connect(source) as current, sqlite3.connect(target) as backup:
        current.backup(backup)
    return target


def build_plan(connection, index):
    rows = connection.execute(
        """
        SELECT id,latitude,longitude,place,place_source,manual_place
        FROM assets
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND latitude BETWEEN -90 AND 90
          AND longitude BETWEEN -180 AND 180
        ORDER BY id
        """
    ).fetchall()
    coordinate_labels = {}
    rule_names = {
        str(row[0] or "").strip()
        for row in connection.execute("SELECT name FROM place_rules")
    }
    plan = []
    for row in rows:
        key = (float(row["latitude"]), float(row["longitude"]))
        current_place = str(row["place"] or "").strip()
        needs_automatic_update = (
            current_place.startswith(("中国", "中国香港", "中国澳门", "中国台湾"))
            or current_place in rule_names
        )
        if needs_automatic_update:
            if key not in coordinate_labels:
                coordinate_labels[key] = index.nearest(key[0], key[1], True)
            automatic, source = coordinate_labels[key]
            next_place = automatic or row["place"]
            next_source = source or row["place_source"]
        else:
            next_place = row["place"]
            next_source = row["place_source"]
        next_manual = (
            index.normalize_manual(next_place, row["manual_place"])
            if str(row["manual_place"] or "").strip()
            else row["manual_place"]
        )
        if (
            next_place != row["place"]
            or next_source != row["place_source"]
            or next_manual != row["manual_place"]
        ):
            plan.append(
                {
                    "id": int(row["id"]),
                    "before": (
                        row["place"], row["place_source"], row["manual_place"]
                    ),
                    "place": next_place,
                    "place_source": next_source,
                    "manual_before": row["manual_place"],
                    "manual_place": next_manual,
                }
            )
    return rows, coordinate_labels, plan


def apply_plan(connection, plan):
    stamp = datetime.now().isoformat(timespec="seconds")
    automatic_changed = 0
    manual_changed = 0
    connection.execute("BEGIN IMMEDIATE")
    if active_scan(connection):
        connection.rollback()
        raise RuntimeError("扫描正在运行，未修改地点")
    for item in plan:
        current = connection.execute(
            "SELECT place,place_source,manual_place FROM assets WHERE id=?",
            (item["id"],),
        ).fetchone()
        if not current:
            connection.rollback()
            raise RuntimeError(f"照片 {item['id']} 在迁移期间消失，已整批回滚")
        if tuple(current) != item["before"]:
            connection.rollback()
            raise RuntimeError(
                f"照片 {item['id']} 的地点在迁移期间发生变化，已整批回滚"
            )
        next_values = (
            item["place"], item["place_source"], item["manual_place"]
        )
        if tuple(current) == next_values:
            continue
        if current["place"] != item["place"] or current["place_source"] != item["place_source"]:
            automatic_changed += 1
        if current["manual_place"] != item["manual_place"]:
            manual_changed += 1
            connection.execute(
                "INSERT INTO edits(created_at,target,before_json,after_json) "
                "VALUES (?,?,?,?)",
                (
                    stamp,
                    f"asset:{item['id']}",
                    json.dumps(
                        {"manual_place": current["manual_place"]},
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "manual_place": item["manual_place"],
                            "source": "place_hierarchy_migration_v1",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        connection.execute(
            "UPDATE assets SET place=?,place_source=?,manual_place=? WHERE id=?",
            (*next_values, item["id"]),
        )
    connection.execute(
        "INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)",
        (
            stamp,
            "maintenance:place-hierarchy-v1",
            json.dumps({"planned": len(plan)}, ensure_ascii=False),
            json.dumps(
                {
                    "automatic_changed": automatic_changed,
                    "manual_changed": manual_changed,
                },
                ensure_ascii=False,
            ),
        ),
    )
    connection.commit()
    return automatic_changed, manual_changed


def main():
    from ourtime_config import DATA, GEO_ROOT

    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DATA / "library.sqlite3")
    parser.add_argument("--geo-root", type=Path, default=GEO_ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        raise SystemExit(f"数据库不存在：{database}")
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    if active_scan(connection):
        raise SystemExit("扫描正在运行；为保护数据，本次没有执行")
    index = PlaceIndex(args.geo_root.resolve())
    index.load()
    rules = []
    for row in connection.execute(
        "SELECT name,latitude,longitude,radius_km,source FROM place_rules"
    ):
        base, _source = index.nearest(
            float(row["latitude"]), float(row["longitude"]), False
        )
        rules.append(
            {
                "name": index.normalize_manual(base, row["name"]),
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "radius_km": row["radius_km"],
                "source": row["source"],
            }
        )
    index.set_overrides(rules, "place-hierarchy-v1")
    rows, coordinates, plan = build_plan(connection, index)
    result = {
        "database": str(database),
        "geotagged": len(rows),
        "resolved_coordinates": len(coordinates),
        "planned_updates": len(plan),
        "apply": bool(args.apply),
    }
    if args.apply and plan:
        backup = backup_database(database)
        automatic_changed, manual_changed = apply_plan(connection, plan)
        result.update(
            {
                "backup": str(backup),
                "automatic_changed": automatic_changed,
                "manual_changed": manual_changed,
            }
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
