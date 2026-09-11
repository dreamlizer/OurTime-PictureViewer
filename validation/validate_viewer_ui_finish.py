"""Focused real-browser checks for the small viewer UI closeout."""
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8765'
REPORT = ROOT / 'validation' / 'reports' / 'viewer-ui-finish.json'
checks = []


def req(path):
    with urllib.request.urlopen(URL + path, timeout=60) as response:
        return json.load(response)


def check(ok, text):
    if not ok:
        raise AssertionError(text)
    checks.append(text)
    print('PASS', text, flush=True)


def sample_ids():
    with sqlite3.connect('file:' + str(ROOT / 'data' / 'library.sqlite3') + '?mode=ro', uri=True) as db:
        group = db.execute(
            '''select a.id from assets a join faces f on f.asset_id=a.id
               join files fi on fi.asset_id=a.id
               where a.excluded=0 and fi.excluded=0 and fi.exists_now=1 and f.ignored=0
               group by a.id having count(f.id) between 3 and 6 order by count(f.id) desc, a.id limit 1'''
        ).fetchone()[0]
        partial = db.execute(
            '''select a.id from assets a join files fi on fi.asset_id=a.id
               where a.excluded=0 and a.camera is not null and a.place is null
               and fi.excluded=0 and fi.exists_now=1 order by a.id limit 1'''
        ).fetchone()[0]
        screenshot = db.execute(
            '''select a.id from assets a join files fi on fi.asset_id=a.id
               where a.excluded=0 and a.camera is null and a.place is null
               and a.format='PNG' and fi.excluded=0 and fi.exists_now=1
               order by a.id limit 1'''
        ).fetchone()[0]
    return {'group': group, 'single': 10, 'partial': partial, 'screenshot': screenshot}


def open_photo(page, asset_id):
    detail = req('/api/photos/' + str(asset_id))
    name = Path(detail['files'][0]['path']).name
    page.goto(URL)
    search = page.locator('[data-ot="search"]')
    if not search.is_visible():
        search = page.locator('#search')
    search.fill(name)
    search.press('Enter')
    page.wait_for_selector(f'[data-photo="{asset_id}"]', timeout=30000)
    page.locator(f'[data-photo="{asset_id}"]').dblclick(timeout=30000)
    page.wait_for_function(
        "document.querySelector('#detail-img').complete && document.querySelector('#detail-img').naturalWidth>0"
    )
    return detail


def open_style(page):
    if page.locator('#face-style-popover').is_hidden():
        page.click('#face-style-button')
    page.wait_for_selector('#face-style-popover:not([hidden])')


def face_geometry(page, faces):
    return page.evaluate(
        '''(faces) => {
          const img = document.querySelector('#detail-img');
          const layer = document.querySelector('#face-name-layer');
          const ir = img.getBoundingClientRect();
          const lr = layer.getBoundingClientRect();
          const boxes = new Map();
          for (const face of faces) {
            let b = face.bbox;
            if (typeof b === 'string') b = JSON.parse(b);
            const [x1,y1,x2,y2,w,h] = b.map(Number);
            boxes.set(String(face.person_id), {
              x: ir.left-lr.left+x1/w*ir.width,
              y: ir.top-lr.top+y1/h*ir.height,
              w: (x2-x1)/w*ir.width,
              h: (y2-y1)/h*ir.height,
            });
          }
          const overlap = (a,b) => a.x < b.x+b.w && a.x+a.w > b.x && a.y < b.y+b.h && a.y+a.h > b.y;
          const labels = [...layer.querySelectorAll('.face-name')].map(button => {
            const r = button.getBoundingClientRect();
            const box = {x:r.left-lr.left,y:r.top-lr.top,w:r.width,h:r.height};
            return {personId:button.dataset.facePerson, side:[...button.classList].find(x => ['left','right','top','bottom'].includes(x)) || '', title:button.title, text:button.textContent.trim(), box};
          });
          const otherFaceOverlap = labels.some(label => [...boxes.entries()].some(([id, box]) => id !== label.personId && overlap(label.box, box)));
          const ownPlacement = labels.every(label => {
            const face = boxes.get(label.personId);
            if (!face || !['left','right'].includes(label.side)) return false;
            const center = label.box.y + label.box.h/2;
            const faceCenter = face.y + face.h/2;
            const centered = Math.abs(center-faceCenter) <= Math.max(12, face.h*.18);
            const outside = label.side === 'left' ? label.box.x+label.box.w <= face.x+1 : label.box.x >= face.x+face.w-1;
            return centered && outside;
          });
          return {labels, otherFaceOverlap, ownPlacement, sides:[...new Set(labels.map(x=>x.side))]};
        }''',
        faces,
    )


