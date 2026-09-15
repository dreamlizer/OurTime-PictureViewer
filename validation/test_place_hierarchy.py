"""Focused checks for hierarchical place labels and map-area suggestions."""
from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from geo_labels import PlaceIndex, common_admin_scope, merge_manual_place
from tools.relabel_place_hierarchy import apply_plan, build_plan


def write_admin_rows(root: Path) -> None:
    target = root / "china-admin" / "extracted" / "ok_data_level4.csv"
    target.parent.mkdir(parents=True)
    rows = [
        ("11", "0", "0", "北京", "b", "bei jing", "110000000000", "北京市"),
        ("1101", "11", "1", "北京", "b", "bei jing", "110100000000", "北京市"),
        ("110105", "1101", "2", "朝阳", "c", "chao yang", "110105000000", "朝阳区"),
        ("110105001", "110105", "3", "双井", "s", "shuang jing", "110105001000", "双井街道"),
        ("110112", "1101", "2", "通州", "t", "tong zhou", "110112000000", "通州区"),
        ("44", "0", "0", "广东", "g", "guang dong", "440000000000", "广东省"),
        ("4401", "44", "1", "广州", "g", "guang zhou", "440100000000", "广州市"),
        ("440106", "4401", "2", "天河", "t", "tian he", "440106000000", "天河区"),
        ("440106001", "440106", "3", "五山", "w", "wu shan", "440106001000", "五山街道"),
        ("13", "0", "0", "河北", "h", "he bei", "130000000000", "河北省"),
        ("1304", "13", "1", "邯郸", "h", "han dan", "130400000000", "邯郸市"),
        ("130425", "1304", "2", "大名", "d", "da ming", "130425000000", "大名县"),
        ("130425001", "130425", "3", "朝内", "c", "chao nei", "130425001000", "朝内街道"),
        ("23", "0", "0", "黑龙江", "h", "hei long jiang", "230000000000", "黑龙江省"),
        ("2305", "23", "1", "双鸭山", "s", "shuang ya shan", "230500000000", "双鸭山市"),
        ("230506", "2305", "2", "宝山", "b", "bao shan", "230506000000", "宝山区"),
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ("id", "pid", "deep", "name", "pinyin_prefix", "pinyin", "ext_id", "ext_name")
        )
        writer.writerows(rows)


def write_cities(root: Path) -> None:
    target = root / "geonames" / "cities500.zip"
    target.parent.mkdir(parents=True)

    def record(name, ascii_name, alternates, latitude, longitude):
        fields = [
            "1", name, ascii_name, alternates, str(latitude), str(longitude),
            "P", "PPL", "CN", "", "", "", "", "", "1000",
        ]
        return "\t".join(fields)

    content = "\n".join(
        [
            record("双井街道", "Shuangjing", "双井街道", 39.900, 116.470),
            record("五山街道", "Wushan", "五山街道", 23.150, 113.350),
        ]
    )
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("cities500.txt", content.encode("utf-8"))


