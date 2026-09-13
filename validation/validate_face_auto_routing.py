"""Isolated checks for conservative face routing; never opens the formal library."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = (ROOT / "validation" / "work").resolve()
REPORT = ROOT / "validation" / "reports" / "face-auto-routing.json"


def check(condition: bool, message: str, checks: list[str]) -> None:
    if not condition:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def vector(score: float, dim: int = 4) -> np.ndarray:
    result = np.zeros(dim, dtype=np.float32)
    result[0] = score
    result[1] = math.sqrt(max(0.0, 1.0 - score * score))
    return result


def row(
    person_id: int,
    score: float,
    *,
    confirmed: bool = False,
    ignored: bool = False,
    suggested_person_id: int | None = None,
    suggested_confirmed: bool = False,
    suggested_ignored: bool = False,
) -> dict:
    return {
        "embedding": vector(score).tobytes(),
        "person_id": person_id,
        "confirmed": int(confirmed),
        "ignored": int(ignored),
        "suggested_person_id": suggested_person_id,
        "suggested_confirmed": int(suggested_confirmed),
        "suggested_ignored": int(suggested_ignored),
    }


def main() -> int:
    checks: list[str] = []
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="face-auto-routing-", dir=WORK_ROOT) as raw_data:
        data = Path(raw_data).resolve()
        if WORK_ROOT not in data.parents:
            raise RuntimeError("隔离资料库不在 validation/work 内")
        os.environ["PHOTO_LIBRARY_DATA"] = str(data)
        os.environ["PHOTO_WEB_ROOT"] = str((ROOT / "web").resolve())
        os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"
        sys.path.insert(0, str(ROOT))
        import app
        from fastapi.testclient import TestClient

        query = np.array([1, 0, 0, 0], dtype=np.float32)

        def choose(rows, used=()):
            return app.choose_face_person(app.build_face_index(rows), query, used)

        direct_named = choose([row(1, 0.55, confirmed=True), row(2, 0.40)])
        check(direct_named["route"] == "confirmed" and direct_named["person_id"] == 1, "达到统一阈值时直接进入已命名人物", checks)

        below_threshold = choose([row(1, 0.519, confirmed=True), row(2, 0.30)])
        check(below_threshold["route"] == "new" and below_threshold["person_id"] is None, "低于统一阈值时保持待确认", checks)

        close_named = choose([row(1, 0.72, confirmed=True), row(2, 0.69, confirmed=True)])
        check(
            close_named["route"] == "suggested"
            and close_named["suggested_person_id"] == 1
            and close_named["margin"] < app.FACE_MATCH_MARGIN,
            "两个真正不同人物接近时保留待确认",
            checks,
        )

        canonical_named = choose([
            row(1, 0.74, confirmed=True),
            row(10, 0.78, suggested_person_id=1, suggested_confirmed=True),
            row(2, 0.60, confirmed=True),
        ])
        check(
            canonical_named["route"] == "confirmed"
            and canonical_named["person_id"] == 1
            and len(app.build_face_index([
                row(1, 0.74, confirmed=True),
                row(10, 0.78, suggested_person_id=1, suggested_confirmed=True),
            ])["unique_person_ids"]) == 1,
            "正式人物与指向他的待确认碎片折叠为同一人物",
            checks,
        )

        direct_ignored = choose([row(3, 0.74, ignored=True), row(1, 0.58, confirmed=True)])
        check(direct_ignored["route"] == "ignored" and direct_ignored["person_id"] == 3, "达到统一阈值的路人继续进入原路人组", checks)

        unknown = choose([row(1, 0.40, confirmed=True), row(2, 0.35)])
        check(unknown["route"] == "new" and unknown["person_id"] is None, "陌生人进入新的待确认人物", checks)

        pending = choose([row(4, 0.55), row(1, 0.42, confirmed=True)])
        check(pending["route"] == "pending" and pending["person_id"] == 4, "未命名候选继续按聚类阈值归组", checks)

        alternative = choose([
            row(1, 0.80, confirmed=True),
            row(2, 0.73, confirmed=True),
            row(3, 0.55, confirmed=True),
        ], used={1})
        check(alternative["route"] == "confirmed" and alternative["person_id"] == 2, "同照片已占用第一人物时可尝试其他可靠候选", checks)

        state_precedence = choose([row(5, 0.76, confirmed=True, ignored=True), row(1, 0.50, confirmed=True)])
        check(state_precedence["route"] == "ignored", "人物同时带 confirmed 与 ignored 时按路人状态处理", checks)

        person_level = choose([
            row(1, 0.72, confirmed=True),
            row(1, 0.71, confirmed=True),
            row(2, 0.60, confirmed=True),
        ])
        check(person_level["route"] == "confirmed" and person_level["person_id"] == 1, "同一人物的多张脸共同构成人物级候选", checks)

        with app.db() as connection:
            ignored_person = connection.execute("INSERT INTO people(name,confirmed,ignored) VALUES ('路人',0,1)").lastrowid
            hidden_normal = connection.execute("INSERT INTO people(name,confirmed,ignored) VALUES ('待核对',0,0)").lastrowid
            suggested_target = connection.execute("INSERT INTO people(name,confirmed) VALUES ('候选目标',1)").lastrowid
            suggested_fragment = connection.execute(
                "INSERT INTO people(suggested_person_id) VALUES (?)", (suggested_target,)
            ).lastrowid
            for pid, face_ignored in ((ignored_person, 1), (hidden_normal, 1), (suggested_fragment, 0)):
                connection.execute(
                    "INSERT INTO assets(sha256,metadata,face_state,created_at) VALUES (?,?,1,?)",
                    (f"{pid:064x}", "{}", app.now()),
                )
                aid = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                connection.execute(
                    "INSERT INTO faces(asset_id,person_id,bbox,embedding,score,ignored) VALUES (?,?,?,?,?,?)",
                    (aid, pid, "[0,0,20,20,100,100]", vector(0.9).tobytes(), 0.95, face_ignored),
                )
        app.invalidate_face_index()
        loaded = app.load_face_index()
        check(ignored_person in loaded["unique_person_ids"], "用户明确标记的路人人脸会进入匹配索引", checks)
        check(hidden_normal not in loaded["unique_person_ids"], "普通人物中单独忽略的人脸不会进入匹配索引", checks)
        check(suggested_fragment not in loaded["unique_person_ids"], "仍有歧义的可能人物只作提示，不进入自动匹配索引", checks)

        client = TestClient(app.app)
        response = client.patch(f"/api/people/{ignored_person}", json={"name": "重新认识的人", "alias": ""})
        check(response.status_code == 200, "路人可以直接重新命名", checks)
        with app.db() as connection:
            restored = connection.execute("SELECT confirmed,ignored FROM people WHERE id=?", (ignored_person,)).fetchone()
            face_flags = connection.execute("SELECT DISTINCT ignored,reviewed FROM faces WHERE person_id=?", (ignored_person,)).fetchall()
        check(tuple(restored) == (1, 0) and all(tuple(item) == (0, 1) for item in face_flags), "重新命名同时清除人脸 ignored 标记并保留已确认状态", checks)

        ignored_embedding = np.array([0, 1, 0, 0], dtype=np.float32)
        with app.db() as connection:
            route_ignored = connection.execute("INSERT INTO people(name,confirmed,ignored) VALUES ('路人',0,1)").lastrowid
            reference_asset = connection.execute(
                "INSERT INTO assets(sha256,metadata,face_state,created_at) VALUES (?,?,1,?)",
                ("a" * 64, "{}", app.now()),
            ).lastrowid
            connection.execute(
                "INSERT INTO faces(asset_id,person_id,bbox,embedding,score,reviewed,ignored) VALUES (?,?,?,?,?,1,1)",
                (reference_asset, route_ignored, "[0,0,20,20,100,100]", ignored_embedding.tobytes(), 0.99),
            )
            routed_asset = connection.execute(
                "INSERT INTO assets(sha256,metadata,face_state,created_at) VALUES (?,?,0,?)",
                ("b" * 64, "{}", app.now()),
            ).lastrowid
            people_before = connection.execute("SELECT count(*) FROM people").fetchone()[0]
        routed_image = data / "route-ignored.jpg"
        Image.new("RGB", (100, 100), "white").save(routed_image)
        original_engine = app.get_face_engine
        app.get_face_engine = lambda: SimpleNamespace(get=lambda _: [SimpleNamespace(
            bbox=np.array([10, 10, 70, 70]), det_score=0.99, normed_embedding=ignored_embedding
        )])
        app.invalidate_face_index()
        try:
            app.process_faces(routed_asset, routed_image)
        finally:
            app.get_face_engine = original_engine
        with app.db() as connection:
            routed_face = connection.execute("SELECT person_id,ignored,reviewed FROM faces WHERE asset_id=?", (routed_asset,)).fetchone()
            people_after = connection.execute("SELECT count(*) FROM people").fetchone()[0]
        check(tuple(routed_face) == (route_ignored, 1, 1) and people_after == people_before, "真实 process_faces 高置信度路人不新建人物并保留路人标记", checks)

        medium_query = np.array([0, 0, 1, 0], dtype=np.float32)
        medium_embedding = np.array([math.sqrt(1 - 0.55**2), 0, 0.55, 0], dtype=np.float32)
        with app.db() as connection:
            route_named = connection.execute("INSERT INTO people(name,confirmed,ignored) VALUES ('候选姓名',1,0)").lastrowid
            reference_asset = connection.execute(
                "INSERT INTO assets(sha256,metadata,face_state,created_at) VALUES (?,?,1,?)",
                ("c" * 64, "{}", app.now()),
            ).lastrowid
            connection.execute(
                "INSERT INTO faces(asset_id,person_id,bbox,embedding,score,reviewed,ignored) VALUES (?,?,?,?,?,1,0)",
                (reference_asset, route_named, "[0,0,20,20,100,100]", medium_embedding.tobytes(), 0.99),
            )
            suggested_asset = connection.execute(
                "INSERT INTO assets(sha256,metadata,face_state,created_at) VALUES (?,?,0,?)",
                ("d" * 64, "{}", app.now()),
            ).lastrowid
            people_before = connection.execute("SELECT count(*) FROM people").fetchone()[0]
        suggested_image = data / "route-suggested.jpg"
        Image.new("RGB", (100, 100), "white").save(suggested_image)
        app.get_face_engine = lambda: SimpleNamespace(get=lambda _: [SimpleNamespace(
            bbox=np.array([10, 10, 70, 70]), det_score=0.99, normed_embedding=medium_query
        )])
        app.invalidate_face_index()
        try:
            app.process_faces(suggested_asset, suggested_image)
        finally:
            app.get_face_engine = original_engine
        with app.db() as connection:
            routed_named_face = connection.execute("SELECT person_id,ignored,reviewed FROM faces WHERE asset_id=?", (suggested_asset,)).fetchone()
            people_after = connection.execute("SELECT count(*) FROM people").fetchone()[0]
        check(
            tuple(routed_named_face) == (route_named, 0, 1) and people_after == people_before,
            "真实 process_faces 达到统一阈值后直接进入已命名人物",
            checks,
        )

        with app.db() as connection:
            done_asset = connection.execute(
                "INSERT INTO assets(sha256,metadata,face_state,created_at) VALUES (?,?,1,?)",
                ("f" * 64, "{}", app.now()),
            ).lastrowid
        original_engine = app.get_face_engine
        app.get_face_engine = lambda: (_ for _ in ()).throw(AssertionError("face engine should not load"))
        try:
            app.process_faces(done_asset, data / "does-not-exist.jpg")
        finally:
            app.get_face_engine = original_engine
        check(True, "face_state=1 的照片在加载模型和解码前直接跳过", checks)

        rows = [row(pid, 0.40 + (pid % 20) * 0.02, confirmed=pid % 3 == 0, ignored=pid % 7 == 0)
                for pid in range(1, 5001) for _ in range(4)]
        large_index = app.build_face_index(rows)
        started = time.perf_counter()
        large_result = app.choose_face_person(large_index, query)
        elapsed_ms = (time.perf_counter() - started) * 1000
        check(len(large_index["unique_person_ids"]) == 5000 and elapsed_ms < 1000, "5000 人、20000 张脸的人物级匹配在 1 秒内完成", checks)

        report = {
            "passed": True,
            "data_dir": str(data),
            "thresholds": {
                "match": app.FACE_GROUP_THRESHOLD,
                "different_person_margin": app.FACE_MATCH_MARGIN,
            },
            "performance": {"people": 5000, "faces": 20000, "elapsed_ms": round(elapsed_ms, 3), "route": large_result["route"]},
            "checks": checks,
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FACE_AUTO_ROUTING_OK", len(checks), "checks", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
