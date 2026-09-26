"""Refresh the public-figure shelf from the reference folder.

Each scan replaces the previous result. Files still in the folder are shown.
Files that were removed are not shown, including as missing photos.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
    ".tif", ".tiff", ".heic", ".heif", ".avif",
}


def references_root(pack_path: Path | None) -> Path | None:
    if pack_path is None:
        return None
    return Path(pack_path).resolve().parent / "references"


def directory_fingerprint(root: Path | None) -> tuple[int, int, int]:
    if root is None or not root.is_dir():
        return (0, 0, 0)
    count = 0
    total = 0
    newest = 0
    for path in iter_images(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        count += 1
        total += int(stat.st_size)
        newest = max(newest, int(stat.st_mtime_ns))
    return (count, total, newest)


def iter_images(root: Path):
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        yield resolved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as image:
            return int(image.size[0] or 3), int(image.size[1] or 4)
    except Exception:
        return (3, 4)


def scan_folder(root: Path | None, cache: dict[str, tuple]) -> list[dict]:
    if root is None or not root.is_dir():
        return []
    root = root.resolve()
    grouped: dict[str, dict] = {}
    for path in iter_images(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        marker = (int(stat.st_size), int(stat.st_mtime_ns))
        cached = cache.get(relative)
        if cached and cached[:2] == marker:
            sha, width, height = cached[2], cached[3], cached[4]
        else:
            try:
                sha = file_sha256(path)
            except OSError:
                continue
            width, height = image_size(path)
            cache[relative] = (marker[0], marker[1], sha, width, height)
        item = grouped.get(sha)
        if item is None or relative < item["relative_path"]:
            grouped[sha] = {
                "sha256": sha,
                "relative_path": relative,
                "filename": path.name,
                "width": width,
                "height": height,
                "asset_id": None,
            }
    return [grouped[key] for key in sorted(grouped)]


def match_asset_ids(db_path: Path | None, hashes: set[str]) -> dict[str, int]:
    if not hashes or db_path is None or not Path(db_path).is_file():
        return {}
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        found: dict[str, int] = {}
        ordered = sorted(hashes)
        for start in range(0, len(ordered), 400):
            chunk = ordered[start:start + 400]
            marks = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT lower(a.sha256), a.id
                FROM assets a
                WHERE lower(a.sha256) IN ({marks})
                  AND coalesce(a.excluded,0)=0
                  AND EXISTS(
                    SELECT 1 FROM files f
                    WHERE f.asset_id=a.id AND coalesce(f.excluded,0)=0 AND coalesce(f.exists_now,1)=1
                  )
                ORDER BY a.id
                """,
                chunk,
            ).fetchall()
            for sha, asset_id in rows:
                found.setdefault(sha, int(asset_id))
        return found
    finally:
        connection.close()



PERSONAL_PREFIXES = ("incoming-", "najia-", "marco-polo", "040b")


def is_personal_filename(filename: str) -> bool:
    return Path(filename).name.lower().startswith(PERSONAL_PREFIXES)


def library_reference_assets(db_path, seed_ids):
    if not seed_ids or db_path is None or not Path(db_path).is_file():
        return []
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        seed = sorted(int(item) for item in seed_ids)
        marks = ",".join("?" for _ in seed)
        parents = set()
        rows = connection.execute(
            "SELECT path FROM files WHERE asset_id IN (" + marks + ") AND coalesce(exists_now,1)=1",
            seed,
        ).fetchall()
        for row in rows:
            parent = str(Path(row[0]).parent)
            if parent and not parent.endswith(":"):
                parents.add(parent)
        found = {}
        slash = chr(92)
        for parent in parents:
            prefix = parent.rstrip("/" + slash) + slash
            listed = connection.execute(
                "SELECT a.id, lower(a.sha256), f.path, a.width, a.height "
                "FROM files f JOIN assets a ON a.id=f.asset_id "
                "WHERE coalesce(f.exists_now,1)=1 AND coalesce(f.excluded,0)=0 AND coalesce(a.excluded,0)=0 "
                "AND substr(f.path, 1, length(?)) = ?",
                (prefix, prefix),
            ).fetchall()
            for asset_id, sha, file_path, width, height in listed:
                filename = Path(file_path).name
                if not sha or is_personal_filename(filename):
                    continue
                current = found.get(sha)
                if current is None or int(asset_id) < current["asset_id"]:
                    found[sha] = {
                        "sha256": sha,
                        "relative_path": "",
                        "filename": filename,
                        "width": int(width or 3),
                        "height": int(height or 4),
                        "asset_id": int(asset_id),
                    }
        return list(found.values())
    finally:
        connection.close()


