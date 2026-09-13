"""B01 red/green integration checks for F01, F02, and F03.

The process imports ``app`` only after assigning a unique synthetic data root.
It calls the current API/domain functions and real SQLite schema; only face
inference and the explicit post-commit index fault are replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
FORMAL_DATA = (ROOT / "data").resolve()
WORK_PARENT = (ROOT / "validation" / "work").resolve()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_isolated(path: Path) -> None:
    resolved = path.resolve()
    if resolved == FORMAL_DATA or not resolved.is_relative_to(WORK_PARENT):
        raise AssertionError(f"unsafe test data path: {resolved}")
    if FORMAL_DATA.exists() and resolved.exists() and os.path.samefile(resolved, FORMAL_DATA):
        raise AssertionError("test data aliases formal data")


def vector(seed: int):
    import numpy as np

    result = np.zeros(512, dtype=np.float32)
    result[seed % 512] = 1.0
    return result


def create_image(path: Path, color: str) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (120, 120), color).save(path, format="JPEG")


def seed_face_asset(app, path: Path, *, name: str, reviewed: int = 1):
    digest = sha256(path)
    emb = vector(len(name))
    stat = path.stat()
    with app.db() as conn:
        person_id = conn.execute(
            "INSERT INTO people(name,confirmed) VALUES (?,1)", (name,)
        ).lastrowid
        asset_id = conn.execute(
            """INSERT INTO assets(sha256,metadata,face_state,created_at)
               VALUES (?,?,1,?)""",
            (digest, "{}", app.now()),
        ).lastrowid
        conn.execute(
            """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
               VALUES (?,?,?,?,?,1,0)""",
            (
                asset_id,
                str(path),
                stat.st_size,
                stat.st_mtime_ns,
                app.now(),
            ),
        )
        face_id = conn.execute(
            """INSERT INTO faces(asset_id,person_id,bbox,embedding,score,reviewed,ignored,crop)
               VALUES (?,?,?,?,?,?,0,?)""",
            (
                asset_id,
                person_id,
                "[20,20,80,80,120,120]",
                emb.tobytes(),
                0.99,
                reviewed,
                "pending.jpg",
            ),
        ).lastrowid
        conn.execute(
            "UPDATE faces SET crop=? WHERE id=?", (f"{face_id}.jpg", face_id)
        )
        conn.execute(
            "UPDATE people SET cover_face_id=? WHERE id=?", (face_id, person_id)
        )
    create_image(app.DATA / "faces" / f"{face_id}.jpg", "#9f7354")
    return {
        "asset_id": asset_id,
        "person_id": person_id,
        "face_id": face_id,
        "embedding": emb.tobytes(),
        "digest": digest,
    }


def snapshot_identity(app, asset_id: int):
    with app.db() as conn:
        face = conn.execute(
            """SELECT f.id,f.person_id,f.embedding,f.reviewed,f.ignored,f.crop,
                      p.cover_face_id
               FROM faces f JOIN people p ON p.id=f.person_id
               WHERE f.asset_id=? ORDER BY f.id""",
            (asset_id,),
        ).fetchall()
        asset = conn.execute(
            "SELECT excluded,exclude_reason,face_state FROM assets WHERE id=?",
            (asset_id,),
        ).fetchone()
    return {
        "faces": [
            {
                **{key: row[key] for key in row.keys() if key != "embedding"},
                "embedding_sha256": hashlib.sha256(row["embedding"]).hexdigest(),
            }
            for row in face
        ],
        "asset": dict(asset),
    }


def restart_identity(data: Path, asset_id: int):
    code = r'''
import hashlib, json, os, sys
from pathlib import Path
os.environ["PHOTO_LIBRARY_DATA"] = sys.argv[1]
os.environ["PHOTO_WEB_ROOT"] = sys.argv[2]
os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"
sys.path.insert(0, sys.argv[3])
import app
with app.db() as conn:
    rows = conn.execute(
        """SELECT f.id,f.person_id,f.embedding,f.reviewed,f.ignored,f.crop,
                  p.cover_face_id
           FROM faces f JOIN people p ON p.id=f.person_id
           WHERE f.asset_id=? ORDER BY f.id""",
        (int(sys.argv[4]),),
    ).fetchall()
print(json.dumps([
    {
        **{key: row[key] for key in row.keys() if key != "embedding"},
        "embedding_sha256": hashlib.sha256(row["embedding"]).hexdigest(),
    }
    for row in rows
]))
'''
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            code,
            str(data),
            str((ROOT / "web").resolve()),
            str(ROOT),
            str(asset_id),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def case_f01_display_only_survives_ingest(app, client, work: Path):
    original = work / "originals" / "f01.jpg"
    create_image(original, "#4a7187")
    seeded = seed_face_asset(app, original, name="Fixture F01")
    original_hash = sha256(original)
    before = snapshot_identity(app, seeded["asset_id"])

    response = client.post(
        "/api/exclusions/assets",
        json={
            "ids": [seeded["asset_id"]],
            "excluded": True,
            "reason": "B01 display-only fixture",
            "display_only": True,
        },
    )
    if response.status_code != 200:
        raise AssertionError(response.text)
    app.ingest(original, with_faces=False)
    restarted_faces = restart_identity(app.DATA, seeded["asset_id"])
    if restarted_faces != before["faces"]:
        raise AssertionError(
            f"display-only identity changed after fresh app import: {restarted_faces}"
        )
    app.invalidate_face_index()
    restored = client.post(
        "/api/exclusions/assets",
        json={"ids": [seeded["asset_id"]], "excluded": False},
    )
    if restored.status_code != 200:
        raise AssertionError(restored.text)

    after = snapshot_identity(app, seeded["asset_id"])
    if after["faces"] != before["faces"]:
        raise AssertionError(
            f"display-only identity changed across ingest/reinit/restore: "
            f"before={before['faces']} after={after['faces']}"
        )
    if after["asset"]["face_state"] != 1:
        raise AssertionError(f"face_state changed: {after['asset']}")
    if sha256(original) != original_hash:
        raise AssertionError("synthetic original bytes changed")
    return {
        "asset_id": seeded["asset_id"],
        "face_id": seeded["face_id"],
        "person_id": seeded["person_id"],
        "original_sha256": original_hash,
        "restart_identity": restarted_faces,
        "identity": after,
    }


def case_f02_postcommit_index_failure(app, _client, work: Path):
    import numpy as np

    original = work / "originals" / "f02.jpg"
    create_image(original, "#96845a")
    inference_calls = {"count": 0}
    embedding = vector(22)

    def get(_image):
        inference_calls["count"] += 1
        return [
            SimpleNamespace(
                bbox=np.array([20, 20, 85, 85]),
                det_score=0.99,
                normed_embedding=embedding,
            )
        ]

    original_engine = app.get_face_engine
    original_remember = app.remember_face
    app.get_face_engine = lambda: SimpleNamespace(get=get)

    def fail_index_publish(*_args, **_kwargs):
        raise RuntimeError("B01 injected index publication failure")

    app.remember_face = fail_index_publish
    try:
        first = app.ingest(original, with_faces=True)
    finally:
        app.remember_face = original_remember
    second = app.ingest(original, with_faces=True)
    app.get_face_engine = original_engine

    with app.db() as conn:
        asset = dict(
            conn.execute(
                "SELECT id,face_state,face_error FROM assets WHERE sha256=?",
                (sha256(original),),
            ).fetchone()
        )
        faces = [
            dict(row)
            for row in conn.execute(
                "SELECT id,person_id,crop FROM faces WHERE asset_id=? ORDER BY id",
                (asset["id"],),
            )
        ]
    missing_crops = [
        row["crop"]
        for row in faces
        if not row["crop"] or not (app.DATA / "faces" / row["crop"]).exists()
    ]
    if first[1] is not None:
        raise AssertionError(f"committed recognition reported failed: {first}")
    if asset["face_state"] != 1 or len(faces) != 1 or missing_crops:
        raise AssertionError(
            f"committed face set is incomplete: asset={asset}, "
            f"faces={faces}, missing={missing_crops}"
        )
    if inference_calls["count"] != 1:
        raise AssertionError(
            f"completed asset was inferred {inference_calls['count']} times"
        )
    if second[3] or second[4]:
        raise AssertionError(f"retry did not skip completed recognition: {second}")
    return {
        "first": first,
        "second": second,
        "asset": asset,
        "faces": faces,
        "inference_calls": inference_calls["count"],
    }


def case_t02_duplicate_path_directory_exclusion(app, client, work: Path):
    first = work / "duplicate-a" / "same.jpg"
    second = work / "duplicate-b" / "same.jpg"
    create_image(first, "#507f72")
    second.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(first, second)
    seeded = seed_face_asset(app, first, name="Fixture T02")
    second_stat = second.stat()
    with app.db() as conn:
        conn.execute(
            """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
               VALUES (?,?,?,?,?,1,0)""",
            (
                seeded["asset_id"],
                str(second),
                second_stat.st_size,
                second_stat.st_mtime_ns,
                app.now(),
            ),
        )
    before = snapshot_identity(app, seeded["asset_id"])
    first_hash, second_hash = sha256(first), sha256(second)

    excluded = client.post(
        "/api/exclusions/roots", json={"path": str(first.parent)}
    )
    if excluded.status_code != 200:
        raise AssertionError(excluded.text)
    app.ingest(first, with_faces=False)
    during = snapshot_identity(app, seeded["asset_id"])
    if during["faces"] != before["faces"]:
        raise AssertionError("directory exclusion removed shared identity data")
    with app.db() as conn:
        files = [
            tuple(row)
            for row in conn.execute(
                "SELECT path,excluded FROM files WHERE asset_id=? ORDER BY path",
                (seeded["asset_id"],),
            )
        ]
        rule_id = conn.execute(
            "SELECT id FROM excluded_roots WHERE path=?", (str(first.parent.resolve()),)
        ).fetchone()[0]
    if sorted(row[1] for row in files) != [0, 1]:
        raise AssertionError(f"duplicate path exclusion mismatch: {files}")
    restored = client.delete(f"/api/exclusions/roots/{rule_id}")
    if restored.status_code != 200:
        raise AssertionError(restored.text)
    if (sha256(first), sha256(second)) != (first_hash, second_hash):
        raise AssertionError("duplicate source bytes changed")
    return {
        "asset_id": seeded["asset_id"],
        "files": files,
        "identity_preserved": during["faces"] == before["faces"],
    }


def create_legacy_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE assets (
          id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, width INTEGER, height INTEGER,
          format TEXT, metadata TEXT NOT NULL DEFAULT '{}', captured_at TEXT, date_source TEXT,
          date_precision TEXT, latitude REAL, longitude REAL, place TEXT, place_source TEXT,
          camera TEXT, category TEXT NOT NULL DEFAULT '照片', error TEXT,
          manual_date TEXT, manual_precision TEXT, manual_place TEXT, notes TEXT NOT NULL DEFAULT '',
          face_state INTEGER NOT NULL DEFAULT 0, face_error TEXT, created_at TEXT NOT NULL,
          excluded INTEGER NOT NULL DEFAULT 0, exclude_reason TEXT NOT NULL DEFAULT '',
          favorite INTEGER NOT NULL DEFAULT 0, object_state INTEGER NOT NULL DEFAULT 0,
          object_error TEXT);
        CREATE TABLE files (
          id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES assets(id),
          path TEXT NOT NULL UNIQUE, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
          modified_at TEXT, exists_now INTEGER NOT NULL DEFAULT 1,
          excluded INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE people (
          id INTEGER PRIMARY KEY, name TEXT, confirmed INTEGER NOT NULL DEFAULT 0,
          suggested_person_id INTEGER REFERENCES people(id), alias TEXT NOT NULL DEFAULT '',
          ignored INTEGER NOT NULL DEFAULT 0, cover_face_id INTEGER);
        CREATE TABLE faces (
          id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES assets(id),
          person_id INTEGER NOT NULL REFERENCES people(id), bbox TEXT NOT NULL,
          embedding BLOB NOT NULL, score REAL, crop TEXT,
          reviewed INTEGER NOT NULL DEFAULT 0, ignored INTEGER NOT NULL DEFAULT 0);
        """
    )


