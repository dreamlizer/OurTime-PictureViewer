"""Focused isolated backend regressions for M2 lock ordering and map grids."""
from __future__ import annotations

import importlib
import math
import os
import sqlite3
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


def insert_asset(connection, asset_id, digest, latitude=None, longitude=None):
    connection.execute(
        """INSERT INTO assets(
             id,sha256,metadata,created_at,face_state,excluded,exclude_reason,
             derivative_policy,latitude,longitude,place
           ) VALUES (?,?,'{}',datetime('now'),0,0,'','preserve',?,?,'fixture')""",
        (asset_id, digest, latitude, longitude),
    )
    connection.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES (?,?,1,1,datetime('now'),1,0)""",
        (asset_id, str(WORK / f"m2-{asset_id}.jpg")),
    )


class M2BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=WORK)
        cls.data = Path(cls.temp.name) / "data"
        cls.previous = {
            key: os.environ.get(key)
            for key in ("PHOTO_LIBRARY_DATA", "PHOTO_MODEL_ROOT", "PHOTO_GEO_ROOT")
        }
        os.environ["PHOTO_LIBRARY_DATA"] = str(cls.data)
        os.environ["PHOTO_MODEL_ROOT"] = str(cls.data / "no-model")
        os.environ["PHOTO_GEO_ROOT"] = str(cls.data / "no-geo")
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
        with self.module.db() as connection:
            for table in ("operation_items", "operations", "edits", "faces", "people", "files", "assets"):
                connection.execute(f"DELETE FROM {table}")

    def test_reverse_shard_batches_and_same_shard_collision_complete(self):
        # Lexical order is shard 2 then shard 1; correct acquisition is 1 then 2.
        digests = (
            "00000002" + "a" * 56,
            "00000081" + "b" * 56,
            "00000101" + "c" * 56,
        )
        self.assertEqual([1, 2], self.module.asset_lock_indices(digests[:2]))
        self.assertEqual([1], self.module.asset_lock_indices(digests[1:]))
        with self.module.db() as connection:
            for asset_id, digest in enumerate(digests, 1):
                insert_asset(connection, asset_id, digest)
        gate = threading.Barrier(2)

        def submit(ids):
            gate.wait(timeout=5)
            return self.module.exclude_assets(
                self.module.ExclusionRequest(
                    ids=ids, excluded=True, display_only=True,
                    operation_id=str(uuid.uuid4()),
                ),
                None,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, [1, 2])
            second = pool.submit(submit, [2, 3])
            results = [first.result(timeout=5), second.result(timeout=5)]
        self.assertTrue(all(item["state_committed"] for item in results))

    def test_map_floor_grid_count_click_parity_and_truncation(self):
        cell = 0.02
        points = [(39.90001 + i * 0.0000001, 116.40001 + i * 0.0000001) for i in range(501)]
        points += [(0.01999, 0.01999), (0.02001, 0.02001), (-0.0001, -0.0001)]
        with self.module.db() as connection:
            for asset_id, (latitude, longitude) in enumerate(points, 1):
                insert_asset(connection, asset_id, f"{asset_id:064x}", latitude, longitude)
        response = self.client.get(
            "/api/places?west=-1&south=-1&east=117&north=41&zoom=15"
        )
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        dense = max(payload["clusters"], key=lambda item: item["count"])
        self.assertEqual(501, dense["count"])
        clicked = self.client.get(
            "/api/photos",
            params={
                "map_cell": dense["cell"],
                "map_lat_bucket": dense["lat_bucket"],
                "map_lng_bucket": dense["lng_bucket"],
                "limit": 100,
            },
        ).json()
        self.assertEqual(dense["count"], clicked["total"])
        negative = [
            item for item in payload["clusters"]
            if item["lat_bucket"] == math.floor(-0.0001 / item["cell"])
            and item["lng_bucket"] == math.floor(-0.0001 / item["cell"])
        ]
        self.assertEqual(1, len(negative))
        by_anchor = {item["anchor_id"]: item for item in payload["clusters"]}
        self.assertEqual(0, by_anchor[502]["lat_bucket"])
        self.assertEqual(1, by_anchor[503]["lat_bucket"])
        self.assertEqual(-1, by_anchor[504]["lat_bucket"])
        self.assertIn("truncated", payload)
        self.assertIn("total_clusters", payload)

    def test_map_reports_real_truncation(self):
        with self.module.db() as connection:
            asset_id = 1
            for row in range(21):
                for column in range(20):
                    insert_asset(
                        connection,
                        asset_id,
                        f"{asset_id:064x}",
                        0.001 + row * 0.03,
                        0.001 + column * 0.03,
                    )
                    asset_id += 1
        payload = self.client.get(
            "/api/places?west=0&south=0&east=1&north=1&zoom=15"
        ).json()
        self.assertEqual(400, len(payload["clusters"]))
        self.assertEqual(420, payload["total_clusters"])
        self.assertTrue(payload["truncated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
