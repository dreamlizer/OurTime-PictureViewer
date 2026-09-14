"""Focused isolated regressions for the M3 final closeout."""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"


def insert_asset(connection, asset_id, path, *, latitude=None, longitude=None):
    connection.execute(
        """INSERT INTO assets(
             id,sha256,metadata,created_at,face_state,latitude,longitude,place,notes
           ) VALUES (?,?, '{}',datetime('now'),0,?,?,'fixture','kept')""",
        (asset_id, f"{asset_id:064x}", latitude, longitude),
    )
    connection.execute(
        """INSERT INTO files(
             id,asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
           ) VALUES (?,?,?,1,1,datetime('now'),1,0)""",
        (asset_id, asset_id, str(path)),
    )


class M3CloseoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=WORK)
        cls.data = Path(cls.temp.name) / "data"
        cls.previous = {
            key: os.environ.get(key)
            for key in ("PHOTO_LIBRARY_DATA", "PHOTO_MODEL_ROOT", "PHOTO_GEO_ROOT")
        }
        os.environ.update({
            "PHOTO_LIBRARY_DATA": str(cls.data),
            "PHOTO_MODEL_ROOT": str(cls.data / "no-model"),
            "PHOTO_GEO_ROOT": str(cls.data / "no-geo"),
        })
        sys.path.insert(0, str(ROOT))
        cls.module = importlib.import_module("app")
        cls.module.initialize_application()
        cls.context = TestClient(cls.module.app, raise_server_exceptions=False)
        cls.client = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        cls.module.shutdown_application()
        sys.modules.pop("app", None)
        for key, value in cls.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.temp.cleanup()

    def setUp(self):
        self.module.STOP.clear()
        with self.module.db() as connection:
            for table in (
                "operation_items", "operations", "edits", "faces", "people",
                "files", "assets",
            ):
                connection.execute(f"DELETE FROM {table}")

    def assert_database_clean(self):
        with self.module.db() as connection:
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], list(connection.execute("PRAGMA foreign_key_check")))

    def test_map_viewport_bounds_match_marker_and_clicked_results(self):
        with self.module.db() as connection:
            insert_asset(connection, 1, WORK / "edge-a.jpg", latitude=10.001, longitude=20.001)
            insert_asset(connection, 2, WORK / "edge-b.jpg", latitude=10.019, longitude=20.001)
        marker = self.client.get(
            "/api/places?west=20&south=10&east=20.01&north=10.01&zoom=15"
        ).json()["clusters"][0]
        self.assertEqual(1, marker["count"])
        params = {
            "map_cell": marker["cell"],
            "map_lat_bucket": marker["lat_bucket"],
            "map_lng_bucket": marker["lng_bucket"],
            "map_west": 20,
            "map_south": 10,
            "map_east": 20.01,
            "map_north": 10.01,
        }
        clicked = self.client.get("/api/photos", params=params)
        self.assertEqual(200, clicked.status_code, clicked.text)
        self.assertEqual(1, clicked.json()["total"])
        self.assertEqual(
            400,
            self.client.get("/api/photos", params={"map_cell": marker["cell"], "map_lat_bucket": marker["lat_bucket"], "map_lng_bucket": marker["lng_bucket"], "map_west": 20}).status_code,
        )

    def test_root_scoped_missing_reconciliation_and_short_write(self):
        root = Path(self.temp.name) / "root"
        sibling = Path(self.temp.name) / "root-similar"
        unrelated = Path(self.temp.name) / "elsewhere"
        for folder in (root, sibling, unrelated):
            folder.mkdir(exist_ok=True)
        existing = root / "existing.jpg"
        existing.write_bytes(b"x")
        missing = root / "missing.jpg"
        denied = root / "denied.jpg"
        denied.write_bytes(b"x")
        sibling_file = sibling / "sibling.jpg"
        other_file = unrelated / "other.jpg"
        sibling_file.write_bytes(b"x")
        other_file.write_bytes(b"x")
        with self.module.db() as connection:
            for asset_id, path in enumerate(
                (existing, missing, denied, sibling_file, other_file), 1
            ):
                insert_asset(connection, asset_id, path)

        calls = []
        errors = []
        entered = threading.Event()
        release = threading.Event()

        def controlled_stat(path):
            path = Path(path)
            calls.append(path)
            if path == missing:
                entered.set()
                self.assertTrue(release.wait(5))
                raise FileNotFoundError(path)
            if path == denied:
                raise PermissionError(path)
            return path.stat()

        with ThreadPoolExecutor(max_workers=2) as pool:
            reconcile = pool.submit(
                self.module.reconcile_missing_files,
                root,
                stat_path=controlled_stat,
                error_callback=lambda path, exc: errors.append((Path(path), type(exc))),
                batch_size=2,
            )
            self.assertTrue(entered.wait(5))
            writer = pool.submit(self._write_during_stat)
            self.assertTrue(writer.result(timeout=5))
            release.set()
            result = reconcile.result(timeout=5)

        self.assertEqual({existing, missing, denied}, set(calls))
        self.assertEqual([(denied, PermissionError)], errors)
        self.assertEqual(1, result["missing"])
        with self.module.db() as connection:
            rows = connection.execute(
                "SELECT id,exists_now FROM files ORDER BY id"
            ).fetchall()
            notes = connection.execute(
                "SELECT id,notes FROM assets ORDER BY id"
            ).fetchall()
        self.assertEqual([(1, 1), (2, 0), (3, 1), (4, 1), (5, 1)], [tuple(row) for row in rows])
        self.assertEqual(
            [(1, "concurrent-write"), (2, "kept"), (3, "kept"), (4, "kept"), (5, "kept")],
            [tuple(row) for row in notes],
        )

        disconnected = Path(self.temp.name) / "disconnected"
        conservative = self.module.reconcile_missing_files(
            disconnected, error_callback=lambda path, exc: errors.append((Path(path), type(exc)))
        )
        self.assertEqual(0, conservative["missing"])
        paused = threading.Event()
        paused.set()
        paused_result = self.module.reconcile_missing_files(
            root, stop_event=paused,
            stat_path=lambda path: self.fail("paused reconciliation started a batch"),
        )
        self.assertEqual(0, paused_result["checked"])
        self.assert_database_clean()

    def _write_during_stat(self):
        with self.module.db() as connection:
            connection.execute(
                "UPDATE assets SET notes='concurrent-write' WHERE id=1"
            )
        return True

    def test_root_disconnect_discards_only_the_uncommitted_batch(self):
        root = Path(self.temp.name) / "removable-root"
        root.mkdir()
        first, second = root / "first.jpg", root / "second.jpg"
        first.write_bytes(b"x")
        second.write_bytes(b"x")
        with self.module.db() as connection:
            insert_asset(connection, 1, first)
            insert_asset(connection, 2, second)

        calls = []
        moved = root.with_name("removable-root-offline")

        def disconnecting_stat(path):
            path = Path(path)
            calls.append(path)
            if path == first:
                root.rename(moved)
                raise FileNotFoundError(path)
            return path.stat()

        result = self.module.reconcile_missing_files(root, stat_path=disconnecting_stat)
        self.assertTrue(result["interrupted"])
        self.assertIn("不可访问", result["reason"])
        self.assertEqual(0, result["missing"])
        self.assertEqual([first, second], calls)
        with self.module.db() as connection:
            self.assertEqual([(1, 1), (2, 1)], [tuple(row) for row in connection.execute(
                "SELECT id,exists_now FROM files ORDER BY id"
            )])
        self.assert_database_clean()

    def test_export_uses_one_read_snapshot_and_releases_failures(self):
        with self.module.db() as connection:
            insert_asset(connection, 1, WORK / "export-1.jpg")

        wrote = False

        def after_table(name):
            nonlocal wrote
            if name != "assets" or wrote:
                return
            with self.module.db() as writer:
                insert_asset(writer, 2, WORK / "export-2.jpg")
            wrote = True

        with self.module.db() as reader:
            snapshot = self.module.collect_export_snapshot(reader, after_table=after_table)
        self.assertTrue(wrote)
        self.assertEqual([1], [row["id"] for row in snapshot["assets"]])
        self.assertEqual([1], [row["asset_id"] for row in snapshot["files"]])
        self.assertEqual(2, snapshot["format_version"])

        with self.assertRaises(RuntimeError):
            with self.module.db() as reader:
                self.module.collect_export_snapshot(
                    reader,
                    after_table=lambda name: (_ for _ in ()).throw(RuntimeError("injected")),
                )
        self.assertTrue(self._write_during_stat())
        self.assert_database_clean()

    def test_empty_export_contract_is_preserved(self):
        response = self.client.get("/api/export")
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertEqual(2, payload["format_version"])
        for key in ("assets", "files", "people", "faces", "edits", "excluded_roots"):
            self.assertEqual([], payload[key])
        self.assertEqual(
            'attachment; filename="photo-library.json"',
            response.headers["content-disposition"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
