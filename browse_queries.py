"""Photo and people listing SQL. Pure helpers; pass a live sqlite connection."""
from __future__ import annotations

import calendar
import math
import os
import re
from datetime import datetime
from pathlib import Path

from library_db import ACTIVE_ASSET, ASSET_LIST_COLUMNS, EFFECTIVE_PLACE

PHOTO_ORDERS = {
    'date_desc': 'sort_date DESC, id DESC',
    'date_asc': 'sort_date ASC, id ASC',
    'name_asc': 'file_order(path), path COLLATE NOCASE, id',
    'name_desc': 'file_order(path) DESC, path COLLATE NOCASE DESC, id DESC',
    'recognized_desc': 'recognized_people DESC, visible_faces DESC, sort_date DESC, id DESC',
}

RECOGNIZED_PEOPLE_SQL = (
    "(SELECT count(DISTINCT x.person_id) FROM faces x "
    "JOIN people p ON p.id=x.person_id "
    "WHERE x.asset_id=a.id AND coalesce(x.ignored,0)=0 "
    "AND coalesce(p.ignored,0)=0 AND p.confirmed=1 "
    "AND trim(coalesce(p.name,''))<>'')"
)
VISIBLE_FACE_COUNT_SQL = (
    "(SELECT count(*) FROM faces x JOIN people p ON p.id=x.person_id "
    "WHERE x.asset_id=a.id AND coalesce(x.ignored,0)=0 "
    "AND coalesce(p.ignored,0)=0)"
)


def file_order_key(path):
    return re.sub(r'\d+', lambda m: m[0].zfill(24), Path(path or '').name.casefold())


def directory_predicate(path):
    prefix = str(path).rstrip('\\/') + os.sep
    pattern = ''.join('\\' + ch if ch in '\\%_' else ch for ch in prefix) + '%'
    return "(path=? COLLATE NOCASE OR path LIKE ? ESCAPE '\\')", [str(path), pattern]


def map_viewport_predicate(west, south, east, north):
    """Return one SQL predicate for a Leaflet viewport, including wrapped worlds."""
    try:
        west, south, east, north = (
            float(value) for value in (west, south, east, north)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError('地图视野边界无效') from exc
    if (
        not all(math.isfinite(value) for value in (west, south, east, north))
        or not -90 <= south < north <= 90
    ):
        raise ValueError('地图视野边界无效')

    if -180 <= west <= 180 and -180 <= east <= 180 and west > east:
        longitude_span = (east - west) % 360
    else:
        longitude_span = east - west
    if longitude_span <= 0:
        raise ValueError('地图视野边界无效')

    latitude_clause = 'a.latitude BETWEEN ? AND ?'
    values = [south, north]
    if longitude_span >= 360:
        return latitude_clause, values

    canonical_west = (west + 180) % 360 - 180
    unwrapped_east = canonical_west + longitude_span
    if unwrapped_east <= 180:
        return (
            latitude_clause + ' AND a.longitude BETWEEN ? AND ?',
            values + [canonical_west, unwrapped_east],
        )
    canonical_east = unwrapped_east - 360
    return (
        latitude_clause + ' AND (a.longitude>=? OR a.longitude<=?)',
        values + [canonical_west, canonical_east],
    )


def parse_id_list(raw, limit=100, label='编号'):
    if not raw:
        return []
    try:
        values = list(dict.fromkeys(int(x) for x in str(raw).split(',') if str(x).strip()))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label}无效') from exc
    if any(value <= 0 for value in values):
        raise ValueError(f'{label}必须为正整数')
    if len(values) > limit:
        raise ValueError(f'一次最多查询 {limit} 个{label}')
    return values


def parse_date_bound(raw, *, end=False):
    text = str(raw or '').strip()
    if not text:
        return ''
    try:
        if re.fullmatch(r'\d{4}', text):
            return f'{text}-12-31' if end else f'{text}-01-01'
        if re.fullmatch(r'\d{4}-\d{2}', text):
            year, month = int(text[:4]), int(text[5:7])
            last = calendar.monthrange(year, month)[1]
            return f'{text}-{last:02d}' if end else f'{text}-01'
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            datetime.strptime(text, '%Y-%m-%d')
            return text
    except ValueError as exc:
        raise ValueError('日期无效') from exc
    raise ValueError('日期格式应为年、年-月或年-月-日')


