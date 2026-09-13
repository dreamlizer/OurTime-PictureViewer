"""Isolated API and browser regression for photo-level people interactions."""
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
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library_db import init_schema


RUN = ROOT / "validation" / "work" / ("photo-people-" + time.strftime("%Y%m%d-%H%M%S"))
DATA = RUN / "data"
PHOTOS = RUN / "photos"
URL = "http://127.0.0.1:8793"
REPORT = ROOT / "validation" / "reports" / "photo-people-interactions.json"
checks: list[str] = []


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def req(path: str, body=None, method: str | None = None):
    request = urllib.request.Request(
        URL + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def add_asset(conn: sqlite3.Connection, aid: int) -> None:
    path = PHOTOS / f"合影-{aid}.jpg"
    Image.new("RGB", (1000, 700), (105 + aid * 12, 91, 72)).save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    conn.execute(
        """INSERT INTO assets(id,sha256,width,height,format,metadata,captured_at,date_source,
                              date_precision,category,created_at,face_state)
           VALUES(?,?,?,?,?,'{}',?,'EXIF','日','照片',?,1)""",
        (aid, sha, 1000, 700, "JPEG", f"2026-09-{aid:02}T12:00:00", "2026-09-13T12:00:00"),
    )
    conn.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES(?,?,?,?,?,1,0)""",
        (aid, str(path), path.stat().st_size, path.stat().st_mtime_ns, "2026-09-13T12:00:00"),
    )


def add_person(conn: sqlite3.Connection, pid: int, name, confirmed: int, ignored: int, face_specs, alias="") -> None:
    conn.execute("INSERT INTO people(id,name,alias,confirmed,ignored) VALUES(?,?,?,?,?)", (pid, name, alias, confirmed, ignored))
    for fid, aid, bbox in face_specs:
        conn.execute(
            """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed,ignored)
               VALUES(?,?,?,?,?,0.96,?,?)""",
            (fid, aid, pid, json.dumps([*bbox, 1000, 700]), b"\0" * 2048, confirmed, ignored),
        )


def seed() -> None:
    DATA.mkdir(parents=True)
    PHOTOS.mkdir(parents=True)
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        init_schema(conn)
        add_asset(conn, 1)
        add_asset(conn, 2)
        add_person(conn, 1, "郑婷", 1, 0, [(101, 1, (180, 180, 270, 300)), (102, 2, (280, 180, 370, 300))])
        add_person(conn, 2, None, 0, 0, [(201, 1, (400, 170, 490, 295)), (202, 2, (480, 180, 570, 300))])
        add_person(conn, 3, "", 0, 0, [(301, 1, (610, 175, 700, 300))])
        add_person(conn, 4, "路人", 0, 1, [(401, 1, (770, 180, 860, 305))])
        add_person(conn, 5, "", 0, 0, [(501, 1, (510, 390, 600, 515))], alias="邻居")
        conn.commit()
    (DATA / "faces").mkdir()
    (DATA / "thumbs").mkdir()
    for fid in (101, 102, 201, 202, 301, 401, 501):
        Image.new("RGB", (100, 120), "#859889").save(DATA / "faces" / f"{fid}.jpg")


def person_state(pid: int):
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        return conn.execute("SELECT name,confirmed,ignored FROM people WHERE id=?", (pid,)).fetchone()


def face_state(fid: int):
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        return conn.execute("SELECT person_id,ignored FROM faces WHERE id=?", (fid,)).fetchone()


def favorite_state(aid: int) -> int:
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        return int(conn.execute("SELECT favorite FROM assets WHERE id=?", (aid,)).fetchone()[0])


def main() -> int:
    seed()
    env = {**os.environ, "PHOTO_LIBRARY_DATA": str(DATA), "PHOTO_WEB_ROOT": str(ROOT / "web")}
    log_path = RUN / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", "8793"],
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result = {"passed": False, "checks": checks, "run": str(RUN)}
    try:
        for _ in range(160):
            try:
                req("/api/status")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            log.flush()
            raise RuntimeError("隔离服务未启动\n" + log_path.read_text(encoding="utf-8", errors="replace"))

        marked = req("/api/photos/1/passersby", {"ignored": True}, "POST")
        check(set(marked["person_ids"]) == {2, 3}, "批量接口只选中当前照片里仍未命名的人物")
        check(person_state(1)[2] == 0 and person_state(4)[2] == 1 and person_state(5)[2] == 0, "批量接口保留已命名、已有别名人物和原有路人")
        check(face_state(202)[1] == 1, "批量归入路人沿用人物组语义并同步同组其他照片")
        restored = req("/api/photos/1/passersby", {"ignored": False, "person_ids": marked["person_ids"]}, "POST")
        check(set(restored["person_ids"]) == {2, 3} and face_state(202)[1] == 0, "批量操作可按本次人物清单完整撤销")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 920}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(URL, wait_until="domcontentloaded")
            page.evaluate("openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})")
            page.wait_for_selector("#detail-dialog[open]")
            page.wait_for_function("document.querySelector('#detail-img').naturalWidth>0")
            page.wait_for_selector('#face-name-layer [data-face-id="101"]')

            viewer_controls = page.evaluate(
                """() => {
                  const stage=document.querySelector('.viewer-stage').getBoundingClientRect();
                  const previous=document.querySelector('#viewer-prev').getBoundingClientRect();
                  const next=document.querySelector('#viewer-next').getBoundingClientRect();
                  const tools=document.querySelector('.viewer-tools');
                  const toolsBox=tools.getBoundingClientRect();
                  const firstGroup=document.querySelector('.viewer-tool-group');
                  const favorite=document.querySelector('#photo-favorite');
                  const alpha=value=>{const match=String(value).match(/rgba?\\([^,]+,[^,]+,[^,]+(?:,\\s*([\\d.]+))?\\)/);return match&&match[1]!==undefined?Number(match[1]):1;};
                  const arrowStyle=getComputedStyle(document.querySelector('#viewer-next'));
                  const favoriteStyle=getComputedStyle(favorite);
                  return {
                    previousInset:previous.left-stage.left,
                    nextInset:stage.right-next.right,
                    toolsInset:stage.right-toolsBox.right,
                    toolsGap:parseFloat(getComputedStyle(tools).gap),
                    groupGap:parseFloat(getComputedStyle(firstGroup).gap),
                    arrowAlpha:alpha(arrowStyle.backgroundColor),
                    arrowOpacity:parseFloat(arrowStyle.opacity),
                    favoriteAlpha:alpha(favoriteStyle.backgroundColor),
                    favoriteTextAlpha:alpha(favoriteStyle.color)
                  };
                }"""
            )
            check(viewer_controls["previousInset"] >= 45 and viewer_controls["nextInset"] >= 85, "左右翻页箭头均向照片内侧收进约一个按钮宽度")
            check(viewer_controls["toolsInset"] >= 28, "右侧工具条离开详情页边缘至少半个按钮宽度")
            check(viewer_controls["toolsGap"] <= 5 and viewer_controls["groupGap"] <= 1, "右侧工具条组间和按钮间距已收紧")
            check(viewer_controls["arrowAlpha"] <= .32 and viewer_controls["arrowOpacity"] <= .7, "翻页箭头使用更轻、更透明的承载层")
            check(viewer_controls["favoriteAlpha"] <= .3 and viewer_controls["favoriteTextAlpha"] <= .65, "未收藏按钮降低背景和文字存在感")
            page.screenshot(path=str(RUN / "viewer-controls-muted.png"), full_page=False)

            favorite_geometry = page.locator("#photo-favorite").evaluate(
                """button => {const image=document.querySelector('#detail-img').getBoundingClientRect();const signature=document.querySelector('#photo-signature').getBoundingClientRect();const box=button.getBoundingClientRect();return {left:box.left>=image.left-1&&box.left<image.left+140,bottom:box.bottom<=image.bottom-8&&box.bottom<signature.top};}"""
            )
            check(favorite_geometry["left"] and favorite_geometry["bottom"], "收藏印章位于照片左下角、原始数据栏上方")
            check(page.locator("#photo-favorite").get_attribute("aria-pressed") == "false", "未收藏照片显示安静的收藏状态")
            page.locator("#photo-favorite").click()
            page.wait_for_function("document.querySelector('#photo-favorite').getAttribute('aria-pressed')==='true'")
            check(favorite_state(1) == 1 and page.locator("#favorites-count").inner_text() == "1", "收藏立即写入资料库并更新左侧数量")
            page.screenshot(path=str(RUN / "photo-favorite-active.png"), full_page=False)
            page.locator("#photo-mat > .viewer-photo-close").click()
            page.wait_for_selector("#detail-dialog", state="hidden")
            page.locator('[data-view="favorites"]').click()
            page.wait_for_selector('#photo-grid [data-photo="1"]')
            check(page.locator("#page-title").inner_text() == "收藏的照片", "左侧收藏入口打开独立收藏照片流")
            page.locator('#photo-grid [data-photo="1"]').click()
            page.wait_for_selector("#detail-dialog[open]")
            page.wait_for_function("document.querySelector('#photo-favorite').getAttribute('aria-pressed')==='true'")
            page.locator("#photo-favorite").click()
            page.wait_for_function("document.querySelector('#photo-favorite').getAttribute('aria-pressed')==='false'")
            page.locator("#photo-mat > .viewer-photo-close").click()
            page.wait_for_selector("#detail-dialog", state="hidden")
            page.wait_for_function("document.querySelectorAll('#photo-grid [data-photo]').length===0")
            check(favorite_state(1) == 0 and "还没有收藏照片" in page.locator("#no-results").inner_text(), "取消收藏后资料库和收藏页同时更新")
            page.locator('[data-view="timeline"]').click()
            page.evaluate("openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})")
            page.wait_for_selector("#detail-dialog[open]")
            page.wait_for_function("document.querySelector('#detail-img').naturalWidth>0")
            page.wait_for_selector('#face-name-layer [data-face-id="101"]')

            page.locator('[data-face-id="201"]').click()
            page.wait_for_selector("#quick-name-dialog[open]")
            check(page.locator('#quick-name-dialog [data-close="quick-name-dialog"]').count() == 1, "快速命名只保留右上角关闭入口")
            check(page.locator("#quick-name-confirm").inner_text() == "确认姓名", "姓名输入区下面使用含义明确的确认姓名按钮")
            check(page.locator("#quick-merge-title").inner_text() == "合并到已有姓名" and page.locator("#quick-merge-panel").is_visible(), "命名与合并使用同级标题，合并区默认直接展开")
            check(page.locator("#quick-name-dialog").evaluate("dialog => dialog.getBoundingClientRect().width <= 360"), "快捷命名弹窗收窄到紧凑宽度")
            page.wait_for_function("!document.querySelector('#quick-merge-target').disabled")
            quick_targets = page.locator("#quick-merge-target option").evaluate_all(
                "options => options.map(option => option.value).filter(Boolean)"
            )
            check(quick_targets == ["1"], "快捷合并候选只包含已命名人物，不混入待命名分组或路人")
            action_layout = page.evaluate(
                """() => {
                  const confirm=document.querySelector('#quick-name-confirm').getBoundingClientRect();
                  const mergeTitle=document.querySelector('#quick-merge-title').getBoundingClientRect();
                  const merge=document.querySelector('#quick-merge-submit').getBoundingClientRect();
                  const ignore=document.querySelector('#quick-ignore-person').getBoundingClientRect();
                  return {confirm,mergeTitle,merge,ignore};
                }"""
            )
            check(
                abs(action_layout["confirm"]["width"] - action_layout["merge"]["width"]) < 1
                and abs(action_layout["confirm"]["height"] - action_layout["merge"]["height"]) < 1,
                "确认姓名与合并按钮同宽同高、视觉层级平等",
            )
            check(
                action_layout["confirm"]["bottom"] < action_layout["mergeTitle"]["top"]
                and action_layout["mergeTitle"]["bottom"] < action_layout["merge"]["top"]
                and action_layout["merge"]["bottom"] < action_layout["ignore"]["top"],
                "命名、合并、路人三个动作按清楚的上下层级排列",
            )
            page.screenshot(path=str(RUN / "quick-name-actions.png"), full_page=False)
            page.locator('#quick-name-dialog [data-close="quick-name-dialog"]').click()
            page.wait_for_selector("#quick-name-dialog", state="hidden")

            page.locator('[data-face-id="401"]').click()
            page.wait_for_selector("#quick-name-dialog[open]")
            page.wait_for_function("!document.querySelector('#quick-merge-target').disabled")
            passerby_targets = page.locator("#quick-merge-target option").evaluate_all(
                "options => options.map(option => option.value).filter(Boolean)"
            )
            check(passerby_targets == ["1"], "从路人标记进入快捷命名时，合并候选仍只读取已命名人物")
            page.locator('#quick-name-dialog [data-close="quick-name-dialog"]').click()
            page.wait_for_selector("#quick-name-dialog", state="hidden")

            auto_side = page.locator('[data-face-id="101"]').evaluate("element => [...element.classList].find(x=>['left','right','top','bottom'].includes(x))")
            check(auto_side in {"left", "right"}, "竖排标签的原有自动算法仍选择左右侧")
            page.click("#face-style-button")
            page.wait_for_selector("#face-style-popover:not([hidden])")
            page.select_option("#face-label-position", "top")
            page.wait_for_function("document.querySelector('[data-face-id=\"101\"]').classList.contains('top')")
            top_geometry = page.locator('[data-face-id="101"]').evaluate(
                """element => {const face=state.detail.faces.find(x=>x.id===101),box=faceBox(face),img=document.querySelector('#detail-img').getBoundingClientRect(),layer=document.querySelector('#face-name-layer').getBoundingClientRect(),label=element.getBoundingClientRect();const faceTop=img.top+box.y1/box.h*img.height;return label.bottom<=faceTop+1;}"""
            )
            check(top_geometry, "“优先上方”把标签放到人脸上方")
            page.select_option("#face-label-position", "bottom")
            page.wait_for_function("document.querySelector('[data-face-id=\"101\"]').classList.contains('bottom')")
            bottom_geometry = page.locator('[data-face-id="101"]').evaluate(
                """element => {const face=state.detail.faces.find(x=>x.id===101),box=faceBox(face),img=document.querySelector('#detail-img').getBoundingClientRect(),label=element.getBoundingClientRect();const faceBottom=img.top+(box.y1+box.height)/box.h*img.height;return label.top>=faceBottom-1;}"""
            )
            check(bottom_geometry, "“优先下方”把标签放到人脸下方")
            page.select_option("#face-label-position", "auto")

            named = page.locator('[data-face-id="101"]')
            named.hover()
            check(page.locator("#face-name-layer").evaluate("element => element.classList.contains('is-linking')"), "悬停姓名标签会进入人物指向状态")
            page.wait_for_function("Number(getComputedStyle(document.querySelector('.face-hover-box')).opacity)>.9")
            check(page.locator(".face-hover-box").evaluate("element => Number(getComputedStyle(element).opacity) > .9"), "人物指向状态显示对应脸框")
            check(bool(page.locator(".face-hover-guide path").get_attribute("d")), "人物标签与脸框之间显示引导线")
            page.screenshot(path=str(RUN / "face-hover-guide.png"), full_page=False)

            named.click()
            check(page.locator("#face-action-popover").is_visible(), "点击已命名标签会打开单张纠错卡")
            page.screenshot(path=str(RUN / "face-action-popover.png"), full_page=False)
            page.click("#face-action-split")
            page.wait_for_function("document.querySelector('[data-face-id=\"101\"]').classList.contains('unnamed')")
            split_person = face_state(101)[0]
            check(split_person != 1 and face_state(102)[0] == 1, "单张认错移出只拆出当前脸，其他照片仍属于原人物")

            page.click("#photo-people-manage")
            check(page.locator("#photo-people-popover").is_visible(), "大图页工具栏提供本照片人物整理入口")
            page.click("#photo-passersby-start")
            copy = page.locator("#photo-passersby-copy").inner_text()
            check("保留 1 位已命名人物" in copy and "3 位待命名者" in copy, "批量确认明确说明保留与处理人数")
            page.click("#photo-passersby-confirm-button")
            page.wait_for_function("!document.querySelector('#photo-passersby-undo').hidden")
            check(page.locator("#photo-passersby-undo").is_visible(), "批量处理完成后立即提供撤销入口")
            page.click("#photo-passersby-undo")
            page.wait_for_function("document.querySelector('#photo-passersby-undo').hidden")
            check(face_state(101)[1] == 0, "界面撤销真实恢复本次批量处理的人物")
            page.screenshot(path=str(RUN / "photo-people-interactions.png"), full_page=False)
            page.set_viewport_size({"width": 390, "height": 844})
            compact_box = page.locator("#photo-people-popover").bounding_box()
            check(
                bool(compact_box and compact_box["x"] >= 0 and compact_box["x"] + compact_box["width"] <= 390),
                "390px 窄屏下人物整理卡完整留在可视区",
            )

            page.set_viewport_size({"width": 1440, "height": 920})
            page.evaluate("document.querySelector('#detail-dialog').close()")
            page.evaluate("window.__ourTimeApp.setView('timeline')")
            page.wait_for_selector("#photo-grid [data-photo]")
            check(page.locator("[data-group-exclude-arm]").count() == 0, "普通照片页不显示合影快捷排除按钮")

            page.evaluate("window.__ourTimeApp.setView('group:5')")
            page.wait_for_selector('#photo-grid [data-photo="1"] [data-group-exclude-arm]')
            page.wait_for_function('document.querySelector(\'[data-photo="1"] img\').naturalWidth > 0')
            check(page.locator('[data-photo="1"] [data-group-exclude-arm]').is_visible(), "合影详情每张照片右下角显示淡色排除按钮")
            page.click('[data-photo="1"] [data-group-exclude-arm]')
            check(page.locator('[data-photo="1"] .group-exclude-confirm').is_visible(), "第一次点击只展开确认和取消，不立即排除")
            check(not page.locator("#detail-dialog").get_attribute("open"), "点击快捷排除不会误打开照片详情")
            page.click('[data-photo="1"] [data-group-exclude-cancel]')
            check(page.locator('[data-photo="1"] .group-exclude-confirm').is_hidden(), "取消后回到淡色排除按钮")

            with sqlite3.connect(DATA / "library.sqlite3") as conn:
                sha = conn.execute("SELECT sha256 FROM assets WHERE id=1").fetchone()[0]
                faces_before = conn.execute("SELECT count(*) FROM faces WHERE asset_id=1").fetchone()[0]
            thumb = DATA / "thumbs" / f"{sha}.jpg"
            check(thumb.is_file() and (DATA / "faces" / "101.jpg").is_file(), "确认前缩略图和人脸识别缓存存在")
            page.screenshot(path=str(RUN / "group-quick-exclude-rest.png"), full_page=False)
            page.click('[data-photo="1"] [data-group-exclude-arm]')
            page.screenshot(path=str(RUN / "group-quick-exclude-confirm.png"), full_page=False)
            page.click('[data-photo="1"] [data-group-exclude-confirm]')
            page.wait_for_function('!document.querySelector(\'[data-photo="1"]\') && waterfall.pending.size === 0')
            with sqlite3.connect(DATA / "library.sqlite3") as conn:
                excluded = conn.execute("SELECT excluded,exclude_reason FROM assets WHERE id=1").fetchone()
                faces_after = conn.execute("SELECT count(*) FROM faces WHERE asset_id=1").fetchone()[0]
            check(excluded == (1, "在合影页排除显示"), "确认后照片退出正常展示并记住合影页排除原因")
            check(faces_after == faces_before and thumb.is_file() and (DATA / "faces" / "101.jpg").is_file(), "仅排除显示不会删除缩略图或人脸识别信息")
            check((PHOTOS / "合影-1.jpg").is_file(), "仅排除显示不会删除原照片")
            excluded_page = req("/api/photos?filter=excluded&limit=24")
            check(any(item["id"] == 1 for item in excluded_page["items"]), "被排除照片仍可在已排除页面找到并恢复")
            restored_display = req("/api/exclusions/assets", {"ids": [1], "excluded": False}, "POST")
            with sqlite3.connect(DATA / "library.sqlite3") as conn:
                restored_asset = conn.execute("SELECT excluded FROM assets WHERE id=1").fetchone()[0]
                restored_faces = conn.execute("SELECT count(*) FROM faces WHERE asset_id=1").fetchone()[0]
            check(
                restored_asset == 0 and restored_faces == faces_before and thumb.is_file()
                and "已恢复显示" in restored_display["message"],
                "从已排除恢复后立即回到正常展示且不需要重做人脸识别",
            )

            with sqlite3.connect(DATA / "library.sqlite3") as conn:
                sha_two = conn.execute("SELECT sha256 FROM assets WHERE id=2").fetchone()[0]
                normal_faces = [row[0] for row in conn.execute("SELECT id FROM faces WHERE asset_id=2").fetchall()]
            normal_thumb = DATA / "thumbs" / f"{sha_two}.jpg"
            Image.new("RGB", (80, 60), "#887766").save(normal_thumb)
            normal_result = req("/api/exclusions/assets", {"ids": [2], "excluded": True, "reason": "普通排除回归"}, "POST")
            with sqlite3.connect(DATA / "library.sqlite3") as conn:
                normal_faces_after = conn.execute("SELECT count(*) FROM faces WHERE asset_id=2").fetchone()[0]
            check(
                not normal_result["display_only"] and normal_result["released_bytes"] > 0
                and not normal_thumb.exists() and normal_faces_after == 0
                and all(not (DATA / "faces" / f"{fid}.jpg").exists() for fid in normal_faces),
                "原有普通排除仍会清理派生缓存，没有被仅显示模式改变",
            )
            check(not errors, "真实浏览器交互没有 JavaScript 错误")
            browser.close()

        result["passed"] = True
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "checks": len(checks), "run": str(RUN)}, ensure_ascii=False))
        return 0
    finally:
        result["checks"] = checks
        if not result["passed"]:
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
