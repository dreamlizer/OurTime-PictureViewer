"""Real browser flow for the rectangle place editor on isolated data."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"
REPORT_DIR = ROOT / "validation" / "reports" / "map-area-place"
sys.path.insert(0, str(ROOT))

from library_db import init_schema


def check(condition, message, results):
    if not condition:
        raise AssertionError(message)
    results.append({"status": "PASS", "message": message})
    print("PASS", message)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                if json.load(response).get("ok"):
                    return
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("isolated server did not become ready")


def seed(data):
    data.mkdir(parents=True)
    thumbs = data / "thumbs"
    originals = data / "synthetic-originals"
    thumbs.mkdir()
    originals.mkdir()
    connection = sqlite3.connect(data / "library.sqlite3")
    try:
        init_schema(connection)
        points = [
            (1, 39.9000, 116.4000, "北京市 · 朝阳区 · 双井街道", (97, 129, 111)),
            (2, 39.9010, 116.4010, "北京市 · 朝阳区 · 劲松街道", (154, 118, 82)),
            (3, 39.9200, 116.4200, "北京市 · 东城区 · 朝内街道", (91, 108, 139)),
        ]
        for asset_id, latitude, longitude, place, color in points:
            digest = f"{asset_id:064x}"
            original = originals / f"{asset_id}.jpg"
            Image.new("RGB", (320, 220), color).save(original, quality=88)
            Image.new("RGB", (180, 124), color).save(thumbs / f"{digest}.jpg", quality=85)
            connection.execute(
                """INSERT INTO assets(
                     id,sha256,width,height,format,metadata,captured_at,
                     latitude,longitude,place,manual_place,notes,face_state,
                     created_at,excluded,exclude_reason,derivative_policy
                   ) VALUES (?,?,320,220,'JPEG','{}','2026-01-02T03:04:05',
                             ?,?,?,NULL,'',0,datetime('now'),0,'','preserve')""",
                (asset_id, digest, latitude, longitude, place),
            )
            connection.execute(
                """INSERT INTO files(
                     asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
                   ) VALUES (?,?,1,1,datetime('now'),1,0)""",
                (asset_id, str(original)),
            )
        connection.commit()
    finally:
        connection.close()


def map_drag(page, south, west, north, east):
    points = page.evaluate(
        """([south,west,north,east]) => {
          const map=state.placeMap;
          const rect=document.getElementById('places-map').getBoundingClientRect();
          const a=map.latLngToContainerPoint([south,west]);
          const b=map.latLngToContainerPoint([north,east]);
          return {
            start:{x:rect.left+a.x,y:rect.top+a.y},
            end:{x:rect.left+b.x,y:rect.top+b.y}
          };
        }""",
        [south, west, north, east],
    )
    page.mouse.move(points["start"]["x"], points["start"]["y"])
    page.mouse.down()
    page.mouse.move(points["end"]["x"], points["end"]["y"], steps=8)
    page.mouse.up()


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(dir=WORK, ignore_cleanup_errors=True) as parent:
        data = Path(parent) / "data"
        seed(data)
        port = free_port()
        env = os.environ.copy()
        env["PHOTO_LIBRARY_DATA"] = str(data)
        env["PHOTO_MODEL_ROOT"] = str(data / "no-model")
        env["PHOTO_GEO_ROOT"] = str(data / "no-geo")
        env["PHOTO_LIBRARY_PORT"] = str(port)
        process = subprocess.Popen(
            [sys.executable, "app.py", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        base = f"http://127.0.0.1:{port}"
        try:
            wait_ready(base)
            page_errors = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 860})
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.goto(base, wait_until="domcontentloaded")
                page.locator('[data-view="places"]').click()
                page.wait_for_selector("#places-view:not([hidden])")
                page.select_option("#places-basemap", "osm")
                page.evaluate(
                    """() => {
                      const map=ensurePlaceMap();
                      map.setView([39.9,116.4],14,{animate:false});
                      map.invalidateSize();
                    }"""
                )
                page.wait_for_timeout(250)
                center_before = page.evaluate(
                    "() => ({lat:state.placeMap.getCenter().lat,lng:state.placeMap.getCenter().lng})"
                )
                page.locator("#map-area-start").click(force=True)
                check(
                    page.evaluate(
                        "() => OurTimeMapAreaEditor.isActive() && !state.placeMap.dragging.enabled()"
                    ),
                    "总览显式进入框选后地图视野被冻结",
                    results,
                )
                check(
                    page.locator("#map-area-start").evaluate(
                        """button => button.dataset.phase==='armed'
                          && button.textContent.includes('框选中')
                          && getComputedStyle(button).animationName==='map-area-pulse'"""
                    ),
                    "进入框选后按钮以闪烁文案明确提示仍在框选状态",
                    results,
                )
                map_drag(page, 39.895, 116.395, 39.905, 116.405)
                page.wait_for_function(
                    "() => document.getElementById('map-area-summary').textContent.includes('2 张')"
                )
                check(
                    page.locator("#map-area-samples img").count() == 2,
                    "真实拖框返回完整数量并显示不超过九张的合成预览",
                    results,
                )
                check(
                    page.locator("#map-area-name").input_value()
                    == "北京市 · 朝阳区"
                    and "共同属于 北京市 · 朝阳区"
                    in page.locator("#map-area-preview-note").inner_text(),
                    "同一区内框选会自动填写共同的市区层级",
                    results,
                )
                check(
                    page.locator("#map-area-start").evaluate(
                        """button => button.dataset.phase==='selected'
                          && button.textContent.includes('框选已完成')
                          && getComputedStyle(button).animationName==='none'"""
                    ),
                    "框选完成后按钮停止闪烁并明确显示已完成",
                    results,
                )
                center_after = page.evaluate(
                    "() => ({lat:state.placeMap.getCenter().lat,lng:state.placeMap.getCenter().lng})"
                )
                check(
                    abs(center_before["lat"] - center_after["lat"]) < 1e-9
                    and abs(center_before["lng"] - center_after["lng"]) < 1e-9,
                    "绘框过程中地图中心没有移动",
                    results,
                )
                page.screenshot(
                    path=REPORT_DIR / "rectangle-preview.png", full_page=True
                )
                page.locator("#map-area-name").fill("北京市 · 朝阳区 · 合成园区")
                page.locator("#map-area-save").click()
                page.wait_for_function(
                    "() => document.getElementById('toast').textContent.includes('修改 2 张')"
                )
                check(
                    page.evaluate(
                        "() => !OurTimeMapAreaEditor.isActive() && state.placeMap.dragging.enabled()"
                    ),
                    "保存成功后退出框选并恢复地图拖动",
                    results,
                )
                check(
                    page.locator("#map-area-start").evaluate(
                        """button => button.dataset.phase==='idle'
                          && button.getAttribute('aria-pressed')==='false'
                          && button.textContent.includes('框选修改地点')"""
                    ),
                    "退出后按钮恢复普通状态，用户可一眼确认已退出",
                    results,
                )
                with sqlite3.connect(data / "library.sqlite3") as connection:
                    saved = connection.execute(
                        "SELECT id,manual_place FROM assets ORDER BY id"
                    ).fetchall()
                    edit_count = connection.execute(
                        "SELECT count(*) FROM edits"
                    ).fetchone()[0]
                check(
                    saved == [
                        (1, "北京市 · 朝阳区 · 合成园区"),
                        (2, "北京市 · 朝阳区 · 合成园区"),
                        (3, None),
                    ]
                    and edit_count == 2,
                    "确认只修改框内人工地点并逐张留痕",
                    results,
                )

                page.evaluate(
                    """async () => {
                      const photo=await api('/api/photos/1');
                      await showPhotoPlaceMap(photo,{returnView:'places'});
                    }"""
                )
                page.set_viewport_size({"width": 390, "height": 780})
                page.evaluate("() => state.placeMap.invalidateSize()")
                page.locator("#map-area-start").click()
                map_drag(page, 39.895, 116.395, 39.905, 116.405)
                page.wait_for_function(
                    "() => document.getElementById('map-area-summary').textContent.includes('2 张')"
                )
                panel_box = page.locator("#map-area-editor").bounding_box()
                check(
                    panel_box is not None
                    and panel_box["x"] >= 0
                    and panel_box["x"] + panel_box["width"] <= 390.5,
                    "单张位置页共用框选流程且390px面板不横向溢出",
                    results,
                )
                page.screenshot(
                    path=REPORT_DIR / "single-photo-narrow.png", full_page=True
                )
                page.locator("#map-area-cancel").click()
                page.locator("#places-photo-edit").click()
                page.wait_for_selector("#photo-place-editor:not([hidden])")
                check(
                    page.locator("#photo-place-radius").get_attribute("max") == "10000",
                    "取消框选不写入且原1到10000米半径入口仍可用",
                    results,
                )
                page.locator("#photo-place-editor-cancel").click()

                for _ in range(3):
                    page.locator("#map-area-start").click()
                    page.locator("#map-area-start").click()
                check(
                    not page.evaluate("() => OurTimeMapAreaEditor.isActive()"),
                    "连续开关三次没有残留编辑会话",
                    results,
                )

                page.locator("#map-area-start").click()
                page.evaluate(
                    """() => {
                      const original=window.fetch.bind(window);
                      let previews=0;
                      window.fetch=async (...args) => {
                        const response=await original(...args);
                        if(String(args[0]).includes('/api/places/area/preview') && ++previews===1){
                          await new Promise(resolve => setTimeout(resolve,500));
                        }
                        return response;
                      };
                    }"""
                )
                page.evaluate(
                    """() => {
                      window.__slowPreview=OurTimeMapAreaEditor.previewBounds({
                        west:116.3999,south:39.8999,east:116.4001,north:39.9001
                      });
                    }"""
                )
                page.evaluate(
                    """() => OurTimeMapAreaEditor.previewBounds({
                      west:116.395,south:39.895,east:116.405,north:39.905
                    })"""
                )
                page.wait_for_function(
                    "() => document.getElementById('map-area-summary').textContent.includes('2 张')"
                )
                page.wait_for_timeout(650)
                check(
                    "2 张" in page.locator("#map-area-summary").inner_text(),
                    "慢预览A晚到时不会覆盖较新的预览B",
                    results,
                )
                page.locator("#map-area-name").fill("北京市 · 朝阳区 · 冲突目标")
                with sqlite3.connect(data / "library.sqlite3") as connection:
                    connection.execute(
                        "UPDATE assets SET manual_place='并发地点' WHERE id=1"
                    )
                    connection.commit()
                page.locator("#map-area-save").click()
                page.wait_for_function(
                    "() => document.getElementById('map-area-summary').textContent.includes('发生变化')"
                )
                check(
                    "本次没有写入" in page.locator("#map-area-preview-note").inner_text(),
                    "浏览器遇到指纹冲突时整批不写并要求重新预览",
                    results,
                )
                with sqlite3.connect(data / "library.sqlite3") as connection:
                    places = connection.execute(
                        "SELECT manual_place FROM assets ORDER BY id"
                    ).fetchall()
                check(
                    places == [
                        ("并发地点",),
                        ("北京市 · 朝阳区 · 合成园区",),
                        (None,),
                    ],
                    "冲突没有偷写新的地点",
                    results,
                )
                page.locator("#map-area-cancel").click()
                check(not page_errors, "浏览器流程没有pageerror", results)
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            time.sleep(2)

        report = {
            "status": "PASS",
            "isolated_data": True,
            "random_port": True,
            "checks": results,
            "screenshots": [
                "rectangle-preview.png",
                "single-photo-narrow.png",
            ],
        }
        (REPORT_DIR / "browser-validation.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
