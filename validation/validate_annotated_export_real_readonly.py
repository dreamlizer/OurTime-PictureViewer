"""Opt-in, read-only real-photo fidelity check using an isolated database."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"
REPORT = ROOT / "validation" / "reports" / "annotated-export-fix" / "real-readonly-validation.json"
sys.path.insert(0, str(ROOT))
from library_db import init_schema


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(url: str) -> None:
    for _ in range(250):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("isolated server did not start")


def candidates(connection: sqlite3.Connection, face_count: int) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT a.*, fi.path, fi.size AS file_size, fi.mtime_ns,
                  fi.modified_at AS file_modified_at
           FROM assets a
           JOIN files fi ON fi.asset_id=a.id
             AND fi.excluded=0 AND fi.exists_now=1
           WHERE a.excluded=0 AND a.format IN ('JPEG','PNG')
             AND a.width>=2500 AND a.height>=1800
             AND a.width*a.height<=20000000
             AND (SELECT count(*)
                  FROM faces f JOIN people p ON p.id=f.person_id
                  WHERE f.asset_id=a.id AND f.ignored=0 AND p.ignored=0
                    AND p.confirmed=1 AND trim(coalesce(p.name,''))<>'')=?
           ORDER BY a.width*a.height DESC,a.id
           LIMIT 100""",
        (face_count,),
    ).fetchall()


