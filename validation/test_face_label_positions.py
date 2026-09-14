"""Isolated API regressions for per-photo face-label positions."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"


class FaceLabelPositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
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
    def tearDownClass(cls) -> None:
        cls.context.__exit__(None, None, None)
        cls.module.shutdown_application()
        sys.modules.pop("app", None)
        for key, value in cls.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.temp.cleanup()

    def setUp(self) -> None:
        with self.module.db() as connection:
            for table in (
                "operation_items",
                "operations",
                "edits",
                "face_label_overrides",
                "faces",
                "people",
                "files",
                "assets",
            ):
                connection.execute(f"DELETE FROM {table}")
            for asset_id in (1, 2):
                connection.execute(
                    """INSERT INTO assets(
                         id,sha256,metadata,created_at,face_state,excluded,
                         exclude_reason,derivative_policy,width,height
                       ) VALUES (?,?,'{}',datetime('now'),1,0,'','preserve',1000,700)""",
                    (asset_id, f"{asset_id:064x}"),
                )
                connection.execute(
                    """INSERT INTO files(
                         asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
                       ) VALUES (?,?,1,1,datetime('now'),1,0)""",
                    (asset_id, str(WORK / f"face-label-{asset_id}.jpg")),
                )
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored) VALUES (1,'测试人物',1,0)"
            )
            for face_id, asset_id in ((101, 1), (202, 2)):
                connection.execute(
                    """INSERT INTO faces(
                         id,asset_id,person_id,bbox,embedding,score,reviewed,ignored
                       ) VALUES (?,?,?,?,?,0.99,1,0)""",
                    (
                        face_id,
                        asset_id,
                        1,
                        json.dumps([100, 100, 220, 260, 1000, 700]),
                        b"\0" * 16,
                    ),
                )

    def save_position(
        self,
        asset_id: int = 1,
        face_id: int = 101,
        *,
        x_ratio: float = 0.42,
        y_ratio: float = 0.36,
        operation_id: str | None = None,
    ):
        operation_id = operation_id or str(uuid.uuid4())
        return self.client.put(
            f"/api/photos/{asset_id}/faces/{face_id}/label-position",
            headers={"Idempotency-Key": operation_id},
            json={
                "x_ratio": x_ratio,
                "y_ratio": y_ratio,
                "operation_id": operation_id,
            },
        )

    def test_position_write_is_idempotent_and_returned_by_photo_detail(self) -> None:
        operation_id = str(uuid.uuid4())
        response = self.save_position(operation_id=operation_id)
        self.assertEqual(200, response.status_code, response.text)
        receipt = response.json()
        self.assertTrue(receipt["state_committed"])
        self.assertEqual("committed", receipt["status"])
        self.assertAlmostEqual(0.42, receipt["x_ratio"])
        self.assertAlmostEqual(0.36, receipt["y_ratio"])

        duplicate = self.save_position(operation_id=operation_id)
        self.assertEqual(receipt, duplicate.json())

        detail = self.client.get("/api/photos/1")
        self.assertEqual(200, detail.status_code, detail.text)
        face = detail.json()["faces"][0]
        self.assertAlmostEqual(0.42, face["label_x_ratio"])
        self.assertAlmostEqual(0.36, face["label_y_ratio"])

        with self.module.db() as connection:
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT count(*) FROM face_label_overrides WHERE face_id=101"
                ).fetchone()[0],
            )
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT count(*) FROM edits WHERE target='face:101:label-position'"
                ).fetchone()[0],
            )

    def test_position_cannot_be_written_to_a_face_from_another_photo(self) -> None:
        response = self.save_position(asset_id=1, face_id=202)
        self.assertEqual(404, response.status_code, response.text)
        with self.module.db() as connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT count(*) FROM face_label_overrides"
                ).fetchone()[0],
            )

    def test_position_validation_rejects_out_of_bounds_values(self) -> None:
        response = self.save_position(x_ratio=1.01)
        self.assertEqual(422, response.status_code, response.text)
        response = self.save_position(y_ratio=-0.01)
        self.assertEqual(422, response.status_code, response.text)

    def test_reset_clears_only_the_current_photos_positions_and_is_idempotent(self) -> None:
        self.assertEqual(200, self.save_position().status_code)
        self.assertEqual(
            200,
            self.save_position(asset_id=2, face_id=202, x_ratio=0.7, y_ratio=0.5).status_code,
        )
        operation_id = str(uuid.uuid4())
        response = self.client.post(
            "/api/photos/1/face-labels/reset",
            headers={"Idempotency-Key": operation_id},
        )
        self.assertEqual(200, response.status_code, response.text)
        receipt = response.json()
        self.assertEqual(1, receipt["cleared"])
        self.assertTrue(receipt["state_committed"])

        duplicate = self.client.post(
            "/api/photos/1/face-labels/reset",
            headers={"Idempotency-Key": operation_id},
        )
        self.assertEqual(receipt, duplicate.json())
        with self.module.db() as connection:
            rows = connection.execute(
                "SELECT asset_id,face_id FROM face_label_overrides ORDER BY face_id"
            ).fetchall()
            self.assertEqual([(2, 202)], [tuple(row) for row in rows])


if __name__ == "__main__":
    unittest.main()
