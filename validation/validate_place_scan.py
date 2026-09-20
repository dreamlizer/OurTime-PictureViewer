"""Isolated HTTP scan regression for GPS namesake disambiguation."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='place-scan-',dir=ROOT/'validation/work') as work:
        work=Path(work)
        photos=work/'photos'
        photos.mkdir()
        image=photos/'gps.jpg'
        exif=Image.Exif()
        exif[34853]={1:'N',2:(39,59,34.69),3:'E',4:(116,25,27.14)}
        Image.new('RGB',(640,480),'#b0c0a0').save(image,exif=exif)
        digest=hashlib.sha256(image.read_bytes()).hexdigest()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        def req(path,body=None):
            request=urllib.request.Request(url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)
        with (work/'server.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--port',str(port)],cwd=ROOT,
                env={**os.environ,'PHOTO_LIBRARY_DATA':str(work/'data'),'PHOTO_WEB_ROOT':str(ROOT/'web')},
                stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for _ in range(100):
                    try:
                        status=req('/api/health')
                        assert Path(status['data_dir']).resolve()==(work/'data').resolve()
                        break
                    except OSError:time.sleep(.1)
                else:raise AssertionError('Isolated server did not start')
                req('/api/scan',{'roots':[str(photos)],'with_faces':False})
                for _ in range(180):
                    status=req('/api/status')
                    if status['job']['status'] not in ('running','pausing'):break
                    time.sleep(.5)
                assert status['job']['status']=='completed',status['job']
                items=req('/api/photos?limit=10')['items']
                assert len(items)==1
                detail=req('/api/photos/'+str(items[0]['id']))
                assert detail['effective_place']=='北京市 · 朝阳区 · 小营',detail['effective_place']
                assert detail['place_source'].startswith('GeoNames'),detail['place_source']
                assert detail['manual_place'] is None
                assert abs(detail['latitude']-39.9929694444)<1e-7
                assert digest==hashlib.sha256(image.read_bytes()).hexdigest()
                result={k:detail[k] for k in ('effective_place','place_source','latitude','longitude','manual_place')}
                result.update(passed=True,original_unchanged=True)
                report=ROOT/'validation/reports/place-conflicts-20260919/scan.json'
                report.parent.mkdir(parents=True,exist_ok=True)
                report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(result,ensure_ascii=False))
            finally:
                if process.poll() is None:
                    req('/api/shutdown',{})
                    process.wait(timeout=30)


if __name__=='__main__':main()
