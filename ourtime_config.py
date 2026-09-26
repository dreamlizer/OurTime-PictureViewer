"""Resolve program vs user-data paths for a portable/green layout.

Precedence: environment variable > config.local.json > config.json >
project-relative defaults. Author-machine paths belong in the untracked
local file, not in published defaults.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "拾光相册"
APP_VERSION = "0.3"
BASE = Path(__file__).resolve().parent
FACE_LABEL_FILES = {"1.png", "4.png", "7.png", "8.png"}


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_settings() -> dict:
    merged = {}
    merged.update(_load_json(BASE / "config.json"))
    merged.update(_load_json(BASE / "config.local.json"))
    return merged


SETTINGS = load_settings()


def _configured(env_name: str, key: str, default=None):
    if env_name in os.environ:
        return os.environ.get(env_name)
    if key in SETTINGS and SETTINGS.get(key) not in (None, ""):
        return SETTINGS.get(key)
    return default


def _as_path(value, default: Path) -> Path:
    if value in (None, ""):
        return default.resolve()
    path = Path(str(value))
    if not path.is_absolute():
        path = BASE / path
    return path.resolve()


def _as_bool(value, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_drive(value) -> str | None:
    text = str(value or "").strip().upper().replace("/", "\\").rstrip("\\")
    if not text:
        return None
    if len(text) == 1 and text.isalpha():
        return f"{text}:"
    if len(text) == 2 and text[0].isalpha() and text[1] == ":":
        return text
    return None


DATA = _as_path(_configured("PHOTO_LIBRARY_DATA", "data_dir"), BASE / "data")
MODEL_ROOT = _as_path(
    _configured("PHOTO_MODEL_ROOT", "model_root"), BASE / "resources" / "models"
)
GEO_ROOT = _as_path(_configured("PHOTO_GEO_ROOT", "geo_root"), BASE / "resources" / "geo")
OBJECT_MODEL_DIR = _as_path(
    _configured("PHOTO_OBJECT_MODEL_ROOT", "object_model_dir"),
    BASE / "resources" / "objects",
)
CHROMIUM_PATH = _configured("PHOTO_CHROMIUM", "chromium_path")
OBJECTS_ENABLED = _as_bool(_configured("PHOTO_OBJECTS_ENABLED", "objects_enabled"), False)
FOLDER_ONLY_DRIVE = normalize_drive(
    _configured("PHOTO_FOLDER_ONLY_DRIVE", "folder_only_drive")
)
PUBLIC_FIGURES_SHELF = _as_bool(
    _configured("PHOTO_PUBLIC_FIGURES_SHELF", "public_figures_shelf"), False
)
PUBLIC_FIGURES_PACK = _as_path(
    _configured("PHOTO_PUBLIC_FIGURES_PACK", "public_figures_pack"),
    BASE / "resources" / "public-faces" / "public-faces.sqlite3",
)


def _face_label_candidates() -> list[Path]:
    configured = _configured("PHOTO_FACE_LABEL_DIR", "face_label_dir")
    candidates = []
    if configured:
        candidates.append(_as_path(configured, DATA / "人名标签"))
    candidates.append((DATA / "人名标签").resolve())
    candidates.append((BASE / "web" / "assets" / "face-labels").resolve())
    unique = []
    seen = set()
    for item in candidates:
        key = os.path.normcase(str(item))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def resolve_face_label_dir() -> Path:
    candidates = _face_label_candidates()
    for folder in candidates:
        if all((folder / name).is_file() for name in FACE_LABEL_FILES):
            return folder
    for folder in candidates:
        if any((folder / name).is_file() for name in FACE_LABEL_FILES):
            return folder
    return candidates[0]


FACE_LABEL_DIR = resolve_face_label_dir()


def face_model_available() -> bool:
    root = MODEL_ROOT / "models" / "buffalo_l"
    return (root / "det_10g.onnx").is_file() and (root / "w600k_r50.onnx").is_file()


def geo_data_available() -> bool:
    return (GEO_ROOT / "geonames" / "cities500.zip").is_file()


def face_labels_available() -> bool:
    return any((FACE_LABEL_DIR / name).is_file() for name in FACE_LABEL_FILES)


def export_renderer_available() -> bool:
    configured = CHROMIUM_PATH
    if configured and Path(str(configured)).is_file():
        return True
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def runtime_identity() -> str:
    import subprocess

    git_dir = BASE / ".git"
    if not git_dir.exists():
        return APP_VERSION
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=BASE,
            capture_output=True,
            text=True,
            encoding="ascii",
            errors="replace",
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        sha = (result.stdout or "").strip()
        return sha or APP_VERSION
    except (OSError, subprocess.SubprocessError):
        return APP_VERSION
