"""Small isolated browser regression for V2 viewer continuity and polish."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

from playwright.sync_api import sync_playwright

from test_m1_frontend_browser import FETCH_HARNESS, ROOT, DATA, RUN, free_port, request, seed


REPORT = ROOT / "validation" / "reports" / "visual-polish-v2-browser.json"
SHOTS = ROOT / "validation" / "reports" / "visual-polish-v2"
checks: list[str] = []


def check(value: bool, text: str) -> None:
    if not value:
        raise AssertionError(text)
    checks.append(text)
    print("PASS", text, flush=True)


def main() -> int:
    seed()
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({
        "PHOTO_LIBRARY_DATA": str(DATA), "PHOTO_LIBRARY_PORT": str(port),
        "PHOTO_WEB_ROOT": str(ROOT / "web"), "PHOTO_MODEL_ROOT": str(DATA / "no-model"),
        "PHOTO_GEO_ROOT": str(DATA / "no-geo"), "PHOTO_NO_BROWSER": "1",
        "NO_ALBUMENTATIONS_UPDATE": "1", "PYTHONIOENCODING": "utf-8",
    })
    RUN.mkdir(parents=True, exist_ok=True)
    log_path = RUN / "visual-polish-server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(ROOT / "app.py"), "--port", str(port)], cwd=ROOT, env=env,
                               stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    errors: list[str] = []
    result = {"passed": False, "checks": checks, "screenshots": [], "run": str(RUN)}
    try:
        for _ in range(120):
            try:
                request(url, "/api/health")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        else:
            raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
        SHOTS.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script(FETCH_HARNESS)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function("document.querySelectorAll('#photo-grid [data-photo]').length===4")
            page.screenshot(path=str(SHOTS / "desktop-home.png"), full_page=True)
            result["screenshots"].append("validation/reports/visual-polish-v2/desktop-home.png")

            page.evaluate("void openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})")
            page.wait_for_function("window.__ourTimeApp.state.detail?.id===1 && document.querySelector('#detail-img').src")
            page.evaluate("""() => { window.__m1Harness.plan=(url,method) =>
                method==='GET' && url.pathname==='/api/photos/2' ? {after:560} : {}; movePhoto(-1); }""")
            page.wait_for_timeout(360)
            pending = page.evaluate("""() => ({
                detail:window.__ourTimeApp.state.detail?.id,
                shown:document.querySelector('#detail-img').getAttribute('src'),
                viewport:getComputedStyle(document.querySelector('#image-viewport')).visibility,
                arrows:getComputedStyle(document.querySelector('#viewer-next')).visibility,
                loading:!document.querySelector('#viewer-loading').hidden
            })""")
            check(pending["detail"] == 1 and pending["shown"] and pending["viewport"] != "hidden" and pending["arrows"] != "hidden" and pending["loading"],
                  "V02 慢翻图仍显示旧图，工具与导航未消失，延迟提示出现：" + str(pending))
            page.wait_for_function("window.__ourTimeApp.state.detail?.id===2")
            check(page.evaluate("document.querySelector('#detail-dialog').dataset.photoId") == "2", "V01 图像提交后资料与显示对象同为照片2")

            page.evaluate("""() => { let failed=false; window.__m1Harness.plan=(url,method) => {
                if(method==='GET' && url.pathname==='/api/photos/3' && !failed){failed=true;return {before:320,kind:'json',status:500,body:{detail:'injected'}};} return {};}; movePhoto(-1); }""")
            page.wait_for_timeout(120)
            failed = page.evaluate("() => ({id:window.__ourTimeApp.state.detail?.id, image:document.querySelector('#detail-img').getAttribute('src'), message:document.querySelector('#viewer-message').textContent})")
            check(failed["id"] == 2 and failed["image"] and "未能载入" not in failed["message"], "V04 请求失败前旧对象和画面仍保持")
            page.wait_for_timeout(220)
            failed = page.evaluate("() => ({id:window.__ourTimeApp.state.detail?.id, message:document.querySelector('#viewer-message').textContent})")
            check(failed["id"] == 2 and "未能载入" in failed["message"], "V04 失败不换对象，并给出可恢复提示")
            page.evaluate("movePhoto(-1)")
            page.wait_for_function("window.__ourTimeApp.state.detail?.id===3")
            page.evaluate("""() => { movePhoto(-1); movePhoto(1); document.querySelector('[data-close="detail-dialog"]').click(); }""")
            page.wait_for_timeout(260)
            check(not page.locator("#detail-dialog").evaluate("el=>el.open"), "V03 快速反向后关闭不会被迟到结果重新打开")

            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(SHOTS / "narrow-home.png"), full_page=True)
            result["screenshots"].append("validation/reports/visual-polish-v2/narrow-home.png")
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            check(not overflow, "V09 390px 首页没有横向溢出")
            page.emulate_media(reduced_motion="reduce")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.evaluate("void openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})")
            page.wait_for_function("window.__ourTimeApp.state.detail?.id===1")
            page.evaluate("movePhoto(-1)")
            page.wait_for_function("window.__ourTimeApp.state.detail?.id===2")
            check(page.evaluate("getComputedStyle(document.querySelector('#detail-img')).animationName") in ("none", ""), "V10 reduced-motion 保留准备后替换且取消装饰动画")
            page.screenshot(path=str(SHOTS / "viewer-reduced-motion.png"), full_page=True)
            result["screenshots"].append("validation/reports/visual-polish-v2/viewer-reduced-motion.png")
            browser.close()
        check(not errors, "定向页面无 pageerror 或未处理 rejection")
        result["passed"] = True
        return 0
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log.close()
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
