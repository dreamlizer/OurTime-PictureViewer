"""Isolated browser acceptance for the design/experience task card.

Never talks to production 8765 or writes the production data directory.
"""
from __future__ import annotations

import json
import hashlib
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from home_recommendations import ensure_home_schema
from library_db import init_schema


STAMP = time.strftime("%Y%m%d-%H%M%S")
RUN = ROOT / "validation" / "work" / f"design-experience-{STAMP}"
DATA = RUN / "data"
PHOTOS = RUN / "photos"
REPORT = ROOT / "validation" / "reports" / "design-experience-20260922" / STAMP
CHECKS: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    CHECKS.append(message)
    print("PASS", message, flush=True)


def add_photo(connection: sqlite3.Connection, asset_id: int) -> None:
    path = PHOTOS / f"memory-{asset_id}.jpg"
    image = Image.new("RGB", (1440, 960), (34 + asset_id * 13, 82 + asset_id * 5, 103))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80 + asset_id * 12, 100, 900, 760), outline=(220, 228, 216), width=18)
    draw.ellipse((300 + asset_id * 9, 220, 760 + asset_id * 9, 680), fill=(120, 154, 134))
    image.save(path, quality=90)
    day = min(10 + asset_id, 17)
    captured = f"2024-09-{day:02d}T10:{asset_id:02d}:00"
    connection.execute(
        """INSERT INTO assets(
             id,sha256,width,height,format,metadata,captured_at,date_source,date_precision,
             place,category,created_at,face_state,notes,favorite
           ) VALUES (?,?,?,?, 'JPEG','{}',?,'EXIF 原始拍摄时间','秒',
                     '杭州 · 西湖','照片',datetime('now'),1,'',0)""",
        (asset_id, f"{asset_id:064x}", 1440, 960, captured),
    )
    connection.execute(
        """INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded)
           VALUES (?,?,1,1,datetime('now'),1,0)""",
        (asset_id, str(path)),
    )


