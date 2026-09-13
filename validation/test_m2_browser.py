"""One isolated real-page chain for M2 person paging and map-grid parity."""
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

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"
REPORT = ROOT / "validation" / "reports" / "m2-browser.json"
sys.path.insert(0, str(ROOT))
from library_db import init_schema


def check(value, message, checks):
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def seed(data):
    data.mkdir(parents=True)
    with sqlite3.connect(data / "library.sqlite3") as connection:
        init_schema(connection)
        connection.execute(
            "INSERT INTO people(id,name,confirmed,ignored) VALUES (1,'合成人物',1,0)"
        )
        for asset_id in range(1, 502):
            latitude = 39.90001 + asset_id * 0.0000001
            longitude = 116.40001 + asset_id * 0.0000001
            connection.execute(
                """INSERT INTO assets(
                     id,sha256,metadata,created_at,face_state,latitude,longitude,place
                   ) VALUES (?,?, '{}',datetime('now'),1,?,?,'合成地点')""",
                (asset_id, f"{asset_id:064x}", latitude, longitude),
            )
            connection.execute(
                """INSERT INTO files(
                     asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
                   ) VALUES (?,?,1,1,datetime('now'),1,0)""",
                (asset_id, str(data / "photos" / f"{asset_id}.jpg")),
            )
            if asset_id <= 100:
                connection.execute(
                    """INSERT INTO faces(
                         id,asset_id,person_id,bbox,embedding,score,reviewed,ignored
                       ) VALUES (?,?,1,'[0,0,10,10]',x'00',0.9,1,0)""",
                    (asset_id, asset_id),
                )
        connection.commit()
    (data / "faces").mkdir()
    (data / "thumbs").mkdir()


def wait_ready(url):
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("isolated server did not start")


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    checks = []
    errors = []
    result = {"passed": False, "checks": checks}
    with tempfile.TemporaryDirectory(dir=WORK) as folder:
        data = Path(folder) / "data"
        seed(data)
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "PHOTO_LIBRARY_DATA": str(data),
            "PHOTO_MODEL_ROOT": str(data / "no-model"),
            "PHOTO_GEO_ROOT": str(data / "no-geo"),
            "NO_ALBUMENTATIONS_UPDATE": "1",
        })
        process = subprocess.Popen(
            [sys.executable, "app.py", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        browser = None
        try:
            wait_ready(url)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url, wait_until="domcontentloaded")

                page.locator('[data-view="people"]').click()
                page.wait_for_selector('#people-grid .person-card[data-person="1"]')
                page.locator('#people-grid .person-card[data-person="1"]').click()
                page.wait_for_selector('#person-dialog[open] #person-faces [data-face-id]')
                check(
                    page.locator("#person-faces [data-face-id]").count() == 48,
                    "人物详情首屏按48张分页",
                    checks,
                )
                page.locator('#person-faces [data-face-id="1"] [data-split]').click()
                page.wait_for_function(
                    "() => !document.querySelector('#person-faces [data-face-id=\"1\"]')"
                )
                page.evaluate(
                    """async () => {
                      while (state.personDetail && state.personDetail.more) {
                        await loadPersonFaces(false);
                      }
                    }"""
                )
                ids = page.locator("#person-faces [data-face-id]").evaluate_all(
                    "nodes => nodes.map(node => Number(node.dataset.faceId))"
                )
                check(
                    len(ids) == 99 and len(set(ids)) == 99 and ids == list(range(2, 101)),
                    "移出首屏人脸后继续分页无遗漏、无重复",
                    checks,
                )

                page.locator("#person-dialog [data-close]").first.click()
                page.locator('[data-view="places"]').click()
                page.wait_for_function(
                    "() => state.placeCluster && state.placeCluster.getLayers().length > 0"
                )
                cluster = page.evaluate(
                    """() => {
                      const layer=state.placeCluster.getLayers()[0];
                      layer.fire('click');
                      return {
                        count:Number(layer.getPopup().getElement().querySelector('.place-popup').querySelector('em').textContent.replace(/\\D/g,'')),
                        markers:state.placeCluster.getLayers().length
                      };
                    }"""
                )
                page.locator(".leaflet-popup .place-popup").click()
                page.wait_for_function(
                    "() => state.placeMapFilter && waterfall && waterfall.total > 0"
                )
                total = page.evaluate("() => waterfall.total")
                check(
                    cluster["markers"] == 1 and cluster["count"] == 501 and total == 501,
                    "地图聚合计数与点击后的照片查询一致",
                    checks,
                )
                frozen = page.evaluate(
                    """async () => {
                      const before={...state.placeMapFilter};
                      state.placeMap.setView([0,0],3,{animate:false});
                      await streamPage(1);
                      const context=currentBrowseContext();
                      return {
                        before,
                        query:{
                          map_west:waterfall.query.map_west,map_south:waterfall.query.map_south,
                          map_east:waterfall.query.map_east,map_north:waterfall.query.map_north
                        },
                        context:{
                          map_west:context.map_west,map_south:context.map_south,
                          map_east:context.map_east,map_north:context.map_north
                        }
                      };
                    }"""
                )
                expected = {
                    key: frozen["before"][key]
                    for key in ("map_west", "map_south", "map_east", "map_north")
                }
                check(
                    frozen["query"] == expected and frozen["context"] == expected,
                    "点击后移动地图不改变分页和大图浏览的原视野边界",
                    checks,
                )
                check(errors == [], "真实页面无pageerror或未处理Promise rejection", checks)
                browser.close()
                browser = None
            result["passed"] = True
            return 0
        finally:
            if browser is not None:
                browser.close()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(
                json.dumps(result | {"errors": errors}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


if __name__ == "__main__":
    raise SystemExit(main())
