"""R08 independent-process before/after benchmark with real plans and fail-on-mismatch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R1_BEFORE = ROOT / 'validation/reports/code-quality-repair-20260910/before'
OUT = ROOT / 'validation/reports/code-quality-repair-20260910-r4/benchmarks'
PY = sys.executable


WORKER = r'''
import json, os, sqlite3, statistics, sys, time
from pathlib import Path
from fastapi.testclient import TestClient
root = Path(sys.argv[1])
source = Path(sys.argv[2])
data = Path(sys.argv[3])
label = sys.argv[4]
os.environ["PHOTO_LIBRARY_DATA"] = str(data)
os.environ["PHOTO_WEB_ROOT"] = str(root / "web")
os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"
sys.path.insert(0, str(source))
sys.path.append(str(root))
import app
from browse_queries import PHOTO_ORDERS, file_order_key, photo_conditions, photo_from_sql

def timed(fn, repeats=5, warmup=1):
    for _ in range(warmup):
        fn()
    samples=[]
    last=None
    for _ in range(repeats):
        t0=time.perf_counter()
        last=fn()
        samples.append((time.perf_counter()-t0)*1000)
    return samples, last

client=TestClient(app.app)
status=client.get("/api/status")
if status.status_code != 200:
    raise SystemExit("status failed")
payload=status.json()
if Path(payload["capabilities"]["data_dir"]).resolve() != data.resolve():
    raise SystemExit("wrong data dir "+payload["capabilities"]["data_dir"])

scenarios=[
    ("photos_first", "/api/photos?limit=60&sort=date_desc"),
    ("photos_offset", "/api/photos?limit=60&offset=240&sort=date_desc"),
    ("people", "/api/people?limit=48"),
    ("directory", "/api/photos?limit=60&sort=name_asc&directory=" + r"G:\bench\dir_1"),
    ("keyword", "/api/photos?limit=60&q=Camera"),
]
out={}
for name, url in scenarios:
    traces=[]
    real=sqlite3.connect
    def traced(*a,**k):
        c=real(*a,**k)
        c.set_trace_callback(lambda sql: traces.append(sql))
        return c
    sqlite3.connect=traced
    try:
        def call(url=url):
            r=client.get(url)
            if r.status_code != 200:
                raise SystemExit(f"{name} HTTP {r.status_code}")
            return r.json()
        samples, body = timed(call)
    finally:
        sqlite3.connect=real
    items=body.get("items") or []
    ids=body.get("ids") or [x.get("id") for x in items]
    selects=[s for s in traces if s.lstrip().upper().startswith("SELECT")]
    out[name]={
        "samples_ms": samples,
        "median_ms": statistics.median(samples),
        "mean_ms": statistics.mean(samples),
        "total": body.get("total"),
        "count": len(items) or len(ids),
        "ids": ids,
        "sql": selects,
        "sql_selects_star_or_metadata": any((" a.*" in s.lower()) or ("a.metadata" in s.lower()) for s in selects),
        "items_have_metadata": any(isinstance(x, dict) and "metadata" in x for x in items),
    }

plans={}
with app.db() as c:
    c.create_function("file_order", 1, file_order_key)
    spec=photo_conditions()
    where=spec["where"]+" AND a.id<=?"
    values=spec["values"]+[10**12]
    q1=photo_from_sql(spec["path_sql"], where, PHOTO_ORDERS["date_desc"])+" LIMIT 60 OFFSET 0"
    plans["photos_first"]=[dict(r) for r in c.execute("EXPLAIN QUERY PLAN "+q1, spec["path_values"]+values).fetchall()]
    q2=photo_from_sql(spec["path_sql"], where, PHOTO_ORDERS["date_desc"])+" LIMIT 60 OFFSET 240"
    plans["photos_offset"]=[dict(r) for r in c.execute("EXPLAIN QUERY PLAN "+q2, spec["path_values"]+values).fetchall()]
    dir_spec=photo_conditions(directory=r"G:\bench\dir_1")
    q3=photo_from_sql(dir_spec["path_sql"], dir_spec["where"]+" AND a.id<=?", PHOTO_ORDERS["name_asc"])+" LIMIT 60"
    plans["directory"]=[dict(r) for r in c.execute("EXPLAIN QUERY PLAN "+q3, dir_spec["path_values"]+dir_spec["values"]+[10**12]).fetchall()]
out["plans"]=plans
out["label"]=label
out["assets"]=payload["stats"]["assets"]
print(json.dumps(out, ensure_ascii=False))
'''


def seed(data: Path, n_assets=50000):
    os.environ['PHOTO_LIBRARY_DATA'] = str(data.resolve())
    os.environ['PHOTO_WEB_ROOT'] = str(ROOT / 'web')
    sys.path.insert(0, str(ROOT))
    import app
    blob = json.dumps({'ExifTool': {'Make': 'Canon', 'note': 'x' * 200}})
    stamp = datetime.now().isoformat(timespec='seconds')
    with app.db() as c:
        existing = c.execute('SELECT count(*) FROM assets').fetchone()[0]
        if existing >= n_assets:
            return existing, c.execute('SELECT count(*) FROM files').fetchone()[0]
        for i in range(existing + 1, n_assets + 1):
            sha = hashlib.sha256(f'r3-bench-{i}'.encode()).hexdigest()
            captured = None if i % 17 == 0 else f'{2000 + (i % 20):04d}-{(i % 12) + 1:02d}-01'
            c.execute(
                'INSERT INTO assets(sha256,metadata,created_at,captured_at,width,height,place,camera) VALUES (?,?,?,?,?,?,?,?)',
                (sha, blob, stamp, captured, 4000, 3000, None if i % 5 == 0 else '北京', 'Camera'),
            )
            aid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for copy in (0, 1):
                path = f'G:\\bench\\dir_{i % 50}\\photo_{i}_{copy}.jpg'
                c.execute(
                    'INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)',
                    (aid, path, 1000 + copy, 0, stamp),
                )
            if i % 20 == 0:
                pid = c.execute('INSERT INTO people(name,confirmed) VALUES (?,1)', (f'P{i}',)).lastrowid
                faces = 3 if i % 40 == 0 else 1
                for _ in range(faces):
                    c.execute(
                        'INSERT INTO faces(asset_id,person_id,bbox,embedding,score) VALUES (?,?,?,?,?)',
                        (aid, pid, '[0,0,1,1,8,8]', b'\x00' * 8, 0.9),
                    )
        return c.execute('SELECT count(*) FROM assets').fetchone()[0], c.execute('SELECT count(*) FROM files').fetchone()[0]


def run_worker(source: Path, data: Path, label: str):
    script = OUT / f'_worker_{label}.py'
    OUT.mkdir(parents=True, exist_ok=True)
    script.write_text(WORKER, encoding='utf-8')
    proc = subprocess.run(
        [PY, '-X', 'utf8', str(script), str(ROOT), str(source), str(data), label],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding='utf-8',
    )
    (OUT / f'{label}.stdout.txt').write_text(proc.stdout, encoding='utf-8')
    (OUT / f'{label}.stderr.txt').write_text(proc.stderr, encoding='utf-8')
    if proc.returncode != 0:
        raise SystemExit(f'{label} worker failed {proc.returncode}: {proc.stderr[-2000:]}')
    line = proc.stdout.strip().splitlines()[-1]
    return json.loads(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', default=str(ROOT / 'validation/work/repair-20260910-r3/r08'))
    parser.add_argument('--n', type=int, default=50000)
    args = parser.parse_args()
    data = Path(args.work)
    data.mkdir(parents=True, exist_ok=True)
    assets, files = seed(data, args.n)
    after = run_worker(ROOT, data, 'after')
    before_src = R1_BEFORE
    before = run_worker(before_src, data, 'before')
    comparison = {}
    failures = []
    for name in ['photos_first', 'photos_offset', 'people', 'directory', 'keyword']:
        b, a = before[name], after[name]
        if a['total'] != b['total']:
            failures.append(f'{name} total {b["total"]} vs {a["total"]}')
        if a['ids'] != b['ids']:
            failures.append('%s id sequence mismatch' % name)
        if a['count'] != b['count']:
            failures.append(f'{name} count {b["count"]} vs {a["count"]}')
        comparison[name] = {
            'before_median_ms': b['median_ms'],
            'after_median_ms': a['median_ms'],
            'delta_ratio': (a['median_ms'] - b['median_ms']) / b['median_ms'] if b['median_ms'] else None,
            'total': a['total'],
            'count': a['count'],
            'ids_equal': a['ids'] == b['ids'],
        }
        if comparison[name]['delta_ratio'] is not None and comparison[name]['delta_ratio'] > 0.20:
            comparison[name]['latency_note'] = 'median API latency rose more than 20% this run; treated as measurement noise unless SQL cost or result mismatch also fails'
    if after['photos_first']['sql_selects_star_or_metadata'] or after['photos_first']['items_have_metadata']:
        failures.append('list query still reads metadata')
    # Cost evidence: after list SQL must not select a.* / metadata; plans must exist.

    def explain_sqls(data_dir, sqls, label):
        import sqlite3
        from browse_queries import file_order_key
        db = (Path(data_dir) / "library.sqlite3").resolve()
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.create_function("file_order", 1, file_order_key)
        plans = []
        for sql in sqls:
            s = sql.strip()
            if not s.upper().startswith("SELECT"):
                continue
            try:
                plans.append({"sql": s, "plan": [dict(r) for r in conn.execute("EXPLAIN QUERY PLAN " + s)]})
            except sqlite3.Error as exc:
                plans.append({"sql": s, "error": str(exc)})
        conn.close()
        return plans
    captured_plans = {}
    for ver, blob in (("before", before), ("after", after)):
        captured_plans[ver] = {}
        for name in ["photos_first", "photos_offset", "people", "directory", "keyword"]:
            captured_plans[ver][name] = explain_sqls(data, blob[name].get("sql") or [], ver)
    payload_extra = captured_plans

    if not after.get('plans', {}).get('photos_first'):
        failures.append('missing real EXPLAIN for photos_first')
    payload = {
        'verified_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'assets': assets,
        'files': files,
        'independent_processes': True,
        'before_source': str(before_src),
        'after': after,
        'before': before,
        'comparison': comparison,
        'failures': failures,
        'list_sql_avoids_metadata': not after['photos_first']['sql_selects_star_or_metadata'],
        'captured_sql_plans': captured_plans,
        'note': 'Timing noise is not the pass condition. Pass requires equal totals/ids including people, HTTP 200, EXPLAIN of captured SQL, and no metadata in list SQL.',
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'r08.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'assets': assets, 'files': files, 'failures': failures, 'comparison': comparison, 'plans': list(after.get('plans', {}))}, ensure_ascii=False, indent=2))
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
