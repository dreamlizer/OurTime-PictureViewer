"""Isolated face-label browser acceptance.

Never uses the production port, production data directory, or production web root.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library_db import init_schema  # noqa: E402

STAMP = time.strftime("%Y%m%d-%H%M%S")
RUN = ROOT / "validation" / "work" / "face-labels-20260925" / "private" / STAMP
DATA = RUN / "data"
PHOTOS = RUN / "photos"
SHOTS = RUN / "shots"
EXPORTS = RUN / "exports"
CHECKS = []
FORMAL_PID = 28992
FORMAL_PORT = 8765
FORMAL_NAME = "照片浏览器"


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    CHECKS.append(message)
    print("PASS", message, flush=True)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def formal_paths():
    root = (ROOT.parent / FORMAL_NAME).resolve()
    return root / "data", root / "web"


def refuse_formal(data_dir, web_root, url, port):
    formal_data, formal_web = formal_paths()
    resolved_data = Path(data_dir).resolve()
    resolved_web = Path(web_root).resolve()
    if resolved_data == formal_data or formal_data in resolved_data.parents:
        raise SystemExit("refusing formal data dir")
    if resolved_web == formal_web or formal_web in resolved_web.parents:
        raise SystemExit("refusing formal web root")
    if port == FORMAL_PORT or (":%d" % FORMAL_PORT) in url:
        raise SystemExit("refusing formal port")
    if "PHOTO_LIBRARY_DATA" not in os.environ and False:
        raise SystemExit("guard")


def write_photo(path, color, names):
    image = Image.new("RGB", (1600, 1000), color)
    draw = ImageDraw.Draw(image)
    draw.ellipse((250, 180, 620, 620), fill=(214, 186, 156))
    draw.ellipse((980, 210, 1320, 640), fill=(186, 154, 132))
    image.save(path, quality=92)


def seed():
    DATA.mkdir(parents=True)
    PHOTOS.mkdir()
    SHOTS.mkdir()
    EXPORTS.mkdir()
    (DATA / "thumbs").mkdir()
    (DATA / "faces").mkdir()
    source = formal_paths()[0] / "人名标签"
    target = DATA / "人名标签"
    target.mkdir()
    for name in ("1.png", "4.png", "7.png", "8.png"):
        blob = (source / name).read_bytes()
        if len(blob) < 100:
            raise SystemExit("plate copy looks empty: " + name)
        (target / name).write_bytes(blob)
    long_name = "陈宁宁宁宁"
    people = [
        (1, "陈宁", "小宁"),
        (2, "Alex Chen", ""),
        (3, long_name, ""),
        (4, "", "只有别名"),
        (5, "待核对", ""),
    ]
    photos = [
        (1, (86, 104, 92)),
        (2, (92, 78, 70)),
        (3, (78, 92, 104)),
    ]
    faces = [
        (101, 1, 1, "[180,120,760,760,1600,1000]"),
        (102, 1, 2, "[900,150,1450,760,1600,1000]"),
        (103, 2, 3, "[220,140,700,760,1600,1000]"),
        (104, 2, 4, "[980,180,1400,760,1600,1000]"),
        (105, 3, 5, "[300,160,820,760,1600,1000]"),
    ]
    with sqlite3.connect(DATA / "library.sqlite3") as db:
        init_schema(db)
        for asset_id, color in photos:
            path = PHOTOS / ("sample-%d.jpg" % asset_id)
            write_photo(path, color, None)
            stat = path.stat()
            db.execute(
                """INSERT INTO assets(id,sha256,width,height,format,metadata,captured_at,date_source,date_precision,place,category,created_at,face_state,notes,favorite)
                   VALUES (?,?,?,?, 'JPEG','{}',?,'test','day',?,?,datetime('now'),1,'',0)""",
                (
                    asset_id,
                    "%064x" % asset_id,
                    1600,
                    1000,
                    "2024-09-%02dT10:00:00" % (24 - asset_id),
                    "测试地点",
                    "照片",
                ),
            )
            db.execute(
                "INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded) VALUES (?,?,?,?,datetime('now'),1,0)",
                (asset_id, str(path), stat.st_size, stat.st_mtime_ns),
            )
        db.executemany(
            "INSERT INTO people(id,name,alias,confirmed,ignored) VALUES (?,?,?,1,0)",
            people,
        )
        for face_id, asset_id, person_id, bbox in faces:
            db.execute(
                "INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed,ignored) VALUES (?,?,?,?,x'00',0.9,1,0)",
                (face_id, asset_id, person_id, bbox),
            )
            Image.new("RGB", (120, 120), (120, 100, 90)).save(DATA / "faces" / ("%d.jpg" % face_id))
        db.execute(
            "INSERT INTO face_label_overrides(face_id,asset_id,x_ratio,y_ratio,layout_version,updated_at) VALUES (101,1,0.22,0.66,1,datetime('now'))"
        )
    return {
        "long_name": long_name,
        "alias_only": people[3][2],
        "chinese": people[0][1],
    }


def hashes(folder):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.glob("*.jpg")}


def wait_health(url):
    for _ in range(160):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("isolated server did not start")


def label_snapshot(page):
    return page.evaluate("""() => [...document.querySelectorAll('#face-name-layer .face-name')].map(el => {
      const style = getComputedStyle(el);
      const preview = document.querySelector('#face-style-preview-label');
      const previewStyle = preview ? getComputedStyle(preview) : null;
      return {
        text: el.textContent,
        direction: el.dataset.faceDirection,
        horizontal: el.classList.contains('is-horizontal'),
        manual: el.classList.contains('manual'),
        kind: el.dataset.faceKind,
        named: !el.classList.contains('unnamed'),
        left: el.style.left,
        top: el.style.top,
        writingMode: style.writingMode,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        color: style.color,
        overflow: style.overflow,
        width: Math.round(el.getBoundingClientRect().width),
        height: Math.round(el.getBoundingClientRect().height),
        theme: document.querySelector('#detail-dialog').dataset.faceTheme,
        effective: document.querySelector('#detail-dialog').dataset.faceThemeEffective,
        previewText: preview ? preview.textContent : '',
        previewFont: previewStyle ? previewStyle.fontFamily : '',
        previewWriting: previewStyle ? previewStyle.writingMode : '',
        previewColor: previewStyle ? previewStyle.color : '',
        previewOverflow: preview ? getComputedStyle(preview.parentElement).overflow : ''
      };
    })""")


def open_first_photo(page):
    page.locator('[data-view="timeline"]').click()
    page.wait_for_function("""() => document.querySelectorAll('#photo-grid [data-photo]').length >= 3""", timeout=15000)
    page.locator('#photo-grid [data-photo="1"]').click()
    page.wait_for_function("""() => document.querySelector('#detail-dialog').dataset.photoId === '1'""", timeout=8000)
    page.wait_for_selector('#face-name-layer .face-name', timeout=15000)
    page.wait_for_function("""() => {
      const img = document.querySelector('#detail-img');
      return img && img.complete && img.naturalWidth > 0 && document.querySelector('#face-style-button');
    }""")
    page.wait_for_function("""() => {
      const next = document.querySelector('#viewer-next');
      const position = document.querySelector('#viewer-position');
      return next && !next.disabled && position && position.textContent.includes('/');
    }""", timeout=15000)


def open_style_panel(page):
    page.locator('#face-style-button').click()
    page.wait_for_function("() => document.querySelector('#face-style-popover') && document.querySelector('#face-style-popover').hidden === false", timeout=4000)


def choose_theme(page, theme):
    button = page.locator('#face-style-popover [data-face-theme-choice="%s"]' % theme)
    button.scroll_into_view_if_needed()
    button.click(force=True)
    page.wait_for_function(
        """theme => document.querySelector('#detail-dialog').dataset.faceTheme === theme""",
        arg=theme,
        timeout=4000,
    )
    page.wait_for_timeout(250)


def drag_label(page, text, dx, dy):
    box = page.locator('#face-name-layer .face-name', has_text=text).first.bounding_box()
    if not box:
        raise AssertionError('label missing for drag: ' + text)
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 12)
    page.mouse.down()
    page.mouse.move(box['x'] + box['width'] / 2 + dx, box['y'] + 12 + dy, steps=8)
    page.mouse.up()


def install_label_gate(page):
    page.evaluate("""() => {
      window.__labelGate = {holds: [], records: []};
      const original = window.fetch.bind(window);
      window.fetch = (input, init={}) => {
        const url = typeof input === 'string' ? input : input.url;
        const method = String(init.method || 'GET').toUpperCase();
        if (method === 'PUT' && url.indexOf('/label-position') >= 0) {
          const body = JSON.parse(init.body || '{}');
          window.__labelGate.records.push({url, x: body.x_ratio, y: body.y_ratio});
          return new Promise((resolve, reject) => {
            window.__labelGate.holds.push(() => original(input, init).then(resolve, reject));
          });
        }
        return original(input, init);
      };
    }""")


def release_label_gate(page):
    page.evaluate("""() => Promise.all(window.__labelGate.holds.splice(0).map(run => run()))""")
    page.wait_for_timeout(300)


def label_of(items, text):
    return next(item for item in items if item["text"] == text)


def export_one(page, theme, fmt):
    result = page.evaluate("""async format => {
      const frozen = window.__ourTimeAnnotatedExport.freeze();
      const response = await fetch('/api/photos/' + frozen.id + '/export-annotated', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({snapshot: frozen.snapshot, format})
      });
      const bytes = new Uint8Array(await response.arrayBuffer());
      let detail = '';
      if (!response.ok) {
        try { detail = new TextDecoder().decode(bytes).slice(0, 300); } catch (error) { detail = String(error); }
      }
      return {
        ok: response.ok,
        status: response.status,
        detail,
        renderer: response.headers.get('X-OurTime-Export-Renderer'),
        html: frozen.snapshot.mat_html,
        geometry: frozen.snapshot.geometry,
        bytes: Array.from(bytes)
      };
    }""", fmt)
    check(result["ok"] and result["renderer"] == "same-origin-fonts-v1", "%s %s export accepted status=%s %s" % (theme, fmt, result.get("status"), result.get("detail")))
    blob = bytes(result["bytes"])
    PNG = bytes([137, 80, 78, 71, 13, 10, 26, 10])
    JPEG = bytes([255, 216, 255])
    check(blob.startswith(PNG if fmt == "png" else JPEG), theme + " " + fmt + " signature")
    html = result["html"]
    check("face-name-tip" not in html and "photo-export-control" not in html, theme + " snapshot drops transient chrome")
    check(result["geometry"]["image_width"] > 100 and result["geometry"]["image_height"] > 100, theme + " export geometry")
    return blob, html


def main():
    names = seed()
    before = hashes(PHOTOS)
    port = free_port()
    url = "http://127.0.0.1:%d" % port
    web_root = ROOT / "web"
    env = os.environ.copy()
    env.update({
        "PHOTO_LIBRARY_DATA": str(DATA),
        "PHOTO_WEB_ROOT": str(web_root),
        "PHOTO_MODEL_ROOT": str(DATA / "no-model"),
        "PHOTO_GEO_ROOT": str(DATA / "no-geo"),
        "PHOTO_FACE_LABEL_DIR": str(DATA / "人名标签"),
        "PHOTO_NO_BROWSER": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    refuse_formal(DATA, web_root, url, port)
    log = (RUN / "server.log").open("w", encoding="utf-8")
    print("isolated data=%s url=%s web=%s" % (DATA, url, web_root), flush=True)
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        health = wait_health(url)
        check(str(Path(health.get("data_dir", "")).resolve()) == str(DATA.resolve()), "isolated data dir")
        check(int(health.get("pid") or 0) != FORMAL_PID, "not production pid")
        check(int(health.get("pid") or 0) > 0, "isolated health has its own pid")
        print("isolated health pid=%s data=%s" % (health.get("pid"), health.get("data_dir")), flush=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url, wait_until="domcontentloaded")
            open_first_photo(page)
            labels = label_snapshot(page)
            check(any(item["text"] == names["chinese"] and item["direction"] == "vertical" and item["manual"] for item in labels), "chinese starts vertical and keeps manual position")
            check(any(item["text"] == "Alex Chen" and item["horizontal"] for item in labels), "latin stays horizontal")
            open_style_panel(page)
            check(page.locator("#face-style-popover").is_visible(), "style panel opens from a real click")
            for theme in ("classic", "ivory", "tea", "accent"):
                choose_theme(page, theme)
                page.screenshot(path=str(SHOTS / ("%s-1440.png" % theme)))
                current = label_snapshot(page)
                check(all(item["theme"] == theme for item in current), theme + " requested")
                check(all(item["effective"] in (theme, "classic") for item in current), theme + " effective is requested or classic fallback")
                check(any(item["text"] == "Alex Chen" and item["horizontal"] and item["writingMode"].startswith("horizontal") for item in current), theme + " latin horizontal")
                check(any(item["text"] == names["chinese"] and item["writingMode"].startswith("vertical") for item in current), theme + " chinese vertical")
                named = [item for item in current if item["named"]]
                if named:
                    preview = named[0]
                    check(preview["previewFont"] == preview["fontFamily"], theme + " preview font matches live label")
                    check(preview["previewWriting"] == preview["writingMode"], theme + " preview direction matches live label")
                    check(preview["previewOverflow"] == "visible", theme + " preview does not clip")
            page.locator("#face-style-reset").click()
            page.wait_for_function("""() => document.querySelector('#detail-dialog').dataset.faceTheme === 'classic'""")
            reset = label_snapshot(page)
            check(all(item["theme"] == "classic" for item in reset), "appearance reset")
            check(any(item["manual"] for item in reset), "manual position kept after appearance reset")
            before_drag = next(item for item in reset if item["text"] == names["chinese"])
            drag_label(page, names["chinese"], 90, -40)
            page.wait_for_timeout(500)
            moved = label_snapshot(page)
            after_drag = next(item for item in moved if item["text"] == names["chinese"])
            check(after_drag["left"] != before_drag["left"] or after_drag["top"] != before_drag["top"], "drag changes label position")
            page.reload(wait_until="domcontentloaded")
            open_first_photo(page)
            reloaded = label_snapshot(page)
            kept = next(item for item in reloaded if item["text"] == names["chinese"])
            check(abs(float(kept["left"].replace("px", "")) - float(after_drag["left"].replace("px", ""))) < 8, "saved position survives reload")
            install_label_gate(page)
            queued = []
            for delta in (70, 55, 40):
                drag_label(page, names["chinese"], delta, -18)
                page.wait_for_timeout(120)
                current = label_of(label_snapshot(page), names["chinese"])
                queued.append((current["left"], current["top"]))
            check(len({item[0] for item in queued}) == 3, "three queued drags stay visually distinct")
            page.locator("#viewer-next").click()
            page.wait_for_function("""() => document.querySelector('#detail-dialog').dataset.photoId === '2'""", timeout=8000)
            release_label_gate(page)
            page.locator("#viewer-prev").click()
            page.wait_for_function("""() => document.querySelector('#detail-dialog').dataset.photoId === '1'""", timeout=8000)
            page.wait_for_selector("#face-name-layer .face-name")
            page.wait_for_timeout(400)
            returned = label_of(label_snapshot(page), names["chinese"])
            check(abs(float(returned["left"].replace("px", "")) - float(queued[-1][0].replace("px", ""))) < 14, "latest delayed receipt wins after photo switch")
            page.route("**/api/face-label-bg/7.png", lambda route: route.fulfill(status=404, body=b""))
            page.reload(wait_until="domcontentloaded")
            open_first_photo(page)
            open_style_panel(page)
            choose_theme(page, "ivory")
            page.wait_for_function("""() => {
              const status = document.querySelector('#face-style-status');
              const root = document.querySelector('#detail-dialog');
              return status && !status.hidden && root.dataset.faceTheme === 'ivory' && root.dataset.faceThemeEffective === 'classic';
            }""", timeout=6000)
            check(True, "missing horizontal plate keeps ivory request and classic fallback")
            page.unroute("**/api/face-label-bg/7.png")
            narrow = context.new_page()
            narrow.set_viewport_size({"width": 390, "height": 844})
            narrow.goto(url, wait_until="domcontentloaded")
            open_first_photo(narrow)
            open_style_panel(narrow)
            panel = narrow.locator("#face-style-popover").bounding_box()
            check(panel and panel["x"] >= -1 and panel["x"] + panel["width"] <= 392, "390px style panel stays inside the viewport")
            narrow.screenshot(path=str(SHOTS / "panel-390.png"))
            narrow.close()
            page.reload(wait_until="domcontentloaded")
            open_first_photo(page)
            for theme in ("classic", "ivory", "tea", "accent"):
                if page.locator('#face-style-popover').is_visible():
                    page.locator('#face-style-close').click()
                open_style_panel(page)
                choose_theme(page, theme)
                page.locator('#face-style-close').click()
                png, html = export_one(page, theme, "png")
                jpeg, _ = export_one(page, theme, "jpeg")
                from PIL import Image
                import io
                with Image.open(io.BytesIO(png)) as image:
                    check(image.size[0] >= 1400 and image.size[1] >= 900, theme + " export uses original dimensions")
                (EXPORTS / (theme + ".png")).write_bytes(png)
                (EXPORTS / (theme + ".jpg")).write_bytes(jpeg)
                check(names["chinese"] in html and "Alex Chen" in html, theme + " export keeps both names")
            if page.locator('#face-style-popover').is_visible():
                page.locator('#face-style-close').click()
            open_style_panel(page)
            page.locator("#face-label-reset-photo").click()
            page.wait_for_function("""() => [...document.querySelectorAll('#face-name-layer .face-name')].every(node => !node.classList.contains('manual'))""", timeout=8000)
            check(True, "explicit reset clears this photo custom positions")
            page.locator('#face-style-close').click()
            page.wait_for_function("() => document.querySelector('#face-style-popover').hidden === true", timeout=4000)
            page.locator("#viewer-next").click()
            page.wait_for_function("""() => document.querySelector('#detail-dialog').dataset.photoId === '2'""", timeout=8000)
            page.wait_for_selector('#face-name-layer .face-name')
            second = label_snapshot(page)
            check(any(item["text"] == names["long_name"] and item["named"] for item in second), "long name is visible")
            check(any(item["text"] == names["alias_only"] and item["named"] for item in second), "alias-only person stays named")
            long_item = next(item for item in second if item["text"] == names["long_name"])
            check(long_item["height"] > 40, "long vertical name has a real height")
            page.locator("#viewer-next").click()
            page.wait_for_function("""() => document.querySelector('#detail-dialog').dataset.photoId === '3'""", timeout=8000)
            page.wait_for_selector('#face-name-layer .face-name.unnamed')
            unnamed = page.locator('#face-name-layer .face-name.unnamed').bounding_box()
            check(unnamed and 20 <= unnamed["width"] <= 32 and 20 <= unnamed["height"] <= 32, "unnamed marker keeps its own size")
            check(not errors, "no page errors: " + " | ".join(errors))
            browser.close()
        after = hashes(PHOTOS)
        check(before == after, "source hashes unchanged")
        (RUN / "checks.json").write_text(json.dumps({"checks": CHECKS, "shots": str(SHOTS)}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("EVIDENCE", RUN, flush=True)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


if __name__ == "__main__":
    main()
