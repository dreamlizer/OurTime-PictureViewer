"""Visual refinement checks for discovery home. Isolated data only."""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library_db import init_schema

STAMP = time.strftime("%Y%m%d-%H%M%S")
RUN = ROOT / "validation" / "work" / ("home-visual-" + STAMP)
DATA = RUN / "data"
PHOTOS = RUN / "photos"
REPORT = ROOT / "validation" / "reports" / "home-visual-20260918"
AFTER = REPORT / "after"
checks = []


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def paint_photo(path, size, color, face_box=None):
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    if face_box:
        x1, y1, x2, y2 = face_box
        draw.ellipse([x1, y1, x2, y2], fill=(240, 210, 180), outline=(80, 50, 40), width=6)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        draw.ellipse([cx - 12, cy - 18, cx - 2, cy - 6], fill=(40, 40, 40))
        draw.ellipse([cx + 2, cy - 18, cx + 12, cy - 6], fill=(40, 40, 40))
    else:
        draw.rectangle([40, size[1] // 2, size[0] - 40, size[1] - 40], fill=(70, 110, 90))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=90)


def add_asset(conn, aid, captured, *, place=None, size=(1200, 800), color=(40, 90, 110), face=None, favorite=0, person_id=None, face_id=None):
    path = PHOTOS / f"home-{aid}.jpg"
    face_box = None
    bbox = "[0,0,20,20,100,100]"
    if face:
        face_box = face
        bbox = json.dumps(list(face) + [size[0], size[1]])
    paint_photo(path, size, color, face_box)
    conn.execute(
        """INSERT INTO assets(id,sha256,width,height,format,metadata,captured_at,date_source,date_precision,
            place,category,created_at,face_state,notes,favorite)
           VALUES (?,?,?,?, 'JPEG','{}',?,'EXIF DateTimeOriginal','\u65e5',?,'\u7167\u7247',datetime('now'),1,'',?)""",
        (aid, f"{aid:064x}", size[0], size[1], captured, place, favorite),
    )
    conn.execute(
        "INSERT INTO files(asset_id,path,size,mtime_ns,exists_now,excluded) VALUES (?,?,1,1,1,0)",
        (aid, str(path)),
    )
    if person_id:
        conn.execute(
            "INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed) VALUES (?,?,?,?,x'00',0.9,1)",
            (face_id or aid, aid, person_id, bbox),
        )


def measure(page):
    return page.evaluate("""() => {
      const rect = el => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return {x:r.x,y:r.y,w:r.width,h:r.height,left:r.left,right:r.right,top:r.top,bottom:r.bottom,
                display:s.display,width:s.width,maxWidth:s.maxWidth,margin:s.margin,padding:s.padding,
                position:s.position,transform:s.transform,grid:s.gridTemplateColumns};
      };
      const main = document.querySelector('main');
      const home = document.querySelector('#home-view');
      const disc = document.querySelector('.home-discovery');
      const heading = document.querySelector('.home-heading');
      const tabs = document.querySelector('.home-tabs');
      const hero = document.querySelector('.home-hero');
      const pair = document.querySelector('.home-pair');
      const leftCard = document.querySelector('.home-pair .home-card');
      const medias = [...document.querySelectorAll('.home-pair .home-media')].map(el => el.getBoundingClientRect().height);
      const yearPhotos = document.querySelectorAll('.home-year-photo').length;
      const toolsInYearGrid = [...document.querySelectorAll('.home-years > *')].filter(el => el.classList.contains('home-card-tools')).length;
      const toolsAsYearPhoto = document.querySelectorAll('.home-year-photo.home-card-tools').length;
      return {
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        dpr: window.devicePixelRatio,
        sidebar: rect(document.querySelector('.sidebar')),
        main: rect(main),
        home, disc: rect(disc), heading: rect(heading), tabs: rect(tabs), hero: rect(hero),
        pair: rect(pair), leftCard: rect(leftCard),
        headingLeft: heading && heading.getBoundingClientRect().left,
        tabsLeft: tabs && tabs.getBoundingClientRect().left,
        heroLeft: hero && hero.getBoundingClientRect().left,
        cardLeft: leftCard && leftCard.getBoundingClientRect().left,
        mediaHeights: medias,
        yearPhotos, toolsInYearGrid, toolsAsYearPhoto,
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2
      };
    }""")


def main():
    DATA.mkdir(parents=True)
    PHOTOS.mkdir()
    AFTER.mkdir(parents=True, exist_ok=True)
    (DATA / "thumbs").mkdir()
    (DATA / "faces").mkdir()
    lin = chr(0x6797) + chr(0x5973) + chr(0x58EB)
    place = (
        chr(0x8FBD) + chr(0x5B81) + chr(0x7701) + " " + chr(0x00B7) + " "
        + chr(0x6C88) + chr(0x9633) + chr(0x5E02) + " " + chr(0x00B7) + " "
        + chr(0x6C88) + chr(0x6CB3) + chr(0x533A)
    )
    with sqlite3.connect(DATA / "library.sqlite3") as conn:
        init_schema(conn)
        conn.execute("INSERT INTO people(id,name,confirmed,ignored) VALUES (1,?,1,0)", (lin,))
        portraits = [
            (21, "2010-04-01T10:00:00", (210, 70, 70), (280, 80, 920, 980)),
            (22, "2014-04-01T10:00:00", (80, 110, 90), (300, 90, 900, 1000)),
            (23, "2018-04-01T10:00:00", (90, 80, 140), (260, 70, 880, 990)),
            (24, "2024-04-01T10:00:00", (70, 90, 120), (300, 100, 940, 1020)),
        ]
        for aid, captured, color, box in portraits:
            add_asset(conn, aid, captured, size=(1200, 1500), color=color, face=box, person_id=1, face_id=aid)
        for extra in (25, 26):
            add_asset(conn, extra, "2012-04-01T10:00:00", size=(1600, 1000), color=(30, 30, 30), face=(20, 20, 70, 80), person_id=1, face_id=extra)
        for year, aid in ((2018, 1), (2019, 2), (2024, 3)):
            add_asset(conn, aid, f"{year}-09-18T10:00:00", place=place, size=(1400, 900), color=(220, 140, 60))
        for extra in range(4, 8):
            add_asset(conn, extra, "2024-09-18T11:00:00", place=place, size=(1400, 900), color=(230, 150, 70))
        for day in range(1, 5):
            add_asset(conn, 10 + day, f"2023-05-{day:02d}T09:00:00", place=place, size=(1600, 900), color=(90, 130, 80), favorite=1 if day == 2 else 0)
        add_asset(conn, 15, "2023-05-02T12:00:00", place=place, size=(700, 1000), color=(40, 40, 40), face=(80, 80, 600, 860), person_id=1, face_id=15)
        conn.commit()
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
        health = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                    health = json.loads(response.read().decode("utf-8"))
                    break
            except OSError:
                time.sleep(0.1)
        if health is None:
            raise RuntimeError("isolated server did not start")
        check(str(DATA) in str(health.get("data_dir")), "isolated health points at PHOTO_LIBRARY_DATA")
        with urllib.request.urlopen(url + "/api/home/recommendations?category=people_years") as response:
            people = json.loads(response.read().decode("utf-8"))
        card = people["items"][0]
        covers = card["covers"]
        evidence = {
            "algorithm": "home-discovery-v4",
            "person": card["title"],
            "covers": covers,
            "note": "isolated fixture, not production library",
        }
        (REPORT / "year-binding-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        years = [item["year"] for item in covers]
        check(years[0] == "2010" and years[-1] == "2024", "covers include first and last year")
        with sqlite3.connect(DATA / "library.sqlite3") as conn:
            for item in covers:
                captured = conn.execute("SELECT substr(captured_at,1,4) FROM assets WHERE id=?", (item["asset_id"],)).fetchone()[0]
                face = conn.execute("SELECT person_id FROM faces WHERE id=?", (item["target_face_id"],)).fetchone()
                check(captured == item["year"], f"asset {item['asset_id']} year {item['year']} matches captured {captured}")
                check(face and face[0] == 1, f"face {item['target_face_id']} belongs to person 1")
        with urllib.request.urlopen(url + "/api/home/recommendations?category=place_revisit") as response:
            places = json.loads(response.read().decode("utf-8"))
        place_card = places["items"][0]
        check(place_card["cover_asset_ids"][0] in (11, 12, 13, 14), "place cover stays on the landscape visit photos")
        check(chr(0x8FBD) + chr(0x5B81) + chr(0x7701) not in place_card["title"], "place title is shortened")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function("state.view=='home' && !document.querySelector('#home-view').hidden")
            page.wait_for_selector(".home-hero, .home-card")
            geo = measure(page)
            page.screenshot(path=str(AFTER / "home-1440.png"), full_page=True)
            check(geo["headingLeft"] - geo["main"]["left"] <= 24, "home content hugs the left of main")
            lefts = [geo["headingLeft"], geo["tabsLeft"], geo["heroLeft"], geo["cardLeft"]]
            check(max(lefts) - min(lefts) <= 4, "title/tabs/hero/left card share a left edge")
            if len(geo["mediaHeights"]) >= 2:
                check(abs(geo["mediaHeights"][0] - geo["mediaHeights"][1]) <= 4, "pair media heights align")
            check(geo["toolsInYearGrid"] == 0 and geo["toolsAsYearPhoto"] == 0, "tools are not year mosaic cells")
            check(geo["yearPhotos"] >= 1, "people mosaic uses year photo cells")
            check(not geo["overflow"], "1440 home does not overflow")
            tools = page.locator(".home-years .home-card-tools button").first
            check(tools.count() == 1 or page.locator(".home-card-tools button").count() >= 1, "swap/hide tools exist")
            page.locator(".home-pair .home-card").first.click()
            page.wait_for_function("state.view=='home-group' && Number(waterfall.total)>=3")
            page.locator("#photo-grid [data-photo]").first.click()
            page.wait_for_selector("#detail-dialog[open]")
            first_id = page.evaluate("viewer.ids[viewer.index]")
            page.keyboard.press("ArrowRight")
            page.wait_for_function("(id) => viewer.ids[viewer.index] !== id", arg=first_id)
            page.keyboard.press("Escape")
            page.locator("#home-group-back").click()
            page.wait_for_function("state.view=='home' && !document.querySelector('#home-view').hidden")
            check(True, "group, viewer, and back still work")
            page.locator(".home-card-tools button").first.focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(200)
            check(page.evaluate("state.view") == "home", "keyboard tool click does not open the group")
            for width, height, name in ((1920, 1080, "home-1920"), (2560, 1080, "home-2560"), (390, 844, "home-390")):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(250)
                shot = measure(page)
                page.screenshot(path=str(AFTER / f"{name}.png"), full_page=True)
                check(shot["innerWidth"] == width, f"{name} innerWidth {shot['innerWidth']}")
                check(not shot["overflow"], f"{name} no horizontal overflow")
                (AFTER / f"{name}.json").write_text(json.dumps(shot, ensure_ascii=False, indent=2), encoding="utf-8")
            (AFTER / "home-1440.json").write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
            check(not errors, "no page errors: " + "; ".join(errors[:3]))
            browser.close()
        (REPORT / "visual-summary.json").write_text(
            json.dumps({"pass": checks, "health": health, "port": port, "evidence": str(REPORT / "year-binding-evidence.json")}, ensure_ascii=False, indent=2),
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
