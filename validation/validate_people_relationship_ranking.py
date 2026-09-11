"""Isolated and live checks for people co-occurrence ranking."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = ROOT / 'validation' / 'work'
REPORT = ROOT / 'validation' / 'reports' / 'people-relationship-ranking-20260911.json'
LIVE = 'http://127.0.0.1:8765'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def add_asset(app, data, key):
    with app.db() as c:
        sha = hashlib.sha256(key.encode()).hexdigest()
        aid = c.execute(
            'INSERT INTO assets(sha256,metadata,created_at,width,height,format) VALUES (?,?,?,?,?,?)',
            (sha, '{}', app.now(), 100, 100, 'JPEG'),
        ).lastrowid
        c.execute(
            'INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded) VALUES (?,?,?,?,?,?,?)',
            (aid, str(data / f'{key}.jpg'), 100, 0, app.now(), 1, 0),
        )
    return aid


def add_person(app, data, name=None, confirmed=0, ignored=0):
    with app.db() as c:
        pid = c.execute(
            'INSERT INTO people(name,alias,confirmed,ignored) VALUES (?,?,?,?)',
            (name, '', confirmed, ignored),
        ).lastrowid
    return pid


def add_face(app, aid, pid, ignored=0):
    with app.db() as c:
        c.execute(
            'INSERT INTO faces(asset_id,person_id,bbox,embedding,score,ignored) VALUES (?,?,?,?,?,?)',
            (aid, pid, '[0,0,100,100,100,100]', b'check', .9, ignored),
        )


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode('utf-8', errors='replace')) from exc


def main():
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix='people-relationship-', dir=WORK_ROOT))
    os.environ['PHOTO_LIBRARY_DATA'] = str(data)
    os.environ['PHOTO_WEB_ROOT'] = str((ROOT / 'web').resolve())
    from validation.repair_checks import import_app

    app = import_app(data)
    high = add_person(app, data, 'Known High', 1)
    low = add_person(app, data, 'Known Low', 1)
    ignored_known = add_person(app, data, 'Ignored Known', 1, 1)
    candidate_d = add_person(app, data, 'Candidate D')
    candidate_a = add_person(app, data, 'Candidate A')
    candidate_b = add_person(app, data, 'Candidate B')
    candidate_c = add_person(app, data, 'Candidate C')

    high_assets = [add_asset(app, data, f'high-{i}') for i in range(10)]
    low_assets = [add_asset(app, data, f'low-{i}') for i in range(2)]
    isolated_asset = add_asset(app, data, 'isolated')
    ignored_face_asset = add_asset(app, data, 'ignored-face-shared')
    ignored_person_asset = add_asset(app, data, 'ignored-person-shared')
    for aid in high_assets:
        add_face(app, aid, high)
    for aid in low_assets:
        add_face(app, aid, low)
    for aid in high_assets[:7]:
        add_face(app, aid, candidate_d)
    for aid in high_assets[:3] + low_assets[:1]:
        add_face(app, aid, candidate_a)
    for aid in low_assets:
        add_face(app, aid, candidate_b)
    add_face(app, isolated_asset, candidate_c)
    add_face(app, ignored_face_asset, candidate_c)
    add_face(app, ignored_face_asset, high, ignored=1)
    add_face(app, ignored_person_asset, candidate_c)
    add_face(app, ignored_person_asset, ignored_known)

    env = os.environ.copy()
    server = subprocess.Popen([sys.executable, 'app.py', '--port', '8783'], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                fetch('http://127.0.0.1:8783/api/status')
                break
            except Exception:
                time.sleep(.25)
        result = fetch('http://127.0.0.1:8783/api/people?limit=48&offset=0')
        by_name = {p['name']: p for p in result['items']}
        candidates = [by_name[name] for name in ('Candidate D', 'Candidate A', 'Candidate B', 'Candidate C')]
        assert [p['name'] for p in candidates] == ['Candidate D', 'Candidate A', 'Candidate B', 'Candidate C']
        assert candidates[0]['best_known_photo_count'] > candidates[2]['best_known_photo_count']
        assert candidates[0]['best_known_shared_count'] > candidates[1]['best_known_shared_count']
        assert candidates[3]['known_connection_count'] == 0
        pages = [fetch(f'http://127.0.0.1:8783/api/people?limit=2&offset={offset}')['items'] for offset in (0, 2, 4)]
        page_ids = [p['id'] for page in pages for p in page]
        assert len(page_ids) == len(set(page_ids))
        assert fetch(f'http://127.0.0.1:8783/api/people?q=Candidate%20C&limit=48')['items'][0]['name'] == 'Candidate C'
        isolated = {'status': 'PASS', 'candidate_order': [p['name'] for p in candidates],
                    'candidate_details': {p['name']: {k: p[k] for k in (
                        'known_connection_count', 'has_known_connection', 'best_known_person_name',
                        'best_known_photo_count', 'best_known_shared_count', 'photo_count')} for p in candidates},
                    'pagination_unique': len(page_ids) == len(set(page_ids))}
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    benchmark = []
    for offset in (0, 48):
        started = time.perf_counter()
        page = fetch(f'{LIVE}/api/people?limit=48&offset={offset}')
        benchmark.append({'offset': offset, 'ms': round((time.perf_counter() - started) * 1000, 2),
                          'items': len(page.get('items', [])), 'total': page.get('total')})
    report = {'isolated': isolated, 'live_benchmark': benchmark}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
