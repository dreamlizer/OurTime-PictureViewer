"""Check actual screenshot ink, not just CSS boxes, at common display scales."""
import io
import json
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

OUT=Path(__file__).resolve().parent/'reports/viewer-caption-optical-20260913'
STYLES=['original','classic','gallery','handwritten','tone']

def main():
    OUT.mkdir(exist_ok=True)
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        for scale in [1,1.5,2]:
            page=browser.new_page(viewport={'width':1580,'height':1000},device_scale_factor=scale)
            page.goto('http://127.0.0.1:8765')
            page.locator('#photo-grid [data-photo]').first.click()
            page.wait_for_function('document.querySelector("#detail-img").naturalWidth>0 && !document.querySelector("#detail-dialog").classList.contains("is-loading")')
            for index,style in enumerate(STYLES):
                page.evaluate('i=>{signatureStyleIndex=i;applySignatureStyle();updateZoom(true)}',index)
                page.evaluate('document.fonts.ready')
                page.wait_for_timeout(220)
                geometry=page.evaluate('''()=>{
                  const c=document.querySelector('#photo-signature'),r=c.getBoundingClientRect(),v=c.firstElementChild;
                  const box=n=>{const q=n.getBoundingClientRect();return {left:q.left-r.left,right:q.right-r.left,top:q.top-r.top,bottom:q.bottom-r.top}};
                  return {left:box(c.querySelector('.signature-memory,.caption-left')),right:box(c.querySelector('.signature-file,.caption-right')),height:r.height,natural:parseFloat(getComputedStyle(v).height),paddingTop:parseFloat(getComputedStyle(v).paddingTop),paddingBottom:parseFloat(getComputedStyle(v).paddingBottom)};
                }''')
                data=page.locator('#photo-signature').screenshot()
                (OUT/f'{style}-{scale}.png').write_bytes(data)
                im=Image.open(io.BytesIO(data)).convert('RGB')
                bottoms=[]
                for side in ['left','right']:
                    box=geometry[side]
                    x0=max(0,round(box['left']*scale));x1=min(im.width,round(box['right']*scale))
                    y0=max(0,round((box['bottom']-16)*scale));y1=min(im.height,round((box['bottom']+1)*scale))
                    rows=[]
                    for y in range(y0,y1):
                        ink=sum(1 for x in range(x0,x1) if (min(im.getpixel((x,y)))>155 if style=='tone' else max(im.getpixel((x,y)))<200))
                        if ink>=2:rows.append(y)
                    assert rows,(scale,style,side,'no final-line ink')
                    bottoms.append(max(rows))
                delta=abs(bottoms[0]-bottoms[1])/scale
                # The tone lockup ends in a 27px optical ring while the right side
                # ends in 10px microcopy, so their visible ink has different font
                # metrics even though both layout boxes share the same baseline.
                tolerance=4 if style=='tone' else 1
                assert delta<=tolerance,(scale,style,'ink edges differ',delta)
                assert 0<=geometry['height']-geometry['natural']<1.01
                assert geometry['paddingBottom']==9
                results.append({'scale':scale,'style':style,'ink_bottoms':bottoms,'ink_delta_css_px':delta,**geometry})
                print('PASS',scale,style,'ink delta',round(delta,2),flush=True)
            page.close()
        browser.close()
    (OUT/'optical-validation.json').write_text(json.dumps({'status':'PASS','cases':results},indent=2),encoding='utf-8')

if __name__=='__main__':
    main()
