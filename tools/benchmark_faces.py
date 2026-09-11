"""Read-only timing of the current face inference settings, without library writes."""
import os,sys,time,json,sqlite3,statistics,contextlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
os.environ['NO_ALBUMENTATIONS_UPDATE']='1'
from PIL import Image,ImageOps
from pillow_heif import register_heif_opener
register_heif_opener()
import numpy as np
import onnxruntime as ort
from insightface.app import FaceAnalysis

with sqlite3.connect('file:'+str(ROOT/'data/library.sqlite3')+'?mode=ro',uri=True) as c:
    rows=c.execute('SELECT a.id,(SELECT path FROM files WHERE asset_id=a.id AND excluded=0 AND exists_now=1 ORDER BY id LIMIT 1) path FROM assets a WHERE a.width>=800 AND a.height>=600 AND a.error IS NULL AND a.excluded=0 ORDER BY a.id').fetchall()
samples=[rows[round(i*(len(rows)-1)/15)] for i in range(16)]
options=ort.SessionOptions();options.intra_op_num_threads=2
start=time.perf_counter()
with (ROOT/'data/face-benchmark-model.log').open('w',encoding='utf-8') as log,contextlib.redirect_stdout(log):
    engine=FaceAnalysis(name='buffalo_l',root='G:/CodexModels/insightface',allowed_modules=['detection','recognition'],providers=['CPUExecutionProvider'],sess_options=options)
    engine.prepare(ctx_id=-1,det_size=(640,640))
load_seconds=time.perf_counter()-start
results=[]
for run in range(2):
    for aid,path in samples:
        if not path:continue
        start=time.perf_counter()
        try:
            with Image.open(path) as source:
                pic=ImageOps.exif_transpose(source).convert('RGB');pic.thumbnail((2400,2400))
                pixels=np.asarray(pic)[:,:,::-1].copy()
            prepared=time.perf_counter()
            faces=engine.get(pixels)
            ended=time.perf_counter()
            accepted=sum(1 for f in faces if f.bbox[2]-f.bbox[0]>=28 and f.bbox[3]-f.bbox[1]>=28 and float(f.det_score)>=.65)
            row={'pass':run+1,'asset_id':aid,'faces':accepted,'detected_faces':len(faces),'read_resize_seconds':round(prepared-start,4),'inference_seconds':round(ended-prepared,4),'total_seconds':round(ended-start,4)}
            results.append(row)
            print(json.dumps(row),flush=True)
        except Exception as error:
            results.append({'pass':run+1,'asset_id':aid,'error':str(error)})

def summary(items):
    values=[r['total_seconds'] for r in items if 'total_seconds' in r]
    if not values:return None
    return {'n':len(values),'mean_seconds':round(statistics.mean(values),3),'median_seconds':round(statistics.median(values),3),'min_seconds':min(values),'max_seconds':max(values),'photos_per_hour':round(3600/statistics.mean(values))}
report={'measured_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'model':'buffalo_l','provider':'CPUExecutionProvider','available_providers':ort.get_available_providers(),'onnx_threads':2,'model_load_seconds':round(load_seconds,3),'sample_count':len(samples),'first_pass':summary([r for r in results if r['pass']==1]),'second_pass':summary([r for r in results if r['pass']==2]),'groups':{name:summary([r for r in results if r['pass']==2 and predicate(r.get('faces',-1))]) for name,predicate in [('no_faces',lambda n:n==0),('one_face',lambda n:n==1),('two_or_more',lambda n:n>=2)]},'scope':'Read + resize + face detection + embedding; no library grouping or persistence. Full metadata scan remains active.','results':results}
(ROOT/'data/face-speed-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False),flush=True)