def seed_fixture() -> None:
    DATA.mkdir(parents=True)
    PHOTOS.mkdir()
    REPORT.mkdir(parents=True)
    (DATA / "thumbs").mkdir()
    (DATA / "faces").mkdir()
    with sqlite3.connect(DATA / "library.sqlite3") as connection:
        init_schema(connection)
        ensure_home_schema(connection)
        for asset_id in range(1, 9):
            add_photo(connection, asset_id)
        connection.execute(
            "INSERT INTO people(id,name,alias,confirmed,ignored) VALUES (1,'林女士','小林',1,0)"
        )
        connection.execute(
            "INSERT INTO people(id,name,alias,confirmed,ignored) VALUES (2,'待核对','',0,0)"
        )
        face_id = 100
        for person_id, asset_ids in ((1, (1, 2, 3, 4)), (2, (5, 6))):
            for asset_id in asset_ids:
                connection.execute(
                    """INSERT INTO faces(id,asset_id,person_id,bbox,embedding,score,reviewed,ignored)
                       VALUES (?,?,?,'[200,120,900,820,1440,960]',x'00',0.9,1,0)""",
                    (face_id, asset_id, person_id),
                )
                Image.new(
                    "RGB",
                    (240, 240),
                    (80 + person_id * 42, 112 + asset_id * 6, 102),
                ).save(DATA / "faces" / f"{face_id}.jpg", quality=88)
                face_id += 1
        connection.execute(
            """INSERT INTO face_label_overrides(face_id,asset_id,x_ratio,y_ratio,layout_version,updated_at)
               VALUES (100,1,0.21,0.64,1,datetime('now'))"""
        )
        place_token = hashlib.sha256("杭州 · 西湖".encode("utf-8")).hexdigest()[:16]
        source_id = f"place:{place_token}:2024-09-11:2024-09-17"
        story_id = "memory:trip:" + source_id
        card = {
            "kind": "memory_trip",
            "group_id": story_id,
            "title": "西湖的秋天",
            "subtitle": "2024 年 9 月",
            "photo_count": 8,
            "cover_asset_ids": [1, 4, 7],
            "covers": [{"asset_id": item, "preview": "thumb", "object_position": "50% 40%"} for item in (1, 4, 7)],
            "date_from": "2024-09-11",
            "date_to": "2024-09-17",
            "place": "杭州 · 西湖",
            "place_label": "西湖",
            "source_group_id": source_id,
            "source_count": 8,
            "highlight_ids": list(range(1, 9)),
            "playable": True,
            "cta": "播放",
        }
        connection.execute(
            """INSERT INTO home_stories(
                 story_id,kind,source_group_id,season,date_from,date_to,title,subtitle,payload_json,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                story_id,
                "memory_trip",
                source_id,
                "autumn",
                "2024-09-11",
                "2024-09-17",
                card["title"],
                card["subtitle"],
                json.dumps(card, ensure_ascii=False),
            ),
        )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(url: str) -> dict:
    import urllib.request

    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("isolated server did not start")


def main() -> None:
    seed_fixture()
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PHOTO_LIBRARY_DATA": str(DATA),
        "PHOTO_WEB_ROOT": str(ROOT / "web"),
        "PHOTO_MODEL_ROOT": str(DATA / "no-model"),
        "PHOTO_GEO_ROOT": str(DATA / "no-geo"),
        "PHOTO_HOME_AS_OF": "2026-09-22",
        "PHOTO_NO_BROWSER": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    server_log = RUN / "server.log"
    log_handle = server_log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=log_handle,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    errors: list[str] = []
    console_warnings: list[str] = []
    request_failures: list[str] = []
    http_errors: list[str] = []
    health = {}
    try:
        health = wait_for_health(url)
        check(str(DATA) in str(health.get("data_dir")), "隔离服务指向本次 PHOTO_LIBRARY_DATA")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: console_warnings.append(f"{message.type}: {message.text}")
                if message.type in ("warning", "error")
                else None,
            )
            page.on(
                "requestfailed",
                lambda request: request_failures.append(f"{request.method} {request.url}: {request.failure}"),
            )
            page.on(
                "response",
                lambda response: http_errors.append(f"{response.status} {response.url}")
                if response.status >= 400
                else None,
            )
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function(
                "state.view==='home' && !document.querySelector('#home-view').hidden"
                " && document.querySelector('.home-memory')"
            )
            page.screenshot(path=str(REPORT / "01-home-1440.png"), full_page=True)

            more = page.locator(".home-memory .home-card-more").first
            bounds = more.bounding_box()
            check(
                bool(bounds and bounds["width"] >= 48 and bounds["height"] >= 32),
                "首页卡片“更多”入口达到 48×32 可点区域",
            )
            more.click()
            menu = page.locator(".home-memory .home-card-menu").first
            check(menu.is_visible(), "“更多”只展开卡片二级菜单")
            check(page.locator("#memory-player").is_hidden(), "展开卡片菜单不会误开回忆")
            page.keyboard.press("Escape")
            check(menu.is_hidden(), "Esc 收起卡片二级菜单")
            check(more.evaluate("el => document.activeElement === el"), "收起菜单后焦点回到“更多”")

            opener = page.locator(".home-memory").first
            opener.click(position={"x": 24, "y": 210})
            page.wait_for_selector("#memory-player:not([hidden])")
            if page.locator("#memory-player").evaluate("el => el.classList.contains('is-overture')"):
                page.keyboard.press("Space")
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='playing'")
            check(page.locator("#memory-toggle").inner_text() == "暂停", "回忆进入明确的 playing 状态")

            page.wait_for_timeout(1200)
            page.locator("#memory-toggle").click()
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='paused'")
            page.wait_for_timeout(200)
            paused_progress = page.locator("#memory-progress").inner_text()
            paused_motion = page.evaluate(
                """() => {
                  const current = [...document.querySelectorAll('.memory-ken')].find(el => el.classList.contains('is-in'));
                  if (!current) return null;
                  const animation = current._memoryKenAnimation;
                  return {
                    transform: getComputedStyle(current).transform,
                    playState: animation ? animation.playState : '',
                    currentTime: animation ? Math.round(Number(animation.currentTime || 0)) : -1
                  };
                }"""
            )
            page.wait_for_timeout(12000)
            check(page.locator("#memory-progress").inner_text() == paused_progress, "暂停 12 秒后照片没有自动前进")
            paused_motion_after = page.evaluate(
                """() => {
                  const current = [...document.querySelectorAll('.memory-ken')].find(el => el.classList.contains('is-in'));
                  if (!current) return null;
                  const animation = current._memoryKenAnimation;
                  return {
                    transform: getComputedStyle(current).transform,
                    playState: animation ? animation.playState : '',
                    currentTime: animation ? Math.round(Number(animation.currentTime || 0)) : -1
                  };
                }"""
            )
            check(
                paused_motion
                and paused_motion_after
                and paused_motion["playState"] == "paused"
                and paused_motion_after["playState"] == "paused"
                and paused_motion["currentTime"] == paused_motion_after["currentTime"]
                and paused_motion["transform"] == paused_motion_after["transform"],
                "暂停同时冻结照片缓慢运动",
            )
            check(page.locator("#memory-toggle").inner_text() == "继续", "暂停后主按钮明确显示“继续”")

            page.locator("#memory-toggle").click()
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='playing'")
            resumed_progress = page.locator("#memory-progress").inner_text()
            page.wait_for_timeout(3200)
            check(page.locator("#memory-progress").inner_text() == resumed_progress, "继续后不会立即跳过当前照片")
            page.wait_for_timeout(3400)
            check(page.locator("#memory-progress").inner_text() != resumed_progress, "继续播放后按完整节奏进入下一张")

            page.locator("#memory-toggle").click()
            current = page.locator("#memory-progress").inner_text()
            page.locator("#memory-next").click()
            next_progress = page.locator("#memory-progress").inner_text()
            page.wait_for_timeout(6200)
            check(next_progress != current, "暂停时仍可手动查看下一张")
            check(page.locator("#memory-progress").inner_text() == next_progress, "手动翻页后保持暂停，不偷偷恢复")

            for _ in range(5):
                page.locator("#memory-toggle").click()
                page.wait_for_timeout(100)
            check(
                page.locator("#memory-player").get_attribute("data-state") == "playing"
                and page.locator("#memory-progress").inner_text() == next_progress,
                "连续切换暂停/继续 5 次没有跳图或状态错乱",
            )
            page.locator("#memory-toggle").click()
            page.locator("#memory-toggle").focus()
            page.keyboard.press("Space")
            check(
                page.locator("#memory-player").get_attribute("data-state") == "playing",
                "暂停按钮聚焦后按 Space 只执行一次继续",
            )
            page.locator("#memory-player").focus()
            page.keyboard.press("Space")
            check(
                page.locator("#memory-player").get_attribute("data-state") == "paused",
                "普通播放区按 Space 只执行一次暂停",
            )

            page.locator("#memory-open-photo").click()
            page.wait_for_selector("#detail-dialog[open]")
            detail_id = page.evaluate("viewer.ids[viewer.index]")
            page.keyboard.press("Escape")
            page.wait_for_function("!document.querySelector('#detail-dialog').open")
            check(
                page.locator("#memory-player").is_visible()
                and page.locator("#memory-player").get_attribute("data-state") == "paused",
                "第一层 Esc 关闭大图并回到原回忆暂停态",
            )
            check(
                str(detail_id) in page.locator("#memory-dots button[aria-current='true']").get_attribute("aria-label")
                or page.locator("#memory-progress").inner_text() == next_progress,
                "关闭大图后仍停在进入前的回忆位置",
            )
            page.keyboard.press("Escape")
            page.wait_for_function("document.querySelector('#memory-player').hidden")
            check(opener.evaluate("el => document.activeElement === el"), "第二层 Esc 退出回忆并把焦点交还卡片")

            opener.click(position={"x": 24, "y": 210})
            page.wait_for_selector("#memory-player:not([hidden])")
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='playing'")
            check(
                not page.locator("#memory-player").evaluate("el => el.classList.contains('is-overture')"),
                "同一标签页再次播放不重复长开场",
            )
            last_dot = page.locator("#memory-dots [data-memory-index]").last
            last_dot.click()
            page.wait_for_timeout(6500)
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='ended'")
            page.wait_for_timeout(15000)
            check(page.locator("#memory-player").is_visible(), "最后一张结束 15 秒后仍停留，不自动黑屏退出")
            check(page.locator("#memory-end").is_visible(), "结束态显示“再看一次 / 看全部 / 返回首页”")
            page.screenshot(path=str(REPORT / "02-memory-ended-1440.png"))

            page.locator("#memory-all").click()
            page.wait_for_function("state.view==='home-group' && Number(waterfall.total)===8")
            check(True, "“看全部”进入原组完整 8 张照片")
            page.locator("#home-group-back").click()
            page.wait_for_function("state.view==='home' && document.querySelector('.home-memory')")
            opener = page.locator(".home-memory").first
            opener.click(position={"x": 24, "y": 210})
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='playing'")
            page.locator("#memory-dots [data-memory-index]").last.click()
            page.wait_for_timeout(6500)
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='ended'")
            page.locator("#memory-again").click()
            page.wait_for_function(
                "document.querySelector('#memory-player').dataset.state==='playing'"
                " && document.querySelector('#memory-progress').textContent.trim().startsWith('1 /')"
            )
            check(True, "“再看一次”从第一张重新播放")
            page.locator("#memory-dots [data-memory-index]").last.click()
            page.wait_for_timeout(6500)
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='ended'")
            page.locator("#memory-home").click()
            page.wait_for_function("document.querySelector('#memory-player').hidden && state.view==='home'")
            check(True, "“返回首页”关闭播放器并保留首页")

            page.emulate_media(reduced_motion="reduce")
            opener.click(position={"x": 24, "y": 210})
            page.wait_for_selector("#memory-player:not([hidden])")
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='playing'")
            animation_count = page.evaluate(
                "() => [...document.querySelectorAll('.memory-ken')].reduce((n, el) => n + el.getAnimations().length, 0)"
            )
            check(animation_count == 0, "减少动态效果偏好下不启动照片缩放动画")
            page.locator("#memory-close").click()
            page.emulate_media(reduced_motion="no-preference")

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            check(
                not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"),
                "390px 首页没有横向溢出",
            )
            opener = page.locator(".home-memory").first
            opener.click(position={"x": 20, "y": 150})
            page.wait_for_selector("#memory-player:not([hidden])")
            page.wait_for_function("document.querySelector('#memory-player').dataset.state==='playing'")
            check(
                not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"),
                "390px 回忆播放器没有横向溢出",
            )
            page.screenshot(path=str(REPORT / "03-memory-390.png"))
            page.locator("#memory-close").click()

            page.set_viewport_size({"width": 1440, "height": 900})
            page.locator("[data-view='people']").click()
            page.wait_for_function("state.view==='people' && document.querySelector('.person-card[data-person=\"1\"]')")
            page.locator(".person-card[data-person='1']").click()
            page.wait_for_selector("#person-dialog[open]")
            page.wait_for_timeout(250)
            check(
                "林女士" in page.locator("#person-title").inner_text(),
                "已命名人物详情首先显示人物身份",
            )
            check(
                page.locator("#show-person-photos").is_visible()
                and "4 张" in page.locator("#show-person-photos").inner_text(),
                "已命名人物把查看相关照片作为首屏主操作",
            )
            check(not page.locator("#person-organize").evaluate("el => el.open"), "已命名人物默认收起整理操作")
            check(
                page.locator("#person-face-count").inner_text() == "4 张",
                "人物照片区明确显示照片数量",
            )
            page.locator("#person-organize > summary").click()
            check(page.locator("#person-organize").evaluate("el => el.open"), "需要时可展开命名、合并和路人操作")
            page.screenshot(path=str(REPORT / "04-person-named-1440.png"))
            page.locator("#person-dialog [data-close='person-dialog']").click()

            page.locator(".person-card[data-person='2']").click()
            page.wait_for_selector("#person-dialog[open]")
            page.wait_for_timeout(250)
            check(
                page.locator("#person-dialog").get_attribute("data-person-mode") == "pending",
                "待命名人物使用 pending 层级",
            )
            check(page.locator("#person-organize").evaluate("el => el.open"), "待命名人物默认展开整理操作")
            page.screenshot(path=str(REPORT / "05-person-pending-1440.png"))
            page.locator("#person-dialog [data-close='person-dialog']").click()

            page.locator("[data-view='timeline']").click()
            page.wait_for_function("state.view==='timeline' && document.querySelector('#photo-grid [data-photo]')")
            page.locator("#photo-grid [data-photo='1']").click()
            page.wait_for_selector("#detail-dialog[open]")
            page.wait_for_function(
                """() => {
                  if (Number(state.detail?.id) !== 1) return false;
                  const face = (state.detail.faces || []).find(item => Number(item.id) === 100);
                  return face && Number(face.label_x_ratio) === 0.21 && Number(face.label_y_ratio) === 0.64;
                }"""
            )
            check(page.locator(".viewer-tool-group").count() == 3, "大图保留缩放、人名、操作三组工具")
            check(
                page.locator("#reveal-button .viewer-action-label").is_visible()
                and page.locator("#reveal-button .viewer-action-label").inner_text() == "定位原文件",
                "桌面空间足够时显示完整“定位原文件”动作名",
            )
            manual_before = page.evaluate(
                """() => {
                  const face = (state.detail?.faces || []).find(item => Number(item.id) === 100);
                  return face ? [face.label_x_ratio, face.label_y_ratio] : null;
                }"""
            )
            page.locator("#face-style-button").click()
            page.wait_for_selector("#face-style-popover:not([hidden])")
            choices = page.locator("[data-face-theme-choice]")
            check(choices.count() == 4, "四套人名标签主题作为可见选项一次呈现")
            for theme in ("classic", "ivory", "tea", "accent"):
                choice = page.locator(f"[data-face-theme-choice='{theme}']")
                check(choice.is_visible(), f"主题 {theme} 可直接点击")
                choice.click()
                page.wait_for_function(
                    "(name) => document.querySelector('#detail-dialog').dataset.faceTheme === name",
                    arg=theme,
                )
                check(choice.get_attribute("aria-pressed") == "true", f"主题 {theme} 选中状态明确")
            manual_after = page.evaluate(
                """() => {
                  const face = (state.detail?.faces || []).find(item => Number(item.id) === 100);
                  return face ? [face.label_x_ratio, face.label_y_ratio] : null;
                }"""
            )
            check(
                bool(
                    manual_before
                    and manual_after
                    and len(manual_before) == 2
                    and len(manual_after) == 2
                    and abs(float(manual_before[0]) - 0.21) < 1e-6
                    and abs(float(manual_before[1]) - 0.64) < 1e-6
                    and all(abs(float(a) - float(b)) < 1e-9 for a, b in zip(manual_before, manual_after))
                ),
                "切换主题不丢失已有手动标签坐标",
            )
            page.screenshot(path=str(REPORT / "06-viewer-themes-1440.png"))
            page.locator("#face-style-close").click()

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(250)
            tool_geometry = page.locator(".viewer-tools").evaluate(
                """el => {
                  const style = getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  return {
                    direction: style.flexDirection,
                    bottom: innerHeight - rect.bottom,
                    width: rect.width,
                    scrollable: el.scrollWidth > el.clientWidth
                  };
                }"""
            )
            check(tool_geometry["direction"] == "row", "窄屏大图工具条改为底部横向排列")
            check(
                0 <= tool_geometry["bottom"] <= 20 and tool_geometry["width"] <= 366,
                "窄屏工具条留在可见底部且不撑破视口",
            )
            check(tool_geometry["scrollable"], "窄屏工具较多时可横向滚动，不压扁动作")
            page.screenshot(path=str(REPORT / "07-viewer-tools-390.png"))
            check(
                not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"),
                "390px 大图没有页面级横向溢出",
            )
            page.locator("#detail-dialog [data-close='detail-dialog']").click()

            check(not errors, "浏览器无 pageerror：" + "; ".join(errors[:3]))
            check(
                not console_warnings and not http_errors,
                "浏览器控制台与 HTTP 无 warning/error："
                + "; ".join((console_warnings + http_errors)[:3]),
            )
            check(not request_failures, "浏览器无失败请求：" + "; ".join(request_failures[:3]))
            browser.close()
        summary = {
            "passes": CHECKS,
            "health": health,
            "url": url,
            "run_dir": str(RUN),
            "report_dir": str(REPORT),
            "page_errors": errors,
            "console_warnings": console_warnings,
            "request_failures": request_failures,
            "http_errors": http_errors,
        }
        (REPORT / "browser-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_handle.close()


if __name__ == "__main__":
    main()