def case_t03_legacy_migration(app, _client, work: Path):
    from library_db import B01_SCHEMA_MIGRATION, init_schema

    legacy_db = work / "legacy" / "library.sqlite3"
    legacy_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(legacy_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_legacy_schema(conn)
    conn.execute(
        """INSERT INTO assets(id,sha256,metadata,face_state,created_at,excluded,exclude_reason)
           VALUES (1,?,?,1,?,1,?)""",
        ("f" * 64, "{}", app.now(), "legacy unknown reason"),
    )
    conn.execute(
        "INSERT INTO files(asset_id,path,size,mtime_ns,excluded) VALUES (1,?,1,0,1)",
        (str(work / "legacy" / "unknown.jpg"),),
    )
    conn.execute(
        "INSERT INTO people(id,name,confirmed,cover_face_id) VALUES (1,?,1,1)",
        ("Legacy Fixture",),
    )
    conn.execute(
        """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,reviewed,crop)
           VALUES (1,1,1,'[1,1,20,20,100,100]',?,1,'1.jpg')""",
        (vector(3).tobytes(),),
    )
    conn.commit()

    init_schema(conn)
    conn.commit()
    first = dict(
        conn.execute(
            "SELECT excluded,exclude_reason,derivative_policy FROM assets WHERE id=1"
        ).fetchone()
    )
    revision_before = conn.execute(
        "SELECT revision FROM face_index_state WHERE id=1"
    ).fetchone()[0]
    init_schema(conn)
    conn.commit()
    second = dict(
        conn.execute(
            "SELECT excluded,exclude_reason,derivative_policy FROM assets WHERE id=1"
        ).fetchone()
    )
    revision_after = conn.execute(
        "SELECT revision FROM face_index_state WHERE id=1"
    ).fetchone()[0]
    marker_count = conn.execute(
        "SELECT count(*) FROM schema_migrations WHERE name=?",
        (B01_SCHEMA_MIGRATION,),
    ).fetchone()[0]
    face_count = conn.execute("SELECT count(*) FROM faces").fetchone()[0]
    cover = conn.execute(
        "SELECT cover_face_id FROM people WHERE id=1"
    ).fetchone()[0]
    conn.close()

    expected = {
        "excluded": 1,
        "exclude_reason": "legacy unknown reason",
        "derivative_policy": "preserve",
    }
    if first != expected or second != expected:
        raise AssertionError(f"legacy exclusion migration changed meaning: {first} {second}")
    if face_count != 1 or cover != 1:
        raise AssertionError("legacy identity was changed by migration")
    if marker_count != 1 or revision_before != revision_after:
        raise AssertionError(
            f"migration not idempotent: marker={marker_count}, "
            f"revision={revision_before}->{revision_after}"
        )
    return {
        "first": first,
        "second": second,
        "face_count": face_count,
        "cover_face_id": cover,
        "migration_marker_count": marker_count,
        "revision": revision_after,
    }


def create_unprocessed_asset(app, path: Path):
    digest = sha256(path)
    stat = path.stat()
    with app.db() as conn:
        asset_id = conn.execute(
            "INSERT INTO assets(sha256,metadata,face_state,created_at) VALUES (?,?,0,?)",
            (digest, "{}", app.now()),
        ).lastrowid
        conn.execute(
            """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
               VALUES (?,?,?,?,?,1,0)""",
            (asset_id, str(path), stat.st_size, stat.st_mtime_ns, app.now()),
        )
    return asset_id


def fake_engine(embedding, calls):
    import numpy as np

    def get(_image):
        calls["count"] += 1
        return [
            SimpleNamespace(
                bbox=np.array([20, 20, 85, 85]),
                det_score=0.99,
                normed_embedding=embedding,
            )
        ]

    return SimpleNamespace(get=get)


def case_t05_precommit_failures(app, _client, work: Path):
    from PIL import Image as PillowImage

    protected_image = work / "originals" / "t05-protected.jpg"
    create_image(protected_image, "#425f6f")
    protected = seed_face_asset(app, protected_image, name="T05 Protected")
    protected_crop = app.DATA / "faces" / f"{protected['face_id']}.jpg"
    protected_hash = sha256(protected_crop)

    crop_image = work / "originals" / "t05-crop-fail.jpg"
    create_image(crop_image, "#a07060")
    crop_asset = create_unprocessed_asset(app, crop_image)
    crop_calls = {"count": 0}
    original_engine = app.get_face_engine
    original_save = PillowImage.Image.save
    app.get_face_engine = lambda: fake_engine(vector(31), crop_calls)

    def fail_pending_save(self, fp, *args, **kwargs):
        if Path(fp).name.startswith(".pending-"):
            raise OSError("B01 injected crop write failure")
        return original_save(self, fp, *args, **kwargs)

    PillowImage.Image.save = fail_pending_save
    try:
        try:
            app.process_faces(crop_asset, crop_image)
        except OSError:
            pass
        else:
            raise AssertionError("crop write fault did not fail")
    finally:
        PillowImage.Image.save = original_save
    with app.db() as conn:
        crop_faces = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (crop_asset,)
        ).fetchone()[0]
        crop_state = conn.execute(
            "SELECT face_state FROM assets WHERE id=?", (crop_asset,)
        ).fetchone()[0]
    if crop_faces or crop_state != 0:
        raise AssertionError(
            f"crop failure published partial DB state: faces={crop_faces}, state={crop_state}"
        )

    commit_image = work / "originals" / "t05-commit-fail.jpg"
    create_image(commit_image, "#5d7755")
    commit_asset = create_unprocessed_asset(app, commit_image)
    commit_calls = {"count": 0}
    app.get_face_engine = lambda: fake_engine(vector(32), commit_calls)
    original_db = app.db
    injected = {"done": False}

    @contextmanager
    def fail_face_commit_db():
        with original_db() as conn:
            yield conn
            if not injected["done"]:
                row = conn.execute(
                    "SELECT face_state FROM assets WHERE id=?", (commit_asset,)
                ).fetchone()
                if row and row["face_state"] == 2:
                    injected["done"] = True
                    raise sqlite3.OperationalError("B01 injected DB commit failure")

    app.db = fail_face_commit_db
    try:
        try:
            app.process_faces(commit_asset, commit_image)
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("DB commit fault did not fail")
    finally:
        app.db = original_db
    with app.db() as conn:
        commit_faces = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (commit_asset,)
        ).fetchone()[0]
        commit_state = conn.execute(
            "SELECT face_state FROM assets WHERE id=?", (commit_asset,)
        ).fetchone()[0]
    pending = list((app.DATA / "faces").glob(".pending-*.jpg"))
    if commit_faces or commit_state != 0 or pending:
        raise AssertionError(
            f"commit failure leaked state: faces={commit_faces}, "
            f"state={commit_state}, pending={pending}"
        )
    if not protected_crop.exists() or sha256(protected_crop) != protected_hash:
        raise AssertionError("pre-commit fault removed an older successful crop")

    app.process_faces(crop_asset, crop_image)
    app.process_faces(commit_asset, commit_image)
    app.get_face_engine = original_engine
    with app.db() as conn:
        recovered = {
            aid: conn.execute(
                "SELECT face_state FROM assets WHERE id=?", (aid,)
            ).fetchone()[0]
            for aid in (crop_asset, commit_asset)
        }
    if recovered != {crop_asset: 1, commit_asset: 1}:
        raise AssertionError(f"retry did not recover cleanly: {recovered}")
    return {
        "crop_failure": {"faces": crop_faces, "state": crop_state},
        "commit_failure": {
            "faces": commit_faces,
            "state": commit_state,
            "fault_injected": injected["done"],
        },
        "protected_crop_sha256": protected_hash,
        "retry_states": recovered,
    }