EFFECTIVE_DATE_SQL = "coalesce(a.manual_date, a.captured_at, '')"
PHOTO_DATE_START_SQL = (
    "CASE WHEN " + EFFECTIVE_DATE_SQL + " GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' THEN substr(" + EFFECTIVE_DATE_SQL + ",1,10)"
    " WHEN length(" + EFFECTIVE_DATE_SQL + ")=7 AND " + EFFECTIVE_DATE_SQL + " GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]' THEN " + EFFECTIVE_DATE_SQL + " || '-01'"
    " WHEN length(" + EFFECTIVE_DATE_SQL + ")=4 AND " + EFFECTIVE_DATE_SQL + " GLOB '[0-9][0-9][0-9][0-9]' THEN " + EFFECTIVE_DATE_SQL + " || '-01-01'"
    " ELSE NULL END"
)
PHOTO_DATE_END_SQL = (
    "CASE WHEN " + EFFECTIVE_DATE_SQL + " GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' THEN substr(" + EFFECTIVE_DATE_SQL + ",1,10)"
    " WHEN length(" + EFFECTIVE_DATE_SQL + ")=7 AND " + EFFECTIVE_DATE_SQL + " GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]' THEN date(" + EFFECTIVE_DATE_SQL + " || '-01','+1 month','-1 day')"
    " WHEN length(" + EFFECTIVE_DATE_SQL + ")=4 AND " + EFFECTIVE_DATE_SQL + " GLOB '[0-9][0-9][0-9][0-9]' THEN " + EFFECTIVE_DATE_SQL + " || '-12-31'"
    " ELSE NULL END"
)


def people_select_sql():
    return (
        "SELECT p.id,p.name,p.alias,p.confirmed,p.ignored,p.suggested_person_id,p.cover_face_id,s.name suggested_name, "
        "count(f.id) face_count, count(DISTINCT f.asset_id) photo_count, "
        "coalesce(max(CASE WHEN f.id=p.cover_face_id THEN f.id END),min(f.id)) cover "
        + "FROM people p JOIN faces f ON f.person_id=p.id "
        + "JOIN assets a ON a.id=f.asset_id "
        + "LEFT JOIN people s ON s.id=p.suggested_person_id"
    )


def people_order_sql(sort='photos'):
    if sort == 'photos':
        return "p.confirmed DESC, photo_count DESC, p.id"
    if sort == 'name_asc':
        return (
            "p.confirmed DESC, "
            "CASE WHEN p.confirmed=1 THEN coalesce(nullif(p.name,''),p.alias) END COLLATE PERSON_PINYIN ASC, "
            "CASE WHEN p.confirmed=0 THEN photo_count END DESC, p.id"
        )
    if sort == 'name_desc':
        return (
            "p.confirmed DESC, "
            "CASE WHEN p.confirmed=1 THEN coalesce(nullif(p.name,''),p.alias) END COLLATE PERSON_PINYIN DESC, "
            "CASE WHEN p.confirmed=0 THEN photo_count END DESC, p.id"
        )
    raise ValueError('不支持的人物排序')


def people_base_where(ignored, needle=None, named=0, grouped=True):
    active_person = (
        "EXISTS(SELECT 1 FROM faces ef JOIN assets a ON a.id=ef.asset_id "
        "WHERE ef.person_id=p.id AND " + ACTIVE_ASSET + ")"
    )
    conditions = ["coalesce(p.ignored,0)=?", ACTIVE_ASSET if grouped else active_person]
    values = [int(bool(ignored))]
    if named:
        conditions.append('p.confirmed=1')
    if needle:
        match = "PERSON_SEARCH_MATCH(coalesce(p.name,''),coalesce(p.alias,''),?)=1"
        if named:
            conditions.append(match)
            values.append(needle)
        else:
            conditions.append("(" + match + " OR CAST(p.id AS TEXT) LIKE ?)")
            values.extend([needle, '%' + needle + '%'])
    return conditions, values


