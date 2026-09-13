"""Isolated contract checks for two-phase scan and persisted face timing."""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "validation" / "work" / ("scan-progress-" + time.strftime("%Y%m%d-%H%M%S"))
DATA = RUN / "data"
PHOTOS = RUN / "photos"
os.environ["PHOTO_LIBRARY_DATA"] = str(DATA)
os.environ["PHOTO_WEB_ROOT"] = str(ROOT / "web")
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def wait_job(job_id: str) -> dict:
    deadline = time.time() + 30
    while time.time() < deadline:
        with app.db() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row and row["status"] not in {"running", "pausing"}:
            return dict(row)
        time.sleep(0.03)
    raise TimeoutError("scan job did not finish")


def main() -> int:
    PHOTOS.mkdir(parents=True)
    for index in range(8):
        Image.new("RGB", (80, 60), (30 + index, 80, 110)).save(PHOTOS / f"photo-{index}.png")

    original_faces = app.process_faces
    original_metadata = app.metadata_for

    def fake_metadata(path: Path, stat, digest: str) -> dict:
        app.make_thumbnail(path, digest)
        return {
            "width": 80, "height": 60, "format": "PNG", "metadata": {},
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "date_source": "测试",
            "date_precision": "秒", "latitude": None, "longitude": None,
            "place": None, "place_source": None, "camera": None, "category": "照片", "error": None,
        }

    def fake_process_faces(asset_id: int, _path: Path) -> int:
        time.sleep(0.025)
        with app.db() as connection:
            connection.execute("UPDATE assets SET face_state=1,face_error=NULL WHERE id=?", (asset_id,))
        return 2

    app.metadata_for = fake_metadata
    app.process_faces = fake_process_faces
    try:
        started = app.begin_scan(app.ScanRequest(roots=[str(PHOTOS)], with_faces=True, workers=1))
        saw_face_stage = False
        deadline = time.time() + 20
        while time.time() < deadline:
            with app.PROGRESS_LOCK:
                saw_face_stage = saw_face_stage or app.PROGRESS.get("current_stage") == "faces"
            with app.db() as connection:
                state = connection.execute("SELECT status FROM jobs WHERE id=?", (started["id"],)).fetchone()[0]
            if state not in {"running", "pausing"}:
                break
            time.sleep(0.01)
        first = wait_job(started["id"])
        check(first["phase"] == "complete" and first["total"] == first["processed"] == 8, "扫描先清点总数并以 8 / 8 完成")
        check(first["discovered"] == 8 and first["metadata_reads"] == 8, "清点数量与新读取数量分别记录")
        check(first["face_photos"] == 8 and first["faces_found"] == 16, "人脸进度同时记录处理照片数与检出脸数")
        check(first["face_seconds"] >= 0.16 and saw_face_stage, "人脸阶段和累计处理耗时均被记录")

        with app.STATUS_CACHE_LOCK:
            app.STATUS_CACHE["payload"] = None
            app.STATUS_CACHE["at"] = 0
        first_status = app.status()
        expected = first["face_seconds"] / first["face_photos"]
        check(abs(first_status["face_average_seconds"] - expected) < 0.002, "平均秒数按已处理照片数加权计算")

        second_id = app.begin_scan(app.ScanRequest(roots=[str(PHOTOS)], with_faces=True, workers=1))["id"]
        second = wait_job(second_id)
        check(second["total"] == second["processed"] == second["skipped"] == 8, "增量重扫仍快速跳过全部旧照片")
        check(second["face_photos"] == 0 and second["faces_found"] == 0, "已完成人脸识别的照片不重复计时或识别")
        with app.STATUS_CACHE_LOCK:
            app.STATUS_CACHE["payload"] = None
            app.STATUS_CACHE["at"] = 0
        second_status = app.status()
        check(abs(second_status["face_average_seconds"] - expected) < 0.002, "后续任务沿用持久化历史样本的加权平均")
    finally:
        app.process_faces = original_faces
        app.metadata_for = original_metadata
        app.close_readers()
        shutil.rmtree(RUN, ignore_errors=True)
    print("SCAN_PROGRESS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
