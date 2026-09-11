import json,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def status():
    with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=15) as r:return json.load(r)
deployment=json.loads((ROOT/'data/upgrade-deployment.json').read_text(encoding='utf-8-sig'))
deadline=time.monotonic()+600
while True:
    s=status()
    if s['job']['status']!='running':raise RuntimeError('Scan stopped before speed measurement')
    if s['stats']['files']>=deployment['BeforeFiles']+150:break
    if time.monotonic()>deadline:raise TimeoutError('Still checking previously indexed files')
    time.sleep(2)
start=s;started=time.monotonic()
print(json.dumps({'phase':'measuring_new_files','files':s['stats']['files'],'metadata_reads':s['job']['metadata_reads']},ensure_ascii=False),flush=True)
samples=[]
for i in range(6):
    time.sleep(10)
    s=status()
    samples.append({'seconds':round(time.monotonic()-started,2),'files':s['stats']['files'],'assets':s['stats']['assets'],'metadata_reads':s['job']['metadata_reads'],'errors':s['job']['errors']})
elapsed=time.monotonic()-started
result={'measured_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'seconds':round(elapsed,2),'workers':s['job']['workers'],
        'new_file_paths':s['stats']['files']-start['stats']['files'],'new_assets':s['stats']['assets']-start['stats']['assets'],
        'metadata_reads':s['job']['metadata_reads']-start['job']['metadata_reads'],'new_errors':s['job']['errors']-start['job']['errors'],
        'file_paths_per_second':round((s['stats']['files']-start['stats']['files'])/elapsed,2),
        'metadata_per_second':round((s['job']['metadata_reads']-start['job']['metadata_reads'])/elapsed,2),
        'samples':samples,'before_baseline_files_per_second':2.8,
        'note':'正式六盘扫描通过已处理区域后，对真实新增照片测量；此前基线来自较早照片，非严格同样本基准。'}
(ROOT/'data/live-speed-after-upgrade.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False),flush=True)