def people_query(ignored=0, q='', offset=0, limit=48, ids='', named=0, sort='photos'):
    ignored = int(bool(ignored))
    needle = (q or '').strip()
    selected = parse_id_list(ids, limit=100, label='人物编号')
    page_limit = min(max(int(limit), 1), 100)
    page_offset = max(int(offset), 0)
    named = int(bool(named))
    page_where, page_values = people_base_where(ignored, needle, named=named)
    extra_where, extra_values = people_base_where(ignored, None, named=named)
    total_where, total_values = people_base_where(ignored, needle, named=named, grouped=False)
    select = people_select_sql()
    order_sql = people_order_sql(sort)
    grouped_page = select + f" WHERE {' AND '.join(page_where)} GROUP BY p.id"
    grouped_extra = select + f" WHERE {' AND '.join(extra_where)} GROUP BY p.id"
    extra_sql = None
    extra_params = None
    if selected:
        extra_sql = (
            grouped_extra
            + ' HAVING p.id IN (' + ','.join('?' for _ in selected) + ') '
            + 'ORDER BY ' + order_sql
        )
        extra_params = extra_values + selected
    return {
        'total_sql': f"SELECT count(*) FROM people p WHERE {' AND '.join(total_where)}",
        'total_params': total_values,
        'page_sql': grouped_page + ' ORDER BY ' + order_sql + ' LIMIT ? OFFSET ?',
        'page_params': page_values + [page_limit, page_offset],
        'extra_sql': extra_sql,
        'extra_params': extra_params,
        'selected': selected,
        'limit': page_limit,
        'offset': page_offset,
    }


