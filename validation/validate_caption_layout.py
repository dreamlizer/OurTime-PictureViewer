"""Read-only browser coverage for caption bounds, wrapping and photo fit."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'validation/reports/viewer-caption-20260913'
MEASURE = """() => {
 const rect = n => {const r=n.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};
 const caption=document.querySelector('#photo-signature'), img=document.querySelector('#detail-img');
 const nodes=[...caption.querySelectorAll('.signature-seal-v3,.signature-memory,.signature-capture,.signature-file,.caption-panel')].filter(n=>n.getBoundingClientRect().width);
 return {caption:rect(caption),image:rect(img),mat:rect(document.querySelector('#photo-mat')),viewport:rect(document.querySelector('#image-viewport')),
 mode:document.querySelector('#detail-dialog').dataset.signatureMode,style:caption.dataset.style||'original',
 face:rect(document.querySelector('#face-name-layer')),
 seal:caption.querySelector('.signature-seal-v3')?{background:getComputedStyle(caption.querySelector('.signature-seal-v3')).backgroundColor,radius:getComputedStyle(caption.querySelector('.signature-seal-v3')).borderRadius}:null,
 groups:nodes.map(n=>({kind:n.className,...rect(n)})),tokens:[...caption.querySelectorAll('.signature-exposure-token,.signature-file-token')].map(n=>({text:n.textContent,...rect(n)})),
 arrows:[...document.querySelectorAll('.viewer-arrow')].map(rect),tools:rect(document.querySelector('.viewer-tools')),content:rect(caption.firstElementChild),text:caption.innerText};
}"""

def check(page, name, fit=True):
    # The viewer's opening/photo-change transition lasts 170 ms. Measure only
    # after it settles; otherwise transformed rectangles can look outside the
    # viewport even though the final fitted layout is correct.
    page.wait_for_timeout(220)
    s=page.evaluate(MEASURE)
    c=s['caption']
    assert 0<=c['height']-s['content']['height']<1.01,(name,'fixed minimum height must not add empty space below caption',s)
    panels=[g for g in s['groups'] if 'caption-panel' in g['kind']]
    if len(panels)==2:
        assert abs(panels[0]['bottom']-panels[1]['bottom'])<=1,(name,'both caption panels must align at the bottom',s)
    if s['style']=='original':
        assert s['seal']=={'background':'rgb(185, 46, 49)','radius':'50%'},(name,'original logo changed',s)
    memory=next((g for g in s['groups'] if g['kind']=='signature-memory'),None)
    right=[g for g in s['groups'] if g['kind'] in ['signature-capture','signature-file']]
    if memory and right:
        assert abs(memory['bottom']-max(g['bottom'] for g in right))<=1,(name,'caption groups must align at the bottom',s)
        if name.startswith('real-') and s['style']=='original':
            assert abs(memory['height']-(max(g['bottom'] for g in right)-min(g['top'] for g in right)))<=4,(name,'right block visually taller than left',s)
    for group in s['groups']:
        if memory and group['kind'] in ['signature-capture','signature-file']:
            if s['style']=='handwritten' and any(g['kind']=='signature-capture' for g in s['groups']):
                assert group['right']<=memory['left']+1,(name,'handwritten details must stay to the left of memory',s)
            else:
                assert group['left']>=memory['right']-1,(name,'details must stay to the right of memory',s)
    for r in s['groups']+s['tokens']+[s['content']]:
        assert r['left']>=c['left']-1 and r['right']<=c['right']+1, (name,'horizontal clipping',s)
        assert r['top']>=c['top']-1 and r['bottom']<=c['bottom']+1, (name,'vertical clipping',s)
    for i,a in enumerate(s['groups']):
        for b in s['groups'][i+1:]:
            assert min(a['right'],b['right'])-max(a['left'],b['left'])<=1 or min(a['bottom'],b['bottom'])-max(a['top'],b['top'])<=1, (name,'groups overlap',s)
    assert c['top']>=s['image']['bottom']-1,(name,'caption overlays photograph',s)
    for edge in ['left','top','right','bottom']:
        assert abs(s['face'][edge]-s['image'][edge])<=1,(name,'face layer must follow photo inside its frame',s)
    if fit:
        assert s['mat']['top']>=s['viewport']['top']-1 and s['mat']['bottom']<=s['viewport']['bottom']+1,(name,'fit outside viewport',s)
        assert c['right']<=s['tools']['left']+1,(name,'toolbar covers caption',s)
        for arrow in s['arrows']:
            assert arrow['bottom']<=c['top']+1 or arrow['left']>=c['right'] or arrow['right']<=c['left'],(name,'navigation covers caption',s)
    assert 'undefined' not in s['text']
    print('PASS',name, s['mode'],round(c['width']),round(c['height']),flush=True)
    return {'name':name,**s}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1580,'height':1000})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8765')
        page.wait_for_selector('#photo-grid [data-photo]')
        page.locator('#photo-grid [data-photo]').first.click()
        page.wait_for_function('document.querySelector("#detail-img").naturalWidth>0 && !document.querySelector("#detail-dialog").classList.contains("is-loading")')
        results.append(check(page,'real-portrait'))
        page.screenshot(path=str(OUT/'after-portrait.png'))
        page.locator('#photo-signature').screenshot(path=str(OUT/'after-real-caption.png'))
        # Exercise real sequential navigation, then drawer and fit controls.
        for i in range(3):
            old=page.locator('#viewer-position').inner_text()
            page.locator('#viewer-next').click()
            page.wait_for_function('old=>document.querySelector("#viewer-position").textContent!==old',arg=old)
            page.wait_for_function('!document.querySelector("#detail-dialog").classList.contains("is-loading")')
            results.append(check(page,f'real-next-{i}'))
        page.locator('#viewer-info').click()
        results.append(check(page,'drawer-open'))
        page.locator('#close-info').click()
        page.locator('#zoom-in').click()
        results.append(check(page,'zoom-in',fit=False))
        page.locator('#zoom-fit').click()
        results.append(check(page,'fit-restored'))
        # In-memory UI fixture only: no DB writes, originals or scanning changes.
        for width,height in [(1580,1000),(1200,900),(700,900),(390,844),(320,700),(900,390)]:
            page.set_viewport_size({'width':width,'height':height})
            for shape in ['landscape','portrait','panorama']:
                page.evaluate("""async shape=>{
                  const img=document.querySelector('#detail-img');
                  const w=shape==='portrait'?900:1600,h=shape==='portrait'?1200:shape==='panorama'?120:1000;
                  const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
                  const ctx=canvas.getContext('2d');ctx.fillStyle='#adb6ae';ctx.fillRect(0,0,w,h);
                  img.src=canvas.toDataURL();await img.decode();
                  renderSignature({effective_date:'2019-10-20T19:21',effective_place:'中国 · 北京市海淀区一个非常长的地点名称测试',camera:'Sony ILCE-7RM5 / FE 24-70mm F2.8 GM II',format:'JPEG',width:w,height:h,metadata:{ExifTool:{FocalLength:129,FNumber:1.9,ExposureTime:0.000125,ISO:12800}}},{path:'layout-fixture.jpg',size:3900000});
                  viewer.fit=true;updateZoom(true);
                }""",shape)
                results.append(check(page,f'long-{shape}-{width}x{height}'))
                if width in [1580,390]:
                    page.locator('#photo-signature').screenshot(path=str(OUT/f'caption-{shape}-{width}.png'))
        # All missing-field combinations, plus an approximate human date.
        for case in range(24):
            mask=case%8
            width=[390,900,1500][case//8]
            page.set_viewport_size({'width':width,'height':1000})
            page.evaluate("""async()=>{
                const c=document.createElement('canvas');c.width=1600;c.height=1000;
                const img=document.querySelector('#detail-img');img.src=c.toDataURL();await img.decode();
            }""")
            page.evaluate("""mask=>{
              renderSignature({effective_date:mask&1?'大约 2012 年夏季，某个周末的下午':'',effective_source:'人工确认',camera:mask&2?'Camera model':'',metadata:mask&2?{ExifTool:{FocalLength:24,FNumber:2.8,ExposureTime:0.01,ISO:100}}:{},width:mask&4?1600:0,height:mask&4?1000:0,format:mask&4?'PNG':''},mask&4?{path:'fixture.png',size:2000000}:{});
              updateZoom(true);
            }""",mask)
            results.append(check(page,f'missing-fields-{width}-{mask}'))
        assert not errors,errors
        browser.close()
    (OUT/'layout-validation.json').write_text(json.dumps({'status':'PASS','cases':results,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    main()
