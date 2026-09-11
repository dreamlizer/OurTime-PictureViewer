"""Isolated repair-case runner. Never imports app before PHOTO_LIBRARY_DATA is set."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK = ROOT / 'validation' / 'work' / 'repair-20260910'
DEFAULT_REPORT = ROOT / 'validation' / 'reports' / 'code-quality-repair-20260910'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class CaseFailure(Exception):
    pass


def assert_true(cond, message):
    if not cond:
        raise CaseFailure(message)


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def setup_isolated(work: Path):
    work.mkdir(parents=True, exist_ok=True)
    data = work / 'data'
    if data.exists():
        # keep a fresh data dir per invocation by using a unique run folder
        pass
    os.environ['PHOTO_LIBRARY_DATA'] = str(data.resolve())
    os.environ['PHOTO_WEB_ROOT'] = str((ROOT / 'web').resolve())
    os.environ.setdefault('PHOTO_MODEL_ROOT', 'G:/CodexModels/insightface')
    os.environ.setdefault('PHOTO_GEO_ROOT', 'G:/CodexModels/geo')
    sys.path.insert(0, str(ROOT))
    return data


def import_app(data_dir: Path):
    os.environ['PHOTO_LIBRARY_DATA'] = str(Path(data_dir).resolve())
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for name in list(sys.modules):
        if name in {'app', 'library_db', 'browse_queries', 'geo_labels'}:
            sys.modules.pop(name)
    import app
    return app


def case_r01(report: Path, work: Path):
    data = work / 'r01'
    if data.exists():
        import shutil
        shutil.rmtree(data)
    data.mkdir(parents=True)
    app = import_app(data)
    from fastapi.testclient import TestClient
    client = TestClient(app.app)
    status = client.get('/api/status').json()
    assert_true(Path(status['capabilities']['data_dir']) == data.resolve(), 'R01 data_dir is not isolated')
    with app.db() as c:
        people = []
        for idx, (name, confirmed, photos) in enumerate([('甲', 1, 5), (None, 0, 1), ('乙', 1, 2), ('丙', 0, 2)], start=1):
            pid = c.execute('INSERT INTO people(name,confirmed) VALUES (?,?)', (name, confirmed)).lastrowid
            people.append(pid)
            for n in range(photos):
                sha = hashlib.sha256(f'r01-{idx}-{n}'.encode()).hexdigest()
                aid = c.execute(
                    "INSERT INTO assets(sha256,metadata,created_at,captured_at) VALUES (?,?,?,?)",
                    (sha, '{}', app.now(), f'2500-01-0{n+1}'),
                ).lastrowid
                c.execute(
                    "INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)",
                    (aid, str(data / f'p{idx}_{n}.jpg'), 10, 0, app.now()),
                )
                c.execute(
                    "INSERT INTO faces(asset_id,person_id,bbox,embedding,score) VALUES (?,?,?,?,?)",
                    (aid, pid, '[0,0,1,1,10,10]', b'\x00' * 16, 0.9),
                )
        extra_ref = c.execute('INSERT INTO people(name,confirmed,suggested_person_id) VALUES (?,?,?)', ('候选', 0, people[2])).lastrowid
        sha = hashlib.sha256(b'r01-ref').hexdigest()
        aid = c.execute("INSERT INTO assets(sha256,metadata,created_at) VALUES (?,?,?)", (sha, '{}', app.now())).lastrowid
        c.execute("INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)", (aid, str(data / 'ref.jpg'), 10, 0, app.now()))
        c.execute("INSERT INTO faces(asset_id,person_id,bbox,embedding,score) VALUES (?,?,?,?,?)", (aid, extra_ref, '[0,0,1,1,10,10]', b'\x00' * 16, 0.8))
    p1, p2, p3, p4 = people
    data_a = client.get('/api/people', params={'limit': 1, 'ids': f'{p3},{p4}'}).json()
    ids = [x['id'] for x in data_a['items']]
    assert_true(p1 in ids, 'R01-A page item missing person 1')
    assert_true(p3 in ids and p4 in ids, 'R01-A extras missing')
    assert_true(data_a['total'] == 5, f"R01-A total should exclude extras-only inflation, got {data_a['total']}")
    assert_true(len(ids) == len(set(ids)), 'R01-A duplicate ids')
    counts = {x['id']: x['photo_count'] for x in data_a['items']}
    assert_true(counts[p3] == 2 and counts[p4] == 2, 'R01-A photo counts')
    named_before = client.get(f'/api/people/{p1}').json()
    # merge 3 and 4 only
    merged = client.post(f'/api/people/{p4}/merge', json={'target_id': p3})
    assert_true(merged.status_code == 200, merged.text)
    after = client.get(f'/api/people/{p1}').json()
    assert_true(after['name'] == named_before['name'], 'R01-B person 1 name changed')
    assert_true(after['face_count'] == named_before['face_count'], 'R01-B person 1 faces changed')
    # extras across pages / duplicate ids
    data_c = client.get('/api/people', params={'limit': 1, 'offset': 1, 'ids': f'{p3},{p3}'}).json()
    assert_true(p3 in [x['id'] for x in data_c['items']], 'R01-C selected id missing across page')
    bad = client.get('/api/people', params={'ids': '999999'})
    assert_true(bad.status_code == 200, 'missing ids should still 200 with empty extras')
    over = client.get('/api/people', params={'ids': ','.join(str(i) for i in range(1, 102))})
    assert_true(over.status_code == 400, 'R01-D over-limit should 400')
    quick = client.get('/api/people', params={'limit': 1, 'ids': str(p3)}).json()
    found = [x for x in quick['items'] if x['id'] == p3]
    assert_true(found, 'R01-E quick name lookup lost selected person')
    with app.db() as c:
        leftover = c.execute('SELECT suggested_person_id FROM people WHERE id=?', (extra_ref,)).fetchone()
        assert_true(leftover['suggested_person_id'] == p3, 'R01-G suggested_person_id not rewritten')
        fk = list(c.execute('PRAGMA foreign_key_check'))
        assert_true(fk == [], f'foreign_key_check {fk}')
    evidence = report / 'logs' / 'r01.json'
    evidence.write_text(json.dumps({'items': data_a, 'after_person1': after, 'quick': quick}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'id': 'R01', 'status': 'PASS', 'evidence': [str(evidence)]}


def independent_order(rows, sort):
    def file_order(path):
        import re
        from pathlib import Path as P
        return re.sub(r'\d+', lambda m: m[0].zfill(24), P(path or '').name.casefold())
    # SQLite treats NULL as smaller than any value.
    if sort == 'date_desc':
        return [r['id'] for r in sorted(rows, key=lambda r: (r['sort_date'] is not None, r['sort_date'] or '', r['id']), reverse=True)]
    if sort == 'date_asc':
        return [r['id'] for r in sorted(rows, key=lambda r: (r['sort_date'] is not None, r['sort_date'] or '', r['id']))]
    if sort == 'name_asc':
        return [r['id'] for r in sorted(rows, key=lambda r: (file_order(r['path']), r['path'].casefold(), r['id']))]
    if sort == 'name_desc':
        return [r['id'] for r in sorted(rows, key=lambda r: (file_order(r['path']), r['path'].casefold(), r['id']), reverse=True)]
    raise CaseFailure(sort)


def case_r02(report: Path, work: Path):
    data = work / 'r02'
    import shutil
    if data.exists():
        shutil.rmtree(data)
    photos = data / 'photos'
    weird = photos / '中文 目录_%'
    other = photos / 'other'
    weird.mkdir(parents=True)
    other.mkdir(parents=True)
    app = import_app(data)
    from fastapi.testclient import TestClient
    client = TestClient(app.app)
    rows = []
    with app.db() as c:
        content_dup = hashlib.sha256(b'same-bytes').hexdigest()
        for i in range(1, 501):
            captured = None if i in {10, 11} else f'{2500 - i:04d}-01-01'
            if i == 2:
                name = '照片2.jpg'
            elif i == 10:
                name = '照片10.jpg'
            else:
                name = f'img_{500-i:04d}.jpg'
            folder = weird if i != 500 else other
            path = (folder / name).resolve()
            path.write_bytes(b'same-bytes' if i in {20, 21} else f'photo-{i}'.encode())
            sha = content_dup if i in {20, 21} else hashlib.sha256(path.read_bytes()).hexdigest()
            existing = c.execute('SELECT id FROM assets WHERE sha256=?', (sha,)).fetchone()
            if existing:
                aid = existing['id']
            else:
                aid = c.execute(
                    "INSERT INTO assets(id,sha256,metadata,created_at,captured_at,width,height) VALUES (?,?,?,?,?,?,?)",
                    (i if i not in {20, 21} else 20, sha, '{}', app.now(), captured, 100, 80),
                ).lastrowid if False else None
                # keep requested IDs 1..500 except duplicate content sharing asset 20
                wanted = 20 if i in {20, 21} else i
                try:
                    aid = c.execute(
                        "INSERT INTO assets(id,sha256,metadata,created_at,captured_at,width,height) VALUES (?,?,?,?,?,?,?)",
                        (wanted, sha, '{}', app.now(), captured, 100, 80),
                    ).lastrowid
                except sqlite3.IntegrityError:
                    aid = c.execute('SELECT id FROM assets WHERE sha256=?', (sha,)).fetchone()['id']
            c.execute(
                "INSERT OR REPLACE INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)",
                (aid, str(path), path.stat().st_size, 0, app.now()),
            )
            rows.append({'id': aid, 'sort_date': captured, 'path': str(path)})
    # unique rows by asset id using representative path (lowest file id / insertion)
    unique = {}
    for row in rows:
        unique.setdefault(row['id'], row)
        # representative path is first inserted, which matches SQL ORDER BY excluded,exists_now DESC,id
    oracle_rows = list(unique.values())
    evidence = {}
    for sort in ['date_desc', 'date_asc', 'name_asc', 'name_desc']:
        expected = independent_order(oracle_rows, sort)
        got = []
        offset = 0
        while True:
            page = client.get('/api/photos', params={'limit': 80, 'offset': offset, 'sort': sort}).json()
            got.extend([x['id'] for x in page['items']])
            offset += len(page['items'])
            if offset >= page['total'] or not page['items']:
                break
        assert_true(got == expected, f'R02-A {sort} mismatch first-diff {next(((a,b) for a,b in zip(got, expected) if a!=b), (got[:5], expected[:5]))}')
        evidence[sort] = {'expected_head': expected[:8], 'got_head': got[:8], 'total': len(got)}
        target_ids = [expected[0], expected[len(expected)//2], expected[-1]]
        for tid in target_ids:
            around = client.get('/api/photos', params={'sequence': 'true', 'around': tid, 'sort': sort, 'limit': 80}).json()
            assert_true(tid in around['ids'], f'R02-B around missing {tid} sort={sort}')
            assert_true(around['total'] == len(expected), 'R02-B total')
    around = client.get('/api/photos', params={'sequence': 'true', 'around': 1, 'sort': 'date_desc', 'limit': 80}).json()
    assert_true(1 in around['ids'], 'R02-C id 1 missing')
    assert_true(around['offset'] != 399, 'R02-C historical offset 399')
    directory = str(weird)
    listing = client.get('/api/photos', params={'limit': 1, 'sort': 'name_asc', 'directory': directory}).json()
    assert_true(listing.get('items'), 'R02-D directory listing empty')
    member = listing['items'][0]['id']
    around_dir = client.get('/api/photos', params={'sequence': 'true', 'around': member, 'sort': 'name_asc', 'directory': directory, 'limit': 80}).json()
    assert_true(around_dir.get('ids'), 'R02-D around empty')
    def in_dir(path, folder):
        try:
            file_path = Path(path).resolve()
            folder_path = Path(folder).resolve()
        except OSError:
            return False
        return file_path == folder_path or folder_path in file_path.parents
    for pid in around_dir['ids']:
        detail = client.get(f'/api/photos/{pid}').json()
        paths = [f['path'] for f in detail.get('files') or []]
        assert_true(any(in_dir(x, directory) for x in paths), f'R02-D photo {pid} not in directory {directory}: {paths}')
    outsider = client.get('/api/photos', params={'sequence': 'true', 'around': 500, 'sort': 'name_asc', 'directory': directory, 'limit': 80}).json()
    if outsider.get('ids') and 500 in outsider['ids']:
        detail = client.get('/api/photos/500').json()
        assert_true(any(in_dir(f['path'], directory) for f in detail.get('files') or []), 'R02-D mixed unrelated directory into around window')
    sibling = Path(directory).resolve().parent / (Path(directory).resolve().name + "bar")
    if around_dir.get("ids"):
        for pid in around_dir["ids"]:
            detail = client.get(f"/api/photos/{pid}").json()
            for f in detail.get("files") or []:
                assert_true(not str(Path(f["path"]).resolve()).startswith(str(sibling)), "R02-D sibling prefix leaked")
    # insert after freeze
    first = client.get('/api/photos', params={'sequence': 'true', 'around': 1, 'sort': 'date_desc', 'limit': 40}).json()
    frozen = first['max_id']
    with app.db() as c:
        c.execute("INSERT INTO assets(sha256,metadata,created_at,captured_at) VALUES (?,?,?,?)", (hashlib.sha256(b'new').hexdigest(), '{}', app.now(), '2600-01-01'))
        aid = c.execute('SELECT id FROM assets WHERE sha256=?', (hashlib.sha256(b'new').hexdigest(),)).fetchone()[0]
        c.execute("INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)", (aid, str(weird / 'new.jpg'), 3, 0, app.now()))
    later = client.get('/api/photos', params={'sequence': 'true', 'offset': first['offset'] + 20, 'sort': 'date_desc', 'limit': 40, 'max_id': frozen}).json()
    assert_true(later['max_id'] == frozen, 'R02-F max_id changed')
    assert_true(aid not in later['ids'], 'R02-F new record leaked')
    missing = client.get('/api/photos', params={'sequence': 'true', 'around': 999999, 'sort': 'date_desc'}).json()
    assert_true(missing.get('missing') or missing.get('ids') == [], 'R02-G should not fake single photo')
    (report / 'logs' / 'r02.json').write_text(json.dumps({'evidence': evidence, 'around1': around, 'missing': missing}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'id': 'R02', 'status': 'PASS', 'evidence': [str(report / 'logs' / 'r02.json')]}


CASES = {
    'R01': case_r01,
    'R02': case_r02,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--work', default=str(DEFAULT_WORK))
    parser.add_argument('--report', default=str(DEFAULT_REPORT))
    args = parser.parse_args()
    report = Path(args.report)
    work = Path(args.work)
    (report / 'logs').mkdir(parents=True, exist_ok=True)
    from repair_cases_extra import case_r04, case_r05, case_r06, case_r07
    CASES.update({'R04': case_r04, 'R05': case_r05, 'R06': case_r06, 'R07': case_r07})
    names = list(CASES) if args.all or not args.case else [args.case]
    results = []
    failed = False
    for name in names:
        fn = CASES.get(name)
        if not fn:
            results.append({'id': name, 'status': 'NOT_RUN', 'actual': 'unknown case'})
            continue
        try:
            results.append(fn(report, work))
        except Exception as exc:
            failed = True
            results.append({'id': name, 'status': 'FAIL', 'actual': str(exc), 'trace': traceback.format_exc()})
    out = report / 'logs' / 'repair_checks.json'
    out.write_text(json.dumps({'verified_at': now(), 'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
