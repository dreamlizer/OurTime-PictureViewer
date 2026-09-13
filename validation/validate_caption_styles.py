"""Real viewer cycles, tooltip, palette and five-style geometry. No library writes."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from validate_caption_layout import check

OUT=Path(__file__).resolve().parent/'reports/viewer-caption-redesign-20260913'
STYLES=['original','classic','gallery','handwritten','tone']

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    results=[]
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1580,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8765')
        page.locator('#photo-grid [data-photo]').first.click()
        page.wait_for_function('document.querySelector("#detail-img").naturalWidth>0 && !document.querySelector("#detail-dialog").classList.contains("is-loading")')
        button=page.locator('.signature-switch')
        tip=page.locator('#signature-switch-tip')
        for style in STYLES:
            assert page.locator('#photo-signature').get_attribute('data-style')==style
            page.evaluate('document.fonts.ready')
            results.append(check(page,'real-'+style))
            button.hover()
            assert tip.is_visible() and '点击切换' in tip.inner_text()
            page.mouse.move(5,5)
            assert not tip.is_visible()
            page.locator('#photo-signature').screenshot(path=str(OUT/f'{style}.png'))
            page.locator('#photo-mat').screenshot(path=str(OUT/f'photo-{style}.png'))
            button.click()
            page.mouse.move(5,5)
            assert not tip.is_visible(),'Mouse click focus must not leave tooltip stuck open'
        assert page.locator('#photo-signature').get_attribute('data-style')=='original'
        # Native button keyboard behavior, followed by real photo navigation.
        button.focus()
        page.keyboard.press('Enter')
        assert page.locator('#photo-signature').get_attribute('data-style')=='classic'
        old=page.locator('#viewer-position').inner_text()
        page.locator('#viewer-next').click()
        page.wait_for_function('old=>document.querySelector("#viewer-position").textContent!==old',arg=old)
        page.wait_for_function('!document.querySelector("#detail-dialog").classList.contains("is-loading")')
        assert page.locator('#photo-signature').get_attribute('data-style')=='classic'
        results.append(check(page,'real-next-keeps-choice'))
        page.locator('#viewer-info').click()
        results.append(check(page,'drawer'))
        page.locator('#close-info').click()
        # Same numerical tokens, including extremes, must survive every layout.
        for width,height in [(1580,1000),(390,844),(320,700),(900,390)]:
            page.set_viewport_size({'width':width,'height':height})
            for shape in ['landscape','portrait','panorama']:
                page.evaluate('''async shape=>{
                  const c=document.createElement('canvas');c.width=shape==='portrait'?900:1600;c.height=shape==='portrait'?1200:shape==='panorama'?120:1000;
                  const ctx=c.getContext('2d');ctx.fillStyle='#b59c78';ctx.fillRect(0,0,c.width,c.height);
                  const img=document.querySelector('#detail-img');img.src=c.toDataURL();await img.decode();
                  renderSignature({effective_date:'2025-05-26T13:54',effective_place:'中国 · 北京市海淀区一个非常长的地点名称测试',camera:'Sony ILCE-7RM5 / FE 24-70mm F2.8 GM II',format:'JPEG',width:c.width,height:c.height,metadata:{ExifTool:{FocalLength:129,FNumber:1.9,ExposureTime:.000125,ISO:12800}}},{path:'fixture.jpg',size:3900000});viewer.fit=true;
                }''',shape)
                for index,style in enumerate(STYLES):
                    page.evaluate('i=>{signatureStyleIndex=i;applySignatureStyle();updateZoom(true)}',index)
                    page.evaluate('document.fonts.ready')
                    result=check(page,f'{style}-{shape}-{width}x{height}')
                    assert len(result['tokens'])==7
                    results.append(result)
                    bounds=page.evaluate('''()=>{
                      const b=document.querySelector('.signature-switch').getBoundingClientRect();
                      return [...document.querySelectorAll('.signature-seal-v3,.signature-memory,.signature-capture,.signature-file,.caption-panel')].filter(n=>!n.hidden).map(n=>{const r=n.getBoundingClientRect();return Math.max(0,Math.min(b.right,r.right)-Math.max(b.left,r.left))*Math.max(0,Math.min(b.bottom,r.bottom)-Math.max(b.top,r.top))})
                    }''')
                    assert all(v==0 for v in bounds),('button overlaps caption content',style,width,bounds)
                    if width==390 and shape=='portrait':
                        page.mouse.move(5,5)
                        page.screenshot(path=str(OUT/f'narrow-{style}.png'))
        for mask in range(8):
            page.set_viewport_size({'width':320,'height':844})
            for index,style in enumerate(STYLES):
                page.evaluate('''({mask,index})=>{
                  signatureStyleIndex=index;
                  renderSignature({effective_date:mask&1?'大约 2012 年夏季，某个周末的下午':'',effective_source:'人工确认',camera:mask&2?'Camera model':'',width:mask&4?1600:0,height:mask&4?1000:0,format:mask&4?'PNG':''},mask&4?{path:'fixture.png',size:2000000}:{});updateZoom(true);
                }''',{'mask':mask,'index':index})
                results.append(check(page,f'{style}-missing-{mask}'))
                assert page.evaluate('''()=>{
                  const b=document.querySelector('.signature-switch').getBoundingClientRect();
                  return [...document.querySelectorAll('.signature-seal-v3,.caption-brand,.caption-handmark')].every(n=>{
                    const r=n.getBoundingClientRect();return b.right<=r.left||b.bottom<=r.top||b.left>=r.right;
                  });
                }'''),('sparse caption button overlaps logo',style,mask)
        # Color changes follow the image and retain readable contrast.
        palettes=[]
        for color in ['#ff3300','#0055ff','#ffffff','#000000']:
            palette=page.evaluate('''async color=>{
              const c=document.createElement('canvas');c.width=c.height=24;const ctx=c.getContext('2d');ctx.fillStyle=color;ctx.fillRect(0,0,24,24);
              const img=document.querySelector('#detail-img');img.src=c.toDataURL();await img.decode();signatureStyleIndex=4;applySignatureStyle();
              return getComputedStyle(document.querySelector('#photo-signature')).backgroundColor;
            }''',color)
            channels=[int(v) for v in palette.removeprefix('rgb(').removesuffix(')').split(',')]
            assert max(channels)<=100
            palettes.append(palette)
        assert len(set(palettes))==4
        page.reload()
        page.locator('#photo-grid [data-photo]').first.click()
        page.wait_for_function('!document.querySelector("#photo-signature").hidden')
        assert page.locator('#photo-signature').get_attribute('data-style')=='original'
        assert not errors,errors
        labels=['01 · 原版（默认）','02 · 双端参数','03 · 作品底签','04 · 手写题签','05 · 随照片取色']
        html='<!doctype html><meta charset="utf-8"><title>拾光 · 五款照片底签</title><style>body{margin:0;padding:28px 32px;background:#e8e9e3;color:#343d35;font-family:"Segoe UI","Microsoft YaHei",sans-serif}h1{font-size:20px;font-weight:500;margin:0 0 6px}p{font-size:12px;color:#727b72;margin:0 0 24px}section{margin:18px 0}h2{font-size:12px;font-weight:400;color:#606b61;margin:0 0 8px}img{display:block;width:auto;max-width:100%;box-shadow:0 2px 8px #25332309}</style><h1>拾光 · 五款照片底签</h1><p>原版保留 · 四款独立标识与构图 · 以下为真实页面截图</p>'
        html+=''.join(f'<section><h2>{label}</h2><img src="{style}.png"></section>' for style,label in zip(STYLES,labels))
        (OUT/'index.html').write_text(html,encoding='utf-8')
        board=browser.new_page(viewport={'width':754,'height':830},device_scale_factor=1)
        board.goto((OUT/'index.html').as_uri())
        board.screenshot(path=str(OUT/'five-styles.png'),full_page=True)
        browser.close()
    (OUT/'validation.json').write_text(json.dumps({'status':'PASS','cases':results,'palettes':palettes,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    main()
