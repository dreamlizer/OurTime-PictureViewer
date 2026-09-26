"""Assemble a portable Windows folder: launcher exe + App + Data."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import sysconfig
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_HOME = Path(sys.base_prefix)
VENV_SITE = Path(sysconfig.get_paths()["purelib"])
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")

ROOT_DISTS = [
    "fastapi", "uvicorn", "Pillow", "insightface", "onnxruntime",
    "opencv-python-headless", "numpy", "defusedxml", "pypinyin", "playwright",
]
EXCLUDE_DISTS = {
    "torch", "torchvision", "torchaudio", "paddlepaddle", "paddle", "paddlex",
    "transformers", "modelscope", "PyQt5", "pandas", "scikit-learn", "sklearn",
    "sympy", "google-api-python-client", "tensorboard", "jupyter",
}
# Every root-level application module ships. Keep the historical names as a
# floor so a future import refactor cannot silently drop a known dependency.
APP_PY_REQUIRED = {
    "app.py", "ourtime_config.py", "browse_queries.py", "geo_labels.py",
    "library_db.py", "map_area_service.py", "metadata_reader.py",
    "recent_operations.py", "object_labels.py", "photo_export.py",
    "home_recommendations.py", "memory_curation.py", "face_split_batch.py",
}
APP_STATIC = ["requirements.txt", "Logo.png", "AppIcon.png"]
FACE_LABEL_FILES = ("1.png", "4.png", "7.png", "8.png")
GEO_FILES = [
    "geonames/cities500.zip",
    "geonames/countryInfo.txt",
    "osm/beijing-places.json",
    "china-admin/extracted/ok_data_level4.csv",
    "beijing/geonames_beijing_admin1_22.txt",
    "china-boundaries/extracted/ok_geo.csv",
    "scenic/scenic_areas_metadata.csv",
]
LIB_SKIP = {"site-packages", "test", "tests", "idlelib", "turtledemo", "tkinter", "ensurepip"}


def load_local_settings() -> dict:
    merged: dict = {}
    for name in ("config.json", "config.local.json"):
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            merged.update(payload)
    return merged


SETTINGS = load_local_settings()


def app_python_names() -> list[str]:
    names = sorted(path.name for path in ROOT.glob("*.py") if path.is_file())
    missing = sorted(APP_PY_REQUIRED - set(names))
    if missing:
        raise RuntimeError("缺少应用模块：" + ", ".join(missing))
    return names


def face_label_source() -> Path | None:
    configured = os.environ.get("OURTIME_PACKAGE_FACE_LABEL_DIR") or SETTINGS.get("face_label_dir")
    candidates: list[Path] = []
    if configured:
        path = Path(str(configured))
        if not path.is_absolute():
            path = ROOT / path
        candidates.append(path)
    candidates.append(ROOT / "data" / "人名标签")
    candidates.append(ROOT / "web" / "assets" / "face-labels")
    for folder in candidates:
        if all((folder / name).is_file() for name in FACE_LABEL_FILES):
            return folder
    return None



def configured_path(env_name: str, setting_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name) or SETTINGS.get(setting_name) or default
    path = Path(str(raw))
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


MODEL_ROOT = configured_path(
    "OURTIME_PACKAGE_MODEL_ROOT", "model_root", ROOT / "resources" / "models"
)
MODEL_SRC = MODEL_ROOT / "models" / "buffalo_l"
GEO_SRC = configured_path(
    "OURTIME_PACKAGE_GEO_ROOT", "geo_root", ROOT / "resources" / "geo"
)


def chromium_source() -> Path:
    configured = os.environ.get("OURTIME_PACKAGE_CHROMIUM") or SETTINGS.get("chromium_path")
    if configured:
        chrome = configured_path(
            "OURTIME_PACKAGE_CHROMIUM", "chromium_path", Path(str(configured))
        )
        if chrome.is_file():
            return chrome.parent.parent
        if chrome.is_dir():
            return chrome
    return Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1200"


CHROMIUM_SRC = chromium_source()


def log(msg: str) -> None:
    print(msg, flush=True)


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src: Path, dst: Path, skip_names: set[str] | None = None) -> None:
    skip_names = skip_names or set()
    if not src.exists():
        raise FileNotFoundError(src)
    dst.mkdir(parents=True, exist_ok=True)
    for current, dirs, files in os.walk(src):
        rel = Path(current).relative_to(src)
        dirs[:] = [d for d in dirs if d not in skip_names and d != "__pycache__"]
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            if name.endswith(".pyc"):
                continue
            copy_file(Path(current) / name, target_dir / name)


def needed_dists() -> list[str]:
    from importlib.metadata import distribution, requires

    seen: set[str] = set()
    stack = list(ROOT_DISTS)
    names: list[str] = []
    while stack:
        name = stack.pop()
        key = name.lower().replace("_", "-")
        if key in seen or key in EXCLUDE_DISTS or name.lower() in EXCLUDE_DISTS:
            continue
        seen.add(key)
        try:
            dist = distribution(name)
        except Exception:
            continue
        names.append(dist.metadata["Name"] if dist.metadata.get("Name") else name)
        reqs = requires(dist.metadata["Name"]) or []
        for raw in reqs:
            text = str(raw)
            if "extra ==" in text:
                continue
            dep = text.split(";")[0].strip()
            for sep in (" ", "<", ">", "=", "[", "!"):
                dep = dep.split(sep, 1)[0]
            if dep:
                stack.append(dep)
    return names


def copy_dist(site: Path, dest_site: Path, dist_name: str) -> None:
    from importlib.metadata import distribution

    dist = distribution(dist_name)
    top = []
    try:
        top = (dist.read_text("top_level.txt") or "").splitlines()
    except Exception:
        top = []
    top = [t.strip().replace("/", ".") for t in top if t.strip()]
    if not top:
        top = [dist_name.split("-")[0].replace("-", "_")]
    for module in top:
        folder = site / module
        file_py = site / (module + ".py")
        if folder.is_dir():
            copy_tree(folder, dest_site / module)
        elif file_py.is_file():
            copy_file(file_py, dest_site / file_py.name)
        libs = site / (module + ".libs")
        if libs.is_dir():
            copy_tree(libs, dest_site / libs.name)
    keys = {
        dist.metadata["Name"].lower(),
        dist.metadata["Name"].lower().replace("-", "_"),
        dist_name.lower(),
        dist_name.lower().replace("-", "_"),
    }
    for item in site.iterdir():
        low = item.name.lower()
        if item.is_dir() and low.endswith(".dist-info"):
            stem = low[: -len(".dist-info")].rsplit("-", 1)[0]
            if stem in keys or stem.replace("_", "-") in keys:
                copy_tree(item, dest_site / item.name)


def copy_python(dest: Path) -> None:
    log("Copying portable Python...")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        copy_file(PY_HOME / name, dest / name)
    for dll in ("msvcp140.dll", "msvcp140_1.dll"):
        src = Path(r"C:\Windows\System32") / dll
        if src.is_file():
            copy_file(src, dest / dll)
    copy_tree(PY_HOME / "DLLs", dest / "DLLs", skip_names={"__pycache__"})
    copy_tree(PY_HOME / "Lib", dest / "Lib", skip_names=LIB_SKIP)
    (dest / "python312._pth").write_text(
        "DLLs" + chr(10) + "Lib" + chr(10) + "." + chr(10) + ".." + chr(10) + "import site" + chr(10),
        encoding="ascii",
    )


def copy_runtime(dest_app: Path) -> None:
    src = ROOT / "runtime"
    dest = dest_app / "runtime"
    dest.mkdir(parents=True, exist_ok=True)
    for name in src.iterdir():
        if name.name in {"face-gpu", "PIL", "defusedxml"}:
            continue
        if name.suffix.lower() in {".dll", ".pyd"} or name.name.startswith("pillow_heif") or name.name == "pillow_heif":
            if name.is_dir():
                copy_tree(name, dest / name.name)
            else:
                copy_file(name, dest / name.name)


def verify_app_imports(python_exe: Path, app_dir: Path) -> None:
    log("Verifying packed application imports...")
    modules = [Path(name).stem for name in app_python_names()]
    code = "import " + ", ".join(modules)
    env = os.environ.copy()
    env["PYTHONHOME"] = str(app_dir / "python")
    env["PYTHONPATH"] = str(app_dir)
    env["PYTHONUTF8"] = "1"
    env["NO_ALBUMENTATIONS_UPDATE"] = "1"
    proc = subprocess.run(
        [str(python_exe), "-c", code],
        cwd=str(app_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError("packed app import failed:\n" + (proc.stderr or proc.stdout or ""))
    log("app imports ok")


def fill_packed_imports(python_exe: Path, dest_site: Path) -> None:
    log("Filling missing imports...")
    check = "import fastapi, uvicorn, insightface, onnxruntime, cv2, PIL, playwright"
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
    for _ in range(25):
        proc = subprocess.run(
            [str(python_exe), "-c", check],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if proc.returncode == 0:
            log("imports ok")
            return
        err = proc.stderr or ""
        if "No module named " not in err:
            log(err[-500:])
            raise RuntimeError("packed python import failed")
        name = err.split("No module named ", 1)[1].strip().strip("'\"").split(".")[0]
        folder = VENV_SITE / name
        file_py = VENV_SITE / (name + ".py")
        pyds = list(VENV_SITE.glob(name + "*.pyd"))
        if folder.is_dir():
            copy_tree(folder, dest_site / name)
            log("  filled " + name)
        elif file_py.is_file():
            copy_file(file_py, dest_site / file_py.name)
            log("  filled " + name)
        elif pyds:
            for pyd in pyds:
                copy_file(pyd, dest_site / pyd.name)
            log("  filled pyd " + name)
        else:
            raise RuntimeError("missing package not found: " + name)
    raise RuntimeError("too many missing imports")


def make_icon(dest: Path) -> Path:
    from PIL import Image

    src = ROOT / "AppIcon.png"
    ico = dest / "logo.ico"
    img = Image.open(src).convert("RGBA")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    canvases = []
    for size in sizes:
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        fitted = img.copy()
        fitted.thumbnail(size, Image.Resampling.LANCZOS)
        x = (size[0] - fitted.size[0]) // 2
        y = (size[1] - fitted.size[1]) // 2
        canvas.paste(fitted, (x, y), fitted)
        canvases.append(canvas)
    canvases[0].save(ico, format="ICO", sizes=[im.size for im in canvases], append_images=canvases[1:])
    return ico


def compile_launcher(app_root: Path, ico: Path) -> None:
    if not CSC.is_file():
        raise RuntimeError("未找到 csc.exe，无法编译启动器")
    cs = ROOT / "tools" / "packaging" / "OurTimeLauncher.cs"
    out_start = app_root / "拾光.exe"
    cmd = [
        str(CSC), "/nologo", "/optimize+", "/target:winexe",
        "/r:System.Windows.Forms.dll", "/r:System.dll",
        "/r:System.Drawing.dll",
        "/win32icon:" + str(ico),
        "/out:" + str(out_start),
        str(cs),
    ]
    subprocess.check_call(cmd)
    shutil.copy2(out_start, app_root / "停止拾光.exe")


def copy_public_faces(app_dir: Path) -> None:
    """Ship public-figure embeddings only.

    Reference photos stay out of the green package: the pack is face
    identity data (names + embeddings), not a photo gallery. User personal
    faces remain exclusively under Data/.
    """
    src = ROOT / "resources" / "public-faces" / "public-faces.sqlite3"
    if not src.is_file():
        log("WARNING: resources/public-faces/public-faces.sqlite3 missing; public-figure pack not included")
        return
    dest = app_dir / "resources" / "public-faces"
    dest.mkdir(parents=True, exist_ok=True)
    log("Copying public-figure face embeddings (no reference photos)...")
    copy_file(src, dest / "public-faces.sqlite3")
    readme = ROOT / "resources" / "public-faces" / "README.md"
    if readme.is_file():
        copy_file(readme, dest / "README.md")
    log("  public-faces.sqlite3 only")


def write_config(app_dir: Path) -> None:
    (app_dir / "config.json").write_text(
        """{
  "data_dir": "../Data",
  "model_root": "resources/models",
  "geo_root": "resources/geo",
  "face_label_dir": "web/assets/face-labels",
  "chromium_path": "browsers/chromium-1200/chrome-win64/chrome.exe",
  "folder_only_drive": "",
  "objects_enabled": false,
  "public_figures_shelf": true,
  "public_figures_pack": "resources/public-faces/public-faces.sqlite3"
}
""",
        encoding="utf-8",
    )


def write_pack_readme(root: Path) -> None:
    (root / "使用说明.txt").write_text(
        "拾光相册 绿色版" + chr(10) + chr(10)
        + "请先解压到普通文件夹，再双击 拾光.exe。不要在压缩包预览里直接打开。" + chr(10)
        + "启动没有黑框。第一次没有照片时，会弹出窗口：把文件夹拖进去或点选。" + chr(10)
        + "关掉网页后约 8 秒后台会退出；正在扫描时会等扫描告一段落。也可双击 停止拾光.exe。" + chr(10)
        + "原照片不会移动。资料在 Data 文件夹。" + chr(10)
        + "人物识别、离线地名、公众人物人脸特征和带标签导出已随包提供；地图底图仍需联网。" + chr(10)
        + "App 里是随程序提供的公众人物人脸特征，不含参考照片；你的私人人物、人脸裁剪和缩略图只在 Data。" + chr(10),
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(root: Path) -> None:
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            ).strip()
        )
    except Exception:
        revision = "unknown"
        dirty = True
    relative_files = [
        "拾光.exe",
        "停止拾光.exe",
        "App/app.py",
        "App/home_recommendations.py",
        "App/memory_curation.py",
        "App/face_split_batch.py",
        "App/web/app.js",
        "App/web/vendor/fonts/lxgw-wenkai-screen/LXGWWenKaiGBScreen.ttf",
        "App/AppIcon.png",
        "App/resources/models/models/buffalo_l/det_10g.onnx",
        "App/resources/models/models/buffalo_l/w600k_r50.onnx",
        "App/resources/geo/geonames/cities500.zip",
        "App/resources/geo/china-admin/extracted/ok_data_level4.csv",
        "App/resources/public-faces/public-faces.sqlite3",
        "App/web/assets/face-labels/1.png",
        "App/web/assets/face-labels/4.png",
        "App/web/assets/face-labels/7.png",
        "App/web/assets/face-labels/8.png",
        "App/browsers/chromium-1200/chrome-win64/chrome.exe",
    ]
    manifest = {
        "format_version": 1,
        "product": "拾光相册",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_revision": revision,
        "source_dirty": dirty,
        "files": {
            rel: file_sha256(root / Path(rel))
            for rel in relative_files
            if (root / Path(rel)).is_file()
        },
    }
    (root / "PACKAGE-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    dest_root = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else ROOT / "dist" / "拾光相册-绿色版"
    )
    if dest_root.exists():
        log("Removing previous pack: " + str(dest_root))
        shutil.rmtree(dest_root)
    app_dir = dest_root / "App"
    data_dir = dest_root / "Data"
    app_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (data_dir / "thumbs").mkdir()
    (data_dir / "faces").mkdir()

    if not PY_HOME.is_dir():
        raise SystemExit("未找到本机 Python 3.12：" + str(PY_HOME))
    if not VENV_SITE.is_dir():
        raise SystemExit("未找到可用的 site-packages：" + str(VENV_SITE))
    if not MODEL_SRC.is_dir():
        raise SystemExit("未找到 buffalo_l 模型：" + str(MODEL_SRC))
    missing_geo = [rel for rel in GEO_FILES if not (GEO_SRC / rel).is_file()]
    if missing_geo:
        raise SystemExit("缺少打包所需地名数据：" + ", ".join(missing_geo))
    if not CHROMIUM_SRC.is_dir():
        raise SystemExit("未找到打包所需 Chromium：" + str(CHROMIUM_SRC))

    copy_python(app_dir / "python")
    dest_site = app_dir / "python" / "Lib" / "site-packages"
    dest_site.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
    log("Copying Python packages...")
    sys.path.insert(0, str(VENV_SITE))
    for name in needed_dists():
        log("  " + name)
        try:
            copy_dist(VENV_SITE, dest_site, name)
        except Exception as exc:
            log("  skip " + name + ": " + str(exc))
    fill_packed_imports(app_dir / "python" / "python.exe", dest_site)

    log("Copying application files...")
    for name in app_python_names():
        copy_file(ROOT / name, app_dir / name)
        log("  " + name)
    for name in APP_STATIC:
        copy_file(ROOT / name, app_dir / name)
    copy_tree(ROOT / "web", app_dir / "web", skip_names={"__pycache__"})
    label_src = face_label_source()
    if label_src:
        log("Copying face-label plates from " + str(label_src))
        label_dst = app_dir / "web" / "assets" / "face-labels"
        label_dst.mkdir(parents=True, exist_ok=True)
        for name in FACE_LABEL_FILES:
            copy_file(label_src / name, label_dst / name)
    else:
        log("WARNING: face-label plates not found; classic fallback only")
    copy_runtime(app_dir)
    if (ROOT / "tools" / "exiftool").is_dir():
        copy_tree(ROOT / "tools" / "exiftool", app_dir / "tools" / "exiftool")
    log("Copying InsightFace buffalo_l...")
    copy_tree(MODEL_SRC, app_dir / "resources" / "models" / "models" / "buffalo_l")
    log("Copying geo data...")
    for rel in GEO_FILES:
        src = GEO_SRC / rel
        if src.is_file():
            copy_file(src, app_dir / "resources" / "geo" / rel)
        else:
            raise RuntimeError("缺少地名数据：" + str(src))
    copy_public_faces(app_dir)

    log("Copying Chromium for annotated export...")
    copy_tree(CHROMIUM_SRC, app_dir / "browsers" / "chromium-1200")

    write_config(app_dir)
    verify_app_imports(app_dir / "python" / "python.exe", app_dir)
    for doc in ("README.md", "THIRD_PARTY_NOTICES.md", "docs/SETUP.md", "docs/PRIVACY-AND-DATA.md"):
        src = ROOT / doc
        if src.is_file():
            copy_file(src, dest_root / Path(doc).name)
    write_pack_readme(dest_root)
    ico = make_icon(app_dir)
    log("Compiling launcher...")
    compile_launcher(dest_root, ico)
    write_manifest(dest_root)

    zip_path = dest_root.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    log("Creating zip...")
    shutil.make_archive(
        str(zip_path.with_suffix("")),
        "zip",
        root_dir=dest_root.parent,
        base_dir=dest_root.name,
    )
    size = sum(p.stat().st_size for p in dest_root.rglob("*") if p.is_file())
    log("DONE folder=" + str(dest_root))
    log("DONE zip=" + str(zip_path))
    log("Folder size bytes=" + str(size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
