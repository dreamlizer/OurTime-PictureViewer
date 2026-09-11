"""R08 query cost measurement on an isolated synthetic library."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'validation' / 'reports' / 'code-quality-repair-20260910'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def setup(data_dir: Path):
    os.environ['PHOTO_LIBRARY_DATA'] = str(data_dir.resolve())
    os.environ['PHOTO_WEB_ROOT'] = str((ROOT / 'web').resolve())
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for name in list(sys.modules):
        if name in {'app', 'library_db', 'browse_queries'}:
            sys.modules.pop(name)
    import app
    return app


def seed(app, n=50000):
    with app.db() as c:
        existing = c.execute('SELECT count(*) FROM assets').fetchone()[0]
        if existing >= n:
            return existing
        c.execute('BEGIN')
        for i in range(existing + 1, n + 1):
            sha = hashlib.sha256(f'bench-{i}'.encode()).hexdigest()
            captured = f'{2000 + (i % 20):04d}-{(i % 12)+1:02d}-01' if i % 17 else None
            aid = c.execute(
                "INSERT INTO assets(sha256,metadata,created_at,captured_at,width,height,place,camera) VALUES (?,?,?,?,?,?,?,?)",
                (sha, json.dumps({'x': 'y' * 40}), app.now(), captured, 4000, 3000, '北京' if i % 3 else None, 'Camera'),
            ).lastrowid
            path = f'G:\\bench\\dir_{i % 50}\\photo_{i}.jpg'
            if i % 11 == 0:
                path = f'G:\\bench\\中文 目录_%\\photo_{i}.jpg'
            c.execute(
                "INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)",
                (aid, path, 1234, 0, app.now()),
            )
            if i % 20 == 0:
                pid = c.execute('INSERT INTO people(name,confirmed) VALUES (?,1)', (f'P{i}',)).lastrowid
                c.execute(
                    "INSERT INTO faces(asset_id,person_id,bbox,embedding,score) VALUES (?,?,?,?,?)",
                    (aid, pid, '[0,0,1,1,8,8]', b'\x00' * 8, 0.9),
                )
        return c.execute('SELECT count(*) FROM assets').fetchone()[0]


def timed(fn, repeats=5, warmup=1):
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return {
        'samples_ms': samples,
        'median_ms': statistics.median(samples),
        'mean_ms': statistics.mean(samples),
        'result': result,
    }


def traces_metadata(sql):
    return 'metadata' in sql.lower() and 'json' not in sql.lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', default=str(ROOT / 'validation' / 'work' / 'repair-20260910' / 'r08'))
    parser.add_argument('--n', type=int, default=50000)
    args = parser.parse_args()
    data = Path(args.work)
    data.mkdir(parents=True, exist_ok=True)
    app = setup(data)
    count = seed(app, args.n)
    from fastapi.testclient import TestClient
    from browse_queries import photo_from_sql, photo_conditions, PHOTO_ORDERS
    client = TestClient(app.app)
    status = client.get('/api/status').json()
    assert Path(status['capabilities']['data_dir']) == data.resolve()

    plans = {}
    from browse_queries import file_order_key
    with app.db() as c:
        c.create_function('file_order', 1, file_order_key)
        spec = photo_conditions(sort_unused := 'date_desc') if False else photo_conditions()
        where = spec['where'] + ' AND a.id<=?'
        values = spec['values'] + [10**9]
        query = photo_from_sql(spec['path_sql'], where, PHOTO_ORDERS['date_desc']) + ' LIMIT 60 OFFSET 0'
        plans['photos_first'] = [dict(r) for r in c.execute('EXPLAIN QUERY PLAN ' + query, spec['path_values'] + values).fetchall()]
        query2 = photo_from_sql(spec['path_sql'], where, PHOTO_ORDERS['date_desc']) + ' LIMIT 60 OFFSET 240'
        plans['photos_offset'] = [dict(r) for r in c.execute('EXPLAIN QUERY PLAN ' + query2, spec['path_values'] + values).fetchall()]
        people_sql = 'SELECT p.id, count(f.id) FROM people p JOIN faces f ON f.person_id=p.id GROUP BY p.id ORDER BY count(DISTINCT f.asset_id) DESC LIMIT 48'
        plans['people'] = [dict(r) for r in c.execute('EXPLAIN QUERY PLAN ' + people_sql).fetchall()]
        dir_spec = photo_conditions(directory=str(Path('G:/bench/dir_1')))
        plans['directory'] = [dict(r) for r in c.execute(
            'EXPLAIN QUERY PLAN ' + photo_from_sql(dir_spec['path_sql'], dir_spec['where'] + ' AND a.id<=?', PHOTO_ORDERS['name_asc']) + ' LIMIT 60',
            dir_spec['path_values'] + dir_spec['values'] + [10**9],
        ).fetchall()]

    traced = []
    def tracer(sql):
        traced.append(sql)

    measurements = {}
    measurements['photos_first'] = timed(lambda: client.get('/api/photos', params={'limit': 60, 'sort': 'date_desc'}).json())
    measurements['photos_offset'] = timed(lambda: client.get('/api/photos', params={'limit': 60, 'offset': 240, 'sort': 'date_desc'}).json())
    measurements['people'] = timed(lambda: client.get('/api/people', params={'limit': 48}).json())
    measurements['directory'] = timed(lambda: client.get('/api/photos', params={'limit': 60, 'directory': r'G:\bench\dir_1', 'sort': 'name_asc'}).json())
    measurements['keyword'] = timed(lambda: client.get('/api/photos', params={'limit': 60, 'q': 'Camera'}).json())

    first_ids = measurements['photos_first']['result']['items']
    second = client.get('/api/photos', params={'limit': 60, 'sort': 'date_desc'}).json()['items']
    same = [a['id'] for a in first_ids] == [a['id'] for a in second]
    metadata_in_list = any('metadata' in item for item in first_ids)

    payload = {
        'verified_at': now(),
        'python': sys.version,
        'sqlite': sqlite3.sqlite_version,
        'assets': count,
        'data_dir': str(data.resolve()),
        'plans': plans,
        'measurements': {k: {kk: vv for kk, vv in v.items() if kk != 'result'} | {'total': (v['result'] or {}).get('total'), 'head': [x['id'] for x in (v['result'] or {}).get('items', [])][:8]} for k, v in measurements.items()},
        'list_ids_stable': same,
        'metadata_in_list_items': metadata_in_list,
        'note': 'Synthetic isolated rows only; not the formal library. No ANALYZE/VACUUM on formal DB.',
    }
    out = REPORT / 'benchmarks' / 'r08.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'assets': count, 'median_ms': {k: v['median_ms'] for k, v in measurements.items()}, 'stable': same, 'metadata_in_list': metadata_in_list}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
