"""B01-R2 red/green checks for committed face state and staged crop ownership.

The suite imports the real ``app`` module only after assigning a unique data
root below ``validation/work``. Face inference is replaced with deterministic
fake engines, and failures are injected at the real DB commit / ``os.replace``
boundaries. It never imports against the formal ``data`` directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
FORMAL_DATA = (ROOT / "data").resolve()
WORK_PARENT = (ROOT / "validation" / "work").resolve()
sys.path.insert(0, str(ROOT))

from validation import test_b01_consistency as b01


def reset_status_cache(app) -> None:
    with app.STATUS_CACHE_LOCK:
        app.STATUS_CACHE["at"] = 0
        app.STATUS_CACHE["payload"] = None


def empty_engine(calls):
    def get(_image):
        calls["count"] += 1
        return []

    return SimpleNamespace(get=get)


def multi_face_engine(calls):
    import numpy as np

    def get(_image):
        calls["count"] += 1
        return [
            SimpleNamespace(
                bbox=np.array([10, 10, 48, 55]),
                det_score=0.99,
                normed_embedding=b01.vector(101),
            ),
            SimpleNamespace(
                bbox=np.array([62, 58, 108, 112]),
                det_score=0.98,
                normed_embedding=b01.vector(102),
            ),
        ]

    return SimpleNamespace(get=get)


def set_asset_state(app, asset_id: int, state: int, error=None) -> None:
    with app.db() as conn:
        conn.execute(
            "UPDATE assets SET face_state=?,face_error=? WHERE id=?",
            (state, error, asset_id),
        )


def make_unique_fixture(path: Path, color: str) -> None:
    b01.create_image(path, color)
    path.write_bytes(path.read_bytes() + b"\nB01-R2:" + path.name.encode("utf-8"))


def asset_face_state(app, asset_id: int):
    with app.db() as conn:
        asset = dict(
            conn.execute(
                "SELECT face_state,face_error FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
        )
        face_count = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (asset_id,)
        ).fetchone()[0]
        person_count = conn.execute(
            """SELECT count(*) FROM people p
               WHERE EXISTS(SELECT 1 FROM faces f
                            WHERE f.person_id=p.id AND f.asset_id=?)""",
            (asset_id,),
        ).fetchone()[0]
    return asset, face_count, person_count


def case_r2_t01_zero_face_commit_recovery(app, _client, work: Path):
    original = work / "originals" / "r2-t01.jpg"
    b01.create_image(original, "#6a7178")
    asset_id = b01.create_unprocessed_asset(app, original)
    calls = {"count": 0}
    original_engine = app.get_face_engine
    original_db = app.db
    fault = {"armed": True, "injected": False}

    @contextmanager
    def fail_final_state_commit():
        with original_db() as conn:
            yield conn
            row = conn.execute(
                "SELECT face_state FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            if fault["armed"] and row and row["face_state"] == 1:
                fault["armed"] = False
                fault["injected"] = True
                raise sqlite3.OperationalError(
                    "B01-R2 injected final face_state commit failure"
                )

    app.get_face_engine = lambda: empty_engine(calls)
    app.db = fail_final_state_commit
    first_exception = None
    try:
        try:
            app.process_faces(asset_id, original)
        except app.FacePublishPending as exc:
            first_exception = type(exc).__name__
        else:
            raise AssertionError("final state commit fault did not surface")
    finally:
        app.db = original_db

    first_asset, first_faces, first_people = asset_face_state(app, asset_id)
    try:
        retry_result = app.process_faces(asset_id, original)
    finally:
        app.get_face_engine = original_engine
    final_asset, final_faces, final_people = asset_face_state(app, asset_id)

    if not fault["injected"] or first_exception != "FacePublishPending":
        raise AssertionError(f"final commit fault not established: {fault}")
    if (
        first_asset["face_state"] != 2
        or first_faces
        or first_people
        or calls["count"] != 1
        or final_asset["face_state"] != 1
        or final_faces
        or final_people
        or retry_result is not None
    ):
        raise AssertionError(
            "committed zero-face recovery reran inference or changed identity: "
            f"first={first_asset, first_faces, first_people}, "
            f"final={final_asset, final_faces, final_people}, "
            f"calls={calls['count']}, retry={retry_result}"
        )
    return {
        "fault_injected": True,
        "first": {**first_asset, "faces": first_faces, "people": first_people},
        "final": {**final_asset, "faces": final_faces, "people": final_people},
        "engine_calls": calls["count"],
    }


def fresh_zero_recovery(data_root: Path, asset_id: int, original: Path) -> int:
    os.environ["PHOTO_LIBRARY_DATA"] = str(data_root.resolve())
    os.environ["PHOTO_WEB_ROOT"] = str((ROOT / "web").resolve())
    os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"
    sys.path.insert(0, str(ROOT))
    import app

    def forbidden_engine():
        raise AssertionError("fresh zero-face recovery loaded the face engine")

    app.get_face_engine = forbidden_engine
    result = app.process_faces(asset_id, original)
    state, faces, people = asset_face_state(app, asset_id)
    if result is not None or state["face_state"] != 1 or faces or people:
        raise AssertionError(
            f"fresh recovery mismatch: result={result}, state={state}, "
            f"faces={faces}, people={people}"
        )
    print(
        json.dumps(
            {
                "data_dir": str(app.DATA),
                "state": state["face_state"],
                "faces": faces,
                "people": people,
                "engine_loaded": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


def case_r2_t02_fresh_process_zero_recovery(app, _client, work: Path):
    original = work / "originals" / "r2-t02.jpg"
    b01.create_image(original, "#65727f")
    asset_id = b01.create_unprocessed_asset(app, original)
    set_asset_state(app, asset_id, 2, "已提交零人脸结果，完成状态待恢复")
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(Path(__file__).resolve()),
        "--fresh-zero-recovery",
        str(work),
        str(asset_id),
        str(original),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "PHOTO_LIBRARY_DATA": str(work)},
    )
    if completed.returncode:
        set_asset_state(app, asset_id, 1, None)
        raise AssertionError(
            f"fresh process recovery failed ({completed.returncode}): "
            f"{completed.stdout}\n{completed.stderr}"
        )
    state, faces, people = asset_face_state(app, asset_id)
    if state["face_state"] != 1 or faces or people:
        raise AssertionError(f"fresh result not persisted: {state, faces, people}")
    return {
        "command": [Path(command[0]).name, *command[1:4], "<isolated>", str(asset_id), "<synthetic>"],
        "child": json.loads(completed.stdout.strip().splitlines()[-1]),
    }


def case_r2_t03_uncommitted_empty_states(app, _client, work: Path):
    calls = {"count": 0}
    original_engine = app.get_face_engine
    app.get_face_engine = lambda: empty_engine(calls)
    states = {}
    try:
        for initial in (0, -1):
            original = work / "originals" / f"r2-t03-{initial}.jpg"
            b01.create_image(original, "#736a82" if initial == 0 else "#736a83")
            asset_id = b01.create_unprocessed_asset(app, original)
            set_asset_state(
                app,
                asset_id,
                initial,
                "真实识别失败，允许显式重试" if initial == -1 else None,
            )
            app.process_faces(asset_id, original)
            states[str(initial)] = asset_face_state(app, asset_id)[0]["face_state"]
    finally:
        app.get_face_engine = original_engine
    if calls["count"] != 2 or states != {"0": 1, "-1": 1}:
        raise AssertionError(f"uncommitted states skipped inference: {calls}, {states}")
    return {"initial_to_final": states, "engine_calls": calls["count"]}


def face_rows(app, asset_id: int):
    with app.db() as conn:
        rows = conn.execute(
            """SELECT id,person_id,bbox,embedding,score,reviewed,ignored,crop
               FROM faces WHERE asset_id=? ORDER BY id""",
            (asset_id,),
        ).fetchall()
    return [
        {
            **{key: row[key] for key in row.keys() if key != "embedding"},
            "embedding_sha256": __import__("hashlib").sha256(row["embedding"]).hexdigest(),
        }
        for row in rows
    ]


def case_r2_t04_partial_publish_recovery(app, _client, work: Path):
    original = work / "originals" / "r2-t04.jpg"
    b01.create_image(original, "#887061")
    asset_id = b01.create_unprocessed_asset(app, original)
    calls = {"count": 0}
    original_engine = app.get_face_engine
    original_replace = app.os.replace
    replace_calls = {"pending": 0, "injected": False}

    def fail_second_pending(source, target):
        if Path(source).name.startswith(".pending-"):
            replace_calls["pending"] += 1
            if replace_calls["pending"] == 2:
                replace_calls["injected"] = True
                raise OSError("B01-R2 injected second crop publish failure")
        return original_replace(source, target)

    app.get_face_engine = lambda: multi_face_engine(calls)
    app.os.replace = fail_second_pending
    first_exception = None
    try:
        try:
            app.process_faces(asset_id, original)
        except app.FacePublishPending as exc:
            first_exception = type(exc).__name__
        else:
            raise AssertionError("partial publish fault did not surface")
    finally:
        app.os.replace = original_replace
    before = face_rows(app, asset_id)
    first_state = asset_face_state(app, asset_id)[0]

    def forbidden_engine():
        raise AssertionError("committed crop recovery reran face inference")

    app.get_face_engine = forbidden_engine
    try:
        app.process_faces(asset_id, original)
    finally:
        app.get_face_engine = original_engine
    after = face_rows(app, asset_id)
    final_state = asset_face_state(app, asset_id)[0]
    missing = [
        row["crop"]
        for row in after
        if not (app.DATA / "faces" / row["crop"]).exists()
    ]
    if (
        not replace_calls["injected"]
        or first_exception != "FacePublishPending"
        or first_state["face_state"] != 2
        or len(before) != 2
        or before != after
        or final_state["face_state"] != 1
        or missing
        or calls["count"] != 1
    ):
        raise AssertionError(
            f"partial publish recovery mismatch: injected={replace_calls}, "
            f"first={first_state}, final={final_state}, calls={calls}, "
            f"rows_equal={before == after}, missing={missing}"
        )
    return {
        "fault_boundary": "second pending os.replace",
        "faces": len(after),
        "identity_rows_unchanged": True,
        "engine_calls": calls["count"],
        "state": [first_state["face_state"], final_state["face_state"]],
    }


def case_r2_t05_persistent_discovery(app, client, work: Path):
    reset_status_cache(app)
    baseline = client.get("/api/status").json()["stats"]
    asset_ids = []
    fixtures = (
        ("#2255aa", None),
        ("#cc7722", "人脸裁剪待恢复：合成故障"),
    )
    for offset, (color, error) in enumerate(fixtures, start=1):
        original = work / "originals" / f"r2-t05-{offset}.jpg"
        make_unique_fixture(original, color)
        asset_id = b01.create_unprocessed_asset(app, original)
        set_asset_state(app, asset_id, 2, error)
        asset_ids.append(asset_id)
    reset_status_cache(app)
    try:
        status = client.get("/api/status").json()["stats"]
        errors = client.get("/api/photos", params={"filter": "errors", "limit": 20}).json()
        items = [row for row in errors["items"] if row["id"] in asset_ids]
        if status.get("faces_pending") != baseline.get("faces_pending"):
            raise AssertionError(f"publish pending mixed into inference pending: {status}")
        if (
            status.get("face_publish_pending", 0)
            != baseline.get("face_publish_pending", 0) + 2
            or status.get("face_publish_errors", 0)
            != baseline.get("face_publish_errors", 0) + 1
            or len(items) != 2
            or {row["face_state"] for row in items} != {2}
            or {row.get("face_status") for row in items}
            != {"committed_pending_publish"}
        ):
            raise AssertionError(
                f"state=2 not persistently discoverable: status={status}, items={items}"
            )
        return {
            "status_delta": {
                "faces_pending": 0,
                "face_publish_pending": 2,
                "face_publish_errors": 1,
            },
            "error_filter_ids": asset_ids,
            "job_errors_required": False,
        }
    finally:
        with app.db() as conn:
            conn.executemany(
                "UPDATE assets SET face_state=1,face_error=NULL WHERE id=?",
                [(asset_id,) for asset_id in asset_ids],
            )
        reset_status_cache(app)


def case_r2_t06_recovery_cache_converges(app, client, work: Path):
    reset_status_cache(app)
    baseline = client.get("/api/status").json()["stats"].get("face_publish_pending", 0)
    original = work / "originals" / "r2-t06.jpg"
    b01.create_image(original, "#61756b")
    asset_id = b01.create_unprocessed_asset(app, original)
    set_asset_state(app, asset_id, 2, "已提交零人脸结果，完成状态待恢复")
    reset_status_cache(app)
    before = client.get("/api/status").json()["stats"]
    original_engine = app.get_face_engine

    def forbidden_engine():
        raise AssertionError("status convergence recovery loaded model")

    app.get_face_engine = forbidden_engine
    try:
        app.process_faces(asset_id, original)
    finally:
        app.get_face_engine = original_engine
    after = client.get("/api/status").json()["stats"]
    errors = client.get("/api/photos", params={"filter": "errors", "limit": 200}).json()
    if (
        before.get("face_publish_pending") != baseline + 1
        or after.get("face_publish_pending") != baseline
        or asset_id in {row["id"] for row in errors["items"]}
    ):
        raise AssertionError(
            f"status cache/query did not converge: before={before}, after={after}"
        )
    return {
        "pending": [before["face_publish_pending"], after["face_publish_pending"]],
        "removed_from_error_filter": True,
    }


def seed_cleanup_asset(app, original: Path, *, policy: str, faces: int = 2):
    make_unique_fixture(original, "#826f63")
    asset_id = b01.create_unprocessed_asset(app, original)
    face_ids = []
    people = []
    with app.db() as conn:
        conn.execute(
            """UPDATE assets
               SET excluded=1,derivative_policy=?,face_state=2
               WHERE id=?""",
            (policy, asset_id),
        )
        for offset in range(faces):
            person_id = conn.execute(
                "INSERT INTO people(name,confirmed) VALUES (?,1)",
                (f"R2 cleanup fixture {asset_id}-{offset}",),
            ).lastrowid
            face_id = conn.execute(
                """INSERT INTO faces(
                     asset_id,person_id,bbox,embedding,score,reviewed,ignored,crop
                   ) VALUES (?,?,?,?,0.99,1,0,?)""",
                (
                    asset_id,
                    person_id,
                    "[10,10,60,60,120,120]",
                    b01.vector(200 + offset).tobytes(),
                    "placeholder.jpg",
                ),
            ).lastrowid
            conn.execute(
                "UPDATE faces SET crop=? WHERE id=?", (f"{face_id}.jpg", face_id)
            )
            conn.execute(
                "UPDATE people SET cover_face_id=? WHERE id=?",
                (face_id, person_id),
            )
            face_ids.append(face_id)
            people.append(person_id)
    files = {}
    for face_id in face_ids:
        owned = [
            app.DATA / "faces" / f"{face_id}.jpg",
            app.DATA / "faces" / f".pending-{face_id}.jpg",
            app.DATA / "faces" / f".recover-{face_id}-fixture.jpg",
        ]
        for path in owned:
            b01.create_image(path, "#aa8866")
        files[face_id] = owned
    return {"asset_id": asset_id, "face_ids": face_ids, "people": people, "files": files}


def case_r2_t07_partial_publish_purge(app, _client, work: Path):
    seeded = seed_cleanup_asset(
        app, work / "originals" / "r2-t07.jpg", policy="purge", faces=2
    )
    before_revision = app.face_index_revision()
    released = app.cleanup_asset_cache(seeded["asset_id"])
    with app.db() as conn:
        faces = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (seeded["asset_id"],)
        ).fetchone()[0]
        covers = conn.execute(
            "SELECT count(*) FROM people WHERE cover_face_id IS NOT NULL AND id IN (?,?)",
            tuple(seeded["people"]),
        ).fetchone()[0]
        state = conn.execute(
            "SELECT face_state FROM assets WHERE id=?", (seeded["asset_id"],)
        ).fetchone()[0]
    residual = [
        path.name for paths in seeded["files"].values() for path in paths if path.exists()
    ]
    if (
        faces
        or covers
        or state != 0
        or residual
        or released <= 0
        or app.face_index_revision() <= before_revision
    ):
        raise AssertionError(
            f"owned purge incomplete: faces={faces}, covers={covers}, state={state}, "
            f"residual={residual}, released={released}"
        )
    return {
        "faces_removed": len(seeded["face_ids"]),
        "owned_files_removed": sum(len(paths) for paths in seeded["files"].values()),
        "released_bytes": released,
        "cover_refs": covers,
        "state": state,
    }


def case_r2_t08_cross_asset_and_preserve(app, _client, work: Path):
    target = seed_cleanup_asset(
        app, work / "originals" / "r2-t08-target.jpg", policy="purge", faces=1
    )
    preserve = seed_cleanup_asset(
        app, work / "originals" / "r2-t08-preserve.jpg", policy="preserve", faces=1
    )
    preserve_paths = preserve["files"][preserve["face_ids"][0]]
    released = app.cleanup_asset_cache(target["asset_id"])
    preserved_release = app.cleanup_asset_cache(preserve["asset_id"])
    repeated = app.cleanup_asset_cache(target["asset_id"])
    boundary = seed_cleanup_asset(
        app, work / "originals" / "r2-t08-boundary.jpg", policy="purge", faces=1
    )
    outside = app.DATA / "outside-owned-folder.jpg"
    b01.create_image(outside, "#bb5522")
    with app.db() as conn:
        conn.execute(
            "UPDATE faces SET crop='../outside-owned-folder.jpg' WHERE id=?",
            (boundary["face_ids"][0],),
        )
    boundary_error = None
    try:
        app.cleanup_asset_cache(boundary["asset_id"])
    except RuntimeError as exc:
        boundary_error = str(exc)
    target_residual = [
        path.name for path in target["files"][target["face_ids"][0]] if path.exists()
    ]
    missing_preserve = [path.name for path in preserve_paths if not path.exists()]
    with app.db() as conn:
        preserve_faces = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (preserve["asset_id"],)
        ).fetchone()[0]
        boundary_faces = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (boundary["asset_id"],)
        ).fetchone()[0]
    if (
        target_residual
        or missing_preserve
        or preserve_faces != 1
        or boundary_faces != 1
        or not outside.exists()
        or "缓存路径不在本项目指定目录内" not in (boundary_error or "")
        or preserved_release != 0
        or repeated != 0
        or released <= 0
    ):
        raise AssertionError(
            f"cross-asset cleanup mismatch: target={target_residual}, "
            f"missing_preserve={missing_preserve}, preserve_faces={preserve_faces}, "
            f"boundary={boundary_error, boundary_faces, outside.exists()}, "
            f"release={released, preserved_release, repeated}"
        )
    return {
        "target_removed": True,
        "preserve_files_retained": len(preserve_paths),
        "preserve_faces": preserve_faces,
        "managed_folder_boundary_rejected": True,
        "repeat_release": repeated,
    }


def case_r2_t09_cleanup_io_retry(app, _client, work: Path):
    target = seed_cleanup_asset(
        app, work / "originals" / "r2-t09.jpg", policy="purge", faces=1
    )
    face_id = target["face_ids"][0]
    pending_name = f".pending-{face_id}.jpg"
    original_remove = app.remove_generated
    injected = {"done": False}

    def fail_pending_once(path, folder):
        if folder == "faces" and Path(path).name == pending_name and not injected["done"]:
            injected["done"] = True
            raise OSError("B01-R2 injected pending cleanup failure")
        return original_remove(path, folder)

    app.remove_generated = fail_pending_once
    first_error = None
    try:
        try:
            app.cleanup_asset_cache(target["asset_id"])
        except OSError as exc:
            first_error = str(exc)
    finally:
        app.remove_generated = original_remove
    first_state, first_faces, _ = asset_face_state(app, target["asset_id"])
    first_residual = [
        path.name for path in target["files"][face_id] if path.exists()
    ]
    released = app.cleanup_asset_cache(target["asset_id"])
    repeated = app.cleanup_asset_cache(target["asset_id"])
    final_state, final_faces, _ = asset_face_state(app, target["asset_id"])
    final_residual = [
        path.name for path in target["files"][face_id] if path.exists()
    ]
    if (
        not injected["done"]
        or not first_error
        or first_state["face_state"] != 2
        or first_faces != 1
        or pending_name not in first_residual
        or final_state["face_state"] != 0
        or final_faces
        or final_residual
        or released <= 0
        or repeated != 0
    ):
        raise AssertionError(
            f"cleanup retry mismatch: injected={injected}, error={first_error}, "
            f"first={first_state, first_faces, first_residual}, "
            f"final={final_state, final_faces, final_residual}, "
            f"released={released}, repeated={repeated}"
        )
    return {
        "fault_injected": True,
        "first_state": first_state["face_state"],
        "first_residual": first_residual,
        "final_state": final_state["face_state"],
        "final_residual": final_residual,
        "retry_released_bytes": released,
    }


def run_suite(output: Path | None) -> int:
    WORK_PARENT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="b01-r2-", dir=str(WORK_PARENT))).resolve()
    b01.assert_isolated(work)
    os.environ["PHOTO_LIBRARY_DATA"] = str(work)
    os.environ["PHOTO_WEB_ROOT"] = str((ROOT / "web").resolve())
    os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"
    sys.path.insert(0, str(ROOT))

    import app
    from fastapi.testclient import TestClient

    if app.DATA.resolve() != work or app.DATA.resolve() == FORMAL_DATA:
        raise AssertionError(f"unsafe app DATA: {app.DATA}")
    client = TestClient(app.app)
    status = client.get("/api/status").json()
    if Path(status["capabilities"]["data_dir"]).resolve() != work:
        raise AssertionError(f"status DATA mismatch: {status}")

    cases = [
        ("R2-T01", case_r2_t01_zero_face_commit_recovery),
        ("R2-T02", case_r2_t02_fresh_process_zero_recovery),
        ("R2-T03", case_r2_t03_uncommitted_empty_states),
        ("R2-T04", case_r2_t04_partial_publish_recovery),
        ("R2-T05", case_r2_t05_persistent_discovery),
        ("R2-T06", case_r2_t06_recovery_cache_converges),
        ("R2-T07", case_r2_t07_partial_publish_purge),
        ("R2-T08", case_r2_t08_cross_asset_and_preserve),
        ("R2-T09", case_r2_t09_cleanup_io_retry),
    ]
    results = []
    for case_id, case in cases:
        try:
            detail = case(app, client, work)
            results.append({"id": case_id, "status": "PASS", "detail": detail})
        except Exception as exc:
            results.append(
                {
                    "id": case_id,
                    "status": "FAIL",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    with app.db() as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    payload = {
        "data_dir": str(work),
        "formal_data": str(FORMAL_DATA),
        "app_data_verified": app.DATA.resolve() == work,
        "status_data_verified": Path(
            status["capabilities"]["data_dir"]
        ).resolve()
        == work,
        "real_face_engine_used": False,
        "integrity_check": integrity,
        "foreign_key_check": foreign_keys,
        "cases": results,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    print(text)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0 if all(row["status"] == "PASS" for row in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--fresh-zero-recovery",
        nargs=3,
        metavar=("DATA_ROOT", "ASSET_ID", "ORIGINAL"),
    )
    args = parser.parse_args()
    if args.fresh_zero_recovery:
        data_root, asset_id, original = args.fresh_zero_recovery
        return fresh_zero_recovery(Path(data_root), int(asset_id), Path(original))
    return run_suite(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