def photo_conditions(q='', filter='all', person='', directory='', max_id=0, date_from='', date_to='', place=''):
    conditions = ['NOT (' + ACTIVE_ASSET + ')' if filter == 'excluded' else ACTIVE_ASSET]
    conditions.append('EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id)')
    values = []
    year = ''
    filter_place = ''
    group_min = 0
    group_max = 0
    current = filter
    if current.startswith('year:'):
        year = current.split(':', 1)[1]
        current = 'all'
    elif current.startswith('place:'):
        filter_place = current.split(':', 1)[1]
        current = 'all'
    elif current.startswith('group:'):
        raw = current.split(':', 1)[1]
        if raw == '10plus':
            group_min = 10
        else:
            exact = re.fullmatch(r'(\d+)', raw)
            interval = re.fullmatch(r'(\d+)-(\d+)', raw)
            at_least = re.fullmatch(r'(\d+)plus', raw)
            at_most = re.fullmatch(r'upto(\d+)', raw)
            if exact:
                group_min = group_max = int(exact.group(1))
                if group_min < 1:
                    raise ValueError('合影人数至少为 1')
            elif interval:
                group_min, group_max = map(int, interval.groups())
            elif at_least:
                group_min = int(at_least.group(1))
            elif at_most:
                group_max = int(at_most.group(1))
            else:
                raise ValueError('合影人数无效')
        if group_min < 0 or group_max < 0 or group_min > 9999 or group_max > 9999:
            raise ValueError('合影人数无效')
        if (group_min and group_min < 1) or (group_max and group_max < 1):
            raise ValueError('合影人数至少为 1')
        if group_min and group_max and group_min > group_max:
            raise ValueError('最少人数不能大于最多人数')
        current = 'all'
    allowed = {
        'all','timeline','excluded','uncertain','duplicates','errors',
        'missing','screenshots','favorites','no_place',
    }
    if current not in allowed:
        raise ValueError('不支持的照片筛选')
    path_condition = 'asset_id=a.id'
    path_values = []
    if max_id > 0:
        conditions.append('a.id<=?')
        values.append(max_id)
    if directory:
        clause, params = directory_predicate(Path(directory).expanduser().resolve())
        conditions.append('EXISTS(SELECT 1 FROM files WHERE asset_id=a.id AND ' + clause + ')')
        values.extend(params)
        path_condition += ' AND ' + clause
        path_values = list(params)
        if current != 'excluded':
            conditions[-1] = conditions[-1][:-1] + ' AND files.excluded=0)'
            path_condition += ' AND excluded=0'
    if q:
        conditions.append(
            '''(coalesce(a.manual_place,'') LIKE ? OR coalesce(a.place,'') LIKE ? OR a.notes LIKE ?
         OR coalesce(a.manual_date,a.captured_at,'') LIKE ? OR coalesce(a.camera,'') LIKE ?
         OR EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id AND f.path LIKE ?)
         OR EXISTS(SELECT 1 FROM faces x JOIN people p ON p.id=x.person_id WHERE x.asset_id=a.id AND (p.name LIKE ? OR coalesce(p.alias,'') LIKE ?)))'''
        )
        values.extend(['%' + q + '%'] * 8)
    if current == 'uncertain':
        conditions.append("a.manual_date IS NULL AND (a.date_source NOT LIKE 'EXIF%' OR a.date_source IS NULL)")
    if current == 'duplicates':
        conditions.append('(SELECT count(*) FROM files f WHERE f.asset_id=a.id AND exists_now=1 AND excluded=0)>1')
    if current == 'errors':
        conditions.append('(a.error IS NOT NULL OR a.face_state IN (-1,2))')
    if current == 'missing':
        conditions.append('NOT EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id AND exists_now=1)')
    if current == 'screenshots':
        conditions.append("a.category IN ('截图','小图 / 素材')")
    if current == 'favorites':
        conditions.append('coalesce(a.favorite,0)=1')
    if current == 'no_place':
        conditions.append("a.latitude IS NULL AND coalesce(a.manual_place,'')=''")
    if year:
        if year == 'unknown':
            conditions.append("coalesce(a.manual_date,a.captured_at,'')=''")
        else:
            conditions.append("substr(coalesce(a.manual_date,a.captured_at),1,4)=?")
            values.append(year)
    start = parse_date_bound(date_from, end=False)
    stop = parse_date_bound(date_to, end=True)
    if start and stop and start > stop:
        raise ValueError('开始日期不能晚于结束日期')
    if start:
        conditions.append(PHOTO_DATE_START_SQL + ' IS NOT NULL AND ' + PHOTO_DATE_START_SQL + '>=?')
        values.append(start)
    if stop:
        conditions.append(PHOTO_DATE_END_SQL + '<=?')
        values.append(stop)
    place_value = str(place or '').strip() or filter_place
    if place_value:
        if place_value == 'unknown':
            conditions.append("coalesce(nullif(a.manual_place,''), a.place,'')=''")
        else:
            conditions.append("coalesce(nullif(a.manual_place,''), a.place)=?")
            values.append(place_value)
    if group_min or group_max:
        having = ['count(*)>=1']
        if group_min:
            having.append('count(*)>=?')
            values.append(group_min)
        if group_max:
            having.append('count(*)<=?')
            values.append(group_max)
        conditions.append('a.id IN (SELECT asset_id FROM faces GROUP BY asset_id HAVING ' + ' AND '.join(having) + ')')
    if person:
        ids = parse_id_list(person, limit=100, label='人物编号')
        for pid in ids:
            conditions.append('EXISTS(SELECT 1 FROM faces x WHERE x.asset_id=a.id AND x.person_id=?)')
            values.append(pid)
    where = ' AND '.join(conditions)
    path_sql = '(SELECT path FROM files WHERE ' + path_condition + ' ORDER BY excluded,exists_now DESC,id LIMIT 1)'
    return {
        'where': where,
        'values': values,
        'path_values': path_values,
        'path_sql': path_sql,
        'filter': current,
    }


def ranked_from_sql(path_sql, where, order_sql):
    return (
        'SELECT id, pos FROM (SELECT a.id, ROW_NUMBER() OVER (ORDER BY ' + order_sql + ') - 1 AS pos FROM ('
       'SELECT a.id, coalesce(a.manual_date, a.captured_at) sort_date, ' + path_sql + ' path FROM assets a WHERE ' + where +
       ') a)'
   )