def case_t06_committed_crop_recovery(app, _client, work: Path):
    original_engine = app.get_face_engine
    inference_calls = {"count": 0}

    def forbidden_engine():
        inference_calls["count"] += 1
        raise AssertionError("crop recovery reran face inference")

    app.get_face_engine = forbidden_engine
    recovered = []
    try:
        for offset, pending_exists in enumerate((True, False), start=1):
            original = work / "originals" / f"t06-{offset}.jpg"
            create_image(original, "#78685c" if offset == 1 else "#78685d")
            digest = sha256(original)
            stat = original.stat()
            with app.db() as conn:
                person_id = conn.execute(
                    "INSERT INTO people(name,confirmed) VALUES (?,1)",
                    (f"T06 Person {offset}",),
                ).lastrowid
                asset_id = conn.execute(
                    """INSERT INTO assets(sha256,metadata,face_state,created_at)
                       VALUES (?,?,2,?)""",
                    (digest, "{}", app.now()),
                ).lastrowid
                conn.execute(
                    """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at)
                       VALUES (?,?,?,?,?)""",
                    (
                        asset_id,
                        str(original),
                        stat.st_size,
                        stat.st_mtime_ns,
                        app.now(),
                    ),
                )
                face_id = conn.execute(
                    """INSERT INTO faces(asset_id,person_id,bbox,embedding,score,reviewed,crop)
                       VALUES (?,?,?, ?,0.99,1,?)""",
                    (
                        asset_id,
                        person_id,
                        "[20,20,85,85,120,120]",
                        vector(40 + offset).tobytes(),
                        f"t06-{offset}-{asset_id}.jpg",
                    ),
                ).lastrowid
            if pending_exists:
                create_image(
                    app.DATA / "faces" / f".pending-{face_id}.jpg", "#aa8866"
                )
            app.process_faces(asset_id, original)
            with app.db() as conn:
                state = conn.execute(
                    "SELECT face_state FROM assets WHERE id=?", (asset_id,)
                ).fetchone()[0]
                crop_name = conn.execute(
                    "SELECT crop FROM faces WHERE id=?", (face_id,)
                ).fetchone()[0]
            final_exists = (app.DATA / "faces" / crop_name).exists()
            if state != 1 or not final_exists:
                raise AssertionError(
                    f"committed crop recovery incomplete: "
                    f"state={state}, final={final_exists}"
                )
            recovered.append(
                {
                    "asset_id": asset_id,
                    "face_id": face_id,
                    "pending_existed": pending_exists,
                    "final_exists": final_exists,
                }
            )
    finally:
        app.get_face_engine = original_engine
    if inference_calls["count"]:
        raise AssertionError(f"recovery inference calls={inference_calls['count']}")
    return {"recovered": recovered, "inference_calls": 0}


