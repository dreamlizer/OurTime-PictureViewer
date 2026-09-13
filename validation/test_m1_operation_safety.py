"""M1 operation-correctness and write-safety regression tests.

All state lives under validation/work.  The suite never imports app with the
default PHOTO_LIBRARY_DATA and never starts the production service.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"


def python_env(data_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PHOTO_LIBRARY_DATA"] = str(data_dir)
    env["PHOTO_MODEL_ROOT"] = str(data_dir / "no-model")
    env["PHOTO_GEO_ROOT"] = str(data_dir / "no-geo")
    env["NO_ALBUMENTATIONS_UPDATE"] = "1"
    return env


def insert_asset(connection: sqlite3.Connection, asset_id: int, digest: str) -> None:
    connection.execute(
        """INSERT INTO assets(
               id,sha256,metadata,created_at,face_state,excluded,exclude_reason,
               derivative_policy
           ) VALUES (?,?,?,datetime('now'),0,0,'','preserve')""",
        (asset_id, digest, "{}"),
    )
    connection.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES (?,?,1,1,datetime('now'),1,0)""",
        (asset_id, str(WORK / f"synthetic-{asset_id}.jpg")),
    )


class ImportBoundaryTests(unittest.TestCase):
    def test_database_gate_rejects_fk_or_integrity_findings(self) -> None:
        from validation.db_gates import passed

        cases = [{"id": "synthetic", "status": "PASS"}]
        self.assertFalse(passed(cases, "ok", [(1, "child", 1, "parent")]))
        self.assertFalse(passed(cases, "malformed", []))
        self.assertTrue(passed(cases, "ok", []))

    def test_import_does_not_create_or_write_data_dir(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            data_dir = Path(parent) / "must-not-exist"
            code = (
                "import json,pathlib,app;"
                "p=pathlib.Path(__import__('os').environ['PHOTO_LIBRARY_DATA']);"
                "print(json.dumps({'exists':p.exists(),"
                "'db':(p/'library.sqlite3').exists()}))"
            )
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=python_env(data_dir),
                text=True,
                capture_output=True,
                timeout=45,
                check=True,
            )
            state = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual({"exists": False, "db": False}, state)

    def test_synthetic_schema_upgrade_is_idempotent(self) -> None:
        from library_db import M1_SCHEMA_MIGRATION, init_schema

        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            database = Path(parent) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            try:
                init_schema(connection)
                connection.execute(
                    """INSERT INTO assets(
                         sha256,metadata,created_at,excluded,exclude_reason,
                         derivative_policy
                       ) VALUES (?,'{}',datetime('now'),1,'legacy','preserve')""",
                    ("9" * 64,),
                )
                connection.execute("DROP TABLE operation_items")
                connection.execute("DROP TABLE operations")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE name=?",
                    (M1_SCHEMA_MIGRATION,),
                )
                connection.commit()
                init_schema(connection)
                init_schema(connection)
                row = connection.execute(
                    "SELECT excluded,exclude_reason,derivative_policy,derivative_operation_id FROM assets"
                ).fetchone()
                markers = connection.execute(
                    "SELECT count(*) FROM schema_migrations WHERE name=?",
                    (M1_SCHEMA_MIGRATION,),
                ).fetchone()[0]
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
            finally:
                connection.close()
            self.assertEqual((1, "legacy", "preserve", None), row)
            self.assertEqual(1, markers)
            self.assertEqual("ok", integrity)
            self.assertEqual([], foreign_keys)


