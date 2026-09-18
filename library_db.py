"""SQLite schema, migrations and place-rule helpers. Does not import app."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from functools import lru_cache
from datetime import datetime
from pathlib import Path

from pypinyin import lazy_pinyin

ACTIVE_ASSET = (
    "a.excluded=0 AND EXISTS(SELECT 1 FROM files af WHERE af.asset_id=a.id AND af.excluded=0)"
)
EFFECTIVE_PLACE = "coalesce(nullif(a.manual_place, ''), a.place)"
POLYPHONIC_SURNAMES = {
    '曾': 'zeng', '单': 'shan', '解': 'xie', '仇': 'qiu', '区': 'ou',
    '查': 'zha', '朴': 'piao', '乐': 'yue', '重': 'chong', '盖': 'ge',
    '万俟': 'moqi', '尉迟': 'yuchi', '长孙': 'zhangsun',
}
ASSET_LIST_COLUMNS = """
a.id, a.sha256, a.width, a.height, a.format, a.captured_at, a.date_source, a.date_precision,
a.latitude, a.longitude, a.place, a.place_source, a.camera, a.category, a.error,
a.manual_date, a.manual_precision, a.manual_place, a.notes, a.face_state, a.face_error,
a.created_at, a.excluded, a.exclude_reason, a.favorite, a.object_state, a.object_error,
    a.derivative_policy, a.derivative_operation_id
