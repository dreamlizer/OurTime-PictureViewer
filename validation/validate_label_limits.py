"""Read-only real-photo validation of independent label/face/eye constraints."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'validation/reports/label-overlap-20260919'
METRICS='''()=>{
 const image=document.querySelector('#detail-img').getBoundingClientRect();
 const faces=state.detail.faces.map(f=>{const b=faceBox(f);return {id:f.id,fx:image.x+b.x1/b.w*image.width,fy:image.y+b.y1/b.h*image.height,fw:b.width/b.w*image.width,fh:b.height/b.h*image.height};});
 const labels=[...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')].map(e=>{
   const r=e.getBoundingClientRect();return {id:Number(e.dataset.faceId),name:e.textContent,side:e.classList.contains('left')?'left':'right',x:r.x,y:r.y,w:r.width,h:r.height};
 });
 const pairs=[];
 labels.forEach((a,i)=>labels.slice(i+1).forEach(b=>pairs.push({a:a.name,b:b.name,ratio:rectOverlapRatio(a,b)})));
 return {asset:state.detail.id,labels,maxLabelOverlap:Math.max(0,...pairs.map(p=>p.ratio)),
   overlaps:pairs.filter(p=>p.ratio>0),eyeOverlap:labels.reduce((s,l)=>s+faces.reduce((t,f)=>t+rectOverlapArea(l,faceLabelEyeRegion(f)),0),0),
   maxFaceOverlap:Math.max(0,...labels.flatMap(l=>faces.map(f=>rectOverlapArea(l,{x:f.fx,y:f.fy,w:f.fw,h:f.fh})/(f.fw*f.fh))))};
}'''


def main():
    REPORT.mkdir(parents=True,exist_ok=True)
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8765',wait_until='domcontentloaded')
        page.wait_for_function("typeof openPhoto==='function'")
        for width,height in ((1440,1050),(1320,1880)):
            page.set_viewport_size({'width':width,'height':height})
            for asset in (102377,102381,338931,339945,338294):
                page.evaluate('(id)=>openPhoto(id,{filter:"all",sort:"date_desc"})',asset)
                page.wait_for_function('(id)=>state.detail?.id===id && !!document.querySelector("#face-name-layer .face-name")',arg=asset)
                for theme in ('tea','classic'):
                    page.evaluate('''async(theme)=>{updateFaceStyle({...FACE_STYLE_PRESETS[theme]});await document.fonts.ready;await selectFaceLabelPosition('auto');}''',theme)
                    page.wait_for_timeout(200)
                    result=page.evaluate(METRICS)
                    result.update(width=width,height=height,theme=theme)
                    results.append(result)
                    (REPORT/'metrics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
                    page.screenshot(path=str(REPORT/f'{asset}-{width}-{theme}.png'))
                    print(json.dumps({k:v for k,v in result.items() if k not in ('labels','overlaps')},ensure_ascii=False),flush=True)
                    assert result['maxLabelOverlap']<=.05+1e-6,result
                    assert result['eyeOverlap']<1,result
                    assert result['maxFaceOverlap']<=.35+1e-6,result
                    if asset in (102377,102381):
                        assert next(l for l in result['labels'] if l['name']=='王珊珊')['side']=='right',result
                    if asset==339945:
                        assert next(l for l in result['labels'] if l['name']=='许惠暖')['side']=='left',result
        assert not errors,errors
        browser.close()
    print(f'PASS {len(results)} real photo/view/style combinations; no pageerrors')


if __name__=='__main__':main()