def fetch_people(conn, ignored=0, q='', offset=0, limit=48, ids='', named=0, sort='photos'):
    spec = people_query(ignored=ignored, q=q, offset=offset, limit=limit, ids=ids, named=named, sort=sort)
    total = conn.execute(spec['total_sql'], spec['total_params']).fetchone()[0]
    rows = [dict(r) for r in conn.execute(spec['page_sql'], spec['page_params']).fetchall()]
    seen = {row['id'] for row in rows}
    if spec['extra_sql']:
        extras = [dict(r) for r in conn.execute(spec['extra_sql'], spec['extra_params']).fetchall()]
        for row in extras:
            if row['id'] not in seen:
                rows.append(row)
                seen.add(row['id'])
    return {
        'total': total,
        'items': rows,
        'offset': spec['offset'],
        'limit': spec['limit'],
        'selected': spec['selected'],
    }


def photo_metric_select(sort):
    if sort != 'recognized_desc':
        return ''
    return (
        ', ' + RECOGNIZED_PEOPLE_SQL + ' recognized_people'
        ', ' + VISIBLE_FACE_COUNT_SQL + ' visible_faces'
    )


def photo_from_sql(path_sql, where, order_sql, metric_select=''):
    metric_columns = ', recognized_people, visible_faces' if metric_select else ''
    return (
        'SELECT a.id, path, sort_date' + metric_columns + ' FROM ('
        'SELECT a.id, coalesce(a.manual_date, a.captured_at) sort_date, ' + path_sql + ' path '
        + metric_select + ' '
        'FROM assets a WHERE ' + where + ') a ORDER BY ' + order_sql
    )


def nearby_photo_spec(conn, anchor_id, radius_m):
    """Return a small-radius SQL predicate centered on one stored GPS position."""
    try:
        anchor_id = int(anchor_id)
        radius_m = float(radius_m)
    except (TypeError, ValueError) as exc:
        raise ValueError('照片编号或半径无效') from exc
    if anchor_id < 1:
        raise ValueError('照片编号无效')
    if not 1 <= radius_m <= 10_000:
        raise ValueError('地点范围必须在 1 到 10000 米之间')
    anchor = conn.execute(
        'SELECT id,latitude,longitude FROM assets WHERE id=?',
        (anchor_id,),
    ).fetchone()
    if not anchor:
        raise ValueError('作为定位基准的照片不存在')
    if anchor['latitude'] is None or anchor['longitude'] is None:
        raise ValueError('这张照片没有定位坐标')
    latitude = float(anchor['latitude'])
    longitude = float(anchor['longitude'])
    meters_per_latitude = 111_132.0
    meters_per_longitude = max(1.0, 111_320.0 * abs(math.cos(math.radians(latitude))))
    latitude_delta = radius_m / meters_per_latitude
    longitude_delta = radius_m / meters_per_longitude
    clause = (
        'a.latitude BETWEEN ? AND ? AND a.longitude BETWEEN ? AND ? '
        'AND (((a.latitude-?)*?)*((a.latitude-?)*?) '
        '+ ((a.longitude-?)*?)*((a.longitude-?)*?)) <= ?'
    )
    params = [
        latitude - latitude_delta, latitude + latitude_delta,
        longitude - longitude_delta, longitude + longitude_delta,
        latitude, meters_per_latitude, latitude, meters_per_latitude,
        longitude, meters_per_longitude, longitude, meters_per_longitude,
        radius_m * radius_m,
    ]
    return clause, params, {
        'id': anchor_id,
        'latitude': latitude,
        'longitude': longitude,
        'radius_m': radius_m,
    }


