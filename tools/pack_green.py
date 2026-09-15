"""Assemble a portable Windows folder: launcher exe + App + Data."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_HOME = Path(r"C:\Users\A\AppData\Local\Programs\Python\Python312")
VENV_SITE = Path(r"C:\Users\A\PycharmProjects\ImageBrowser\.venv\Lib\site-packages")
MODEL_SRC = Path(r"G:\CodexModels\insightface\models\buffalo_l")
GEO_SRC = Path(r"G:\CodexModels\geo")
CHROMIUM_SRC = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1200"
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
APP_PY = [
    "app.py", "ourtime_config.py", "browse_queries.py", "geo_labels.py",
    "library_db.py", "map_area_service.py", "metadata_reader.py",
    "object_labels.py", "photo_export.py", "requirements.txt", "Logo.png",
]
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
        if name.name in {"face-gpu", "onnxruntime", "PIL", "defusedxml"}:
            continue
        if name.suffix.lower() in {".dll", ".pyd"} or name.name.startswith("pillow_heif") or name.name == "pillow_heif":
            if name.is_dir():
                copy_tree(name, dest / name.name)
            else:
                copy_file(name, dest / name.name)


def fill_packed_imports(python_exe: Path, dest_site: Path) -> None:
    log("Filling missing imports...")
    check = "import fastapi, uvicorn, insightface, onnxruntime, cv2, PIL, playwright"
    for _ in range(25):
        proc = subprocess.run([str(python_exe), "-c", check], capture_output=True, text=True)
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

    src = ROOT / "Logo.png"
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


def write_config(app_dir: Path) -> None:
    (app_dir / "config.json").write_text(
        """{
  "data_dir": "../Data",
  "model_root": "resources/models",
  "geo_root": "resources/geo",
  "face_label_dir": "web/assets/face-labels",
  "chromium_path": "browsers/chromium-1200/chrome-win64/chrome.exe",
  "folder_only_drive": "",
  "objects_enabled": false
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
        + "原照片不会移动。资料在 Data 文件夹。" + chr(10),
        encoding="utf-8",
    )


def main() -> int:
    dest_root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist" / "拾光相册"
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
    for name in APP_PY:
        copy_file(ROOT / name, app_dir / name)
    copy_tree(ROOT / "web", app_dir / "web", skip_names={"__pycache__"})
    copy_runtime(app_dir)
    if (ROOT / "tools" / "exiftool").is_dir():
        copy_tree(ROOT / "tools" / "exiftool", app_dir / "tools" / "exiftool")
    labels = ROOT / "data" / "人名标签"
    if labels.is_dir():
        dest_labels = app_dir / "web" / "assets" / "face-labels"
        dest_labels.mkdir(parents=True, exist_ok=True)
        for png in labels.glob("*.png"):
            copy_file(png, dest_labels / png.name)

    log("Copying InsightFace buffalo_l...")
    copy_tree(MODEL_SRC, app_dir / "resources" / "models" / "models" / "buffalo_l")
    log("Copying geo data...")
    for rel in GEO_FILES:
        src = GEO_SRC / rel
        if src.is_file():
            copy_file(src, app_dir / "resources" / "geo" / rel)
        else:
            log("  missing geo " + rel)

    if CHROMIUM_SRC.is_dir():
        log("Copying Chromium for annotated export...")
        copy_tree(CHROMIUM_SRC, app_dir / "browsers" / "chromium-1200")
    else:
        log("Chromium-1200 not found; export-with-labels will be unavailable.")

    write_config(app_dir)
    for doc in ("README.md", "THIRD_PARTY_NOTICES.md", "docs/SETUP.md", "docs/PRIVACY-AND-DATA.md"):
        src = ROOT / doc
        if src.is_file():
            copy_file(src, dest_root / Path(doc).name)
    write_pack_readme(dest_root)
    ico = make_icon(app_dir)
    log("Compiling launcher...")
    compile_launcher(dest_root, ico)

    zip_path = dest_root.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    log("Creating zip...")
    subprocess.check_call(["tar", "-acf", str(zip_path), "-C", str(dest_root.parent), dest_root.name])
    size = sum(p.stat().st_size for p in dest_root.rglob("*") if p.is_file())
    log("DONE folder=" + str(dest_root))
    log("DONE zip=" + str(zip_path))
    log("Folder size bytes=" + str(size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
