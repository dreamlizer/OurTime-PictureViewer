"""Download the official Windows package linked by exiftool.org and verify its checksum."""
import urllib.request,re,hashlib,zipfile,io,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
version='13.59';name=f'exiftool-{version}_64.zip'
checksums=urllib.request.urlopen(f'https://exiftool.org/checksums-{version}.txt',timeout=30).read().decode()
lines=[line for line in checksums.splitlines() if name in line]
print('Official checksums:',lines,flush=True)
url=f'https://downloads.sourceforge.net/project/exiftool/{name}'
payload=urllib.request.urlopen(url,timeout=60).read()
sha=hashlib.sha256(payload).hexdigest()
matched=any(sha in line.lower() for line in lines)
if not matched:
    md5=hashlib.md5(payload).hexdigest();sha1=hashlib.sha1(payload).hexdigest()
    if not any(md5 in line.lower() or sha1 in line.lower() for line in lines):
        raise RuntimeError(f'Official checksum did not match: SHA256 {sha}')
target=root/'tools'/'exiftool';target.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    for member in z.infolist():
        path=(target/member.filename).resolve()
        if not path.is_relative_to(target.resolve()): raise RuntimeError('Unsafe archive path')
    z.extractall(target)
exe=next(target.rglob('exiftool(-k).exe'))
exe.rename(exe.with_name('exiftool.exe'))
(target/'source.json').write_text(json.dumps({'version':version,'url':url,'official_checksum_url':f'https://exiftool.org/checksums-{version}.txt','sha256':sha,'verified':True},indent=2),encoding='utf-8')
print('Installed',exe.parent,flush=True)
