"""Focused renderer contract for the corrected annotated-photo export."""
from __future__ import annotations

import hashlib
import http.server
import io
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from photo_export import render_annotated_image


class AnnotatedExportFixTests(unittest.TestCase):
    def test_frozen_v2_snapshot_renders_jpeg_by_default_and_optional_png(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            source = temp / "source.jpg"
            picture = Image.new("RGB", (3200, 2000), "#d9c9a9")
            draw = ImageDraw.Draw(picture)
            for x in range(0, 3200, 20):
                draw.line((x, 0, x, 2000), fill=(x % 255, 80, 110), width=2)
            for y in range(0, 2000, 20):
                draw.line((0, y, 3200, y), fill=(80, y % 255, 110), width=1)
            picture.save(source, quality=96)
            before = hashlib.sha256(source.read_bytes()).hexdigest()

            class Handler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_GET(self):
                    if self.path == "/api/original/1":
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.end_headers()
                        self.wfile.write(source.read_bytes())
                        return
                    self.send_error(404)

            server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            snapshot = {
                "snapshot_version": 2,
                "geometry": {
                    "image_width": 800,
                    "image_height": 500,
                    "mat_width": 800,
                    "mat_height": 610,
                },
                "dialog_attrs": {
                    "data-face-theme": "classic",
                    "data-signature-mode": "medium",
                },
                "mat_html": """
                  <figure id="photo-mat" style="position:relative;width:800px;height:610px;margin:0;background:#fff">
                    <img id="detail-img" style="display:block;width:800px;height:500px">
                    <div id="face-name-layer" style="position:absolute;inset:0 0 110px;display:block">
                      <button class="face-name" style="position:absolute;left:120px;top:150px;padding:5px 7px;border:0;border-radius:4px;background:rgba(91,57,35,.7);color:#fff;font:15px/1.35 'Microsoft YaHei';writing-mode:vertical-rl">王小明</button>
                    </div>
                    <figcaption id="photo-signature" style="display:flex;width:800px;height:110px;padding:18px 24px 9px;box-sizing:border-box;background:#fff;color:#242424">
                      <div class="signature-v3" style="display:grid;width:100%;grid-template-columns:1fr 1fr;align-items:end">
                        <strong style="font:24px/1.2 serif">2026.09.14</strong>
                        <small style="justify-self:end;font:12px/1.4 sans-serif">SONY · 35mm · ƒ/2</small>
                      </div>
                    </figcaption>
                  </figure>
                """,
            }
            try:
                jpeg = render_annotated_image(
                    original=source,
                    snapshot=snapshot,
                    base_url=f"http://127.0.0.1:{port}/",
                    web_root=ROOT / "web",
                    aid=1,
                )
                png = render_annotated_image(
                    original=source,
                    snapshot=snapshot,
                    base_url=f"http://127.0.0.1:{port}/",
                    web_root=ROOT / "web",
                    aid=1,
                    output_format="png",
                )
            finally:
                server.shutdown()
                server.server_close()

            self.assertTrue(jpeg.startswith(b"\xff\xd8\xff"))
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(jpeg)) as result:
                self.assertEqual(result.width, 3200)
                self.assertGreater(result.height, 2000)
            self.assertLess(len(jpeg), len(png))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_v1_snapshot_and_unknown_format_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.jpg"
            Image.new("RGB", (100, 100), "white").save(source)
            with self.assertRaisesRegex(ValueError, "刷新"):
                render_annotated_image(
                    original=source,
                    snapshot={"mat_html": "<figure id='photo-mat'></figure>"},
                    base_url="http://127.0.0.1:1/",
                    web_root=ROOT / "web",
                    aid=1,
                )
            with self.assertRaisesRegex(ValueError, "格式"):
                render_annotated_image(
                    original=source,
                    snapshot={"snapshot_version": 2},
                    base_url="http://127.0.0.1:1/",
                    web_root=ROOT / "web",
                    aid=1,
                    output_format="webp",
                )


if __name__ == "__main__":
    unittest.main()
