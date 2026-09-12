"""R04-E: merge/ignore interleaved with index load via an event barrier."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'validation/reports/code-quality-repair-20260910-r4/runs/r04e'
WORK = ROOT / 'validation/work/repair-20260910-r4/r04e'
OUT.mkdir(parents=True, exist_ok=True)



def assert_loaded_index_ok(index, people, source, keep, ignore):
    if index is None:
        raise AssertionError("concurrent load returned no index")
    refs = list(index["person_ids"])
    ignored_ids = {pid for pid, row in people.items() if row.get("ignored")}
    missing = [pid for pid in refs if pid not in people]
    ignored_missing = [pid for pid in ignored_ids if pid not in refs]
    source_gone = source not in people
    ok = (not missing) and (not ignored_missing) and source_gone
    return ok, {"refs": refs, "missing": missing, "ignored_missing": ignored_missing, "source_gone": source_gone}


def prove_assertion_fails_on_stale_index():
    people = {1: {"name": "Keep", "ignored": 0}, 3: {"name": "IgnoreMe", "ignored": 1}}
    stale = {"person_ids": [1, 2]}
    ok, detail = assert_loaded_index_ok(stale, people, source=2, keep=1, ignore=3)
    if ok:
        raise AssertionError("stale index should fail")
    if not detail["missing"] or 3 not in detail["ignored_missing"]:
        raise AssertionError("negative case did not catch missing refs or absent passerby: %s" % detail)
    return detail

def main():
    if WORK.exists():
        import shutil
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    os.environ['PHOTO_LIBRARY_DATA'] = str(WORK.resolve())
    os.environ['PHOTO_WEB_ROOT'] = str((ROOT / 'web').resolve())
    os.environ['NO_ALBUMENTATIONS_UPDATE'] = '1'
    sys.path.insert(0, str(ROOT))
    import app
    from fastapi.testclient import TestClient
    client = TestClient(app.app)
    status = client.get('/api/status').json()
    assert 'r04e' in status['capabilities']['data_dir']

    with app.db() as c:
        ids = []
        for i, name in enumerate(['Keep', 'MergeSource', 'IgnoreMe'], start=1):
            pid = c.execute('INSERT INTO people(name,confirmed) VALUES (?,1)', (name,)).lastrowid
            sha = hashlib.sha256(f'r04e-{i}'.encode()).hexdigest()
            aid = c.execute('INSERT INTO assets(sha256,metadata,created_at) VALUES (?,?,?)', (sha, '{}', app.now())).lastrowid
            c.execute('INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)', (aid, str(WORK / f'{i}.jpg'), 10, 0, app.now()))
            emb = (b'\x00' * 4 + bytes([i]) + b'\x00' * 507)
            c.execute('INSERT INTO faces(asset_id,person_id,bbox,embedding,score) VALUES (?,?,?,?,?)', (aid, pid, '[0,0,1,1,8,8]', emb[:512] if len(emb) >= 512 else emb + b'\x00' * (512 - len(emb)), 0.9))
            ids.append(pid)
        keep, source, ignore = ids

    app.invalidate_face_index()
    barrier = {'after_query': threading.Event(), 'allow_publish': threading.Event(), 'merged': None, 'ignored': None, 'error': None}
    orig_db = app.db

    @contextmanager
    def gated_db():
        with orig_db() as c:
            yield c
            if any(frame.function == 'load_face_index' for frame in inspect.stack()):
                barrier['after_query'].set()
                if not barrier['allow_publish'].wait(8):
                    raise TimeoutError('publish gate timed out')

    app.db = gated_db
    loaded = {}

    def loader():
        try:
            loaded['index'] = app.load_face_index()
        except Exception as exc:
            barrier['error'] = repr(exc)

    t = threading.Thread(target=loader)
    t.start()
    if not barrier['after_query'].wait(8):
        raise TimeoutError('query gate timed out')
    barrier['merged'] = client.post(f'/api/people/{source}/merge', json={'target_id': keep})
    barrier['ignored'] = client.post(f'/api/people/{ignore}/ignore', json={'ignored': True})
    barrier['allow_publish'].set()
    t.join(10)
    app.db = orig_db
    if t.is_alive():
        raise AssertionError('loader did not terminate')
    if barrier['error']:
        raise AssertionError(barrier['error'])
    index = loaded.get('index')
    if index is None:
        raise AssertionError('concurrent load returned no index')
    with orig_db() as c:
        people = {r['id']: dict(r) for r in c.execute('SELECT * FROM people')}
        faces = [dict(r) for r in c.execute('SELECT id,person_id,ignored FROM faces')]
        fk = list(c.execute('PRAGMA foreign_key_check'))
        integ = c.execute('PRAGMA integrity_check').fetchone()[0]
    ok_index, detail = assert_loaded_index_ok(index, people, source, keep, ignore)
    refs = detail['refs']; missing=detail['missing']; ignored_missing=detail['ignored_missing']; source_gone=detail['source_gone']
    payload = {
        'verified_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'data_dir': status['capabilities']['data_dir'],
        'merge_status': barrier['merged'].status_code,
        'ignore_status': barrier['ignored'].status_code,
        'loader_error': barrier['error'],
        'people': {str(k): {'name': v.get('name'), 'ignored': v.get('ignored')} for k, v in people.items()},
        'index_person_ids': refs,
        'missing_refs': missing,
        'ignored_missing_from_index': ignored_missing,
        'source_gone': source_gone,
        'fk': fk,
        'integrity': integ,
        'passed': barrier['merged'].status_code == 200 and barrier['ignored'].status_code == 200 and not missing and not ignored_missing and source_gone and integ == 'ok' and fk == [] and barrier['error'] is None,
    }
    negative = prove_assertion_fails_on_stale_index()
    payload['negative_stale_index'] = negative
    (OUT / 'r04e.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k: payload[k] for k in ['passed', 'merge_status', 'ignore_status', 'missing_refs', 'ignored_missing_from_index', 'source_gone', 'integrity', 'loader_error', 'index_person_ids']}, ensure_ascii=False, indent=2))
    sys.exit(0 if payload['passed'] else 1)


if __name__ == '__main__':
    main()
