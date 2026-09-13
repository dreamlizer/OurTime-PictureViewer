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
        page.locator('[data-view="places"]').click()
        page.wait_for_function("state.view==='places'")
        page.wait_for_function("!document.querySelector('.place-map-actions').hidden")
        normal = page.evaluate(
            """() => ({
              heading:document.querySelector('#places-heading').textContent,
              actionsHidden:document.querySelector('.place-map-actions').hidden,
              listHidden:document.querySelector('#places-list').hidden
            })"""
        )
        check(normal == {"heading": "按地点看", "actionsHidden": False, "listHidden": False}, "退出单张地图后普通地点地图完整恢复")
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
