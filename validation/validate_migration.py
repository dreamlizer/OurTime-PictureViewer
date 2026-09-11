import os,sys,json,sqlite3,hashlib,shutil,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'data/backups/library-20260907-175247-0b7c75.sqlite3'
TARGET=ROOT/'validation/work'/('migration-'+time.strftime('%Y%m%d-%H%M%S'))
TARGET.mkdir(parents=True)
shutil.copy2(SOURCE,TARGET/'library.sqlite3')
def signature(path):
    with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as c:
        digest=hashlib.sha256()
        for row in c.execute('SELECT id,sha256,metadata,manual_date,manual_precision,manual_place,notes FROM assets ORDER BY id'):
            digest.update(json.dumps(row,ensure_ascii=False).encode())
        return {'assets':c.execute('SELECT count(*) FROM assets').fetchone()[0],
                'files':c.execute('SELECT count(*) FROM files').fetchone()[0],
                'faces':c.execute('SELECT count(*) FROM faces').fetchone()[0],
                'people':c.execute('SELECT count(*) FROM people').fetchone()[0],
                'asset_signature':digest.hexdigest()}
before=signature(TARGET/'library.sqlite3')
env={**os.environ,'PHOTO_LIBRARY_DATA':str(TARGET),'NO_ALBUMENTATIONS_UPDATE':'1'}
run=subprocess.run([sys.executable,'-c','import app; print(app.status()["capabilities"]["version"])'],env=env,cwd=ROOT,capture_output=True)
if run.returncode: raise RuntimeError(run.stderr.decode(errors='replace'))
after=signature(TARGET/'library.sqlite3')
assert before==after,(before,after)
with sqlite3.connect(TARGET/'library.sqlite3') as c:
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert c.execute('SELECT count(*) FROM assets WHERE excluded=1').fetchone()[0]==0
report={'passed':True,'source_backup':str(SOURCE),'test_database':str(TARGET),'before':before,'after':after,
        'checks':['真实旧版数据库迁移后原始字段和人工补录签名保持一致','文件、照片、人物、人脸数量保持一致','数据库完整性检查通过','迁移未自动排除用户照片']}
(ROOT/'validation/reports/migration-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
