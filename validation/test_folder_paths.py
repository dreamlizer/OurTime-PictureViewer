from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')


def extract(name: str) -> str:
    marker = f'function {name}('
    start = APP.find(marker)
    if start < 0:
        raise SystemExit(name + ' missing')
    end = APP.find('\nfunction ', start + 1)
    if end < 0:
        end = APP.find('\nasync function ', start + 1)
    return APP[start:end]


def run_js(source: str, expression: str):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        page.set_content('<script>' + source + '; window.__out = ' + expression + ';</script>')
        value = page.evaluate('window.__out')
        browser.close()
        return value


def main() -> int:
    source = '\n'.join([
        extract('folderSep'),
        extract('isFolderSlash'),
        extract('folderNormalize'),
        extract('folderParts'),
        extract('folderKey'),
        extract('folderParent'),
    ])
    samples = {
        'I:\\': 'I:\\',
        'I:': 'I:\\',
        'I:/A/B': 'I:\\A\\B',
        'I:\\A\\B\\': 'I:\\A\\B',
        'I:\\A/B\\C': 'I:\\A\\B\\C',
        'i:\\photos\\2025\\travel': 'I:\\photos\\2025\\travel',
    }
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        page.set_content('<script>' + source + '</script>')
        for raw, expected in samples.items():
            got = page.evaluate('path => folderNormalize(path)', raw)
            assert got == expected, (raw, got, expected)
        parts = page.evaluate('path => folderParts(path)', r'I:\photos\2025\travel')
        assert parts == ['I:', 'photos', '2025', 'travel'], parts
        parent = page.evaluate('path => folderParent(path)', r'I:\photos\2025\travel')
        assert parent == 'I:\\photos\\2025', parent
        key = page.evaluate('path => folderKey(path)', 'I:/photos/2025/')
        assert key == 'i:\\photos\\2025', key
        browser.close()
    app = APP
    assert 'function folderNormalize(' in app
    assert 'function isFolderSlash(' in app
    print('FOLDER_PATHS_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