class PlaceHierarchyTests(unittest.TestCase):
    def test_new_scans_receive_full_domestic_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            write_admin_rows(root)
            write_cities(root)
            index = PlaceIndex(root)

            self.assertEqual(
                "北京市 · 朝阳区 · 双井街道",
                index.nearest(39.900, 116.470)[0],
            )
            self.assertEqual(
                "广东省 · 广州市 · 天河区 · 五山街道",
                index.nearest(23.150, 113.350)[0],
            )
            index.set_overrides(
                [
                    {
                        "name": "北京 · 西坝河芳馨园",
                        "latitude": 39.900,
                        "longitude": 116.470,
                        "radius_km": 0.5,
                    }
                ],
                "test",
            )
            self.assertEqual(
                "北京市 · 朝阳区 · 西坝河芳馨园",
                index.nearest(39.900, 116.470)[0],
            )

    def test_manual_detail_is_kept_but_domestic_prefix_is_normalized(self) -> None:
        self.assertEqual(
            "北京市 · 朝阳区 · 金港国际",
            merge_manual_place(
                "北京市 · 朝阳区 · 双井街道",
                "中国 · 北京 金港国际",
            ),
        )
        self.assertEqual(
            "广东省 · 梅州市 · 梅县区 · 松口古镇",
            merge_manual_place(
                "广东省 · 梅州市 · 梅县区 · 松口镇",
                "中国 · 广东梅州 松口古镇",
            ),
        )
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            write_admin_rows(root)
            write_cities(root)
            index = PlaceIndex(root)
            self.assertEqual(
                "北京市 · 通州区 · 湾里",
                index.normalize_manual(
                    "甘肃省 · 庆阳市 · 庆城县",
                    "中国 · 北京通州 湾里",
                ),
            )
            self.assertEqual(
                "北京市 · 东城区 · 朝内大街130号",
                index.normalize_manual(
                    "北京市 · 东城区 · 朝内头条社区",
                    "中国 · 朝内大街130号",
                ),
            )
            self.assertEqual(
                "北京市 · 石景山区 · 八宝山革命公墓",
                index.normalize_manual(
                    "北京市 · 石景山区 · 范家坟社区",
                    "中国 · 北京 · 八宝山革命公墓",
                ),
            )

    def test_rectangle_suggestion_degrades_at_administrative_boundaries(self) -> None:
        self.assertEqual(
            "北京市 · 朝阳区",
            common_admin_scope(
                [
                    "北京市 · 朝阳区 · 双井街道",
                    "北京市 · 朝阳区 · 劲松街道",
                ]
            ),
        )
        self.assertEqual(
            "北京市",
            common_admin_scope(
                [
                    "北京市 · 朝阳区 · 双井街道",
                    "北京市 · 海淀区 · 中关村街道",
                ]
            ),
        )
        self.assertEqual(
            "",
            common_admin_scope(
                [
                    "北京市 · 朝阳区 · 双井街道",
                    "广东省 · 广州市 · 天河区 · 五山街道",
                ]
            ),
        )

    def test_existing_automatic_and_manual_places_migrate_together(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE assets(
              id INTEGER PRIMARY KEY, latitude REAL, longitude REAL,
              place TEXT, place_source TEXT, manual_place TEXT
            );
            CREATE TABLE jobs(id TEXT, status TEXT, started_at TEXT);
            CREATE TABLE place_rules(name TEXT);
            CREATE TABLE edits(
              id INTEGER PRIMARY KEY, created_at TEXT, target TEXT,
              before_json TEXT, after_json TEXT
            );
            INSERT INTO assets VALUES(
              1,39.9,116.47,'中国 · 双井街道','旧地名库',
              '中国 · 北京 金港国际'
            );
            """
        )

        class FakeIndex:
            def nearest(self, _lat, _lon, include_overrides):
                self.include_overrides = include_overrides
                return "北京市 · 朝阳区 · 双井街道", "分层地名测试"

            def normalize_manual(self, base, manual):
                return merge_manual_place(base, manual)

        index = FakeIndex()
        _rows, _coordinates, plan = build_plan(connection, index)
        self.assertTrue(index.include_overrides)
        self.assertEqual(1, len(plan))
        self.assertEqual("北京市 · 朝阳区 · 金港国际", plan[0]["manual_place"])
        automatic, manual = apply_plan(connection, plan)
        self.assertEqual((1, 1), (automatic, manual))
        row = connection.execute(
            "SELECT place,manual_place FROM assets WHERE id=1"
        ).fetchone()
        self.assertEqual(
            ("北京市 · 朝阳区 · 双井街道", "北京市 · 朝阳区 · 金港国际"),
            tuple(row),
        )

    def test_migration_does_not_relabel_existing_foreign_places(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE assets(
              id INTEGER PRIMARY KEY, latitude REAL, longitude REAL,
              place TEXT, place_source TEXT, manual_place TEXT
            );
            CREATE TABLE place_rules(name TEXT);
            INSERT INTO assets VALUES(
              1,24.0,98.0,'Mu-se · Myanmar','GeoNames',NULL
            );
            """
        )

        class MustNotResolve:
            def nearest(self, *_args):
                raise AssertionError("foreign place should not be relabelled")

            def normalize_manual(self, _base, manual):
                return manual

        _rows, coordinates, plan = build_plan(connection, MustNotResolve())
        self.assertEqual({}, coordinates)
        self.assertEqual([], plan)


if __name__ == "__main__":
    unittest.main()
