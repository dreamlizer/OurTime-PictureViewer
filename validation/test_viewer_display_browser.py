"""P5 minimal real entry check: load live index, assert controller + open photo path exists."""
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/"
errors = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(500)
    has_display = page.evaluate("() => Boolean(window.OurTimeViewerDisplay && window.__ourTimeViewerDisplay)")
    state = page.evaluate("() => window.__ourTimeViewerDisplay && window.__ourTimeViewerDisplay.debugState()")
    scripts = page.evaluate("() => [...document.scripts].map(s => s.src).filter(Boolean)")
    opened = False
    after = closed = None
    photo_id = 0
    try:
        page.evaluate("() => window.OurTimeContinuity ? window.OurTimeContinuity.navigate('timeline') : window.setView('timeline')")
        page.wait_for_timeout(1500)
        page.locator("#photo-grid [data-photo]:not([data-photo='shelf'])").first.wait_for(timeout=15000)
        page.locator("#photo-grid [data-photo]:not([data-photo='shelf'])").first.click()
        page.wait_for_selector("#detail-dialog[open]", timeout=10000)
        page.wait_for_timeout(1000)
        after = page.evaluate("() => window.__ourTimeViewerDisplay && window.__ourTimeViewerDisplay.debugState()")
        photo_id = page.evaluate("() => Number(document.querySelector('#detail-dialog').dataset.photoId)||0")
        opened = bool(after and after.get("lifecycle") in ("open", "closing") and photo_id)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        closed = page.evaluate("() => window.__ourTimeViewerDisplay && window.__ourTimeViewerDisplay.debugState()")
    except Exception as exc:
        errors.append(f"open-photo: {exc}")
    browser.close()

print("DISPLAY_MOUNTED", has_display)
print("STATE", state)
print("AFTER_OPEN", after, "photo_id", photo_id)
print("AFTER_CLOSE", closed)
print("SCRIPTS_HAS_DISPLAY", any("viewer-display.js" in s for s in scripts))
print("PAGE_ERRORS", errors[:10])
real_errors = [e for e in errors if not e.startswith("open-photo:")]
if not has_display:
    raise SystemExit("ENTRY_FAIL display controller missing")
if not opened:
    raise SystemExit("ENTRY_FAIL could not open photo")
if real_errors:
    raise SystemExit("ENTRY_FAIL page errors " + str(real_errors[:5]))
if not closed or closed.get("lifecycle") != "closed":
    raise SystemExit("ENTRY_FAIL close did not finish")
print("ENTRY_OK")