def fetch_photos(conn, *, q='', filter='all', person='', offset=0, limit=60, directory='', sort='date_desc',
                 sequence=False, max_id=0, around=0, tail=False, date_from='', date_to='', place='',
                 nearby=0, radius_m=100, map_cell=0, map_lat_bucket=None, map_lng_bucket=None,
                 map_west=None, map_south=None, map_east=None, map_north=None):
    if sort not in PHOTO_ORDERS:
        raise ValueError('未知的照片排序方式')
    spec = photo_conditions(
        q=q, filter=filter, person=person, directory=directory, max_id=max_id,
        date_from=date_from, date_to=date_to, place=place,
    )
    bounds_raw=(map_west,map_south,map_east,map_north)
    bounds_supplied=[value is not None for value in bounds_raw]
    if any(bounds_supplied) and not all(bounds_supplied):
        raise ValueError('地图视野边界必须完整提供')
    map_requested=bool(
        map_cell or map_lat_bucket is not None or map_lng_bucket is not None
        or any(bounds_supplied)
    )
    if map_requested:
        try:
            cell=float(map_cell)
            lat_bucket=float(map_lat_bucket)
            lng_bucket=float(map_lng_bucket)
        except (TypeError, ValueError) as exc:
            raise ValueError('地图定位点无效') from exc
        if not all(math.isfinite(value) for value in (cell,lat_bucket,lng_bucket)) or not .00025<=cell<=45:
            raise ValueError('地图定位点无效')
        spec['where'] += (
            ' AND CAST(floor(a.latitude/?) AS INTEGER)=?'
            ' AND CAST(floor(a.longitude/?) AS INTEGER)=?'
        )
        spec['values'].extend([cell,lat_bucket,cell,lng_bucket])
        if all(bounds_supplied):
            viewport_clause, viewport_values = map_viewport_predicate(*bounds_raw)
            spec['where'] += ' AND ' + viewport_clause
            spec['values'].extend(viewport_values)
    if nearby:
        nearby_clause, nearby_values, _anchor = nearby_photo_spec(conn, nearby, radius_m)
        spec['where'] += ' AND ' + nearby_clause
        spec['values'].extend(nearby_values)
    conn.create_function('file_order', 1, file_order_key)
    conn.execute('BEGIN')
    upper = max_id or conn.execute('SELECT coalesce(max(id),0) FROM assets').fetchone()[0]
    where = spec['where'] + ' AND a.id<=?'
    values = list(spec['values']) + [upper]
    path_values = list(spec['path_values'])
    order_sql = PHOTO_ORDERS[sort]
    metric_select = photo_metric_select(sort)
    query = photo_from_sql(spec['path_sql'], where, order_sql, metric_select)
    total = conn.execute('SELECT count(*) FROM assets a WHERE ' + where, values).fetchone()[0]
    if sequence:
        window = min(max(int(limit), 1), 400)
        start = max(int(offset), 0)
        if around:
            ranked = (
                'SELECT id, ROW_NUMBER() OVER (ORDER BY ' + order_sql + ') - 1 AS pos FROM ('
                'SELECT a.id, coalesce(a.manual_date, a.captured_at) sort_date, ' + spec['path_sql'] + ' path '
                + metric_select + ' '
                'FROM assets a WHERE ' + where + ') a'
            )
            pos = conn.execute(
                'SELECT pos FROM (' + ranked + ') ranked WHERE id=?',
                path_values + values + [around],
            ).fetchone()
            if pos is None:
                return {
                    'ids': [],
                    'total': total,
                    'offset': 0,
                    'max_id': upper,
                    'has_more': False,
                    'missing': True,
                    'around': around,
                }
            idx = int(pos['pos'])
            start = max(0, idx - window // 2)
        elif tail:
            start = max(0, total - window)
        rows = conn.execute(query + ' LIMIT ? OFFSET ?', path_values + values + [window, start]).fetchall()
        return {
            'ids': [r['id'] for r in rows],
            'total': total,
            'offset': start,
            'max_id': upper,
            'has_more': start + len(rows) < total,
            'missing': False,
        }
    page = min(max(int(limit), 1), 120)
    start = max(int(offset), 0)
    rows = conn.execute(query + ' LIMIT ? OFFSET ?', path_values + values + [page, start]).fetchall()
    items = []
    if rows:
        ids = [r['id'] for r in rows]
        placeholders = ','.join('?' for _ in ids)
        detail_sql = (
            'SELECT ' + ASSET_LIST_COLUMNS +
            ', (SELECT count(*) FROM files WHERE asset_id=a.id AND exists_now=1 AND excluded=0) copies '
            'FROM assets a WHERE a.id IN (' + placeholders + ')'
        )
        details = {r['id']: dict(r) for r in conn.execute(detail_sql, ids)}
        items = []
        for row in rows:
            item = dict(details[row['id']], path=row['path'])
            if sort == 'recognized_desc':
                item['recognized_people'] = int(row['recognized_people'] or 0)
                item['visible_faces'] = int(row['visible_faces'] or 0)
            items.append(item)
    return {'total': total, 'items': items, 'max_id': upper, 'missing': False}

