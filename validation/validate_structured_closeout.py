"""Read-only live checks for the structured-filter closeout."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright
import urllib.request

URL = "http://127.0.0.1:8765"
REPORT = Path(__file__).resolve().parents[1] / "validation" / "reports" / "structured-closeout-20260912"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS", message, flush=True)


def api(path: str):
    with urllib.request.urlopen(URL + path, timeout=20) as response:
        return json.load(response)


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    status = api("/api/status")
    data_dir = (status.get("capabilities") or {}).get("data_dir") or ""
    check(data_dir.replace("/", "\\").lower().endswith("\\data") or data_dir.lower().endswith("\\data"), "8765 指向正式 data 目录")
    people = api("/api/people?named=1&limit=8")["items"]
    check(len(people) >= 2, "已命名人物足够做 AND 验收")
    a, b = people[0], people[1]
    and_total = api(f"/api/photos?person={a['id']},{b['id']}&limit=1")["total"]
    only_a = api(f"/api/photos?person={a['id']}&limit=1")["total"]
    check(and_total <= only_a, "多人物 AND 结果不大于单人结果")
    named_page = api("/api/people?named=1&limit=40")
    check(all(item.get("confirmed") == 1 for item in named_page["items"]), "人物筛选默认只返回已确认人物")

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=20000)
        check(page.locator('[data-ot="search"]').count() == 0, "首页没有自由搜索框")
        order = page.locator("[data-ot]").evaluate_all(
            "els => els.filter(e => ['person','time','place','folder','select','sort'].includes(e.dataset.ot)).map(e => e.dataset.ot)"
        )
        check(order == ["person", "time", "place", "folder", "select", "sort"], "首页工具栏为人物/时间/地点/文件夹/选择/排序")
        check(page.locator("#organize-nav").evaluate("el => el.open") is False, "首页整理默认折叠")
        page.screenshot(path=str(REPORT / "home.png"), full_page=True)

        photo_hits = {"count": 0}
        def on_request(request):
            if "/api/photos?" in request.url:
                photo_hits["count"] += 1
        page.on("request", on_request)
        before = photo_hits["count"]
        page.locator('[data-ot="person"]').click()
        page.wait_for_selector(".ot-home-popover:not([hidden])", timeout=8000)
        page.locator('[data-ot="people-search"]').fill("张")
        page.wait_for_timeout(400)
        check(photo_hits["count"] == before, "人物输入文字不刷新照片")
        page.keyboard.press("Escape")

        page.locator('[data-ot="place"]').click()
        page.wait_for_selector(".ot-home-popover:not([hidden])", timeout=8000)
        page.locator('[data-ot="place-search"]').fill("北")
        page.wait_for_timeout(400)
        check(photo_hits["count"] == before, "地点输入文字不刷新照片")
        page.keyboard.press("Escape")

        page.locator('[data-view="groups"]').click()
        page.wait_for_selector("#groups-view:not([hidden])", timeout=15000)
        page.locator('[data-group="5"]').click()
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)
        check(page.locator("#page-title").inner_text() == "5人合影", "合影详情主标题为 5人合影")
        page.wait_for_function("() => { const el=document.querySelector('#group-result-count'); return el && !el.hidden && /\d/.test(el.textContent||''); }", timeout=15000)
        count_info = page.locator("#group-result-count").evaluate("el => ({text: (el.textContent||'').trim(), hidden: el.hidden, display: getComputedStyle(el).display})")
        check(any(ch.isdigit() for ch in str(count_info.get("text") or "")) and not count_info.get("hidden"), "合影张数显示在主标题旁")
        check(page.locator("#collection-title").evaluate("el => { const box=el.closest('.section-heading'); const s=getComputedStyle(box); return s.display==='none' || box.hidden; }"), "合影详情不再显示第二层合影标题")
        check(page.locator('[data-ot="place"]').is_hidden() and page.locator('[data-ot="folder"]').is_hidden() and page.locator('[data-ot="select"]').is_hidden(), "合影详情只留人物、时间和排序")
        page.screenshot(path=str(REPORT / "group-5.png"), full_page=True)

        page.locator('[data-view="people"]').click()
        page.wait_for_selector("#people-view:not([hidden])", timeout=15000)
        check(page.locator("#page-title").inner_text() == "人物档案", "人物页主标题为人物档案")
        check(page.locator("#people-view h2").count() == 0, "人物页没有正文第二标题")
        check(page.locator("#stats").is_hidden(), "人物页隐藏全局统计")
        check(page.locator("#people-grid .person-check").count() == 0, "普通状态不显示 checkbox")
        page.locator("#people-merge-toggle").click()
        page.wait_for_timeout(200)
        check(page.locator("#people-merge-bar").is_visible(), "进入合并模式后出现操作栏")
        check(page.locator("#people-grid .person-check").count() > 0, "进入合并模式后出现 checkbox")
        page.locator("#clear-people-selection").click()
        page.wait_for_timeout(200)
        check(page.locator("#people-merge-bar").is_hidden(), "取消后退出合并操作栏")
        check(page.locator("#people-grid .person-check").count() == 0, "取消后 checkbox 全部消失")
        check(page.locator("#people-merge-toggle").inner_text() == "合并人物", "取消后按钮恢复为合并人物")
        page.screenshot(path=str(REPORT / "people.png"), full_page=True)

        named = [item for item in people if item.get("name")]
        target = named[0]
        page.locator(f'#people-grid [data-person="{target["id"]}"]').click()
        page.wait_for_selector("#person-dialog[open]", timeout=10000)
        page.locator("#show-person-photos").click()
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)
        chip = page.locator('[data-condition^="person:"]').first.inner_text()
        check("ID " not in chip and (target["name"] in chip or "×" in chip), "从人物详情查看照片后 chip 显示姓名而不是 ID")
        page.screenshot(path=str(REPORT / "person-chip.png"), full_page=True)

        page.locator("#organize-nav summary").click()
        page.wait_for_timeout(150)
        page.locator('[data-view="passersby"]').click()
        page.wait_for_selector("#passersby-view:not([hidden])", timeout=15000)
        check(page.locator("#organize-nav").evaluate("el => el.open") is True, "进入路人后整理自动展开")
        page.locator('[data-view="timeline"]').click()
        page.wait_for_selector("#home-query-host .ot-home-ui", timeout=15000)
        check(page.locator("#organize-nav").evaluate("el => el.open") is False, "离开整理项后恢复折叠")

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(250)
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        check(not overflow, "390px 首页无横向溢出")
        page.screenshot(path=str(REPORT / "narrow-390.png"), full_page=True)
        check(not errors, "真实浏览器无 JavaScript 错误")
        browser.close()
    print(json.dumps({"passed": True, "screenshots": sorted(p.name for p in REPORT.glob('*.png'))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
