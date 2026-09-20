"""Isolated browser checks for the discovery home. Never talks to production 8765."""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library_db import init_schema

RUN = ROOT / "validation" / "work" / ("home-discovery-" + time.strftime("%Y%m%d-%H%M%S"))
DATA = RUN / "data"
PHOTOS = RUN / "photos"
REPORT = ROOT / "validation" / "reports" / "home-discovery-20260918"
checks = []


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def add_photo(conn, aid, captured, *, place=None, person_id=None, face_id=None, size=(960, 640)):
    path = PHOTOS / f"home-{aid}.jpg"
    Image.new("RGB", size, (40 + aid % 180, 90, 110)).save(path)
    conn.execute(
        """INSERT INTO assets(id,sha256,width,height,format,metadata,captured_at,date_source,date_precision,
            place,category,created_at,face_state,notes,favorite)
           VALUES (?,?,?,?, 'JPEG','{}',?,'EXIF 原始拍摄时间','日',?,'照片',datetime('now'),1,'',0)""",
        (aid, f"{aid:064x}", size[0], size[1], captured, place),
    )
    conn.execute(
        "INSERT INTO files(asset_id,path,size,mtime_ns,exists_now,excluded) VALUES (?,?,1,1,1,0)",
        (aid, str(path)),
    )
    if person_id:
        conn.execute(
            "INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed) VALUES (?,?,?,'[0,0,20,20,100,100]',x'00',0.9,1)",
            (face_id or aid, aid, person_id),
        )


def main():
    DATA.mkdir(parents=True)
    PHOTOS.mkdir()
    REPORT.mkdir(parents=True, exist_ok=True)
    (DATA / "thumbs").mkdir()
    (DATA / "faces").mkdir()
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        init_schema(conn)
        for year, aid in ((2018, 1), (2020, 2), (2024, 3)):
            add_photo(conn, aid, f"{year}-09-18T10:00:00", place="杭州 · 西湖")
        for extra in range(4, 9):
            add_photo(conn, extra, "2024-09-18T11:00:00", place="杭州 · 西湖")
        for day in range(1, 5):
            add_photo(conn, 10 + day, f"2023-08-{day:02d}T09:00:00", place="长白山")
        conn.execute("INSERT INTO people(id,name,confirmed,ignored) VALUES (1,'林女士',1,0)")
        add_photo(conn, 21, "2012-04-01T10:00:00", person_id=1, face_id=21)
        add_photo(conn, 22, "2020-04-01T10:00:00", person_id=1, face_id=22)
        for i in range(30, 90):
            add_photo(conn, i, "2019-09-18T12:00:00")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PHOTO_LIBRARY_DATA": str(DATA),
        "PHOTO_WEB_ROOT": str(ROOT / "web"),
        "PHOTO_MODEL_ROOT": str(DATA / "no-model"),
        "PHOTO_GEO_ROOT": str(DATA / "no-geo"),
        "PHOTO_HOME_AS_OF": "2026-09-18",
        "PHOTO_NO_BROWSER": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    log = (RUN / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    errors = []
    try:
        import urllib.request
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                    health = json.loads(response.read().decode("utf-8"))
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("isolated server did not start")
        check(str(DATA) in str(health.get("data_dir")), "isolated health points at PHOTO_LIBRARY_DATA")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function("state.view=='home' && !document.querySelector('#home-view').hidden")
            page.wait_for_selector(".home-hero, .home-card")
            page.screenshot(path=str(REPORT / "home-1440.png"))
            check(page.locator(".home-kicker").count() >= 1, "home shows recommendation kickers")
            check("往年今日" in page.content() or "重访一个地方" in page.content() or "人物这些年" in page.content(), "home shows at least one real category")
            page.locator("[data-home-open]").first.click()
            page.wait_for_function("state.view=='home-group' && Number(waterfall.total)>=3")
            total = page.evaluate("waterfall.total")
            check(total >= 3, "opened group lists more than cover photos")
            page.locator("#photo-grid [data-photo]").first.click()
            page.wait_for_selector("#detail-dialog[open]")
            page.wait_for_function("viewer.ids && viewer.ids.length && viewer.ids[viewer.index]")
            first_id = page.evaluate("viewer.ids[viewer.index]")
            page.keyboard.press("ArrowRight")
            page.wait_for_function("(id) => viewer.ids[viewer.index] !== id", arg=first_id)
            second_id = page.evaluate("viewer.ids[viewer.index]")
            check(first_id != second_id, "viewer advances inside the recommendation group")
            page.keyboard.press("Escape")
            page.wait_for_function("!document.querySelector('#detail-dialog').open")
            page.locator("#home-group-back").click()
            page.wait_for_function("state.view=='home' && !document.querySelector('#home-view').hidden")
            check(True, "back from group restores discovery home")
            page.locator("[data-home-tab='on_this_day']").click()
            page.wait_for_timeout(300)
            check(page.evaluate("state.homeCategory")=="on_this_day", "category tab switches on_this_day")
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
            page.screenshot(path=str(REPORT / "home-390.png"))
            check(not overflow, "390px home does not overflow horizontally")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.locator("[data-view='timeline']").click()
            page.wait_for_function("state.view=='timeline'")
            page.wait_for_selector("#photo-grid [data-photo]")
            check(True, "all-photos timeline still opens from home")
            check(not errors, "no page errors: " + "; ".join(errors[:3]))
            browser.close()
        (REPORT / "browser-summary.json").write_text(
            json.dumps({"pass": checks, "health": health, "port": port}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


if __name__ == "__main__":
    main()
