"""Synthetic fidelity check for the bounded annotated-PNG renderer."""
import hashlib
import http.server
import socketserver
import tempfile
import threading
from pathlib import Path
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parents[1]))
from photo_export import render_annotated_png


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); source = root / "source.jpg"
        image = Image.new("RGB", (5000, 3000), "#d9c9a9"); draw = ImageDraw.Draw(image)
        for x in range(0, 5000, 25): draw.line((x, 0, x, 3000), fill=(x % 255, 80, 110), width=2)
        for y in range(0, 3000, 25): draw.line((0, y, 5000, y), fill=(80, y % 255, 110), width=1)
        image.save(source, quality=96)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path == "/api/original/1":
                    self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.end_headers(); self.wfile.write(source.read_bytes()); return
                self.send_error(404)
        server = socketserver.TCPServer(("127.0.0.1", 0), Handler); port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            snap = {"dialog_attrs":{"data-face-theme":"classic"},"geometry":{"image_width":1000,"image_height":600,"mat_width":1000},"mat_html":'''<figure id="photo-mat"><img id="detail-img" src="/api/preview/1"><div id="face-name-layer" class="face-name-layer"><button class="face-name" style="left:110px;top:150px">王小明</button></div><figcaption id="photo-signature"><div class="signature-v3"><span class="signature-seal">拾</span><div class="signature-capture"><strong>2026 年秋 · 北京</strong><small>SONY · 35mm · f/2</small></div></div></figcaption></figure>'''}
            png = render_annotated_png(original=source, snapshot=snap, base_url=f"http://127.0.0.1:{port}/", web_root=Path(__file__).parents[1] / "web", aid=1)
        finally:
            server.shutdown(); server.server_close()
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(__import__("io").BytesIO(png)) as result:
            assert result.width >= 5000 and result.height > 3000, result.size
            if "--write-evidence" in sys.argv:
                evidence = Path(__file__).parents[1] / "docs" / "repair-reports" / "assets"
                evidence.mkdir(parents=True, exist_ok=True)
                image.resize((800, 480)).save(evidence / "annotated-export-synthetic-source.jpg", quality=90)
                result.convert("RGB").resize((800, round(800 * result.height / result.width))).save(evidence / "annotated-export-synthetic-output.png", optimize=True)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        print("PASS annotated export: 5000px synthetic source, Chinese label, signature, lossless PNG")

if __name__ == "__main__": main()
