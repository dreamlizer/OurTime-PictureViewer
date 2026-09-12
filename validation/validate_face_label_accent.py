"""Focused live-browser acceptance for the fourth, dark-vermilion face-label theme."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from validate_face_label_ivory import (
    REPORT_DIR,
    check,
    choose_group_photo,
    open_photo,
    request_status,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT = REPORT_DIR / "face-label-accent.json"
FONT_FILES = {
    "zcool-xiaowei": ("ZCOOL XiaoWei", "ZCOOLXiaoWei-Regular.ttf"),
    "noto-serif-sc": ("Noto Serif SC", "NotoSerifSC-wght.ttf"),
    "zhi-mang-xing": ("Zhi Mang Xing", "ZhiMangXing-Regular.ttf"),
}


def main():
    checks = []
    screenshots = []
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    for directory, (family, filename) in FONT_FILES.items():
        font_path = ROOT / "web" / "vendor" / "fonts" / directory / filename
        license_path = font_path.with_name("OFL.txt")
        check(font_path.is_file() and font_path.stat().st_size > 4_000_000, f"{family} 字体文件完整", checks)
        check(
            license_path.is_file()
            and "SIL OPEN FONT LICENSE" in license_path.read_text(encoding="utf-8"),
            f"{family} 保留 OFL 授权",
            checks,
        )
        status, content_type, byte_count = request_status(
            f"/vendor/fonts/{directory}/{filename}"
        )
        check(
            status == 200 and content_type == "font/ttf" and byte_count == font_path.stat().st_size,
            f"{family} 经正式服务返回完整 TTF",
            checks,
        )

    asset_id, name_lengths = choose_group_photo()
    page_errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        open_photo(page, asset_id)
        page.evaluate("applyFacePreset('accent')")
        page.wait_for_function(
            "() => document.querySelector('#detail-dialog')?.dataset.faceTheme==='accent'"
        )
        page.wait_for_function("""() => document.fonts.check('15px "ZCOOL XiaoWei"')""")

        if page.locator("#face-style-popover").is_hidden():
            page.click("#face-style-button")
        check(page.locator("#face-theme").input_value() == "accent", "第四主题显示并选中暗朱", checks)
        compact_layout = page.locator("#face-style-popover").evaluate(
            """panel => {
              const rect = panel.getBoundingClientRect();
              const measureRow = id => {
                const input = panel.querySelector(id);
                const row = input.closest('.face-range-field');
                const label = row.querySelector(':scope > span:first-child');
                const output = row.querySelector('output');
                const centers = [label, input, output].map(item => {
                  const itemRect = item.getBoundingClientRect();
                  return itemRect.top + itemRect.height / 2;
                });
                return {
                  rowHeight: row.getBoundingClientRect().height,
                  centerSpread: Math.max(...centers) - Math.min(...centers)
                };
              };
              const fontSelect = panel.querySelector('#face-font-family');
              const fontRow = fontSelect.closest('.face-style-field');
              const fontRect = fontSelect.getBoundingClientRect();
              const fontRowRect = fontRow.getBoundingClientRect();
              return {
                height: rect.height,
                clientHeight: panel.clientHeight,
                scrollHeight: panel.scrollHeight,
                fontSizeRow: measureRow('#face-font-size'),
                opacityRow: measureRow('#face-bg-opacity'),
                fontRowHeight: fontRowRect.height,
                fontInsideRow:
                  fontRect.top >= fontRowRect.top &&
                  fontRect.bottom <= fontRowRect.bottom
              };
            }"""
        )
        check(
            compact_layout["height"] < 710
            and compact_layout["scrollHeight"] <= compact_layout["clientHeight"],
            "桌面设置菜单已压缩且不需要内部滚动",
            checks,
        )
        check(
            compact_layout["fontSizeRow"]["rowHeight"] <= 46
            and compact_layout["opacityRow"]["rowHeight"] <= 46
            and compact_layout["fontSizeRow"]["centerSpread"] <= 2
            and compact_layout["opacityRow"]["centerSpread"] <= 2,
            "字号和底色透明度均为单行紧凑滑杆",
            checks,
        )
        check(
            compact_layout["fontRowHeight"] <= 42 and compact_layout["fontInsideRow"],
            "字体选择完整收在单行内且没有遮盖",
            checks,
        )
        options = page.locator("#face-font-family option").evaluate_all(
            "items => items.map(item => ({value:item.value,text:item.textContent}))"
        )
        check(
            [item["value"] for item in options]
            == ["zcool-xiaowei", "noto-serif-sc", "zhi-mang-xing"]
            and page.locator("#face-font-family").input_value() == "zcool-xiaowei",
            "暗朱字体只提供三款内置候选并默认 ZCOOL XiaoWei",
            checks,
        )
        check(
            all(
                not page.locator(selector).is_disabled()
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
            "暗朱的字体、字号、配色和形状仍可在同一菜单内调整",
            checks,
        )

        labels = page.locator("#face-name-layer .face-name:not(.unnamed)").evaluate_all(
            """labels => labels.map(label => {
              const style=getComputedStyle(label);
              const rect=label.getBoundingClientRect();
              return {
                width:Math.round(rect.width),
                height:Math.round(rect.height),
                color:style.color,
                backgroundColor:style.backgroundColor,
                backgroundImage:style.backgroundImage,
                borderStyle:style.borderStyle,
                borderColor:style.borderColor,
                borderRadius:style.borderRadius,
                boxShadow:style.boxShadow,
                fontFamily:style.fontFamily,
                fontSize:style.fontSize,
                letterSpacing:style.letterSpacing
              };
            })"""
        )
        check(len(labels) >= 2, "真实合影显示至少两个暗朱人名标签", checks)
        check(
            all(
                item["color"] == "rgb(244, 228, 203)"
                and item["backgroundColor"] == "rgba(111, 48, 44, 0.84)"
                and "ZCOOL XiaoWei" in item["fontFamily"]
                and item["fontSize"] == "15px"
                and item["borderStyle"] == "solid"
                and item["borderRadius"] == "4px"
                for item in labels
            ),
            "暗朱默认字色、底色、字号、字体和圆角正确",
            checks,
        )
        check(
            all(
                "repeating-linear-gradient" in item["backgroundImage"]
                and "inset" in item["boxShadow"]
                and item["borderColor"] != "rgba(0, 0, 0, 0)"
                for item in labels
            ),
            "暗朱呈现轻纸纹、细金边和克制立体层次",
            checks,
        )

        for value, (family, _) in FONT_FILES.items():
            page.locator("#face-font-family").select_option(value)
            page.wait_for_function(
                """name => document.fonts.check(`15px "${name}"`)""",
                arg=family,
            )
            rendered = page.locator(
                "#face-name-layer .face-name:not(.unnamed)"
            ).evaluate_all("items => items.map(item => getComputedStyle(item).fontFamily)")
            check(
                all(family in current for current in rendered),
                f"暗朱可切换并实际渲染 {family}",
                checks,
            )
            screenshot = f"face-label-accent-{value}.png"
            page.screenshot(path=str(REPORT_DIR / screenshot), full_page=False)
            screenshots.append(screenshot)

        page.evaluate("applyFacePreset('accent')")
        check(
            page.locator("#face-style-popover").is_visible()
            and page.locator("#face-font-family").input_value() == "zcool-xiaowei",
            "恢复暗朱预设仍停留在同一个设置菜单",
            checks,
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof viewer === 'object'")
        check(
            page.evaluate(
                "() => viewer.faceStyle.theme==='accent' && viewer.faceStyle.fontFamily==='zcool-xiaowei' && viewer.faceStyle.fontSize===15"
            ),
            "暗朱主题、默认字体和字号在刷新后继续保留",
            checks,
        )

        narrow_page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        open_photo(narrow_page, asset_id)
        narrow_page.evaluate("applyFacePreset('accent')")
        narrow_page.click("#face-style-button")
        narrow_page.wait_for_selector("#face-style-popover", state="visible")
        panel_rect = narrow_page.locator("#face-style-popover").evaluate(
            """panel => {
              const rect=panel.getBoundingClientRect();
              return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom};
            }"""
        )
        check(
            panel_rect["left"] >= 0
            and panel_rect["right"] <= 390
            and panel_rect["top"] >= 0
            and panel_rect["bottom"] <= 844,
            "390px 窄屏下暗朱字体菜单完整留在视口内",
            checks,
        )
        narrow_screenshot = "face-label-accent-settings-narrow.png"
        narrow_page.screenshot(path=str(REPORT_DIR / narrow_screenshot), full_page=False)
        screenshots.append(narrow_screenshot)
        narrow_page.close()

        laptop_page = browser.new_page(viewport={"width": 1366, "height": 768}, device_scale_factor=1)
        open_photo(laptop_page, asset_id)
        laptop_page.evaluate("applyFacePreset('accent')")
        laptop_page.click("#face-style-button")
        laptop_page.wait_for_selector("#face-style-popover", state="visible")
        laptop_layout = laptop_page.locator("#face-style-popover").evaluate(
            """panel => {
              const rect=panel.getBoundingClientRect();
              return {
                left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,
                height:rect.height,clientHeight:panel.clientHeight,scrollHeight:panel.scrollHeight
              };
            }"""
        )
        check(
            laptop_layout["left"] >= 0
            and laptop_layout["right"] <= 1366
            and laptop_layout["top"] >= 0
            and laptop_layout["bottom"] <= 768
            and laptop_layout["height"] < 710
            and laptop_layout["scrollHeight"] <= laptop_layout["clientHeight"],
            "1366×768 小屏电脑上菜单完整显示且无需滚动",
            checks,
        )
        laptop_screenshot = "face-label-accent-settings-laptop.png"
        laptop_page.screenshot(path=str(REPORT_DIR / laptop_screenshot), full_page=False)
        screenshots.append(laptop_screenshot)
        laptop_page.close()

        landscape_page = browser.new_page(viewport={"width": 844, "height": 390}, device_scale_factor=1)
        open_photo(landscape_page, asset_id)
        landscape_page.evaluate("applyFacePreset('accent')")
        landscape_page.click("#face-style-button")
        landscape_page.wait_for_selector("#face-style-popover", state="visible")
        landscape_rect = landscape_page.locator("#face-style-popover").evaluate(
            """panel => {
              const rect=panel.getBoundingClientRect();
              return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom};
            }"""
        )
        check(
            landscape_rect["left"] >= 0
            and landscape_rect["right"] <= 844
            and landscape_rect["top"] >= 0
            and landscape_rect["bottom"] <= 390,
            "844×390 横屏下暗朱字体菜单完整留在视口内并可滚动",
            checks,
        )
        landscape_page.close()
        browser.close()

    check(not page_errors, "暗朱真实浏览器验证没有 JavaScript 错误", checks)
    payload = {
        "passed": True,
        "asset_id": asset_id,
        "real_name_lengths": sorted(name_lengths),
        "fonts": FONT_FILES,
        "labels": labels,
        "compact_layout": compact_layout,
        "laptop_layout": laptop_layout,
        "checks": checks,
        "screenshots": screenshots,
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ACCENT_LABEL_OK", len(checks), "checks", flush=True)


if __name__ == "__main__":
    main()
