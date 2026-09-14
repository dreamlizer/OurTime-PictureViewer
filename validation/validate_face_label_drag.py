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
            page.evaluate(
                "openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})"
            )
            page.wait_for_selector('#face-name-layer [data-face-id="101"]')
            page.wait_for_function(
                "document.querySelector('#detail-img').naturalWidth>0"
            )

            label = page.locator('#face-name-layer [data-face-id="101"]')
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
            with page.expect_response(
                lambda response: response.url.endswith("/api/photos/1/face-labels/reset")
            ):
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
                reset_detail["label_x_ratio"] is None
                and reset_detail["label_y_ratio"] is None
                and other_count == 1,
                "选择靠上会清理当前照片手动位置并保留其他照片位置",
            )

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
            page.select_option("#face-label-position", "right")
            page.wait_for_timeout(180)
            check(
                page.locator('#face-name-layer [data-face-id="101"]').get_attribute("data-label-manual") == "true"
                and page.locator("#face-label-position").input_value() == "manual",
                "布局重置失败时保留手动标签且不伪装为已清理",
            )
            page.unroute("**/api/photos/1/face-labels/reset")
            with page.expect_response(
                lambda response: response.url.endswith("/api/photos/1/face-labels/reset")
            ):
                page.select_option("#face-label-position", "auto")
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
                "失败后重新选择自动可清空手动位置并恢复自动排列",
            )
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
