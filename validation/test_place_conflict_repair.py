import sqlite3
import unittest
from tools.repair_place_conflicts import build_plan, apply_plan


class Index:
    def _district_at(self,lat,lon): return '北京市 · 朝阳区'
    def nearest(self,lat,lon,include_overrides=False):
        return '北京市 · 朝阳区 · 小营','GeoNames 社区/街区参考'


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:')
        self.c.row_factory=sqlite3.Row
        self.c.executescript('''
          CREATE TABLE assets(id INTEGER PRIMARY KEY,latitude REAL,longitude REAL,place TEXT,place_source TEXT,manual_place TEXT);
          CREATE TABLE jobs(id TEXT,status TEXT,started_at TEXT);
          CREATE TABLE edits(created_at TEXT,target TEXT,before_json TEXT,after_json TEXT);
          INSERT INTO assets VALUES(1,39.99,116.42,'河北省 · 小营镇','OpenStreetMap 社区/街区参考',NULL);
          INSERT INTO assets VALUES(2,39.99,116.42,'河北省 · 小营镇','OpenStreetMap 社区/街区参考','用户地点');
          INSERT INTO assets VALUES(3,39.99,116.42,'北京市 · 朝阳区','GeoNames 社区/街区参考',NULL);
          INSERT INTO assets VALUES(4,39.99,116.42,'河北省 · 小营镇','用户框选区域',NULL);
        ''')

    def tearDown(self): self.c.close()

    def test_only_proven_automatic_conflict_changes_and_is_idempotent(self):
        plan=build_plan(self.c,Index())
        self.assertEqual([1],[x['id'] for x in plan])
        apply_plan(self.c,plan)
        self.assertEqual([],build_plan(self.c,Index()))
        self.assertEqual(1,self.c.execute('SELECT count(*) FROM edits').fetchone()[0])
        self.assertEqual('用户地点',self.c.execute('SELECT manual_place FROM assets WHERE id=2').fetchone()[0])

    def test_concurrent_user_edit_prevents_any_write(self):
        plan=build_plan(self.c,Index())
        self.c.execute("UPDATE assets SET manual_place='刚刚手填' WHERE id=1")
        self.c.commit()
        with self.assertRaises(RuntimeError): apply_plan(self.c,plan)
        self.assertEqual(0,self.c.execute('SELECT count(*) FROM edits').fetchone()[0])

    def test_scan_prevents_writes(self):
        plan=build_plan(self.c,Index())
        self.c.execute("INSERT INTO jobs VALUES('scan','running','now')")
        self.c.commit()
        with self.assertRaises(RuntimeError): apply_plan(self.c,plan)


if __name__=='__main__': unittest.main()