def case_t08_stale_index_build(app, _client, work: Path):
    original = work / "originals" / "t08.jpg"
    create_image(original, "#697b91")
    seeded = seed_face_asset(app, original, name="T08 Person")
    app.invalidate_face_index()
    original_db = app.db
    after_query = threading.Event()
    allow_publish = threading.Event()

    @contextmanager
    def gated_db():
        with original_db() as conn:
            yield conn
            if any(frame.function == "load_face_index" for frame in inspect.stack()):
                if not after_query.is_set():
                    after_query.set()
                    if not allow_publish.wait(8):
                        raise TimeoutError("index publish gate timed out")

    app.db = gated_db
    loaded = {}

    def loader():
        try:
            loaded["index"] = app.load_face_index()
        except Exception as exc:
            loaded["error"] = repr(exc)

    thread = threading.Thread(target=loader)
    thread.start()
    if not after_query.wait(8):
        app.db = original_db
        raise TimeoutError("index query gate timed out")
    with original_db() as conn:
        conn.execute(
            "UPDATE people SET confirmed=0,ignored=1 WHERE id=?",
            (seeded["person_id"],),
        )
    allow_publish.set()
    thread.join(10)
    app.db = original_db
    if thread.is_alive() or loaded.get("error"):
        raise AssertionError(f"index loader failed: {loaded}")
    index = loaded["index"]
    current_revision = app.face_index_revision()
    slot = index["person_slot_by_id"][seeded["person_id"]]
    if index["revision"] != current_revision:
        raise AssertionError(
            f"stale revision published: {index['revision']} != {current_revision}"
        )
    if index["person_states"][slot] != "ignored":
        raise AssertionError(
            f"old person state published: {index['person_states'][slot]}"
        )
    return {
        "person_id": seeded["person_id"],
        "revision": current_revision,
        "state": index["person_states"][slot],
    }


