"""Camera-source fallback and single brand label across all five captions."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from validate_caption_layout import check

OUT=Path(__file__).resolve().parent/'reports/viewer-caption-camera-20260913'
STYLES=['original','classic','gallery','handwritten','tone']
CASES=[
    ('exiftool',{'metadata':{'ExifTool':{'IFD0:Make':'Sony','IFD0:Model':'ILCE-7M4'}}},'Sony ILCE-7M4'),
    ('ifd0',{'metadata':{'IFD0':{'Make':'Canon','Model':'Canon EOS R6'}}},'Canon EOS R6'),
    ('numeric-ifd0',{'metadata':{'IFD0':{'271':'Nikon','272':'Z 6'}}},'Nikon Z 6'),
    ('empty-first-tag',{'metadata':{'ExifTool':{'XMP:Model':'','IFD0:Model':'MEIZU 21 Pro'}}},'MEIZU 21 Pro'),
    ('stored-camera',{'camera':'HUAWEI P30'},'HUAWEI P30'),
    ('make-only',{'metadata':{'IFD0':{'Make':'FUJIFILM'}}},'FUJIFILM'),
    ('no-camera',{},None),
]

def inspect(page,name,style,expected):
    result=page.evaluate('''()=>{
      const c=document.querySelector('#photo-signature');
      return {text:c.innerText,left:c.querySelector('.caption-left')?.innerText||'',camera:c.querySelector('.signature-camera,.caption-camera')?.textContent||''};
    }''')
    assert result['text'].count('拾光相册')<=1,(name,style,'duplicate brand',result)
    if expected:
        assert result['camera']==expected,(name,style,'camera omitted',result)
        if style!='original':assert expected in result['left'],(name,style,'camera must be on left')
    elif style!='original':
        assert '拾光相册' in result['left'],(name,style,'missing fallback')
    return {'name':name,'style':style,'expected_camera':expected,**result}

def main():
    OUT.mkdir(exist_ok=True)
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8765')
        page.locator('#photo-grid [data-photo]').first.click()
        page.wait_for_function('document.querySelector("#detail-img").naturalWidth>0 && !document.querySelector("#detail-dialog").classList.contains("is-loading")')
        for name,photo,expected in CASES:
            for index,style in enumerate(STYLES):
                page.evaluate('''({photo,index})=>{
                  signatureStyleIndex=index;
                  renderSignature({effective_date:'2022-07-02T06:14:10',effective_source:'文件修改时间参考',width:1600,height:903,format:'JPEG',...photo},{size:275074});updateZoom(true);
                }''',{'photo':photo,'index':index})
                results.append(inspect(page,name,style,expected))
        # Actual matching library photos, through the real detail renderer.
        for aid in [110591,110604]:
            page.evaluate('async id=>{await displayPhoto(id)}',aid)
            page.wait_for_function('!document.querySelector("#detail-dialog").classList.contains("is-loading")')
            for index,style in enumerate(STYLES):
                page.evaluate('i=>{signatureStyleIndex=i;applySignatureStyle();updateZoom(true)}',index)
                page.evaluate('document.fonts.ready')
                check(page,f'camera-absent-{aid}-{style}')
                results.append(inspect(page,str(aid),style,None))
                if style in ['classic','gallery']:
                    page.locator('#photo-signature').screenshot(path=str(OUT/f'{aid}-{style}.png'))
        assert not errors,errors
        browser.close()
    (OUT/'camera-validation.json').write_text(json.dumps({'status':'PASS','cases':results,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS',len(results),'camera / fallback cases')

if __name__=='__main__':main()
