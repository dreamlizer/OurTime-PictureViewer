"""Focused live-browser acceptance for the first PNG face-label theme."""
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT_DIR = ROOT / "validation" / "reports"
REPORT = REPORT_DIR / "face-label-ivory.json"


def check(ok, message, checks):
    if not ok:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def request_status(path):
    try:
        with urllib.request.urlopen(URL + path, timeout=10) as response:
            return response.status, response.headers.get_content_type(), len(response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get_content_type(), 0


def choose_group_photo():
    uri = "file:" + str((ROOT / "data" / "library.sqlite3").resolve()) + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute(
            """select f.asset_id,p.name
               from faces f join people p on p.id=f.person_id join assets a on a.id=f.asset_id
               where f.ignored=0 and a.excluded=0 and p.name is not null and trim(p.name)<>''
               and exists(select 1 from files fi where fi.asset_id=a.id and fi.excluded=0 and fi.exists_now=1)
               order by f.asset_id"""
        ).fetchall()
    grouped = {}
    for asset_id, name in rows:
        grouped.setdefault(asset_id, []).append(len("".join(name.split())))
    candidates = [
        (asset_id, lengths)
        for asset_id, lengths in grouped.items()
        if 2 <= len(lengths) <= 4 and 2 in lengths and 3 in lengths
    ]
    if not candidates:
        raise AssertionError("正式库没有找到同时含 2 字和 3 字姓名的 2～4 人照片")
    return max(candidates, key=lambda item: (len(item[1]), -item[0]))


def open_photo(page, asset_id):
    page.goto(URL, wait_until="domcontentloaded")
    page.evaluate("localStorage.removeItem('ourtime.viewer.preferences.v2')")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("typeof openPhoto === 'function'")
    page.evaluate(
        """id => openPhoto(id,{
          q:'',filter:'all',person:'',directory:'',sort:'date_desc',
          date_from:'',date_to:'',place:'',max_id:0
        })""",
        asset_id,
    )
    page.wait_for_function(
        """() => {
          const dialog=document.querySelector('#detail-dialog');
          const image=document.querySelector('#detail-img');
          return dialog?.open && image?.complete && image.naturalWidth>0 &&
            document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)').length>=2;
        }""",
        timeout=30000,
    )


def main():
    checks = []
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    asset_id, name_lengths = choose_group_photo()

    status, content_type, byte_count = request_status("/api/face-label-bg/1.png")
    check(status == 200 and content_type == "image/png" and byte_count > 0, "素笺底图 API 返回 PNG", checks)
    check(request_status("/api/face-label-bg/10.png")[0] == 404, "非白名单 PNG 返回 404", checks)
    check(
        request_status("/api/face-label-bg/%2e%2e%2flibrary.sqlite3")[0] == 404,
        "编码目录穿越不能读取资料库",
        checks,
    )

    page_errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        open_photo(page, asset_id)

        page.evaluate("applyFacePreset('classic')")
        page.wait_for_timeout(150)
        page.screenshot(path=str(REPORT_DIR / "face-label-classic.png"), full_page=False)

        if page.locator("#face-style-popover").is_hidden():
            page.click("#face-style-button")
        page.select_option("#face-theme", "ivory")
        page.wait_for_function(
            """() => document.querySelector('#detail-dialog')?.dataset.faceTheme==='ivory' &&
              [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')]
                .every(label => getComputedStyle(label,'::before').backgroundImage.includes('/api/face-label-bg/'))"""
        )
        page.wait_for_timeout(250)

        theme_names = page.locator("#face-theme option").all_text_contents()
        check(theme_names == ["原始", "素笺", "线框", "暗朱"], "主题名称显示为原始 / 素笺 / 线框 / 暗朱", checks)
        check(page.locator("#toggle-face-dir").is_disabled(), "素笺固定竖排，避免竖牌被横排模式破坏", checks)
        check(
            page.locator("#face-style-popover .face-custom-control:visible").count() == 4
            and page.locator("#face-style-popover .face-style-colors:visible").count() == 1
            and page.locator("#face-style-popover .face-shadow-row:visible").count() == 1,
            "素笺设置保持完整菜单结构",
            checks,
        )
        check(
            all(
                page.locator(selector).is_disabled()
                for selector in (
                    "#face-font-size",
                    "#face-font-family",
                    "#face-text-color",
                    "#face-bg-color",
                    "#face-bg-opacity",
                    "#face-radius",
                    "#face-shadow",
                )
            ),
            "素笺不可修改的样式控件置灰禁用",
            checks,
        )

        labels = page.evaluate(
            """() => [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')].map(label => {
              const rect=label.getBoundingClientRect();
              const style=getComputedStyle(label);
              return {
                count:[...label.textContent.replace(/\\s+/g,'')].length,
                size:label.dataset.faceLabelSize,
                width:Math.round(rect.width),
                height:Math.round(rect.height),
                background:getComputedStyle(label,'::before').backgroundImage,
                backgroundOpacity:getComputedStyle(label,'::before').opacity,
                writingMode:style.writingMode,
                textOrientation:style.textOrientation,
                color:style.color,
                border:style.borderStyle,
                boxShadow:style.boxShadow,
                fontFamily:style.fontFamily,
                paddingLeft:style.paddingLeft,
                paddingRight:style.paddingRight,
                letterSpacing:style.letterSpacing
              };
            })"""
        )
        check(len(labels) >= 2, "真实合影显示至少两个人名素笺", checks)
        check(
            all(
                label["size"] in ("s", "m", "l")
                and "/api/face-label-bg/" in label["background"]
                and label["backgroundOpacity"] == "0.76"
                for label in labels
            ),
            "真实姓名按长度使用固定素笺底图",
            checks,
        )
        check(
            all(label["writingMode"] in ("vertical-rl", "vertical-lr") and label["border"] == "none" for label in labels),
            "素笺为竖排且没有额外边框",
            checks,
        )
        check(
            all(label["boxShadow"] == "none" and "STKaiti" in label["fontFamily"] for label in labels),
            "素笺没有额外阴影并使用本机楷体 fallback 栈",
            checks,
        )
        check(
            all(label["paddingLeft"] == "7px" and label["paddingRight"] == "9px" for label in labels),
            "素笺文字在牌内向左微调 1px",
            checks,
        )
        check(
            all(float(label["letterSpacing"].removesuffix("px")) >= 1.9 for label in labels if label["size"] == "s"),
            "两字姓名增加字间距",
            checks,
        )
        geometry = page.evaluate(
            """() => {
              const image=document.querySelector('#detail-img');
              const layer=document.querySelector('#face-name-layer');
              const imageRect=image.getBoundingClientRect();
              const layerRect=layer.getBoundingClientRect();
              const faces=(state.detail?.faces||[]).filter(face => !face.ignored && face.bbox).map(face => {
                const box=Array.isArray(face.bbox)?face.bbox:JSON.parse(face.bbox);
                const [x1,y1,x2,y2,w,h]=box.map(Number);
                return {
                  x:imageRect.left-layerRect.left+x1/w*imageRect.width,
                  y:imageRect.top-layerRect.top+y1/h*imageRect.height,
                  w:(x2-x1)/w*imageRect.width,
                  h:(y2-y1)/h*imageRect.height
                };
              });
              const labels=[...layer.querySelectorAll('.face-name:not(.unnamed)')].map(label => {
                const rect=label.getBoundingClientRect();
                return {x:rect.left-layerRect.left,y:rect.top-layerRect.top,w:rect.width,h:rect.height};
              });
              const overlap=(a,b) => a.x < b.x+b.w && a.x+a.w > b.x && a.y < b.y+b.h && a.y+a.h > b.y;
              return {
                labelOverlap:labels.some((label,index) => labels.some((other,otherIndex) => index<otherIndex && overlap(label,other))),
                faceOverlap:labels.some(label => faces.some(face => overlap(label,face)))
              };
            }"""
        )
        check(not geometry["labelOverlap"], "真实合影中的素笺互不遮挡", checks)
        check(not geometry["faceOverlap"], "真实合影中的素笺不遮挡人脸", checks)

        synthetic = page.evaluate(
            """() => {
              const layer=document.querySelector('#face-name-layer');
              const values=['小明','欧阳明','欧阳娜娜','欧阳娜娜子'];
              return values.map(value => {
                const label=document.createElement('button');
                label.type='button';
                label.className='face-name';
                label.textContent=value;
                applyFaceLabelProfile(label,value);
                layer.appendChild(label);
                const rect=label.getBoundingClientRect();
                const result={
                  count:[...value].length,
                  size:label.dataset.faceLabelSize,
                  width:Math.round(rect.width),
                  height:Math.round(rect.height),
                  fontSize:getComputedStyle(label).fontSize
                };
                label.remove();
                return result;
              });
            }"""
        )
        expected = [
            {"count": 2, "size": "s", "width": 34, "height": 56, "fontSize": "15px"},
            {"count": 3, "size": "m", "width": 34, "height": 72, "fontSize": "14px"},
            {"count": 4, "size": "l", "width": 34, "height": 86, "fontSize": "13px"},
            {"count": 5, "size": "l", "width": 34, "height": 86, "fontSize": "12px"},
        ]
        check(synthetic == expected, "2 / 3 / 4 / 5 字尺寸和字号符合素笺规则", checks)

        page.screenshot(path=str(REPORT_DIR / "face-label-ivory-settings.png"), full_page=False)
        page.click("#face-style-close")
        page.wait_for_selector("#face-style-popover", state="hidden")
        page.screenshot(path=str(REPORT_DIR / "face-label-ivory.png"), full_page=False)

        fallback_page = browser.new_page(viewport={"width": 1200, "height": 800})
        fallback_page.route(
            "**/api/face-label-bg/**",
            lambda route: route.fulfill(status=404, content_type="text/plain", body="missing test asset"),
        )
        open_photo(fallback_page, asset_id)
        fallback_page.evaluate("applyFacePreset('ivory')")
        fallback_page.wait_for_function(
            "() => document.querySelector('#detail-dialog')?.dataset.faceTheme==='classic'"
        )
        check(
            fallback_page.locator("#face-theme").input_value() == "classic",
            "素笺底图缺失时自动恢复原始主题",
            checks,
        )
        check(
            not fallback_page.locator("#face-font-size").is_disabled()
            and not fallback_page.locator("#face-radius").is_disabled(),
            "恢复原始主题后样式控件重新可用",
            checks,
        )
        fallback_page.close()
        browser.close()

    check(not page_errors, "真实浏览器没有 JavaScript 错误", checks)
    payload = {
        "passed": True,
        "asset_id": asset_id,
        "real_name_lengths": sorted(name_lengths),
        "labels": labels,
        "synthetic_sizes": synthetic,
        "checks": checks,
        "screenshots": ["face-label-classic.png", "face-label-ivory-settings.png", "face-label-ivory.png"],
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("IVORY_LABEL_OK", len(checks), "checks", flush=True)


if __name__ == "__main__":
    main()