def case_t09_stale_match_target(app, _client, work: Path):
    reference = work / "originals" / "t09-reference.jpg"
    create_image(reference, "#7c6654")
    seeded = seed_face_asset(app, reference, name="T09 Old Target")
    query = work / "originals" / "t09-query.jpg"
    create_image(query, "#876b56")
    query_asset = create_unprocessed_asset(app, query)
    calls = {"count": 0}
    original_engine = app.get_face_engine
    original_choose = app.choose_face_person
    app.get_face_engine = lambda: fake_engine(vector(len("T09 Old Target")), calls)
    app.invalidate_face_index()
    mutated = {"done": False, "replacement": None}

    def choose_then_merge(index, embedding, used_people=None):
        decision = original_choose(index, embedding, used_people)
        if not mutated["done"] and decision.get("person_id") == seeded["person_id"]:
            with app.db() as conn:
                replacement = conn.execute(
                    "INSERT INTO people(name,confirmed) VALUES (?,1)",
                    ("T09 Replacement",),
                ).lastrowid
                conn.execute(
                    "UPDATE faces SET person_id=? WHERE person_id=?",
                    (replacement, seeded["person_id"]),
                )
                conn.execute(
                    "DELETE FROM people WHERE id=?", (seeded["person_id"],)
                )
            mutated.update(done=True, replacement=replacement)
        return decision

    app.choose_face_person = choose_then_merge
    try:
        app.process_faces(query_asset, query)
    finally:
        app.choose_face_person = original_choose
        app.get_face_engine = original_engine
    with app.db() as conn:
        new_person = conn.execute(
            "SELECT person_id FROM faces WHERE asset_id=?", (query_asset,)
        ).fetchone()[0]
        old_exists = conn.execute(
            "SELECT count(*) FROM people WHERE id=?", (seeded["person_id"],)
        ).fetchone()[0]
    if not mutated["done"] or old_exists or new_person == seeded["person_id"]:
        raise AssertionError(
            f"stale match target committed: mutated={mutated}, "
            f"new_person={new_person}, old_exists={old_exists}"
        )
    return {
        "old_person_id": seeded["person_id"],
        "replacement_person_id": mutated["replacement"],
        "committed_person_id": new_person,
        "inference_calls": calls["count"],
    }


