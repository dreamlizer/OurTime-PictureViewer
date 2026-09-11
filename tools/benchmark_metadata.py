"""Read-only small-sample comparison of ExifTool invocation strategies."""
import os,json,sqlite3,subprocess,time,re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

BASE=Path(__file__).resolve().parents[1]
EXE=next((BASE/'tools'/'exiftool').glob('*/exiftool.exe'))
with sqlite3.connect(f'file:{(BASE/"data/library.sqlite3").as_posix()}?mode=ro',uri=True) as c:
    rows=c.execute('''SELECT f.path,f.size FROM files f JOIN assets a ON a.id=f.asset_id
      WHERE a.error IS NULL AND f.exists_now=1 AND a.format='JPEG' AND f.size<15000000
      GROUP BY a.id ORDER BY a.id LIMIT 16''').fetchall()
paths=[r[0] for r in rows]
env={**os.environ,'LC_ALL':'C','LC_CTYPE':'C','LANG':'C'}
def read(paths):
    result=subprocess.run([str(EXE),'-json','-G1','-a','-s','-n','-struct','-charset','filename=UTF8','-@','-'],
         input=('\n'.join(paths)+'\n').encode('utf-8'),env=env,capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    diagnostics=result.stderr.decode('utf-8',errors='replace').strip()
    if result.returncode or (diagnostics and not re.fullmatch(r'\d+ image files read',diagnostics)):
        raise RuntimeError(diagnostics)
    records=json.loads(result.stdout)
    if len(records)!=len(paths): raise RuntimeError('Output record count mismatch')
    return records

# Warm the same small sample once so each strategy sees cached input.
read(paths)
results=[]
for strategy,workers in [('serial',1),('parallel_2',2),('parallel_4',4),('batch_16',0)]:
    start=time.perf_counter()
    if workers==0:
        records=read(paths)
    elif workers==1:
        records=[read([path])[0] for path in paths]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            records=[r[0] for r in pool.map(lambda p:read([p]),paths)]
    elapsed=time.perf_counter()-start
    results.append({'strategy':strategy,'files':len(records),'seconds':round(elapsed,3),'files_per_second':round(len(records)/elapsed,2)})
    print(json.dumps(results[-1]),flush=True)
report={'time':time.strftime('%Y-%m-%dT%H:%M:%S'),'sample_files':len(paths),'sample_bytes':sum(r[1] for r in rows),
        'scope':'Cached JPEG metadata only; no hashing, thumbnails, database writes, or face inference. Production scan left running.',
        'results':results}
(BASE/'data/metadata-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