class M1ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        WORK.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=WORK)
        cls.data_dir = Path(cls.temp.name) / "data"
        cls.previous_env = {
            name: os.environ.get(name)
            for name in ("PHOTO_LIBRARY_DATA", "PHOTO_MODEL_ROOT", "PHOTO_GEO_ROOT")
        }
        os.environ.update(python_env(cls.data_dir))
        sys.path.insert(0, str(ROOT))
        cls.module = importlib.import_module("app")
        cls.module.initialize_application()
        cls.client_context = TestClient(cls.module.app, raise_server_exceptions=False)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        cls.module.shutdown_application()
        sys.modules.pop("app", None)
        for name, value in cls.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        cls.temp.cleanup()

    def setUp(self) -> None:
        with self.module.db() as connection:
            connection.execute("DELETE FROM operation_items")
            connection.execute("DELETE FROM operations")
            connection.execute("DELETE FROM edits")
            connection.execute("DELETE FROM faces")
            connection.execute("DELETE FROM people")
            connection.execute("DELETE FROM files")
            connection.execute("DELETE FROM assets")

    def test_batch_exclusion_prevalidates_all_ids(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "a" * 64)
        response = self.client.post(
            "/api/exclusions/assets",
            json={
                "ids": [1, 999],
                "excluded": True,
                "operation_id": str(uuid.uuid4()),
            },
        )
        self.assertEqual(404, response.status_code)
        with self.module.db() as connection:
            row = connection.execute(
                "SELECT excluded,derivative_policy FROM assets WHERE id=1"
            ).fetchone()
        self.assertEqual((0, "preserve"), tuple(row))

    def test_batch_exclusion_is_idempotent_and_payload_bound(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "b" * 64)
        operation_id = str(uuid.uuid4())
        payload = {"ids": [1, 1], "excluded": True, "operation_id": operation_id}
        first = self.client.post("/api/exclusions/assets", json=payload)
        second = self.client.post("/api/exclusions/assets", json=payload)
        conflict = self.client.post(
            "/api/exclusions/assets",
            json={**payload, "excluded": False},
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(409, conflict.status_code)
        self.assertEqual(operation_id, first.json()["operation_id"])
        self.assertTrue(first.json()["state_committed"])

    def test_same_operation_key_is_serialized_under_concurrency(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "0" * 64)
        operation_id = str(uuid.uuid4())
        payload = {"ids": [1], "excluded": True, "operation_id": operation_id}
        gate = threading.Barrier(2)

        def submit():
            gate.wait(timeout=5)
            return self.module.exclude_assets(
                self.module.ExclusionRequest(**payload), None
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [future.result(timeout=10) for future in (pool.submit(submit), pool.submit(submit))]
        self.assertEqual(responses[0], responses[1])
        with self.module.db() as connection:
            operations = connection.execute(
                "SELECT count(*) FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()[0]
            edits = connection.execute(
                "SELECT count(*) FROM edits WHERE target='asset:1'"
            ).fetchone()[0]
        self.assertEqual(1, operations)
        self.assertEqual(1, edits)

    def test_batch_transaction_failure_rolls_back_before_cleanup(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "1" * 64)
            insert_asset(connection, 2, "2" * 64)
            connection.execute(
                """CREATE TRIGGER fail_second_asset BEFORE UPDATE ON assets
                   WHEN NEW.id=2 BEGIN SELECT RAISE(ABORT,'injected'); END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.module.exclude_assets(
                self.module.ExclusionRequest(
                    ids=[1, 2],
                    excluded=True,
                    operation_id=str(uuid.uuid4()),
                ),
                None,
            )
        with self.module.db() as connection:
            rows = connection.execute(
                "SELECT id,excluded,derivative_policy FROM assets ORDER BY id"
            ).fetchall()
            connection.execute("DROP TRIGGER fail_second_asset")
        self.assertEqual([(1, 0, "preserve"), (2, 0, "preserve")], [tuple(x) for x in rows])

    def test_restore_cancels_stale_cleanup_operation(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "3" * 64)
            person = connection.execute(
                "INSERT INTO people(name,confirmed) VALUES ('fixture',1)"
            ).lastrowid
            face = connection.execute(
                """INSERT INTO faces(asset_id,person_id,bbox,embedding,crop,reviewed)
                   VALUES (?,?,?,x'00',?,1)""",
                (1, person, "[0,0,1,1]", "owned.jpg"),
            ).lastrowid
        crop = self.data_dir / "faces" / "owned.jpg"
        crop.write_bytes(b"owned")
        entered, release = threading.Event(), threading.Event()
        original_cleanup = self.module.cleanup_asset_cache

        def delayed_cleanup(asset_id, operation_id=None):
            entered.set()
            self.assertTrue(release.wait(5))
            return original_cleanup(asset_id, operation_id)

        self.module.cleanup_asset_cache = delayed_cleanup
        exclude_operation = str(uuid.uuid4())
        result = {}

        def exclude():
            result["response"] = self.client.post(
                "/api/exclusions/assets",
                json={
                    "ids": [1],
                    "excluded": True,
                    "operation_id": exclude_operation,
                },
            )

        worker = threading.Thread(target=exclude)
        worker.start()
        self.assertTrue(entered.wait(5))
        restored = self.client.post(
            "/api/exclusions/assets",
            json={
                "ids": [1],
                "excluded": False,
                "operation_id": str(uuid.uuid4()),
            },
        )
        release.set()
        worker.join(5)
        self.module.cleanup_asset_cache = original_cleanup
        self.assertFalse(worker.is_alive())
        self.assertEqual(200, restored.status_code)
        self.assertEqual(200, result["response"].status_code)
        self.assertTrue(crop.exists())
        with self.module.db() as connection:
            asset = connection.execute(
                "SELECT excluded,derivative_policy,derivative_operation_id FROM assets WHERE id=1"
            ).fetchone()
            face_owner = connection.execute(
                "SELECT person_id FROM faces WHERE id=?", (face,)
            ).fetchone()[0]
        self.assertEqual((0, "preserve", None), tuple(asset))
        self.assertEqual(person, face_owner)

    def test_cleanup_failure_has_receipt_and_retry_is_exact(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "4" * 64)
            person = connection.execute(
                "INSERT INTO people(name,confirmed) VALUES ('fixture',1)"
            ).lastrowid
            for face_id in (1, 2, 3):
                connection.execute(
                    """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,reviewed)
                       VALUES (?,1,?,'[0,0,1,1]',x'00',1)""",
                    (face_id, person),
                )
        face_dir = self.data_dir / "faces"
        for face_id in (1, 2, 3):
            (face_dir / f"{face_id}.jpg").write_bytes(str(face_id).encode("ascii"))
        unknown = face_dir / "unknown-orphan.jpg"
        unknown.write_bytes(b"unknown")
        original_remove = self.module.remove_generated
        original_index = self.module.index_face_derivatives
        failed_once = False
        enumerations = 0

        def counted_index(directory=None):
            nonlocal enumerations
            enumerations += 1
            return original_index(directory)

        def fail_second(path, folder):
            nonlocal failed_once
            if folder == "faces" and Path(path).name == "2.jpg" and not failed_once:
                failed_once = True
                raise OSError("injected cleanup failure")
            return original_remove(path, folder)

        self.module.index_face_derivatives = counted_index
        self.module.remove_generated = fail_second
        operation_id = str(uuid.uuid4())
        try:
            first = self.client.post(
                "/api/exclusions/assets",
                json={"ids": [1], "excluded": True, "operation_id": operation_id},
            )
            self.assertEqual(200, first.status_code, first.text)
            self.assertEqual("failed", first.json()["cleanup_status"])
            self.assertTrue(first.json()["state_committed"])
            self.assertEqual(1, enumerations)
            self.module.remove_generated = original_remove
            retry = self.client.post(f"/api/operations/{operation_id}/retry-cleanup")
            again = self.client.post(f"/api/operations/{operation_id}/retry-cleanup")
        finally:
            self.module.remove_generated = original_remove
            self.module.index_face_derivatives = original_index
        self.assertEqual(200, retry.status_code, retry.text)
        self.assertEqual("completed", retry.json()["cleanup_status"])
        self.assertEqual(200, again.status_code, again.text)
        self.assertEqual(0, again.json()["released_bytes"] - retry.json()["released_bytes"])
        self.assertEqual(2, enumerations)
        self.assertTrue(unknown.exists())
        self.assertFalse(any((face_dir / f"{face_id}.jpg").exists() for face_id in (1, 2, 3)))
        with self.module.db() as connection:
            face_count = connection.execute(
                "SELECT count(*) FROM faces WHERE asset_id=1"
            ).fetchone()[0]
            item = connection.execute(
                """SELECT status FROM operation_items
                   WHERE operation_id=? AND item_kind='asset_cleanup' AND item_id='1'""",
                (operation_id,),
            ).fetchone()[0]
        self.assertEqual(0, face_count)
        self.assertEqual("completed", item)

    def test_colliding_asset_lock_shards_have_stable_order(self) -> None:
        digests = ("00000000" + "1" * 56, "00000080" + "2" * 56)
        self.assertIs(self.module.asset_lock(digests[0]), self.module.asset_lock(digests[1]))
        with self.module.db() as connection:
            insert_asset(connection, 1, digests[0])
            insert_asset(connection, 2, digests[1])
        gate = threading.Barrier(2)

        def submit(ids, excluded):
            gate.wait(timeout=5)
            body = self.module.ExclusionRequest(
                ids=ids,
                excluded=excluded,
                operation_id=str(uuid.uuid4()),
            )
            return self.module.exclude_assets(body, None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(submit, [1, 2], True),
                pool.submit(submit, [2, 1], False),
            )
            responses = [future.result(timeout=10) for future in futures]
        self.assertTrue(all(response["state_committed"] for response in responses))
        with self.module.db() as connection:
            rows = connection.execute(
                """SELECT excluded,derivative_policy,derivative_operation_id
                   FROM assets ORDER BY id"""
            ).fetchall()
        self.assertEqual(rows[0][0:2], rows[1][0:2])
        self.assertIsNone(rows[0][2])
        self.assertIsNone(rows[1][2])

    def test_invalid_ids_are_rejected_without_side_effects(self) -> None:
        for invalid in (0, -1, True):
            response = self.client.post(
                "/api/exclusions/assets",
                json={"ids": [invalid], "excluded": True},
            )
            self.assertEqual(422, response.status_code, response.text)
        response = self.client.patch(
            "/api/photos",
            json={
                "ids": [1],
                "manual_date": "2023-02-30",
                "manual_precision": "日",
            },
        )
        self.assertEqual(400, response.status_code)
        for query in (
            "filter=unknown",
            "filter=group:0",
            "date_from=2023-02-30",
            "sort=unknown",
        ):
            response = self.client.get("/api/photos?" + query)
            self.assertEqual(400, response.status_code, response.text)
        for path, method, body in (
            ("/api/people/0", "PATCH", {"name": "甲"}),
            ("/api/people/-1/ignore", "POST", {"ignored": True}),
            ("/api/photos/0/passersby", "POST", {"ignored": True}),
            ("/api/photos/0/favorite", "PUT", {"favorite": True}),
        ):
            response = self.client.request(method, path, json=body)
            self.assertEqual(422, response.status_code, response.text)
        self.assertEqual(
            422,
            self.client.post(
                "/api/exclusions/assets",
                json={"ids": list(range(1, 1002)), "excluded": True},
            ).status_code,
        )
        self.assertEqual(
            422,
            self.client.patch("/api/people/1", json={"name": "x" * 101}).status_code,
        )
        self.assertEqual(
            400,
            self.client.patch(
                "/api/photos",
                json={"ids": [1], "manual_precision": "未知"},
            ).status_code,
        )

    def test_structured_errors_keep_legacy_detail(self) -> None:
        response = self.client.get("/api/photos/999999")
        self.assertEqual(404, response.status_code)
        payload = response.json()
        self.assertEqual("照片不存在", payload["detail"])
        self.assertEqual("not_found", payload["error_code"])

    def test_state2_is_visible_and_get_does_not_run_face_model(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "c" * 64)
            insert_asset(connection, 2, "d" * 64)
            connection.execute(
                "UPDATE assets SET face_state=2,face_error=NULL WHERE id=1"
            )
            connection.execute(
                "UPDATE assets SET face_state=2,face_error='publish failed' WHERE id=2"
            )
        original = self.module.get_face_engine
        self.module.get_face_engine = lambda: (_ for _ in ()).throw(
            AssertionError("GET must not load face model")
        )
        try:
            status = self.client.get("/api/status").json()
            detail = self.client.get("/api/photos/1").json()
            errors = self.client.get(
                "/api/photos?filter=errors&offset=0&limit=24"
            ).json()
        finally:
            self.module.get_face_engine = original
        self.assertEqual(2, status["stats"]["face_publish_pending"])
        self.assertEqual("committed_pending_publish", detail["face_status"])
        self.assertIn("待恢复", detail["face_status_message"])
        self.assertEqual({1, 2}, {item["id"] for item in errors["items"]})

    def test_face_derivative_inventory_uses_exact_ids(self) -> None:
        face_dir = self.data_dir / "faces"
        for name in (
            "1.jpg",
            "10.jpg",
            "100.jpg",
            ".pending-1.jpg",
            ".recover-1-a.jpg",
            ".recover-10-b.jpg",
            "custom-one.jpg",
            "unknown-orphan.jpg",
        ):
            (face_dir / name).write_bytes(b"x")
        faces = [
            {"id": 1, "crop": "custom-one.jpg"},
            {"id": 10, "crop": None},
            {"id": 100, "crop": None},
        ]
        inventory = self.module.index_face_derivatives(face_dir)
        one = {path.name for path in self.module.owned_face_derivative_paths(faces[0], inventory)}
        ten = {path.name for path in self.module.owned_face_derivative_paths(faces[1], inventory)}
        hundred = {path.name for path in self.module.owned_face_derivative_paths(faces[2], inventory)}
        self.assertEqual(
            {"custom-one.jpg", ".pending-1.jpg", ".recover-1-a.jpg"}, one
        )
        self.assertEqual({"10.jpg", ".recover-10-b.jpg"}, ten)
        self.assertEqual({"100.jpg"}, hundred)
        self.assertNotIn("unknown-orphan.jpg", one | ten | hundred)

    def test_merge_requires_same_photo_confirmation_and_can_undo(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "e" * 64)
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored) VALUES (1,'甲',1,0)"
            )
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored) VALUES (2,'乙',1,0)"
            )
            connection.execute(
                """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,reviewed,ignored)
                   VALUES (1,1,1,'[0,0,10,10]',x'00',1,0)"""
            )
            connection.execute(
                """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,reviewed,ignored)
                   VALUES (2,1,2,'[20,0,30,10]',x'00',1,0)"""
            )
        operation_id = str(uuid.uuid4())
        blocked = self.client.post(
            "/api/people/2/merge",
            json={"target_id": 1, "operation_id": operation_id},
        )
        self.assertEqual(409, blocked.status_code)
        error = blocked.json()
        self.assertEqual("same_photo_confirmation_required", error["error_code"])
        confirmed = self.client.post(
            "/api/people/2/merge",
            json={
                "target_id": 1,
                "operation_id": operation_id,
                "confirmation_token": error["confirmation_token"],
            },
        )
        self.assertEqual(200, confirmed.status_code, confirmed.text)
        undo_check = self.client.post(
            f"/api/operations/{operation_id}/undo", json={"dry_run": True}
        )
        self.assertEqual(200, undo_check.status_code, undo_check.text)
        undone = self.client.post(
            f"/api/operations/{operation_id}/undo", json={"dry_run": False}
        )
        self.assertEqual(200, undone.status_code, undone.text)
        with self.module.db() as connection:
            people = connection.execute(
                "SELECT id,name FROM people ORDER BY id"
            ).fetchall()
            faces = connection.execute(
                "SELECT id,person_id FROM faces ORDER BY id"
            ).fetchall()
        self.assertEqual([(1, "甲"), (2, "乙")], [tuple(row) for row in people])
        self.assertEqual([(1, 1), (2, 2)], [tuple(row) for row in faces])

    def test_suggestion_cycle_is_rejected(self) -> None:
        with self.module.db() as connection:
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored,suggested_person_id) VALUES (1,'甲',1,0,NULL)"
            )
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored,suggested_person_id) VALUES (2,NULL,0,0,1)"
            )
        response = self.client.post(
            "/api/people/1/merge",
            json={"target_id": 2, "operation_id": str(uuid.uuid4())},
        )
        self.assertEqual(409, response.status_code)
        self.assertEqual("relationship_conflict", response.json()["error_code"])

    def test_merge_undo_refuses_post_merge_changes(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "7" * 64)
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored) VALUES (1,'甲',1,0)"
            )
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored) VALUES (2,'乙',1,0)"
            )
            connection.execute(
                """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,reviewed,ignored)
                   VALUES (1,1,2,'[0,0,1,1]',x'00',1,0)"""
            )
        operation_id = str(uuid.uuid4())
        merged = self.client.post(
            "/api/people/2/merge",
            json={"target_id": 1, "operation_id": operation_id},
        )
        self.assertEqual(200, merged.status_code, merged.text)
        with self.module.db() as connection:
            connection.execute("UPDATE faces SET person_id=1,reviewed=0 WHERE id=1")
            connection.execute("INSERT INTO people(id,name,confirmed) VALUES (2,'new',1)")
        undo = self.client.post(
            f"/api/operations/{operation_id}/undo", json={"dry_run": False}
        )
        self.assertEqual(409, undo.status_code)
        self.assertEqual("undo_conflict", undo.json()["error_code"])

    def test_merge_undo_refuses_changed_moved_face_state(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "8" * 64)
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored) VALUES (1,'甲',1,0)"
            )
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored) VALUES (2,'乙',1,0)"
            )
            connection.execute(
                """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,reviewed,ignored)
                   VALUES (1,1,2,'[0,0,1,1]',x'00',1,0)"""
            )
        operation_id = str(uuid.uuid4())
        merged = self.client.post(
            "/api/people/2/merge",
            json={"target_id": 1, "operation_id": operation_id},
        )
        self.assertEqual(200, merged.status_code, merged.text)
        with self.module.db() as connection:
            connection.execute("UPDATE faces SET reviewed=0 WHERE id=1")
        undo = self.client.post(
            f"/api/operations/{operation_id}/undo", json={"dry_run": False}
        )
        self.assertEqual(409, undo.status_code)
        self.assertEqual("undo_conflict", undo.json()["error_code"])
        with self.module.db() as connection:
            state = connection.execute(
                "SELECT person_id,reviewed FROM faces WHERE id=1"
            ).fetchone()
        self.assertEqual((1, 0), tuple(state))

    def test_operation_receipt_survives_second_client(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, "f" * 64)
        operation_id = str(uuid.uuid4())
        response = self.client.post(
            "/api/exclusions/assets",
            json={"ids": [1], "excluded": True, "operation_id": operation_id},
        )
        self.assertEqual(200, response.status_code)
        receipt = self.client.get(f"/api/operations/{operation_id}")
        self.assertEqual(200, receipt.status_code)
        self.assertEqual(operation_id, receipt.json()["operation_id"])
        self.assertEqual("committed", receipt.json()["status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