def case_t10_concurrent_same_asset(app, _client, work: Path):
    original = work / "originals" / "t10.jpg"
    create_image(original, "#587a73")
    asset_id = create_unprocessed_asset(app, original)
    calls = {"count": 0}
    original_engine = app.get_face_engine
    app.get_face_engine = lambda: fake_engine(vector(52), calls)
    gate = threading.Barrier(3)
    results, errors = [], []

    def worker():
        try:
            gate.wait(8)
            results.append(app.process_faces(asset_id, original))
        except Exception as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    gate.wait(8)
    for thread in threads:
        thread.join(10)
    app.get_face_engine = original_engine
    with app.db() as conn:
        face_count = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (asset_id,)
        ).fetchone()[0]
        state = conn.execute(
            "SELECT face_state FROM assets WHERE id=?", (asset_id,)
        ).fetchone()[0]
    if errors or any(thread.is_alive() for thread in threads):
        raise AssertionError(f"concurrent processing failed: {errors}")
    if face_count != 1 or state != 1 or calls["count"] != 1:
        raise AssertionError(
            f"concurrent duplicate: faces={face_count}, state={state}, "
            f"inference={calls['count']}, results={results}"
        )
    return {
        "faces": face_count,
        "state": state,
        "inference_calls": calls["count"],
        "results": results,
    }


