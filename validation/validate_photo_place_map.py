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
        check(abs(single["zoom"] - 0.2) <= 0.02, "单张地图默认缩放倍数约为 0.2")
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
        check(True, "返回大图按钮也回到同一张照片")
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
