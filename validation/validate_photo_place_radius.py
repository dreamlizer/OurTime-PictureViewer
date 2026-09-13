"""Isolated acceptance for GPS-radius place review, save, and viewer return."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "validation" / "work" / ("photo-place-radius-" + time.strftime("%Y%m%d-%H%M%S"))
DATA = RUN / "data"
PHOTOS = RUN / "photos"
URL = "http://127.0.0.1:8796"
REPORT = ROOT / "validation" / "reports" / "photo-place-radius-20260913"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def request_json(path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        URL + path,
        data=payload,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed() -> dict[int, float]:
    distances = {index + 1: float(distance) for index, distance in enumerate(range(0, 440, 40))}
    distances[12] = 620.0
    originals: dict[int, str] = {}
    with closing(sqlite3.connect(DATA / "library.sqlite3")) as connection:
        for asset_id, distance in distances.items():
            path = PHOTOS / f"范围照片-{asset_id:02d}.jpg"
            Image.new("RGB", (360 + asset_id * 3, 250 + asset_id * 2), (55 + asset_id * 9, 105, 82)).save(path)
            digest = sha256(path)
            originals[asset_id] = digest
            latitude = 39.9 + distance / 111_132.0
            connection.execute(
                """INSERT INTO assets(id,sha256,width,height,format,metadata,captured_at,date_source,date_precision,
                          latitude,longitude,place,place_source,category,notes,face_state,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (asset_id, digest, 360 + asset_id * 3, 250 + asset_id * 2, "JPEG", "{}",
                 f"2025-01-{asset_id:02d}T10:00:00", "测试", "日", latitude, 116.4,
                 "中国 · 测试附近", "测试", "照片", "", 0, "2026-09-13T12:00:00"),
            )
            stat = path.stat()
            connection.execute(
                "INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded) VALUES (?,?,?,?,?,?,0)",
                (asset_id, str(path), stat.st_size, stat.st_mtime_ns, "2026-09-13T12:00:00", 1),
            )
        connection.commit()
    return {asset_id: distance for asset_id, distance in distances.items()}, originals


