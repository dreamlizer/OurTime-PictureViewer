"""Read-only browser validation for photo detail -> single-photo map -> detail."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "photo-place-map.json"


def main() -> int:
    checks: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)
        checks.append(message)
        print("PASS", message, flush=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#photo-grid [data-photo]")
        origin_view = page.evaluate("state.view")
        located = page.locator("#photo-grid [data-photo]").evaluate_all(
            """cards => cards.map(card => Number(card.dataset.photo))"""
        )
        photo_id = next(
            (
                photo_id
                for photo_id in located
                if page.evaluate(
                    """async id => {
                      const photo=await fetch('/api/photos/'+id).then(r=>r.json());
                      return photo.latitude!==null && photo.longitude!==null;
                    }""",
                    photo_id,
                )
            ),
            None,
        )
        check(photo_id is not None, "当前照片流存在带定位坐标的照片")

        page.locator(f'#photo-grid [data-photo="{photo_id}"]').click()
        page.wait_for_function(
            "id => document.querySelector('#detail-dialog').open && Number(state.detail?.id)===id",
            arg=photo_id,
        )
        page.wait_for_function("!document.querySelector('#detail-dialog').classList.contains('is-loading')")
        geometry = page.evaluate(
            """() => {
              const button=document.querySelector('#photo-place-map').getBoundingClientRect();
              const image=document.querySelector('#detail-img').getBoundingClientRect();
              const caption=document.querySelector('#photo-signature').getBoundingClientRect();
              const style=getComputedStyle(document.querySelector('#photo-place-map'));
              return {button,image,caption,opacity:Number(style.opacity)};
            }"""
        )
        check(
            geometry["button"]["right"] <= geometry["image"]["right"] + 1
            and geometry["button"]["bottom"] <= geometry["caption"]["top"] - 8,
            "地图入口位于照片右下角、元数据白边上方",
        )
        check(geometry["opacity"] < 0.85, "地图入口保持接近关闭按钮的淡化视觉")
        page.screenshot(path=str(REPORT.with_name("photo-place-entry.png")), full_page=False)

        page.locator("#photo-place-map").click()
        page.wait_for_function("id => state.view==='photo-place:'+id", arg=photo_id)
        page.wait_for_selector("#places-view:not([hidden]) .photo-place-marker img")
        page.wait_for_function(
            "document.querySelector('.photo-place-marker img').naturalWidth>0"
        )
        def fills_thumbnail() -> bool:
            return page.locator('.photo-place-thumb').evaluate('''frame=>{
              const image=frame.querySelector('img'),r=frame.getBoundingClientRect(),i=image.getBoundingClientRect();
              return Math.abs(i.width-frame.clientWidth)<.5&&Math.abs(i.height-frame.clientHeight)<.5
                &&Math.abs(i.left-r.left-frame.clientLeft)<.5&&Math.abs(i.top-r.top-frame.clientTop)<.5
                &&getComputedStyle(image).objectFit==='cover';
            }''')
        check(fills_thumbnail(), '真实地图缩略图铺满相框，右侧没有空白')
        page.locator('.photo-place-marker').screenshot(path=str(REPORT.with_name('photo-place-thumbnail-fixed.png')))
        real_src=page.locator('.photo-place-thumb img').get_attribute('src')
        for viewport in [{'width':1440,'height':900},{'width':390,'height':844}]:
            page.set_viewport_size(viewport)
            for width,height in [(480,640),(640,480),(400,400)]:
                page.locator('.photo-place-thumb img').evaluate('''async (image,size)=>{
                  image.src='data:image/svg+xml,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${size[0]}" height="${size[1]}"><rect width="100%" height="100%" fill="#567563"/></svg>`);
                  await image.decode();
                }''',[width,height])
                check(fills_thumbnail(),f'{viewport["width"]}px 窗口 / {width}×{height} 缩略图无空边且保持等比裁切')
        page.set_viewport_size({'width':1440,'height':900})
        page.locator('.photo-place-thumb img').evaluate('async (image,src)=>{image.src=src;await image.decode()}',real_src)
        single = page.evaluate(
            """() => ({
              layers:state.placeCluster.getLayers().length,
              zoom:Number(document.querySelector('#places-zoom').value),
              heading:document.querySelector('#places-heading').textContent,
              help:document.querySelector('#places-help').textContent,
              backVisible:!document.querySelector('#places-photo-back').hidden,
              actionsHidden:document.querySelector('.place-map-actions').hidden,
              mapVisible:document.querySelector('#places-map').getBoundingClientRect().height>0
            })"""
        )
        check(single["layers"] == 1, "单张地图只绘制这一张照片的定位")
        check(abs(single["zoom"] - 0.15) <= 0.02, "单张地图默认缩放倍数约为 0.15")
        check(single["help"] == "", "单张地图不显示解释性废话")
        check(single["heading"] != "按地点看" and single["mapVisible"], "左侧地点状态打开单张照片地图并显示实际地点")
        check(single["backVisible"] and single["actionsHidden"], "单张地图隐藏聚合筛选并保留返回大图入口")
        page.screenshot(path=str(REPORT.with_name("photo-place-map.png")), full_page=False)

        page.locator(".photo-place-marker").click()
        page.wait_for_function(
            "id => document.querySelector('#detail-dialog').open && Number(state.detail?.id)===id",
            arg=photo_id,
        )
        check(True, "点击地图缩略图返回同一张照片的大图")
        page.locator("#photo-place-map").click()
        page.wait_for_function("id => state.view==='photo-place:'+id", arg=photo_id)
        page.locator("#places-photo-back").click()
        page.wait_for_function(
            "id => document.querySelector('#detail-dialog').open && Number(state.detail?.id)===id",
            arg=photo_id,
        )
        check(page.evaluate("view => state.view===view", origin_view), "返回大图时先恢复进入地图前的照片页面")
        dialog_box = page.locator("#detail-dialog").bounding_box()
        assert dialog_box is not None
        page.mouse.click(dialog_box["x"] + 8, dialog_box["y"] + 8)
        page.wait_for_function("!document.querySelector('#detail-dialog').open")
        check(
            page.evaluate("view => state.view===view && document.querySelector('#places-view').hidden", origin_view),
            "返回大图后点击空白关闭照片，回到原照片页面而不是再次回地图",
        )
        page.locator(f'#photo-grid [data-photo="{photo_id}"]').click()
        page.wait_for_function(
            "id => document.querySelector('#detail-dialog').open && Number(state.detail?.id)===id",
            arg=photo_id,
        )
        page.evaluate("state.detail.latitude=null;state.detail.longitude=null")
        page.locator("#photo-place-map").click()
        page.wait_for_function("document.querySelector('#toast').textContent.includes('没有定位坐标')")
        check(page.locator("#detail-dialog").is_visible(), "没有坐标时明确提示并保持当前大图")
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('#detail-dialog').open")
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector('[data-view="places"]')
        page.locator('[data-view="places"]').click()
        page.wait_for_function("state.view==='places'")
        page.wait_for_function("!document.querySelector('.place-map-actions').hidden")
        page.wait_for_selector("#places-view.is-ready")
        normal = page.evaluate(
            """() => ({
              heading:document.querySelector('#places-heading').textContent,
              helpHidden:document.querySelector('#places-help').hidden,
              actionsHidden:document.querySelector('.place-map-actions').hidden,
              listHidden:document.querySelector('#places-list').hidden,
              searchPlaceholder:document.querySelector('#places-search').placeholder
            })"""
        )
        check(
            normal == {
                "heading": "按地点看",
                "helpHidden": True,
                "actionsHidden": False,
                "listHidden": True,
                "searchPlaceholder": "搜索已记录地点",
            },
            "普通地点地图恢复，说明文案已精简并明确搜索范围",
        )
        page.locator("#places-search").fill("望京")
        page.wait_for_selector('#places-list:not([hidden]) [data-place*="望京"]')
        search = page.evaluate(
            """() => ({
              expanded:document.querySelector('#places-search').getAttribute('aria-expanded'),
              results:[...document.querySelectorAll('#places-list [data-place]')].map(node=>node.dataset.place),
              dropdownTop:document.querySelector('#places-list').getBoundingClientRect().top,
              inputBottom:document.querySelector('#places-search').getBoundingClientRect().bottom
            })"""
        )
        check(search["expanded"] == "true" and any("望京" in place for place in search["results"]), "搜索能找到照片库内已记录的望京地点")
        check(search["dropdownTop"] >= search["inputBottom"] - 1, "地点搜索结果直接显示在输入框下方")
        page.screenshot(path=str(REPORT.with_name("places-search-results.png")), full_page=False)
        page.locator("#places-search").fill("")
        page.wait_for_function("document.querySelector('#places-list').hidden")
        page.evaluate("window.scrollTo(0, document.querySelector('#places-map').offsetTop + 180)")
        page.wait_for_timeout(200)
        sticky = page.evaluate(
            """() => {
              const chrome=document.querySelector('#places-view .view-chrome');
              const rect=chrome.getBoundingClientRect();
              const top=document.elementFromPoint(rect.left+Math.min(80,rect.width/2),rect.top+Math.min(18,rect.height/2));
              return {position:getComputedStyle(chrome).position,top:rect.top,ownsTop:chrome.contains(top)};
            }"""
        )
        check(sticky["position"] == "sticky" and sticky["top"] >= 100 and sticky["ownsTop"], "地点标题和工具栏滚动时固定在地图上方且不被遮住")
        page.screenshot(path=str(REPORT.with_name("places-sticky-header.png")), full_page=False)
        check(not errors, "真实浏览器链路没有 JavaScript 错误")
        browser.close()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps({"passed": True, "checks": checks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
