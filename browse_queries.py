"""Photo and people listing SQL. Pure helpers; pass a live sqlite connection."""
from __future__ import annotations

import os
import re
from pathlib import Path

from library_db import ACTIVE_ASSET, ASSET_LIST_COLUMNS, EFFECTIVE_PLACE

PHOTO_ORDERS = {
    'date_desc': 'sort_date DESC, id DESC',
    'date_asc': 'sort_date ASC, id ASC',
    'name_asc': 'file_order(path), path COLLATE NOCASE, id',
    'name_desc': 'file_order(path) DESC, path COLLATE NOCASE DESC, id DESC',
}


def file_order_key(path):
    return re.sub(r'\d+', lambda m: m[0].zfill(24), Path(path or '').name.casefold())


def directory_predicate(path):
    prefix = str(path).rstrip('\\/') + os.sep
    pattern = ''.join('\\' + ch if ch in '\\%_' else ch for ch in prefix) + '%'
    return "(path=? COLLATE NOCASE OR path LIKE ? ESCAPE '\\')", [str(path), pattern]


def parse_id_list(raw, limit=100, label='编号'):
    if not raw:
        return []
    try:
        values = list(dict.fromkeys(int(x) for x in str(raw).split(',') if str(x).strip()))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label}无效') from exc
    if len(values) > limit:
        raise ValueError(f'一次最多查询 {limit} 个{label}')
    return values


def people_select_sql():
    return (
        "SELECT p.id,p.name,p.alias,p.confirmed,p.ignored,p.suggested_person_id,s.name suggested_name, "
        "count(f.id) face_count, count(DISTINCT f.asset_id) photo_count, min(f.id) cover "
        "FROM people p JOIN faces f ON f.person_id=p.id "
        "LEFT JOIN people s ON s.id=p.suggested_person_id"
    )


def people_base_where(ignored, needle=None):
    conditions = ["coalesce(p.ignored,0)=?", "EXISTS(SELECT 1 FROM faces f WHERE f.person_id=p.id)"]
    values = [int(bool(ignored))]
    if needle:
        conditions.append("(coalesce(p.name,'') LIKE ? OR coalesce(p.alias,'') LIKE ? OR CAST(p.id AS TEXT) LIKE ?)")
        like = '%' + needle + '%'
        values.extend([like, like, like])
    return conditions, values


def people_query(ignored=0, q='', offset=0, limit=48, ids=''):
    ignored = int(bool(ignored))
    needle = (q or '').strip()
    selected = parse_id_list(ids, limit=100, label='人物编号')
    page_limit = min(max(int(limit), 1), 100)
    page_offset = max(int(offset), 0)
    page_where, page_values = people_base_where(ignored, needle)
    extra_where, extra_values = people_base_where(ignored, None)
    select = people_select_sql()
    grouped_page = f"{select} WHERE {' AND '.join(page_where)} GROUP BY p.id"
    grouped_extra = f"{select} WHERE {' AND '.join(extra_where)} GROUP BY p.id"
    extra_sql = None
    extra_params = None
    if selected:
        extra_sql = (
            grouped_extra
            + ' HAVING p.id IN (' + ','.join('?' for _ in selected) + ') '
            + 'ORDER BY p.confirmed DESC, photo_count DESC, p.id'
        )
        extra_params = extra_values + selected
    return {
        'total_sql': f"SELECT count(*) FROM people p WHERE {' AND '.join(page_where)}",
        'total_params': page_values,
        'page_sql': grouped_page + ' ORDER BY p.confirmed DESC, photo_count DESC, p.id LIMIT ? OFFSET ?',
        'page_params': page_values + [page_limit, page_offset],
        'extra_sql': extra_sql,
        'extra_params': extra_params,
        'selected': selected,
        'limit': page_limit,
        'offset': page_offset,
    }