def case_t11_cleanup_retry(app, _client, work: Path):
    original = work / "originals" / "t11.jpg"
    create_image(original, "#775e72")
    seeded = seed_face_asset(app, original, name="T11 Person")
    app.make_thumbnail(original, seeded["digest"])
    with app.db() as conn:
        conn.execute(
            """UPDATE assets
               SET excluded=1,exclude_reason='T11',derivative_policy='purge'
               WHERE id=?""",
            (seeded["asset_id"],),
        )
    original_remove = app.remove_generated
    injected = {"done": False}

    def fail_once(path, folder):
        if folder == "faces" and not injected["done"]:
            injected["done"] = True
            raise OSError("B01 injected cleanup failure")
        return original_remove(path, folder)

    app.remove_generated = fail_once
    try:
        try:
            app.cleanup_asset_cache(seeded["asset_id"])
        except OSError:
            pass
        else:
            raise AssertionError("cleanup fault did not fail")
    finally:
        app.remove_generated = original_remove
    with app.db() as conn:
        after_failure = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (seeded["asset_id"],)
        ).fetchone()[0]
    released = app.cleanup_asset_cache(seeded["asset_id"])
    repeated = app.cleanup_asset_cache(seeded["asset_id"])
    with app.db() as conn:
        after_retry = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (seeded["asset_id"],)
        ).fetchone()[0]
        cover = conn.execute(
            "SELECT cover_face_id FROM people WHERE id=?", (seeded["person_id"],)
        ).fetchone()[0]
    if after_failure != 1 or after_retry != 0 or cover is not None or repeated != 0:
        raise AssertionError(
            f"cleanup retry mismatch: failure_faces={after_failure}, "
            f"retry_faces={after_retry}, cover={cover}, repeated={repeated}"
        )
    return {
        "fault_injected": injected["done"],
        "faces_after_failure": after_failure,
        "faces_after_retry": after_retry,
        "released_bytes": released,
        "repeated_release": repeated,
        "cover_face_id": cover,
    }


