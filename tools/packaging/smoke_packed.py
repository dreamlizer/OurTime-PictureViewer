import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "dist" / "拾光相册" / "App"
PY = APP / "python" / "python.exe"
DATA = Path(os.environ["TEMP"]) / "ourtime-green-smoke"
if DATA.exists():
    import shutil
    shutil.rmtree(DATA)
DATA.mkdir()
env = os.environ.copy()
env.update({
    "PHOTO_LIBRARY_DATA": str(DATA),
    "PHOTO_LIBRARY_PORT": "8799",
    "PHOTO_MODEL_ROOT": str(APP / "resources" / "models"),
    "PHOTO_GEO_ROOT": str(APP / "resources" / "geo"),
    "PHOTO_CHROMIUM": str(APP / "browsers" / "chromium-1200" / "chrome-win64" / "chrome.exe"),
    "PLAYWRIGHT_BROWSERS_PATH": str(APP / "browsers"),
    "NO_ALBUMENTATIONS_UPDATE": "1",
    "PYTHONUTF8": "1",
})
print("imports", flush=True)
subprocess.check_call([str(PY), "-c", "import fastapi, insightface, onnxruntime, cv2, PIL; print(onnxruntime.get_available_providers())"], env=env, cwd=str(APP))
print("server", flush=True)
proc = subprocess.Popen([str(PY), str(APP / "app.py"), "--port", "8799"], cwd=str(APP), env=env)
ok = False
try:
    for _ in range(40):
        time.sleep(0.5)
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen("http://127.0.0.1:8799/api/health", timeout=2) as resp:
                body = json.load(resp)
            if body.get("ok"):
                print("health", body)
                ok = True
                break
        except Exception:
            pass
    if not ok:
        print("HEALTH_FAIL", proc.poll())
        sys.exit(1)
    with urllib.request.urlopen("http://127.0.0.1:8799/api/status", timeout=15) as resp:
        status = json.load(resp)
    cap = status["capabilities"]
    print("face_model", cap.get("face_model"))
    print("geo", cap.get("geo"))
    print("export_renderer", cap.get("export_renderer"))
    req = urllib.request.Request("http://127.0.0.1:8799/api/shutdown", method="POST")
    urllib.request.urlopen(req, timeout=5).read()
finally:
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()
print("SMOKE_PACK_OK")
