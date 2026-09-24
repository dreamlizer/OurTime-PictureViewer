"""Same-occasion split suggestions stay preview-only until confirmed."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from face_split_batch import suggest_split_batch
from library_db import init_schema

RUN = ROOT / "validation" / "work" / ("face-split-batch-" + str(time.time_ns()))
DATA = RUN / "data"
URL = "http://127.0.0.1:8794"


def vector(seed: int, drift: float = 0.0) -> np.ndarray:
    values = np.zeros(8, dtype=np.float32)
    values[seed % 8] = 1.0
    if drift:
        values[(seed + 1) % 8] = drift
    return values / np.linalg.norm(values)


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def unit_rules() -> None:
    positive = vector(0)
    seed = vector(1)
    same = [
        {"id": 2, "embedding": vector(1, 0.05), "captured_at": "2024-06-01T12:10:00"},
        {"id": 3, "embedding": vector(1, 0.08), "captured_at": "2024-06-01T14:40:00"},
    ]
    scattered = {"id": 4, "embedding": vector(1, 0.04), "captured_at": "2024-06-03T12:10:00"}
    close = {"id": 5, "embedding": vector(0, 0.35), "captured_at": "2024-06-01T12:20:00"}
    result = suggest_split_batch(
        [seed],
        [positive],
        same + [scattered, close],
        seed_times=["2024-06-01T12:00:00"],
    )
    check([item["id"] for item in result["batch"]] == [2, 3], "same-day lookalikes enter the batch")
    check([item["id"] for item in result["loose"]] == [4], "a later lookalike stays out of the default batch")
    check(5 not in {item["id"] for item in result["batch"] + result["loose"]}, "a close score is not suggested")
    empty = suggest_split_batch([seed], [], same)
    check(empty["batch"] == [] and empty["reason"] == "not_enough_examples", "no positive example means no batch")


def request(path: str, body=None, method: str | None = None, headers: dict | None = None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    call = urllib.request.Request(
        URL + path,
        data=payload,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(call, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        raw = error.read()
        if not raw:
            raise
        return error.code, json.loads(raw.decode("utf-8"))


def add_asset(conn: sqlite3.Connection, asset_id: int, captured_at: str) -> None:
    conn.execute(
        """INSERT INTO assets(
             id,sha256,width,height,format,metadata,captured_at,date_source,date_precision,
             category,created_at,face_state,excluded
           ) VALUES(?,?,64,64,'JPEG','{}',?,'EXIF','second','photo',?,1,0)""",
        (asset_id, f"asset-{asset_id}", captured_at, "2026-09-24T00:00:00"),
    )
    conn.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES(?,?,1,1,?,1,0)""",
        (asset_id, f"isolated-{asset_id}.jpg", captured_at),
    )


def add_face(conn: sqlite3.Connection, face_id: int, asset_id: int, person_id: int, embedding: np.ndarray) -> None:
    conn.execute(
        """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed,ignored)
           VALUES(?,?,?,'[0,0,8,8]',?,0.9,1,0)""",
        (face_id, asset_id, person_id, np.asarray(embedding, dtype=np.float32).tobytes()),
    )


def seed() -> None:
    DATA.mkdir(parents=True)
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        init_schema(conn)
        conn.execute("INSERT INTO people(id,name,confirmed,cover_face_id) VALUES(1,'Sample',1,1)")
        rows = [
            (1, 1, "2024-06-01T10:00:00", vector(0)),
            (2, 2, "2024-06-01T12:00:00", vector(1)),
            (3, 3, "2024-06-01T12:20:00", vector(1, 0.05)),
            (4, 4, "2024-06-01T14:30:00", vector(1, 0.08)),
            (5, 5, "2024-06-03T12:00:00", vector(1, 0.04)),
            (6, 6, "2024-06-01T12:10:00", vector(0, 0.35)),
        ]
        for face_id, asset_id, captured_at, embedding in rows:
            add_asset(conn, asset_id, captured_at)
            add_face(conn, face_id, asset_id, 1, embedding)
        conn.commit()


def api_flow() -> None:
    seed()
    env = {**os.environ, "PHOTO_LIBRARY_DATA": str(DATA), "PHOTO_WEB_ROOT": str(ROOT / "web")}
    log_path = RUN / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", "8794"],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        ready = False
        for _ in range(120):
            try:
                status, _ = request("/api/health")
                ready = status == 200
                if ready:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        if not ready:
            log.flush()
            raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])

        status, split = request("/api/faces/2/split", method="POST", headers={"Idempotency-Key": "split-seed"})
        batch_ids = [item["id"] for item in split["batch"]["batch"]]
        check(status == 200 and batch_ids == [3, 4], "preview offers only the same-occasion faces")
        check(5 not in batch_ids and 6 not in batch_ids, "scattered and close faces are absent from the preview")

        status, rejected = request(
            "/api/people/1/split-batch",
            {"face_ids": [5], "seed_face_ids": [2]},
            "POST",
            {"Idempotency-Key": "reject-loose"},
        )
        check(status == 409, "a face outside the batch is refused")

        status, confirmed = request(
            "/api/people/1/split-batch",
            {"face_ids": [3], "seed_face_ids": [2]},
            "POST",
            {"Idempotency-Key": "confirm-one"},
        )
        check(status == 200 and confirmed["moved_faces"] == 1, "confirmation moves only the checked face")
        with sqlite3.connect(DATA / "library.sqlite3") as conn:
            moved = conn.execute("SELECT person_id FROM faces WHERE id=3").fetchone()[0]
            stayed = conn.execute("SELECT person_id FROM faces WHERE id=4").fetchone()[0]
        check(moved != 1 and stayed == 1, "an unchecked same-occasion face stays with the person")

        status, stale = request(
            "/api/people/1/split-batch",
            {"face_ids": [3, 4], "seed_face_ids": [2]},
            "POST",
            {"Idempotency-Key": "stale-batch"},
        )
        check(status == 409, "a stale selected face is refused")

        status, undo = request("/api/operations/confirm-one/undo", {"dry_run": False}, "POST")
        check(status == 200 and undo["status"] == "undone", "the confirmed batch can be undone")
        with sqlite3.connect(DATA / "library.sqlite3") as conn:
            restored = conn.execute("SELECT person_id FROM faces WHERE id=3").fetchone()[0]
            created = conn.execute("SELECT count(*) FROM people WHERE id=?", (confirmed["person_id"],)).fetchone()[0]
        check(restored == 1 and created == 0, "undo returns the face and removes the temporary person")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


if __name__ == "__main__":
    unit_rules()
    api_flow()
    print("FACE_SPLIT_BATCH_OK")
