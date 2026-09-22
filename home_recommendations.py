"""Homepage discovery recommendations. Isolated from scan and EXIF writers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from library_db import ACTIVE_ASSET, ASSET_LIST_COLUMNS, EFFECTIVE_PLACE, now
from memory_curation import (
    HIGHLIGHT_MAX,
    HIGHLIGHT_MIN,
    curate_highlights,
    ensure_quality,
    ensure_quality_schema,
    pick_day_event,
)
from ourtime_config import DATA

SCHEMA_VERSION = 2
ALGORITHM_VERSION = "home-discovery-v10"
CATALOG_BUILD_LOCK = threading.Lock()
SNAPSHOT_LIMIT = 80
PAGE_SIZE = 6
MAX_PLACE_DAYS = 7
MIN_PLACE_PHOTOS = 3
MIN_PERSON_PHOTOS = 2
PROTAGONIST_MIN_RATIO = 0.018
PROTAGONIST_SOFT_RATIO = 0.012
PERSON_YEAR_PER_YEAR = 2
PERSON_HIGHLIGHT_MAX = 18
PERSON_POOL_MAX = 80
INLINE_CATALOG_ASSETS = 200
PRECISION_DAY = "日"
PRECISION_MONTH = "月"
PRECISION_YEAR = "年"
PRECISION_SECOND = "秒"
DIGITAL_SOURCE = "元数据数字化时间"
CATEGORY_SCREENSHOT = "截图"
CATEGORY_SMALL = "小图 / 素材"
WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

QUALIFIED_SQL = (
    ACTIVE_ASSET
    + " AND a.category NOT IN (?, ?)"
    + " AND EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.excluded=0 AND f.exists_now=1)"
)
QUALIFIED_VALUES = (CATEGORY_SCREENSHOT, CATEGORY_SMALL)
PLACE_MIN_AGE_DAYS = 14
FACE_COUNT_SQL = (
    "(SELECT count(*) FROM faces x JOIN people p ON p.id=x.person_id "
    "WHERE x.asset_id=a.id AND coalesce(x.ignored,0)=0 AND coalesce(p.ignored,0)=0)"
)
BAD_COVER_SQL = (
    "(SELECT count(*) FROM object_tags o WHERE o.asset_id=a.id "
    "AND o.label IN ('文件扫描件','截图','手机'))"
)
COVER_INNER_SQL = (
    ", " + FACE_COUNT_SQL + " face_count, " + BAD_COVER_SQL + " bad_cover, "
)

DAY_DATE_SQL = (
    "CASE "
    "WHEN trim(coalesce(a.manual_date,'')) <> '' THEN "
    "CASE WHEN coalesce(a.manual_precision,'') = ? AND substr(trim(a.manual_date),1,10) "
    "GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN substr(trim(a.manual_date),1,10) ELSE NULL END "
    "WHEN trim(coalesce(a.manual_date,'')) = '' AND (a.date_source LIKE 'EXIF%' OR a.date_source = ?) "
    "AND coalesce(a.date_precision,'') IN (?, ?) "
    "AND substr(coalesce(a.captured_at,''),1,10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
    "THEN substr(a.captured_at,1,10) ELSE NULL END"
)
MONTH_DATE_SQL = (
    "CASE "
    "WHEN trim(coalesce(a.manual_date,'')) <> '' THEN CASE "
    "WHEN coalesce(a.manual_precision,'') = ? AND substr(trim(a.manual_date),1,10) "
    "GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN substr(trim(a.manual_date),1,7) "
    "WHEN coalesce(a.manual_precision,'') = ? AND substr(trim(a.manual_date),1,7) "
    "GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]' THEN substr(trim(a.manual_date),1,7) "
    "ELSE NULL END "
    "WHEN trim(coalesce(a.manual_date,'')) = '' AND (a.date_source LIKE 'EXIF%' OR a.date_source = ?) THEN CASE "
    "WHEN coalesce(a.date_precision,'') IN (?, ?) AND substr(coalesce(a.captured_at,''),1,10) "
    "GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN substr(a.captured_at,1,7) "
    "WHEN coalesce(a.date_precision,'') = ? AND substr(coalesce(a.captured_at,''),1,7) "
    "GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]' THEN substr(a.captured_at,1,7) "
    "ELSE NULL END ELSE NULL END"
)
YEAR_DATE_SQL = (
    "CASE "
    "WHEN trim(coalesce(a.manual_date,'')) <> '' THEN CASE "
    "WHEN coalesce(a.manual_precision,'') IN (?, ?, ?) AND substr(trim(a.manual_date),1,4) "
    "GLOB '[0-9][0-9][0-9][0-9]' THEN substr(trim(a.manual_date),1,4) ELSE NULL END "
    "WHEN trim(coalesce(a.manual_date,'')) = '' AND (a.date_source LIKE 'EXIF%' OR a.date_source = ?) "
    "AND coalesce(a.date_precision,'') IN (?, ?, ?, ?) "
    "AND substr(coalesce(a.captured_at,''),1,4) GLOB '[0-9][0-9][0-9][0-9]' "
    "THEN substr(a.captured_at,1,4) ELSE NULL END"
)


class RecommendationError(Exception):
    def __init__(self, status_code: int, detail: str, error_code: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code


def as_of_date(raw: str | None = None) -> date:
    text = str(raw or os.environ.get("PHOTO_HOME_AS_OF") or "").strip()
    if text:
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise RecommendationError(400, "推荐日期无效", "invalid_request") from exc
    return date.today()


def data_revision(conn) -> str:
    face = conn.execute("SELECT coalesce(revision,0) FROM face_index_state WHERE id=1").fetchone()
    edits = conn.execute("SELECT coalesce(max(id),0) FROM edits").fetchone()[0]
    assets = conn.execute(
        "SELECT coalesce(max(id),0), coalesce(sum(excluded),0), coalesce(sum(coalesce(favorite,0)),0) FROM assets"
    ).fetchone()
    files = conn.execute(
        "SELECT coalesce(max(id),0), coalesce(sum(CASE WHEN exists_now=0 OR excluded=1 THEN 1 ELSE 0 END),0) FROM files"
    ).fetchone()
    ops = conn.execute("SELECT coalesce(max(rowid),0) FROM operations").fetchone()[0]
    return "|".join(str(part) for part in (ALGORITHM_VERSION, face[0] if face else 0, edits, *assets, *files, ops))


def ensure_home_schema(conn) -> None:
    ensure_quality_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS home_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            revision TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT NOT NULL,
            photo_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            member_ids TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS home_snapshots_created ON home_snapshots(created_at);
        CREATE TABLE IF NOT EXISTS home_recommendation_cache (
            cache_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS home_catalog (
            kind TEXT PRIMARY KEY,
            algorithm_version TEXT NOT NULL,
            built_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS home_catalog_state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            built_at TEXT,
            algorithm_version TEXT,
            status TEXT,
            message TEXT
        );
        CREATE TABLE IF NOT EXISTS home_stories (
            story_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            source_group_id TEXT NOT NULL,
            season TEXT,
            date_from TEXT,
            date_to TEXT,
            title TEXT,
            subtitle TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS home_stories_source ON home_stories(source_group_id);
        CREATE INDEX IF NOT EXISTS home_stories_season ON home_stories(season, kind);
        """
    )


def prune_snapshots(conn) -> None:
    rows = conn.execute("SELECT snapshot_id FROM home_snapshots ORDER BY created_at DESC, snapshot_id DESC").fetchall()
    for snapshot_id in [row[0] for row in rows[SNAPSHOT_LIMIT:]]:
        conn.execute("DELETE FROM home_snapshots WHERE snapshot_id=?", (snapshot_id,))


