"""Bounded local renderer for a frozen photo-mat snapshot.

It intentionally has no database access and only receives the already selected
asset id plus a restricted DOM fragment from the open viewer.
"""
import base64
import io
import re
from pathlib import Path

from PIL import Image, ImageOps

MAX_PIXELS = 40_000_000
MAX_EDGE = 12_000
SUPPORTED = {"JPEG", "PNG"}
_BAD = re.compile(r"<(?:script|iframe|object|form)\b|\bon\w+\s*=|(?:javascript|file|https?)\s*:", re.I)


def _snapshot_html(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("缺少冻结版式")
    mat = snapshot.get("mat_html")
    attrs = snapshot.get("dialog_attrs", {})
    if not isinstance(mat, str) or not 100 < len(mat) <= 100_000 or _BAD.search(mat):
        raise ValueError("冻结版式不符合导出安全限制")
    if not isinstance(attrs, dict) or len(attrs) > 24:
        raise ValueError("冻结版式属性不符合导出安全限制")
    cleaned = {str(k): str(v)[:300] for k, v in attrs.items()
               if str(k).startswith("data-")}
    style = str(snapshot.get("dialog_style", ""))[:4000]
    if style and ("url(" in style.lower() or not re.fullmatch(r"[\w\s:#(),.%+'\"/;\-]+", style)):
        raise ValueError("冻结版式样式不符合导出安全限制")
    pairs = [f'{k}="{v.replace(chr(34), "")}"' for k, v in cleaned.items()]
    if style:
        pairs.append(f'style="{style.replace(chr(34), "")}"')
    return mat, " ".join(pairs)


def render_annotated_png(*, original: Path, snapshot: dict, base_url: str, web_root: Path, aid: int) -> bytes:
    """Render one source photo at its corrected original pixel scale to PNG."""
    with Image.open(original) as opened:
        if opened.format not in SUPPORTED or opened.mode not in {"RGB", "RGBA"}:
            raise ValueError("首版仅支持普通静态 JPG/JPEG 或 8 位 RGB/RGBA PNG")
        picture = ImageOps.exif_transpose(opened)
        source_w, source_h = picture.size
    if source_w * source_h > MAX_PIXELS or max(source_w, source_h) > MAX_EDGE:
        raise ValueError("原图超过首版导出上限（40MP 或单边 12000px），未自动缩小")
    mat, dialog_attrs = _snapshot_html(snapshot)
    geometry = snapshot.get("geometry", {})
    css_w = float(geometry.get("image_width", 0))
    css_h = float(geometry.get("image_height", 0))
    mat_w = float(geometry.get("mat_width", 0))
    if not (40 <= css_w <= 12000 and 40 <= css_h <= 12000 and css_w <= mat_w <= 16000):
        raise ValueError("冻结版式尺寸无效")
    scale = source_w / css_w
    if not (0.25 <= scale <= 32) or abs(source_h / css_h - scale) > 0.02:
        raise ValueError("原图与冻结版式比例不一致")
    # The same versioned stylesheet and local webfonts are used; no external URL
    # is supplied by the browser client.
    css = (web_root / "style.css").read_text(encoding="utf-8") + "\n" + (web_root / "appearance.css").read_text(encoding="utf-8")
    html = f'''<!doctype html><html lang="zh-CN"><head><base href="{base_url}"><style>{css}</style>
    <style>html,body{{margin:0;padding:0;background:transparent;overflow:hidden}}#detail-dialog{{display:block!important;position:relative!important;width:max-content!important;height:max-content!important;overflow:visible!important;background:transparent!important}}#detail-dialog .dialog-shell,#detail-dialog .detail-image,#detail-dialog .viewer-body,#detail-dialog .viewer-stage,#detail-dialog #image-viewport,#detail-dialog #image-canvas{{position:relative!important;inset:auto!important;width:max-content!important;height:max-content!important;overflow:visible!important;padding:0!important}}#detail-dialog #photo-mat{{margin:0!important;width:{mat_w}px!important}}#detail-dialog #detail-img{{width:{css_w}px!important;height:{css_h}px!important;display:block!important}}#detail-dialog #photo-signature{{display:flex!important}}#detail-dialog .viewer-photo-close,#detail-dialog .photo-favorite,#detail-dialog .photo-place-map,#detail-dialog .face-hover-guide,#detail-dialog .face-hover-box{{display:none!important}}</style></head><body><dialog id="detail-dialog" {dialog_attrs}><div class="dialog-shell"><div class="detail-image"><div class="viewer-body"><div class="viewer-stage"><div id="image-viewport"><div id="image-canvas">{mat}</div></div></div></div></div></div></dialog></body></html>'''
    # Force the controlled original endpoint after sanitised frozen markup is in place.
    html = re.sub(r'<img\b[^>]*\bid=["\']detail-img["\'][^>]*>', f'<img id="detail-img" src="{base_url}api/original/{aid}" alt="照片">', html, count=1, flags=re.I)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("本机未安装可选的 Playwright/Chromium 渲染组件") from exc
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": max(1, round(mat_w)), "height": max(1, round(css_h + 500))}, device_scale_factor=scale)
            page.set_content(html, wait_until="load", timeout=20_000)
            page.locator("#detail-img").wait_for(state="visible", timeout=20_000)
            page.wait_for_function("document.fonts && document.fonts.status === 'loaded'", timeout=20_000)
            box = page.locator("#photo-mat").bounding_box()
            if not box or box["width"] <= 0 or box["height"] <= 0:
                raise RuntimeError("冻结版式无法完成渲染")
            if (box["width"] * scale) * (box["height"] * scale) > MAX_PIXELS:
                raise ValueError("带底签成品超过 40MP 上限，未自动缩小")
            return page.locator("#photo-mat").screenshot(type="png", animations="disabled", timeout=30_000)
        finally:
            browser.close()
