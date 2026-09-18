"""Offline settlement references, Chinese domestic labels and spatial buckets."""
import math
import csv
import re
import threading
import zipfile
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from map_area_service import bounds_area_km2, match_place_area_rule
csv.field_size_limit(10 * 1024 * 1024)

DOMESTIC = {
    'CN': '',
    'HK': '香港特别行政区',
    'MO': '澳门特别行政区',
    'TW': '台湾省',
}
CHINESE = re.compile(r'^[\u3400-\u9fff·（）\s]+$')
MUNICIPALITIES = {'北京市', '上海市', '天津市', '重庆市'}
ADMIN_SUFFIXES = (
    '特别行政区', '维吾尔自治区', '壮族自治区', '回族自治区', '自治区',
    '自治州', '街道', '地区', '新区', '林区', '矿区', '省', '市', '区',
    '县', '旗', '盟', '镇', '乡',
)


def _compact(value):
    return re.sub(r'[^0-9a-z\u3400-\u9fff]', '', str(value or '').casefold())


def _place_parts(value):
    parts = [
        re.sub(r'附近$', '', part.strip())
        for part in re.split(r'\s*·\s*', str(value or '').strip())
        if part.strip()
    ]
    return [part for part in parts if part not in {'中国', '中国大陆'}]


def _short_admin_name(value):
    text = str(value or '').strip()
    for suffix in ADMIN_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[:-len(suffix)]
    return text


def _dedupe_path(parts):
    result = []
    for raw in parts:
        part = re.sub(r'附近$', '', str(raw or '').strip())
        if not part or part in {'中国', '中国大陆'}:
            continue
        if result and part == result[-1]:
            continue
        result.append(part)
    return result


def _is_province(value):
    text = str(value or '').strip()
    return (
        text in MUNICIPALITIES
        or text.endswith(('省', '自治区', '特别行政区'))
    )


def _is_district(value):
    text = str(value or '').strip()
    if text.endswith(('社区', '园区', '景区', '校区', '片区', '小区')):
        return False
    return text.endswith(('区', '县', '旗', '县级市'))


def administrative_prefix(value):
    """Return only the province/city/district portion of a domestic label."""
    parts = _place_parts(value)
    if not parts or not _is_province(parts[0]):
        return []
    if parts[0] in MUNICIPALITIES:
        limit = 2 if len(parts) > 1 and _is_district(parts[1]) else 1
    else:
        limit = 1
        if len(parts) > 1 and parts[1].endswith(('市', '自治州', '地区', '盟')):
            limit = 2
        if len(parts) > limit and _is_district(parts[limit]):
            limit += 1
    return parts[:limit]


def _remove_leading_admin(text, component):
    """Remove one known administrative prefix while preserving manual detail."""
    value = str(text or '').lstrip(' ·，,、')
    choices = sorted(
        {str(component or '').strip(), _short_admin_name(component)},
        key=len,
        reverse=True,
    )
    for choice in choices:
        if choice and value.startswith(choice):
            return value[len(choice):].lstrip(' ·，,、'), True
    return value, False


def merge_manual_place(base_label, manual_label):
    """Attach a user's place detail to a normalized domestic admin prefix."""
    manual_parts = _place_parts(manual_label)
    manual = ' · '.join(manual_parts).strip()
    if not manual:
        return ' · '.join(administrative_prefix(base_label) or _place_parts(base_label))
    prefix = administrative_prefix(base_label)
    if not prefix:
        return manual
    # An explicit, different province is a correction of the GPS-derived place.
    if manual_parts and _is_province(manual_parts[0]):
        if _short_admin_name(manual_parts[0]) != _short_admin_name(prefix[0]):
            return ' · '.join(_dedupe_path(manual_parts))
    detail = manual
    for component in prefix:
        detail, _ = _remove_leading_admin(detail, component)
    detail_parts = _dedupe_path(_place_parts(detail))
    if not detail_parts:
        return ' · '.join(prefix)
    prefix_keys = {_compact(part) for part in prefix}
    detail_parts = [
        part for part in detail_parts
        if _compact(part) not in prefix_keys
        and _compact(_short_admin_name(part))
        not in {_compact(_short_admin_name(item)) for item in prefix}
    ]
    return ' · '.join(_dedupe_path([*prefix, *detail_parts]))


