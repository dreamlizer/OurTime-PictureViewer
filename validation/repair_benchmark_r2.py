"""R08 before/after benchmark. Round-1 before/ is the pre-fix baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R1_BEFORE = ROOT / 'validation/reports/code-quality-repair-20260910/before'
REPORT = ROOT / 'validation/reports/code-quality-repair-20260910-r2/benchmarks'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def timed(fn, repeats=5, warmup=1):
    for _ in range(warmup):
        fn()
    samples = []
    last = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        last = fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return {'samples_ms': samples, 'median_ms': statistics.median(samples), 'mean_ms': statistics.mean(samples), 'result': last}


def load_app(data_dir: Path, source_root: Path):
    os.environ['PHOTO_LIBRARY_DATA'] = str(data_dir.resolve())
    os.environ['PHOTO_WEB_ROOT'] = str((ROOT / 'web').resolve())
    os.environ['NO_ALBUMENTATIONS_UPDATE'] = '1'
    for name in list(sys.modules):
        if name in {'app', 'library_db', 'browse_queries', 'geo_labels', 'metadata_reader', 'object_labels'}:
            sys.modules.pop(name)
    cleaned = [p for p in sys.path if p not in {str(source_root), str(ROOT)}]
    sys.path[:] = [str(source_root), str(ROOT)] + cleaned
    import app
    return app


def seed(conn, n_assets=50000):
    existing = conn.execute('SELECT count(*) FROM assets').fetchone()[0]
    if existing >= n_assets:
        return existing, conn.execute('SELECT count(*) FROM files').fetchone()[0]
    blob = json.dumps({'ExifTool': {'Make': 'Canon', 'Model': 'X' * 80, 'note': 'y' * 200}}, ensure_ascii=False)
    stamp = datetime.now().isoformat(timespec='seconds')
    for i in range(existing + 1, n_assets + 1):
        sha = hashlib.sha256(f'bench-{i}'.encode()).hexdigest()
        captured = None if i % 17 == 0 else f'{2000 + (i % 20):04d}-{(i % 12) + 1:02d}-01'
        conn.execute(
            'INSERT INTO assets(sha256,metadata,created_at,captured_at,width,height,place,camera) VALUES (?,?,?,?,?,?,?,?)',
            (sha, blob, stamp, captured, 4000, 3000, None if i % 5 == 0 else '北京', 'Camera'),
        )
        aid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        for copy in (0, 1):
            path = f'G:\\bench\\dir_{i % 50}\\photo_{i}_{copy}.jpg'
            if i % 11 == 0:
                path = f'G:\\bench\\中文 目录_%\\photo_{i}_{copy}.jpg'
            conn.execute(
                'INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)',
                (aid, path, 1234 + copy, 0, stamp),
            )
        if i % 20 == 0:
            pid = conn.execute('INSERT INTO people(name,confirmed) VALUES (?,1)', (f'P{i}',)).lastrowid
            faces = 3 if i % 40 == 0 else 1
            for _ in range(faces):
                conn.execute(
                    'INSERT INTO faces(asset_id,person_id,bbox,embedding,score) VALUES (?,?,?,?,?)',
                    (aid, pid, '[0,0,1,1,8,8]', b'\x00' * 8, 0.9),
                )
    conn.commit()
    return conn.execute('SELECT count(*) FROM assets').fetchone()[0], conn.execute('SELECT count(*) FROM files').fetchone()[0]


SCENARIOS = [
    ('photos_first', lambda c: c.get('/api/photos', params={'limit': 60, 'sort': 'date_desc'})),
    ('photos_offset', lambda c: c.get('/api/photos', params={'limit': 60, 'offset': 240, 'sort': 'date_desc'})),
    ('people', lambda c: c.get('/api/people', params={'limit': 48})),
    ('directory', lambda c: c.get('/api/photos', params={'limit': 60, 'directory': r'G:\bench\dir_1', 'sort': 'name_asc'})),
    ('keyword', lambda c: c.get('/api/photos', params={'limit': 60, 'q': 'Camera'})),
]


def measure(app):
    from fastapi.testclient import TestClient
    client = TestClient(app.app)
    status = client.get('/api/status').json()
    traces = []
    real_connect = sqlite3.connect

    def traced_connect(*a, **k):
        conn = real_connect(*a, **k)
        conn.set_trace_callback(lambda sql: traces.append(sql))
        return conn

    sqlite3.connect = traced_connect
    try:
        out = {}
        for name, fn in SCENARIOS:
            traces.clear()
            timed_res = timed(lambda fn=fn: fn(client).json())
            sqls = list(traces)
            result = timed_res['result']
            items = result.get('items') or []
            ids = result.get('ids') or [x.get('id') for x in items]
            select_sql = [s for s in sqls if s.lstrip().upper().startswith('SELECT')]
            out[name] = {
                'samples_ms': timed_res['samples_ms'],
                'median_ms': timed_res['median_ms'],
                'mean_ms': timed_res['mean_ms'],
                'total': result.get('total'),
                'head': ids[:12],
                'sql': select_sql[:30],
                'sql_reads_metadata': any(' a.metadata' in s.lower() or s.lower().startswith('select a.*') or 'a.*,' in s.lower() for s in select_sql),
                'items_have_metadata': any(isinstance(x, dict) and 'metadata' in x for x in items),
            }
        with app.db() as c:
            out['_explain_photos'] = [dict(r) for r in c.execute('EXPLAIN QUERY PLAN SELECT a.id FROM assets a WHERE a.excluded=0 LIMIT 60').fetchall()]
        out['_status_dir'] = (status.get('capabilities') or {}).get('data_dir')
        return out
    finally:
        sqlite3.connect = real_connect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', default=str(ROOT / 'validation/work/repair-20260910-r2/r08'))
    parser.add_argument('--n', type=int, default=50000)
    args = parser.parse_args()
    data = Path(args.work)
    data.mkdir(parents=True, exist_ok=True)
    after_app = load_app(data, ROOT)
    with after_app.db() as c:
        assets, files = seed(c, args.n)
    after = measure(after_app)
    before_src = data / 'before_src'
    if before_src.exists():
        shutil.rmtree(before_src)
    before_src.mkdir()
    for name in ['app.py', 'geo_labels.py', 'metadata_reader.py', 'object_labels.py']:
        src = R1_BEFORE / name if (R1_BEFORE / name).exists() else ROOT / name
        if src.exists():
            shutil.copy2(src, before_src / name)
    before_app = load_app(data, before_src)
    before = measure(before_app)
    comparison = {}
    hotspot = False
    regression = []
    for name, _ in SCENARIOS:
        b = before[name]['median_ms']
        a = after[name]['median_ms']
        delta = (a - b) / b if b else None
        comparison[name] = {
            'before_median_ms': b,
            'after_median_ms': a,
            'delta_ratio': delta,
            'before_head': before[name]['head'],
            'after_head': after[name]['head'],
            'total_equal': before[name]['total'] == after[name]['total'],
        }
        if delta is not None and a < b:
            hotspot = True
        if delta is not None and delta > 0.20:
            regression.append(name)
    payload = {
        'verified_at': now(),
        'python': sys.version,
        'sqlite': sqlite3.sqlite_version,
        'assets': assets,
        'files': files,
        'data_dir': str(data.resolve()),
        'before_source': str(R1_BEFORE),
        'after': {k: v for k, v in after.items() if not str(k).startswith('_')},
        'before': {k: v for k, v in before.items() if not str(k).startswith('_')},
        'comparison': comparison,
        'hotspot_improved': hotspot,
        'regression_over_20pct': regression,
        'after_list_sql_reads_metadata': after['photos_first']['sql_reads_metadata'],
        'after_items_have_metadata': after['photos_first']['items_have_metadata'],
        'note': 'Same synthetic DB. Round-1 before/ is pre-fix baseline. No ANALYZE/VACUUM on formal DB.',
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    out = REPORT / 'r08-before-after.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'assets': assets,
        'files': files,
        'hotspot_improved': hotspot,
        'regression_over_20pct': regression,
        'metadata_sql': payload['after_list_sql_reads_metadata'],
        'metadata_items': payload['after_items_have_metadata'],
        'medians': {k: {'before': v['before_median_ms'], 'after': v['after_median_ms'], 'delta': v['delta_ratio']} for k, v in comparison.items()},
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
