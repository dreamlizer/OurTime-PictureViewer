"""Isolated browser/API checks for person merge and duplicate-name handling."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = ROOT / 'validation' / 'work'
REPORT = ROOT / 'validation' / 'reports' / 'people-merge-naming-20260911.json'
URL = 'http://127.0.0.1:8782'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def wait_server():
    for _ in range(80):
        try:
            with urllib.request.urlopen(URL + '/api/status', timeout=1):
                return
        except Exception:
            time.sleep(.25)
    raise RuntimeError('isolated server did not start')


def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode('utf-8'))


def add_person(app, data, name=None, alias='', confirmed=0):
    with app.db() as c:
        pid = c.execute(
            'INSERT INTO people(name,alias,confirmed,ignored) VALUES (?,?,?,0)',
            (name, alias, confirmed),
        ).lastrowid
        sha = hashlib.sha256(f'person-check-{pid}'.encode()).hexdigest()
        aid = c.execute(
            'INSERT INTO assets(sha256,metadata,created_at,width,height,format) VALUES (?,?,?,?,?,?)',
            (sha, '{}', app.now(), 100, 100, 'JPEG'),
        ).lastrowid
        c.execute(
            'INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded) VALUES (?,?,?,?,?,?,?)',
            (aid, str(data / f'person-{pid}.jpg'), 100, 0, app.now(), 1, 0),
        )
        c.execute(
            'INSERT INTO faces(asset_id,person_id,bbox,embedding,score) VALUES (?,?,?,?,?)',
            (aid, pid, '[0,0,100,100,100,100]', b'check', .9),
        )
    return pid


def check(condition, message, results):
    if not condition:
        raise AssertionError(message)
    results.append(message)


def main():
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix='people-merge-naming-', dir=WORK_ROOT))
    os.environ['PHOTO_LIBRARY_DATA'] = str(data)
    os.environ['PHOTO_WEB_ROOT'] = str((ROOT / 'web').resolve())
    from validation.repair_checks import import_app

    app = import_app(data)
    target = add_person(app, data, '目标人物', '目标', 1)
    detail_source = add_person(app, data)
    batch_source = add_person(app, data)
    duplicate_target = add_person(app, data, '重名测试', '小石', 1)
    quick_section_source = add_person(app, data)
    quick_separate = add_person(app, data)
    quick_merge = add_person(app, data)
    different_alias = add_person(app, data)
    detail_name_source = add_person(app, data)
    no_alias_target = add_person(app, data, '无别名重名', '', 1)
    no_alias_source = add_person(app, data)
    backend_error_source = add_person(app, data)
    refresh_error_source = add_person(app, data)

    env = os.environ.copy()
    server = subprocess.Popen(
        [sys.executable, 'app.py', '--port', '8782'],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results = []
    page_errors = []
    try:
        wait_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1400, 'height': 900}, locale='zh-CN')
            page.on('pageerror', lambda error: page_errors.append(str(error)))

            def open_people():
                page.goto(URL, wait_until='domcontentloaded', timeout=20000)
                page.locator('[data-view="people"]').click()
                page.wait_for_selector('#people-grid .person-card', timeout=15000)

            open_people()
            page.locator(f'#people-grid [data-person="{detail_source}"]').click()
            page.wait_for_selector('#person-dialog[open]')
            page.select_option('#merge-target', str(target))
            page.locator('#merge-person').click()
            page.wait_for_function(f"document.querySelector('#person-title').textContent.includes('目标人物')")
            status, _ = fetch_json(URL + f'/api/people/{detail_source}')
            _, target_detail = fetch_json(URL + f'/api/people/{target}')
            check(status == 404, '详情合并后 source 人物已删除', results)
            check(target_detail['face_count'] == 2 and target_detail['photo_count'] == 2, '详情合并后 target 人脸和照片数量正确', results)

            open_people()
            page.locator('#people-merge-toggle').click()
            page.wait_for_selector('[data-select-person]')
            page.locator(f'[data-select-person="{target}"]').click()
            page.locator(f'[data-select-person="{batch_source}"]').click()
            page.wait_for_function("!document.querySelector('#merge-selected-people').disabled")
            page.locator('#merge-selected-people').click()
            page.wait_for_timeout(700)
            status, _ = fetch_json(URL + f'/api/people/{batch_source}')
            _, target_detail = fetch_json(URL + f'/api/people/{target}')
            check(status == 404, '批量合并后 source 人物已删除', results)
            check(target_detail['face_count'] == 3 and target_detail['photo_count'] == 3, '批量合并后 target 数量正确', results)
            check(not any('querySelector' in error for error in page_errors), '详情和批量合并没有非法 querySelector 错误', results)

            open_people()
            page.locator(f'[data-quick-name="{quick_section_source}"]').click()
            page.wait_for_selector('#quick-name-dialog[open]')
            check(page.locator('#quick-merge-panel').is_hidden(), '快速命名合并区默认收起且仍在同一对话框', results)
            page.locator('#quick-merge-toggle').click()
            page.wait_for_selector('#quick-merge-panel:not([hidden])')
            page.fill('#quick-merge-search', '重名测试')
            page.wait_for_function(f"Array.from(document.querySelectorAll('#quick-merge-target option')).some(o => o.value === '{duplicate_target}')")
            check(not page.locator(f'#quick-merge-target option[value="{quick_section_source}"]').count(), '快速合并目标排除当前人物', results)
            page.locator('[data-close="quick-name-dialog"]').last.click()
            page.wait_for_timeout(100)
            page.locator(f'[data-quick-name="{quick_section_source}"]').click()
            page.wait_for_selector('#quick-name-dialog[open]')
            check(page.locator('#quick-merge-panel').is_hidden() and page.locator('#quick-merge-search').input_value() == '', '快速命名关闭后合并草稿已重置', results)
            page.locator('#quick-merge-toggle').click()
            page.fill('#quick-merge-search', '重名测试')
            page.wait_for_function(f"Array.from(document.querySelectorAll('#quick-merge-target option')).some(o => o.value === '{duplicate_target}')")
            page.select_option('#quick-merge-target', str(duplicate_target))
            page.locator('#quick-merge-submit').click()
            page.wait_for_function("!document.querySelector('#quick-name-dialog').open")
            status, _ = fetch_json(URL + f'/api/people/{quick_section_source}')
            _, duplicate_detail = fetch_json(URL + f'/api/people/{duplicate_target}')
            check(status == 404, '快速命名合并区调用现有 merge 后 source 已删除', results)
            check(duplicate_detail['face_count'] == 2 and page.locator('#quick-name-dialog[open]').count() == 0, '快速命名合并成功后目标收齐 faces 且对话框关闭', results)

            open_people()
            page.locator(f'[data-quick-name="{quick_separate}"]').click()
            page.fill('#quick-name-input', '  重名测试  ')
            page.fill('#quick-alias-input', '  小石  ')
            page.locator('#quick-name-form button[type="submit"]').click()
            page.wait_for_selector('#person-name-conflict-dialog[open]')
            check('可能是同一个人' in page.locator('#person-name-conflict-dialog').inner_text(), '快速命名完全重名时显示确认', results)
            page.locator('#person-conflict-separate').click()
            page.wait_for_timeout(300)
            _, separated = fetch_json(URL + f'/api/people/{quick_separate}')
            check(separated['name'] == '重名测试' and separated['alias'] == '小石', '选择仍分开后确实保存为不同人物', results)

            open_people()
            page.locator(f'[data-quick-name="{quick_merge}"]').click()
            page.fill('#quick-name-input', '重名测试')
            page.fill('#quick-alias-input', '小石')
            page.locator('#quick-name-form button[type="submit"]').click()
            page.wait_for_selector('#person-name-conflict-dialog[open]')
            page.locator('#person-conflict-merge').click()
            page.wait_for_timeout(500)
            status, _ = fetch_json(URL + f'/api/people/{quick_merge}')
            _, duplicate_detail = fetch_json(URL + f'/api/people/{duplicate_target}')
            check(status == 404, '选择合并后当前人物 source 已删除', results)
            check(duplicate_detail['face_count'] == 3, '选择合并后目标人物收齐 faces', results)

            open_people()
            page.locator(f'[data-quick-name="{different_alias}"]').click()
            page.fill('#quick-name-input', '  重名测试  ')
            page.fill('#quick-alias-input', '  另一个  ')
            page.locator('#quick-name-form button[type="submit"]').click()
            page.wait_for_timeout(350)
            check(page.locator('#person-name-conflict-dialog[open]').count() == 0, '同名不同 alias 不阻止保存且不进入合并确认', results)
            _, different = fetch_json(URL + f'/api/people/{different_alias}')
            check(different['name'] == '重名测试' and different['alias'] == '另一个', '同名不同 alias 保存成功', results)

            open_people()
            page.locator(f'[data-quick-name="{no_alias_source}"]').click()
            page.fill('#quick-name-input', '无别名重名')
            page.fill('#quick-alias-input', '')
            page.locator('#quick-name-form button[type="submit"]').click()
            page.wait_for_selector('#person-name-conflict-dialog[open]')
            page.locator('#person-conflict-separate').click()
            page.wait_for_timeout(300)
            _, no_alias = fetch_json(URL + f'/api/people/{no_alias_source}')
            check(no_alias['name'] == '无别名重名' and no_alias['alias'] == '', '同名且双方无 alias 时要求确认并可分开保存', results)

            open_people()
            page.locator(f'#people-grid [data-person="{detail_name_source}"]').click()
            page.wait_for_selector('#person-dialog[open]')
            page.fill('#person-name', '重名测试')
            page.fill('#person-alias', '小石')
            page.locator('#person-form button[type="submit"]').click()
            page.wait_for_selector('#person-name-conflict-dialog[open]')
            page.locator('#person-conflict-cancel').click()
            page.wait_for_timeout(250)
            _, unchanged = fetch_json(URL + f'/api/people/{detail_name_source}')
            check(unchanged['name'] is None, '人物详情命名与快速命名共用重名确认，取消不保存', results)

            open_people()
            page.locator(f'#people-grid [data-person="{backend_error_source}"]').click()
            page.wait_for_selector('#person-dialog[open]')
            page.select_option('#merge-target', str(target))
            def backend_error(route):
                if route.request.method == 'POST' and route.request.url.endswith('/merge'):
                    route.fulfill(status=400, content_type='application/json', body=json.dumps({'detail': '隔离后端错误'}))
                else:
                    route.continue_()
            page.route('**/api/people/**', backend_error)
            page.locator('#merge-person').click()
            page.wait_for_function("document.querySelector('#person-notice').textContent.includes('隔离后端错误')")
            check('隔离后端错误' in page.locator('#person-notice').inner_text(), '后端 merge 失败时展示真实后端错误', results)
            page.unroute('**/api/people/**', backend_error)

            page.reload(wait_until='domcontentloaded')
            page.locator('[data-view="people"]').click()
            page.wait_for_selector(f'#people-grid [data-person="{refresh_error_source}"]')
            page.locator(f'#people-grid [data-person="{refresh_error_source}"]').click()
            page.wait_for_selector('#person-dialog[open]')
            page.select_option('#merge-target', str(target))
            def success_refresh_error(route):
                if route.request.method == 'POST' and route.request.url.endswith('/merge'):
                    route.fulfill(status=200, content_type='application/json', body=json.dumps({'ok': True}))
                elif route.request.method == 'GET' and route.request.url.endswith(f'/api/people/{target}?limit=48'):
                    route.fulfill(status=500, content_type='application/json', body=json.dumps({'detail': '隔离刷新错误'}))
                else:
                    route.continue_()
            page.route('**/api/people/**', success_refresh_error)
            page.locator('#merge-person').click()
            page.wait_for_function("document.querySelector('#person-notice').textContent.includes('人物已合并')")
            warning = page.locator('#person-notice').inner_text()
            check('人物已合并' in warning and '合并失败' not in warning, 'API 成功但 UI 刷新异常时不误报 merge 失败', results)
            page.unroute('**/api/people/**', success_refresh_error)
            check(not page_errors, '人物 merge/naming 隔离浏览器无 JavaScript 错误', results)
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'passed': True, 'checks': results, 'work': str(data)}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'checks': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
