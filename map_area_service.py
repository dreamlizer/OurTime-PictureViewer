"""Pure helpers for map rectangle selection and stable change detection."""
from __future__ import annotations

import hashlib
import json
import math


MAP_AREA_SCOPE = "active_geotagged_all_dates"
MAP_AREA_ALGORITHM = "map-area-selection-v1"
_CHINA_A = 6378245.0
_CHINA_EE = 0.00669342162296594323


def normalize_map_area(coordinate_space, bounds):
    space = str(coordinate_space or "").strip().lower()
    if space not in {"wgs84", "gcj02"}:
        raise ValueError("坐标系无效")
    if not isinstance(bounds, dict):
        raise ValueError("地图范围不完整")
    normalized = {}
    for name in ("west", "south", "east", "north"):
        value = bounds.get(name)
        if isinstance(value, bool):
            raise ValueError("地图范围无效")
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("地图范围无效") from exc
        if not math.isfinite(value):
            raise ValueError("地图范围无效")
        normalized[name] = value
    if not (
        -180 <= normalized["west"] < normalized["east"] <= 180
        and -90 <= normalized["south"] < normalized["north"] <= 90
    ):
        raise ValueError("地图范围无效或跨越日界线，请缩小选区")
    return {"coordinate_space": space, "bounds": normalized}


def _outside_china(latitude, longitude):
    return (
        longitude < 72.004
        or longitude > 137.8347
        or latitude < 0.8293
        or latitude > 55.8271
    )


def _gcj_latitude(x, y):
    value = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y
    value += 0.2 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    value += (20 * math.sin(y * math.pi) + 40 * math.sin(y / 3 * math.pi)) * 2 / 3
    return value + (
        160 * math.sin(y / 12 * math.pi) + 320 * math.sin(y * math.pi / 30)
    ) * 2 / 3


def _gcj_longitude(x, y):
    value = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y
    value += 0.1 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    value += (20 * math.sin(x * math.pi) + 40 * math.sin(x / 3 * math.pi)) * 2 / 3
    return value + (
        150 * math.sin(x / 12 * math.pi) + 300 * math.sin(x / 30 * math.pi)
    ) * 2 / 3


def map_area_wgs84_to_gcj02(latitude, longitude):
    latitude = float(latitude)
    longitude = float(longitude)
    if _outside_china(latitude, longitude):
        return latitude, longitude
    delta_lat = _gcj_latitude(longitude - 105, latitude - 35)
    delta_lng = _gcj_longitude(longitude - 105, latitude - 35)
    radians = latitude / 180 * math.pi
    magic = 1 - _CHINA_EE * math.sin(radians) ** 2
    root = math.sqrt(magic)
    delta_lat = delta_lat * 180 / (
        (_CHINA_A * (1 - _CHINA_EE)) / (magic * root) * math.pi
    )
    delta_lng = delta_lng * 180 / (
        _CHINA_A / root * math.cos(radians) * math.pi
    )
    return latitude + delta_lat, longitude + delta_lng


def _inside(bounds, latitude, longitude):
    return (
        bounds["south"] <= latitude <= bounds["north"]
        and bounds["west"] <= longitude <= bounds["east"]
    )


def display_map_point(area, latitude, longitude):
    latitude = float(latitude)
    longitude = float(longitude)
    if area["coordinate_space"] == "gcj02":
        return map_area_wgs84_to_gcj02(latitude, longitude)
    return latitude, longitude


def gps_in_map_area(area, latitude, longitude):
    point_lat, point_lng = display_map_point(area, latitude, longitude)
    return _inside(area["bounds"], point_lat, point_lng)


def bounds_area_km2(bounds):
    south = float(bounds["south"])
    north = float(bounds["north"])
    west = float(bounds["west"])
    east = float(bounds["east"])
    mid_lat = (south + north) / 2
    lat_km = abs(north - south) * 111.32
    lng_km = abs(east - west) * 111.32 * math.cos(math.radians(mid_lat))
    return max(lat_km * lng_km, 1e-12)


def area_rule_as_map_area(rule):
    return {
        "coordinate_space": rule["coordinate_space"],
        "bounds": {
            "west": float(rule["west"]),
            "south": float(rule["south"]),
            "east": float(rule["east"]),
            "north": float(rule["north"]),
        },
    }


def match_place_area_rule(rules, latitude, longitude):
    best = None
    best_area = None
    for item in rules or ():
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            area = area_rule_as_map_area(item)
            if not gps_in_map_area(area, latitude, longitude):
                continue
            size = bounds_area_km2(area["bounds"])
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or size < best_area:
            best = item
            best_area = size
    return best, best_area


def select_map_area_rows(connection, area, active_asset_sql):
    columns = """
        a.id,a.sha256,a.latitude,a.longitude,a.manual_place,a.place,
        a.manual_date,a.captured_at,a.width,a.height
    """
    bounds = area["bounds"]
    common = (
        active_asset_sql
        + " AND a.latitude IS NOT NULL AND a.longitude IS NOT NULL"
        + " AND a.latitude BETWEEN -90 AND 90"
        + " AND a.longitude BETWEEN -180 AND 180"
    )
    values = []
    if area["coordinate_space"] == "wgs84":
        common += (
            " AND a.latitude BETWEEN ? AND ?"
            " AND a.longitude BETWEEN ? AND ?"
        )
        values.extend(
            [bounds["south"], bounds["north"], bounds["west"], bounds["east"]]
        )
    rows = connection.execute(
        "SELECT " + columns + " FROM assets a WHERE " + common + " ORDER BY a.id",
        values,
    ).fetchall()
    selected = []
    for row in rows:
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            continue
        display_lat, display_lng = (
            map_area_wgs84_to_gcj02(latitude, longitude)
            if area["coordinate_space"] == "gcj02"
            else (latitude, longitude)
        )
        if _inside(bounds, display_lat, display_lng):
            selected.append(row)
    return selected


def map_area_selection_fingerprint(area, rows):
    payload = {
        "algorithm": MAP_AREA_ALGORITHM,
        "scope": MAP_AREA_SCOPE,
        "coordinate_space": area["coordinate_space"],
        "bounds": {
            name: float(area["bounds"][name]).hex()
            for name in ("west", "south", "east", "north")
        },
        "rows": [
            [
                int(row["id"]),
                row["sha256"],
                float(row["latitude"]).hex(),
                float(row["longitude"]).hex(),
                row["manual_place"],
                row["place"],
            ]
            for row in rows
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def map_area_preview_payload(area, rows):
    samples = []
    for row in rows[:9]:
        samples.append(
            {
                "id": int(row["id"]),
                "effective_place": row["manual_place"] or row["place"],
                "effective_date": row["manual_date"] or row["captured_at"],
                "width": row["width"],
                "height": row["height"],
            }
        )
    matched = len(rows)
    return {
        "scope": MAP_AREA_SCOPE,
        "coordinate_space": area["coordinate_space"],
        "bounds": dict(area["bounds"]),
        "matched": matched,
        "manual_place_count": sum(
            1 for row in rows if str(row["manual_place"] or "").strip()
        ),
        "samples": samples,
        "samples_limit": 9,
        "selection_fingerprint": map_area_selection_fingerprint(area, rows),
        "requires_large_confirmation": matched > 1000,
    }