def photo_conditions(q='', filter='all', person='', directory='', max_id=0):
    conditions = ['NOT (' + ACTIVE_ASSET + ')' if filter == 'excluded' else ACTIVE_ASSET]
    conditions.append('EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id)')
    values = []
    year = ''
    place = ''
    group = 0
    current = filter
    if current.startswith('year:'):
        year = current.split(':', 1)[1]
        current = 'all'
    elif current.startswith('place:'):
        place = current.split(':', 1)[1]
        current = 'all'
    elif current.startswith('group:'):
        raw = current.split(':', 1)[1]
        if raw == '10plus':
            group = 10
        else:
            try:
                group = int(raw)
            except ValueError as exc:
                raise ValueError('合影人数无效') from exc
            if group < 2:
                raise ValueError('合影从两人开始')
        current = 'all'
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
        conditions.append('(a.error IS NOT NULL OR a.face_state=-1)')
    if current == 'missing':
        conditions.append('NOT EXISTS(SELECT 1 FROM files f WHERE f.asset_id=a.id AND exists_now=1)')
    if current == 'screenshots':
        conditions.append("a.category IN ('截图','小图 / 素材')")
    if current == 'no_place':
        conditions.append("a.latitude IS NULL AND coalesce(a.manual_place,'')=''")
    if year:
        if year == 'unknown':
            conditions.append("coalesce(a.manual_date,a.captured_at,'')=''")
        else:
            conditions.append("substr(coalesce(a.manual_date,a.captured_at),1,4)=?")
            values.append(year)
    if place:
        if place == 'unknown':
            conditions.append("coalesce(nullif(a.manual_place,''), a.place,'')=''")
        else:
            conditions.append("coalesce(nullif(a.manual_place,''), a.place)=?")
            values.append(place)
    if group:
        if group >= 10:
            conditions.append('a.id IN (SELECT asset_id FROM faces GROUP BY asset_id HAVING count(*)>=10)')
        else:
            conditions.append('a.id IN (SELECT asset_id FROM faces GROUP BY asset_id HAVING count(*)=?)')
            values.append(group)
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



def fetch_people(conn, ignored=0, q='', offset=0, limit=48, ids=''):
    spec = people_query(ignored=ignored, q=q, offset=offset, limit=limit, ids=ids)
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


def photo_from_sql(path_sql, where, order_sql):
    return (
        'SELECT a.id, path, sort_date FROM ('
        'SELECT a.id, coalesce(a.manual_date, a.captured_at) sort_date, ' + path_sql + ' path '
        'FROM assets a WHERE ' + where + ') a ORDER BY ' + order_sql
    )


def fetch_photos(conn, *, q='', filter='all', person='', offset=0, limit=60, directory='', sort='date_desc',
                 sequence=False, max_id=0, around=0, tail=False):
    if sort not in PHOTO_ORDERS:
        raise ValueError('未知的照片排序方式')
    spec = photo_conditions(q=q, filter=filter, person=person, directory=directory, max_id=max_id)
    conn.create_function('file_order', 1, file_order_key)
    conn.execute('BEGIN')
    upper = max_id or conn.execute('SELECT coalesce(max(id),0) FROM assets').fetchone()[0]
    where = spec['where'] + ' AND a.id<=?'
    values = list(spec['values']) + [upper]
    path_values = list(spec['path_values'])
    order_sql = PHOTO_ORDERS[sort]
    query = photo_from_sql(spec['path_sql'], where, order_sql)
    total = conn.execute('SELECT count(*) FROM assets a WHERE ' + where, values).fetchone()[0]
    if sequence:
        window = min(max(int(limit), 1), 400)
        start = max(int(offset), 0)
        if around:
            ranked = (
                'SELECT id, ROW_NUMBER() OVER (ORDER BY ' + order_sql + ') - 1 AS pos FROM ('
                'SELECT a.id, coalesce(a.manual_date, a.captured_at) sort_date, ' + spec['path_sql'] + ' path '
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
        items = [dict(details[r['id']], path=r['path']) for r in rows]
    return {'total': total, 'items': items, 'max_id': upper, 'missing': False}

