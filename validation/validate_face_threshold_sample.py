"""Read-only leave-one-out check of face auto-match thresholds on the formal embeddings."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FORMAL_DB = (ROOT / "data" / "library.sqlite3").resolve()
WORK_ROOT = (ROOT / "validation" / "work").resolve()
REPORT = ROOT / "validation" / "reports" / "face-threshold-sample.json"
MAX_QUERIES_PER_STATE = 120


def state_of(row) -> str:
    return "ignored" if row["ignored"] else "confirmed" if row["confirmed"] else "pending"


def main() -> int:
    if not FORMAL_DB.is_file():
        raise RuntimeError("正式资料库不存在，无法执行只读阈值抽样")
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="face-threshold-", dir=WORK_ROOT) as raw_data:
        os.environ["PHOTO_LIBRARY_DATA"] = raw_data
        os.environ["PHOTO_WEB_ROOT"] = str((ROOT / "web").resolve())
        sys.path.insert(0, str(ROOT))
        import app

        connection = sqlite3.connect(f"file:{FORMAL_DB.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """WITH ranked AS (
                       SELECT f.id,f.asset_id,f.embedding,f.person_id,p.confirmed,p.ignored,
                              row_number() OVER (PARTITION BY f.person_id ORDER BY f.id DESC) AS rn
                       FROM faces f JOIN people p ON p.id=f.person_id
                       WHERE f.embedding IS NOT NULL
                         AND (coalesce(p.ignored,0)=1 OR coalesce(f.ignored,0)=0)
                   )
                   SELECT id,asset_id,embedding,person_id,confirmed,ignored
                   FROM ranked WHERE rn<=5 ORDER BY person_id,id DESC"""
            ).fetchall()
        finally:
            connection.close()

        grouped: dict[int, list] = defaultdict(list)
        asset_people: dict[int, set[int]] = defaultdict(set)
        for item in rows:
            grouped[int(item["person_id"])].append(item)
            asset_people[int(item["asset_id"])].add(int(item["person_id"]))

        queries = []
        reference_rows = []
        state_counts = defaultdict(int)
        for person_id in sorted(grouped):
            faces = grouped[person_id]
            state = state_of(faces[0])
            reserve_query = len(faces) >= 2 and state_counts[state] < MAX_QUERIES_PER_STATE
            if reserve_query:
                queries.append(faces[0])
                state_counts[state] += 1
                references = faces[1:]
            else:
                references = faces[:4]
            reference_rows.extend({
                "embedding": item["embedding"],
                "person_id": int(item["person_id"]),
                "confirmed": int(item["confirmed"]),
                "ignored": int(item["ignored"]),
            } for item in references[:4])

        started = time.perf_counter()
        index = app.build_face_index(reference_rows)
        build_ms = (time.perf_counter() - started) * 1000
        results = defaultdict(lambda: {"queries": 0, "correct_auto": 0, "conservative": 0, "wrong_auto": 0, "same_pending": 0, "other_pending": 0})
        worst_wrong = []
        timings = []
        same_asset_candidates_excluded = 0
        for query in queries:
            embedding = np.frombuffer(query["embedding"], dtype=np.float32)
            expected_person = int(query["person_id"])
            # Leave-one-out sampling must not let another face from the same photo
            # act as historical identity evidence. Production sees a new asset only
            # once and also forbids assigning one person twice within that asset.
            used_people = asset_people[int(query["asset_id"])] - {expected_person}
            same_asset_candidates_excluded += len(used_people)
            started = time.perf_counter()
            decision = app.choose_face_person(index, embedding, used_people=used_people)
            timings.append((time.perf_counter() - started) * 1000)
            expected_state = state_of(query)
            bucket = results[expected_state]
            bucket["queries"] += 1
            direct = decision["route"] in {"confirmed", "ignored"}
            if expected_state in {"confirmed", "ignored"}:
                if direct and decision["person_id"] == expected_person:
                    bucket["correct_auto"] += 1
                elif direct:
                    bucket["wrong_auto"] += 1
                    worst_wrong.append({
                        "query_face_id": int(query["id"]),
                        "expected_person_id": expected_person,
                        "actual_person_id": int(decision["person_id"]),
                        "expected_state": expected_state,
                        "actual_state": decision["route"],
                        "score": round(decision["best_score"], 4),
                        "margin": round(decision["margin"], 4),
                    })
                else:
                    bucket["conservative"] += 1
            else:
                if decision["route"] == "pending" and decision["person_id"] == expected_person:
                    bucket["same_pending"] += 1
                elif decision["route"] == "pending":
                    bucket["other_pending"] += 1
                elif direct:
                    bucket["wrong_auto"] += 1
                else:
                    bucket["conservative"] += 1

        named_wrong = results["confirmed"]["wrong_auto"]
        ignored_wrong = results["ignored"]["wrong_auto"]
        report = {
            "passed": named_wrong == 0 and ignored_wrong == 0,
            "source": "formal embeddings opened read-only; no paths or names recorded",
            "thresholds": {
                "group": app.FACE_GROUP_THRESHOLD,
                "auto": app.FACE_AUTO_MATCH_THRESHOLD,
                "margin": app.FACE_AUTO_MATCH_MARGIN,
            },
            "sample": {
                "people_in_index": len(index["unique_person_ids"]),
                "reference_faces": index["size"],
                "query_faces": len(queries),
                "same_asset_candidate_people_excluded": same_asset_candidates_excluded,
                "by_state": dict(results),
            },
            "performance": {
                "build_ms": round(build_ms, 3),
                "mean_query_ms": round(sum(timings) / len(timings), 3) if timings else 0,
                "max_query_ms": round(max(timings), 3) if timings else 0,
            },
            "wrong_auto_examples_without_identity": worst_wrong[:20],
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["passed"]:
            raise AssertionError("只读样本发现自动错归，需要提高阈值或 margin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
