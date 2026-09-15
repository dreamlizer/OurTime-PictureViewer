"""Isolated browser regression for stable hierarchical map clusters."""
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
REPORT = ROOT / "validation" / "reports" / "map-cluster-browser.json"
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
        for asset_id, latitude in (
            (1, 0.005),
            (2, 0.030),
            (3, 0.005),
            (4, 0.030),
        ):
            connection.execute(
                """INSERT INTO assets(
                     id,sha256,metadata,created_at,face_state,latitude,longitude,place
                   ) VALUES (?,?, '{}',datetime('now'),0,?,0.005,'层级测试')""",
                (asset_id, f"{asset_id:064x}", latitude),
            )
            connection.execute(
                """INSERT INTO files(
                     asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
                   ) VALUES (?,?,1,1,datetime('now'),1,0)""",
                (asset_id, str(data / f"{asset_id}.jpg")),
            )
        connection.execute(
            """INSERT INTO assets(
                 id,sha256,metadata,created_at,face_state,latitude,longitude,place
               ) VALUES (5,?, '{}',datetime('now'),0,41.24,9.19,'Santa Teresa Gallura · Italy')""",
            (f"{5:064x}",),
        )
        connection.execute(
            """INSERT INTO files(
                 asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
               ) VALUES (5,?,1,1,datetime('now'),1,0)""",
            (str(data / "europe.jpg"),),
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
    checks, errors = [], []
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
                page.locator('[data-view="places"]').click()
                page.wait_for_function("() => state.placeMap && state.placeCluster")

                hierarchy = page.evaluate(
                    """async () => {
                      state.placeMap.setView([0.02,0.02],11,{animate:false});
                      clearTimeout(state.placeMapTimer);
                      await loadPlaceMap();
                      const parent=[...state.placeMarkers.keys()][0];
                      const parentPosition=state.placeMarkers.get(parent).getLatLng();
                      state.placeMap.setZoom(12,{animate:false});
                      clearTimeout(state.placeMapTimer);
                      await loadPlaceMap();
                      return {
                        plugin:typeof L.markerClusterGroup,
                        parent,
                        children:[...state.placeMarkers.values()].map(marker=>({
                          id:marker.__placeCluster.cluster_id,
                          parent:marker.__placeCluster.parent_id,
                          latitude:marker.getLatLng().lat,
                          longitude:marker.getLatLng().lng,
                        })),
                        parentPosition,
                        retiring:state.placeRetiring.size,
                        layers:state.placeCluster.getLayers().length,
                        animating:document.querySelector('#places-map').classList.contains('is-cluster-transition'),
                      };
                    }"""
                )
                check(hierarchy["plugin"] == "undefined", "地点总览只保留一层服务端聚合", checks)
                marker_style = page.locator(".place-cluster-marker").first.evaluate(
                    """element => {
                      const style=getComputedStyle(element);
                      return {
                        display:style.display,
                        background:style.backgroundColor,
                        border:style.borderStyle,
                        width:element.getBoundingClientRect().width,
                        height:element.getBoundingClientRect().height,
                      };
                    }"""
                )
                check(
                    marker_style["display"] == "grid"
                    and marker_style["background"] != "rgba(0, 0, 0, 0)"
                    and marker_style["border"] == "solid"
                    and marker_style["width"] >= 36
                    and marker_style["height"] >= 36,
                    "聚合点保留清晰可见的圆形底牌",
                    checks,
                )
                check(
                    len(hierarchy["children"]) == 2
                    and {item["parent"] for item in hierarchy["children"]} == {hierarchy["parent"]},
                    "放大后的子点来自同一个稳定父聚合点",
                    checks,
                )
                check(
                    hierarchy["retiring"] == 1
                    and hierarchy["layers"] == 3
                    and hierarchy["animating"],
                    "父点与子点短暂共存并执行展开动画",
                    checks,
                )
                page.wait_for_timeout(360)
                settled = page.evaluate(
                    """() => ({
                      retiring:state.placeRetiring.size,
                      layers:state.placeCluster.getLayers().length,
                      current:state.placeMarkers.size,
                      animating:document.querySelector('#places-map').classList.contains('is-cluster-transition')
                    })"""
                )
                check(
                    settled == {"retiring": 0, "layers": 2, "current": 2, "animating": False},
                    "动画结束后只保留当前层级的定位点",
                    checks,
                )

                rapid = page.evaluate(
                    """async () => {
                      const realFetch=window.fetch.bind(window);
                      window.fetch=(url,options={}) => {
                        const text=String(url);
                        if(!text.includes('/api/places?'))return realFetch(url,options);
                        const zoom=Number(new URL(text,location.href).searchParams.get('zoom'));
                        const delay=zoom<12?260:15;
                        return new Promise((resolve,reject)=>{
                          const timer=setTimeout(()=>realFetch(url,options).then(resolve,reject),delay);
                          options.signal?.addEventListener('abort',()=>{
                            clearTimeout(timer);
                            reject(new DOMException('Aborted','AbortError'));
                          },{once:true});
                        });
                      };
                      state.placeMap.setZoom(11,{animate:false});
                      clearTimeout(state.placeMapTimer);
                      const slow=loadPlaceMap();
                      await new Promise(resolve=>setTimeout(resolve,20));
                      state.placeMap.setZoom(12,{animate:false});
                      clearTimeout(state.placeMapTimer);
                      const fast=loadPlaceMap();
                      const outcomes=await Promise.allSettled([slow,fast]);
                      window.fetch=realFetch;
                      return {
                        outcomes:outcomes.map(item=>item.status),
                        rendered:state.placeRenderedZoom,
                        current:[...state.placeMarkers.values()].map(marker=>marker.__placeCluster.cluster_id),
                      };
                    }"""
                )
                check(
                    rapid["outcomes"] == ["rejected", "fulfilled"]
                    and rapid["rendered"] == 12
                    and len(rapid["current"]) == 2,
                    "快速缩放会废弃旧请求且只渲染最后层级",
                    checks,
                )
                world = page.evaluate(
                    """async () => {
                      state.placeMap.setView([45,10],1,{animate:false});
                      clearTimeout(state.placeMapTimer);
                      await loadPlaceMap();
                      const bounds=placeGpsBounds(state.placeMap.getBounds());
                      const marker=[...state.placeMarkers.values()][0];
                      marker?.openPopup();
                      return {bounds,found:Boolean(marker)};
                    }"""
                )
                check(
                    world["bounds"]["west"] == -180
                    and world["bounds"]["east"] == 180,
                    "全球缩放视野归一为完整经度范围",
                    checks,
                )
                check(world["found"], "含欧洲照片的全球定位点可点击", checks)
                with page.expect_response(
                    lambda response: "/api/photos?" in response.url
                    and "map_west=" in response.url
                ) as response_info:
                    page.locator(".place-popup").click()
                response = response_info.value
                check(response.status == 200, "点击欧洲定位点能加载照片列表", checks)
                page.wait_for_function(
                    "() => String(state.view).startsWith('place:') && waterfall.total > 0"
                )
                check(
                    page.locator("#stream-retry").is_hidden(),
                    "欧洲地点照片页没有视野边界错误",
                    checks,
                )
                check(errors == [], "真实页面无 JavaScript 错误", checks)
                browser.close()
                browser = None
            result["passed"] = True
            return 0
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
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
