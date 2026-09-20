"""Dry-run inventory for a future green-software package. Does not copy or zip."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ALLOW_FILES = [
    "启动拾光.vbs",
    "停止拾光.vbs",
    "重启拾光.cmd",
    "launch.ps1",
    "start.ps1",
    "stop.ps1",
    "restart.ps1",
    "ourtime-config.ps1",
    "app.py",
    "ourtime_config.py",
    "browse_queries.py",
    "geo_labels.py",
    "library_db.py",
    "map_area_service.py",
    "recent_operations.py",
    "home_recommendations.py",
    "metadata_reader.py",
    "object_labels.py",
    "photo_export.py",
    "requirements.txt",
    "config.example.json",
    "README.md",
    "CHANGELOG.md",
    "THIRD_PARTY_NOTICES.md",
    "Logo.png",
]

ALLOW_DIRS = [
    "web",
    "tools/exiftool",
    "docs",
    "resources",
]

FORBID_GLOBS = [
    "data/**",
    "runtime/**",
    ".venv/**",
    ".git/**",
    "validation/work/**",
    "validation/reports/**",
    "config.local.json",
    "config.json",
    "*.sqlite3",
    "*.sqlite3-wal",
    "*.sqlite3-shm",
    "人物特征与姓名-*.sqlite3",
    "人物紧凑模板-*.sqlite3",
    "OurTime_*.zip",
]


def is_forbidden(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("data/") or rel == "data":
        return True
    if rel.startswith(".git/") or rel == ".git":
        return True
    if rel.startswith("runtime/") or rel.startswith(".venv/"):
        return True
    if path.name in {"config.local.json", "config.json"}:
        return True
    if path.suffix.lower() in {".sqlite3", ".wal", ".shm"}:
        return True
    if rel.endswith(".zip") and rel.startswith("OurTime_"):
        return True
    return False


def main() -> int:
    present = []
    missing = []
    for name in ALLOW_FILES:
        path = ROOT / name
        item = {"path": name, "present": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0}
        (present if item["present"] else missing).append(item)
    for name in ALLOW_DIRS:
        path = ROOT / name
        item = {"path": name + "/", "present": path.is_dir(), "bytes": 0}
        (present if item["present"] else missing).append(item)

    forbidden_hits = []
    for pattern in ["data", ".git", "runtime", ".venv", "config.local.json"]:
        path = ROOT / pattern
        if path.exists():
            forbidden_hits.append(str(path.relative_to(ROOT)))

    from ourtime_config import face_model_available

    payload = {
        "layout": "green-software",
        "program_dir": ".",
        "user_data_dir": "data/",
        "allowlist": present,
        "missing_allowlist": missing,
        "forbidden_local_items_exist_but_must_not_be_packed": forbidden_hits,
        "models_status": "NOT_PACKED",
        "face_model_present_locally": face_model_available(),
        "note": "本脚本只检查清单，不打包、不复制真实库或模型。",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
