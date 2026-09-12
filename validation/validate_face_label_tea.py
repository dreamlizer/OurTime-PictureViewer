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
    for filename in ("4.png", "5.png", "6.png"):
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
        [item["effective_height"] for item in images] == [1681, 1887, 2028],
        "茶棕 4 / 5 / 6.png 的非透明牌体高度由短到长",
        checks,
    )
    check(
        all(item["alpha_extrema"] == [0, 255] for item in images),
        "茶棕三张底牌都保留完整透明通道",
        checks,
    )
    for filename in ("4.png", "5.png", "6.png"):
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
                .every(label => getComputedStyle(label,'::before').backgroundImage.includes('/api/face-label-bg/'))"""
        )
        page.wait_for_function("""() => document.fonts.check('16px "Ma Shan Zheng"')""")
        page.wait_for_timeout(250)

        if page.locator("#face-style-popover").is_hidden():
            page.click("#face-style-button")
        check(page.locator("#face-theme").input_value() == "tea", "第三主题显示并选中茶棕", checks)
        check(page.locator("#toggle-face-dir").is_disabled(), "茶棕固定竖排，避免底牌被横排破坏", checks)
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
                size:label.dataset.faceLabelSize,
                width:Math.round(rect.width),
                height:Math.round(rect.height),
                left:rect.left,
                background:plate.backgroundImage,
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
        expected_file = {"s": "4.png", "m": "5.png", "l": "6.png"}
        check(
            all(expected_file[label["size"]] in label["background"] for label in labels),
            "茶棕按 S / M / L 固定使用 4 / 5 / 6.png",
            checks,
        )
        check(
            all(label["backgroundOpacity"] == "0.85" for label in labels),
            "茶棕默认底牌透明度为 85%",
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
            "茶棕完整保留 PNG 自带双线与纹理，不叠加 CSS 装饰",
            checks,
        )
        check(
            all(label["paddingLeft"] == "7px" and label["paddingRight"] == "9px" for label in labels),
            "茶棕文字在牌内保持向左 1px 的光学居中",
            checks,
        )
        check(
            all(float(label["letterSpacing"].removesuffix("px")) >= 4 for label in labels if label["size"] == "s")
            and all(float(label["letterSpacing"].removesuffix("px")) >= 1.8 for label in labels if label["size"] == "m"),
            "茶棕沿用两字和三字姓名的成熟字距",
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
                  size:label.dataset.faceLabelSize,
                  width:Math.round(rect.width),
                  height:Math.round(rect.height),
                  fontSize:getComputedStyle(label).fontSize,
                  background:getComputedStyle(label,'::before').backgroundImage
                };
                label.remove();
                return result;
              });
            }"""
        )
        expected = [
            {"count": 2, "size": "s", "width": 38, "height": 56, "fontSize": "16px", "file": "4.png"},
            {"count": 3, "size": "m", "width": 38, "height": 72, "fontSize": "15px", "file": "5.png"},
            {"count": 4, "size": "l", "width": 38, "height": 86, "fontSize": "14px", "file": "6.png"},
            {"count": 5, "size": "l", "width": 38, "height": 86, "fontSize": "13px", "file": "6.png"},
        ]
        check(
            all(
                {key: item[key] for key in ("count", "size", "width", "height", "fontSize")}
                == {key: wanted[key] for key in ("count", "size", "width", "height", "fontSize")}
                and wanted["file"] in item["background"]
                for item, wanted in zip(synthetic, expected)
            ),
            "茶棕 2 / 3 / 4 / 5 字尺寸、字号和底牌映射正确",
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
            all(
                item["width"] == 38
                and item["fontSize"] == f"{17 if item['count'] <= 2 else 16 if item['count'] == 3 else 15 if item['count'] == 4 else 14}px"
                and item["opacity"] == "0.6"
                for item in adjusted
            ),
            "茶棕调节字号和透明度时牌宽与居中基准保持稳定",
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
                "() => viewer.faceStyle.theme==='tea' && viewer.faceStyle.backgroundOpacity===0.85 && viewer.faceStyle.fontFamily==='ma-shan-zheng'"
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
        "mapping": {"s": "4.png", "m": "5.png", "l": "6.png"},
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