def choose_assets(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    selected = []
    for count in (3, 1, 2):
        row = next(
            (
                item
                for item in candidates(connection, count)
                if Path(item["path"]).is_file()
            ),
            None,
        )
        if row is None:
            raise RuntimeError(f"没有找到可读的 {count} 人真实照片样本")
        selected.append(row)
    return selected


def seed_isolated(data: Path, rows: list[sqlite3.Row], source: sqlite3.Connection) -> list[dict]:
    data.mkdir(parents=True)
    records = []
    with sqlite3.connect(data / "library.sqlite3") as target:
        init_schema(target)
        next_person = 1
        for aid, row in enumerate(rows, 1):
            original = Path(row["path"])
            before = hashlib.sha256(original.read_bytes()).hexdigest()
            target.execute(
                """INSERT INTO assets(
                     id,sha256,width,height,format,metadata,captured_at,date_source,
                     date_precision,place,camera,created_at,face_state
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    aid,
                    row["sha256"],
                    row["width"],
                    row["height"],
                    row["format"],
                    row["metadata"],
                    row["captured_at"],
                    row["date_source"],
                    row["date_precision"],
                    "真实照片只读抽样",
                    row["camera"],
                    row["created_at"],
                ),
            )
            target.execute(
                """INSERT INTO files(
                     asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
                   ) VALUES(?,?,?,?,?,1,0)""",
                (
                    aid,
                    str(original),
                    row["file_size"],
                    row["mtime_ns"],
                    row["file_modified_at"],
                ),
            )
            faces = source.execute(
                """SELECT f.bbox
                   FROM faces f JOIN people p ON p.id=f.person_id
                   WHERE f.asset_id=? AND f.ignored=0 AND p.ignored=0
                     AND p.confirmed=1 AND trim(coalesce(p.name,''))<>''
                   ORDER BY f.id""",
                (row["id"],),
            ).fetchall()
            for index, face in enumerate(faces, 1):
                person_id = next_person
                next_person += 1
                target.execute(
                    "INSERT INTO people(id,name,confirmed,ignored) VALUES(?,?,1,0)",
                    (person_id, f"人物{index}"),
                )
                target.execute(
                    """INSERT INTO faces(
                         asset_id,person_id,bbox,embedding,score,reviewed,ignored
                       ) VALUES(?,?,?,x'00',0.96,1,0)""",
                    (aid, person_id, face["bbox"]),
                )
            records.append(
                {
                    "aid": aid,
                    "path": original,
                    "sha256": before,
                    "width": int(row["width"]),
                    "height": int(row["height"]),
                    "faces": len(faces),
                }
            )
        target.commit()
    (data / "faces").mkdir()
    (data / "thumbs").mkdir()
    return records


def mean_difference(viewer_bytes: bytes, export_path: Path) -> float:
    with Image.open(io.BytesIO(viewer_bytes)).convert("RGB") as viewer:
        with Image.open(export_path).convert("RGB") as exported:
            exported = exported.resize(viewer.size, Image.Resampling.LANCZOS)
            return sum(ImageStat.Stat(ImageChops.difference(viewer, exported)).mean) / 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    source_db = args.data.resolve() / "library.sqlite3"
    uri = f"file:{source_db.as_posix()}?mode=ro"
    WORK.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(uri, uri=True) as source:
        source.row_factory = sqlite3.Row
        rows = choose_assets(source)
        with tempfile.TemporaryDirectory(dir=WORK, ignore_cleanup_errors=True) as folder:
            run = Path(folder)
            records = seed_isolated(run / "data", rows, source)
            port = free_port()
            url = f"http://127.0.0.1:{port}"
            env = os.environ.copy()
            env.update(
                {
                    "PHOTO_LIBRARY_DATA": str(run / "data"),
                    "PHOTO_WEB_ROOT": str(ROOT / "web"),
                    "PHOTO_MODEL_ROOT": str(run / "no-model"),
                    "PHOTO_GEO_ROOT": str(run / "no-geo"),
                    "NO_ALBUMENTATIONS_UPDATE": "1",
                }
            )
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "app.py"), "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            results = []
            browser = None
            try:
                wait_ready(url)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                    page = browser.new_page(
                        viewport={"width": 1440, "height": 920},
                        accept_downloads=True,
                    )
                    page.goto(url, wait_until="domcontentloaded")
                    for record, theme, clicks in zip(
                        records,
                        ("classic", "ivory", "tea"),
                        (0, 1, 2),
                    ):
                        page.evaluate(
                            """id => openPhoto(id,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})""",
                            record["aid"],
                        )
                        page.wait_for_function(
                            "id => state.detail?.id===id && document.querySelector('#detail-img').naturalWidth>0 && !document.querySelector('#detail-dialog').classList.contains('is-loading')",
                            arg=record["aid"],
                        )
                        page.evaluate("theme => applyFacePreset(theme)", theme)
                        for _ in range(clicks):
                            page.locator("#photo-signature .signature-switch").click()
                        page.wait_for_timeout(200)
                        viewer = page.locator("#photo-mat").screenshot(
                            animations="disabled",
                            style=".viewer-photo-close,.photo-favorite,.photo-place-map,.signature-switch,.signature-info-popover{display:none!important}",
                        )
                        with page.expect_download(timeout=60_000) as info:
                            page.locator("#export-annotated-photo").click()
                        output = run / f"real-{record['aid']}.jpg"
                        info.value.save_as(output)
                        with Image.open(output) as exported:
                            if exported.format != "JPEG" or exported.width < record["width"]:
                                raise AssertionError("真实照片没有按原图宽度导出 JPEG")
                        difference = mean_difference(viewer, output)
                        if difference >= 18:
                            raise AssertionError(f"真实照片版式对照差异过大: {difference:.3f}")
                        results.append(
                            {
                                "sample": record["aid"],
                                "faces": record["faces"],
                                "source_pixels": [record["width"], record["height"]],
                                "mean_pixel_difference": round(difference, 3),
                            }
                        )
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
            if any(
                hashlib.sha256(record["path"].read_bytes()).hexdigest() != record["sha256"]
                for record in records
            ):
                raise AssertionError("真实照片 SHA256 发生变化")
    REPORT.write_text(
        json.dumps(
            {
                "passed": True,
                "source_database": "formal-read-only",
                "isolated_runtime": True,
                "photos_persisted": False,
                "samples": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("PASS real-photo read-only export:", json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
