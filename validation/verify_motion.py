"""Live browser checks for delayed thumbnails, scrolling and reduced motion."""
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8765'

async def main():
    checks = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width':1500, 'height':1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        release = asyncio.Event()
        async def delay(route):
            await release.wait()
            await route.continue_()
        await page.route('**/api/thumb/**', delay)
        await page.goto(URL, wait_until='domcontentloaded')
        await page.wait_for_selector('.stream-entered')
        image = page.locator('.stream-entered img').first
        assert await image.evaluate('(e)=>getComputedStyle(e).opacity') == '0'
        assert await image.evaluate('(e)=>getComputedStyle(e.parentElement).backgroundColor') == 'rgba(0, 0, 0, 0)'
        assert await image.evaluate('(e)=>getComputedStyle(e.parentElement,"::after").opacity') == '0'
        checks.append('慢速图片请求期间只保留透明尺寸，不显示灰色占位框')
        release.set()
        await page.wait_for_function("document.querySelector('.stream-entered.stream-image-ready img')?.naturalWidth > 0")
        await page.wait_for_timeout(500)
        image = page.locator('.stream-entered.stream-image-ready img').first
        assert await image.evaluate('(e)=>getComputedStyle(e).opacity') == '1'
        assert await image.evaluate('(e)=>getComputedStyle(e.parentElement,"::after").opacity') == '0'
        checks.append('真实缩略图加载后自然淡入')
        await page.evaluate('scrollTo(0,200)')
        await page.wait_for_timeout(40)
        await page.evaluate('scrollTo(0,1000)')
        await page.wait_for_function("document.querySelector('#photo-grid').classList.contains('stream-fast')")
        assert await page.locator('#photo-grid').evaluate('(e)=>getComputedStyle(e).getPropertyValue("--stream-duration").trim()') == '100ms'
        await page.wait_for_function("!document.querySelector('#photo-grid').classList.contains('stream-fast')")
        checks.append('快速滚动缩短过渡并在停下后恢复')
        await page.emulate_media(reduced_motion='reduce')
        assert await page.locator('.stream-motion').first.evaluate('(e)=>getComputedStyle(e).transitionDuration') == '0s'
        checks.append('减少动态效果设置即时生效')
        await page.emulate_media(reduced_motion='no-preference')
        await page.evaluate('scrollTo(0,0)')
        await page.wait_for_timeout(650)
        await page.screenshot(path=str(ROOT/'validation/reports/21-motion-desktop.png'))
        assert not errors, errors
        checks.append('真实页面没有 JavaScript 错误')
        await browser.close()
    report = {'passed':True, 'checks':checks}
    (ROOT/'validation/reports/motion-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))

asyncio.run(main())
