from pathlib import Path
import math
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from browse_queries import fetch_people, fetch_photos, parse_date_bound
from library_db import init_schema


def seed(conn):
    now = '2026-09-12T00:00:00'
    rows = [
        (1, '2025-05-10', '北京', 0),
        (2, '2025-05-11', '北京', 0),
        (3, '2025-06-01', '上海', 0),
        (4, '2025', '北京', 0),
        (5, '2024-05-10', '北京', 0),
    ]
    for asset_id, date, place, excluded in rows:
        conn.execute('INSERT INTO assets(id,sha256,width,height,format,created_at,captured_at,place,excluded) VALUES(?,?,?,?,?,?,?,?,?)',
                     (asset_id, f'h{asset_id}', 10, 10, 'JPEG', now, date, place, excluded))
        conn.execute('INSERT INTO files(id,asset_id,path,size,mtime_ns,exists_now,excluded) VALUES(?,?,?,?,?,?,?)',
                     (asset_id, asset_id, f'X:\\photos\\{asset_id}.jpg', 1, asset_id, 1, 0))
    conn.execute('INSERT INTO people(id,name,confirmed,alias,ignored) VALUES(1,?,?,?,?)', ('张三', 1, '', 0))
    conn.execute('INSERT INTO people(id,name,confirmed,alias,ignored) VALUES(2,?,?,?,?)', ('李四', 1, '', 0))
    conn.execute('INSERT INTO people(id,name,confirmed,alias,ignored) VALUES(3,?,?,?,?)', ('待核对', 0, '', 0))
    faces = [
        (1, 1, 1, '[0,0,1,1]', b'a'),
        (2, 1, 2, '[0,0,1,1]', b'b'),
        (3, 2, 1, '[0,0,1,1]', b'c'),
        (4, 2, 2, '[0,0,1,1]', b'd'),
        (5, 3, 1, '[0,0,1,1]', b'e'),
        (6, 4, 3, '[0,0,1,1]', b'f'),
    ]
    conn.executemany('INSERT INTO faces(id,asset_id,person_id,bbox,embedding) VALUES(?,?,?,?,?)', faces)
    conn.commit()


def main():
    assert parse_date_bound('2025-05', end=False) == '2025-05-01'
    assert parse_date_bound('2025-05', end=True) == '2025-05-31'
    with tempfile.TemporaryDirectory() as folder:
        conn = sqlite3.connect(Path(folder) / 'library.sqlite3')
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        seed(conn)
        named = fetch_people(conn, named=1, limit=40)
        assert [row['name'] for row in named['items']] == ['张三', '李四'], named
        month = fetch_photos(conn, date_from='2025-05', date_to='2025-05', place='北京')
        conn.commit()
        assert [item['id'] for item in month['items']] == [2, 1], month
        combo = fetch_photos(conn, person='1,2', date_from='2025-05', date_to='2025-05', place='北京')
        conn.commit()
        assert [item['id'] for item in combo['items']] == [2, 1], combo
        group = fetch_photos(conn, filter='group:2', person='1,2', date_from='2025-05', date_to='2025-05')
        conn.commit()
        assert [item['id'] for item in group['items']] == [2, 1], group
        interval = fetch_photos(conn, filter='group:1-2')
        conn.commit()
        assert [item['id'] for item in interval['items']] == [3, 2, 1, 4], interval
        at_least = fetch_photos(conn, filter='group:2plus')
        conn.commit()
        assert [item['id'] for item in at_least['items']] == [2, 1], at_least
        at_most = fetch_photos(conn, filter='group:upto1')
        conn.commit()
        assert [item['id'] for item in at_most['items']] == [3, 4], at_most
        try:
            fetch_photos(conn, filter='group:6-3')
        except ValueError as exc:
            assert '最少人数不能大于最多人数' in str(exc)
        else:
            raise AssertionError('invalid group range should fail')
        year_only = fetch_photos(conn, date_from='2025', date_to='2025', place='北京')
        conn.commit()
        assert sorted(item['id'] for item in year_only['items']) == [1, 2, 4], year_only
        conn.execute('UPDATE assets SET latitude=?,longitude=? WHERE id IN (1,2)', (39.9000,116.4000))
        conn.execute('UPDATE assets SET latitude=?,longitude=? WHERE id=3', (39.9005,116.4005))
        conn.commit()
        cell=.02
        cluster=fetch_photos(conn, map_cell=cell, map_lat_bucket=math.floor(39.9000/cell), map_lng_bucket=math.floor(116.4000/cell))
        conn.commit()
        assert [item['id'] for item in cluster['items']] == [3, 2, 1], cluster
        try:
            fetch_photos(conn, map_cell=.0001, map_lat_bucket=1, map_lng_bucket=1)
        except ValueError as exc:
            assert '地图定位点无效' in str(exc)
        else:
            raise AssertionError('accepted invalid map cell')
        conn.close()
    print('STRUCTURED_QUERY_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