def main():
    ids = sample_ids()
    status_before = req('/api/status')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        detail = open_photo(page, ids['group'])
        geometry = face_geometry(page, detail.get('faces', []))
        check(len(geometry['labels']) >= 3, '真实三人以上合影显示姓名标签')
        check(len(geometry['sides']) <= 2 and max(sum(label['side'] == side for label in geometry['labels']) for side in ('left', 'right')) >= 2,
              '多人照片优先保持统一左右侧')
        check(not geometry['otherFaceOverlap'], '姓名标签不覆盖其他人物脸框')
        check(geometry['ownPlacement'], '竖排姓名紧邻脸框外沿并相对自身脸框垂直居中')
        unnamed = [label for label in geometry['labels'] if label['title'] == '命名人物']
        check(not unnamed or all(label['text'] == '+' for label in unnamed), '未命名人物使用低权重命名入口')
        active_after = page.evaluate("getComputedStyle(document.querySelector('#toggle-face-names'),'::after').content")
        check(active_after in ('none', 'normal'), '激活工具不再显示重复绿色圆点')

        open_style(page)
        named = next((face['name'] for face in detail.get('faces', []) if face.get('name') and not face.get('ignored')), None)
        check(page.locator('#face-style-preview-label').inner_text() == (named or '示例姓名'), '人名样式预览使用当前照片真实姓名或示例姓名')
        check(page.locator('#face-label-position').input_value() == 'auto', '标签位置默认自动')
        check(page.locator('#face-label-position option[value="left"]').inner_text() == '优先左侧' and page.locator('#face-label-position option[value="right"]').inner_text() == '优先右侧', '标签位置文案符合安全 fallback 语义')
        check(page.locator('#face-style-preview-label').evaluate('(e)=>getComputedStyle(e).writingMode') in ('vertical-rl', 'vertical-lr'), '竖排状态下样式预览为竖排')
        page.click('#toggle-face-dir')
        check(page.locator('#face-style-preview-label').evaluate('(e)=>getComputedStyle(e).writingMode') == 'horizontal-tb', '切换横排时打开的样式预览立即刷新')
        page.click('#toggle-face-dir')
        for value in ('left', 'right', 'auto'):
            page.select_option('#face-label-position', value)
            check(page.evaluate("JSON.parse(localStorage.getItem('ourtime.viewer.preferences.v2')).faceLabelPosition") == value,
                  f'标签位置 {value} 写入 viewer preferences')
            page.reload()
            open_photo(page, ids['single'])
            open_style(page)
            check(page.locator('#face-label-position').input_value() == value, f'标签位置 {value} 重新打开后仍保存')
        page.select_option('#face-label-position', 'right')
        page.click('#face-style-reset')
        check(page.locator('#face-label-position').input_value() == 'auto' and page.evaluate("JSON.parse(localStorage.getItem('ourtime.viewer.preferences.v2')).faceLabelPosition") == 'auto', '恢复默认同时把标签位置恢复为 auto')

        for label, asset_id in (('单人照', ids['single']), ('部分 EXIF', ids['partial']), ('近乎无 EXIF 截图', ids['screenshot'])):
            open_photo(page, asset_id)
            check(page.locator('#signature-primary').count() == 1 and page.locator('#signature-settings').count() == 1 and page.locator('#signature-format').count() == 1,
                  f'{label} 使用稳定 metadata 语义槽')
            check(page.locator('#photo-signature').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'), f'{label} 底栏不横向溢出')

        synthetic = page.evaluate(
            '''() => {
              const read = () => ({
                primary: document.querySelector('#signature-primary-value').textContent,
                settings: document.querySelector('#signature-settings').textContent,
                format: document.querySelector('#signature-format').textContent,
                formatHidden: document.querySelector('#signature-format').hidden,
                text: document.querySelector('#photo-signature').textContent,
              });
              renderSignature({effective_date:'',effective_place:'',camera:'MockCam',format:'PNG',width:800,height:600,metadata:{ExifTool:{}}},{path:'mock.png',size:2048});
              const capture = read();
              renderSignature({effective_date:'',effective_place:'',camera:'',format:'',width:800,height:600,metadata:{},files:[]},{path:'screenshot.png',size:2048});
              const filename = read();
              renderSignature({effective_date:'2020-01-02T03:04:05',effective_place:'测试地点',camera:'',format:'JPEG',width:800,height:600,metadata:{ExifTool:{Make:'Mock',Model:'Camera',ISO:100}}},{path:'memory.jpg',size:2048});
              return {capture,filename,memory:read()};
            }'''
        )
        check(synthetic['capture']['primary'] == 'MockCam' and synthetic['capture']['formatHidden'], '无时间地点但有设备时基础信息只进次区')
        check(synthetic['filename']['primary'] == 'screenshot.png' and synthetic['filename']['formatHidden'], '无时间地点和设备时主区显示文件名、次区显示基础信息')
        check(synthetic['memory']['text'].count('JPEG') == 1 and synthetic['memory']['text'].count('Mock') == 1, '有时间地点时设备和文件信息各显示一次')

        page.set_viewport_size({'width': 390, 'height': 844})
        open_photo(page, ids['screenshot'])
        check(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), '390px 窄屏无横向溢出')
        check(not errors, '真实浏览器无 JavaScript 错误')
        browser.close()

    status_after = req('/api/status')
    check(status_before['capabilities']['pid'] == status_after['capabilities']['pid'], '验证未重启本地后台')
    report = {'passed': True, 'ids': ids, 'checks': checks, 'time': time.strftime('%Y-%m-%dT%H:%M:%S')}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
