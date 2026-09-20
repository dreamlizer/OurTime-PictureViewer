"""Homepage discovery recommendations. Isolated from scan and EXIF writers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from library_db import ACTIVE_ASSET, ASSET_LIST_COLUMNS, EFFECTIVE_PLACE, now

ALGORITHM_VERSION = "home-discovery-v5"
SCHEMA_VERSION = 1
CATALOG_BUILD_LOCK = threading.Lock()
SNAPSHOT_LIMIT = 80
PAGE_SIZE = 6
MAX_PLACE_DAYS = 7
MIN_PLACE_PHOTOS = 3
MIN_PERSON_PHOTOS = 2
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
    visible = 1 if 0.02 <= metrics["ratio"] <= 0.5 else (0.4 if metrics["ratio"] > 0.5 else 0)
    solo = 1 if face_count <= 2 else 0
    return (
        1 if metrics["ok"] else 0,
        visible,
        solo,
        metrics["ratio"],
        int(row.get("favorite") or 0),
        -face_count,
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
    sql = (
        "SELECT p.id person_id, p.name person_name, a.id, a.width, a.height, coalesce(a.favorite,0) favorite"
        + COVER_INNER_SQL + YEAR_DATE_SQL + " year_date, x.id face_id, x.bbox bbox FROM people p "
        "JOIN faces x ON x.person_id=p.id AND coalesce(x.ignored,0)=0 "
        "JOIN assets a ON a.id=x.asset_id "
        "WHERE coalesce(p.ignored,0)=0 AND p.confirmed=1 AND trim(coalesce(p.name,''))<>'' "
        "AND " + QUALIFIED_SQL
    )
    rows = [dict(r) for r in conn.execute(sql, (*_year_params(), *QUALIFIED_VALUES)).fetchall()]
    by_person = {}
    for row in rows:
        by_person.setdefault(int(row["person_id"]), []).append(row)
    groups = []
    for person_id, items in by_person.items():
        members = _best_person_rows(items)
        if len(members) < MIN_PERSON_PHOTOS:
            continue
        years = sorted({row["year_date"] for row in members if row.get("year_date")})
        name = members[0]["person_name"]
        if len(years) >= 2:
            kind, title, subtitle, cta = "person_years", name, f"{years[0]} — {years[-1]}", "看看这些年"
        else:
            kind, title, subtitle, cta = "person_fragment", f"与{name}的片段", f"{len(members)} 张照片", "看看这些年"
        by_year = {}
        undated = []
        for row in members:
            year = row.get("year_date")
            if year:
                by_year.setdefault(str(year), []).append(row)
            else:
                undated.append(row)
        chosen_years = _choose_span_years(list(by_year))
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
            ranked_rows = sorted(members, key=_person_photo_score, reverse=True)
            for row in ranked_rows[:4]:
                covers.append(_cover_from_row(row, year=row.get("year_date")))
        groups.append(_card(
            kind, f"person:{person_id}", title, subtitle, len(members), [item["asset_id"] for item in covers[:4]],
            f"{years[0]}-01-01" if years else None, f"{years[-1]}-12-31" if years else None,
            {"person_id": person_id, "person_name": name, "years": years, "cta": cta, "covers": covers[:4], "_alts": alts},
        ))
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
            dominant = 1 if int(card.get("photo_count") or 0) >= 8000 else 0
            return (0 if card["kind"] == "person_years" else 1, dominant, -span, seed)
        return (seed, card["group_id"])
    return sorted(groups, key=key)


def _public_card(card):
    item = dict(card)
    item.pop("_alts", None)
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
        limit = 4 if str(card["kind"]).startswith("person") else 1
        kept = kept[:limit]
        item = _public_card(card)
        item["covers"] = kept
        item["cover_asset_ids"] = [int(cover["asset_id"]) for cover in kept]
        result.append(item)
    return result



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
    row = conn.execute(
        "SELECT payload_json FROM home_catalog WHERE kind=? AND algorithm_version=?",
        (kind, ALGORITHM_VERSION),
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
        _save_catalog_kind(conn, "people_years", people)
        _save_catalog_kind(conn, "place_revisit", places)
        built = now()
        conn.execute(
            "INSERT OR REPLACE INTO home_catalog_state(id, built_at, algorithm_version, status, message) VALUES (1,?,?,?,?)",
            (built, ALGORITHM_VERSION, "ready", ""),
        )
        conn.execute("DELETE FROM home_recommendation_cache")
        return catalog_meta(conn)


def _person_groups_ready(conn):
    cached = _load_catalog_kind(conn, "people_years")
    if cached is not None:
        return cached
    with CATALOG_BUILD_LOCK:
        cached = _load_catalog_kind(conn, "people_years")
        if cached is not None:
            return cached
        groups = _person_groups(conn)
        _save_catalog_kind(conn, "people_years", groups)
        _touch_catalog_state(conn)
        return groups


def _place_groups_ready(conn, today: date):
    cached = _load_catalog_kind(conn, "place_revisit")
    if cached is not None:
        return cached
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
    cached = conn.execute("SELECT payload_json FROM home_recommendation_cache WHERE cache_key=?", (cache_key,)).fetchone()
    if cached:
        payload = json.loads(cached[0])
        payload["catalog"] = meta
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
    if category == "all":
        primary_list = day_groups or month_groups
        day_ranked = _rank_groups(primary_list, today, "on_this_day")
        place_ranked = _rank_groups(places, today, "place_revisit")
        people_ranked = _rank_groups(people, today, "people_years")
        items = [card for card in (day_ranked[:1] + place_ranked[:1] + people_ranked[:1]) if card]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "as_of_date": today.isoformat(),
            "generated_at": now(),
            "revision": revision,
            "category": category,
            "weekday": format_weekday(today),
            "items": _dedupe_covers(items),
            "candidates": {
                "on_this_day": [_public_card(card) for card in day_ranked[:24]],
                "place_revisit": [_public_card(card) for card in place_ranked[:24]],
                "people_years": [_public_card(card) for card in people_ranked[:24]],
            },
            "next_cursor": None,
            "catalog": meta,
        }
    else:
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
            "candidates": {category: [_public_card(card) for card in ranked]},
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
        sql = (
            "SELECT DISTINCT a.id, " + YEAR_DATE_SQL + " year_date FROM assets a "
            "JOIN faces x ON x.asset_id=a.id AND x.person_id=? AND coalesce(x.ignored,0)=0 "
            "JOIN people p ON p.id=? AND p.confirmed=1 AND coalesce(p.ignored,0)=0 "
            "AND trim(coalesce(p.name,''))<>'' "
            "WHERE " + QUALIFIED_SQL + " ORDER BY a.id"
        )
        rows = conn.execute(sql, (*_year_params(), person_id, person_id, *QUALIFIED_VALUES)).fetchall()
        dated = []
        undated = []
        for row in rows:
            (dated if row[1] else undated).append(row)
        dated.sort(key=lambda row: (row[1], row[0]))
        ids = [int(r[0]) for r in dated + undated]
        person = conn.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
        if not person or not ids:
            raise RecommendationError(404, "这组人物推荐已经不在了", "not_found")
        years = [r[1] for r in dated]
        title = person[0] if len(set(years)) >= 2 else f"与{person[0]}的片段"
        return {"kind": "person_years" if len(set(years)) >= 2 else "person_fragment", "person_id": person_id, "title": title}, ids
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