def _stable(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_cn_date(value: date, *, month: bool = False) -> str:
    if month:
        return f"{value.year} 年 {value.month} 月"
    return f"{value.year} 年 {value.month} 月 {value.day} 日"


def format_weekday(value: date) -> str:
    return WEEKDAYS[value.weekday()]


def pretty_place_text(value: str) -> str:
    text = re.sub(r"^\s*(?:中国大陆|中国)\s*(?:·\s*)?", "", str(value or ""))
    text = re.sub(r"\s*·\s*", " · ", text)
    return re.sub(r"\s+", " ", text).strip()


def _day_params():
    return (PRECISION_DAY, DIGITAL_SOURCE, PRECISION_SECOND, PRECISION_DAY)


def _month_params():
    return (PRECISION_DAY, PRECISION_MONTH, DIGITAL_SOURCE, PRECISION_SECOND, PRECISION_DAY, PRECISION_MONTH)


def _year_params():
    return (PRECISION_DAY, PRECISION_MONTH, PRECISION_YEAR, DIGITAL_SOURCE, PRECISION_SECOND, PRECISION_DAY, PRECISION_MONTH, PRECISION_YEAR)


def pick_covers(rows, limit: int):
    ranked = sorted(
        rows,
        key=lambda row: (
            -int(row.get("bad_cover") or 0),
            1 if int(row.get("face_count") or 0) > 0 else 0,
            int(row.get("favorite") or 0),
            1 if int(row.get("width") or 0) >= int(row.get("height") or 0) else 0,
            min(int(row.get("width") or 0) * int(row.get("height") or 0), 2_000_000),
            -int(row["id"]),
        ),
        reverse=True,
    )
    return [int(row["id"]) for row in ranked[:limit]]


def pick_place_covers(rows, limit: int = 1):
    ranked = sorted(
        rows,
        key=lambda row: (
            -int(row.get("bad_cover") or 0),
            int(row.get("favorite") or 0),
            1 if int(row.get("width") or 0) >= int(row.get("height") or 0) else 0,
            min(int(row.get("width") or 0) * int(row.get("height") or 0), 2_000_000),
            -int(row["id"]),
        ),
        reverse=True,
    )
    return [int(row["id"]) for row in ranked[:limit]]


def pretty_place_short(value: str) -> str:
    full = pretty_place_text(value)
    parts = [part.strip() for part in re.split(r"\s*·\s*", full) if part.strip()]
    def strip_admin(part: str) -> str:
        for suffix in ("特别行政区", "自治区", "省", "市"):
            if part.endswith(suffix) and len(part) > len(suffix):
                return part[: -len(suffix)]
        return part
    if len(parts) >= 3:
        return f"{strip_admin(parts[-2])} · {parts[-1]}"
    if len(parts) == 2:
        return f"{strip_admin(parts[0])} · {parts[1]}"
    return full


def format_cn_date_compact(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def _parse_bbox(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        x1, y1, x2, y2 = [float(raw[i]) for i in range(4)]
        width = float(raw[4]) if len(raw) > 4 else 0.0
        height = float(raw[5]) if len(raw) > 5 else 0.0
    except (TypeError, ValueError):
        return None
    return x1, y1, x2, y2, width, height


def _face_metrics(row):
    box = _parse_bbox(row.get("bbox"))
    img_w = float(row.get("width") or 0) or (box[4] if box else 0)
    img_h = float(row.get("height") or 0) or (box[5] if box else 0)
    if not box or img_w <= 0 or img_h <= 0:
        return {"ratio": 0.0, "cx": 0.5, "cy": 0.28, "ok": False}
    x1, y1, x2, y2 = box[:4]
    face_w = max(1.0, x2 - x1)
    face_h = max(1.0, y2 - y1)
    ratio = (face_w * face_h) / (img_w * img_h)
    return {
        "ratio": ratio,
        "cx": ((x1 + x2) / 2.0) / img_w,
        "cy": ((y1 + y2) / 2.0) / img_h,
        "ok": ratio >= 0.012,
    }


def _object_position(metrics) -> str:
    return f"{round(metrics['cx'] * 100, 1)}% {round(metrics['cy'] * 100, 1)}%"


def _cover_from_row(row, *, year=None):
    metrics = _face_metrics(row)
    year_value = year if year is not None else row.get("year_date")
    preview = "thumb"
    if row.get("face_id") and (not metrics["ok"] or metrics["ratio"] < 0.018):
        preview = "face"
    return {
        "asset_id": int(row["id"]),
        "year": str(year_value) if year_value else None,
        "target_face_id": int(row["face_id"]) if row.get("face_id") else None,
        "object_position": _object_position(metrics) if metrics["ok"] else "50% 28%",
        "preview": preview,
    }


def _person_photo_score(row):
    metrics = _face_metrics(row)
    face_count = int(row.get("face_count") or 0)
    crowd = int(row.get("scene_face_count") or face_count or 0)
    rank = int(row.get("face_rank") or 1)
    share = float(row.get("face_share") or 1.0)
    visible = 1 if PROTAGONIST_MIN_RATIO <= metrics["ratio"] <= 0.62 else (0.45 if metrics["ratio"] > 0.62 else 0)
    solo = 1 if crowd <= 2 else (0.55 if crowd <= 4 else 0)
    lead = 1 if rank == 1 else (0.6 if share >= 0.75 else 0)
    return (
        1 if _is_protagonist(row) else 0,
        1 if metrics["ok"] else 0,
        visible,
        lead,
        solo,
        metrics["ratio"],
        int(row.get("favorite") or 0),
        -crowd,
        -int(row["id"]),
    )


def _choose_span_years(years):
    years = sorted({str(year) for year in years if year})
    if not years:
        return []
    if len(years) <= 4:
        return years
    def year_int(value):
        return int(str(value)[:4])
    first, last = years[0], years[-1]
    chosen = [first]
    if last != first:
        chosen.append(last)
    span = year_int(last) - year_int(first)
    for frac in (1 / 3, 2 / 3):
        target = year_int(first) + span * frac
        remaining = [year for year in years if year not in chosen]
        if not remaining:
            break
        pick = min(remaining, key=lambda year: (abs(year_int(year) - target), year_int(year)))
        chosen.append(pick)
    remaining = [year for year in years if year not in chosen]
    while len(chosen) < 4 and remaining:
        def dist(year):
            return min(abs(year_int(year) - year_int(item)) for item in chosen)
        remaining.sort(key=lambda year: (-dist(year), year_int(year)))
        chosen.append(remaining.pop(0))
    return sorted(chosen, key=lambda year: int(str(year)[:4]))


def _best_person_rows(items):
    best = {}
    for row in items:
        asset_id = int(row["id"])
        previous = best.get(asset_id)
        if previous is None or _face_metrics(row)["ratio"] > _face_metrics(previous)["ratio"]:
            best[asset_id] = row
    return list(best.values())


def _chunks(items, size=400):
    sequence = list(items)
    for index in range(0, len(sequence), size):
        yield sequence[index:index + size]


def _row_day(row):
    day = str(row.get("day_date") or "").strip()
    if len(day) >= 10:
        return day[:10]
    captured = str(row.get("captured_at") or "")
    return captured[:10] if len(captured) >= 10 else ""


def _annotate_scene_roles(conn, rows):
    if not rows:
        return rows
    sizes = {}
    for row in rows:
        asset_id = int(row["id"])
        sizes[asset_id] = (row.get("width"), row.get("height"), row.get("bbox"))
    ratios_by_asset = {asset_id: [] for asset_id in sizes}
    ids = list(sizes)
    for chunk in _chunks(ids):
        placeholders = ",".join("?" for _ in chunk)
        sql = (
            "SELECT asset_id, bbox FROM faces "
            "WHERE coalesce(ignored,0)=0 AND asset_id IN (" + placeholders + ")"
        )
        for item in conn.execute(sql, chunk).fetchall():
            asset_id = int(item["asset_id"])
            width, height, _bbox = sizes.get(asset_id, (0, 0, None))
            metrics = _face_metrics({
                "bbox": item["bbox"],
                "width": width,
                "height": height,
            })
            ratios_by_asset.setdefault(asset_id, []).append(metrics["ratio"])
    for row in rows:
        mine = _face_metrics(row)["ratio"]
        ratios = sorted(ratios_by_asset.get(int(row["id"])) or [mine], reverse=True)
        largest = float(ratios[0] or 0.0)
        rank = 1 + sum(1 for value in ratios if value > mine + 1e-9)
        crowd = max(int(row.get("face_count") or 0), len(ratios))
        row["scene_face_count"] = crowd
        row["face_rank"] = rank
        row["largest_ratio"] = largest
        row["face_share"] = (mine / largest) if largest > 0 else 0.0
    return rows


def _is_protagonist(row):
    metrics = _face_metrics(row)
    ratio = metrics["ratio"]
    if not metrics["ok"] or ratio < PROTAGONIST_MIN_RATIO:
        return False
    if metrics["cx"] < 0.08 or metrics["cx"] > 0.92 or metrics["cy"] < 0.05 or metrics["cy"] > 0.90:
        return False
    crowd = int(row.get("scene_face_count") or row.get("face_count") or 0)
    rank = int(row.get("face_rank") or 1)
    share = float(row.get("face_share") or 1.0)
    if crowd <= 3 and rank == 1:
        return True
    if rank == 1 and share >= 0.85:
        return True
    if ratio < 0.025:
        return False
    if crowd >= 6 and (rank > 2 or share < 0.75):
        return False
    if crowd >= 4 and rank > 1 and share < 0.62:
        return False
    if rank > 1 and share < 0.70:
        return False
    return True


def _person_pool_rows(members):
    leads = [row for row in members if _is_protagonist(row)]
    if len(leads) >= MIN_PERSON_PHOTOS:
        return leads
    visible = [row for row in members if _face_metrics(row)["ok"] or _is_protagonist(row)]
    if len(visible) >= MIN_PERSON_PHOTOS:
        return visible
    return list(members)


def _year_number(value):
    text = str(value or "").strip()
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else 0


def _spread_year_order(years, limit):
    ordered = sorted({str(year) for year in years if year}, key=_year_number)
    if len(ordered) <= limit:
        return ordered
    chosen = [ordered[0], ordered[-1]]
    span = _year_number(ordered[-1]) - _year_number(ordered[0])
    targets = [(_year_number(ordered[0]) + span * index / (limit - 1), index) for index in range(1, limit - 1)]
    used = set(chosen)
    for target, index in targets:
        remaining = [year for year in ordered if year not in used]
        pick = min(remaining, key=lambda year: (abs(_year_number(year) - target), abs(ordered.index(year) - index), _year_number(year)))
        chosen.append(pick)
        used.add(pick)
    return sorted(chosen, key=_year_number)


def _pick_spread_rows(rows, *, per_year, limit):
    by_year = {}
    undated = []
    for row in sorted(rows, key=_person_photo_score, reverse=True):
        year = str(row.get("year_date") or "").strip()
        if year:
            by_year.setdefault(year, []).append(row)
        else:
            undated.append(row)
    years = _spread_year_order(by_year, limit)
    picked = []
    used_ids = set()
    used_days = set()

    def take(source, cap_year=None):
        if len(picked) >= limit:
            return
        year_taken = 0
        for row in source:
            if len(picked) >= limit:
                return
            if cap_year is not None and year_taken >= cap_year:
                return
            asset_id = int(row["id"])
            if asset_id in used_ids:
                continue
            day = _row_day(row)
            if day and day in used_days:
                continue
            picked.append(row)
            used_ids.add(asset_id)
            if day:
                used_days.add(day)
            year_taken += 1

    for year in years:
        take(by_year[year], 1)
    if len(picked) < limit:
        for year in years:
            already = sum(1 for row in picked if str(row.get("year_date") or "") == year)
            take(by_year[year], max(0, per_year - already))
    if len(picked) < limit:
        take(undated)
    picked.sort(key=lambda row: (str(row.get("year_date") or "9999"), _row_day(row), int(row["id"])))
    return picked[:limit]


def _person_rows(conn, person_id=None):
    sql = (
        "SELECT p.id person_id, p.name person_name, a.id, a.width, a.height, coalesce(a.favorite,0) favorite, "
        "a.captured_at captured_at"
        + COVER_INNER_SQL + YEAR_DATE_SQL + " year_date, " + DAY_DATE_SQL + " day_date, "
        "x.id face_id, x.bbox bbox FROM people p "
        "JOIN faces x ON x.person_id=p.id AND coalesce(x.ignored,0)=0 "
        "JOIN assets a ON a.id=x.asset_id "
        "WHERE coalesce(p.ignored,0)=0 AND p.confirmed=1 AND trim(coalesce(p.name,''))<>'' "
        "AND " + QUALIFIED_SQL
    )
    params = [*_year_params(), *_day_params(), *QUALIFIED_VALUES]
    if person_id:
        sql += " AND p.id=?"
        params.append(int(person_id))
    rows = [dict(item) for item in conn.execute(sql, params).fetchall()]
    return _annotate_scene_roles(conn, rows)


def _person_card_from_items(person_id, items):
    members = _best_person_rows(items)
    if len(members) < MIN_PERSON_PHOTOS:
        return None
    years = sorted({row["year_date"] for row in members if row.get("year_date")})
    name = members[0]["person_name"]
    if len(years) >= 2:
        kind, title, subtitle, cta = "person_years", name, f"{years[0]} — {years[-1]}", "看看这些年"
    else:
        kind, title, subtitle, cta = "person_fragment", f"与{name}的片段", f"{years[0]}" if years else "", "看看这些年"
    pool = _person_pool_rows(members)
    highlights = _pick_spread_rows(pool, per_year=PERSON_YEAR_PER_YEAR, limit=PERSON_HIGHLIGHT_MAX)
    if len(highlights) < MIN_PERSON_PHOTOS:
        highlights = _pick_spread_rows(members, per_year=PERSON_YEAR_PER_YEAR, limit=PERSON_HIGHLIGHT_MAX)
    if len(highlights) < MIN_PERSON_PHOTOS:
        return None
    pool_ids = [int(row["id"]) for row in _pick_spread_rows(pool, per_year=4, limit=PERSON_POOL_MAX)]
    highlight_ids = [int(row["id"]) for row in highlights]
    by_year = {}
    for row in pool:
        year = row.get("year_date")
        if year:
            by_year.setdefault(str(year), []).append(row)
    chosen_years = _choose_span_years(list(by_year) or [row.get("year_date") for row in highlights if row.get("year_date")])
    covers = []
    used_ids = set()
    alts = []
    for year in chosen_years:
        ranked_rows = sorted(by_year.get(year, []), key=_person_photo_score, reverse=True)
        picked = None
        for row in ranked_rows:
            if int(row["id"]) in used_ids:
                continue
            if picked is None:
                picked = row
            else:
                alts.append(_cover_from_row(row, year=year))
        if picked is None:
            continue
        used_ids.add(int(picked["id"]))
        covers.append(_cover_from_row(picked, year=year))
    if not covers:
        for row in highlights[:4]:
            covers.append(_cover_from_row(row, year=row.get("year_date")))
    return _card(
        kind, f"person:{person_id}", title, subtitle, len(highlight_ids), [item["asset_id"] for item in covers[:4]],
        f"{years[0]}-01-01" if years else None, f"{years[-1]}-12-31" if years else None,
        {
            "person_id": person_id,
            "person_name": name,
            "years": years,
            "cta": cta,
            "covers": covers[:4],
            "_alts": alts,
            "highlight_ids": highlight_ids,
            "source_count": len(members),
            "_pool_ids": pool_ids or highlight_ids,
        },
    )


def _covers_payload(cover_ids, *, year=None, face_id=None):
    return [
        {
            "asset_id": int(asset_id),
            "year": str(year) if year else None,
            "target_face_id": int(face_id) if face_id else None,
            "object_position": "50% 28%",
            "preview": "thumb",
        }
        for asset_id in cover_ids
    ]


def _card(kind, group_id, title, subtitle, photo_count, cover_ids, date_from, date_to, extra=None):
    extra = extra or {}
    covers = extra.get("covers")
    if not covers:
        covers = _covers_payload(cover_ids)
    cover_ids = [int(item["asset_id"]) for item in covers]
    return {
        "group_id": group_id,
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "photo_count": photo_count,
        "cover_asset_ids": cover_ids,
        "covers": covers,
        "date_from": date_from,
        "date_to": date_to,
        "place": extra.get("place"),
        "place_label": extra.get("place_label"),
        "title_full": extra.get("title_full"),
        "person_id": extra.get("person_id"),
        "person_name": extra.get("person_name"),
        "years": extra.get("years"),
        "cta": extra.get("cta") or "看看这一天",
        "source_group_id": extra.get("source_group_id"),
        "source_count": extra.get("source_count"),
        "highlight_ids": extra.get("highlight_ids") or [],
        "playable": extra.get("playable") or False,
        "_alts": extra.get("_alts") or [],
    }


def _on_this_day_groups(conn, today: date):
    sql = (
        "SELECT id, width, height, favorite, face_count, bad_cover, day_date, place FROM ("
        "SELECT a.id, a.width, a.height, coalesce(a.favorite,0) favorite"
        + COVER_INNER_SQL + DAY_DATE_SQL + " day_date, " + EFFECTIVE_PLACE + " place "
        "FROM assets a WHERE " + QUALIFIED_SQL + ") dated "
        "WHERE day_date IS NOT NULL AND substr(day_date,6,5)=? AND CAST(substr(day_date,1,4) AS INTEGER) < ?"
    )
    params = (*_day_params(), *QUALIFIED_VALUES, today.strftime("%m-%d"), today.year)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    by_year = {}
    for row in rows:
        by_year.setdefault(row["day_date"][:4], []).append(row)
    groups = []
    for year, items in sorted(by_year.items(), key=lambda kv: kv[0], reverse=True):
        try:
            day = date(int(year), today.month, today.day)
        except ValueError:
            continue
        places = {str(item.get("place") or "").strip() for item in items if str(item.get("place") or "").strip()}
        place = next(iter(places)) if len(places) == 1 else ""
        groups.append(_card(
            "on_this_day", f"day:{day.isoformat()}", format_cn_date(day),
            f"{len(items)} 张照片", len(items), pick_covers(items, 1),
            day.isoformat(), day.isoformat(),
            {"place": place, "place_label": pretty_place_text(place) if place else "", "cta": "看看这一天"},
        ))
    return groups


def _on_this_month_groups(conn, today: date):
    sql = (
        "SELECT id, width, height, favorite, face_count, bad_cover, month_date FROM ("
        "SELECT a.id, a.width, a.height, coalesce(a.favorite,0) favorite"
        + COVER_INNER_SQL + MONTH_DATE_SQL + " month_date "
        "FROM assets a WHERE " + QUALIFIED_SQL + ") dated "
        "WHERE month_date IS NOT NULL AND substr(month_date,6,2)=? AND CAST(substr(month_date,1,4) AS INTEGER) < ?"
    )
    params = (*_month_params(), *QUALIFIED_VALUES, today.strftime("%m"), today.year)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    by_month = {}
    for row in rows:
        by_month.setdefault(row["month_date"], []).append(row)
    groups = []
    for month, items in sorted(by_month.items(), key=lambda kv: kv[0], reverse=True):
        year, mon = month.split("-")
        groups.append(_card(
            "on_this_month", f"month:{month}", format_cn_date(date(int(year), int(mon), 1), month=True),
            f"{len(items)} 张照片 · 那年这个月", len(items), pick_covers(items, 1),
            f"{month}-01", f"{month}-01", {"cta": "看看这个月"},
        ))
    return groups


def _place_range_label(first: date, last: date, count: int) -> str:
    zhang = "张"
    if first == last:
        return f"{format_cn_date_compact(first)} · {count}{zhang}"
    if first.year == last.year and first.month == last.month:
        return f"{first.year}年{first.month}月{first.day}日–{last.day}日 · {count}{zhang}"
    if first.year == last.year:
        return f"{first.year}年{first.month}月{first.day}日–{last.month}月{last.day}日 · {count}{zhang}"
    return f"{format_cn_date_compact(first)}–{format_cn_date_compact(last)} · {count}{zhang}"


def _place_groups(conn, today: date):
    sql = (
        "SELECT id, width, height, favorite, face_count, bad_cover, day_date, place FROM ("
        "SELECT a.id, a.width, a.height, coalesce(a.favorite,0) favorite"
        + COVER_INNER_SQL + DAY_DATE_SQL + " day_date, " + EFFECTIVE_PLACE + " place "
        "FROM assets a WHERE " + QUALIFIED_SQL + ") dated "
        "WHERE day_date IS NOT NULL AND trim(coalesce(place,'')) <> '' AND day_date < ?"
    )
    params = (*_day_params(), *QUALIFIED_VALUES, today.isoformat())
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    by_place = {}
    for row in rows:
        place = str(row.get("place") or "").strip()
        if place:
            by_place.setdefault(place, []).append(row)
    groups = []
    for place, items in by_place.items():
        items.sort(key=lambda row: (row["day_date"], row["id"]))
        segment = []
        start = last = None

        def emit(chunk, place=place):
            if len(chunk) < MIN_PLACE_PHOTOS:
                return
            first_day = date.fromisoformat(chunk[0]["day_date"])
            last_day = date.fromisoformat(chunk[-1]["day_date"])
            if last_day >= today:
                return
            token = _stable(place)[:16]
            cover_ids = pick_place_covers(chunk, 1)
            cover_row = next((row for row in chunk if int(row["id"]) == cover_ids[0]), chunk[0])
            short = pretty_place_short(place)
            full = pretty_place_text(place)
            range_label = _place_range_label(first_day, last_day, len(chunk))
            portrait_people = (
                int(cover_row.get("face_count") or 0) >= 1
                and int(cover_row.get("width") or 0) < int(cover_row.get("height") or 0)
            )
            subtitle = (f"在{short}的片段 · {range_label}" if portrait_people else range_label)
            groups.append(_card(
                "place_span",
                f"place:{token}:{first_day.isoformat()}:{last_day.isoformat()}",
                short,
                subtitle,
                len(chunk), cover_ids,
                first_day.isoformat(), last_day.isoformat(),
                {
                    "place": place,
                    "place_label": short,
                    "title_full": full,
                    "covers": _covers_payload(cover_ids),
                    "cta": "看看这个地方",
                },
            ))

        for row in items:
            day = date.fromisoformat(row["day_date"])
            if not segment:
                segment = [row]
                start = last = day
                continue
            if (day - last).days <= 1 and (day - start).days <= MAX_PLACE_DAYS - 1:
                segment.append(row)
                last = day
                continue
            emit(segment)
            segment = [row]
            start = last = day
        emit(segment)
    groups.sort(key=lambda card: (card["date_to"] or "", card["group_id"]), reverse=True)
    return groups


def _person_groups(conn):
    rows = _person_rows(conn)
    by_person = {}
    for row in rows:
        by_person.setdefault(int(row["person_id"]), []).append(row)
    groups = []
    for person_id, items in by_person.items():
        card = _person_card_from_items(person_id, items)
        if card:
            groups.append(card)
    groups.sort(key=lambda card: (0 if card["kind"] == "person_years" else 1, -card["photo_count"], card["group_id"]))
    return groups


def _rank_groups(groups, today: date, category: str):
    def key(card):
        seed = _stable(f"{today.isoformat()}|{category}|{ALGORITHM_VERSION}|{card['group_id']}")
        year = int(str(card.get("date_from") or "0")[:4] or 0)
        if category == "on_this_day":
            return (0 if card["kind"] == "on_this_day" else 1, -year, seed)
        if category == "place_revisit":
            try:
                age = (today - date.fromisoformat(str(card.get("date_to") or "")[:10])).days
            except ValueError:
                age = 0
            if age < PLACE_MIN_AGE_DAYS:
                recency = 2
            elif age > 8 * 365:
                recency = 1
            else:
                recency = 0
            photos = min(int(card.get("photo_count") or 0), 30)
            return (recency, -photos, seed)
        if category == "people_years":
            span = len(card.get("years") or [])
            dominant = 1 if int(card.get("source_count") or card.get("photo_count") or 0) >= 8000 else 0
            return (0 if card["kind"] == "person_years" else 1, dominant, -span, seed)
        return (seed, card["group_id"])
    return sorted(groups, key=key)


def _public_card(card):
    item = dict(card)
    item.pop("_alts", None)
    item.pop("_pool_ids", None)
    return item


def _stored_person_card(card):
    item = dict(card)
    item.pop("_pool_ids", None)
    return item


def _dedupe_covers(cards):
    seen = set()
    result = []
    for card in cards:
        covers = list(card.get("covers") or [])
        alts = list(card.get("_alts") or [])
        kept = []
        for cover in covers:
            asset_id = int(cover["asset_id"])
            if asset_id not in seen:
                kept.append(cover)
                seen.add(asset_id)
                continue
            replacement = next((item for item in alts if int(item["asset_id"]) not in seen and item.get("year") == cover.get("year")), None)
            if replacement:
                alts.remove(replacement)
                kept.append(replacement)
                seen.add(int(replacement["asset_id"]))
        if not kept:
            continue
        kind = str(card["kind"] or "")
        limit = 4 if kind.startswith("person") or kind.startswith("memory") else 1
        kept = kept[:limit]
        item = _public_card(card)
        item["covers"] = kept
        item["cover_asset_ids"] = [int(cover["asset_id"]) for cover in kept]
        result.append(item)
    return result





MEMORY_TRIP_MIN = 7
MEMORY_PERSON_MIN = 7
MEMORY_DAY_MIN = 7


def _thumb_dir():
    return Path(DATA) / "thumbs"


def _detail_rows(conn, ids):
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    sql = (
        "SELECT a.id, a.sha256, a.width, a.height, coalesce(a.favorite,0) favorite, a.captured_at"
        + COVER_INNER_SQL + DAY_DATE_SQL + " day_date, " + YEAR_DATE_SQL + " year_date, "
        + EFFECTIVE_PLACE + " place "
        "FROM assets a WHERE a.id IN (" + placeholders + ") AND " + QUALIFIED_SQL
    )
    params = (*_day_params(), *_year_params(), *ids, *QUALIFIED_VALUES)
    found = {int(row["id"]): dict(row) for row in conn.execute(sql, params).fetchall()}
    rows = [found[asset_id] for asset_id in ids if asset_id in found]
    for row in rows:
        row["face_ratio"] = 0.0
    return rows


def _attach_person_faces(conn, rows, person_id=None):
    if not rows:
        return rows
    ids = [int(row["id"]) for row in rows]
    placeholders = ",".join("?" for _ in ids)
    sql = (
        "SELECT x.asset_id, x.bbox FROM faces x JOIN people p ON p.id=x.person_id "
        "WHERE x.asset_id IN (" + placeholders + ") AND coalesce(x.ignored,0)=0 "
        "AND coalesce(p.ignored,0)=0"
    )
    params = list(ids)
    if person_id:
        sql += " AND x.person_id=?"
        params.append(int(person_id))
    best = {}
    for row in conn.execute(sql, params).fetchall():
        item = {"id": row["asset_id"], "bbox": row["bbox"], "width": 0, "height": 0}
        current = best.get(int(row["asset_id"]))
        metrics = _face_metrics(item)
        if current is None or metrics["ratio"] > current:
            best[int(row["asset_id"])] = metrics["ratio"]
    for row in rows:
        row["face_ratio"] = float(best.get(int(row["id"])) or 0.0)
    return rows


def _attach_people_sets(conn, rows):
    if not rows:
        return rows
    ids = [int(row["id"]) for row in rows]
    placeholders = ",".join("?" for _ in ids)
    grouped = {asset_id: set() for asset_id in ids}
    for row in conn.execute(
        """SELECT x.asset_id, x.person_id FROM faces x
           JOIN people p ON p.id=x.person_id
           WHERE x.asset_id IN (""" + placeholders + """)
             AND coalesce(x.ignored,0)=0 AND coalesce(p.ignored,0)=0""",
        ids,
    ):
        grouped[int(row["asset_id"])].add(int(row["person_id"]))
    for row in rows:
        row["person_ids"] = sorted(grouped.get(int(row["id"])) or [])
    return rows


def _memory_card(kind, source, highlight_ids, extra=None):
    extra = extra or {}
    covers = extra.get("covers") or _covers_payload(highlight_ids[:4])
    title = extra.get("title") or source.get("title")
    subtitle = extra.get("subtitle") or source.get("subtitle")
    source_id = source.get("group_id")
    group_id = extra.get("group_id") or ("memory:" + kind.split("_", 1)[-1] + ":" + source_id)
    return _card(
        kind,
        group_id,
        title,
        subtitle,
        len(highlight_ids),
        highlight_ids[:4],
        source.get("date_from"),
        source.get("date_to"),
        {
            **extra,
            "covers": covers,
            "place": source.get("place") or extra.get("place"),
            "place_label": source.get("place_label") or extra.get("place_label"),
            "title_full": source.get("title_full") or extra.get("title_full"),
            "person_id": source.get("person_id") or extra.get("person_id"),
            "person_name": source.get("person_name") or extra.get("person_name"),
            "years": source.get("years") or extra.get("years"),
            "cta": extra.get("cta") or "播放",
            "source_group_id": source_id,
            "source_count": extra.get("source_count") or source.get("photo_count"),
            "highlight_ids": highlight_ids,
            "playable": True,
        },
    )


def _curate_group(conn, source, ids, *, mode, person_id=None, min_source=5):
    if len(ids) < min_source:
        return None
    if len(ids) > 80:
        step = max(1, len(ids) // 80)
        ids = ids[::step][:80]
    rows = _detail_rows(conn, ids)
    if person_id:
        _attach_person_faces(conn, rows, person_id)
    if mode == "day":
        _attach_people_sets(conn, rows)
        rows = pick_day_event(rows)
        if len(rows) < min_source:
            return None
    ensure_quality(conn, rows, _thumb_dir())
    highlights = curate_highlights(rows, mode=mode, limit=10)
    if len(highlights) < HIGHLIGHT_MIN:
        return None
    return highlights


def _build_trip_memories(conn, places, today):
    ranked = _rank_groups(places, today, "place_revisit")
    out = []
    for card in ranked:
        if int(card.get("photo_count") or 0) < MEMORY_TRIP_MIN:
            continue
        try:
            meta, ids = _members_for_source(conn, card["group_id"], today)
        except RecommendationError:
            continue
        highlights = _curate_group(conn, card, ids, mode="trip", min_source=MEMORY_TRIP_MIN)
        if not highlights:
            continue
        first = date.fromisoformat(str(card.get("date_from")))
        last = date.fromisoformat(str(card.get("date_to")))
        subtitle = _place_range_label(first, last, len(highlights)).split(" · ")[0]
        out.append(_memory_card("memory_trip", card, highlights, {
            "subtitle": subtitle,
            "title": card.get("place_label") or card.get("title"),
        }))
        if len(out) >= 24:
            break
    return out


def _build_person_memories(conn, people, today):
    ranked = _rank_groups(people, today, "people_years")
    out = []
    for card in ranked:
        if card.get("kind") != "person_years":
            continue
        if int(card.get("source_count") or card.get("photo_count") or 0) < MEMORY_PERSON_MIN:
            continue
        years = card.get("years") or []
        if len(years) < 2:
            continue
        try:
            ids = [int(item) for item in (card.get("_pool_ids") or card.get("highlight_ids") or [])]
            if not ids:
                _meta, ids = _members_for_source(conn, card["group_id"], today)
        except RecommendationError:
            continue
        highlights = _curate_group(
            conn, card, ids, mode="person", person_id=card.get("person_id"), min_source=MEMORY_PERSON_MIN
        )
        if not highlights:
            continue
        subtitle = f"{years[0]} — {years[-1]}"
        out.append(_memory_card("memory_person", card, highlights, {
            "subtitle": subtitle,
            "title": card.get("person_name") or card.get("title"),
        }))
        if len(out) >= 24:
            break
    return out


def _build_day_memories(conn, today):
    groups = _on_this_day_groups(conn, today) or _on_this_month_groups(conn, today)
    ranked = _rank_groups(groups, today, "on_this_day")
    out = []
    for card in ranked:
        if int(card.get("photo_count") or 0) < MEMORY_DAY_MIN:
            continue
        try:
            meta, ids = _members_for_source(conn, card["group_id"], today)
        except RecommendationError:
            continue
        highlights = _curate_group(conn, card, ids, mode="day", min_source=MEMORY_DAY_MIN)
        if not highlights:
            continue
        place = card.get("place_label") or ""
        subtitle = place or str(len(highlights))
        out.append(_memory_card("memory_day", card, highlights, {
            "subtitle": place,
            "title": card.get("title"),
        }))
        if len(out) >= 12:
            break
    return out


def _members_for_source(conn, group_id: str, today: date):
    return _members_for_group(conn, group_id, today)


def _load_memory_catalog(conn, kind):
    cached = _load_catalog_kind(conn, kind)
    if cached is None:
        return []
    if isinstance(cached, dict):
        return cached.get("items") or []
    return cached


def _memory_items(payload):
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload.get("items") or []
    return payload


def _memory_from_catalog(conn, group_id: str):
    row = conn.execute(
        "SELECT payload_json FROM home_stories WHERE story_id=? OR source_group_id=?",
        (group_id, group_id),
    ).fetchone()
    if row:
        return json.loads(row[0])
    for card in _load_all_stories(conn):
        if card.get("group_id") == group_id or card.get("source_group_id") == group_id:
            return card
    for kind in ("memory_trip", "memory_person", "memory_day"):
        for card in _load_memory_catalog(conn, kind):
            if card.get("group_id") == group_id:
                return card
    return None




def _season_of(value) -> str:
    text = str(value or "")
    if len(text) < 7:
        return ""
    try:
        month = int(text[5:7])
    except ValueError:
        return ""
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    if month in (12, 1, 2):
        return "winter"
    return ""


def _existing_story_sources(conn) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT source_group_id FROM home_stories").fetchall()}


def _insert_story(conn, card) -> bool:
    source = str(card.get("source_group_id") or card.get("group_id") or "")
    if not source:
        return False
    story_id = str(card.get("group_id") or ("story:" + source))
    before = conn.execute("SELECT 1 FROM home_stories WHERE story_id=? OR source_group_id=?", (story_id, source)).fetchone()
    if before:
        return False
    conn.execute(
        "INSERT OR IGNORE INTO home_stories(story_id, kind, source_group_id, season, date_from, date_to, title, subtitle, payload_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (story_id, card.get("kind") or "memory_trip", source, _season_of(card.get("date_from")),
         card.get("date_from"), card.get("date_to"), card.get("title") or "", card.get("subtitle") or "",
         json.dumps(_public_card(card), ensure_ascii=False), now()),
    )
    return conn.execute("SELECT changes()").fetchone()[0] > 0


def _load_all_stories(conn):
    rows = conn.execute("SELECT payload_json, season FROM home_stories").fetchall()
    stories = []
    for row in rows:
        card = json.loads(row[0])
        card["_season"] = row[1] or _season_of(card.get("date_from"))
        stories.append(card)
    return stories


def _pick_homepage_stories(stories, today: date):
    season = _season_of(today.isoformat())
    def sort_key(card):
        same = 0 if (card.get("_season") or "") == season else 1
        kind_order = 0 if str(card.get("kind") or "").endswith("trip") else (1 if "person" in str(card.get("kind")) else 2)
        seed = _stable(f"{today.isoformat()}|{card.get('group_id')}")
        return (same, kind_order, seed)
    ranked = sorted(stories, key=sort_key)
    picked = []
    seen_kind = set()
    seen_id = set()
    for card in ranked:
        gid = card.get("group_id")
        kind = str(card.get("kind") or "")
        if gid in seen_id:
            continue
        if kind in seen_kind:
            continue
        picked.append(card)
        seen_kind.add(kind)
        seen_id.add(gid)
        if len(picked) >= 3:
            break
    if len(picked) < 3:
        for card in ranked:
            gid = card.get("group_id")
            if gid in seen_id:
                continue
            picked.append(card)
            seen_id.add(gid)
            if len(picked) >= 3:
                break
    by_kind = {"memory_day": [], "memory_trip": [], "memory_person": []}
    for card in ranked:
        kind = str(card.get("kind") or "")
        if kind in by_kind:
            by_kind[kind].append(_public_card(card))
        elif "trip" in kind:
            by_kind["memory_trip"].append(_public_card(card))
        elif "person" in kind:
            by_kind["memory_person"].append(_public_card(card))
        else:
            by_kind["memory_day"].append(_public_card(card))
    return [_public_card(card) for card in picked], by_kind


def _ingest_closed_stories(conn, places, people, today: date) -> int:
    existing = _existing_story_sources(conn)
    added = 0
    for card in _rank_groups(places, today, "place_revisit"):
        if card.get("group_id") in existing:
            continue
        if int(card.get("photo_count") or 0) < MEMORY_TRIP_MIN:
            continue
        try:
            _meta, ids = _members_for_source(conn, card["group_id"], today)
        except RecommendationError:
            continue
        highlights = _curate_group(conn, card, ids, mode="trip", min_source=MEMORY_TRIP_MIN)
        if not highlights:
            continue
        first = date.fromisoformat(str(card.get("date_from")))
        last = date.fromisoformat(str(card.get("date_to")))
        memory = _memory_card("memory_trip", card, highlights, {
            "subtitle": _place_range_label(first, last, len(highlights)).split(" · ")[0],
            "title": card.get("place_label") or card.get("title"),
        })
        if _insert_story(conn, memory):
            added += 1
            existing.add(card["group_id"])
            conn.commit()
    for card in _rank_groups(people, today, "people_years"):
        if card.get("kind") != "person_years":
            continue
        source = card.get("group_id")
        if source in existing:
            continue
        if int(card.get("source_count") or card.get("photo_count") or 0) < MEMORY_PERSON_MIN:
            continue
        years = card.get("years") or []
        if len(years) < 2:
            continue
        try:
            ids = [int(item) for item in (card.get("_pool_ids") or card.get("highlight_ids") or [])]
            if not ids:
                _meta, ids = _members_for_source(conn, source, today)
        except RecommendationError:
            continue
        highlights = _curate_group(conn, card, ids, mode="person", person_id=card.get("person_id"), min_source=MEMORY_PERSON_MIN)
        if not highlights:
            continue
        memory = _memory_card("memory_person", card, highlights, {
            "subtitle": f"{years[0]} — {years[-1]}",
            "title": card.get("person_name") or card.get("title"),
        })
        if _insert_story(conn, memory):
            added += 1
            existing.add(source)
            conn.commit()
    return added


def catalog_meta(conn):
    ensure_home_schema(conn)
    row = conn.execute(
        "SELECT built_at, algorithm_version, status, message FROM home_catalog_state WHERE id=1"
    ).fetchone()
    if not row:
        return {"built_at": None, "algorithm_version": None, "status": "empty", "message": ""}
    return {
        "built_at": row["built_at"],
        "algorithm_version": row["algorithm_version"],
        "status": row["status"] or "empty",
        "message": row["message"] or "",
    }


def _save_catalog_kind(conn, kind, payload):
    conn.execute(
        "INSERT OR REPLACE INTO home_catalog(kind, algorithm_version, built_at, payload_json) VALUES (?,?,?,?)",
        (kind, ALGORITHM_VERSION, now(), json.dumps(payload, ensure_ascii=False)),
    )


def _load_catalog_kind(conn, kind):
    return _load_catalog_kind_row(conn, kind, match_version=True)


def _load_catalog_kind_row(conn, kind, *, match_version=True):
    row = conn.execute(
        "SELECT payload_json FROM home_catalog WHERE kind=? AND algorithm_version=?"
        if match_version else
        "SELECT payload_json FROM home_catalog WHERE kind=? ORDER BY built_at DESC LIMIT 1",
        (kind, ALGORITHM_VERSION) if match_version else (kind,),
    ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def rebuild_home_catalog(conn, *, today=None):
    with CATALOG_BUILD_LOCK:
        ensure_home_schema(conn)
        today = today or as_of_date()
        people = _person_groups(conn)
        places = _place_groups(conn, today)
        _save_catalog_kind(conn, "people_years", [_stored_person_card(card) for card in people])
        _save_catalog_kind(conn, "place_revisit", places)
        conn.commit()
        added = _ingest_closed_stories(conn, places, people, today)
        conn.commit()
        built = now()
        conn.execute(
            "INSERT OR REPLACE INTO home_catalog_state(id, built_at, algorithm_version, status, message) VALUES (1,?,?,?,?)",
            (built, ALGORITHM_VERSION, "ready", f"added {added}"),
        )
        conn.execute("DELETE FROM home_recommendation_cache")
        return catalog_meta(conn)


def _person_groups_ready(conn):
    cached = _load_catalog_kind(conn, "people_years")
    if cached is not None:
        return cached
    stale = _load_catalog_kind_row(conn, "people_years", match_version=False)
    assets = int(conn.execute("SELECT count(*) FROM assets").fetchone()[0] or 0)
    if stale is not None or assets > INLINE_CATALOG_ASSETS:
        return stale or []
    with CATALOG_BUILD_LOCK:
        cached = _load_catalog_kind(conn, "people_years")
        if cached is not None:
            return cached
        groups = _person_groups(conn)
        groups = [_stored_person_card(card) for card in groups]
        _save_catalog_kind(conn, "people_years", groups)
        _touch_catalog_state(conn)
        return groups


def _place_groups_ready(conn, today: date):
    cached = _load_catalog_kind(conn, "place_revisit")
    if cached is not None:
        return cached
    stale = _load_catalog_kind_row(conn, "place_revisit", match_version=False)
    assets = int(conn.execute("SELECT count(*) FROM assets").fetchone()[0] or 0)
    if stale is not None or assets > INLINE_CATALOG_ASSETS:
        return stale or []
    with CATALOG_BUILD_LOCK:
        cached = _load_catalog_kind(conn, "place_revisit")
        if cached is not None:
            return cached
        groups = _place_groups(conn, today)
        _save_catalog_kind(conn, "place_revisit", groups)
        _touch_catalog_state(conn)
        return groups


def _touch_catalog_state(conn):
    conn.execute(
        "INSERT OR REPLACE INTO home_catalog_state(id, built_at, algorithm_version, status, message) VALUES (1,?,?,?,?)",
        (now(), ALGORITHM_VERSION, "ready", ""),
    )


def build_recommendations(conn, *, category: str = "all", cursor: str = "", today: date | None = None):
    ensure_home_schema(conn)
    today = today or as_of_date()
    if category not in {"all", "on_this_day", "place_revisit", "people_years"}:
        raise RecommendationError(400, "不支持的推荐类别", "invalid_request")
    meta = catalog_meta(conn)
    catalog_stamp = meta.get("built_at") or "none"
    live_revision = data_revision(conn)
    revision = f"{ALGORITHM_VERSION}|{catalog_stamp}|{live_revision}"
    cache_key = f"{ALGORITHM_VERSION}|{today.isoformat()}|{catalog_stamp}|{live_revision}|{category}|{cursor}"
    if category == "all":
        cache_key = f"{ALGORITHM_VERSION}|mem|{today.isoformat()}|{catalog_stamp}|all|{cursor}"
    cached = conn.execute("SELECT payload_json FROM home_recommendation_cache WHERE cache_key=?", (cache_key,)).fetchone()
    if cached:
        payload = json.loads(cached[0])
        payload["catalog"] = meta
        return payload
    if category == "all":
        stories = _load_all_stories(conn)
        if not stories:
            places = _place_groups_ready(conn, today)
            people = _person_groups_ready(conn)
            _ingest_closed_stories(conn, places, people, today)
            stories = _load_all_stories(conn)
        items, by_kind = _pick_homepage_stories(stories, today)
        day_cards = [_public_card(card) for card in (_on_this_day_groups(conn, today) or [])[:12]]
        place_cards = [_public_card(card) for card in (_load_catalog_kind(conn, "place_revisit") or [])[:24]]
        people_cards = [_public_card(card) for card in (_load_catalog_kind(conn, "people_years") or [])[:24]]
        for kind in ("memory_day", "memory_trip", "memory_person"):
            by_kind[kind] = (by_kind.get(kind) or [])[:24]
        conn.commit()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "as_of_date": today.isoformat(),
            "generated_at": now(),
            "revision": revision,
            "category": category,
            "weekday": format_weekday(today),
            "items": _dedupe_covers(items),
            "candidates": {
                "on_this_day": day_cards,
                "place_revisit": place_cards,
                "people_years": people_cards,
                "memory_day": by_kind.get("memory_day") or [],
                "memory_trip": by_kind.get("memory_trip") or [],
                "memory_person": by_kind.get("memory_person") or [],
            },
            "next_cursor": None,
            "catalog": meta,
        }
        conn.execute(
            "INSERT OR REPLACE INTO home_recommendation_cache(cache_key,payload_json,created_at) VALUES (?,?,?)",
            (cache_key, json.dumps(payload, ensure_ascii=False), now()),
        )
        return payload
    day_groups = _on_this_day_groups(conn, today)
    month_groups = _on_this_month_groups(conn, today)
    if category == "on_this_day":
        source = day_groups or month_groups
        places, people = [], []
    elif category == "place_revisit":
        source = _place_groups_ready(conn, today)
        places, people = source, []
    elif category == "people_years":
        source = _person_groups_ready(conn)
        places, people = [], source
    else:
        source = []
        places = _place_groups_ready(conn, today)
        people = _person_groups_ready(conn)
    meta = catalog_meta(conn)
    catalog_stamp = meta.get("built_at") or "none"
    live_revision = data_revision(conn)
    revision = f"{ALGORITHM_VERSION}|{catalog_stamp}|{live_revision}"
    cache_key = f"{ALGORITHM_VERSION}|{today.isoformat()}|{catalog_stamp}|{live_revision}|{category}|{cursor}"
    conn.commit()
    ranked = _rank_groups(source, today, category)
    offset = 0
    if cursor:
        if not re.fullmatch(r"o:\d+", cursor):
            raise RecommendationError(400, "分页游标无效", "invalid_request")
        offset = int(cursor.split(":")[1])
    page = ranked[offset:offset + PAGE_SIZE]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": today.isoformat(),
        "generated_at": now(),
        "revision": revision,
        "category": category,
        "weekday": format_weekday(today),
        "items": [_public_card(card) for card in page],
        "candidates": {category: [_public_card(card) for card in ranked[:60]]},
        "next_cursor": f"o:{offset + PAGE_SIZE}" if offset + PAGE_SIZE < len(ranked) else None,
        "total_groups": len(ranked),
        "catalog": meta,
    }
    conn.execute(
        "INSERT OR REPLACE INTO home_recommendation_cache(cache_key,payload_json,created_at) VALUES (?,?,?)",
        (cache_key, json.dumps(payload, ensure_ascii=False), now()),
    )
    conn.execute(
        "DELETE FROM home_recommendation_cache WHERE created_at < ?",
        ((datetime.now() - timedelta(hours=12)).isoformat(timespec="seconds"),),
    )
    return payload


def invalidate_home_cache(conn) -> None:
    ensure_home_schema(conn)
    conn.execute("DELETE FROM home_recommendation_cache")


def _members_for_group(conn, group_id: str, today: date):
    if str(group_id or "").startswith("memory:"):
        card = _memory_from_catalog(conn, group_id)
        if not card:
            source_id = group_id.split(":", 2)[-1] if group_id.count(":") >= 2 else ""
            if source_id:
                return _members_for_group(conn, source_id, today)
            raise RecommendationError(404, "这条回忆已经不可用", "not_found")
        ids = [int(item) for item in (card.get("highlight_ids") or card.get("cover_asset_ids") or [])]
        if not ids:
            raise RecommendationError(404, "这条回忆已经没有可看的照片", "not_found")
        meta = {
            "kind": card.get("kind") or "memory_trip",
            "title": card.get("title"),
            "date_from": card.get("date_from"),
            "date_to": card.get("date_to"),
            "place": card.get("place"),
            "source_group_id": card.get("source_group_id"),
            "source_count": card.get("source_count"),
            "highlight_ids": ids,
            "playable": True,
        }
        return meta, ids
    if group_id.startswith("day:"):
        day = date.fromisoformat(group_id.split(":", 1)[1])
        sql = "SELECT a.id FROM assets a WHERE " + QUALIFIED_SQL + " AND " + DAY_DATE_SQL + "=? ORDER BY a.id"
        ids = [int(r[0]) for r in conn.execute(sql, (*QUALIFIED_VALUES, *_day_params(), day.isoformat())).fetchall()]
        return {"kind": "on_this_day", "date_from": day.isoformat(), "date_to": day.isoformat(), "title": format_cn_date(day)}, ids
    if group_id.startswith("month:"):
        month = group_id.split(":", 1)[1]
        sql = "SELECT a.id FROM assets a WHERE " + QUALIFIED_SQL + " AND " + MONTH_DATE_SQL + "=? ORDER BY a.id"
        ids = [int(r[0]) for r in conn.execute(sql, (*QUALIFIED_VALUES, *_month_params(), month)).fetchall()]
        year, mon = month.split("-")
        return {"kind": "on_this_month", "date_from": f"{month}-01", "date_to": f"{month}-01", "title": format_cn_date(date(int(year), int(mon), 1), month=True)}, ids
    if group_id.startswith("place:"):
        parts = group_id.split(":")
        if len(parts) != 4:
            raise RecommendationError(400, "推荐组无效", "invalid_request")
        start_d, stop_d = date.fromisoformat(parts[2]), date.fromisoformat(parts[3])
        match = next((card for card in _place_groups_ready(conn, today) if card["group_id"] == group_id), None)
        if not match:
            raise RecommendationError(404, "这组推荐已经不在了", "not_found")
        sql = (
            "SELECT a.id FROM assets a WHERE " + QUALIFIED_SQL + " AND " + DAY_DATE_SQL + " BETWEEN ? AND ? "
            "AND " + EFFECTIVE_PLACE + "=? ORDER BY " + DAY_DATE_SQL + ", a.id"
        )
        ids = [int(r[0]) for r in conn.execute(
            sql, (*QUALIFIED_VALUES, *_day_params(), start_d.isoformat(), stop_d.isoformat(), match["place"], *_day_params())
        ).fetchall()]
        return {"kind": "place_span", "date_from": parts[2], "date_to": parts[3], "place": match["place"], "title": match["title"]}, ids
    if group_id.startswith("person:"):
        person_id = int(group_id.split(":")[1])
        match = next(
            (card for card in (_load_catalog_kind(conn, "people_years") or []) if str(card.get("group_id")) == group_id),
            None,
        )
        ids = []
        for item in (match or {}).get("highlight_ids") or []:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                continue
        if not ids:
            card = _person_card_from_items(person_id, _person_rows(conn, person_id))
            match = card or match
            ids = [int(item) for item in (card or {}).get("highlight_ids") or []]
        if not ids:
            raise RecommendationError(404, "这组人物推荐已经不在了", "not_found")
        title = (match or {}).get("title")
        if not title:
            person = conn.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
            if not person:
                raise RecommendationError(404, "这组人物推荐已经不在了", "not_found")
            title = person[0]
        kind = (match or {}).get("kind") or ("person_years" if len((match or {}).get("years") or []) >= 2 else "person_fragment")
        return {
            "kind": kind,
            "person_id": person_id,
            "title": title,
            "highlight_ids": ids,
            "source_count": (match or {}).get("source_count"),
        }, ids
    raise RecommendationError(400, "推荐组无效", "invalid_request")


def open_group(conn, group_id: str):
    ensure_home_schema(conn)
    today = as_of_date()
    revision = data_revision(conn)
    meta, ids = _members_for_group(conn, str(group_id or "").strip(), today)
    if not ids:
        raise RecommendationError(404, "这组推荐已经没有可看的照片", "not_found")
    snapshot_id = uuid.uuid4().hex
    title = meta["title"]
    subtitle = f"{len(ids)} 张照片"
    payload = {
        "snapshot_id": snapshot_id,
        "group_id": group_id,
        "kind": meta["kind"],
        "title": title,
        "subtitle": subtitle,
        "photo_count": len(ids),
        "as_of_date": today.isoformat(),
        "revision": revision,
        "cover_asset_ids": ids[:4],
        "date_from": meta.get("date_from"),
        "date_to": meta.get("date_to"),
        "source_group_id": meta.get("source_group_id"),
        "source_count": meta.get("source_count"),
        "highlight_ids": meta.get("highlight_ids") or ids,
        "playable": bool(meta.get("playable")),
        "browse": {"recommendation_snapshot": snapshot_id},
    }
    conn.execute(
        """INSERT INTO home_snapshots(
            snapshot_id,group_id,kind,as_of_date,revision,algorithm_version,
            title,subtitle,photo_count,payload_json,member_ids,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (snapshot_id, group_id, meta["kind"], today.isoformat(), revision, ALGORITHM_VERSION,
         title, subtitle, len(ids), json.dumps({**payload, **meta}, ensure_ascii=False), json.dumps(ids), now()),
    )
    prune_snapshots(conn)
    return payload


def load_snapshot(conn, snapshot_id: str):
    if not snapshot_id:
        raise RecommendationError(400, "缺少推荐快照", "invalid_request")
    ensure_home_schema(conn)
    row = conn.execute("SELECT * FROM home_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if not row:
        raise RecommendationError(404, "推荐快照不存在", "not_found")
    snapshot = dict(row)
    if snapshot["revision"] != data_revision(conn) or snapshot["algorithm_version"] != ALGORITHM_VERSION:
        raise RecommendationError(409, "这组内容已更新，请返回首页刷新", "recommendation_expired")
    ids = json.loads(snapshot["member_ids"])
    placeholders = ",".join("?" for _ in ids) or "NULL"
    still = {int(r[0]) for r in conn.execute(
        "SELECT a.id FROM assets a WHERE a.id IN (" + placeholders + ") AND " + QUALIFIED_SQL, (*ids, *QUALIFIED_VALUES)
    ).fetchall()}
    if any(asset_id not in still for asset_id in ids):
        raise RecommendationError(409, "这组内容已更新，请返回首页刷新", "recommendation_expired")
    snapshot["ids"] = ids
    snapshot["payload"] = json.loads(snapshot["payload_json"])
    return snapshot


def fetch_snapshot_photos(conn, snapshot_id: str, *, offset=0, limit=60, sequence=False, around=0, tail=False):
    snapshot = load_snapshot(conn, snapshot_id)
    ids = snapshot["ids"]
    total = len(ids)
    window = min(max(int(limit), 1), 400 if sequence else 120)
    start = max(int(offset), 0)
    if around:
        try:
            idx = ids.index(int(around))
        except ValueError:
            return {"ids": [], "total": total, "offset": 0, "max_id": 0, "has_more": False, "missing": True, "around": around, "items": []}
        start = max(0, idx - window // 2)
    elif tail:
        start = max(0, total - window)
    page_ids = ids[start:start + window]
    items = []
    if page_ids and not sequence:
        placeholders = ",".join("?" for _ in page_ids)
        details = {r["id"]: dict(r) for r in conn.execute(
            "SELECT " + ASSET_LIST_COLUMNS +
            ", (SELECT count(*) FROM files WHERE asset_id=a.id AND exists_now=1 AND excluded=0) copies "
            "FROM assets a WHERE a.id IN (" + placeholders + ")", page_ids,
        ).fetchall()}
        paths = {r["id"]: r["path"] for r in conn.execute(
            "SELECT a.id, (SELECT path FROM files WHERE asset_id=a.id AND excluded=0 AND exists_now=1 ORDER BY id LIMIT 1) path "
            "FROM assets a WHERE a.id IN (" + placeholders + ")", page_ids,
        ).fetchall()}
        for asset_id in page_ids:
            item = details.get(asset_id)
            if not item:
                continue
            item["path"] = paths.get(asset_id)
            items.append(item)
    return {
        "ids": page_ids, "total": total, "offset": start, "max_id": 0,
        "has_more": start + len(page_ids) < total, "missing": False, "items": items,
        "recommendation_snapshot": snapshot_id, "group_id": snapshot["group_id"], "title": snapshot["title"],
    }
