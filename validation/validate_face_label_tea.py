"""Focused live-browser acceptance for the tea-brown PNG face-label theme."""
import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from validate_face_label_ivory import (
    REPORT_DIR,
    URL,
    check,
    choose_group_photo,
    open_photo,
    request_status,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT = REPORT_DIR / "face-label-tea.json"
SOURCE_DIR = ROOT / "data" / "人名标签"


def image_evidence():
    evidence = []
    for filename in ("4.png",):
        with Image.open(SOURCE_DIR / filename).convert("RGBA") as image:
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            evidence.append(
                {
                    "filename": filename,
                    "size": list(image.size),
                    "alpha_extrema": list(alpha.getextrema()),
                    "bbox": list(bbox),
                    "effective_height": bbox[3] - bbox[1],
                }
            )
    return evidence


def main():
    checks = []
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    asset_id, name_lengths = choose_group_photo()

    images = image_evidence()
    check(
        images[0]["effective_height"] == 1681 and images[0]["alpha_extrema"] == [0, 255],
        "茶棕竖牌 4.png 保留完整透明通道",
        checks,
    )
    for filename in ("4.png",):
        status, content_type, byte_count = request_status(f"/api/face-label-bg/{filename}")
        check(
            status == 200 and content_type == "image/png" and byte_count > 0,
            f"茶棕底图 {filename} API 返回 PNG",
            checks,
        )

    page_errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        open_photo(page, asset_id)
        page.evaluate("applyFacePreset('tea')")
        page.wait_for_function(
            """() => document.querySelector('#detail-dialog')?.dataset.faceTheme==='tea' &&
              [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')]
                .every(label => (getComputedStyle(label,'::before').borderImageSource||'').includes('/api/face-label-bg/4.png'))"""
        )
        page.wait_for_function("""() => document.fonts.check('16px "Ma Shan Zheng"')""")
        page.wait_for_timeout(250)

        if page.locator("#face-style-popover").is_hidden():
            page.click("#face-style-button")
        check(page.locator("#face-theme").input_value() == "tea", "第三主题显示并选中茶棕", checks)
        check(not page.locator("#toggle-face-dir").is_disabled(), "茶棕与顶栏文字方向按钮仍可切换横排", checks)
        check(
            not page.locator("#face-font-size").is_disabled()
            and not page.locator("#face-bg-opacity").is_disabled()
            and not page.locator("#face-font-family").is_disabled(),
            "茶棕允许调节字号、毛笔字体和底牌透明度",
            checks,
        )
        check(
            all(
                page.locator(selector).is_disabled()
                for selector in (
                    "#face-text-color",
                    "#face-bg-color",
                    "#face-radius",
                    "#face-shadow",
                )
            ),
            "茶棕其余固定样式控件在同一菜单中置灰",
            checks,
        )
        font_options = page.locator("#face-font-family option").evaluate_all(
            """options => options.map(option => ({value:option.value,text:option.textContent}))"""
        )
        check(
            [option["value"] for option in font_options]
            == ["ma-shan-zheng", "long-cang", "liu-jian-mao-cao"]
            and page.locator("#face-font-family").input_value() == "ma-shan-zheng",
            "茶棕字体只提供三款内置毛笔字体并默认 Ma Shan Zheng",
            checks,
        )

        labels = page.evaluate(
            """() => [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')].map(label => {
              const rect=label.getBoundingClientRect();
              const style=getComputedStyle(label);
              const plate=getComputedStyle(label,'::before');
              const after=getComputedStyle(label,'::after');
              return {
                count:[...label.textContent.replace(/\\s+/g,'')].length,
                width:Math.round(rect.width),
                height:Math.round(rect.height),
                left:rect.left,
                plate:plate.borderImageSource,
                plateSlice:plate.borderImageSlice,
                backgroundOpacity:plate.opacity,
                backgroundFilter:plate.filter,
                backgroundClip:plate.clipPath,
                afterContent:after.content,
                color:style.color,
                border:style.borderStyle,
                boxShadow:style.boxShadow,
                textShadow:style.textShadow,
                fontFamily:style.fontFamily,
                fontSize:style.fontSize,
                paddingLeft:style.paddingLeft,
                paddingRight:style.paddingRight,
                letterSpacing:style.letterSpacing
              };
            })"""
        )
        check(len(labels) >= 2, "真实合影显示至少两个人名茶棕标签", checks)
        check(
            all(
                "/api/face-label-bg/4.png" in label["plate"]
                and "fill" in label["plateSlice"]
                for label in labels
            ),
            "茶棕姓名共用竖牌，并按文字拉伸中段",
            checks,
        )
        check(
            all(label["backgroundOpacity"] == "0.66" for label in labels),
            "茶棕默认底牌透明度降为 66%",
            checks,
        )
        check(
            all(
                label["color"] == "rgb(247, 234, 216)"
                and "Ma Shan Zheng" in label["fontFamily"]
                and label["border"] == "none"
                and label["boxShadow"] == "none"
                and label["textShadow"] == "none"
                for label in labels
            ),
            "茶棕使用暖象牙色 Ma Shan Zheng 且无额外边框、阴影",
            checks,
        )
        check(
            all(
                label["backgroundFilter"] == "none"
                and label["backgroundClip"] == "none"
                and label["afterContent"] == "none"
                for label in labels
            ),
            "茶棕完整保留 PNG 单轮廓与复古角花，不叠加第二层 CSS 内框",
            checks,
        )
        check(
            all(
                label["paddingLeft"].endswith("px")
                and label["paddingRight"].endswith("px")
                and abs(float(label["paddingLeft"].removesuffix("px")) - float(label["paddingRight"].removesuffix("px"))) <= 0.1
                for label in labels
            ),
            "茶棕左右留白随同一缩放系数保持对称",
            checks,
        )
        check(
            all(label["letterSpacing"] == "normal" or float(label["letterSpacing"].removesuffix("px")) > 0 for label in labels),
            "茶棕竖排保留统一字距",
            checks,
        )

        synthetic = page.evaluate(
            """() => {
              const layer=document.querySelector('#face-name-layer');
              return ['小明','欧阳明','欧阳娜娜','欧阳娜娜子'].map(value => {
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
                  fontSize:getComputedStyle(label).fontSize,
                  plate:(getComputedStyle(label,'::before').borderImageSource||'')
                };
                label.remove();
                return result;
              });
            }"""
        )
        check(
            [item["count"] for item in synthetic] == [2, 3, 4, 5]
            and all("/api/face-label-bg/4.png" in item["plate"] for item in synthetic),
            "茶棕 2 / 3 / 4 / 5 字姓名共用同一张竖牌",
            checks,
        )
        check(
            all(synthetic[index]["height"] < synthetic[index + 1]["height"] for index in range(3))
            and all(item["width"] > 0 and item["height"] > item["width"] for item in synthetic),
            "茶棕竖牌包住文字并随姓名长度递增",
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
        check(not geometry["labelOverlap"], "真实合影中的茶棕标签互不遮挡", checks)
        check(not geometry["faceOverlap"], "真实合影中的茶棕标签不遮挡人脸", checks)

        page.locator("#face-font-size").evaluate(
            """control => {
              control.value='17';
              control.dispatchEvent(new Event('input',{bubbles:true}));
            }"""
        )
        page.locator("#face-bg-opacity").evaluate(
            """control => {
              control.value='60';
              control.dispatchEvent(new Event('input',{bubbles:true}));
            }"""
        )
        page.wait_for_timeout(150)
        adjusted = page.evaluate(
            """() => [...document.querySelectorAll('#face-name-layer .face-name:not(.unnamed)')].map(label => ({
              count:[...label.textContent.replace(/\\s+/g,'')].length,
              width:Math.round(label.getBoundingClientRect().width),
              fontSize:getComputedStyle(label).fontSize,
              opacity:getComputedStyle(label,'::before').opacity
            }))"""
        )
        check(
            all(item["width"] > 0 and item["opacity"] == "0.6" for item in adjusted),
            "茶棕调节字号和透明度时底牌继续包住文字并实时变淡",
            checks,
        )

        for value, family in (
            ("long-cang", "Long Cang"),
            ("liu-jian-mao-cao", "Liu Jian Mao Cao"),
            ("ma-shan-zheng", "Ma Shan Zheng"),
        ):
            page.locator("#face-font-family").select_option(value)
            page.wait_for_function(
                """family => document.fonts.check(`16px "${family}"`)""",
                arg=family,
            )
            rendered_families = page.locator(
                "#face-name-layer .face-name:not(.unnamed)"
            ).evaluate_all("labels => labels.map(label => getComputedStyle(label).fontFamily)")
            check(
                all(family in rendered for rendered in rendered_families),
                f"茶棕可切换并实际渲染 {family}",
                checks,
            )

        page.evaluate("applyFacePreset('tea')")
        page.wait_for_timeout(150)
        page.screenshot(path=str(REPORT_DIR / "face-label-tea-settings.png"), full_page=False)
        page.click("#face-style-close")
        page.wait_for_selector("#face-style-popover", state="hidden")
        page.screenshot(path=str(REPORT_DIR / "face-label-tea.png"), full_page=False)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof viewer === 'object'")
        check(
            page.evaluate(
                "() => viewer.faceStyle.theme==='tea' && viewer.faceStyle.backgroundOpacity===0.66 && viewer.faceStyle.fontFamily==='ma-shan-zheng'"
            ),
            "茶棕选择、默认字体和透明度在刷新后继续保留",
            checks,
        )

        fallback_page = browser.new_page(viewport={"width": 1200, "height": 800})
        fallback_page.route(
            "**/api/face-label-bg/**",
            lambda route: route.fulfill(status=404, content_type="text/plain", body="missing test asset"),
        )
        open_photo(fallback_page, asset_id)
        fallback_page.evaluate("applyFacePreset('tea')")
        fallback_page.wait_for_function(
            "() => document.querySelector('#detail-dialog')?.dataset.faceTheme==='classic'"
        )
        check(
            fallback_page.locator("#face-theme").input_value() == "classic",
            "茶棕底图缺失时自动恢复默认主题",
            checks,
        )
        fallback_page.close()

        narrow_page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        open_photo(narrow_page, asset_id)
        narrow_page.evaluate("applyFacePreset('tea')")
        narrow_page.click("#face-style-button")
        narrow_page.wait_for_selector("#face-style-popover", state="visible")
        panel_rect = narrow_page.locator("#face-style-popover").evaluate(
            """panel => {
              const rect=panel.getBoundingClientRect();
              return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:rect.width,height:rect.height};
            }"""
        )
        check(
            panel_rect["left"] >= 0
            and panel_rect["right"] <= 390
            and panel_rect["top"] >= 0
            and panel_rect["bottom"] <= 844,
            "390px 窄屏下人名标签菜单完整留在视口内",
            checks,
        )
        narrow_page.screenshot(
            path=str(REPORT_DIR / "face-label-tea-settings-narrow.png"),
            full_page=False,
        )
        narrow_page.close()
        browser.close()

    check(not page_errors, "茶棕真实浏览器验证没有 JavaScript 错误", checks)
    payload = {
        "passed": True,
        "asset_id": asset_id,
        "real_name_lengths": sorted(name_lengths),
        "image_evidence": images,
        "plate": "4.png",
        "labels": labels,
        "synthetic_sizes": synthetic,
        "checks": checks,
        "screenshots": [
            "face-label-tea-settings.png",
            "face-label-tea.png",
            "face-label-tea-settings-narrow.png",
        ],
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("TEA_LABEL_OK", len(checks), "checks", flush=True)


if __name__ == "__main__":
    main()
