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
    status, content_type, byte_count = request_status(
        "/vendor/fonts/lxgw-wenkai-screen/LXGWWenKaiGBScreen.ttf"
    )
    check(
        status == 200 and content_type in ("font/ttf", "application/octet-stream")
        and byte_count == 26037854,
        "屏幕阅读版文楷由本机项目完整提供",
        checks,
    )
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
        classic_style = page.evaluate(
            """() => {
              const label=document.querySelector('#face-name-layer .face-name:not(.unnamed)');
              const style=getComputedStyle(label);
              return {
                preset:{...viewer.faceStyle},
                fontFamily:style.fontFamily,
                background:style.backgroundColor,
                radius:style.borderRadius,
                paddingLeft:style.paddingLeft,
                paddingRight:style.paddingRight
              };
            }"""
        )
        check(
            classic_style["preset"]["fontFamily"] == "kai"
            and classic_style["preset"]["backgroundOpacity"] == 0.5
            and classic_style["preset"]["radius"] == 10
            and classic_style["preset"]["paddingX"] == 3,
            "默认主题使用楷体、50% 底色、圆角和收窄后的左右留白",
            checks,
        )
        check(
            "KaiTi" in classic_style["fontFamily"]
            and classic_style["background"] == "rgba(20, 24, 18, 0.5)"
            and classic_style["radius"].endswith("px")
            and classic_style["paddingLeft"].endswith("px")
            and classic_style["paddingRight"].endswith("px"),
            f"默认主题的新预设实际应用到照片标签: {classic_style}",
            checks,
        )
        page.screenshot(path=str(REPORT_DIR / "face-label-classic.png"), full_page=False)

        if page.locator("#face-style-popover").is_hidden():
            page.click("#face-style-button")
        check(
            page.evaluate("window.isSecureContext && typeof window.queryLocalFonts==='function'"),
            "本机 Chrome 在拾光 localhost 页面支持本地字体访问",
            checks,
        )
        page.evaluate(
            """() => Object.defineProperty(window,'queryLocalFonts',{
              configurable:true,
              value:async()=>[
                {family:'Microsoft YaHei'},
                {family:'FangSong'},
                {family:'Microsoft YaHei'},
                {family:'KaiTi'}
              ]
            })"""
        )
        page.select_option("#face-font-family", "other")
        page.wait_for_selector("#local-font-dialog", state="visible")
        page.wait_for_function(
            "() => document.querySelectorAll('#local-font-list option').length===3"
        )
        font_names = page.locator("#local-font-list option").all_text_contents()
        check(
            set(font_names) == {"Microsoft YaHei", "FangSong", "KaiTi"},
            "“其他”读取并去重列出本机字体",
            checks,
        )
        page.select_option("#local-font-list", "FangSong")
        page.wait_for_timeout(100)
        check(
            "FangSong" in page.locator("#local-font-preview-label").evaluate(
                "element => getComputedStyle(element).fontFamily"
            )
            and page.locator("#local-font-preview-name").text_content() == "FangSong",
            "选择本机字体后弹窗即时预览标签效果",
            checks,
        )
        page.screenshot(path=str(REPORT_DIR / "face-label-local-font-dialog.png"), full_page=False)
        page.click("#local-font-confirm")
        page.wait_for_selector("#local-font-dialog", state="hidden")
        check(
            not page.locator("#face-style-popover").is_hidden()
            and page.locator("#face-font-family").input_value() == "custom"
            and page.locator("#face-font-family option[value='custom']").text_content() == "其他 · FangSong"
            and page.evaluate(
                """() => {
                  const saved=JSON.parse(localStorage.getItem('ourtime.viewer.preferences.v2')||'{}');
                  return viewer.faceStyle.fontFamily==='custom'
                    && viewer.faceStyle.customFontFamily==='FangSong'
                    && saved.faceStyle?.fontFamily==='custom'
                    && saved.faceStyle?.customFontFamily==='FangSong';
                }"""
            ),
            "确定字体后返回同一个人名标签菜单、应用并保存选择",
            checks,
        )
        page.evaluate("applyFacePreset('classic')")
        page.evaluate(
            """() => {
              document.querySelector('#face-style-popover').dataset.stabilityProbe='same-menu';
            }"""
        )
        menu_geometry = []
        for theme in ("classic", "tea", "accent", "ivory"):
            page.click(f'[data-face-theme-choice="{theme}"]')
            page.wait_for_function(
                f"() => document.querySelector('#face-style-popover')?.dataset.faceTheme==='{theme}'"
            )
            page.wait_for_timeout(100)
            menu_geometry.append(
                page.evaluate(
                    """() => {
                      const menu=document.querySelector('#face-style-popover');
                      const preview=menu.querySelector('.face-style-preview');
                      const rect=menu.getBoundingClientRect();
                      return {
                        theme:menu.dataset.faceTheme,
                        probe:menu.dataset.stabilityProbe,
                        left:Math.round(rect.left*10)/10,
                        top:Math.round(rect.top*10)/10,
                        width:Math.round(rect.width*10)/10,
                        height:Math.round(rect.height*10)/10,
                        previewHeight:Math.round(preview.getBoundingClientRect().height)
                      };
                    }"""
                )
            )
        check(
            all(item["probe"] == "same-menu" for item in menu_geometry),
            "切换主题始终复用同一个设置菜单",
            checks,
        )
        check(
            len({(item["left"], item["top"], item["width"], item["height"]) for item in menu_geometry}) == 1
            and all(item["previewHeight"] <= 104 for item in menu_geometry),
            "四个主题切换时菜单位置和尺寸保持不动且预览区紧凑",
            checks,
        )
        page.click('[data-face-theme-choice="ivory"]')
        page.wait_for_function(
            """() => document.querySelector('#detail-dialog')?.dataset.faceTheme==='ivory' &&
              [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')]
                .every(label => (getComputedStyle(label,'::before').borderImageSource||'').includes('/api/face-label-bg/1.png'))"""
        )
        page.wait_for_function(
            """() => document.fonts.check('15px "LXGW WenKai GB Screen"')"""
        )
        page.wait_for_timeout(250)

        theme_names = page.locator("#face-theme option").all_text_contents()
        check(theme_names == ["默认", "素笺", "茶棕", "暗朱"], "主题名称显示为默认 / 素笺 / 茶棕 / 暗朱", checks)
        check(not page.locator("#toggle-face-dir").is_disabled(), "素笺与顶栏文字方向按钮仍可切换横排", checks)
        check(
            page.locator("#face-style-popover .face-custom-control:visible").count() == 4
            and page.locator("#face-style-popover .face-style-colors:visible").count() == 1
            and page.locator("#face-style-popover .face-shadow-row:visible").count() == 1,
            "素笺设置保持完整菜单结构",
            checks,
        )
        check(
            not page.locator("#face-font-size").is_disabled()
            and not page.locator("#face-bg-opacity").is_disabled(),
            "素笺允许调节字号和底牌透明度",
            checks,
        )
        check(
            all(page.locator(selector).is_disabled() for selector in (
                "#face-font-family",
                "#face-text-color",
                "#face-bg-color",
                "#face-radius",
                "#face-shadow",
            )),
            "素笺其余固定样式控件置灰禁用",
            checks,
        )

        labels = page.evaluate(
            """() => [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')].map(label => {
              const rect=label.getBoundingClientRect();
              const style=getComputedStyle(label);
              return {
                count:[...label.textContent.replace(/\\s+/g,'')].length,
                width:Math.round(rect.width),
                height:Math.round(rect.height),
                plate:getComputedStyle(label,'::before').borderImageSource,
                plateSlice:getComputedStyle(label,'::before').borderImageSlice,
                backgroundOpacity:getComputedStyle(label,'::before').opacity,
                backgroundFilter:getComputedStyle(label,'::before').filter,
                backgroundClip:getComputedStyle(label,'::before').clipPath,
                texture:getComputedStyle(label,'::after').content,
                textureImage:getComputedStyle(label,'::after').backgroundImage,
                writingMode:style.writingMode,
                textOrientation:style.textOrientation,
                color:style.color,
                border:style.borderStyle,
                boxShadow:style.boxShadow,
                fontFamily:style.fontFamily,
                fontSize:style.fontSize,
                left:rect.left,
                paddingLeft:style.paddingLeft,
                paddingRight:style.paddingRight,
                letterSpacing:style.letterSpacing
              };
            })"""
        )
        check(len(labels) >= 2, "真实合影显示至少两个人名素笺", checks)
        check(
            all(
                "/api/face-label-bg/1.png" in label["plate"]
                and "fill" in label["plateSlice"]
                and label["backgroundOpacity"] == "0.76"
                for label in labels
            ),
            "真实姓名共用素笺竖牌，并按文字拉伸中段",
            checks,
        )
        check(
            all(label["writingMode"] in ("vertical-rl", "vertical-lr") and label["border"] == "none" for label in labels),
            "素笺为竖排且没有额外边框",
            checks,
        )
        check(
            all(
                label["boxShadow"] == "none"
                and "LXGW WenKai GB Screen" in label["fontFamily"]
                for label in labels
            ),
            "素笺没有额外阴影并使用内置屏幕阅读版文楷",
            checks,
        )
        cdp = page.context.new_cdp_session(page)
        cdp.send("DOM.enable")
        cdp.send("CSS.enable")
        document_node = cdp.send("DOM.getDocument", {"depth": -1})["root"]["nodeId"]
        label_node = cdp.send(
            "DOM.querySelector",
            {
                "nodeId": document_node,
                "selector": "#face-name-layer .face-name:not(.unnamed)",
            },
        )["nodeId"]
        platform_fonts = cdp.send(
            "CSS.getPlatformFontsForNode", {"nodeId": label_node}
        )["fonts"]
        check(
            any(
                font["familyName"] == "LXGW WenKai GB Screen"
                and font["isCustomFont"]
                for font in platform_fonts
            ),
            "Chrome 实际使用项目内置文楷而不是系统回退字体",
            checks,
        )
        check(
            all(label["color"] == "rgb(90, 47, 40)" for label in labels),
            "素笺文字使用清晰但克制的深赭红",
            checks,
        )
        check(
            all(
                label["paddingLeft"].endswith("px")
                and label["paddingRight"].endswith("px")
                and abs(float(label["paddingLeft"].removesuffix("px")) - float(label["paddingRight"].removesuffix("px"))) <= 0.1
                for label in labels
            ),
            "素笺左右留白随同一缩放系数保持对称",
            checks,
        )
        check(
            all(label["letterSpacing"] == "normal" or float(label["letterSpacing"].removesuffix("px")) > 0 for label in labels),
            "素笺竖排保留统一字距",
            checks,
        )
        check(
            all(
                label["backgroundFilter"] == "none"
                and label["backgroundClip"] == "none"
                and label["texture"] == "none"
                and label["textureImage"] == "none"
                for label in labels
            ),
            "素笺只保留 PNG 单轮廓和角花，不再叠加纸纹或内框",
            checks,
        )

        page.locator("#face-font-size").evaluate(
            """control => {
              control.value='17';
              control.dispatchEvent(new Event('input',{bubbles:true}));
            }"""
        )
        page.wait_for_timeout(150)
        resized_labels = page.evaluate(
            """() => [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')].map(label => {
              const rect=label.getBoundingClientRect();
              return {
                count:[...label.textContent.replace(/\\s+/g,'')].length,
                width:Math.round(rect.width),
                left:rect.left,
                fontSize:getComputedStyle(label).fontSize,
                paddingLeft:getComputedStyle(label).paddingLeft,
                paddingRight:getComputedStyle(label).paddingRight
              };
            })"""
        )
        check(
            all(
                label["fontSize"].endswith("px")
                and abs(float(label["fontSize"].removesuffix("px")) - 17 * float(labels[index]["fontSize"].removesuffix("px")) / 15) < 0.8
                for index, label in enumerate(resized_labels)
            ),
            "字号滑杆按同一倍率调整实际文字",
            checks,
        )
        check(
            all(
                label["width"] >= labels[index]["width"]
                and label["paddingLeft"] == labels[index]["paddingLeft"]
                and label["paddingRight"] == labels[index]["paddingRight"]
                for index, label in enumerate(resized_labels)
            ),
            "字号改变后底牌随文字伸缩，留白仍锁定同一缩放系数",
            checks,
        )

        page.locator("#face-bg-opacity").evaluate(
            """control => {
              control.value='55';
              control.dispatchEvent(new Event('input',{bubbles:true}));
            }"""
        )
        page.wait_for_timeout(100)
        check(
            page.evaluate(
                """() => [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')]
                  .every(label => getComputedStyle(label,'::before').opacity === '0.55')"""
            ),
            "底牌透明度滑杆实时改变 PNG 透明度",
            checks,
        )
        check(
            page.locator("#face-font-size-value").text_content() == "17px"
            and page.locator("#face-bg-opacity-value").text_content() == "55%",
            "字号和透明度数值提示同步更新",
            checks,
        )

        page.locator("#face-font-size").evaluate(
            """control => {
              control.value='15';
              control.dispatchEvent(new Event('input',{bubbles:true}));
            }"""
        )
        page.locator("#face-bg-opacity").evaluate(
            """control => {
              control.value='76';
              control.dispatchEvent(new Event('input',{bubbles:true}));
            }"""
        )
        page.wait_for_timeout(150)
        check(
            page.evaluate(
                """() => [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')]
                  .every(label => getComputedStyle(label,'::before').opacity === '0.76')"""
            ),
            "恢复默认字号和透明度后样式正确",
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
                  width:Math.round(rect.width),
                  height:Math.round(rect.height),
                  fontSize:getComputedStyle(label).fontSize
                };
                label.remove();
                return result;
              });
            }"""
        )
        check(
            [item["count"] for item in synthetic] == [2, 3, 4, 5]
            and all(item["height"] > 0 for item in synthetic),
            "2 / 3 / 4 / 5 字姓名共用同一张素笺竖牌",
            checks,
        )
        check(
            all(synthetic[index]["height"] < synthetic[index + 1]["height"] for index in range(3))
            and all(item["width"] > 0 and item["height"] > item["width"] for item in synthetic),
            "素笺竖牌包住文字并随姓名长度递增",
            checks,
        )

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
            "素笺底图缺失时自动恢复默认主题",
            checks,
        )
        check(
            not fallback_page.locator("#face-font-size").is_disabled()
            and not fallback_page.locator("#face-radius").is_disabled(),
            "恢复默认主题后样式控件重新可用",
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
        "platform_fonts": platform_fonts,
        "synthetic_sizes": synthetic,
        "checks": checks,
        "screenshots": [
            "face-label-classic.png",
            "face-label-local-font-dialog.png",
            "face-label-ivory-settings.png",
            "face-label-ivory.png",
        ],
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("IVORY_LABEL_OK", len(checks), "checks", flush=True)


if __name__ == "__main__":
    main()
