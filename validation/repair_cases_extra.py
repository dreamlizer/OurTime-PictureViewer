from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path



def seed_asset(app, c, key, **fields):
    sha = hashlib.sha256(str(key).encode()).hexdigest()
    cols = {'sha256': sha, 'metadata': '{}', 'created_at': app.now()}
    cols.update(fields)
    aid = c.execute(
        'INSERT INTO assets(' + ','.join(cols) + ') VALUES (' + ','.join('?' for _ in cols) + ')',
        list(cols.values()),
    ).lastrowid
    return aid, sha


class DummyFace:
    def __init__(self, bbox, emb, score=0.9):
        self.bbox = bbox
        self.normed_embedding = emb
        self.det_score = score


class DummyEngine:
    def __init__(self, faces):
        self.faces = faces
        self.calls = 0

    def get(self, arr):
        self.calls += 1
        return self.faces


def case_r04(report: Path, work: Path):
    from repair_checks import CaseFailure, assert_true, import_app
    import numpy as np
    from PIL import Image
    data = work / 'r04'
    if data.exists():
        shutil.rmtree(data)
    photos = data / 'photos'
    photos.mkdir(parents=True)
    img1 = photos / 'a.jpg'
    img2 = photos / 'b.jpg'
    Image.new('RGB', (80, 80), (20, 20, 20)).save(img1)
    Image.new('RGB', (80, 80), (30, 30, 30)).save(img2)
    app = import_app(data)
    emb = np.ones(512, dtype=np.float32)
    emb = emb / np.linalg.norm(emb)
    engine = DummyEngine([DummyFace([5, 5, 50, 50], emb)])
    app.get_face_engine = lambda: engine
    real_save = Image.Image.save
    boom = {'n': 0}

    def flaky_save(self, *a, **k):
        boom['n'] += 1
        if boom['n'] == 1:
            raise OSError('injected crop failure')
        return real_save(self, *a, **k)

    Image.Image.save = flaky_save
    try:
        with app.db() as c:
            aid1, _ = seed_asset(app, c, 'r04-1')
            c.execute(
                "INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)",
                (aid1, str(img1), 10, 0, app.now()),
            )
            aid2, _ = seed_asset(app, c, 'r04-2')
            c.execute(
                "INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)",
                (aid2, str(img2), 10, 0, app.now()),
            )
        try:
            app.process_faces(aid1, img1)
            raise CaseFailure('first process should fail')
        except OSError:
            pass
        with app.db() as c:
            people = c.execute('SELECT count(*) FROM people').fetchone()[0]
            faces = c.execute('SELECT count(*) FROM faces').fetchone()[0]
            state = c.execute('SELECT face_state FROM assets WHERE id=?', (aid1,)).fetchone()[0]
            assert_true(people == 0 and faces == 0, f'R04-A residue people={people} faces={faces}')
            assert_true(state != 1, 'R04-A face_state=1 after rollback')
        app.process_faces(aid2, img2)
        with app.db() as c:
            people = c.execute('SELECT count(*) FROM people').fetchone()[0]
            faces = c.execute('SELECT count(*) FROM faces').fetchone()[0]
            assert_true(people == 1 and faces == 1, 'R04-B second image did not succeed')
            fk = list(c.execute('PRAGMA foreign_key_check'))
            assert_true(fk == [], str(fk))
            c.execute("UPDATE people SET name='X', alias='Y', confirmed=1 WHERE id=(SELECT person_id FROM faces LIMIT 1)")
            aid_done = c.execute('SELECT id FROM assets WHERE face_state=1').fetchone()['id']
        calls = engine.calls
        app.process_faces(aid_done, img2)
        assert_true(engine.calls == calls, 'R04-G reprocessed completed asset')
        with app.db() as c:
            person = dict(c.execute('SELECT name,alias FROM people LIMIT 1').fetchone())
            assert_true(person['name'] == 'X' and person['alias'] == 'Y', 'R04-G annotations changed')
    finally:
        Image.Image.save = real_save
    (report / 'logs' / 'r04.json').write_text(json.dumps({'ok': True}, ensure_ascii=False), encoding='utf-8')
    return {'id': 'R04', 'status': 'PASS', 'evidence': [str(report / 'logs' / 'r04.json')]}


