# Isolated recommendation rules. Never opens the production library.
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"


def insert_asset(connection, asset_id, *, captured_at=None, date_source="EXIF 原始拍摄时间",
                 date_precision="日", manual_date=None, manual_precision=None, place=None,
                 manual_place=None, excluded=0, category="照片", exists_now=1, file_excluded=0,
                 extra_file=False, favorite=0, width=1200, height=800):
    connection.execute(
        """INSERT INTO assets(
             id,sha256,width,height,format,metadata,captured_at,date_source,date_precision,
             place,manual_place,manual_date,manual_precision,category,created_at,face_state,
             excluded,exclude_reason,notes,favorite
           ) VALUES (?,?,?,?, 'JPEG','{}',?,?,?,?,?,?,?,?, datetime('now'),1,?,'','fixture',?)""",
        (asset_id, f"{asset_id:064x}", width, height, captured_at, date_source, date_precision,
         place, manual_place, manual_date, manual_precision, category, excluded, favorite),
    )
    connection.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES (?,?,1,1,datetime('now'),?,?)""",
        (asset_id, str(WORK / f"home-{asset_id}.jpg"), exists_now, file_excluded),
    )
    if extra_file:
        connection.execute(
            """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
               VALUES (?,?,1,1,datetime('now'),1,0)""",
            (asset_id, str(WORK / f"home-{asset_id}-copy.jpg")),
        )


def insert_person(connection, person_id, name, *, confirmed=1, ignored=0, suggested=None):
    connection.execute(
        "INSERT INTO people(id,name,confirmed,ignored,suggested_person_id) VALUES (?,?,?,?,?)",
        (person_id, name, confirmed, ignored, suggested),
    )


def insert_face(connection, face_id, asset_id, person_id, bbox='[0,0,10,10,20,20]'):
    connection.execute(
        "INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed) VALUES (?,?,?,?,x'00',0.9,1)",
        (face_id, asset_id, person_id, bbox),
    )


class HomeRecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=WORK)
        cls.data = Path(cls.temp.name) / "data"
        cls.previous = {key: os.environ.get(key) for key in (
            "PHOTO_LIBRARY_DATA", "PHOTO_MODEL_ROOT", "PHOTO_GEO_ROOT", "PHOTO_HOME_AS_OF"
        )}
        os.environ["PHOTO_LIBRARY_DATA"] = str(cls.data)
        os.environ["PHOTO_MODEL_ROOT"] = str(cls.data / "no-model")
        os.environ["PHOTO_GEO_ROOT"] = str(cls.data / "no-geo")
        os.environ["PHOTO_HOME_AS_OF"] = "2026-09-18"
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
        sys.modules.pop("home_recommendations", None)
        for key, value in cls.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.temp.cleanup()

    def setUp(self):
        os.environ["PHOTO_HOME_AS_OF"] = "2026-09-18"
        with self.module.db() as connection:
            for table in ("home_snapshots", "home_recommendation_cache", "home_catalog",
                          "home_catalog_state", "operation_items",
                          "operations", "edits", "faces", "people", "files", "assets"):
                try:
                    connection.execute(f"DELETE FROM {table}")
                except Exception:
                    pass

    def rec(self, category="all", cursor=""):
        return self.client.get("/api/home/recommendations", params={"category": category, "cursor": cursor})

    def test_d01_on_this_day_uses_precise_past_years_only(self):
        with self.module.db() as c:
            insert_asset(c, 1, captured_at="2023-09-18T10:00:00")
            insert_asset(c, 2, captured_at="2024-09-18T10:00:00")
            insert_asset(c, 3, captured_at="2025-09-18T10:00:00")
            insert_asset(c, 4, captured_at="2026-09-18T10:00:00")
            insert_asset(c, 5, captured_at="2024-09-18T10:00:00", date_source="文件修改时间参考")
            insert_asset(c, 6, captured_at="2020-01-01T00:00:00", manual_date="2021", manual_precision="年")
            insert_asset(c, 7, captured_at="2022-09-18T10:00:00", manual_date="2022-09", manual_precision="月")
        data = self.rec("on_this_day").json()
        self.assertEqual(200, self.rec("on_this_day").status_code)
        groups = data["items"]
        days = [g["group_id"] for g in groups]
        self.assertEqual(["day:2025-09-18", "day:2024-09-18", "day:2023-09-18"], days)
        opened = self.client.post("/api/home/groups/open", json={"group_id": "day:2025-09-18"}).json()
        photos = self.client.get("/api/photos", params={"recommendation_snapshot": opened["snapshot_id"]}).json()
        self.assertEqual([3], [item["id"] for item in photos["items"]])

    def test_exif_second_precision_counts_as_calendar_day(self):
        with self.module.db() as c:
            insert_asset(c, 1, captured_at="2019-09-18T18:39:24", date_precision="秒")
            insert_asset(c, 2, captured_at="2019-09-18T18:41:57", date_precision="秒")
            insert_asset(c, 3, captured_at="2019-09-18T10:00:00", date_source="文件修改时间参考", date_precision="秒")
        data = self.rec("on_this_day").json()
        self.assertEqual(["day:2019-09-18"], [g["group_id"] for g in data["items"]])
        opened = self.client.post("/api/home/groups/open", json={"group_id": "day:2019-09-18"}).json()
        photos = self.client.get("/api/photos", params={"recommendation_snapshot": opened["snapshot_id"]}).json()
        self.assertEqual([1, 2], [item["id"] for item in photos["items"]])

    def test_cover_prefers_faces_over_huge_empty_frame(self):
        with self.module.db() as c:
            insert_asset(c, 1, captured_at="2011-09-18T10:00:00", date_precision="秒", width=4000, height=3000)
            insert_asset(c, 2, captured_at="2011-09-18T11:00:00", date_precision="秒", width=800, height=600)
            insert_person(c, 1, "小明")
            insert_face(c, 1, 2, 1)
        data = self.rec("on_this_day").json()
        self.assertEqual([2], data["items"][0]["cover_asset_ids"])

    def test_place_ranks_older_visit_ahead_of_last_week(self):
        with self.module.db() as c:
            for i in range(3):
                insert_asset(c, 10+i, captured_at=f"2026-09-0{6+i}T10:00:00", date_precision="秒", place="北京 · 通州 · 湾里")
            for i in range(3):
                insert_asset(c, 20+i, captured_at=f"2024-05-1{i+1}T10:00:00", date_precision="秒", place="杭州 · 西湖")
        data = self.rec("place_revisit").json()
        titles = [g["title"] for g in data["items"]]
        self.assertTrue(titles)
        self.assertIn("西湖", titles[0])
        self.assertNotIn("湾里", titles[0])

    def test_d02_falls_back_to_same_month_then_empty(self):
        with self.module.db() as c:
            insert_asset(c, 1, captured_at="2024-09-03T10:00:00")
            insert_asset(c, 2, captured_at="2023-09-20T10:00:00", date_precision="月")
        data = self.rec("on_this_day").json()
        self.assertTrue(data["items"])
        self.assertEqual("on_this_month", data["items"][0]["kind"])
        self.assertIn("那年这个月", data["items"][0]["subtitle"])
        with self.module.db() as c:
            c.execute("DELETE FROM files"); c.execute("DELETE FROM assets")
        data = self.rec("on_this_day").json()
        self.assertEqual([], data["items"])

    def test_l01_splits_gap_and_caps_seven_days(self):
        with self.module.db() as c:
            for day in (1, 2, 4):
                insert_asset(c, day, captured_at=f"2024-08-{day:02d}T10:00:00", place="杭州 · 西湖")
            for day in range(1, 11):
                insert_asset(c, 20 + day, captured_at=f"2023-07-{day:02d}T10:00:00", place="杭州 · 西湖")
        data = self.rec("place_revisit").json()
        groups = data["items"]
        self.assertTrue(groups)
        ten = [g for g in data["candidates"]["place_revisit"] if g["date_from"].startswith("2023-07")]
        self.assertEqual(["2023-07-01"], [g["date_from"] for g in ten if g["date_from"] == "2023-07-01"][:1])
        first = next(g for g in ten if g["date_from"] == "2023-07-01")
        self.assertEqual("2023-07-07", first["date_to"])
        self.assertEqual(7, first["photo_count"])
        second = next(g for g in ten if g["date_from"] == "2023-07-08")
        self.assertEqual(3, second["photo_count"])
        short = [g for g in data["candidates"]["place_revisit"] if g["date_from"].startswith("2024-08")]
        self.assertEqual([], short)

    def test_l02_does_not_merge_same_street_different_city(self):
        with self.module.db() as c:
            for i, place in enumerate(("杭州 · 西湖路", "南京 · 西湖路", "杭州 · 西湖路"), 1):
                insert_asset(c, i, captured_at=f"2024-05-0{i}T10:00:00", place=place)
            insert_asset(c, 4, captured_at="2024-05-02T12:00:00", place="杭州 · 西湖路")
        data = self.rec("place_revisit").json()
        titles = {g["title"] for g in data["candidates"]["place_revisit"]}
        self.assertIn("杭州 · 西湖路", titles)
        self.assertTrue(all("南京" not in (g.get("place") or "") or g["photo_count"] < 3 for g in data["candidates"]["place_revisit"]))

    def test_p01_p02_people_years_and_dedupe(self):
        with self.module.db() as c:
            insert_person(c, 1, "林女士")
            insert_person(c, 2, "路人甲", confirmed=1, ignored=1)
            insert_person(c, 3, None, confirmed=0, suggested=1)
            insert_asset(c, 10, captured_at="2012-01-01T10:00:00")
            insert_asset(c, 11, captured_at="2020-01-01T10:00:00")
            insert_asset(c, 12, captured_at="2020-06-01T10:00:00", extra_file=True)
            insert_face(c, 1, 10, 1)
            insert_face(c, 2, 11, 1)
            insert_face(c, 3, 12, 1)
            insert_face(c, 4, 12, 1)
            insert_asset(c, 13, captured_at="2021-01-01T10:00:00")
            insert_face(c, 5, 13, 2)
            insert_asset(c, 14, captured_at="2021-02-01T10:00:00")
            insert_face(c, 6, 14, 3)
        data = self.rec("people_years").json()
        groups = data["items"]
        self.assertEqual(1, len(groups))
        self.assertEqual("林女士", groups[0]["title"])
        self.assertEqual(3, groups[0]["photo_count"])
        opened = self.client.post("/api/home/groups/open", json={"group_id": groups[0]["group_id"]}).json()
        photos = self.client.get("/api/photos", params={"recommendation_snapshot": opened["snapshot_id"], "limit": 60}).json()
        self.assertEqual([10, 11, 12], [item["id"] for item in photos["items"]])

    def test_q01_excludes_unusable_assets(self):
        with self.module.db() as c:
            insert_asset(c, 1, captured_at="2024-09-18T10:00:00", excluded=1)
            insert_asset(c, 2, captured_at="2023-09-18T10:00:00", exists_now=0)
            insert_asset(c, 3, captured_at="2022-09-18T10:00:00", category="截图")
            insert_asset(c, 4, captured_at="2021-09-18T10:00:00", category="小图 / 素材")
            insert_asset(c, 5, captured_at="2020-09-18T10:00:00")
        data = self.rec("on_this_day").json()
        self.assertEqual(["day:2020-09-18"], [g["group_id"] for g in data["items"]])

    def test_b01_snapshot_pagination_matches_frozen_set(self):
        with self.module.db() as c:
            for i in range(1, 126):
                insert_asset(c, i, captured_at="2018-09-18T10:00:00")
        opened = self.client.post("/api/home/groups/open", json={"group_id": "day:2018-09-18"}).json()
        self.assertEqual(125, opened["photo_count"])
        seen = []
        offset = 0
        while True:
            page = self.client.get("/api/photos", params={
                "recommendation_snapshot": opened["snapshot_id"], "offset": offset, "limit": 60
            }).json()
            ids = [item["id"] for item in page["items"]]
            seen.extend(ids)
            if len(seen) >= page["total"]:
                break
            offset += len(ids)
        self.assertEqual(list(range(1, 126)), seen)
        seq = self.client.get("/api/photos", params={
            "recommendation_snapshot": opened["snapshot_id"], "sequence": "true", "around": 80, "limit": 20
        }).json()
        self.assertIn(80, seq["ids"])
        self.assertEqual(125, seq["total"])

    def test_m01_exclude_expires_snapshot(self):
        with self.module.db() as c:
            insert_asset(c, 1, captured_at="2019-09-18T10:00:00")
            insert_asset(c, 2, captured_at="2019-09-18T11:00:00")
        opened = self.client.post("/api/home/groups/open", json={"group_id": "day:2019-09-18"}).json()
        with self.module.db() as c:
            c.execute("UPDATE assets SET excluded=1 WHERE id=1")
        expired = self.client.get("/api/photos", params={"recommendation_snapshot": opened["snapshot_id"]})
        self.assertEqual(409, expired.status_code)
        self.assertEqual("recommendation_expired", expired.json()["error_code"])

    def test_s01_same_day_order_is_stable(self):
        with self.module.db() as c:
            insert_asset(c, 1, captured_at="2017-09-18T10:00:00")
            insert_asset(c, 2, captured_at="2016-09-18T10:00:00")
        first = self.rec("on_this_day").json()["items"]
        second = self.rec("on_this_day").json()["items"]
        self.assertEqual([g["group_id"] for g in first], [g["group_id"] for g in second])

    def test_u01_organize_undo_expires_or_refreshes_recommendation(self):
        with self.module.db() as c:
            insert_asset(c, 1, captured_at="2015-09-18T10:00:00", place="旧地点")
            insert_asset(c, 2, captured_at="2015-09-18T11:00:00", place="旧地点")
        opened = self.client.post("/api/home/groups/open", json={"group_id": "day:2015-09-18"}).json()
        edited = self.client.patch("/api/organize/photos", json={"ids": [1], "manual_date": "2014-01-01", "manual_precision": "日"})
        self.assertEqual(200, edited.status_code, edited.text)
        expired = self.client.get("/api/photos", params={"recommendation_snapshot": opened["snapshot_id"]})
        self.assertEqual(409, expired.status_code)
        op = edited.json()["operation_id"]
        undone = self.client.post(f"/api/organize/operations/{op}/undo", json={"dry_run": False})
        self.assertEqual(200, undone.status_code, undone.text)
        groups = self.rec("on_this_day").json()["items"]
        self.assertIn("day:2015-09-18", [g["group_id"] for g in groups])



    def test_people_cover_years_bind_to_assets_across_span(self):
        years = [2010, 2012, 2014, 2016, 2018, 2024]
        with self.module.db() as c:
            insert_person(c, 1, '林女士')
            for index, year in enumerate(years, start=1):
                insert_asset(c, index, captured_at=f"{year}-06-01T10:00:00", width=1200, height=800)
                insert_face(c, index, index, 1, bbox='[200,80,700,680,1200,800]')
        data = self.rec("people_years").json()
        card = data["items"][0]
        covers = card["covers"]
        self.assertEqual(4, len(covers))
        self.assertEqual(["2010", "2024"], [covers[0]["year"], covers[-1]["year"]])
        self.assertEqual(["2010", "2014", "2018", "2024"], [item["year"] for item in covers])
        by_id = {item["asset_id"]: item["year"] for item in covers}
        with self.module.db() as c:
            for asset_id, year in by_id.items():
                captured = c.execute("SELECT substr(captured_at,1,4) FROM assets WHERE id=?", (asset_id,)).fetchone()[0]
                face_id = next(item["target_face_id"] for item in covers if item["asset_id"] == asset_id)
                face = c.execute("SELECT id, person_id FROM faces WHERE id=?", (face_id,)).fetchone()
                self.assertEqual(year, captured)
                self.assertEqual(1, face[1])
        self.assertTrue(all(item["target_face_id"] for item in covers))

    def test_people_cover_prefers_large_target_face_not_group_photo(self):
        with self.module.db() as c:
            insert_person(c, 1, '林女士')
            insert_asset(c, 1, captured_at="2012-01-01T10:00:00", width=1600, height=1000)
            insert_asset(c, 2, captured_at="2012-06-01T10:00:00", width=1600, height=1000)
            insert_asset(c, 3, captured_at="2022-01-01T10:00:00", width=900, height=1200)
            insert_face(c, 11, 1, 1, bbox='[20,20,70,80,1600,1000]')
            insert_face(c, 12, 2, 1, bbox='[500,120,1100,900,1600,1000]')
            insert_face(c, 13, 3, 1, bbox='[200,80,700,900,900,1200]')
        data = self.rec("people_years").json()
        card = data["items"][0]
        covers_2012 = [item for item in card["covers"] if item["year"] == "2012"]
        self.assertTrue(covers_2012)
        self.assertEqual(2, covers_2012[0]["asset_id"])
        self.assertEqual(12, covers_2012[0]["target_face_id"])

    def test_place_cover_does_not_prefer_faces(self):
        with self.module.db() as c:
            insert_person(c, 1, '林女士')
            for i in range(3):
                insert_asset(c, 10+i, captured_at=f"2024-01-1{i+1}T10:00:00", place='辽宁省 · 沈阳市 · 沈河区', width=600, height=900)
            insert_face(c, 1, 10, 1, bbox='[80,80,500,800,600,900]')
            insert_asset(c, 20, captured_at="2024-01-12T10:00:00", place='辽宁省 · 沈阳市 · 沈河区', width=1600, height=900, favorite=1)
        data = self.rec("place_revisit").json()
        card = data["items"][0]
        self.assertEqual([20], card["cover_asset_ids"])
        self.assertIn(chr(0x6C88)+chr(0x9633), card["title"])
        self.assertIn(chr(0x6C88)+chr(0x6CB3)+chr(0x533A), card["title"])
        self.assertNotIn(chr(0x8FBD)+chr(0x5B81)+chr(0x7701), card["title"])
        self.assertIn("2024", card["subtitle"])
        self.assertIn("4", card["subtitle"])

    def test_catalog_keeps_people_until_rebuild(self):
        with self.module.db() as c:
            insert_person(c, 1, "林女士")
            insert_asset(c, 10, captured_at="2012-01-01T10:00:00")
            insert_asset(c, 11, captured_at="2020-01-01T10:00:00")
            insert_face(c, 1, 10, 1)
            insert_face(c, 2, 11, 1)
        first = self.rec("people_years").json()
        self.assertEqual(["林女士"], [g["title"] for g in first["items"]])
        self.assertEqual("ready", first.get("catalog", {}).get("status"))
        with self.module.db() as c:
            insert_person(c, 2, "王先生")
            insert_asset(c, 20, captured_at="2015-01-01T10:00:00")
            insert_asset(c, 21, captured_at="2021-01-01T10:00:00")
            insert_face(c, 3, 20, 2)
            insert_face(c, 4, 21, 2)
            c.execute("DELETE FROM home_recommendation_cache")
        stale = self.rec("people_years").json()
        self.assertEqual(["林女士"], [g["title"] for g in stale["items"]])
        rebuilt = self.client.post("/api/home/catalog/rebuild")
        self.assertEqual(200, rebuilt.status_code, rebuilt.text)
        self.assertEqual("ready", rebuilt.json()["status"])
        self.assertEqual("home-discovery-v5", rebuilt.json()["algorithm_version"])
        fresh = self.rec("people_years").json()
        self.assertEqual({"林女士", "王先生"}, {g["title"] for g in fresh["items"]})

if __name__ == "__main__":
    unittest.main()
