"""Read-only live-photo checks for label proximity; never drag or write the library."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=['before','after'],required=True)
    args=parser.parse_args()
    report=ROOT/'validation/reports/label-attachment-20260919'
    report.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1050})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8765',wait_until='domcontentloaded')
        page.wait_for_function("typeof openPhoto==='function'")
        results={}
        for asset in (338931,339945):
            page.evaluate('(id)=>openPhoto(id,{filter:"all",sort:"date_desc"})',asset)
            page.wait_for_function('(id)=>state.detail?.id===id && !!document.querySelector("#face-name-layer .face-name")',arg=asset)
            page.evaluate("async()=>{updateFaceStyle({...FACE_STYLE_PRESETS.tea});await document.fonts.ready;await selectFaceLabelPosition('auto');}")
            page.wait_for_timeout(350)
            results[str(asset)]=page.evaluate('''()=>{
              const image=document.querySelector('#detail-img').getBoundingClientRect();
              const items=state.detail.faces.map(f=>{const b=faceBox(f);return {id:f.id,name:f.name,fx:image.x+b.x1/b.w*image.width,fy:image.y+b.y1/b.h*image.height,fw:b.width/b.w*image.width,fh:b.height/b.h*image.height};});
              return [...document.querySelectorAll('#face-name-layer .face-name')].map(e=>{
                const r=e.getBoundingClientRect(),rect={x:r.x,y:r.y,w:r.width,h:r.height};
                const own=items.find(f=>f.id===Number(e.dataset.faceId));
                const others=items.filter(f=>f!==own);
                const overlap=f=>rectOverlapArea(rect,{x:f.fx,y:f.fy,w:f.fw,h:f.fh});
                return {id:own.id,name:own.name,side:e.classList.contains('left')?'left':'right',x:r.x,y:r.y,
                  ownDistance:faceLabelDistance(rect,own),otherDistance:Math.min(...others.map(f=>faceLabelDistance(rect,f))),
                  ownOverlap:overlap(own),otherOverlap:others.reduce((s,f)=>s+overlap(f),0)};
              });
            }''')
            page.screenshot(path=str(report/f'{args.phase}-{asset}.png'))
        assert not errors,errors
        if args.phase=='after':
            before=json.loads((report/'before.json').read_text(encoding='utf-8'))
            for fid in (253069,253070):
                old=next(x for x in before['338931'] if x['id']==fid)
                new=next(x for x in results['338931'] if x['id']==fid)
                assert new['side']=='left',new
                assert new['ownDistance']<old['ownDistance'],(old,new)
                assert new['ownDistance']<=new['otherDistance']+1,new
                assert new['otherOverlap']<=old['otherOverlap']+1,(old,new)
            assert next(x for x in results['339945'] if x['name']=='许惠暖')['side']=='left'
        (report/f'{args.phase}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
        print(json.dumps(results,ensure_ascii=False))


if __name__=='__main__':main()