""".strip()

B01_SCHEMA_MIGRATION = "b01_visibility_and_face_index_v1"
M1_SCHEMA_MIGRATION = "m1_operation_safety_v1"
FACE_LABEL_LAYOUT_MIGRATION = "face_label_layout_v1"


def now():
    return datetime.now().isoformat(timespec='seconds')


@lru_cache(maxsize=8192)
def person_search_forms(value):
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    if not text:
        return ('', '', '')
    surname = next((part for part in ('万俟', '尉迟', '长孙') if text.startswith(part)), text[:1])
    prefix = POLYPHONIC_SURNAMES.get(surname)
    remainder = text[len(surname):] if prefix else text
    syllables = [
        normalize_person_search(part)
        for part in lazy_pinyin(remainder, errors=lambda chars: list(chars))
    ]
    syllables = [part for part in syllables if part]
    full = normalize_person_search((prefix or '') + ''.join(syllables))
    if prefix:
        prefix_initials = {
            '万俟': 'mq', '尉迟': 'yc', '长孙': 'zs',
        }.get(surname, prefix[:1])
    else:
        prefix_initials = ''
    initials = normalize_person_search(prefix_initials + ''.join(part[:1] for part in syllables))
    return (normalize_person_search(text), full, initials)


def normalize_person_search(value):
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    text = text.replace('u:', 'v').replace('ü', 'v')
    return ''.join(character for character in text if character.isalnum())


@lru_cache(maxsize=8192)
def person_pinyin_key(value):
    text = str(value or '').strip()
    return person_search_forms(text)[1] + '\0' + text.casefold() if text else ''


def person_search_match(name, alias, query):
    needle = normalize_person_search(query)
    if not needle:
        return 1
    for value in (name, alias):
        if any(needle in form for form in person_search_forms(value) if form):
            return 1
    return 0


def register_collations(conn):
    def compare(left, right):
        a = person_pinyin_key(left)
        b = person_pinyin_key(right)
        return (a > b) - (a < b)
    conn.create_collation('PERSON_PINYIN', compare)
    conn.create_function('PERSON_SEARCH_MATCH', 3, person_search_match, deterministic=True)


def init_schema(conn):
    register_collations(conn)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_autocheckpoint=2000')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS assets (
          id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, width INTEGER, height INTEGER,
          format TEXT, metadata TEXT NOT NULL DEFAULT '{}', captured_at TEXT, date_source TEXT,
          date_precision TEXT, latitude REAL, longitude REAL, place TEXT, place_source TEXT,
          camera TEXT, category TEXT NOT NULL DEFAULT '照片', error TEXT,
          manual_date TEXT, manual_precision TEXT, manual_place TEXT, notes TEXT NOT NULL DEFAULT '',
          face_state INTEGER NOT NULL DEFAULT 0, face_error TEXT, created_at TEXT NOT NULL,
          derivative_policy TEXT NOT NULL DEFAULT 'preserve'
            CHECK(derivative_policy IN ('preserve','purge')));
        CREATE TABLE IF NOT EXISTS files (
          id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES assets(id), path TEXT NOT NULL UNIQUE,
          size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, modified_at TEXT, exists_now INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX IF NOT EXISTS files_asset ON files(asset_id);
        CREATE TABLE IF NOT EXISTS people (
          id INTEGER PRIMARY KEY, name TEXT, confirmed INTEGER NOT NULL DEFAULT 0,
          suggested_person_id INTEGER REFERENCES people(id));
        CREATE TABLE IF NOT EXISTS faces (
          id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES assets(id), person_id INTEGER NOT NULL REFERENCES people(id),
          bbox TEXT NOT NULL, embedding BLOB NOT NULL, score REAL, crop TEXT, reviewed INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, roots TEXT NOT NULL, with_faces INTEGER NOT NULL, include_system INTEGER NOT NULL,
          status TEXT NOT NULL, discovered INTEGER DEFAULT 0, processed INTEGER DEFAULT 0,
          added INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
          current_path TEXT DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT, message TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS job_errors (
          id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), path TEXT, stage TEXT, message TEXT);
        CREATE TABLE IF NOT EXISTS edits (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, target TEXT NOT NULL, before_json TEXT, after_json TEXT);
        CREATE TABLE IF NOT EXISTS excluded_roots (
          id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS place_rules (
          name TEXT PRIMARY KEY,
          latitude REAL NOT NULL CHECK(latitude>=-90 AND latitude<=90 AND latitude=latitude),
          longitude REAL NOT NULL CHECK(longitude>=-180 AND longitude<=180 AND longitude=longitude),
          radius_km REAL NOT NULL CHECK(radius_km>0 AND radius_km=radius_km),
          source TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS place_area_rules (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          coordinate_space TEXT NOT NULL CHECK(coordinate_space IN ('wgs84','gcj02')),
          west REAL NOT NULL,
          south REAL NOT NULL,
          east REAL NOT NULL,
          north REAL NOT NULL,
          source TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS place_area_rules_bounds
          ON place_area_rules(coordinate_space, west, south, east, north);
        CREATE TABLE IF NOT EXISTS place_rule_imports (
          path TEXT PRIMARY KEY,
          sha256 TEXT NOT NULL,
          imported_at TEXT NOT NULL,
          status TEXT NOT NULL,
          message TEXT NOT NULL DEFAULT '');
    ''')
    migrations = {
        'assets': {
            'excluded': 'INTEGER NOT NULL DEFAULT 0',
            'exclude_reason': "TEXT NOT NULL DEFAULT ''",
            'favorite': 'INTEGER NOT NULL DEFAULT 0',
            # Historical exclusions have no trustworthy structured proof that
            # derivative deletion was authorized, so migration is conservative.
            'derivative_policy': (
                "TEXT NOT NULL DEFAULT 'preserve' "
                "CHECK(derivative_policy IN ('preserve','purge'))"
            ),
            'derivative_operation_id': 'TEXT',
        },
        'files': {'excluded': 'INTEGER NOT NULL DEFAULT 0'},
        'jobs': {
            'workers': 'INTEGER NOT NULL DEFAULT 2',
            'excluded': 'INTEGER NOT NULL DEFAULT 0',
            'auxiliary': 'INTEGER NOT NULL DEFAULT 0',
            'metadata_reads': 'INTEGER NOT NULL DEFAULT 0',
            'total': 'INTEGER NOT NULL DEFAULT 0',
            'phase': "TEXT NOT NULL DEFAULT ''",
            'current_stage': "TEXT NOT NULL DEFAULT ''",
            'face_photos': 'INTEGER NOT NULL DEFAULT 0',
            'faces_found': 'INTEGER NOT NULL DEFAULT 0',
            'face_seconds': 'REAL NOT NULL DEFAULT 0',
        },
        'people': {
            'alias': "TEXT NOT NULL DEFAULT ''",
            'ignored': 'INTEGER NOT NULL DEFAULT 0',
            'cover_face_id': 'INTEGER',
        },
        'faces': {'ignored': 'INTEGER NOT NULL DEFAULT 0'},
    }
    for table, columns in migrations.items():
        existing = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
    conn.execute('CREATE INDEX IF NOT EXISTS files_asset_excluded ON files(asset_id,excluded)')
    conn.execute('CREATE INDEX IF NOT EXISTS assets_excluded ON assets(excluded)')
    conn.execute('CREATE INDEX IF NOT EXISTS assets_active_location ON assets(excluded,latitude,longitude) WHERE latitude IS NOT NULL AND longitude IS NOT NULL')
    conn.execute('CREATE INDEX IF NOT EXISTS assets_favorite ON assets(favorite,id)')
    conn.execute('CREATE INDEX IF NOT EXISTS assets_browse_date ON assets(coalesce(manual_date,captured_at),id)')
    conn.execute('CREATE INDEX IF NOT EXISTS people_ignored ON people(ignored,confirmed,id)')
    conn.execute('CREATE INDEX IF NOT EXISTS faces_asset ON faces(asset_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS faces_person_asset ON faces(person_id, asset_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS files_path_nocase ON files(path COLLATE NOCASE)')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS face_index_state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            revision INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO face_index_state(id,revision) VALUES (1,0);
        CREATE TABLE IF NOT EXISTS object_tags (
            asset_id INTEGER NOT NULL REFERENCES assets(id),
            label TEXT NOT NULL,
            score REAL NOT NULL,
            PRIMARY KEY(asset_id, label)
        );
        CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            state_committed INTEGER NOT NULL DEFAULT 0,
            cleanup_status TEXT NOT NULL DEFAULT 'not_applicable',
            request_json TEXT NOT NULL,
            result_json TEXT,
            undo_json TEXT,
            error_code TEXT,
            error_detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operation_items (
            operation_id TEXT NOT NULL REFERENCES operations(operation_id) ON DELETE CASCADE,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(operation_id,item_kind,item_id)
        );
        CREATE TABLE IF NOT EXISTS face_label_overrides (
            face_id INTEGER PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE,
            asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            x_ratio REAL NOT NULL CHECK(x_ratio>=0 AND x_ratio<=1),
            y_ratio REAL NOT NULL CHECK(y_ratio>=0 AND y_ratio<=1),
            layout_version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS face_label_overrides_asset
            ON face_label_overrides(asset_id);
    ''')
    conn.executescript('''
        CREATE TRIGGER IF NOT EXISTS face_index_faces_insert
        AFTER INSERT ON faces BEGIN
          UPDATE face_index_state SET revision=revision+1 WHERE id=1;
        END;
        CREATE TRIGGER IF NOT EXISTS face_index_faces_update
        AFTER UPDATE ON faces BEGIN
          UPDATE face_index_state SET revision=revision+1 WHERE id=1;
        END;
        CREATE TRIGGER IF NOT EXISTS face_index_faces_delete
        AFTER DELETE ON faces BEGIN
          UPDATE face_index_state SET revision=revision+1 WHERE id=1;
        END;
        CREATE TRIGGER IF NOT EXISTS face_index_people_insert
        AFTER INSERT ON people BEGIN
          UPDATE face_index_state SET revision=revision+1 WHERE id=1;
        END;
        CREATE TRIGGER IF NOT EXISTS face_index_people_update
        AFTER UPDATE ON people BEGIN
          UPDATE face_index_state SET revision=revision+1 WHERE id=1;
        END;
        CREATE TRIGGER IF NOT EXISTS face_index_people_delete
        AFTER DELETE ON people BEGIN
          UPDATE face_index_state SET revision=revision+1 WHERE id=1;
        END;
    ''')
    invalid_policy = conn.execute(
        """SELECT count(*) FROM assets
           WHERE derivative_policy NOT IN ('preserve','purge')
              OR derivative_policy IS NULL"""
    ).fetchone()[0]
    if invalid_policy:
        raise RuntimeError(
            f"B01 migration found {invalid_policy} invalid derivative policies"
        )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(name,applied_at) VALUES (?,?)",
        (B01_SCHEMA_MIGRATION, now()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(name,applied_at) VALUES (?,?)",
        (M1_SCHEMA_MIGRATION, now()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(name,applied_at) VALUES (?,?)",
        (FACE_LABEL_LAYOUT_MIGRATION, now()),
    )
    conn.execute('CREATE INDEX IF NOT EXISTS object_tags_label ON object_tags(label, score DESC)')
    existing_assets = {r[1] for r in conn.execute('PRAGMA table_info(assets)')}
    for name, declaration in {
        'object_state': 'INTEGER NOT NULL DEFAULT 0',
        'object_error': 'TEXT',
    }.items():
        if name not in existing_assets:
            conn.execute(f'ALTER TABLE assets ADD COLUMN {name} {declaration}')


def recover_interrupted_jobs(conn):
    """Recover stale jobs only after the process owns this data directory."""
    conn.execute(
        "UPDATE jobs SET status='paused', message='上次运行中断，可继续扫描' "
        "WHERE status IN ('running','pausing')"
    )


def json_sha256(path: Path):
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), data


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) == float("inf"):
        return None
    return number


def parse_place_rule_values(latitude, longitude, radius_km=0.25):
    lat = finite_number(latitude)
    lon = finite_number(longitude)
    radius = finite_number(radius_km if radius_km is not None else 0.25)
    if None in (lat, lon, radius):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and radius > 0):
        return None
    return lat, lon, radius


def parse_place_rule(item):
    if not isinstance(item, dict):
        return None
    return parse_place_rule_values(item.get("latitude"), item.get("longitude"), item.get("radius_km", 0.25))


def migrate_place_overrides(conn, data_dir):
    """Import place-overrides.json once from the active library directory only."""
    folder = Path(data_dir).resolve()
    path = folder / "place-overrides.json"
    marker = str(path)
    existing = conn.execute("SELECT sha256,status,message FROM place_rule_imports WHERE path=?", (marker,)).fetchone()
    already_ok = bool(existing and existing["status"] == "ok")
    if not path.exists():
        return {"status": "absent" if not already_ok else "skipped", "path": marker}
    try:
        digest, raw = json_sha256(path)
        payload = json.loads(raw.decode("utf-8"))
        places = payload.get("places") if isinstance(payload, dict) else None
        if not isinstance(places, list):
            raise ValueError("places 不是列表")
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        if already_ok:
            return {"status": "skipped", "path": marker, "sha256": existing["sha256"], "read_error": str(exc)}
        conn.execute(
            """INSERT INTO place_rule_imports(path,sha256,imported_at,status,message)
               VALUES (?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, imported_at=excluded.imported_at,
               status=excluded.status, message=excluded.message""",
            (marker, "", now(), "failed", f"JSON 损坏或格式错误：{exc}"),
        )
        return {"status": "failed", "path": marker, "message": str(exc)}
    if already_ok:
        return {"status": "skipped", "path": marker, "sha256": existing["sha256"]}
    parsed_rows = []
    rejected = []
    for item in places:
        if not isinstance(item, dict):
            rejected.append({"reason": "not an object"})
            continue
        name = str(item.get("name") or "").strip()
        parsed = parse_place_rule(item)
        if not name or parsed is None:
            rejected.append({"name": name, "reason": "invalid coordinates or name"})
            continue
        lat, lon, radius = parsed
        parsed_rows.append((name, lat, lon, radius, item.get("source") or ("用户确认：" + name)))
    stamp = now()
    if rejected:
        conn.execute(
            """INSERT INTO place_rule_imports(path,sha256,imported_at,status,message)
               VALUES (?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, imported_at=excluded.imported_at,
               status=excluded.status, message=excluded.message""",
            (marker, digest, stamp, "failed", f"rejected {len(rejected)} invalid place rule(s); import aborted"),
        )
        return {"status": "failed", "path": marker, "sha256": digest, "imported": 0, "rejected": len(rejected), "message": "import aborted because the file contains invalid rules"}
    imported = 0
    skipped_existing = 0
    for name, lat, lon, radius, source in parsed_rows:
        row = conn.execute("SELECT name FROM place_rules WHERE name=?", (name,)).fetchone()
        if row:
            skipped_existing += 1
            continue
        conn.execute(
            """INSERT INTO place_rules(name,latitude,longitude,radius_km,source,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (name, lat, lon, radius, source, stamp, stamp),
        )
        imported += 1
    conn.execute(
        """INSERT INTO place_rule_imports(path,sha256,imported_at,status,message)
           VALUES (?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, imported_at=excluded.imported_at,
           status=excluded.status, message=excluded.message""",
        (marker, digest, stamp, "ok", f"imported {imported}, kept existing {skipped_existing}"),
    )
    return {"status": "ok", "path": marker, "sha256": digest, "imported": imported, "rejected": 0, "kept_existing": skipped_existing}


def load_place_rules(conn):
    rows = conn.execute(
        "SELECT name, latitude, longitude, radius_km, source, created_at, updated_at FROM place_rules ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def place_rules_version(conn):
    row = conn.execute("SELECT count(*) n, coalesce(max(updated_at),'') t FROM place_rules").fetchone()
    areas = conn.execute(
        "SELECT count(*) n, coalesce(max(updated_at),'') t FROM place_area_rules"
    ).fetchone()
    return f"{row['n']}:{row['t']}:{areas['n']}:{areas['t']}"


def upsert_place_rule(conn, name, latitude, longitude, radius_km, source):
    parsed = parse_place_rule_values(latitude, longitude, radius_km)
    if parsed is None:
        raise ValueError("地点规则坐标或半径无效")
    lat, lon, radius = parsed
    stamp = now()
    row = conn.execute("SELECT created_at FROM place_rules WHERE name=?", (name,)).fetchone()
    created = row["created_at"] if row else stamp
    conn.execute(
        """INSERT INTO place_rules(name,latitude,longitude,radius_km,source,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET
             latitude=excluded.latitude, longitude=excluded.longitude, radius_km=excluded.radius_km,
             source=excluded.source, updated_at=excluded.updated_at""",
        (name, lat, lon, radius, source, created, stamp),
    )


def load_place_area_rules(conn):
    rows = conn.execute(
        """SELECT id, name, coordinate_space, west, south, east, north, source,
                  created_at, updated_at
           FROM place_area_rules ORDER BY id"""
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_place_area_rule(conn, name, coordinate_space, bounds, source):
    label = str(name or "").strip()
    space = str(coordinate_space or "").strip().lower()
    if not label or space not in {"wgs84", "gcj02"} or not isinstance(bounds, dict):
        raise ValueError("框选地点规则无效")
    values = []
    for key in ("west", "south", "east", "north"):
        try:
            value = float(bounds[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("框选地点规则无效") from exc
        if not (value == value) or abs(value) == float("inf"):
            raise ValueError("框选地点规则无效")
        values.append(value)
    west, south, east, north = values
    stamp = now()
    existing = conn.execute(
        """SELECT created_at FROM place_area_rules
           WHERE coordinate_space=? AND west=? AND south=? AND east=? AND north=?""",
        (space, west, south, east, north),
    ).fetchone()
    created = existing["created_at"] if existing else stamp
    conn.execute(
        """INSERT INTO place_area_rules(
             name, coordinate_space, west, south, east, north, source, created_at, updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(coordinate_space, west, south, east, north) DO UPDATE SET
             name=excluded.name, source=excluded.source, updated_at=excluded.updated_at""",
        (label, space, west, south, east, north, source, created, stamp),
    )