def case_t12_api_sequence(app, client, work: Path):
    original = work / "originals" / "t12.jpg"
    create_image(original, "#6c7892")
    seeded = seed_face_asset(app, original, name="T12 Before")
    initial_revision = app.face_index_revision()
    operations = [
        client.post(
            "/api/exclusions/assets",
            json={
                "ids": [seeded["asset_id"]],
                "excluded": True,
                "display_only": True,
                "reason": "T12 display only",
            },
        ),
        client.post(
            "/api/exclusions/assets",
            json={"ids": [seeded["asset_id"]], "excluded": False},
        ),
        client.patch(
            f"/api/people/{seeded['person_id']}",
            json={"name": "T12 After", "alias": "Fixture"},
        ),
        client.post(
            "/api/exclusions/assets",
            json={
                "ids": [seeded["asset_id"]],
                "excluded": True,
                "display_only": False,
                "reason": "T12 cleanup",
            },
        ),
    ]
    statuses = [response.status_code for response in operations]
    with app.db() as conn:
        asset = dict(
            conn.execute(
                """SELECT excluded,derivative_policy,face_state
                   FROM assets WHERE id=?""",
                (seeded["asset_id"],),
            ).fetchone()
        )
        person = dict(
            conn.execute(
                "SELECT name,alias,cover_face_id FROM people WHERE id=?",
                (seeded["person_id"],),
            ).fetchone()
        )
        face_count = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (seeded["asset_id"],)
        ).fetchone()[0]
    final_revision = app.face_index_revision()
    if statuses != [200, 200, 200, 200]:
        raise AssertionError(f"API sequence failed: {statuses}")
    if asset != {"excluded": 1, "derivative_policy": "purge", "face_state": 0}:
        raise AssertionError(f"final asset state mismatch: {asset}")
    if person != {"name": "T12 After", "alias": "Fixture", "cover_face_id": None}:
        raise AssertionError(f"person/cover state mismatch: {person}")
    if face_count != 0 or final_revision <= initial_revision:
        raise AssertionError(
            f"face/index state mismatch: faces={face_count}, "
            f"revision={initial_revision}->{final_revision}"
        )
    return {
        "statuses": statuses,
        "asset": asset,
        "person": person,
        "face_count": face_count,
        "revision": [initial_revision, final_revision],
    }


def case_f03_cleanup_invalidates_index(app, client, work: Path):
    original = work / "originals" / "f03.jpg"
    create_image(original, "#6f587e")
    seeded = seed_face_asset(app, original, name="Fixture F03")
    app.invalidate_face_index()
    before = app.load_face_index()
    if seeded["person_id"] not in before["person_ids"]:
        raise AssertionError("fixture person missing before cleanup")

    response = client.post(
        "/api/exclusions/assets",
        json={
            "ids": [seeded["asset_id"]],
            "excluded": True,
            "reason": "B01 destructive cleanup fixture",
            "display_only": False,
        },
    )
    if response.status_code != 200:
        raise AssertionError(response.text)
    with app.db() as conn:
        remaining = conn.execute(
            "SELECT count(*) FROM faces WHERE asset_id=?", (seeded["asset_id"],)
        ).fetchone()[0]
    after = app.load_face_index()
    if remaining != 0:
        raise AssertionError(f"cleanup left {remaining} database face(s)")
    if seeded["person_id"] in after["person_ids"]:
        raise AssertionError(
            "deleted sample remained in the in-process matching index"
        )
    app.invalidate_face_index()
    rebuilt = app.load_face_index()
    if seeded["person_id"] in rebuilt["person_ids"]:
        raise AssertionError("deleted sample remained after index rebuild")
    return {
        "person_id": seeded["person_id"],
        "before_size": before["size"],
        "after_size": after["size"],
        "rebuilt_size": rebuilt["size"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    WORK_PARENT.mkdir(parents=True, exist_ok=True)
    work = Path(
        tempfile.mkdtemp(prefix="b01-consistency-", dir=str(WORK_PARENT))
    ).resolve()
    assert_isolated(work)
    os.environ["PHOTO_LIBRARY_DATA"] = str(work)
    os.environ["PHOTO_WEB_ROOT"] = str((ROOT / "web").resolve())
    os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"
    sys.path.insert(0, str(ROOT))

    import app
    from fastapi.testclient import TestClient

    if app.DATA.resolve() != work:
        raise AssertionError(f"app DATA mismatch: {app.DATA} != {work}")
    client = TestClient(app.app)
    status = client.get("/api/status").json()
    if Path(status["capabilities"]["data_dir"]).resolve() != work:
        raise AssertionError(f"status DATA mismatch: {status}")

    cases = [
        ("B01-T01/F01", case_f01_display_only_survives_ingest),
        ("B01-T02", case_t02_duplicate_path_directory_exclusion),
        ("B01-T03", case_t03_legacy_migration),
        ("B01-T04/F02", case_f02_postcommit_index_failure),
        ("B01-T05", case_t05_precommit_failures),
        ("B01-T06", case_t06_committed_crop_recovery),
        ("B01-T07/F03", case_f03_cleanup_invalidates_index),
        ("B01-T08", case_t08_stale_index_build),
        ("B01-T09", case_t09_stale_match_target),
        ("B01-T10", case_t10_concurrent_same_asset),
        ("B01-T11", case_t11_cleanup_retry),
        ("B01-T12", case_t12_api_sequence),
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
        "integrity_check": integrity,
        "foreign_key_check": foreign_keys,
        "cases": results,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if all(row["status"] == "PASS" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
