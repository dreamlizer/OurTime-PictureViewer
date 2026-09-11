"""Refresh only generated settlement labels; preserve source metadata and user notes."""
import argparse,hashlib,json,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from geo_labels import PlaceIndex

def run(data):
    geo=PlaceIndex(Path('G:/CodexModels/geo'))
    with sqlite3.connect(data/'library.sqlite3',timeout=30) as c:
        c.row_factory=sqlite3.Row
        def signature():
            digest=hashlib.sha256()
            for row in c.execute('SELECT id,sha256,metadata,manual_date,manual_precision,manual_place,notes FROM assets ORDER BY id'):
                digest.update(json.dumps(list(row),ensure_ascii=False).encode())
            return digest.hexdigest()
        before=signature()
        rows=c.execute("""SELECT id,latitude,longitude,place,place_source FROM assets
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            AND coalesce(manual_place,'')=''
            AND (place_source LIKE 'GeoNames %' OR place_source LIKE '离线%' OR place_source LIKE '离线地名库%')""").fetchall()
        changes=[]
        for row in rows:
            place,source=geo.nearest(row['latitude'],row['longitude'])
            if place:
                changes.append((place,source,row['id'],row['place'],row['place_source']))
        for i in range(0,len(changes),500):
            c.executemany('UPDATE assets SET place=?,place_source=? WHERE id=? AND place=? AND place_source=?',changes[i:i+500]);c.commit()
        after=signature()
        if before!=after:raise RuntimeError('原始元数据或人工字段签名变化')
        report={'time':time.strftime('%Y-%m-%dT%H:%M:%S'),'updated':len(changes),'source_and_manual_fields_unchanged':True,'signature':after,'examples':[{'id':x[2],'before':x[3],'after':x[0]} for x in changes[:20]],'integrity':c.execute('PRAGMA integrity_check').fetchone()[0]}
    (data/'chinese-place-upgrade.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return report
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True,type=Path)
    run(parser.parse_args().data.resolve())
