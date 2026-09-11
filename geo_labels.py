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
csv.field_size_limit(10 * 1024 * 1024)

DOMESTIC = {'CN': '中国', 'HK': '中国香港', 'MO': '中国澳门', 'TW': '中国台湾'}
CHINESE = re.compile(r'^[\u3400-\u9fff·（）\s]+$')


class PlaceIndex:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.buckets = None
        self.districts = None
        self.overrides = None
        self.overrides_loader = None
        self.override_version = None

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

    def load(self):
        with self.lock:
            if self.buckets is not None:
                return
            buckets = defaultdict(list)
            districts = []
            countries = {}
            # GeoNames aliases can begin with an obsolete district name. Prefer
            # an existing Chinese alias matching the current primary place name,
            # checked against the local official-name table, never transliterated.
            spellings = defaultdict(set)
            name_file = self.root / 'china-admin/extracted/ok_data_level4.csv'
            if name_file.exists():
                with name_file.open(encoding='utf-8-sig',newline='') as stream:
                    for row in csv.DictReader(stream):
                        key=re.sub(r'[^a-z]','',row['pinyin'].lower())
                        spellings[key].update((row['name'],row['ext_name']))
            country_file = self.root / 'geonames/countryInfo.txt'
            if country_file.exists():
                for line in country_file.read_text(encoding='utf-8').splitlines():
                    fields = line.split('\t')
                    if not line.startswith('#') and len(fields) > 4:
                        countries[fields[0]] = fields[4]
            def add_point(lat, lon, label, kind='global'):
                buckets[(math.floor(lat), math.floor(lon))].append((lat, lon, label, kind))
            beijing = self.root / 'beijing/geonames_beijing_admin1_22.txt'
            keep = {'PPL','PPLA','PPLA2','PPLA3','PPLA4','PPLC','PPLX'}
            if beijing.exists():
                for line in beijing.read_text(encoding='utf-8').splitlines():
                    f = line.split('\t')
                    if len(f) < 8:
                        continue
                    if f[7] in keep:
                        names = [n for n in [f[1], *f[3].split(',')] if CHINESE.fullmatch(n) and n not in {'北京','北京市'}]
                        if names:
                            name = names[-1] if any(n.endswith(('街道','乡','镇','地区')) for n in names) else names[0]
                            add_point(float(f[4]), float(f[5]), f'中国 · {name}附近', 'local')
                    elif f[6] == 'A' and f[7] == 'ADM3':
                        names = [n for n in [f[1], *f[3].split(',')] if CHINESE.fullmatch(n) and n.endswith('区')]
                        if names:
                            add_point(float(f[4]), float(f[5]), f'中国 · {names[0]}', 'district')
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
                        kind, label = 'street', f'中国 · {name}'
                    elif place in {'neighbourhood','quarter','city_block'} or admin in {'9','10'}:
                        kind, label = 'local', f'中国 · {name}附近'
                    else:
                        continue
                    add_point(float(lat), float(lon), label, kind)
            geo_file = self.root / 'china-boundaries/extracted/ok_geo.csv'
            if geo_file.exists():
                with geo_file.open(encoding='utf-8-sig', newline='') as stream:
                    for row in csv.DictReader(stream):
                        if row.get('deep') != '2' or not row.get('polygon'):
                            continue
                        if not (row['id'].startswith('11') or '北京' in row.get('ext_path','')):
                            continue
                        rings = []
                        for part in row['polygon'].split(';'):
                            pts = []
                            for pair in part.split(','):
                                xy = pair.strip().split()
                                if len(xy) == 2:
                                    pts.append((float(xy[1]), float(xy[0])))
                            if len(pts) >= 3:
                                rings.append(pts)
                        if rings:
                            districts.append((f"中国 · {row['name']}", rings))
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
                            preferred=spellings.get(re.sub(r'[^a-z]','',f[2].lower()),set())
                            name = next((n for n in names if n in preferred),names[0] if names else '地名待确认')
                            label = f'{DOMESTIC[country]} · {name}附近' if names else f'{DOMESTIC[country]} · 地名待确认'
                        else:
                            label = f'{f[2] or f[1]} · {countries.get(country, country)}'
                        add_point(lat, lon, label, False)
            self.buckets = dict(buckets)

    @lru_cache(maxsize=20000)
    def nearest(self, lat, lon):
        self.load()
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None, None
        if self.overrides_loader:
            items, version = self.overrides_loader()
            if version != self.override_version or self.overrides is None:
                self.set_overrides(items, version)
        elif self.overrides is None:
            self.load_overrides()
        for item in self.overrides or ():
            try:
                plat, plon = float(item['latitude']), float(item['longitude'])
                radius = float(item.get('radius_km') or 0.25)
            except (KeyError, TypeError, ValueError):
                continue
            d = self._distance(lat, lon, plat, plon)
            if d <= radius and item.get('name'):
                return item['name'], item.get('source') or f'用户确认常用地点，约 {d*1000:.0f} 米'
        candidates = []
        for y in range(math.floor(lat - 1), math.floor(lat + 1) + 1):
            for x in range(math.floor(lon - 2), math.floor(lon + 2) + 1):
                candidates.extend(self.buckets.get((y, (x + 180) % 360 - 180), ()))
        candidates = [p for p in candidates if abs(p[0]-lat) <= 1 and abs((p[1]-lon+180)%360-180) <= 2]
        if not candidates:
            return None, None
        def distance(p):
            return self._distance(lat, lon, p[0], p[1])
        scored = [(distance(p), p) for p in candidates]
        local = [(d, p) for d, p in scored if len(p) > 3 and p[3] == 'local' and d <= 0.8]
        if local:
            d, p = min(local, key=lambda x: x[0])
            return p[2], f'OpenStreetMap 社区/街区参考，约 {d:.1f} 公里；不是门牌地址'
        streets = [(d, p) for d, p in scored if len(p) > 3 and p[3] == 'street' and d <= 1.5]
        if streets:
            d, p = min(streets, key=lambda x: x[0])
            return p[2], f'OpenStreetMap 街道参考，约 {d:.1f} 公里；不是门牌地址'
        for name, rings in self.districts or ():
            if any(self._inside(lat, lon, ring) for ring in rings):
                return name, '离线行政区划边界；不是小区或街道精确地址'
        d, p = min(scored, key=lambda x: x[0])
        if d > 80:
            return None, None
        return p[2], f'离线地名库最近聚居地，约 {d:.1f} 公里；不是精确地址'

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