def case_r05(report: Path, work: Path):
    from repair_checks import CaseFailure, assert_true, import_app
    data = work / 'r05'
    if data.exists():
        shutil.rmtree(data)
    app = import_app(data)
    from fastapi.testclient import TestClient
    client = TestClient(app.app)
    with app.db() as c:
        a1, _ = seed_asset(app, c, 'r05-auto', place='公园附近', latitude=39.9, longitude=116.4)
        c.execute("INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)", (a1, str(data / 'a.jpg'), 1, 0, app.now()))
        a2, _ = seed_asset(app, c, 'r05-manual', place='公园附近', manual_place='旧人工', latitude=39.91, longitude=116.41)
        c.execute("INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)", (a2, str(data / 'b.jpg'), 1, 0, app.now()))
        a3, _ = seed_asset(app, c, 'r05-ex', place='公园附近', excluded=1)
        c.execute("INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,excluded) VALUES (?,?,?,?,?,1)", (a3, str(data / 'c.jpg'), 1, 0, app.now()))
    same = client.post('/api/places/rename', json={'from_place': '公园附近', 'to_place': '公园附近'})
    assert_true(same.status_code == 400, 'same target should fail')
    empty = client.post('/api/places/rename', json={'from_place': '公园附近', 'to_place': '  '})
    assert_true(empty.status_code == 400, 'empty target')
    ok = client.post('/api/places/rename', json={'from_place': '公园附近', 'to_place': '北京 · 某园', 'remember': True}).json()
    assert_true(ok['updated'] == 1, f"updated {ok}")
    detail = client.get(f'/api/photos/{a1}').json()
    assert_true(detail['place'] == '公园附近', 'original place mutated')
    assert_true(detail['manual_place'] == '北京 · 某园', 'manual not set')
    manual = client.get(f'/api/photos/{a2}').json()
    assert_true(manual['manual_place'] == '旧人工', 'unrelated manual changed')
    with app.db() as c:
        edit = dict(c.execute("SELECT before_json FROM edits WHERE target=?", (f'asset:{a1}',)).fetchone())
        assert_true(json.loads(edit['before_json'])['manual_place'] is None, 'before_json should keep NULL manual')
        excluded = dict(c.execute('SELECT manual_place,place FROM assets WHERE id=?', (a3,)).fetchone())
        assert_true(excluded['manual_place'] is None and excluded['place'] == '公园附近', 'excluded mutated')
    miss = client.post('/api/places/rename', json={'from_place': '不存在的地方', 'to_place': '新', 'remember': True}).json()
    assert_true(miss['updated'] == 0, 'unmatched updated')
    (report / 'logs' / 'r05.json').write_text(json.dumps(ok, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'id': 'R05', 'status': 'PASS', 'evidence': [str(report / 'logs' / 'r05.json')]}


def case_r06(report: Path, work: Path):
    from repair_checks import CaseFailure, assert_true, import_app
    data = work / 'r06'
    if data.exists():
        shutil.rmtree(data)
    app = import_app(data)
    from fastapi.testclient import TestClient
    client = TestClient(app.app)
    with app.db() as c:
        a_id, _ = seed_asset(app, c, 'r06-a')
        b_id, _ = seed_asset(app, c, 'r06-b')
        c.execute("INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)", (a_id, str(data / 'a.jpg'), 1, 0, app.now()))
        c.execute("INSERT INTO files(asset_id,path,size,mtime_ns,modified_at) VALUES (?,?,?,?,?)", (b_id, str(data / 'b.jpg'), 1, 0, app.now()))
        pid_a = c.execute("INSERT INTO people(name,confirmed) VALUES ('A',1)").lastrowid
        pid_b = c.execute("INSERT INTO people(name,confirmed,suggested_person_id) VALUES ('B',0,?)", (pid_a,)).lastrowid
        fa = c.execute(
            "INSERT INTO faces(asset_id,person_id,bbox,embedding,score) VALUES (?,?,?,?,?)",
            (a_id, pid_a, '[0,0,1,1,8,8]', b'\x00' * 8, 0.9),
        ).lastrowid
    res = client.post(f'/api/faces/{fa}/ignore', json={'ignored': True}).json()
    with app.db() as c:
        b = dict(c.execute('SELECT * FROM people WHERE id=?', (pid_b,)).fetchone())
        assert_true(b['suggested_person_id'] is None, 'R06.1 suggested still points to deleted person')
        gone = c.execute('SELECT 1 FROM people WHERE id=?', (pid_a,)).fetchone()
        assert_true(gone is None, 'person A not deleted')
        fk = list(c.execute('PRAGMA foreign_key_check'))
        assert_true(fk == [], str(fk))
    restore = client.post(f'/api/faces/{fa}/ignore', json={'ignored': False})
    assert_true(restore.status_code == 200, restore.text)
    (report / 'logs' / 'r06.json').write_text(json.dumps({'ignore': res}, ensure_ascii=False), encoding='utf-8')
    return {'id': 'R06', 'status': 'PASS', 'evidence': [str(report / 'logs' / 'r06.json')]}


def case_r07(report: Path, work: Path):
    from repair_checks import CaseFailure, assert_true, import_app
    from library_db import migrate_place_overrides
    data_a = work / 'r07a'
    data_b = work / 'r07b'
    for d in (data_a, data_b):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    (data_a / 'place-overrides.json').write_text(
        json.dumps({'places': [{'name': '规则甲', 'latitude': 39.9, 'longitude': 116.4, 'radius_km': 0.4, 'source': 'user'}]}, ensure_ascii=False),
        encoding='utf-8',
    )
    (data_b / 'place-overrides.json').write_text(
        json.dumps({'places': [{'name': '规则乙', 'latitude': 31.2, 'longitude': 121.5, 'radius_km': 0.4}]}, ensure_ascii=False),
        encoding='utf-8',
    )
    app = import_app(data_a)
    name, _ = app.nearest_place(39.9, 116.4)
    assert_true(name == '规则甲', f'R07-A expected 规则甲 got {name}')
    app2 = import_app(data_b)
    name2, _ = app2.nearest_place(31.2, 121.5)
    assert_true(name2 == '规则乙', f'R07-A isolation failed {name2}')
    name_cross, _ = app2.nearest_place(39.9, 116.4)
    assert_true(name_cross != '规则甲', 'R07-A leaked other library rule')
    with app.db() as c:
        again = migrate_place_overrides(c, data_a)
        assert_true(again['status'] == 'skipped', again)
        c.execute("UPDATE place_rules SET latitude=1 WHERE name='规则甲'")
        third = migrate_place_overrides(c, data_a)
        lat = c.execute("SELECT latitude FROM place_rules WHERE name='规则甲'").fetchone()[0]
        assert_true(third['status'] == 'skipped' and lat == 1, 'duplicate import overwrote edits')
    (data_a / 'place-overrides.json').write_text('{bad', encoding='utf-8')
    with app.db() as c:
        bad = migrate_place_overrides(c, data_a)
        # A successful import must not be revoked by a later unreadable JSON.
        assert_true(bad['status'] == 'skipped', bad)
        still = c.execute("SELECT latitude FROM place_rules WHERE name='规则甲'").fetchone()[0]
        assert_true(still == 1, 'corrupt json wiped rules')
    bak = app.backup()
    restore_dir = work / 'r07-restore'
    if restore_dir.exists():
        shutil.rmtree(restore_dir)
    restore_dir.mkdir()
    shutil.copy2(bak['path'], restore_dir / 'library.sqlite3')
    app3 = import_app(restore_dir)
    with app3.db() as c:
        n = c.execute('SELECT count(*) FROM place_rules').fetchone()[0]
        assert_true(n >= 1, 'backup missing place_rules')
        fk = list(c.execute('PRAGMA foreign_key_check'))
        assert_true(fk == [], str(fk))
        integ = c.execute('PRAGMA integrity_check').fetchone()[0]
        assert_true(integ == 'ok', integ)
    (report / 'logs' / 'r07.json').write_text(json.dumps({'a': name, 'b': name2, 'backup': bak['path']}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'id': 'R07', 'status': 'PASS', 'evidence': [str(report / 'logs' / 'r07.json')]}
