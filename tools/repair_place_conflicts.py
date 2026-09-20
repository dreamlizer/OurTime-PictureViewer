"""Plan/apply only GPS-proven cross-province automatic label conflicts."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geo_labels import PlaceIndex, administrative_prefix
from ourtime_config import DATA, GEO_ROOT
from tools.relabel_place_hierarchy import active_scan, backup_database


def build_plan(connection, index):
    plan = []
    districts = {}
    resolved = {}
    for row in connection.execute('''SELECT id,latitude,longitude,place,place_source,manual_place
        FROM assets WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        AND coalesce(manual_place,'')='' ORDER BY id'''):
        prefix = administrative_prefix(row['place'])
        source = row['place_source'] or ''
        if not prefix or not source.startswith(('OpenStreetMap ', 'GeoNames ', '离线地名库')):
            continue
        key = (row['latitude'], row['longitude'])
        if key not in districts:
            districts[key] = index._district_at(*key)
        district = districts[key]
        known = administrative_prefix(district)
        if not known or known[0] == prefix[0]:
            continue
        if key not in resolved:
            resolved[key] = index.nearest(*key, include_overrides=False)
        label, origin = resolved[key]
        if not label or administrative_prefix(label)[:len(known)] != known:
            raise RuntimeError(f'Unresolved boundary conflict for asset {row["id"]}')
        plan.append(dict(row) | {'next_place': label, 'next_source': origin, 'boundary': district})
    return plan


def apply_plan(connection, plan):
    connection.execute('BEGIN IMMEDIATE')
    try:
        if active_scan(connection):
            raise RuntimeError('Scan active; no changes applied')
        stamp = datetime.now().isoformat(timespec='seconds')
        for item in plan:
            current = connection.execute('SELECT latitude,longitude,place,place_source,manual_place FROM assets WHERE id=?', (item['id'],)).fetchone()
            keys = ('latitude','longitude','place','place_source','manual_place')
            if current is None or any(current[k] != item[k] for k in keys):
                raise RuntimeError(f'Asset {item["id"]} changed after planning; transaction rolled back')
            connection.execute('UPDATE assets SET place=?,place_source=? WHERE id=?',
                               (item['next_place'], item['next_source'], item['id']))
            before = {k:item[k] for k in ('place','place_source')}
            after = {'place':item['next_place'], 'place_source':item['next_source'], 'source':'gps_province_conflict_repair_v1'}
            connection.execute('INSERT INTO edits(created_at,target,before_json,after_json) VALUES(?,?,?,?)',
                               (stamp, f'asset:{item["id"]}', json.dumps(before,ensure_ascii=False), json.dumps(after,ensure_ascii=False)))
        if plan:
            tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'home_catalog' in tables:
                connection.execute("DELETE FROM home_catalog WHERE kind='place_revisit'")
            if 'home_recommendation_cache' in tables:
                connection.execute('DELETE FROM home_recommendation_cache')
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',type=Path,default=DATA/'library.sqlite3')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    database=args.database.resolve()
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        if active_scan(connection): raise RuntimeError('Scan active; no changes applied')
        index=PlaceIndex(GEO_ROOT)
        index.load()
        plan=build_plan(connection,index)
    result={'count':len(plan),'groups':dict(Counter(f'{x["place"]} -> {x["next_place"]}' for x in plan)), 'applied':False, 'items':plan}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.apply and plan:
        result['backup']=str(backup_database(database))
        with sqlite3.connect(database,timeout=30) as connection:
            connection.row_factory=sqlite3.Row
            apply_plan(connection,plan)
        result['applied']=True
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('items','groups')},ensure_ascii=False))


if __name__=='__main__': main()
