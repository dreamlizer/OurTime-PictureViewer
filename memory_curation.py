"""Score existing thumbnails and pick 7-10 photos for a memory reel."""
from __future__ import annotations

import statistics
from pathlib import Path

from library_db import now

HIGHLIGHT_MIN = 7
HIGHLIGHT_MAX = 10
MIN_LONG_SIDE = 1000
MIN_PIXELS = 600000
DHASH_SIZE = 8
NEAR_DUP_HAMMING = 14
BURST_SECONDS = 20
NEAR_DUP_MINUTES = 8
BURST_ID_GAP = 8
EVENT_GAP_SECONDS = 40 * 60
EVENT_PEOPLE_GAP_SECONDS = 12 * 3600
SHARPNESS_RELATIVE = 0.4
SHARPNESS_ABS_FLOOR = 0.12
QUALITY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS photo_quality (
    asset_id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL,
    sharpness REAL,
    dhash TEXT,
    status TEXT NOT NULL,
    computed_at TEXT NOT NULL
);
"""


def ensure_quality_schema(conn) -> None:
    conn.execute(QUALITY_TABLE_SQL)


def hamming_distance(left: str | None, right: str | None) -> int:
    if not left or not right:
        return DHASH_SIZE * DHASH_SIZE
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return DHASH_SIZE * DHASH_SIZE


def _resize_max(image, longest: int):
    width, height = image.size
    if max(width, height) <= longest:
        return image
    scale = longest / float(max(width, height))
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(size)


def _laplacian_variance(gray):
    import numpy as np
    padded = np.pad(gray.astype(np.float64), 1, mode="edge")
    lap = (
        padded[0:-2, 1:-1]
        + padded[2:, 1:-1]
        + padded[1:-1, 0:-2]
        + padded[1:-1, 2:]
        - 4 * padded[1:-1, 1:-1]
    )
    return float(lap.var())


def dhash_hex(image, hash_size: int = DHASH_SIZE) -> str:
    import numpy as np
    gray = image.convert("L").resize((hash_size + 1, hash_size))
    arr = np.asarray(gray, dtype=np.int16)
    bits = (arr[:, 1:] > arr[:, :-1]).reshape(-1)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    width = (hash_size * hash_size + 3) // 4
    return f"{value:0{width}x}"


def compute_thumb_metrics(path: Path) -> dict:
    from PIL import Image
    import numpy as np

    with Image.open(path) as original:
        image = original.convert("RGB")
        image = _resize_max(image, 512)
        gray = np.asarray(image.convert("L"), dtype=np.float64)
        if gray.size == 0:
            raise ValueError("empty thumbnail")
        lap = _laplacian_variance(gray)
        contrast = float(gray.var()) + 1e-6
        sharpness = lap / contrast
        return {
            "sharpness": float(sharpness),
            "dhash": dhash_hex(image),
            "status": "ok",
        }


def _parse_captured(row) -> float | None:
    text = str(row.get("captured_at") or "")
    if len(text) < 19:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(text[:19]).timestamp()
    except ValueError:
        return None


def thumb_path(thumb_dir: Path, sha256: str) -> Path:
    return Path(thumb_dir) / f"{sha256}.jpg"


def ensure_quality(conn, rows, thumb_dir: Path) -> None:
    ensure_quality_schema(conn)
    if not rows:
        return
    ids = [int(row["id"]) for row in rows]
    placeholders = ",".join("?" for _ in ids)
    existing = {
        int(item["asset_id"]): dict(item)
        for item in conn.execute(
            "SELECT asset_id, sha256, sharpness, dhash, status FROM photo_quality WHERE asset_id IN ("
            + placeholders + ")",
            ids,
        ).fetchall()
    }
    pending = []
    for row in rows:
        asset_id = int(row["id"])
        digest = str(row.get("sha256") or "")
        cached = existing.get(asset_id)
        if cached and cached.get("sha256") == digest and cached.get("status") in {"ok", "missing_thumb", "error"}:
            row["sharpness"] = cached.get("sharpness")
            row["dhash"] = cached.get("dhash")
            row["quality_status"] = cached.get("status")
            continue
        pending.append(row)
    stamp = now()
    for row in pending:
        asset_id = int(row["id"])
        digest = str(row.get("sha256") or "")
        path = thumb_path(thumb_dir, digest) if digest else None
        try:
            if not path or not path.is_file():
                metrics = {"sharpness": None, "dhash": None, "status": "missing_thumb"}
            else:
                metrics = compute_thumb_metrics(path)
        except Exception:
            metrics = {"sharpness": None, "dhash": None, "status": "error"}
        conn.execute(
            "INSERT OR REPLACE INTO photo_quality(asset_id, sha256, sharpness, dhash, status, computed_at) "
            "VALUES (?,?,?,?,?,?)",
            (asset_id, digest, metrics["sharpness"], metrics["dhash"], metrics["status"], stamp),
        )
        row["sharpness"] = metrics["sharpness"]
        row["dhash"] = metrics["dhash"]
        row["quality_status"] = metrics["status"]


def _is_blurry(row, median_sharp: float | None) -> bool:
    sharp = row.get("sharpness")
    if sharp is None or median_sharp is None:
        return False
    return sharp < SHARPNESS_ABS_FLOOR or sharp < (SHARPNESS_RELATIVE * median_sharp)


def _is_low_res(row) -> bool:
    width = int(row.get("width") or 0)
    height = int(row.get("height") or 0)
    if width <= 0 or height <= 0:
        return True
    return max(width, height) < MIN_LONG_SIDE or (width * height) < MIN_PIXELS


def _people_of(row) -> set[int]:
    people = row.get("person_ids") or []
    out = set()
    for item in people:
        try:
            pid = int(item)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            out.add(pid)
    return out


def cluster_day_events(rows) -> list[list]:
    dated = []
    undated = []
    for row in rows:
        stamp = _parse_captured(row)
        if stamp is None:
            undated.append(row)
        else:
            dated.append((stamp, row))
    dated.sort(key=lambda item: (item[0], int(item[1].get("id") or 0)))
    clusters = []
    for stamp, row in dated:
        people = _people_of(row)
        if not clusters:
            clusters.append({"rows": [row], "end": stamp, "people": set(people)})
            continue
        last = clusters[-1]
        gap = stamp - last["end"]
        shared = bool(people & last["people"])
        both_named = bool(people) and bool(last["people"])
        if both_named and not shared and gap > 20 * 60:
            clusters.append({"rows": [row], "end": stamp, "people": set(people)})
            continue
        if gap <= EVENT_GAP_SECONDS or (shared and gap <= EVENT_PEOPLE_GAP_SECONDS) or (not both_named and gap <= EVENT_GAP_SECONDS):
            last["rows"].append(row)
            last["end"] = stamp
            last["people"] |= people
        else:
            clusters.append({"rows": [row], "end": stamp, "people": set(people)})
    for row in undated:
        people = _people_of(row)
        match = next((item for item in clusters if people and (people & item["people"])), None)
        if match:
            match["rows"].append(row)
            match["people"] |= people
    if not clusters:
        return [list(rows)] if rows else []
    clusters.sort(key=lambda item: (len(item["rows"]), len(item["people"])), reverse=True)
    return [item["rows"] for item in clusters]


def pick_day_event(rows) -> list:
    clusters = cluster_day_events(rows)
    return clusters[0] if clusters else list(rows)


def _is_duplicate(row, kept) -> bool:
    rid = int(row["id"])
    stamp = _parse_captured(row)
    day = str(row.get("day_date") or "")
    for item in kept:
        if int(item["id"]) == rid:
            return True
        other = _parse_captured(item)
        if stamp is not None and other is not None and abs(stamp - other) <= BURST_SECONDS:
            return True
        ham = hamming_distance(row.get("dhash"), item.get("dhash"))
        same_day = bool(day) and day == str(item.get("day_date") or "")
        if ham <= NEAR_DUP_HAMMING and (same_day or (stamp is not None and other is not None and abs(stamp - other) <= 6 * 3600)):
            return True
        if same_day and stamp is not None and other is not None and abs(stamp - other) <= NEAR_DUP_MINUTES * 60:
            return True
        id_gap = abs(rid - int(item["id"]))
        if id_gap <= BURST_ID_GAP:
            if stamp is None or other is None:
                return True
            if abs(stamp - other) <= 15 * 60:
                return True
    return False


def _highlight_score(row, mode: str) -> tuple:
    sharp = float(row.get("sharpness") or 0)
    faces = int(row.get("face_count") or 0)
    favorite = int(row.get("favorite") or 0)
    bad = int(row.get("bad_cover") or 0)
    width = int(row.get("width") or 0)
    height = int(row.get("height") or 0)
    landscape = 1 if width >= height else 0
    pixels = min(width * height, 2_000_000)
    if mode == "person":
        ratio = float(row.get("face_ratio") or 0)
        visible = 1 if 0.025 <= ratio <= 0.62 else (0.35 if ratio > 0.62 else 0)
        solo = 1 if faces <= 2 else 0
        return (favorite, visible, sharp, solo, ratio, -bad, -int(row["id"]))
    has_face = 1 if faces > 0 else 0
    if mode == "trip":
        return (favorite, sharp, landscape, has_face, pixels, -bad, -int(row["id"]))
    return (favorite, has_face, sharp, landscape, pixels, -bad, -int(row["id"]))


def _spread(rows, *, key_fn, per_bucket: int, limit: int):
    buckets = {}
    order = []
    for row in rows:
        key = key_fn(row) or "_"
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        if len(buckets[key]) < per_bucket:
            buckets[key].append(row)
    if len(order) > limit:
        chosen = [order[0], order[-1]]
        used = set(chosen)
        for index in range(1, limit - 1):
            target = index * (len(order) - 1) / (limit - 1)
            remaining = [key for key in order if key not in used]
            pick = min(remaining, key=lambda key: (abs(order.index(key) - target), order.index(key)))
            chosen.append(pick)
            used.add(pick)
        order = sorted(chosen, key=order.index)
    picked = []
    while len(picked) < limit:
        progressed = False
        for key in order:
            if buckets[key]:
                picked.append(buckets[key].pop(0))
                progressed = True
                if len(picked) >= limit:
                    break
        if not progressed:
            break
    return picked


def curate_highlights(rows, *, mode: str = "trip", limit: int = 10) -> list[int]:
    if not rows:
        return []
    target = max(HIGHLIGHT_MIN, min(int(limit), HIGHLIGHT_MAX))
    sharps = [float(row["sharpness"]) for row in rows if row.get("sharpness") is not None]
    median_sharp = statistics.median(sharps) if sharps else None
    ranked = []
    for row in rows:
        if _is_low_res(row):
            continue
        if int(row.get("bad_cover") or 0) > 0 and not int(row.get("favorite") or 0):
            continue
        if _is_blurry(row, median_sharp) and not int(row.get("favorite") or 0):
            continue
        if mode == "person":
            ratio = float(row.get("face_ratio") or 0)
            if 0 < ratio < 0.025 and not int(row.get("favorite") or 0):
                continue
        ranked.append(row)
    ranked.sort(key=lambda row: _highlight_score(row, mode), reverse=True)
    pool = []
    for row in ranked:
        if _is_duplicate(row, pool):
            continue
        pool.append(row)
        if len(pool) >= 24:
            break
    if mode == "person":
        selected = _spread(pool, key_fn=lambda row: str(row.get("year_date") or row.get("day_date") or ""), per_bucket=1, limit=target)
    else:
        selected = _spread(pool, key_fn=lambda row: str(row.get("day_date") or ""), per_bucket=1, limit=target)
    if len(selected) < target:
        for row in pool:
            if _is_duplicate(row, selected):
                continue
            selected.append(row)
            if len(selected) >= target:
                break
    if len(selected) < HIGHLIGHT_MIN:
        seen = {int(row["id"]) for row in selected}
        fallback = ranked
        for row in fallback:
            if int(row["id"]) in seen or _is_duplicate(row, selected):
                continue
            selected.append(row)
            seen.add(int(row["id"]))
            if len(selected) >= HIGHLIGHT_MIN:
                break
    selected.sort(key=lambda row: (str(row.get("day_date") or ""), str(row.get("captured_at") or ""), int(row["id"])))
    out = []
    seen_ids = set()
    for row in selected[:target]:
        asset_id = int(row["id"])
        if asset_id in seen_ids:
            continue
        seen_ids.add(asset_id)
        out.append(asset_id)
    return out
