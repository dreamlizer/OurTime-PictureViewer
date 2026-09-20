"""Isolated real-browser validation for draggable, persistent face labels."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = ROOT / "validation" / "work"
sys.path.insert(0, str(ROOT))
checks: list[str] = []


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(base_url: str, path: str):
    with urllib.request.urlopen(base_url + path, timeout=20) as response:
        return json.load(response)


def add_asset(connection, photos: Path, asset_id: int, face_id: int, person_id: int) -> None:
    path = photos / f"drag-{asset_id}.jpg"
    color = ((asset_id * 71) % 255, (asset_id * 103) % 255, (asset_id * 149) % 255)
    Image.new("RGB", (1000, 700), color).save(path, quality=95)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    connection.execute(
        """INSERT INTO assets(
             id,sha256,width,height,format,metadata,captured_at,date_source,
             date_precision,category,created_at,face_state
           ) VALUES(?,?,?,?,?,'{}',?,'EXIF','秒','照片',datetime('now'),1)""",
        (asset_id, digest, 1000, 700, "JPEG", f"2026-09-{asset_id:02}T12:00:00"),
    )
    connection.execute(
        """INSERT INTO files(
             asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
           ) VALUES (?,?,?,?,datetime('now'),1,0)""",
        (asset_id, str(path), path.stat().st_size, path.stat().st_mtime_ns),
    )
    connection.execute(
        "INSERT INTO people(id,name,confirmed,ignored) VALUES (?,?,1,0)",
        (person_id, f"测试人物{asset_id}"),
    )
    connection.execute(
        """INSERT INTO faces(
             id,asset_id,person_id,bbox,embedding,score,reviewed,ignored
           ) VALUES (?,?,?,?,?,0.99,1,0)""",
        (
            face_id,
            asset_id,
            person_id,
            json.dumps([390, 180, 520, 350, 1000, 700]),
            b"\0" * 16,
        ),
    )


def normalized_label_position(page, face_id: int) -> dict[str, float | bool]:
    return page.evaluate(
        """faceId => {
          const label=document.querySelector(`#face-name-layer [data-face-id="${faceId}"]`);
          const image=document.querySelector('#detail-img');
          const lr=label.getBoundingClientRect(),ir=image.getBoundingClientRect();
          return {
            x:(lr.left+lr.width/2-ir.left)/ir.width,
            y:(lr.top+lr.height/2-ir.top)/ir.height,
            manual:label.dataset.labelManual==='true'
          };
        }""",
        face_id,
    )


def drag_label(page, face_id: int, x_ratio: float, y_ratio: float) -> None:
    label = page.locator(f'#face-name-layer [data-face-id="{face_id}"]')
    box = label.bounding_box()
    image = page.locator("#detail-img").bounding_box()
    if not box or not image:
        raise AssertionError("标签或照片没有可用几何信息")
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    target_x = image["x"] + image["width"] * x_ratio
    target_y = image["y"] + image["height"] * y_ratio
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(target_x, target_y, steps=8)
    page.mouse.up()


def close_face_editor(page) -> None:
    if page.locator("#face-action-popover").is_visible():
        page.keyboard.press("Escape")
        page.wait_for_selector("#face-action-popover", state="hidden")

def add_real_layout_fixture(connection, photos):
    """Copy only an existing preview and seven boxes, never write the real library."""
    source_id=int(os.environ.get('LABEL_FIXTURE_ASSET','12294'))
    with sqlite3.connect((ROOT/'data/library.sqlite3').as_uri()+'?mode=ro',uri=True) as source:
        asset=source.execute('SELECT sha256 FROM assets WHERE id=?',(source_id,)).fetchone()
        faces=source.execute('SELECT f.bbox,p.name FROM faces f JOIN people p ON p.id=f.person_id WHERE f.asset_id=?',(source_id,)).fetchall()
    add_asset(connection,photos,3,303,3)
    image_path=photos/'drag-3.jpg'
    shutil.copyfile(ROOT/'data/thumbs'/f'{asset[0]}.jpg',image_path)
    with Image.open(image_path) as image: width,height=image.size
    connection.execute('UPDATE assets SET width=?,height=?,sha256=? WHERE id=3',(width,height,hashlib.sha256(image_path.read_bytes()).hexdigest()))
    connection.execute('UPDATE files SET size=?,mtime_ns=? WHERE asset_id=3',(image_path.stat().st_size,image_path.stat().st_mtime_ns))
    connection.execute('DELETE FROM faces WHERE asset_id=3')
    for i,(bbox,name) in enumerate(faces):
        connection.execute('INSERT INTO people(id,name,confirmed,ignored) VALUES(?,?,1,0)',(3000+i,name))
        connection.execute('INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed,ignored) VALUES(?,3,?,?,?,.99,1,0)',(3000+i,3000+i,bbox,b'\0'*16))

def check_smart_layout(page):
    report=ROOT/'validation/reports'/('smart-face-labels-20260919-'+os.environ.get('LABEL_FIXTURE_ASSET','12294'))
    report.mkdir(parents=True,exist_ok=True)
    if os.environ.get('LABEL_TALL_VIEW'): page.set_viewport_size({'width':1294,'height':1844})
    page.evaluate("openPhoto(3,{filter:'all',sort:'date_desc'})")
    page.wait_for_selector('#face-name-layer [data-face-id="3000"]')
    page.evaluate("async()=>{updateFaceStyle({...FACE_STYLE_PRESETS.ivory});await document.fonts.ready;await selectFaceLabelPosition('auto');}")
    if os.environ.get('LABEL_TEA'): page.evaluate("async()=>{updateFaceStyle({...FACE_STYLE_PRESETS.tea});await document.fonts.ready;}")
    if os.environ.get('LABEL_FONT_SIZE'): page.evaluate('(n)=>updateFaceStyle({fontSize:n})',int(os.environ['LABEL_FONT_SIZE']))
    page.wait_for_timeout(250)
    metrics=page.evaluate("""() => {
      const img=document.querySelector('#detail-img').getBoundingClientRect();
      const faces=state.detail.faces.map(f=>{const b=faceBox(f);return {x:img.x+b.x1/b.w*img.width,y:img.y+b.y1/b.h*img.height,w:b.width/b.w*img.width,h:b.height/b.h*img.height}});
      const labels=[...document.querySelectorAll('.face-name-layer .face-name')].map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,scale:Number(e.dataset.faceScale),side:e.classList.contains('left')?'left':'right'}});
      return {labels,faceOverlap:labels.reduce((s,l)=>s+faces.reduce((t,f)=>t+rectOverlapArea(l,f),0),0),labelOverlap:labels.reduce((s,l,i)=>s+labels.slice(0,i).reduce((t,o)=>t+rectOverlapArea(l,o),0),0)};
    }""")
    check(len(metrics['labels'])==7,'真实七人预览使用实际字体显示七个标签')
    check(len({r['scale'] for r in metrics['labels']})==1,'同图竖版标签统一倍率')
    check(metrics['faceOverlap']<1 and metrics['labelOverlap']<1,'真实七人排布不遮脸且标签无重叠')
    page.screenshot(path=str(report/'seven-people.png'))
    with page.expect_download(timeout=30000) as downloaded:
        page.click('#export-annotated-photo')
    downloaded.value.save_as(str(report/'seven-people-export.jpg'))
    with Image.open(report/'seven-people-export.jpg') as exported:
        check(exported.width>0 and exported.height>0,'真实导出按钮生成可读带标签JPEG')
    # Verify the joint solver independently with a case where right is clearly safer.
    joint=page.evaluate("""() => {
      const items=[{fx:200,fy:200,fw:80,fh:100,cx:240,cy:250},{fx:310,fy:200,fw:80,fh:100,cx:350,cy:250}];
      const gs=items.map((it,index)=>({it,index,w:38,h:72,smart:true,btn:{classList:{add(){}}}}));
      const placed=[];placeSmartVerticalLabels(gs,items,placed,{x:0,y:0,w:700,h:500});
      return placed.map(r=>({side:r.side,x:r.x,y:r.y}));
    }""")
    check({r['side'] for r in joint}=={'left','right'},'近邻之间不足放标签时自动改用外侧')
    before=page.evaluate("state.detail.faces")
    scales=page.evaluate("""() => {
      state.detail.faces=state.detail.faces.slice(0,2).map((f,i)=>({...f,bbox:[i*600+100,100,i*600+300,700,1500,1000]}));
      layoutFaceNameButtons(document.querySelector('#face-name-layer'),state.detail.faces,false);
      return [...document.querySelectorAll('.face-name-layer .face-name')].map(e=>Number(e.dataset.faceScale));
    }""")
    check(all(s==1.6 for s in scales),'近景大脸整组放大且封顶1.6')
    from validate_face_label_scale import synthetic_scale_result
    mixed=synthetic_scale_result(page)
    check(abs(mixed['small']['scale']-170/140)<.02 and abs(mixed['large']['scale']-170/140)<.02,
          '远近脸高60与280时采用中位170的统一倍率，不被最大脸独占')
    check(abs(mixed['small']['fontSize']-mixed['large']['fontSize'])<.1,
          '同图远近人物字号相同')
    check(mixed['unnamed']['width']<=26 and mixed['unnamed']['height']<=26,
          '未命名小标记不随组倍率放大')
    page.evaluate('(faces)=>{state.detail.faces=faces;renderFaceNames(state.detail)}',before)
    (report/'result.json').write_text(json.dumps({'real_seven':metrics,'crowded_pair':joint,'large_faces':scales,'mixed_sizes':mixed,'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')


def main() -> int:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="face-label-drag-", dir=WORK_ROOT))
    data = run / "data"
    photos = run / "photos"
    data.mkdir()
    photos.mkdir()
    from library_db import init_schema

    with sqlite3.connect(data / "library.sqlite3") as connection:
        init_schema(connection)
        add_asset(connection, photos, 1, 101, 1)
        add_asset(connection, photos, 2, 202, 2)
        add_real_layout_fixture(connection, photos)
        connection.execute(
            """INSERT INTO face_label_overrides(
                 face_id,asset_id,x_ratio,y_ratio,layout_version,updated_at
               ) VALUES (202,2,.2,.2,1,datetime('now'))"""
        )

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PHOTO_LIBRARY_DATA": str(data),
        "PHOTO_WEB_ROOT": str(ROOT / "web"),
        "PHOTO_MODEL_ROOT": str(run / "no-model"),
        "PHOTO_GEO_ROOT": str(run / "no-geo"),
    }
    log_path = run / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        for _ in range(160):
            try:
                status = request_json(base_url, "/api/status")
                if Path(status["capabilities"]["data_dir"]).resolve() == data.resolve():
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            log.flush()
            raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 920})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_function("typeof openPhoto === 'function'")
            check(page.evaluate("faceDirMode()==='auto' && faceLabelVerticalFor('测试人物')"), '首次打开默认中文竖排')
            page.evaluate("localStorage.setItem(VIEWER_PREFS_KEY,JSON.stringify({faceDirMode:'horizontal',faceVertical:false}))")
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function("typeof openPhoto === 'function'")
            check(page.evaluate("faceDirMode()==='auto' && faceLabelVerticalFor('测试人物')"), '旧版横排设置升级为中文默认竖排')
            page.evaluate("viewer.faceDirMode='horizontal';saveViewerPrefs()")
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function("typeof openPhoto === 'function'")
            check(page.evaluate("faceDirMode()==='horizontal' && !faceLabelVerticalFor('测试人物')"), '升级后主动选横排仍可保存')
            page.evaluate("viewer.faceDirMode='auto';saveViewerPrefs()")
            page.evaluate(
                "openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})"
            )
            page.wait_for_selector('#face-name-layer [data-face-id="101"]')
            page.wait_for_function(
                "document.querySelector('#detail-img').naturalWidth>0"
            )

            label = page.locator('#face-name-layer [data-face-id="101"]')
            check(label.evaluate("e=>getComputedStyle(e).writingMode==='vertical-rl'"), "默认风格中文标签实际渲染为竖排")
            label.click()
            page.wait_for_timeout(120)
            check(
                page.locator("#face-action-popover").is_hidden(),
                "单击标签不误开人物修改",
            )

            drag_label(page, 101, 0.76, 0.72)
            page.wait_for_function(
                "document.querySelector('#face-name-layer [data-face-id=\"101\"]').dataset.labelManual==='true'"
            )
            page.wait_for_timeout(450)
            first = normalized_label_position(page, 101)
            check(
                abs(first["x"] - 0.76) < 0.025
                and abs(first["y"] - 0.72) < 0.025
                and first["manual"],
                "拖动松手后标签停留在目标位置且不弹回",
            )
            check(
                page.locator("#face-action-popover").is_hidden(),
                "拖动标签不误开人物修改",
            )
            saved = request_json(base_url, "/api/photos/1")["faces"][0]
            check(
                abs(saved["label_x_ratio"] - 0.76) < 0.025
                and abs(saved["label_y_ratio"] - 0.72) < 0.025,
                "松手后只读接口返回已保存坐标",
            )

            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("typeof openPhoto === 'function'")
            page.evaluate(
                "openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})"
            )
            page.wait_for_selector('#face-name-layer [data-face-id="101"]')
            page.wait_for_function(
                "document.querySelector('#detail-img').naturalWidth>0"
            )
            reopened = normalized_label_position(page, 101)
            check(
                abs(reopened["x"] - saved["label_x_ratio"]) < 0.025
                and abs(reopened["y"] - saved["label_y_ratio"]) < 0.025
                and reopened["manual"],
                "刷新并重新打开后标签位置保持",
            )

            page.click("#zoom-in")
            page.wait_for_timeout(180)
            zoomed = normalized_label_position(page, 101)
            check(
                abs(zoomed["x"] - saved["label_x_ratio"]) < 0.025
                and abs(zoomed["y"] - saved["label_y_ratio"]) < 0.025,
                "缩放重排后手动标签不弹回",
            )

            before_double = normalized_label_position(page, 101)
            label = page.locator('#face-name-layer [data-face-id="101"]')
            label.dblclick()
            page.wait_for_selector("#face-action-popover:not([hidden])")
            after_double = normalized_label_position(page, 101)
            check(
                abs(before_double["x"] - after_double["x"]) < 0.005
                and abs(before_double["y"] - after_double["y"]) < 0.005,
                "双击打开人物修改且不移动标签",
            )
            close_face_editor(page)

            frozen = page.evaluate(
                """() => {
                  const current=document.querySelector('#face-name-layer [data-face-id="101"]');
                  const snapshot=window.__ourTimeAnnotatedExport.freeze().snapshot;
                  const doc=new DOMParser().parseFromString(snapshot.mat_html,'text/html');
                  const exported=doc.querySelector('[data-face-id="101"]');
                  return {
                    currentLeft:current.style.left,currentTop:current.style.top,
                    exportLeft:exported.style.left,exportTop:exported.style.top
                  };
                }"""
            )
            check(
                frozen["currentLeft"] == frozen["exportLeft"]
                and frozen["currentTop"] == frozen["exportTop"],
                "导出快照保留手动标签位置",
            )

            page.evaluate(
                """() => {
                  const realFetch=window.fetch.bind(window);
                  let first=true;
                  window.fetch=(url,options)=>{
                    const response=realFetch(url,options);
                    if(first && String(url).includes('/label-position')){
                      first=false;
                      return response.then(value=>new Promise(resolve=>setTimeout(()=>resolve(value),500)));
                    }
                    return response;
                  };
                }"""
            )
            drag_label(page, 101, 0.34, 0.64)
            drag_label(page, 101, 0.68, 0.28)
            page.wait_for_timeout(260)
            during_race = normalized_label_position(page, 101)
            check(
                abs(during_race["x"] - 0.68) < 0.025
                and abs(during_race["y"] - 0.28) < 0.025,
                "连续拖动等待写入时不回跳旧位置",
            )
            page.wait_for_timeout(850)
            after_race = normalized_label_position(page, 101)
            saved_after_race = request_json(base_url, "/api/photos/1")["faces"][0]
            check(
                abs(after_race["x"] - 0.68) < 0.025
                and abs(after_race["y"] - 0.28) < 0.025
                and abs(saved_after_race["label_x_ratio"] - 0.68) < 0.025,
                "连续拖动最终界面和数据库都采用最后位置",
            )

            previous = normalized_label_position(page, 101)
            page.route(
                "**/api/photos/1/faces/101/label-position",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"detail":"forced failure"}',
                ),
            )
            drag_label(page, 101, 0.22, 0.82)
            page.wait_for_function(
                "() => document.querySelector('#toast').classList.contains('visible')"
            )
            page.wait_for_timeout(180)
            rolled_back = normalized_label_position(page, 101)
            check(
                abs(rolled_back["x"] - previous["x"]) < 0.025
                and abs(rolled_back["y"] - previous["y"]) < 0.025,
                "保存失败明确回退到上一个已保存位置",
            )
            page.unroute("**/api/photos/1/faces/101/label-position")

            page.click("#face-style-button")
            page.wait_for_selector("#face-style-popover:not([hidden])")
            check(
                page.locator("#face-label-position").input_value() == "manual",
                "存在手动位置时布局选择器明确显示本张为手动位置",
            )
            page.select_option("#face-label-position", "top")
            page.wait_for_function(
                """() => {
                  const label=document.querySelector('#face-name-layer [data-face-id="101"]');
                  return label.dataset.labelManual!=='true' && label.classList.contains('top');
                }"""
            )
            reset_detail = request_json(base_url, "/api/photos/1")["faces"][0]
            with sqlite3.connect(data / "library.sqlite3") as connection:
                other_count = connection.execute(
                    "SELECT count(*) FROM face_label_overrides WHERE asset_id=2"
                ).fetchone()[0]
            check(
                reset_detail["label_x_ratio"] == saved_after_race["label_x_ratio"]
                and reset_detail["label_y_ratio"] == saved_after_race["label_y_ratio"]
                and other_count == 1,
                "选择偏上保留当前和其他照片的自定义记录",
            )
            page.select_option("#face-label-position", "auto")
            check(not normalized_label_position(page,101)["manual"], "智能排布不使用手动坐标")
            page.select_option("#face-label-position", "manual")
            restored=normalized_label_position(page,101)
            check(restored["manual"] and abs(restored["x"]-reset_detail["label_x_ratio"])<.025,
                  "切回自定义恢复原坐标，切换不清库")

            page.click("#face-style-close")
            drag_label(page, 101, 0.36, 0.72)
            page.wait_for_timeout(150)
            saved_before_failed_reset = request_json(base_url, "/api/photos/1")["faces"][0]
            page.click("#face-style-button")
            page.route(
                "**/api/photos/1/face-labels/reset",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"detail":"forced reset failure"}',
                ),
            )
            page.click("#face-label-reset-photo")
            page.wait_for_timeout(180)
            check(
                page.locator('#face-name-layer [data-face-id="101"]').get_attribute("data-label-manual") == "true"
                and page.locator("#face-label-position").input_value() == "manual",
                "显式清除失败时保留自定义标签且不伪装为已清理",
            )
            page.unroute("**/api/photos/1/face-labels/reset")
            with page.expect_response(
                lambda response: response.url.endswith("/api/photos/1/face-labels/reset")
            ):
                page.click("#face-label-reset-photo")
            page.wait_for_function(
                """() => {
                  const label=document.querySelector('#face-name-layer [data-face-id="101"]');
                  return label.dataset.labelManual!=='true'
                    && document.querySelector('#face-label-position').value==='auto';
                }"""
            )
            reset_after_failure = request_json(base_url, "/api/photos/1")["faces"][0]
            check(
                saved_before_failed_reset["label_x_ratio"] is not None
                and reset_after_failure["label_x_ratio"] is None,
                "失败后显式清除可删除本张自定义并恢复智能排布",
            )
            page.wait_for_function("document.querySelector('#face-label-position option[value=manual]').disabled")
            check(page.locator('#face-label-position option[value="manual"]').evaluate('(el)=>el.disabled'),
                  "无自定义坐标时选项置灰")
            page.click('#face-style-close')
            page.evaluate("openPhoto(2,{filter:'all',sort:'date_desc'})")
            page.wait_for_selector('#face-name-layer [data-face-id="202"]')
            check(normalized_label_position(page,202)["manual"],"旧数据库坐标无需迁移，自动进入自定义")
            check_smart_layout(page)
            check(not errors, "浏览器没有 pageerror")
            browser.close()
        print(f"PASS {len(checks)} checks", flush=True)
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        shutil.rmtree(run, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