def main() -> int:
    RUN.mkdir(parents=True)
    DATA.mkdir()
    PHOTOS.mkdir()
    env = {**os.environ, "PHOTO_LIBRARY_DATA": str(DATA), "PHOTO_WEB_ROOT": str(ROOT / "web")}
    log_path = RUN / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", "8796"],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        for _ in range(160):
            try:
                request_json("/api/status")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            log.flush()
            raise RuntimeError("隔离服务未启动\n" + log_path.read_text(encoding="utf-8", errors="replace"))

        distances, originals = seed()
        map_data = request_json("/api/places?west=116.3&south=39.8&east=116.5&north=40.1&zoom=15.3")
        check(any(item["anchor_id"] == 1 for item in map_data["clusters"]), "地图点返回同一网格内可用于地点编辑的定位照片")
        nearby = request_json("/api/photos/1/nearby?radius_m=500")
        check(nearby["total"] == 11 and len(nearby["samples"]) == 9, "500 米范围返回全部数量并固定九张预览")
        check(all(item["distance_m"] <= 500 for item in nearby["points"]), "范围预览不混入半径外照片")
        sequence = request_json("/api/photos?nearby=1&radius_m=500&sequence=true&limit=200")
        check(sequence["total"] == 11 and len(sequence["ids"]) == 11, "大图序列包含范围内全部照片而不是九张样片")
        try:
            request_json("/api/photos/1/nearby?radius_m=501")
        except urllib.error.HTTPError as error:
            check(error.code == 400, "后端拒绝超过 500 米的预览范围")
        else:
            raise AssertionError("后端接受了超过 500 米的范围")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://**", lambda route: route.abort())
            page.goto(URL, wait_until="domcontentloaded")
            page.wait_for_function("typeof openPhoto === 'function'")
            page.evaluate("openPhoto(1)")
            page.wait_for_selector("#detail-dialog[open]")
            page.locator("#photo-place-map").click()
            page.wait_for_function("state.view === 'photo-place:1'")
            page.wait_for_selector("#places-photo-edit:not([hidden])")
            check(page.locator("#places-help").inner_text() == "", "单张地图不再显示解释性废话")
            check(abs(float(page.locator("#places-zoom").input_value()) - 0.15) < 0.02, "单张地图默认大小调整为 0.15")

            page.locator("#places-photo-edit").click()
            page.wait_for_selector("#photo-place-editor:not([hidden])")
            geometry = page.evaluate("""() => {const values=Object.fromEntries(['photo-place-editor','places-map'].map(id => {const r=document.getElementById(id).getBoundingClientRect();return [id,{top:r.top,bottom:r.bottom,height:r.height}];}));const c=document.querySelector('#places-view .view-chrome').getBoundingClientRect();values.chrome={top:c.top,bottom:c.bottom,height:c.height};return values;}""")
            check(geometry["photo-place-editor"]["top"] >= geometry["places-map"]["top"] + 8, "地点修改面板完整落在地图画布内")
            check(geometry["photo-place-editor"]["top"] >= geometry["chrome"]["bottom"] + 8, "地点修改面板不被固定标题栏遮挡")
            page.locator("#photo-place-radius").fill("500")
            page.wait_for_function("document.querySelector('#photo-place-range-summary').textContent.includes('11 张')")
            check(page.locator("#photo-place-samples .photo-place-sample").count() == 9, "地图范围面板只显示九张样片")
            check(page.evaluate("state.photoMap.rangeLayers.length") == 12, "地图画出半径圈和范围内全部定位点")
            check("照片-01" in page.locator("#photo-place-source-name").inner_text(), "修改面板明确显示定位基准照片")
            REPORT.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(REPORT / "radius-500-preview.png"), full_page=False)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(120)
            check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "390px 地图与地点修改面板不横向溢出")
            check(page.evaluate("document.querySelector('#photo-place-editor').getBoundingClientRect().top >= document.querySelector('#places-view .view-chrome').getBoundingClientRect().bottom + 8"), "窄屏地点修改面板也不被固定标题栏遮挡")
            check(page.locator("#photo-place-samples .photo-place-sample").count() == 9, "窄屏仍保持三乘三的九张预览")
            page.screenshot(path=str(REPORT / "radius-500-preview-390.png"), full_page=False)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(120)

            page.locator("#photo-place-samples .photo-place-sample").nth(4).click()
            page.wait_for_selector("#detail-dialog[open]")
            page.wait_for_function("viewer.total === 11")
            page.wait_for_function("!document.querySelector('#detail-dialog').classList.contains('is-loading')")
            viewer_context = page.locator("#viewer-context").inner_text()
            check("当前位置 500 米内" in viewer_context, f"九宫格进入大图后标明完整范围（实际：{viewer_context}）")
            first_id = page.evaluate("Number(state.detail.id)")
            page.keyboard.press("ArrowRight")
            page.wait_for_function("id => Number(state.detail.id) !== id", arg=first_id)
            check(True, "范围大图可以左右浏览全部候选照片")
            page.keyboard.press("Escape")
            page.wait_for_function("!document.querySelector('#detail-dialog').open")
            check(page.locator("#photo-place-editor").is_visible() and page.evaluate("state.view === 'photo-place:1'"), "关闭大图回到原半径地图和九宫格")

            page.locator("#photo-place-radius").fill("300")
            page.locator("#photo-place-name").fill("测试园区东门")
            page.wait_for_function("document.querySelector('#photo-place-range-summary').textContent.includes('8 张')")
            page.locator("#photo-place-save").click()
            page.wait_for_function("document.querySelector('#toast').textContent.includes('8 张照片')")
            check(page.locator("#photo-place-editor").is_hidden(), "确认后地点面板收起并给出明确反馈")

            page.evaluate("""async () => {
                await setView('places');
                const map = ensurePlaceMap();
                map.setView([39.90123, 116.40156], 15.3, {animate:false});
                await loadPlaceMap();
                await openPlaceDetail('测试园区东门', {id:1, latitude:39.9, longitude:116.4});
            }""")
            page.wait_for_function("state.view === 'place:测试园区东门'")
            check(page.locator("body").evaluate("node => node.classList.contains('is-place-detail-view')"), "地点详情进入精简布局状态")
            check(not page.locator("#library-view .filters").is_visible() and not page.locator("#library-view .directory-filter").is_visible(), "地点详情隐藏批量补录和目录筛选杂项")
            check(page.locator("#place-detail-back").is_visible() and page.locator("#place-detail-edit").is_visible(), "地点标题同时提供返回地图和修改地点入口")
            check(page.locator("#place-refine").count() == 0 and "地图里没有" not in page.locator("body").inner_text(), "旧地点输入框和无效提示已移除")
            page.screenshot(path=str(REPORT / "place-detail-compact.png"), full_page=False)
            page.set_viewport_size({"width": 1440, "height": 500})
            page.evaluate("scrollTo(0, 360)")
            page.wait_for_timeout(80)
            masthead_top = page.locator(".masthead").bounding_box()["y"]
            check(abs(masthead_top - 60) < 1 and page.locator("#page-title").is_visible(), "地点照片向上滚动时标题和操作仍固定可见")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.evaluate("scrollTo(0, 0)")
            page.locator("#place-detail-edit").click()
            page.wait_for_function("state.view === 'photo-place:1'")
            page.wait_for_selector("#photo-place-editor:not([hidden])")
            check(page.locator("#places-photo-back").inner_text() == "返回地点照片", "地点范围编辑明确返回地点照片，而不是误入大图")
            page.locator("#places-photo-back").click()
            page.wait_for_function("state.view === 'place:测试园区东门'")
            page.locator("#place-detail-back").click()
            page.wait_for_function("state.view === 'places'")
            restored = page.evaluate("""() => {const center=state.placeMap.getCenter();return {latitude:center.lat,longitude:center.lng,zoom:state.placeMap.getZoom()};}""")
            check(abs(restored["latitude"] - 39.90123) < 0.00001 and abs(restored["longitude"] - 116.40156) < 0.00001 and abs(restored["zoom"] - 15.3) < 0.01, "返回地点地图后精确恢复进入前的中心与缩放")
            page.screenshot(path=str(REPORT / "place-map-restored.png"), full_page=False)
            check(not errors, "完整地图地点链路没有 JavaScript 运行错误")
            browser.close()

        with closing(sqlite3.connect(DATA / "library.sqlite3")) as connection:
            changed = connection.execute("SELECT id,manual_place FROM assets ORDER BY id").fetchall()
            history = connection.execute("SELECT count(*) FROM edits WHERE after_json LIKE '%radius_m%'").fetchone()[0]
        expected = {asset_id for asset_id, distance in distances.items() if distance <= 300}
        actual = {asset_id for asset_id, place in changed if place == "测试园区东门"}
        check(actual == expected and len(actual) == 8, "保存只修改 300 米范围内的八张照片")
        check(history == 8, "每张受影响照片都保留可追查的修改记录")
        check(all(sha256(PHOTOS / f"范围照片-{asset_id:02d}.jpg") == digest for asset_id, digest in originals.items()), "原照片内容和 GPS 文件均未改写")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        if RUN.exists():
            for attempt in range(12):
                try:
                    shutil.rmtree(RUN)
                    break
                except PermissionError:
                    if attempt == 11:
                        raise
                    time.sleep(0.25)


if __name__ == "__main__":
    raise SystemExit(main())
