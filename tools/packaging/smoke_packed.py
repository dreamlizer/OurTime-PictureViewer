"""Clean-room acceptance for the final green-package ZIP.

Runs only against fresh extractions, isolated data, and random ports.
It never reads or writes the formal library.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ZIP = SOURCE_ROOT / "dist" / "拾光相册-绿色版.zip"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ACTIVE_SERVICES: list[tuple[Path, int]] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(port: int, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)


def wait_health(port: int, timeout: float = 45) -> dict:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            payload = request_json(port, "/api/health")
            if payload.get("ok"):
                return payload
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise AssertionError(f"成品服务未在 {timeout:g} 秒内就绪：{last_error}")


def wait_job(port: int, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request_json(port, "/api/status").get("job")
        if job and job.get("status") not in {"running", "pausing"}:
            return job
        time.sleep(0.5)
    raise AssertionError("成品扫描未在限时内完成")


def package_environment(root: Path, port: int) -> dict:
    app = root / "App"
    env = os.environ.copy()
    env.update(
        {
            "PHOTO_LIBRARY_DATA": str(root / "Data"),
            "PHOTO_LIBRARY_PORT": str(port),
            "PHOTO_MODEL_ROOT": str(app / "resources" / "models"),
            "PHOTO_GEO_ROOT": str(app / "resources" / "geo"),
            "PHOTO_CHROMIUM": str(
                app / "browsers" / "chromium-1200" / "chrome-win64" / "chrome.exe"
            ),
            "PLAYWRIGHT_BROWSERS_PATH": str(app / "browsers"),
            "PHOTO_NO_BROWSER": "1",
            "PHOTO_NO_DIALOG": "1",
            "NO_ALBUMENTATIONS_UPDATE": "1",
            "PYTHONUTF8": "1",
        }
    )
    return env


def unpack(zip_path: Path, parent: Path, name: str) -> Path:
    target = parent / name
    target.mkdir()
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)
    children = [item for item in target.iterdir() if item.is_dir()]
    check(len(children) == 1, f"{name} 解压后只有一个成品根目录")
    return children[0]


def validate_archive(zip_path: Path) -> None:
    check(zip_path.is_file(), "最终 ZIP 已生成")
    with zipfile.ZipFile(zip_path) as archive:
        bad = []
        data_files = []
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            parts = [part for part in normalized.split("/") if part]
            if normalized.startswith("/") or ".." in parts:
                bad.append(normalized)
            if len(parts) >= 2 and parts[1].lower() == "data" and not info.is_dir():
                data_files.append(normalized)
        check(not bad, "ZIP 内没有绝对路径或越界路径")
        check(not data_files, "ZIP 的 Data 目录为空，不含正式资料库或照片")
        public_in_data = [
            normalized
            for normalized in (
                info.filename.replace("\\", "/") for info in archive.infolist()
            )
            if normalized.startswith("Data/") and "public-faces" in normalized
        ]
        check(not public_in_data, "ZIP 的用户 Data 不含公众人物资源")
        names = {item.filename.replace("\\", "/") for item in archive.infolist()}
        check(
            not any(name.endswith("/config.local.json") for name in names),
            "ZIP 不含作者电脑的 config.local.json",
        )
        check(
            any(name.endswith("/PACKAGE-MANIFEST.json") for name in names),
            "ZIP 含构建清单",
        )
    check(zipfile.is_zipfile(zip_path), "ZIP 结构可正常读取")


def verify_manifest(root: Path) -> None:
    manifest = json.loads((root / "PACKAGE-MANIFEST.json").read_text(encoding="utf-8"))
    files = manifest.get("files") or {}
    check(len(files) >= 8, "构建清单覆盖启动器、代码、模型、地名库和浏览器")
    for relative, expected in files.items():
        path = root / Path(relative)
        check(path.is_file() and sha256(path) == expected, f"清单哈希正确：{relative}")
    check(
        sha256(root / "App" / "app.py") == sha256(SOURCE_ROOT / "app.py"),
        "包内 app.py 与当前被测源码一致",
    )
    check(
        sha256(root / "App" / "web" / "app.js")
        == sha256(SOURCE_ROOT / "web" / "app.js"),
        "包内 app.js 与当前被测源码一致",
    )
    source_modules = sorted(path.name for path in SOURCE_ROOT.glob("*.py") if path.is_file())
    missing_modules = [name for name in source_modules if not (root / "App" / name).is_file()]
    check(not missing_modules, "包内含全部根目录应用模块：" + ", ".join(source_modules))
    for name in ("1.png", "4.png", "7.png", "8.png"):
        check(
            (root / "App" / "web" / "assets" / "face-labels" / name).is_file(),
            f"包内含人名标签底板 {name}",
        )
    check(
        (root / "App" / "resources" / "public-faces" / "public-faces.sqlite3").is_file(),
        "公众人物人脸特征在程序资源里",
    )
    check(
        not (root / "App" / "resources" / "public-faces" / "references").exists(),
        "公众人物参考照片不打进绿色包",
    )
    check(
        not (root / "App" / "resources" / "public-faces" / "SEED-REPORT.json").exists(),
        "不把生成审计报告打进用户可见包",
    )
    check(
        not any((root / "Data").rglob("public-faces*")),
        "用户 Data 不含公众人物资源副本",
    )


def run_packed_python(root: Path, env: dict, code: str, timeout: int = 90) -> str:
    app = root / "App"
    python = app / "python" / "python.exe"
    python_env = env.copy()
    python_env["PYTHONHOME"] = str(app / "python")
    python_env["PYTHONPATH"] = str(app)
    completed = subprocess.run(
        [str(python), "-c", code],
        cwd=app,
        env=python_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode:
        raise AssertionError(
            "包内 Python 检查失败：\n"
            + (completed.stdout or "")
            + "\n"
            + (completed.stderr or "")
        )
    return completed.stdout.strip()


def stop_with_user_entry(root: Path, env: dict, port: int) -> None:
    completed = subprocess.run(
        [str(root / "停止拾光.exe")],
        cwd=root,
        env=env,
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    check(completed.returncode == 0, "停止拾光.exe 可安全停止隔离服务")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            request_json(port, "/api/health")
        except Exception:
            return
        time.sleep(0.25)
    raise AssertionError("停止入口返回后端口仍在监听")


def validate_real_start(root: Path, full: bool) -> None:
    port = free_port()
    env = package_environment(root, port)
    data = root / "Data"
    launcher = root / "拾光.exe"

    started = subprocess.run(
        [str(launcher), "--no-browser"],
        cwd=root,
        env=env,
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    check(started.returncode == 0, "从成品拾光.exe 启动成功")
    health = wait_health(port)
    ACTIVE_SERVICES.append((root, port))
    check(Path(health["data_dir"]).resolve() == data.resolve(), "成品只使用本次解压的空白 Data")
    first_pid = int(health["pid"])

    repeated = subprocess.run(
        [str(launcher), "--no-browser"],
        cwd=root,
        env=env,
        timeout=10,
        creationflags=CREATE_NO_WINDOW,
    )
    check(repeated.returncode == 0, "重复启动同一成品不会再起第二个后台")
    check(int(request_json(port, "/api/health")["pid"]) == first_pid, "重复启动仍由原实例响应")

    status = request_json(port, "/api/status")
    capabilities = status["capabilities"]
    check(status["stats"]["assets"] == 0, "首次启动是空白资料库")
    check(capabilities.get("face_model") is True, "InsightFace buffalo_l 模型可见")
    check(capabilities.get("geo") is True, "离线地名数据可见")
    check(capabilities.get("face_labels") is True, "人名标签底板随包可见")
    check(capabilities.get("export_renderer") is True, "带标签导出浏览器可见")
    check(capabilities.get("objects_enabled") is False, "未打包已停用的物体识别业务")

    module_output = run_packed_python(
        root,
        env,
        (
            "import json;"
            "import home_recommendations, memory_curation, face_split_batch, recent_operations;"
            "print(json.dumps({'home': home_recommendations.ALGORITHM_VERSION,"
            " 'curation': hasattr(memory_curation, 'curate_highlights'),"
            " 'split': callable(face_split_batch.suggest_split_batch)}, ensure_ascii=False))"
        ),
        timeout=90,
    )
    module_state = json.loads(module_output.splitlines()[-1])
    check(
        bool(module_state.get("home"))
        and module_state.get("curation") is True
        and module_state.get("split") is True,
        "首页推荐、回忆精选与人脸拆分模块在包内可导入",
    )
    catalog = request_json(port, "/api/home/catalog")
    check(isinstance(catalog, dict), "首页故事目录接口可响应")

    shelf = request_json(port, "/api/public-figures")
    check(isinstance(shelf, dict), "公众人物货架接口可响应")
    shelf_output = run_packed_python(
        root,
        env,
        (
            "import json,sqlite3;"
            "from pathlib import Path;"
            "from ourtime_config import PUBLIC_FIGURES_PACK, PUBLIC_FIGURES_SHELF, DATA;"
            "p=Path(PUBLIC_FIGURES_PACK);"
            "con=sqlite3.connect(p);"
            "people=con.execute('select count(*) from people').fetchone()[0];"
            "samples=con.execute('select count(*) from samples').fetchone()[0];"
            "abs_paths=con.execute(\"select count(*) from samples where relative_path like '%:%' or relative_path like '/%'\").fetchone()[0];"
            "con.close();"
            "print(json.dumps({'shelf':PUBLIC_FIGURES_SHELF,'pack':str(p),'under_app': 'resources' in p.parts and 'public-faces' in p.parts,"
            " 'people':people,'samples':samples,'abs_paths':abs_paths,"
            " 'data_has_pack':any(Path(DATA).rglob('public-faces*'))},ensure_ascii=False))"
        ),
        timeout=60,
    )
    shelf_state = json.loads(shelf_output.splitlines()[-1])
    check(shelf_state.get("shelf") is True, f"绿色包默认启用公众人物货架：{shelf_state}")
    check(
        shelf_state.get("under_app") is True and shelf_state.get("data_has_pack") is False,
        f"公众人物资源在 App、用户 Data 仍只放私人识别：{shelf_state}",
    )
    check(
        int(shelf_state.get("people") or 0) >= 1
        and int(shelf_state.get("samples") or 0) >= 1
        and int(shelf_state.get("abs_paths") or 0) == 0,
        f"公众人物库有特征且无绝对路径：{shelf_state}",
    )

    if full:
        app = root / "App"
        model_output = run_packed_python(
            root,
            env,
            (
                "import json,numpy as np;"
                "from app import get_face_engine,current_face_runtime;"
                "e=get_face_engine();"
                "faces=e.get(np.zeros((640,640,3),dtype=np.uint8));"
                "feat=e.models['recognition'].get_feat(np.zeros((112,112,3),dtype=np.uint8));"
                "print(json.dumps({'runtime':current_face_runtime(),"
                "'faces':len(faces),'embedding_shape':list(feat.shape)},ensure_ascii=False))"
            ),
            timeout=120,
        )
        check('"embedding_shape": [1, 512]' in model_output, "InsightFace 检测与 512 维识别推理实际运行")

        geo_root = app / "resources" / "geo"
        geo_output = run_packed_python(
            root,
            env,
            (
                "from pathlib import Path;"
                "from geo_labels import PlaceIndex;"
                f"p=PlaceIndex(Path(r'{geo_root}'));"
                "print(p.nearest(39.9042,116.4074)[0])"
            ),
            timeout=60,
        )
        check("北京市" in geo_output, "离线地名库实际完成北京坐标查询")

        browser_output = run_packed_python(
            root,
            env,
            (
                "from playwright.sync_api import sync_playwright;"
                "from ourtime_config import CHROMIUM_PATH;"
                "p=sync_playwright().start();"
                "b=p.chromium.launch(executable_path=str(CHROMIUM_PATH),headless=True);"
                "page=b.new_page();page.set_content('<title>拾光导出</title><b>ok</b>');"
                "print(page.title());b.close();p.stop()"
            ),
            timeout=60,
        )
        check(browser_output.endswith("拾光导出"), "包内 Chromium 可被导出链实际拉起")

        sample = (
            app
            / "python"
            / "Lib"
            / "site-packages"
            / "insightface"
            / "data"
            / "images"
            / "t1.jpg"
        )
        check(sample.is_file(), "包内含非私人 InsightFace 测试样图")
        before = sha256(sample)
        receipt = request_json(
            port,
            "/api/scan",
            {
                "roots": [str(sample.parent)],
                "with_faces": True,
                "include_system": False,
                "workers": 1,
            },
        )
        check(receipt.get("state_committed") is True, "真实扫描请求获得已提交回执")
        job = wait_job(port)
        check(job.get("status") == "completed" and job.get("errors") == 0, "成品真实人脸扫描无错误完成")
        final_status = request_json(port, "/api/status")
        check(final_status["stats"]["assets"] >= 1, "成品完成照片入库与缩略图生成")
        people = request_json(port, "/api/people?limit=50")
        check(len(people.get("items") or []) >= 1, "成品完成真实人脸检测与候选分组")
        check(sha256(sample) == before, "扫描前后测试原图哈希不变")

        viewer_output = run_packed_python(
            root,
            env,
            (
                "import json;"
                "from playwright.sync_api import sync_playwright;"
                "from ourtime_config import CHROMIUM_PATH;"
                "p=sync_playwright().start();"
                "b=p.chromium.launch(executable_path=str(CHROMIUM_PATH),headless=True);"
                "page=b.new_page(viewport={'width':1280,'height':800});"
                f"page.goto('http://127.0.0.1:{port}/');"
                "page.wait_for_selector('button[data-view=home].active',timeout=30000);"
                "page.click('button[data-view=timeline]');"
                "page.wait_for_selector('#photo-grid [data-photo]:not([data-photo=shelf]):not([data-public-rel])',timeout=30000);"
                "page.locator('#photo-grid [data-photo]:not([data-photo=shelf]):not([data-public-rel])').first.click();"
                "page.wait_for_selector('#detail-dialog[open] #export-annotated-photo',timeout=30000);"
                "result=page.evaluate(\"\"\"async()=>{"
                "  const dialog=document.querySelector('#detail-dialog');"
                "  const prev=window.setViewerLoading;"
                "  window.setViewerLoading=(loading,token=0)=>{if(!loading)return;return prev(loading,token);};"
                "  window.setViewerLoading(true,987654);"
                "  await new Promise(r=>setTimeout(r,400));"
                "  const result={"
                "    exportDisplay:getComputedStyle(document.querySelector('.photo-export-control')).display,"
                "    exportCount:document.querySelectorAll('#export-annotated-photo').length,"
                "    loadingDisplay:getComputedStyle(document.querySelector('#viewer-loading')).display,"
                "    loadingHidden:document.querySelector('#viewer-loading').hidden,"
                "    loadingText:document.querySelector('#viewer-loading').textContent,"
                "    isLoading:dialog.classList.contains('is-loading'),"
                "    exportBlocked:(()=>{try{window.__ourTimeAnnotatedExport.freeze();return false}catch(e){return String(e.message||e)}})()"
                "  };"
                "  window.setViewerLoading=prev;"
                "  window.setViewerLoading(false);"
                "  return result;"
                "}\"\"\");"
                "print(json.dumps(result,ensure_ascii=False));"
                "b.close();p.stop()"
            ),
            timeout=60,
        )
        viewer_state = json.loads(viewer_output.splitlines()[-1])
        check(viewer_state.get("loadingText") == "加载中", f"翻图加载态文案是“加载中”：{viewer_state}")
        check(viewer_state.get("loadingHidden") is False, f"加载指示在等待后可见：{viewer_state}")
        check(viewer_state.get("loadingDisplay") not in {"none", ""}, f"加载指示实际显示：{viewer_state}")
        check(viewer_state.get("exportCount") == 1, f"只保留一个导出按钮：{viewer_state}")
        check(
            isinstance(viewer_state.get("exportBlocked"), str)
            and bool(viewer_state.get("exportBlocked"))
            and "准备中" in viewer_state.get("exportBlocked", ""),
            f"加载中禁止导出并给出准备中提示：{viewer_state}",
        )

        conn = sqlite3.connect(data / "library.sqlite3")
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            check(
                {"assets", "faces", "people", "place_rules", "operations"} <= tables,
                "首次资料库含人物、地点规则和操作回执表",
            )
            check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "首次资料库完整性正常")
        finally:
            conn.close()

    stop_with_user_entry(root, env, port)
    ACTIVE_SERVICES.remove((root, port))


def validate_true_port_occupancy(root: Path) -> None:
    port = free_port()
    env = package_environment(root, port)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        listener.settimeout(0.5)
        stopped = threading.Event()

        def drain_connections() -> None:
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                except (TimeoutError, socket.timeout, OSError):
                    continue
                connection.close()

        worker = threading.Thread(target=drain_connections, daemon=True)
        worker.start()
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [str(root / "拾光.exe"), "--no-browser"],
                cwd=root,
                env=env,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            elapsed = time.monotonic() - started
        finally:
            stopped.set()
            worker.join(timeout=2)
    check(completed.returncode == 1 and elapsed < 5, "真实端口占用会立即明确失败，不会假启动")


def main() -> int:
    zip_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_ZIP
    validate_archive(zip_path)
    temp = Path(tempfile.mkdtemp(prefix="ourtime-green-accept-"))
    try:
        parent = temp
        first = unpack(zip_path, parent, "fresh-one")
        verify_manifest(first)
        validate_true_port_occupancy(first)
        validate_real_start(first, full=True)
        second = unpack(zip_path, parent, "fresh-two")
        validate_real_start(second, full=False)
    finally:
        for _, port in ACTIVE_SERVICES[:]:
            try:
                request_json(port, "/api/scan/pause", {})
            except Exception:
                pass
            try:
                request_json(port, "/api/shutdown", {})
            except Exception:
                pass
        ACTIVE_SERVICES.clear()
        time.sleep(1)
        shutil.rmtree(temp, ignore_errors=True)
    print("GREEN_PACKAGE_ACCEPTANCE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
