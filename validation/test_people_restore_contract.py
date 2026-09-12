"""Isolated API and real-browser regression for people/passersby restore flows."""
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

RUN = ROOT / "validation" / "work" / ("people-restore-" + time.strftime("%Y%m%d-%H%M%S"))
DATA = RUN / "data"
WEB = RUN / "web"
PHOTOS = RUN / "photos"
URL = "http://127.0.0.1:8791"
REPORT = ROOT / "validation" / "reports" / "people-restore-contract.json"
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


def add_asset(conn: sqlite3.Connection, aid: int, excluded_file: bool = False) -> Path:
    path = PHOTOS / f"照片-{aid}.jpg"
    Image.new("RGB", (640, 420), (40 + aid * 17, 80, 110)).save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    conn.execute(
        """INSERT INTO assets(id,sha256,width,height,format,metadata,captured_at,date_source,
                              date_precision,category,created_at,face_state)
           VALUES(?,?,?,?,?,'{}',?,'EXIF','日','照片',?,1)""",
        (aid, sha, 640, 420, "JPEG", f"2026-09-{aid:02}T10:00:00", "2026-09-12T10:00:00"),
    )
    conn.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES(?,?,?,?,?,1,?)""",
        (aid, str(path), path.stat().st_size, path.stat().st_mtime_ns, "2026-09-12T10:00:00", int(excluded_file)),
    )
    return path


def add_person(conn: sqlite3.Connection, pid: int, name, confirmed: int, ignored: int, assets: list[int]) -> list[int]:
    conn.execute(
        "INSERT INTO people(id,name,alias,confirmed,ignored) VALUES(?,?,?,?,?)",
        (pid, name, "", confirmed, ignored),
    )
    face_ids = []
    for aid in assets:
        fid = pid * 100 + aid
        conn.execute(
            """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed,ignored)
               VALUES(?,?,?,'[10,10,110,110,640,420]',?,0.95,?,?)""",
            (fid, aid, pid, b"\0" * 2048, confirmed, ignored),
        )
        face_ids.append(fid)
    return face_ids


def seed() -> dict[str, int]:
    DATA.mkdir(parents=True)
    PHOTOS.mkdir(parents=True)
    shutil.copytree(ROOT / "web", WEB)
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        init_schema(conn)
        for aid in range(1, 10):
            add_asset(conn, aid, excluded_file=aid in {2, 4, 8})
        faces = {
            "passerby_mixed": add_person(conn, 1, "路人", 0, 1, [1, 2]),
            "passerby_inactive": add_person(conn, 2, "路人", 0, 1, [8]),
            "named_mixed": add_person(conn, 3, "小明", 1, 0, [3, 4]),
            "named_roundtrip": add_person(conn, 4, "小红", 1, 0, [5]),
            "face_restore": add_person(conn, 5, "路人", 0, 1, [6]),
            "split_restore": add_person(conn, 6, "路人", 0, 1, [7]),
            "browser_restore": add_person(conn, 7, "路人", 0, 1, [9]),
            "browser_name_feedback": add_person(conn, 8, None, 0, 0, [9]),
        }
        conn.commit()
    (DATA / "faces").mkdir()
    for face_ids in faces.values():
        for fid in face_ids:
            Image.new("RGB", (110, 110), "#789181").save(DATA / "faces" / f"{fid}.jpg")
    return {key: value[0] for key, value in faces.items()}


def main() -> int:
    face = seed()
    env = {**os.environ, "PHOTO_LIBRARY_DATA": str(DATA), "PHOTO_WEB_ROOT": str(WEB)}
    log_path = RUN / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", "8791"],
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result = {"passed": False, "checks": checks, "run": str(RUN)}
    try:
        ready = False
        for _ in range(120):
            try:
                req("/api/status")
                ready = True
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        if not ready:
            log.flush()
            raise RuntimeError("隔离服务未启动：\n" + log_path.read_text(encoding="utf-8", errors="replace")[-4000:])

        passersby = req("/api/people?ignored=1&limit=20")
        p1 = next(item for item in passersby["items"] if item["id"] == 1)
        check(not any(item["id"] == 2 for item in passersby["items"]), "全为已排除照片的路人组不出现在路人列表")
        check((p1["face_count"], p1["photo_count"], p1["cover"]) == (1, 1, face["passerby_mixed"]), "路人卡片数量和封面只使用有效照片")

        named = req("/api/people?ignored=0&limit=20")
        p3 = next(item for item in named["items"] if item["id"] == 3)
        check((p3["face_count"], p3["photo_count"], p3["cover"]) == (1, 1, face["named_mixed"]), "普通人物卡片也只使用有效照片")
        detail = req("/api/people/3?limit=48")
        check([item["asset_id"] for item in detail["faces"]] == [3] and detail["face_count"] == 1, "人物详情不返回已排除照片中的脸")
        check(req("/api/photos?person=3&sequence=true")["ids"] == [3], "人物照片序列与人物详情使用同一有效范围")

        req("/api/people/1/ignore", {"ignored": False}, "POST")
        restored = req("/api/people/1?limit=48")
        check(not restored["ignored"] and restored["name"] in (None, ""), "系统生成的路人组恢复为未命名待识别人物")

        req("/api/people/4/ignore", {"ignored": True}, "POST")
        req("/api/people/4/ignore", {"ignored": False}, "POST")
        roundtrip = req("/api/people/4?limit=48")
        check(roundtrip["name"] == "小红" and roundtrip["confirmed"] == 1, "已有姓名的人标为路人再恢复时保留姓名和确认状态")

        restored_face = req(f"/api/faces/{face['face_restore']}/ignore", {"ignored": False}, "POST")
        single = req(f"/api/people/{restored_face['person_id']}?limit=48")
        check(single["name"] in (None, "") and single["confirmed"] == 0 and not single["ignored"], "单张恢复创建独立的未命名人物组")

        split = req(f"/api/faces/{face['split_restore']}/split", {}, "POST")
        with sqlite3.connect(DATA / "library.sqlite3") as conn:
            moved = conn.execute("SELECT ignored,reviewed FROM faces WHERE id=?", (face["split_restore"],)).fetchone()
        check(tuple(moved) == (0, 0), "从路人组移出单张脸时同步恢复识别状态")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(URL, wait_until="domcontentloaded")
            page.evaluate("setView('people')")
            page.wait_for_selector('#people-grid [data-open-person="8"]')
            page.locator('#people-grid [data-open-person="8"]').click()
            page.wait_for_selector("#person-dialog[open]")
            check(page.locator("#person-save-state").is_hidden(), "未命名人物打开详情时不显示虚假的已保存状态")
            page.locator("#person-name").fill("测试姓名")
            page.locator('#person-form button[type="submit"]').click()
            page.wait_for_function("document.querySelector('#person-save-state').textContent === '已标记为：测试姓名'")
            check(page.locator("#person-dialog").get_attribute("open") is not None, "确认姓名后人物详情保持打开供用户核对")
            status_box = page.locator("#person-save-state").bounding_box()
            button_box = page.locator('#person-form button[type="submit"]').bounding_box()
            check(
                bool(status_box and button_box and status_box["x"] >= button_box["x"] + button_box["width"]),
                "姓名保存结果显示在确认按钮右侧",
            )
            check(req("/api/people/8?limit=48")["name"] == "测试姓名", "右侧成功反馈对应隔离数据库中的真实姓名")
            page.screenshot(path=str(RUN / "person-name-feedback.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            check(
                not page.evaluate("document.querySelector('#person-dialog').scrollWidth > document.querySelector('#person-dialog').clientWidth"),
                "390px 人物详情姓名反馈没有横向溢出",
            )
            page.set_viewport_size({"width": 1440, "height": 960})
            page.locator("#person-name").fill("尚未再次保存")
            check(page.locator("#person-save-state").is_hidden(), "继续编辑姓名时清除旧成功状态，避免误认已保存")
            page.locator("#person-dialog").evaluate("dialog => dialog.close()")

            page.evaluate("setView('passersby')")
            page.wait_for_selector('#passersby-grid [data-person="7"]')
            card = page.locator('#passersby-grid [data-person="7"]')
            check(card.locator('[data-restore-person="7"]').is_visible(), "路人卡片有直接恢复到人物档案入口")
            card.locator('[data-restore-person="7"]').click()
            page.wait_for_selector('#passersby-grid [data-person="7"]', state="detached")
            check(req("/api/people/7?limit=48")["ignored"] == 0, "路人卡片恢复实际写入隔离数据库并原地移除卡片")

            for width in (760, 390):
                page.set_viewport_size({"width": width, "height": 844})
                page.evaluate("setView('passersby')")
                page.wait_for_selector("#passersby-view:not([hidden])")
                check(not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"), f"{width}px 路人页没有横向溢出")

            page.set_viewport_size({"width": 1440, "height": 960})
            awaitable = page.evaluate("openPhoto(3,{q:'',filter:'all',person:'3',directory:'',sort:'date_desc'})")
            page.wait_for_selector("#detail-dialog[open]")
            page.wait_for_function("document.querySelector('#detail-img').naturalWidth>0")
            page.keyboard.press("Escape")
            page.route("**/api/original/5", lambda route: route.abort())
            page.route("**/api/preview/5*", lambda route: route.abort())
            page.evaluate("openPhoto(5,{q:'',filter:'all',person:'4',directory:'',sort:'date_desc'})")
            page.wait_for_function(
                "document.querySelector('#viewer-message').textContent.includes('暂时无法显示')",
                timeout=5000,
            )
            check(page.locator("#detail-img").is_hidden() or not page.locator("#detail-img").get_attribute("src"), "关闭后打开失败照片不会残留上一张图")
            check("暂时无法显示" in page.locator("#viewer-message").inner_text(), "图片失败时给出当前照片的明确提示")
            check(not errors, "真实 Chrome 路人恢复与失败图片路径无 JavaScript 错误")
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
