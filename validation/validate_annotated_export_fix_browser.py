"""Isolated browser proof for frozen-layout JPEG/PNG photo export."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"
REPORT_DIR = ROOT / "validation" / "reports" / "annotated-export-fix"
EVIDENCE_DIR = ROOT / "docs" / "repair-reports" / "assets" / "annotated-export-fix"
sys.path.insert(0, str(ROOT))
from library_db import init_schema


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def check(value, message: str, checks: list[str]) -> None:
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def patterned_photo(path: Path, size: tuple[int, int], colors: tuple[str, str]) -> None:
    image = Image.new("RGB", size, colors[0])
    draw = ImageDraw.Draw(image)
    step = max(18, min(size) // 80)
    for x in range(0, size[0], step):
        draw.line((x, 0, size[0] - x // 2, size[1]), fill=colors[1], width=max(2, step // 8))
    for y in range(0, size[1], step * 2):
        draw.ellipse((size[0] // 5, y, size[0] // 5 + step * 3, y + step * 3), outline="#f4e9d3", width=3)
    image.save(path, quality=96)


def smooth_photo(path: Path, size: tuple[int, int]) -> None:
    image = Image.new("RGB", size, "#394958")
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (size[0] // 12, size[1] // 10, size[0] * 11 // 12, size[1] * 9 // 10),
        fill="#617486",
    )
    draw.ellipse(
        (size[0] // 3, size[1] // 5, size[0] * 2 // 3, size[1] * 4 // 5),
        fill="#a0adad",
    )
    image.save(path, quality=96)


def seed(data: Path, photos: Path) -> dict[int, Path]:
    data.mkdir(parents=True)
    photos.mkdir(parents=True)
    originals = {
        1: photos / "合成多人照片.jpg",
        2: photos / "合成单人照片.jpg",
        3: photos / "合成主题照片.jpg",
    }
    patterned_photo(originals[1], (3200, 2000), ("#75624f", "#ad8b6b"))
    patterned_photo(originals[2], (2400, 3200), ("#5b7168", "#8faf9e"))
    smooth_photo(originals[3], (4096, 2730))
    metadata = {
        "ExifTool": {
            "EXIF:Make": "SONY",
            "EXIF:Model": "ILCE-7M4",
            "EXIF:FocalLength": 35,
            "EXIF:FNumber": 2.8,
            "EXIF:ExposureTime": 0.008,
            "EXIF:ISO": 200,
        }
    }
    with sqlite3.connect(data / "library.sqlite3") as connection:
        init_schema(connection)
        for aid, path in originals.items():
            width, height = Image.open(path).size
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            connection.execute(
                """INSERT INTO assets(
                     id,sha256,width,height,format,metadata,captured_at,date_source,
                     date_precision,place,created_at,face_state
                   ) VALUES(?,?,?,?,?,?,?,'EXIF','日',?,datetime('now'),1)""",
                (
                    aid,
                    sha,
                    width,
                    height,
                    "JPEG",
                    json.dumps(metadata, ensure_ascii=False),
                    f"2026-09-{10 + aid:02}T14:2{aid}:00",
                    ["北京 · 合成公园", "上海 · 合成展厅", "杭州 · 合成湖畔"][aid - 1],
                ),
            )
            connection.execute(
                """INSERT INTO files(
                     asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
                   ) VALUES(?,?,?,?,datetime('now'),1,0)""",
                (aid, str(path), path.stat().st_size, path.stat().st_mtime_ns),
            )
        people = [(1, "王小明", "小明"), (2, "李安然", ""), (3, "陈思远", ""), (4, "周宁", "")]
        connection.executemany(
            "INSERT INTO people(id,name,alias,confirmed,ignored) VALUES(?,?,?,1,0)",
            people,
        )
        faces = [
            (101, 1, 1, [420, 440, 780, 930, 3200, 2000]),
            (102, 1, 2, [1350, 400, 1700, 900, 3200, 2000]),
            (103, 1, 3, [2280, 460, 2660, 960, 3200, 2000]),
            (201, 2, 4, [800, 740, 1540, 1660, 2400, 3200]),
            (301, 3, 1, [980, 560, 1480, 1200, 4096, 2730]),
            (302, 3, 2, [2400, 520, 2920, 1190, 4096, 2730]),
        ]
        connection.executemany(
            """INSERT INTO faces(
                 id,asset_id,person_id,bbox,embedding,score,reviewed,ignored
               ) VALUES(?,?,?,?,x'00',0.96,1,0)""",
            [(fid, aid, pid, json.dumps(box)) for fid, aid, pid, box in faces],
        )
        connection.commit()
    (data / "faces").mkdir()
    (data / "thumbs").mkdir()
    return originals


def wait_ready(url: str) -> None:
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("isolated server did not start")


def mean_difference(left: bytes, right_path: Path) -> float:
    with Image.open(io.BytesIO(left)).convert("RGB") as viewer_image:
        with Image.open(right_path).convert("RGB") as exported:
            exported.thumbnail(viewer_image.size, Image.Resampling.LANCZOS)
            if exported.size != viewer_image.size:
                exported = exported.resize(viewer_image.size, Image.Resampling.LANCZOS)
            difference = ImageChops.difference(viewer_image, exported)
            return sum(ImageStat.Stat(difference).mean) / 3


def save_small(source, target: Path, max_width: int = 1000) -> None:
    with Image.open(source).convert("RGB") as image:
        if image.width > max_width:
            height = round(image.height * max_width / image.width)
            image = image.resize((max_width, height), Image.Resampling.LANCZOS)
        image.save(target, quality=90, optimize=True)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    checks: list[str] = []
    page_errors: list[str] = []
    comparisons: list[dict] = []
    with tempfile.TemporaryDirectory(dir=WORK, ignore_cleanup_errors=True) as folder:
        run = Path(folder)
        data = run / "data"
        originals = seed(data, run / "photos")
        source_hashes = {aid: hashlib.sha256(path.read_bytes()).hexdigest() for aid, path in originals.items()}
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            {
                "PHOTO_LIBRARY_DATA": str(data),
                "PHOTO_WEB_ROOT": str(ROOT / "web"),
                "PHOTO_MODEL_ROOT": str(run / "no-model"),
                "PHOTO_GEO_ROOT": str(run / "no-geo"),
                "NO_ALBUMENTATIONS_UPDATE": "1",
            }
        )
        log_path = run / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "app.py"), "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            browser = None
            try:
                wait_ready(url)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                    page = browser.new_page(
                        viewport={"width": 1440, "height": 920},
                        device_scale_factor=1,
                        accept_downloads=True,
                    )
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.goto(url, wait_until="domcontentloaded")
                    scenarios = [
                        (1, "classic", True, 0, 3, "multi-vertical"),
                        (2, "ivory", True, 1, 1, "single-ivory"),
                        (3, "tea", True, 2, 2, "two-tea"),
                    ]
                    for aid, theme, vertical, signature_clicks, expected_faces, slug in scenarios:
                        if aid == 2:
                            page.evaluate(
                                """async () => {
                                  void openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'});
                                  await openPhoto(2,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'});
                                }"""
                            )
                        else:
                            page.evaluate(
                                """id => openPhoto(id,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})""",
                                aid,
                            )
                        page.wait_for_function(
                            "id => state.detail?.id===id && document.querySelector('#detail-img').naturalWidth>0 && !document.querySelector('#detail-dialog').classList.contains('is-loading')",
                            arg=aid,
                        )
                        page.evaluate(
                            """({theme,vertical}) => {
                              applyFacePreset(theme);
                              viewer.faceVertical=vertical;
                              renderFaceNames(state.detail);
                            }""",
                            {"theme": theme, "vertical": vertical},
                        )
                        for _ in range(signature_clicks):
                            page.locator("#photo-signature .signature-switch").click()
                        page.wait_for_timeout(250)
                        check(
                            page.locator("#face-name-layer .face-name:not(.unnamed)").count() == expected_faces,
                            f"{slug} 当前查看器显示预期姓名标签",
                            checks,
                        )
                        snapshot = page.evaluate("() => window.__ourTimeAnnotatedExport.freeze()")
                        check(snapshot["id"] == aid, f"{slug} 快速切换后冻结当前照片而非上一张", checks)
                        markup = snapshot["snapshot"]["mat_html"]
                        check(
                            all(token not in markup for token in ("viewer-photo-close", "photo-favorite", "photo-place-map", "photo-export-control", "signature-switch")),
                            f"{slug} 冻结快照排除全部交互控件",
                            checks,
                        )
                        check(
                            not re.search(r"url\(&quot;/api/face-label-bg/[1-9]\.png&quot;\)", markup, re.I),
                            f"{slug} 合法人名标签底图不会被 HTML 引号转义误拦",
                            checks,
                        )
                        viewer_path = REPORT_DIR / f"{slug}-viewer.png"
                        clean_viewer = page.locator("#photo-mat").screenshot(
                            animations="disabled",
                            style=".viewer-photo-close,.photo-favorite,.photo-place-map,.signature-switch,.signature-info-popover{display:none!important}",
                        )
                        viewer_path.write_bytes(clean_viewer)
                        page.locator("#photo-mat").screenshot(path=str(REPORT_DIR / f"{slug}-viewer-with-controls.png"))
                        page.locator("#export-annotated-format").select_option("jpeg")
                        with page.expect_download(timeout=45_000) as download_info:
                            page.locator("#export-annotated-photo").click()
                        download = download_info.value
                        export_path = REPORT_DIR / f"{slug}-export.jpg"
                        download.save_as(export_path)
                        with Image.open(export_path) as exported:
                            source_width = Image.open(originals[aid]).width
                            check(exported.format == "JPEG", f"{slug} 默认导出 JPEG", checks)
                            check(exported.width >= source_width, f"{slug} 使用原图像素宽度而非放大预览", checks)
                        difference = mean_difference(clean_viewer, export_path)
                        check(difference < 14, f"{slug} 查看器与导出缩放对照平均像素差低于14", checks)
                        comparisons.append({"name": slug, "mean_pixel_difference": round(difference, 3)})
                        save_small(viewer_path, EVIDENCE_DIR / f"{slug}-viewer.jpg")
                        save_small(export_path, EVIDENCE_DIR / f"{slug}-export.jpg")

                    page.evaluate(
                        """() => openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})"""
                    )
                    page.wait_for_function(
                        "() => state.detail?.id===1 && document.querySelector('#detail-img').naturalWidth>0 && !document.querySelector('#detail-dialog').classList.contains('is-loading')"
                    )
                    page.locator("#export-annotated-format").select_option("png")
                    with page.expect_download(timeout=45_000) as png_download_info:
                        page.locator("#export-annotated-photo").click()
                    png_path = REPORT_DIR / "multi-vertical-export.png"
                    png_download_info.value.save_as(png_path)
                    with Image.open(png_path) as png_image:
                        check(png_image.format == "PNG", "PNG 可选导出保留", checks)
                    jpeg_path = REPORT_DIR / "multi-vertical-export.jpg"
                    check(jpeg_path.stat().st_size < png_path.stat().st_size, "同一照片默认 JPEG 体积小于 PNG", checks)

                    page.evaluate(
                        """() => {
                          viewer.faceNames=false;
                          renderFaceNames(state.detail);
                        }"""
                    )
                    no_labels = page.evaluate("() => window.__ourTimeAnnotatedExport.freeze().snapshot.mat_html")
                    check(
                        'id="face-name-layer"' in no_labels and "display: none !important" in no_labels,
                        "关闭人名显示后快照不导出标签但保留底签",
                        checks,
                    )

                    frozen = page.evaluate("() => window.__ourTimeAnnotatedExport.freeze()")
                    current_original = originals[frozen["id"]]
                    missing = current_original.with_suffix(".missing")
                    current_original.rename(missing)
                    try:
                        response = page.evaluate(
                            """async frozen => {
                              const response=await fetch(`/api/photos/${frozen.id}/export-annotated`,{
                                method:'POST',
                                headers:{'Content-Type':'application/json'},
                                body:JSON.stringify({snapshot:frozen.snapshot,format:'jpeg'})
                              });
                              return {status:response.status,text:await response.text()};
                            }""",
                            frozen,
                        )
                    finally:
                        missing.rename(current_original)
                    check(
                        response["status"] == 404 and "原文件" in response["text"],
                        "原图丢失时明确失败且不使用预览图降级",
                        checks,
                    )
                    check(not page_errors, "浏览器流程无 pageerror", checks)
            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        check(
            all(hashlib.sha256(path.read_bytes()).hexdigest() == source_hashes[aid] for aid, path in originals.items()),
            "三张隔离原图 SHA256 前后不变",
            checks,
        )
    result = {
        "passed": True,
        "checks": checks,
        "comparisons": comparisons,
        "page_errors": page_errors,
    }
    (REPORT_DIR / "browser-validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"PASS {len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