def common_admin_scope(labels):
    """Find a truthful shared province/city/district for an area selection."""
    prefixes = [administrative_prefix(label) for label in labels if str(label or '').strip()]
    if not prefixes or any(not prefix for prefix in prefixes):
        return ''
    common = []
    for index, component in enumerate(prefixes[0]):
        if not all(
            len(prefix) > index and prefix[index] == component
            for prefix in prefixes[1:]
        ):
            break
        common.append(component)
    return ' · '.join(common)


class PlaceIndex:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.buckets = None
        self.districts = None
        self.boundary_file = None
        self.boundary_buckets = None
        self.admin_entries = None
        self.admin_names = None
        self.admin_chinese_names = None
        self.admin_centers = None
        self.overrides = None
        self.overrides_loader = None
        self.override_version = None

        self.area_rules = None
        self.area_rules_loader = None

    def load_overrides(self):
        if self.overrides_loader:
            items, version = self.overrides_loader()
            self.overrides = items or []
            if version != self.override_version:
                self.override_version = version
                self.nearest.cache_clear()
            return
        self.overrides = []

    def set_overrides(self, items, version=None):
        self.overrides = items or []
        if version != self.override_version:
            self.override_version = version
            self.nearest.cache_clear()

    def _build_admin_index(self, name_file):
        rows = {}
        if name_file.exists():
            with name_file.open(encoding='utf-8-sig', newline='') as stream:
                for row in csv.DictReader(stream):
                    row_id = str(row.get('id') or '').strip()
                    if not row_id:
                        continue
                    rows[row_id] = {
                        'id': row_id,
                        'pid': str(row.get('pid') or '').strip(),
                        'name': str(row.get('name') or '').strip(),
                        'ext_name': str(row.get('ext_name') or '').strip(),
                        'pinyin': str(row.get('pinyin') or '').strip(),
                    }
        memo = {}

        def lineage(row_id, seen=None):
            if row_id in memo:
                return memo[row_id]
            row = rows.get(row_id)
            if not row:
                return ()
            seen = set(seen or ())
            if row_id in seen:
                return ()
            seen.add(row_id)
            parent = lineage(row['pid'], seen)
            component = row['ext_name'] or row['name']
            parts = tuple(_dedupe_path([*(item[0] for item in parent), component]))
            ids = [item[1] for item in parent]
            if len(parts) > len(ids):
                ids.append(row_id)
            result = tuple(zip(parts, ids))
            memo[row_id] = result
            return result

        names = defaultdict(list)
        chinese_names = defaultdict(list)
        entries = {}
        for row_id, row in rows.items():
            linked = lineage(row_id)
            entry = {
                'id': row_id,
                'path': tuple(item[0] for item in linked),
                'ids': tuple(item[1] for item in linked),
            }
            entries[row_id] = entry
            keys = {
                _compact(row['name']),
                _compact(row['ext_name']),
                _compact(row['pinyin']),
            }
            for key in keys:
                if key:
                    names[key].append(entry)
            for raw in (row['name'], row['ext_name']):
                key = _compact(raw)
                if key and CHINESE.fullmatch(str(raw or '').strip()):
                    chinese_names[key].append(entry)
        self.admin_entries = entries
        self.admin_names = dict(names)
        self.admin_chinese_names = dict(chinese_names)

    def _admin_path(self, names, lat=None, lon=None, province_hint=None):
        matches = {}
        for name in names or ():
            for key in {_compact(name), _compact(_short_admin_name(name))}:
                for entry in (self.admin_names or {}).get(key, ()):
                    matches[entry['id']] = entry
        candidates = list(matches.values())
        if province_hint:
            province_key = _compact(_short_admin_name(province_hint))
            narrowed = [
                entry for entry in candidates
                if entry['path']
                and _compact(_short_admin_name(entry['path'][0])) == province_key
            ]
            if narrowed:
                candidates = narrowed
        if not candidates:
            return ()

        def rank(entry):
            distance = float('inf')
            if lat is not None and lon is not None:
                for row_id in reversed(entry['ids']):
                    center = (self.admin_centers or {}).get(row_id)
                    if center:
                        distance = self._distance(lat, lon, center[0], center[1])
                        break
            return distance, -len(entry['path']), entry['id']

        return min(candidates, key=rank)['path']

    @staticmethod
    def _label(path):
        return ' · '.join(_dedupe_path(path))

    @lru_cache(maxsize=2048)
    def _explicit_admin_prefix(self, manual_label):
        chunks = [
            _compact(chunk)
            for part in _place_parts(manual_label)
            for chunk in re.split(r'[\s，,、]+', part)
            if _compact(chunk)
        ]
        if not chunks:
            return ()
        candidates = {}
        for key, entries in (self.admin_chinese_names or {}).items():
            short_key = _compact(_short_admin_name(key))
            if len(short_key) < 2:
                continue
            for entry in entries:
                prefix = tuple(administrative_prefix(self._label(entry['path'])))
                # Street/community names can resemble unrelated district names.
                # Only explicit province/city/district rows may override GPS.
                if not prefix or len(prefix) != len(entry['path']):
                    continue
                variants = {
                    _compact(prefix[-1]),
                    _compact(_short_admin_name(prefix[-1])),
                    _compact(''.join(prefix)),
                    _compact(''.join(_short_admin_name(item) for item in prefix)),
                }
                matched = max(
                    (
                        len(variant)
                        for variant in variants
                        if len(variant) >= 2
                        and any(chunk.startswith(variant) for chunk in chunks)
                    ),
                    default=0,
                )
                if not matched:
                    continue
                current = candidates.get(prefix)
                score = (len(prefix), matched, len(short_key))
                if current is None or score > current:
                    candidates[prefix] = score
        if not candidates:
            return ()
        return max(candidates, key=lambda prefix: candidates[prefix])

    def normalize_manual(self, base_label, manual_label):
        self.load()
        base_prefix = tuple(administrative_prefix(base_label))
        explicit = self._explicit_admin_prefix(str(manual_label or ''))
        chosen = base_prefix
        if explicit:
            if not base_prefix or explicit[0] != base_prefix[0]:
                chosen = explicit
            elif len(explicit) > len(base_prefix):
                chosen = explicit
        return merge_manual_place(self._label(chosen) or base_label, manual_label)

    @lru_cache(maxsize=128)
    def _boundary_rings(self, offset, length):
        if not self.boundary_file:
            return ()
        with self.boundary_file.open('rb') as stream:
            stream.seek(offset)
            raw = stream.read(length)
        fields = next(csv.reader([raw.decode('utf-8').rstrip('\r\n')]))
        polygon = fields[-1] if fields else ''
        rings = []
        for part in polygon.split(';'):
            points = []
            for pair in part.split(','):
                xy = pair.strip().split()
                if len(xy) == 2:
                    try:
                        points.append((float(xy[1]), float(xy[0])))
                    except ValueError:
                        continue
            if len(points) >= 3:
                rings.append(points)
        return tuple(tuple(ring) for ring in rings)

    def _district_at(self, lat, lon):
        records = (self.boundary_buckets or {}).get(
            (math.floor(lat), math.floor(lon)), ()
        )
        candidates = [
            record for record in records
            if record['south'] <= lat <= record['north']
            and record['west'] <= lon <= record['east']
        ]
        candidates.sort(
            key=lambda item: (
                (item['north'] - item['south']) * (item['east'] - item['west']),
                item['id'],
            )
        )
        for record in candidates:
            rings = self._boundary_rings(record['offset'], record['length'])
            if any(self._inside(lat, lon, ring) for ring in rings):
                return record['label']
        return ''

    @staticmethod
    def _with_district(label, district):
        if not district:
            return label
        district_parts = _place_parts(district)
        label_parts = _place_parts(label)
        label_admin = administrative_prefix(label)
        if label_admin[:len(district_parts)] == district_parts:
            return ' · '.join(label_parts)
        if not label_admin or label_admin[0] != district_parts[0]:
            return label
        tail = label_parts[len(label_admin):]
        return ' · '.join(_dedupe_path([*district_parts, *tail]))

    def load(self):
        with self.lock:
            if self.buckets is not None:
                return
            buckets = defaultdict(list)
            districts = []
            boundary_buckets = defaultdict(list)
            countries = {}
            # GeoNames aliases can begin with an obsolete district name. Prefer
            # an existing Chinese alias matching the current primary place name,
            # checked against the local official-name table, never transliterated.
            name_file = self.root / 'china-admin/extracted/ok_data_level4.csv'
            self._build_admin_index(name_file)
            self.admin_centers = {}
            country_file = self.root / 'geonames/countryInfo.txt'
            if country_file.exists():
                for line in country_file.read_text(encoding='utf-8').splitlines():
                    fields = line.split('\t')
                    if not line.startswith('#') and len(fields) > 4:
                        countries[fields[0]] = fields[4]
            def add_point(lat, lon, label, kind='global'):
                buckets[(math.floor(lat), math.floor(lon))].append((lat, lon, label, kind))
            geo_file = self.root / 'china-boundaries/extracted/ok_geo.csv'
            if geo_file.exists():
                self.boundary_file = geo_file
                with geo_file.open('rb') as stream:
                    header = next(csv.reader([stream.readline().decode('utf-8-sig')]))
                    while True:
                        offset = stream.tell()
                        raw = stream.readline()
                        if not raw:
                            break
                        length = len(raw)
                        fields = next(csv.reader([raw.decode('utf-8').rstrip('\r\n')]))
                        row = dict(zip(header, fields))
                        row_id = str(row.get('id') or '').strip()
                        geo = str(row.get('geo') or '').strip().split()
                        if len(geo) == 2:
                            try:
                                self.admin_centers[row_id] = (float(geo[1]), float(geo[0]))
                            except ValueError:
                                pass
                        if row.get('deep') != '2' or not row.get('polygon'):
                            continue
                        south, north = 90.0, -90.0
                        west, east = 180.0, -180.0
                        for part in row['polygon'].split(';'):
                            for pair in part.split(','):
                                xy = pair.strip().split()
                                if len(xy) == 2:
                                    try:
                                        point_lon, point_lat = float(xy[0]), float(xy[1])
                                    except ValueError:
                                        continue
                                    south, north = min(south, point_lat), max(north, point_lat)
                                    west, east = min(west, point_lon), max(east, point_lon)
                        if south > north or west > east:
                            continue
                        entry = (self.admin_entries or {}).get(row_id)
                        path = entry['path'] if entry else tuple(
                            str(row.get('ext_path') or row.get('name') or '').split()
                        )
                        record = {
                            'id': row_id,
                            'label': self._label(path),
                            'south': south, 'north': north,
                            'west': west, 'east': east,
                            'offset': offset, 'length': length,
                        }
                        for bucket_lat in range(math.floor(south), math.floor(north) + 1):
                            for bucket_lon in range(math.floor(west), math.floor(east) + 1):
                                boundary_buckets[(bucket_lat, bucket_lon)].append(record)
            self.boundary_buckets = dict(boundary_buckets)
            beijing = self.root / 'beijing/geonames_beijing_admin1_22.txt'
            keep = {'PPL','PPLA','PPLA2','PPLA3','PPLA4','PPLC','PPLX'}
            if beijing.exists():
                for line in beijing.read_text(encoding='utf-8').splitlines():
                    f = line.split('\t')
                    if len(f) < 8:
                        continue
                    lat, lon = float(f[4]), float(f[5])
                    if f[7] in keep:
                        names = [n for n in [f[1], *f[3].split(',')] if CHINESE.fullmatch(n) and n not in {'北京','北京市'}]
                        if names:
                            name = names[-1] if any(n.endswith(('街道','乡','镇','地区')) for n in names) else names[0]
                            path = self._admin_path(names, lat, lon, '北京市')
                            label = self._label(path or ('北京市', name))
                            add_point(lat, lon, label, 'local')
                    elif f[6] == 'A' and f[7] == 'ADM3':
                        names = [n for n in [f[1], *f[3].split(',')] if CHINESE.fullmatch(n) and n.endswith('区')]
                        if names:
                            path = self._admin_path(names, lat, lon, '北京市')
                            add_point(lat, lon, self._label(path or ('北京市', names[0])), 'district')
                        continue
            osm_file = self.root / 'osm/beijing-places.json'
            if osm_file.exists():
                try:
                    osm = json.loads(osm_file.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    osm = {}
                for el in osm.get('elements', []):
                    tags = el.get('tags') or {}
                    name = tags.get('name:zh') or tags.get('name') or ''
                    if not CHINESE.search(name):
                        continue
                    if 'center' in el:
                        lat, lon = el['center']['lat'], el['center']['lon']
                    elif 'lat' in el and 'lon' in el:
                        lat, lon = el['lat'], el['lon']
                    else:
                        continue
                    place = tags.get('place') or ''
                    admin = tags.get('admin_level') or ''
                    if name.endswith(('街道','地区','乡','镇')) or place == 'suburb' or admin == '8':
                        kind = 'street'
                    elif place in {'neighbourhood','quarter','city_block'} or admin in {'9','10'}:
                        kind = 'local'
                    else:
                        continue
                    path = self._admin_path((name,), float(lat), float(lon), '北京市')
                    label = self._label(path or ('北京市', name))
                    add_point(float(lat), float(lon), label, kind)
            self.districts = districts
            pack = self.root / 'geonames/cities500.zip'
            if pack.exists():
                with zipfile.ZipFile(pack) as archive, archive.open('cities500.txt') as stream:
                    for line in stream:
                        f = line.decode('utf-8').rstrip('\n').split('\t')
                        if len(f) < 15:
                            continue
                        lat, lon, country = float(f[4]), float(f[5]), f[8]
                        if country in DOMESTIC:
                            names = [n for n in [f[1], *f[3].split(',')] if CHINESE.fullmatch(n)]
                            path = self._admin_path(
                                [*names, f[2]], lat, lon,
                                DOMESTIC[country] or None,
                            )
                            if path:
                                label = self._label(path)
                            else:
                                name = names[0] if names else '地名待确认'
                                label = self._label(
                                    [DOMESTIC[country], name]
                                )
                        else:
                            label = f'{f[2] or f[1]} · {countries.get(country, country)}'
                        add_point(lat, lon, label, False)
            self.buckets = dict(buckets)

    @lru_cache(maxsize=20000)
    def nearest(self, lat, lon, include_overrides=True):
        self.load()
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None, None
        district = self._district_at(lat, lon)
        if include_overrides and self.overrides_loader:
            items, version = self.overrides_loader()
            if version != self.override_version or self.overrides is None:
                self.set_overrides(items, version)
        elif include_overrides and self.overrides is None:
            self.load_overrides()
        if include_overrides and self.area_rules_loader:
            area_items, area_version = self.area_rules_loader()
            if area_version != self.override_version or self.area_rules is None:
                self.area_rules = area_items or []
        elif include_overrides and self.area_rules is None:
            self.area_rules = []
        matched_rule = None
        if include_overrides:
            area_item, area_size = match_place_area_rule(self.area_rules, lat, lon)
            if area_item and area_size is not None:
                matched_rule = {
                    'name': area_item['name'],
                    'source': area_item.get('source') or '用户框选区域',
                    'area_km2': area_size,
                    'kind': 'area',
                }
            for item in self.overrides or ():
                try:
                    plat, plon = float(item['latitude']), float(item['longitude'])
                    radius = float(item.get('radius_km') or 0.25)
                except (KeyError, TypeError, ValueError):
                    continue
                name = str(item.get('name') or '').strip()
                if not name:
                    continue
                distance = self._distance(lat, lon, plat, plon)
                if distance > radius:
                    continue
                size = math.pi * radius * radius
                if matched_rule is None or size < matched_rule['area_km2'] - 1e-9 or (
                    abs(size - matched_rule['area_km2']) <= 1e-9 and matched_rule['kind'] != 'area'
                ):
                    matched_rule = {
                        'name': name,
                        'source': item.get('source') or f'用户确认常用地点，约 {distance*1000:.0f} 米',
                        'area_km2': size,
                        'kind': 'circle',
                    }

        def selected(label, source):
            label = self._with_district(label, district)
            if matched_rule:
                label = self.normalize_manual(label, matched_rule['name'])
                source = matched_rule['source']
            return label, source

        candidates = []
        for y in range(math.floor(lat - 1), math.floor(lat + 1) + 1):
            for x in range(math.floor(lon - 2), math.floor(lon + 2) + 1):
                candidates.extend(self.buckets.get((y, (x + 180) % 360 - 180), ()))
        candidates = [p for p in candidates if abs(p[0]-lat) <= 1 and abs((p[1]-lon+180)%360-180) <= 2]
        if not candidates:
            if district:
                return selected(district, '离线行政区划边界；不是小区或街道精确地址')
            if matched_rule:
                return (
                    ' · '.join(_place_parts(matched_rule['name'])),
                    matched_rule['source'],
                )
            return None, None
        def distance(p):
            return self._distance(lat, lon, p[0], p[1])
        scored = [(distance(p), p) for p in candidates]
        local = [(d, p) for d, p in scored if len(p) > 3 and p[3] == 'local' and d <= 0.8]
        if local:
            d, p = min(local, key=lambda x: x[0])
            return selected(p[2], f'OpenStreetMap 社区/街区参考，约 {d:.1f} 公里；不是门牌地址')
        streets = [(d, p) for d, p in scored if len(p) > 3 and p[3] == 'street' and d <= 1.5]
        if streets:
            d, p = min(streets, key=lambda x: x[0])
            return selected(p[2], f'OpenStreetMap 街道参考，约 {d:.1f} 公里；不是门牌地址')
        if district:
            return selected(district, '离线行政区划边界；不是小区或街道精确地址')
        d, p = min(scored, key=lambda x: x[0])
        if d > 80:
            if matched_rule:
                return (
                    ' · '.join(_place_parts(matched_rule['name'])),
                    matched_rule['source'],
                )
            return None, None
        return selected(p[2], f'离线地名库最近聚居地，约 {d:.1f} 公里；不是精确地址')

    @staticmethod
    def _distance(lat1, lon1, lat2, lon2):
        a = math.sin(math.radians(lat1-lat2)/2)**2 + math.cos(math.radians(lat2))*math.cos(math.radians(lat1))*math.sin(math.radians(lon1-lon2)/2)**2
        return 12742 * math.asin(min(1, math.sqrt(a)))

    @staticmethod
    def _inside(lat, lon, ring):
        inside = False
        j = len(ring) - 1
        for i, (yi, xi) in enumerate(ring):
            yj, xj = ring[j]
            if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
                inside = not inside
            j = i
        return inside
