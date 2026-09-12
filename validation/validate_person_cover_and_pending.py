"""Isolated API and real-browser closure for fixed person covers and pending jump."""
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

RUN = ROOT / "validation" / "work" / ("person-cover-" + str(time.time_ns()))
DATA = RUN / "data"
WEB = RUN / "web"
PHOTOS = RUN / "photos"
URL = "http://127.0.0.1:8792"
REPORT = ROOT / "validation" / "reports" / "person-cover-and-pending.json"
SCREENSHOT = ROOT / "validation" / "reports" / "person-cover-selected.png"
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
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def add_face(conn: sqlite3.Connection, aid: int, fid: int, pid: int, color: tuple[int, int, int]) -> None:
    path = PHOTOS / f"photo-{aid}.jpg"
    Image.new("RGB", (480, 360), color).save(path)
    digest = hashlib.sha256(str(aid).encode("ascii") + path.read_bytes()).hexdigest()
    conn.execute(
        """INSERT INTO assets(id,sha256,width,height,format,metadata,captured_at,date_source,
                              date_precision,category,created_at,face_state)
           VALUES(?,?,?,?,?,'{}',?,'EXIF','日','照片',?,1)""",
        (aid, digest, 480, 360, "JPEG", f"2026-09-{(aid % 28) + 1:02}T10:00:00", "2026-09-12T10:00:00"),
    )
    conn.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES(?,?,?,?,?,1,0)""",
        (aid, str(path), path.stat().st_size, path.stat().st_mtime_ns, "2026-09-12T10:00:00"),
    )
    conn.execute(
        """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed,ignored)
           VALUES(?,?,?,'[80,45,240,250,480,360]',?,0.95,1,0)""",
        (fid, aid, pid, b"\0" * 2048),
    )
    Image.new("RGB", (160, 160), color).save(DATA / "faces" / f"{fid}.jpg")


def seed() -> None:
    DATA.mkdir(parents=True)
    (DATA / "faces").mkdir()
    PHOTOS.mkdir(parents=True)
    shutil.copytree(ROOT / "web", WEB)
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        init_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(people)")}
        check("cover_face_id" in columns, "旧资料库启动时会自动增加固定头像字段")
        conn.execute("INSERT INTO people(id,name,alias,confirmed,ignored) VALUES(1,'测试人物','',1,0)")
        add_face(conn, 1, 101, 1, (96, 126, 112))
        add_face(conn, 2, 102, 1, (168, 126, 91))
        for pid in range(2, 57):
            conn.execute(
                "INSERT INTO people(id,name,alias,confirmed,ignored) VALUES(?,?,?,?,0)",
                (pid, f"已命名{pid:02}", "", 1),
            )
            add_face(conn, pid + 1, 1000 + pid, pid, (80 + pid % 80, 105, 125))
        for pid in (90, 91):
            conn.execute("INSERT INTO people(id,name,alias,confirmed,ignored) VALUES(?,NULL,'',0,0)", (pid,))
            add_face(conn, pid + 1, 2000 + pid, pid, (112, 135, 105))
        conn.commit()


def main() -> int:
    seed()
    env = {**os.environ, "PHOTO_LIBRARY_DATA": str(DATA), "PHOTO_WEB_ROOT": str(WEB)}
    log_path = RUN / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", "8792"],
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
                status, _ = req("/api/status")
                ready = status == 200
                if ready:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        if not ready:
            log.flush()
            raise RuntimeError("隔离服务未启动：\n" + log_path.read_text(encoding="utf-8", errors="replace")[-4000:])

        status, initial = req("/api/people/1?limit=48")
        check(status == 200 and initial["cover"] == 101, "未人工选择时仍沿用原来的自动头像")
        status, chosen = req("/api/people/1/cover", {"face_id": 102}, "PUT")
        check(status == 200 and chosen["cover"] == 102, "已命名人物可以把指定人脸设为首页头像")
        status, detail = req("/api/people/1?limit=48")
        check(status == 200 and detail["cover"] == 102 and detail["cover_face_id"] == 102, "固定头像写入数据库并在详情接口返回")
        status, listing = req("/api/people?limit=1")
        check(status == 200 and listing["items"][0]["cover"] == 102, "人物档案卡片使用同一个固定头像")
        status, _ = req("/api/people/90/cover", {"face_id": 2090}, "PUT")
        check(status == 400, "未命名人物不能提前固定首页头像")
        status, _ = req("/api/people/1/cover", {"face_id": 1002}, "PUT")
        check(status == 400, "不能把其他人物的人脸设为当前人物头像")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(URL, wait_until="domcontentloaded")
            page.evaluate("setView('people')")
            page.wait_for_selector("#people-grid .person-card")
            check(page.locator(".people-section-divider").count() == 0, "首屏全是已命名人物时不会伪造待命名分界")
            page.locator("#people-jump-pending").click()
            page.wait_for_function("state.peoplePendingOnly && !state.peopleStream.people.loading")
            check(page.locator(".people-section-divider").count() == 1, "待命名按钮直接载入真正的待命名分界")
            check(
                page.locator("#people-grid .person-card.person-unnamed").count() == 2
                and page.locator("#people-grid .person-card.person-named").count() == 0,
                "跳转不需要先加载前面的五十多位已命名人物",
            )
            check(page.locator("#people-jump-pending").inner_text() == "全部人物", "进入待命名区后提供清楚的返回入口")
            page.locator("#people-jump-pending").click()
            page.wait_for_function("!state.peoplePendingOnly && !state.peopleStream.people.loading")

            page.locator('#people-grid [data-open-person="1"]').click()
            page.wait_for_selector("#person-dialog[open]")
            buttons = page.locator("#person-faces [data-set-cover]")
            check(buttons.count() == 2, "已命名人物的每张人脸右上角都有轻量头像选择按钮")
            first_box = page.locator('#person-faces [data-face-id="101"] img').bounding_box()
            button_box = page.locator('[data-set-cover="101"]').bounding_box()
            check(
                bool(
                    first_box
                    and button_box
                    and abs((button_box["x"] + button_box["width"]) - (first_box["x"] + first_box["width"])) <= 10
                    and abs(button_box["y"] - first_box["y"]) <= 10
                    and 26 <= button_box["width"] <= 30
                ),
                "头像选择按钮贴在照片右上角且尺寸不抢眼",
            )
            page.locator('[data-set-cover="101"]').click()
            page.wait_for_function("document.querySelector('#toast').textContent === '首页头像已选定'")
            check(page.locator('[data-set-cover="101"]').get_attribute("aria-pressed") == "true", "点选后当前照片立即显示固定状态")
            check(page.locator('#people-grid [data-person="1"] img').get_attribute("src") == "/api/face/101", "人物档案首页头像无需整页刷新就会更新")
            page.screenshot(path=str(SCREENSHOT))
            page.locator("#person-dialog").evaluate("dialog => dialog.close()")
            page.reload(wait_until="domcontentloaded")
            page.evaluate("setView('people')")
            page.wait_for_selector('#people-grid [data-person="1"] img')
            check(page.locator('#people-grid [data-person="1"] img').get_attribute("src") == "/api/face/101", "刷新页面后固定头像仍然保持")

            page.evaluate("openPerson(90)")
            page.wait_for_selector("#person-dialog[open]")
            check(page.locator("#person-faces [data-set-cover]").count() == 0, "待命名人物不会显示固定头像按钮")
            check(not errors, "真实 Chrome 操作没有 JavaScript 错误")
            browser.close()

        status, _ = req("/api/people/1/cover", {"face_id": 102}, "PUT")
        check(status == 200, "可以再次选择另一张照片替换固定头像")
        status, _ = req("/api/faces/102/split", {}, "POST")
        _, after_split = req("/api/people/1?limit=48")
        check(after_split["cover_face_id"] is None and after_split["cover"] == 101, "固定头像被移出人物后安全恢复为自动头像")

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