class PublicFigureShelf:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.db_path: Path | None = None
        self.pack_path: Path | None = None
        self.enabled = False
        self.ready = False
        self.scanning = False
        self.error = ""
        self.revision = 0
        self.fingerprint: tuple[int, int, int] = (0, 0, 0)
        self.checked_at = 0.0
        self.cache: dict[str, tuple] = {}
        self.records: list[dict] = []

    def configure(self, db_path: Path, pack_path: Path, enabled: bool) -> None:
        with self._lock:
            self.db_path = Path(db_path)
            self.pack_path = Path(pack_path)
            self.enabled = bool(enabled)

    def start(self) -> None:
        self._ensure_scan(force=True)

    def _ensure_scan(self, force: bool = False) -> None:
        with self._lock:
            if not self.enabled:
                self.ready = True
                self.scanning = False
                self.records = []
                return
            if self._thread and self._thread.is_alive():
                return
            now = time.monotonic()
            if self.ready and not force and now - self.checked_at < 5:
                return
            self.checked_at = now
            root = references_root(self.pack_path)
            fingerprint = directory_fingerprint(root)
            if self.ready and not force and fingerprint == self.fingerprint:
                return
            self.scanning = True
            self.error = ""
            if force:
                self.ready = False
            self._thread = threading.Thread(
                target=self.refresh, name="public-figure-shelf", daemon=True
            )
            self._thread.start()

    def refresh(self) -> None:
        try:
            with self._lock:
                db_path = self.db_path
                root = references_root(self.pack_path)
                cache = dict(self.cache)
            records = scan_folder(root, cache)
            matched = match_asset_ids(db_path, {item["sha256"] for item in records})
            for item in records:
                item["asset_id"] = matched.get(item["sha256"])
            known = {item["sha256"] for item in records}
            for extra in library_reference_assets(db_path, set(matched.values())):
                if extra["sha256"] in known:
                    continue
                records.append(extra)
                known.add(extra["sha256"])
            fingerprint = directory_fingerprint(root)
            error = ""
        except Exception:
            records = []
            cache = {}
            fingerprint = (0, 0, 0)
            error = "公众人物文件夹暂时没有读完"
        with self._lock:
            self.cache = cache
            self.records = records
            self.fingerprint = fingerprint
            self.ready = True
            self.scanning = False
            self.error = error
            self.revision += 1

    def status(self) -> dict:
        self._ensure_scan(force=False)
        with self._lock:
            visible = [item for item in self.records if self._file_exists(item)]
            return {
                "enabled": self.enabled,
                "ready": self.ready,
                "scanning": self.scanning,
                "count": len(visible) if self.ready else 0,
                "revision": self.revision,
                "error": self.error,
            }

    def visible_records(self) -> list[dict]:
        self._ensure_scan(force=False)
        with self._lock:
            if not self.enabled or not self.ready:
                return []
            return [dict(item) for item in self.records if self._file_exists(item)]

    def record_slug(self, item: dict) -> str:
        rel = str(item.get("relative_path") or "").replace(chr(92), "/").strip("/")
        if rel.startswith("references/"):
            rel = rel[len("references/"):]
        if "/" in rel:
            return rel.split("/", 1)[0]
        return ""

    def _text_hit(self, needle: str, *parts: object) -> bool:
        folded = str(needle or "").strip().casefold()
        if not folded:
            return True
        for part in parts:
            text = str(part or "")
            if text and folded in text.casefold():
                return True
        return False

    def catalog(self) -> list[dict]:
        path = self.pack_path
        if path is None or not Path(path).is_file():
            return []
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, slug, name, alias FROM people WHERE coalesce(status,'active')!='removed'"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def search_people(self, q: str = "", limit: int = 40) -> list[dict]:
        counts: dict[str, int] = {}
        for item in self.visible_records():
            slug = self.record_slug(item)
            if slug:
                counts[slug] = counts.get(slug, 0) + 1
        found = []
        for row in self.catalog():
            slug = str(row.get("slug") or "")
            count = counts.get(slug, 0)
            if count <= 0:
                continue
            if not self._text_hit(q, slug, slug.replace("-", " "), row.get("name"), row.get("alias")):
                continue
            found.append({
                "id": "pf:" + str(row["id"]),
                "label": row.get("name") or row.get("alias") or slug,
                "photo_count": count,
            })
        found.sort(key=lambda item: (-int(item["photo_count"]), str(item["label"])))
        size = max(1, min(int(limit or 40), 80))
        return found[:size]

    def narrow(self, records: list[dict], person: str = "", q: str = "", directory: str = "") -> list[dict]:
        people = self.catalog()
        by_id = {str(row["id"]): row for row in people}
        by_slug = {str(row.get("slug") or ""): row for row in people if row.get("slug")}
        wanted_slugs = set()
        for token in str(person or "").split(","):
            token = token.strip()
            if token.startswith("pf:"):
                token = token[3:]
            row = by_id.get(token) or by_slug.get(token)
            if row and row.get("slug"):
                wanted_slugs.add(str(row["slug"]))
        directory_text = str(directory or "").replace(chr(92), "/").strip().strip("/")
        directory_name = directory_text.rsplit("/", 1)[-1].casefold() if directory_text else ""
        kept = []
        for item in records:
            slug = self.record_slug(item)
            row = by_slug.get(slug)
            if wanted_slugs and slug not in wanted_slugs:
                continue
            if not self._text_hit(q, slug, slug.replace("-", " "), item.get("filename"), row and row.get("name"), row and row.get("alias")):
                continue
            if directory_name and directory_name not in slug.casefold() and directory_name not in str(item.get("filename") or "").casefold():
                item = dict(item)
                item["_needs_directory"] = directory_text
            kept.append(item)
        return kept

    def asset_ids(self) -> tuple[int, ...]:
        return tuple(sorted({
            int(item["asset_id"]) for item in self.visible_records() if item.get("asset_id")
        }))

    def resolve(self, relative_path: str) -> Path | None:
        root = references_root(self.pack_path)
        if root is None or not root.is_dir():
            return None
        raw = str(relative_path or "").replace(chr(92), "/").lstrip("/")
        if not raw or raw.startswith("../") or "/../" in f"/{raw}":
            return None
        path = (root / raw).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            return None
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            return None
        return path

    def _file_exists(self, item: dict) -> bool:
        if item.get("relative_path"):
            return self.resolve(item.get("relative_path") or "") is not None
        return bool(item.get("asset_id"))

    def photo_scope(self, photo_filter: str, narrowed: bool) -> tuple[str, tuple[int, ...]]:
        if photo_filter == "public-figures":
            return "folder", ()
        ids = self.asset_ids() if self.enabled and self.ready else ()
        if not self.enabled or not self.ready or narrowed or not ids:
            return "", ()
        if photo_filter == "timeline" or str(photo_filter).startswith("year:"):
            return "exclude", ids
        return "", ()


shelf = PublicFigureShelf()
