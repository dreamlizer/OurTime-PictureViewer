"""Read-only real hover/copy checks, then synthetic long paths and GPS boundaries."""
import json
from pathlib import Path, PureWindowsPath
from playwright.sync_api import sync_playwright

OUT=Path(__file__).resolve().parent/'reports/signature-info-compact-20260913'

def bounds(page):
    box=page.locator('#signature-info-popover').bounding_box()
    assert box and box['x']>=0 and box['y']>=0,box
    viewport=page.viewport_size
    assert box['x']+box['width']<=viewport['width'] and box['y']+box['height']<=viewport['height'],box
    assert page.evaluate('''()=>{
      const p=document.querySelector('#signature-info-popover'),b=p.querySelector('.signature-info-copy').getBoundingClientRect(),r=p.getBoundingClientRect();
      return b.top>=r.top&&b.bottom<=r.bottom&&b.left-r.left<30&&p.scrollWidth<=p.clientWidth;
    }''')

def main():
    OUT.mkdir(exist_ok=True)
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000},permissions=['clipboard-read','clipboard-write'])
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8765')
        page.locator('#photo-grid [data-photo]').first.click()
        page.wait_for_function('document.querySelector("#detail-img").naturalWidth>0 && !document.querySelector("#detail-dialog").classList.contains("is-loading")')
        card=page.locator('#signature-info-popover')
        caption=page.locator('#photo-signature')
        assert caption.get_attribute('title') is None
        page.locator('.signature-date').hover()
        card.wait_for(state='visible')
        page.locator('#signature-info-heading').hover()
        page.wait_for_timeout(600)
        assert card.is_visible()
        page.locator('#signature-info-heading').click()
        assert page.locator('#detail-dialog').evaluate('(n)=>n.open')
        bounds(page)
        page.locator('.signature-info-row dd').first.dblclick()
        assert page.evaluate('window.getSelection().toString().length>0')
        checks.append('real hover transfer and click keep card/viewer open')
        # Exercise the real Clipboard API and restore the prior clipboard without
        # logging or persisting its contents or any real photo paths.
        prior=page.evaluate('navigator.clipboard.readText()')
        try:
            page.locator('.signature-info-copy').click()
            page.wait_for_function('document.querySelector(".signature-info-status").textContent==="已复制"')
            copied=page.evaluate('navigator.clipboard.readText()')
            expected=page.evaluate('signatureInfo.text')
            assert copied.replace('\r\n','\n')==expected.replace('\r\n','\n')
            assert '\n\n' in copied.replace('\r\n','\n') and '文件夹：' in copied
            actual_path=page.evaluate('state.detail.files[0].path')
            assert str(PureWindowsPath(actual_path).parent) in copied
            assert '文件名：'+PureWindowsPath(actual_path).name in copied
            assert all(label not in copied for label in ['文件夹名称：','文件格式：','完整路径：','未记录'])
        finally:
            page.evaluate('text=>navigator.clipboard.writeText(text)',prior)
        checks.append('real clipboard contents equal every displayed section with line breaks')
        page.mouse.move(2,2);page.wait_for_timeout(600)
        assert not card.is_visible()
        page.locator('.signature-date').hover();card.wait_for(state='visible')
        page.keyboard.press('Escape')
        assert not card.is_visible() and page.locator('#detail-dialog').evaluate('(n)=>n.open')
        # Move away then return: closing must not leave the opening timer stuck.
        page.mouse.move(2,2);page.locator('.signature-date').hover();card.wait_for(state='visible')
        previous_id=page.evaluate('state.detail.id')
        page.locator('#viewer-next').click()
        page.wait_for_function('id=>state.detail.id!==id && !document.querySelector("#detail-dialog").classList.contains("is-loading")',arg=previous_id)
        assert not card.is_visible()
        checks.append('leave/reopen/Escape/photo change lifecycle')
        # Synthetic metadata is only rendered in browser memory; nothing is saved.
        page.evaluate(r'''()=>{
          const path='X:\\演示照片\\旅行与家人\\非常长的文件夹名称用于检查换行\\海边日落.jpg';
          renderSignature({effective_date:'2025-05-24T10:55:25',effective_source:'EXIF 原始拍摄时间',effective_place:'中国 · 海边',camera:'MEIZU 21 Pro',latitude:39.123456,longitude:116.654321,width:4080,height:3060,format:'JPEG',metadata:{ExifTool:{GPSAltitude:35.8,FocalLength:48,FNumber:1.9,ExposureTime:.01,ISO:718}},files:[{path},{path:'Y:\\备份照片\\海边日落.jpg'}]},{path,size:2411724});updateZoom(true);showSignatureInfo();
        }''')
        text=page.evaluate('signatureInfo.text')
        assert '39.123456°' in text and '116.654321°' in text and '35.8 m' in text
        assert '文件名：海边日落.jpg' in text and '其他位置：Y:\\备份照片\\海边日落.jpg' in text
        assert r'文件夹：X:\演示照片\旅行与家人\非常长的文件夹名称用于检查换行' in text
        assert all(label not in text for label in ['文件夹名称：','文件格式：','完整路径：','未记录'])
        (OUT/'copy-example.txt').write_bytes(text.encode('utf-8-sig'))
        for width,height in [(1440,1000),(390,844),(900,390)]:
            page.set_viewport_size({'width':width,'height':height})
            page.wait_for_timeout(300)
            page.evaluate('showSignatureInfo()')
            page.locator('#signature-info-heading').hover()
            page.evaluate('window.getSelection().removeAllRanges()')
            page.wait_for_timeout(250)
            bounds(page)
            if width==1440:
                assert card.bounding_box()['height']<365
            assert page.evaluate('''()=>{
              const p=document.querySelector('#signature-info-popover'),f=p.querySelector('footer'),b=p.querySelector('.signature-info-body');
              return getComputedStyle(f).marginTop==='0px'&&Math.abs(f.getBoundingClientRect().top-b.getBoundingClientRect().bottom)<1;
            }''')
            gps=card.locator('.signature-info-gps dd')
            assert gps.evaluate('(n)=>n.getBoundingClientRect().height<20'), 'GPS should fit in one line'
            card.screenshot(path=str(OUT/f'info-{width}.png'))
            page.locator('.signature-info-body').evaluate('(n)=>n.scrollTop=n.scrollHeight')
            bounds(page)
            card.screenshot(path=str(OUT/f'files-{width}.png'))
            page.locator('.signature-info-body').evaluate('(n)=>n.scrollTop=0')
        checks.append('full GPS/path/copies and desktop/narrow/short viewport bounds')
        page.set_viewport_size({'width':1440,'height':1000})
        page.wait_for_timeout(300)
        page.evaluate(r'''()=>{
          renderSignature({effective_date:'2025-05-26T13:56:44',camera:'MEIZU 21 Pro',latitude:39.912186,longitude:116.233672,width:3060,height:4080,metadata:{ExifTool:{FocalLength:129,FNumber:1.9,ExposureTime:1/157,ISO:151}}},{path:'X:\\演示照片\\海边日落.jpg',size:3879731});updateZoom();showSignatureInfo();
        }''')
        page.locator('#signature-info-heading').hover()
        bounds(page)
        fitted=page.evaluate('''()=>{
          const p=document.querySelector('#signature-info-popover'),box=p.getBoundingClientRect();
          const right=Math.max(...Array.from(p.querySelectorAll('dd'),e=>{const r=document.createRange();r.selectNodeContents(e);return r.getBoundingClientRect().right}));
          return {width:box.width,rightGap:box.right-right};
        }''')
        assert 200<fitted['width']<360,fitted
        assert 12<=fitted['rightGap']<=28,fitted
        card.screenshot(path=str(OUT/'info-adaptive.png'))
        checks.append('short content hugs longest field with right padding; long content caps at viewport width')
        for latitude,longitude,expected in [(0,0,'0.000000°N'),(-33.86,-151.2,'33.860000°S'),(None,None,None),(999,999,None)]:
            page.evaluate(r'''v=>{renderSignature({latitude:v[0],longitude:v[1],metadata:{},notes:'<img src=x onerror=alert(1)>\n第二行'},{});updateZoom();showSignatureInfo()}''',[latitude,longitude])
            text=page.evaluate('signatureInfo.text')
            if expected:assert expected in text
            else:
                assert 'GPS' not in text and '未记录' not in text
                assert card.locator('.signature-info-row').count()==1
                assert card.locator('.signature-info-group').count()==1
                assert card.bounding_box()['height']<150
            assert card.locator('img').count()==0 and '<img src=x' in card.inner_text()
        checks.append('zero/negative/missing/invalid GPS and escaped multiline notes')
        checks.append('compact natural height, no footer gap, one-line GPS and no empty or duplicate fields')
        page.locator('.signature-info-close').click()
        assert not card.is_visible()
        caption.focus();page.keyboard.press('Enter');card.wait_for(state='visible')
        page.keyboard.press('Escape');assert not card.is_visible()
        # Failure feedback must not pretend copying succeeded.
        page.evaluate('''()=>{Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:async()=>{throw new Error('test denial')}});showSignatureInfo()}''')
        page.locator('.signature-info-copy').click()
        page.wait_for_function('document.querySelector(".signature-info-status").textContent.includes("复制失败")')
        checks.append('keyboard interaction and honest clipboard failure feedback')
        assert not errors,errors
        browser.close()
    (OUT/'validation.json').write_text(json.dumps({'status':'PASS','checks':checks,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS',*checks,sep='\n')

if __name__=='__main__':main()
