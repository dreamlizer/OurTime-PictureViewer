"""Isolated checks for portable defaults. Never uses the formal library or port 8765."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_m1_lifecycle import (
    PYTHON,
    ROOT,
    WORK,
    api,
    env_for,
    free_port,
    graceful_stop,
    start_server,
    wait_health,
)


class ReleaseReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        WORK.mkdir(parents=True, exist_ok=True)

    def test_isolated_new_user_defaults(self) -> None:
        with tempfile.TemporaryDirectory(prefix="拾光 新用户 ", dir=WORK) as parent:
            data = Path(parent) / "用户数据"
            port = free_port()
            old = {
                key: os.environ.get(key)
                for key in ("PHOTO_FOLDER_ONLY_DRIVE", "PHOTO_OBJECTS_ENABLED")
            }
            os.environ["PHOTO_FOLDER_ONLY_DRIVE"] = ""
            os.environ["PHOTO_OBJECTS_ENABLED"] = "0"
            process = start_server(data, port)
            try:
                health = wait_health(port)
                self.assertTrue(health["ok"])
                self.assertEqual(Path(health["data_dir"]).resolve(), data.resolve())
                self.assertNotEqual(port, 8765)
                status = api(port, "/api/status")
                cap = status["capabilities"]
                self.assertEqual(status["stats"]["assets"], 0)
                self.assertFalse(cap["face_model"])
                self.assertFalse(cap["geo"])
                self.assertFalse(cap["objects_enabled"])
                self.assertFalse(cap.get("folder_only_drive"))
                drives = api(port, "/api/drives")
                folders = api(port, "/api/folders?scope=browse")
                self.assertEqual(
                    [item["path"].upper() for item in folders["items"]],
                    [root.upper() for root in drives["roots"]],
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    api(port, "/api/objects/probe", {"limit": 1}, method="POST")
                self.assertEqual(raised.exception.code, 403)
                conn = sqlite3.connect(data / "library.sqlite3")
                try:
                    conn.execute("PRAGMA foreign_keys=ON")
                    self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
                    self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())
                finally:
                    conn.close()
            finally:
                graceful_stop(process, port)
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_scan_with_faces_rejected_without_model(self) -> None:
        with tempfile.TemporaryDirectory(prefix="拾光 缺模型 ", dir=WORK) as parent:
            data = Path(parent) / "data"
            port = free_port()
            env = env_for(data, port)
            env["PHOTO_FOLDER_ONLY_DRIVE"] = ""
            process = start_server(data, port)
            try:
                wait_health(port)
                sample = Path(parent) / "photos"
                sample.mkdir()
                (sample / "a.jpg").write_bytes(
                    Path(__file__).resolve().parents[1].joinpath("web", "Logo.png").read_bytes()
                    if False else b"not-a-real-jpeg"
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    api(port, "/api/scan", {
                        "roots": [str(sample)],
                        "with_faces": True,
                        "include_system": False,
                        "workers": 1,
                    }, method="POST")
                self.assertEqual(raised.exception.code, 409)
            finally:
                graceful_stop(process, port)

    def test_template_report_cannot_overwrite_source(self) -> None:
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "build_person_templates", ROOT / "tools" / "build_person_templates.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="templates-", dir=WORK) as folder:
            root = Path(folder)
            source = root / "source.sqlite3"
            output = root / "out.sqlite3"
            source.write_bytes(b"not-a-db")
            with self.assertRaises(ValueError):
                module.ensure_distinct_template_paths(source, output, source)

    def test_export_url_whitelist(self) -> None:
        from photo_export import _export_url_allowed

        base = "http://127.0.0.1:8766/"
        self.assertTrue(_export_url_allowed(base + "api/health", base, 12))
        self.assertTrue(_export_url_allowed(base + "api/original/12", base, 12))
        self.assertTrue(_export_url_allowed(base + "api/face-label-bg/1.png", base, 12))
        self.assertTrue(_export_url_allowed(base + "vendor/fonts/demo.ttf", base, 12))
        self.assertFalse(_export_url_allowed(base + "api/original/99", base, 12))
        self.assertFalse(_export_url_allowed("https://example.com/", base, 12))
        self.assertFalse(_export_url_allowed("file:///C:/secret", base, 12))

    def test_public_defaults_are_not_author_machine_paths(self) -> None:
        start = (ROOT / "start.ps1").read_text(encoding="utf-8")
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("PycharmProjects", start)
        self.assertNotIn("G:/CodexModels", app)
        self.assertNotIn("G:\\CodexModels", app)
        self.assertNotIn("FOLDER_ONLY_DRIVE", js)
        self.assertNotIn("本地版 / 03", html)
        self.assertIn("拾光相册", html)


if __name__ == "__main__":
    unittest.main()
