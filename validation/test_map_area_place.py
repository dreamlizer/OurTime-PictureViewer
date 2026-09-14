"""Isolated regressions for rectangle-based manual place editing."""
from __future__ import annotations

import importlib
import json
import math
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"


def insert_asset(
    connection,
    asset_id: int,
    latitude: float | None,
    longitude: float | None,
    *,
    manual_place: str | None = None,
    place: str | None = "原始地点",
    excluded: int = 0,
) -> None:
    connection.execute(
        """INSERT INTO assets(
             id,sha256,metadata,created_at,face_state,excluded,exclude_reason,
             derivative_policy,latitude,longitude,place,manual_place,
             captured_at,notes,favorite
           ) VALUES (?,?,'{}',datetime('now'),0,?,'','preserve',?,?,?,?,
                     '2026-01-02T03:04:05','保留备注',1)""",
        (
            asset_id,
            f"{asset_id:064x}",
            excluded,
            latitude,
            longitude,
            place,
            manual_place,
        ),
    )
    connection.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES (?,?,1,1,datetime('now'),1,0)""",
        (asset_id, str(WORK / f"map-area-{asset_id}.jpg")),
    )


class MapAreaPlaceTests(unittest.TestCase):
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
                "operation_items", "operations", "edits", "faces", "people",
                "files", "assets",
            ):
                connection.execute(f"DELETE FROM {table}")

    @staticmethod
    def bounds() -> dict[str, float]:
        return {"west": 116.39, "south": 39.89, "east": 116.41, "north": 39.91}

    def preview(self, coordinate_space: str = "wgs84", bounds=None):
        return self.client.post(
            "/api/places/area/preview",
            json={"coordinate_space": coordinate_space, "bounds": bounds or self.bounds()},
        )

    def apply(self, preview: dict, place: str = "北京 · 测试园区", **changes):
        body = {
            "coordinate_space": preview["coordinate_space"],
            "bounds": preview["bounds"],
            "selection_fingerprint": preview["selection_fingerprint"],
            "place": place,
            "confirm_many": False,
            "operation_id": str(uuid.uuid4()),
        }
        body.update(changes)
        return self.client.post("/api/places/area/apply", json=body)

    def test_preview_uses_complete_active_unique_asset_selection(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, 39.90, 116.40)
            insert_asset(connection, 2, 39.89, 116.39, manual_place="旧人工地点")
            insert_asset(connection, 3, 39.92, 116.42)
            insert_asset(connection, 4, 39.90, 116.40, excluded=1)
            insert_asset(connection, 5, None, None)
            connection.execute(
                """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
                   VALUES (1,?,1,1,datetime('now'),1,0)""",
                (str(WORK / "map-area-duplicate-copy.jpg"),),
            )
        response = self.preview()
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertEqual(2, payload["matched"])
        self.assertEqual(1, payload["manual_place_count"])
        self.assertEqual([1, 2], [item["id"] for item in payload["samples"]])
        self.assertLessEqual(len(payload["samples"]), 9)
        self.assertNotIn(str(WORK), response.text)

    def test_gcj_conversion_matches_fixed_reference_and_display_selection(self) -> None:
        converted = self.module.map_area_wgs84_to_gcj02(39.9042, 116.4074)
        self.assertAlmostEqual(39.9056033432, converted[0], places=7)
        self.assertAlmostEqual(116.4136422538, converted[1], places=7)
        self.assertEqual((-20.0, -30.0), self.module.map_area_wgs84_to_gcj02(-20, -30))
        with self.module.db() as connection:
            insert_asset(connection, 1, 39.9042, 116.4074)
            insert_asset(connection, 2, 39.9042, 116.4200)
        bounds = {
            "west": converted[1] - 0.00001,
            "south": converted[0] - 0.00001,
            "east": converted[1] + 0.00001,
            "north": converted[0] + 0.00001,
        }
        response = self.preview("gcj02", bounds)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual([1], [item["id"] for item in response.json()["samples"]])

    def test_invalid_requests_and_empty_selection_do_not_write(self) -> None:
        cases = [
            {"coordinate_space": "unknown", "bounds": self.bounds()},
            {
                "coordinate_space": "wgs84",
                "bounds": {"west": 2, "south": 0, "east": 1, "north": 1},
            },
            {
                "coordinate_space": "wgs84",
                "bounds": {"west": -181, "south": 0, "east": 1, "north": 1},
            },
        ]
        for body in cases:
            response = self.client.post("/api/places/area/preview", json=body)
            self.assertIn(response.status_code, (400, 422), response.text)
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                self.module.normalize_map_area(
                    "wgs84", {"west": value, "south": 0, "east": 1, "north": 1}
                )
        empty = self.preview().json()
        self.assertEqual(0, empty["matched"])
        response = self.apply(empty)
        self.assertEqual(400, response.status_code, response.text)
        with self.module.db() as connection:
            self.assertEqual(0, connection.execute("SELECT count(*) FROM edits").fetchone()[0])

    def test_selection_changes_reject_entire_batch_but_outside_change_does_not(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, 39.90, 116.40)
            insert_asset(connection, 2, 40.00, 116.50)
        preview = self.preview().json()
        with self.module.db() as connection:
            connection.execute("UPDATE assets SET manual_place='框外变化' WHERE id=2")
        okay = self.apply(preview)
        self.assertEqual(200, okay.status_code, okay.text)
        self.assertEqual(1, okay.json()["updated"])

        with self.module.db() as connection:
            connection.execute("UPDATE assets SET manual_place=NULL WHERE id=1")
        changed_preview = self.preview().json()
        with self.module.db() as connection:
            connection.execute("UPDATE assets SET manual_place='并发变化' WHERE id=1")
        conflict = self.apply(changed_preview, place="另一个地点")
        self.assertEqual(409, conflict.status_code, conflict.text)
        self.assertEqual("selection_changed", conflict.json()["error_code"])
        with self.module.db() as connection:
            row = connection.execute("SELECT manual_place FROM assets WHERE id=1").fetchone()
            self.assertEqual("并发变化", row[0])

    def test_membership_and_gps_changes_are_detected_even_when_count_is_same(self) -> None:
        scenarios = ("new_member", "excluded_member", "same_count_swap", "gps_change")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with self.module.db() as connection:
                    for table in ("operations", "edits", "files", "assets"):
                        connection.execute(f"DELETE FROM {table}")
                    insert_asset(connection, 1, 39.9000, 116.4000)
                    if scenario == "same_count_swap":
                        insert_asset(connection, 2, 39.9200, 116.4200)
                preview = self.preview().json()
                with self.module.db() as connection:
                    if scenario == "new_member":
                        insert_asset(connection, 2, 39.9010, 116.4010)
                    elif scenario == "excluded_member":
                        connection.execute("UPDATE assets SET excluded=1 WHERE id=1")
                    elif scenario == "same_count_swap":
                        connection.execute(
                            """UPDATE assets
                               SET latitude=CASE id WHEN 1 THEN 39.92 ELSE 39.90 END,
                                   longitude=CASE id WHEN 1 THEN 116.42 ELSE 116.40 END
                               WHERE id IN (1,2)"""
                        )
                    else:
                        connection.execute(
                            "UPDATE assets SET latitude=39.9001 WHERE id=1"
                        )
                response = self.apply(preview)
                self.assertEqual(409, response.status_code, response.text)
                self.assertEqual("selection_changed", response.json()["error_code"])
                with self.module.db() as connection:
                    self.assertEqual(
                        0,
                        connection.execute(
                            "SELECT count(*) FROM assets WHERE manual_place IS NOT NULL"
                        ).fetchone()[0],
                    )
                    self.assertEqual(
                        0, connection.execute("SELECT count(*) FROM edits").fetchone()[0]
                    )

    def test_transaction_rolls_back_assets_logs_and_operation(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, 39.90, 116.40)
            insert_asset(connection, 2, 39.901, 116.401)
        preview = self.preview().json()
        with self.module.db() as connection:
            connection.execute(
                """CREATE TRIGGER fail_second_area_write
                   BEFORE UPDATE OF manual_place ON assets
                   WHEN NEW.id=2
                   BEGIN SELECT RAISE(ABORT,'synthetic area failure'); END"""
            )
        operation_id = str(uuid.uuid4())
        response = self.apply(preview, operation_id=operation_id)
        self.assertEqual(500, response.status_code, response.text)
        with self.module.db() as connection:
            values = [
                row[0] for row in connection.execute(
                    "SELECT manual_place FROM assets ORDER BY id"
                )
            ]
            self.assertEqual([None, None], values)
            self.assertEqual(0, connection.execute("SELECT count(*) FROM edits").fetchone()[0])
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT count(*) FROM operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()[0],
            )
            connection.execute("DROP TRIGGER fail_second_area_write")

    def test_idempotent_receipt_and_field_level_audit(self) -> None:
        with self.module.db() as connection:
            insert_asset(connection, 1, 39.90, 116.40, manual_place=None)
            insert_asset(connection, 2, 39.901, 116.401, manual_place="北京 · 测试园区")
        preview = self.preview().json()
        operation_id = str(uuid.uuid4())
        first = self.apply(preview, operation_id=operation_id)
        second = self.apply(preview, operation_id=operation_id)
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual((2, 1, 1), (
            first.json()["matched"], first.json()["updated"], first.json()["unchanged"]
        ))
        conflict = self.apply(preview, place="不同地点", operation_id=operation_id)
        self.assertEqual(409, conflict.status_code, conflict.text)
        self.assertEqual("idempotency_conflict", conflict.json()["error_code"])
        receipt = self.client.get(f"/api/operations/{operation_id}")
        receipt_payload = receipt.json()
        self.assertEqual([], receipt_payload.pop("items"))
        self.assertEqual(first.json(), receipt_payload)
        with self.module.db() as connection:
            row = connection.execute(
                """SELECT latitude,longitude,place,captured_at,notes,favorite,manual_place
                   FROM assets WHERE id=1"""
            ).fetchone()
            self.assertEqual(
                (39.90, 116.40, "原始地点", "2026-01-02T03:04:05",
                 "保留备注", 1, "北京 · 测试园区"),
                tuple(row),
            )
            edits = connection.execute(
                "SELECT before_json,after_json FROM edits WHERE target='asset:1'"
            ).fetchall()
            self.assertEqual(1, len(edits))
            self.assertEqual({"manual_place": None}, json.loads(edits[0][0]))
            after = json.loads(edits[0][1])
            self.assertEqual("map_rectangle", after["source"])
            self.assertEqual(operation_id, after["operation_id"])
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT count(*) FROM edits WHERE target='asset:2'"
                ).fetchone()[0],
            )

    def test_more_than_one_thousand_requires_confirmation_and_updates_all(self) -> None:
        with self.module.db() as connection:
            for asset_id in range(1, 1502):
                insert_asset(
                    connection,
                    asset_id,
                    39.895 + (asset_id % 100) * 0.0001,
                    116.395 + (asset_id % 100) * 0.0001,
                )
        preview = self.preview().json()
        self.assertEqual(1501, preview["matched"])
        self.assertTrue(preview["requires_large_confirmation"])
        refused = self.apply(preview)
        self.assertEqual(409, refused.status_code, refused.text)
        self.assertEqual(
            "large_selection_confirmation_required", refused.json()["error_code"]
        )
        applied = self.apply(preview, confirm_many=True)
        self.assertEqual(200, applied.status_code, applied.text)
        self.assertEqual(1501, applied.json()["updated"])
        with self.module.db() as connection:
            self.assertEqual(
                1501,
                connection.execute(
                    "SELECT count(*) FROM assets WHERE manual_place='北京 · 测试园区'"
                ).fetchone()[0],
            )
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], list(connection.execute("PRAGMA foreign_key_check")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
