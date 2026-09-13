"""Reconcile pending face fragments with their confirmed suggested person.

Dry-run is the default. ``--apply`` creates a SQLite backup before changing the
formal library. Existing embeddings are reused; no photo or face model is read.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FORMAL_DB = (ROOT / "data" / "library.sqlite3").resolve()
WORK_ROOT = (ROOT / "validation" / "work").resolve()
REPORT = ROOT / "validation" / "reports" / "face-candidate-fragment-repair.json"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def open_read_only() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{FORMAL_DB.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def create_backup() -> Path:
    backup_dir = ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"library-{stamp}-before-canonical-face-repair.sqlite3"
    source = sqlite3.connect(str(FORMAL_DB))
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply eligible moves after creating a backup")
    parser.add_argument(
        "--reuse-backup",
        type=Path,
        help="reuse an existing backup under data/backups for a follow-up convergence pass",
    )
    args = parser.parse_args()
    if not FORMAL_DB.is_file():
        raise RuntimeError("正式资料库不存在")

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="face-fragment-repair-", dir=WORK_ROOT) as raw_data:
        os.environ["PHOTO_LIBRARY_DATA"] = raw_data
        os.environ["PHOTO_WEB_ROOT"] = str((ROOT / "web").resolve())
        os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"
        sys.path.insert(0, str(ROOT))
        import app

        with open_read_only() as connection:
            references = connection.execute(
                """SELECT f.embedding,f.person_id,p.confirmed,p.ignored,
                          NULL AS suggested_person_id,
                          0 AS suggested_confirmed,
                          0 AS suggested_ignored
                   FROM faces f
                   JOIN people p ON p.id=f.person_id
                   WHERE f.embedding IS NOT NULL
                     AND (p.confirmed=1 OR p.ignored=1)
                     AND (coalesce(p.ignored,0)=1 OR coalesce(f.ignored,0)=0)
                   ORDER BY f.id"""
            ).fetchall()
            candidates = connection.execute(
                """SELECT f.id AS face_id,f.asset_id,f.person_id AS source_person_id,
                          f.embedding,p.suggested_person_id,
                          target.confirmed AS suggested_confirmed,
                          target.ignored AS suggested_ignored
                   FROM faces f
                   JOIN people p ON p.id=f.person_id
                   JOIN people target ON target.id=p.suggested_person_id
                   WHERE p.confirmed=0 AND p.ignored=0
                     AND f.ignored=0 AND f.embedding IS NOT NULL
                     AND (target.confirmed=1 OR target.ignored=1)
                   ORDER BY f.id"""
            ).fetchall()
            existing = connection.execute(
                """SELECT f.asset_id,f.person_id
                   FROM faces f JOIN people p ON p.id=f.person_id
                   WHERE p.confirmed=1 OR p.ignored=1"""
            ).fetchall()

        index = app.build_face_index(references)
        used_by_asset: dict[int, set[int]] = defaultdict(set)
        for row in existing:
            used_by_asset[int(row["asset_id"])].add(int(row["person_id"]))

        eligible = []
        reasons = Counter()
        source_counts: dict[int, Counter] = defaultdict(Counter)
        for row in candidates:
            asset_id = int(row["asset_id"])
            target_id = int(row["suggested_person_id"])
            embedding = np.frombuffer(row["embedding"], dtype=np.float32)
            decision = app.choose_face_person(index, embedding, used_by_asset[asset_id])
            reason = "eligible"
            if decision["person_id"] != target_id or decision["route"] not in {"confirmed", "ignored"}:
                if target_id in used_by_asset[asset_id]:
                    reason = "same_person_already_in_photo"
                elif decision["best_score"] is None or decision["best_score"] < app.FACE_GROUP_THRESHOLD:
                    reason = "below_threshold"
                elif decision["margin"] is not None and decision["margin"] < app.FACE_MATCH_MARGIN:
                    reason = "different_people_too_close"
                else:
                    reason = "best_person_changed"
            if reason == "eligible":
                item = {
                    "face_id": int(row["face_id"]),
                    "asset_id": asset_id,
                    "source_person_id": int(row["source_person_id"]),
                    "target_person_id": target_id,
                    "target_ignored": int(row["suggested_ignored"] or 0),
                    "score": round(float(decision["best_score"]), 6),
                    "margin": round(float(decision["margin"]), 6),
                }
                eligible.append(item)
                used_by_asset[asset_id].add(target_id)
            reasons[reason] += 1
            source_counts[int(row["source_person_id"])][reason] += 1

        target_examples = {}
        for source_id in (70886, 70574):
            target_examples[str(source_id)] = dict(source_counts.get(source_id, {}))

        payload = {
            "verified_at": now(),
            "mode": "apply" if args.apply else "dry-run",
            "thresholds": {
                "match": app.FACE_GROUP_THRESHOLD,
                "different_person_margin": app.FACE_MATCH_MARGIN,
            },
            "reference_faces": index["size"],
            "candidate_faces": len(candidates),
            "result_counts": dict(reasons),
            "eligible_faces": len(eligible),
            "target_example_groups": target_examples,
            "backup": None,
            "applied_faces": 0,
            "deleted_empty_people": 0,
            "integrity": None,
        }

        if args.apply and eligible:
            if args.reuse_backup:
                backup_path = args.reuse_backup.resolve()
                backup_root = (ROOT / "data" / "backups").resolve()
                if backup_root not in backup_path.parents or not backup_path.is_file():
                    raise RuntimeError("复用备份必须是 data/backups 中已存在的 SQLite 文件")
            else:
                backup_path = create_backup()
            payload["backup"] = str(backup_path)
            connection = sqlite3.connect(str(FORMAL_DB))
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            touched_sources: dict[int, int] = {}
            applied = 0
            try:
                connection.execute("BEGIN IMMEDIATE")
                for item in eligible:
                    current = connection.execute(
                        """SELECT f.person_id,p.suggested_person_id
                           FROM faces f JOIN people p ON p.id=f.person_id
                           WHERE f.id=?""",
                        (item["face_id"],),
                    ).fetchone()
                    if (
                        current is None
                        or int(current["person_id"]) != item["source_person_id"]
                        or int(current["suggested_person_id"] or 0) != item["target_person_id"]
                    ):
                        continue
                    connection.execute(
                        "UPDATE faces SET person_id=?,reviewed=1,ignored=? WHERE id=?",
                        (item["target_person_id"], item["target_ignored"], item["face_id"]),
                    )
                    connection.execute(
                        "INSERT INTO edits(created_at,target,before_json,after_json) VALUES (?,?,?,?)",
                        (
                            now(),
                            f"face:{item['face_id']}",
                            json.dumps({"person_id": item["source_person_id"]}, ensure_ascii=False),
                            json.dumps(
                                {
                                    "person_id": item["target_person_id"],
                                    "canonical_face_repair": True,
                                    "score": item["score"],
                                    "margin": item["margin"],
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                    touched_sources[item["source_person_id"]] = item["target_person_id"]
                    applied += 1

                deleted = 0
                for source_id, target_id in touched_sources.items():
                    left = connection.execute(
                        "SELECT count(*) FROM faces WHERE person_id=?", (source_id,)
                    ).fetchone()[0]
                    if left == 0:
                        connection.execute(
                            "UPDATE people SET suggested_person_id=? WHERE suggested_person_id=?",
                            (target_id, source_id),
                        )
                        connection.execute("DELETE FROM people WHERE id=?", (source_id,))
                        deleted += 1
                connection.commit()
                payload["applied_faces"] = applied
                payload["deleted_empty_people"] = deleted
                payload["integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.apply and payload["integrity"] != "ok":
            raise AssertionError("正式库完整性检查未通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
