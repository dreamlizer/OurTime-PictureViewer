"""Read-only checks for the deployed fixed-cover and pending-people UI."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
REPORT = ROOT / "validation" / "reports" / "person-cover-live.json"


def req(path: str):
    with urllib.request.urlopen(URL + path, timeout=30) as response:
        return json.load(response)


def check(value: bool, message: str, checks: list[str]) -> None:
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def main() -> int:
    checks: list[str] = []
    before = req("/api/status")
    named_total = req("/api/people?named=1&limit=1")["total"]
    named = req("/api/people?named=1&limit=12")["items"]
    sample = next(item for item in named if item["face_count"] >= 1)
    pending = req("/api/people?" + urllib.parse.urlencode({"offset": named_total, "limit": 1}))["items"]
    check(bool(pending) and not pending[0]["confirmed"], "正式库存在可直接到达的待命名人物", checks)
    detail = req(f"/api/people/{sample['id']}?limit=48")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL, wait_until="domcontentloaded")
        page.evaluate("setView('people')")
        page.wait_for_selector("#people-grid .person-card")
        page.locator("#people-jump-pending").click()
        page.wait_for_function("state.peoplePendingOnly && !state.peopleStream.people.loading")
        check(page.locator(".people-section-divider").count() == 1, "正式人物页可直接跳到待命名分界", checks)
        first_pending = page.locator("#people-grid .person-card.person-unnamed").first
        check(first_pending.is_visible(), "正式人物页已显示待命名头像", checks)

        page.evaluate("(id)=>openPerson(id)", sample["id"])
        page.wait_for_selector("#person-dialog[open]")
        cover_buttons = page.locator("#person-faces [data-set-cover]")
        check(cover_buttons.count() == len(detail["faces"]), "正式已命名人物的已加载照片都有头像选择按钮", checks)
        selected = page.locator("#person-faces [data-set-cover][aria-pressed='true']").count()
        expected = 1 if detail.get("cover_face_id") in {face["id"] for face in detail["faces"]} else 0
        check(selected == expected, "正式页面只把人工固定头像显示为选中", checks)
        check(not errors, "正式页面查看过程没有 JavaScript 错误", checks)
        browser.close()

    after = req("/api/status")
    check(before["capabilities"]["pid"] == after["capabilities"]["pid"], "只读验收没有重启或打断正式后台", checks)
    check(after["job"]["status"] == "paused", "正式扫描任务仍保持暂停", checks)
    result = {"passed": True, "checks": checks, "named_total": named_total}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "checks": len(checks)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
