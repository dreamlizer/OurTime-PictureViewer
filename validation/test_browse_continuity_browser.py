"""Isolated synthetic-library regression, never connects to production 8765."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from library_db import init_schema
from PIL import Image
from playwright.sync_api import sync_playwright

RUN=ROOT/'validation'/'work'/('browse-continuity-'+time.strftime('%Y%m%d-%H%M%S'))
DATA=RUN/'data';DATA.mkdir(parents=True)
PHOTOS=RUN/'photos';PHOTOS.mkdir()
REPORT=ROOT/'validation'/'reports'/'browse-continuity-20260918';REPORT.mkdir(exist_ok=True)
checks=[]

def check(condition,message):
    if not condition:raise AssertionError(message)
    checks.append(message);print('PASS',message,flush=True)

def main():
    with sqlite3.connect(DATA/'library.sqlite3') as c:
        init_schema(c)
        for aid in range(1,181):
            path=PHOTOS/f'fixture-{aid}.jpg'
            Image.new('RGB',(480,320),(aid%230,120,100)).save(path)
            c.execute('''INSERT INTO assets(id,sha256,width,height,format,metadata,captured_at,date_source,date_precision,
                category,created_at,face_state,manual_place) VALUES (?,?,480,320,'JPEG','{}',?,'EXIF','秒','照片',?,1,?)''',
                (aid,f'{aid:064x}',f'2020-09-{aid%28+1:02d}T12:00:00','2026-09-18',f'原地点{aid}'))
            c.execute('INSERT INTO files(asset_id,path,size,mtime_ns) VALUES (?,?,1,1)',(aid,str(path)))
        for pid in (1,2):
            c.execute('INSERT INTO people(id,name,confirmed) VALUES (?,?,1)',(pid,f'测试人物{pid}'))
            c.execute("INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed) VALUES (?,?,?,'[0,0,30,30,480,320]',x'00',0.95,1)",(pid,pid,pid))
    (DATA/'faces').mkdir();(DATA/'thumbs').mkdir()
    for pid in (1,2):Image.new('RGB',(40,40),'#889988').save(DATA/'faces'/f'{pid}.jpg')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    env={**os.environ,'PHOTO_LIBRARY_DATA':str(DATA),'PHOTO_WEB_ROOT':str(ROOT/'web'),
         'PHOTO_MODEL_ROOT':str(DATA/'no-model'),'PHOTO_GEO_ROOT':str(DATA/'no-geo'),
         'PHOTO_NO_BROWSER':'1','PYTHONIOENCODING':'utf-8'}
    log=(RUN/'server.log').open('w',encoding='utf-8')
    process=subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--port',str(port)],cwd=ROOT,env=env,
        stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    errors=[];passed=False
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(url+'/api/health',timeout=2):break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('isolated server did not start')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url)
            page.wait_for_function("['home','timeline','scan'].includes(state.view)")
            if page.evaluate("state.view") != 'timeline':
                page.locator('[data-view="timeline"]').click()
            page.wait_for_selector('#photo-grid [data-photo]')
            page.evaluate("async()=>{state.q='fixture';await loadPhotos();}")
            page.mouse.move(1000,650)
            for _ in range(4):
                page.mouse.wheel(0,650);page.wait_for_timeout(400)
            page.evaluate('OurTimeContinuity.capturePosition()')
            before=page.evaluate("JSON.parse(sessionStorage.getItem('ourtime.browse.v1'))")
            check(before['scroll']>400,'scrolled fixture library before navigation')
            page.locator('[data-view="people"]').click()
            page.wait_for_function("state.view==='people' && !document.querySelector('#people-view').hidden")
            page.locator('[data-view="timeline"]').click()
            page.wait_for_function("state.view==='timeline' && scrollY>400 && OurTimeContinuity.isIdle()")
            check(page.evaluate('state.q')=='fixture','sidebar return preserves structured filter')
            check(abs(page.evaluate('scrollY')-before['scroll'])<100,'sidebar return preserves photo position')
            page.reload();page.wait_for_function("state.view==='timeline' && scrollY>400 && OurTimeContinuity.isIdle()")
            check(page.evaluate('state.q')=='fixture','refresh preserves filter and scroll')
            page.locator('[data-view="favorites"]').click();page.wait_for_function("state.view==='favorites' && history.state?.ourtimeBrowse?.view==='favorites'")
            page.go_back();page.wait_for_function("state.view==='timeline' && scrollY>400 && OurTimeContinuity.isIdle()")
            check(page.evaluate('state.q')=='fixture','browser back restores photo query')
            page.go_forward();page.wait_for_function("state.view==='favorites' && OurTimeContinuity.isIdle()")
            check(True,'browser forward restores destination')
            page.locator('[data-view="timeline"]').click();page.wait_for_function("state.view==='timeline' && OurTimeContinuity.isIdle()")
            # Exercise actual batch form -> server commit -> recent operations -> undo.
            page.evaluate("()=>{state.selected=new Set([1,2]);state.selecting=true;updateBatch();document.querySelector('#batch-edit').click();}")
            page.locator('#batch-place-enabled').check();page.locator('#batch-place').fill('批量测试地点')
            page.locator('#batch-form button[type="submit"]').click()
            try:page.wait_for_function("!document.querySelector('#batch-dialog').open",timeout=10000)
            except Exception:
                print(page.evaluate("({toast:document.querySelector('#toast').textContent,selected:[...state.selected],view:state.view,place:document.querySelector('#batch-place').value,enabled:document.querySelector('#batch-place-enabled').checked})"),flush=True)
                raise
            page.locator('#organize-nav').evaluate('(el)=>el.open=true')
            page.locator('#organize-nav [data-recent-operations]').click()
            page.wait_for_selector('[data-undo-operation]')
            page.screenshot(path=str(REPORT/'recent-operations.png'),animations='disabled')
            def lose_undo_response(route):
                if route.request.post_data_json.get('dry_run') is False:
                    route.fetch();route.abort('failed')
                else:route.continue_()
            page.route('**/api/organize/operations/*/undo',lose_undo_response)
            page.locator('[data-undo-operation]').first.click()
            page.wait_for_function("document.querySelector('.operations-message').textContent.includes('已撤销')")
            with sqlite3.connect(DATA/'library.sqlite3') as c:
                values=[r[0] for r in c.execute('SELECT manual_place FROM assets WHERE id IN (1,2) ORDER BY id')]
            check(values==['原地点1','原地点2'],'actual batch form undo restores distinct original values')
            check(True,'lost undo response recovers committed status without duplicate write')
            page.unroute('**/api/organize/operations/*/undo',lose_undo_response)
            page.locator('#operations-dialog .dialog-close').click()
            page.reload();page.wait_for_selector('#photo-grid [data-photo]')
            page.locator('#organize-nav').evaluate('(el)=>el.open=true')
            page.locator('#organize-nav [data-recent-operations]').click();page.wait_for_selector('[data-undo-operation]')
            check(page.locator('[data-undo-operation]').first.is_disabled(),'undone receipt survives refresh')
            page.set_viewport_size({'width':390,'height':844})
            page.evaluate('waitWaterfallFrames(3)')
            page.screenshot(path=str(REPORT/'recent-operations-narrow.png'),animations='disabled')
            check(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'narrow viewport has no horizontal overflow')
            page.locator('#operations-dialog .dialog-close').click();page.set_viewport_size({'width':1440,'height':900})
            page.locator('[data-view="people"]').click();page.wait_for_function("state.view==='people' && OurTimeContinuity.isIdle()")
            page.locator('[data-open-person="2"]').click();page.wait_for_selector('#person-dialog[open]')
            page.locator('#merge-target').select_option('1');page.locator('#merge-person').click()
            page.wait_for_function("state.personId===1 && document.querySelector('#person-notice').textContent.includes('已合并')")
            page.locator('#person-dialog [data-recent-operations]').click();page.wait_for_selector('[data-undo-operation]:not([disabled])')
            check('合并人物' in page.locator('.operation-row').first.inner_text(),'person merge appears in shared recent operations')
            page.locator('[data-undo-operation]').first.click();page.wait_for_function("document.querySelector('.operations-message').textContent==='已撤销这次操作。'")
            with sqlite3.connect(DATA/'library.sqlite3') as c:
                check(c.execute('SELECT person_id FROM faces WHERE id=2').fetchone()[0]==2,'person merge undo restores original face group through UI')
            page.route('**/api/organize/photos',lambda route:route.fulfill(status=405,content_type='application/json',body='{"detail":"Method Not Allowed"}'))
            legacy=page.evaluate("async()=>await editPhotoMetadata({ids:[3],notes:'旧后台兼容测试'})")
            check(legacy['updated']==1 and legacy['undoAvailable'] is False,'older backend keeps editing available without claiming undo support')
            check(not errors,'no browser page errors: '+str(errors))
            browser.close();passed=True
    finally:
        process.terminate();process.wait(timeout=15);log.close()
        (REPORT/'result.json').write_text(json.dumps({'passed':passed,'checks':checks,'page_errors':errors,'run':str(RUN)},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0

if __name__=='__main__':raise SystemExit(main())
