"""Bounded local renderer for a frozen photo-mat snapshot.

It intentionally has no database access and only receives the already selected
asset id plus a restricted, computed-style DOM snapshot from the open viewer.
"""
import io
import json
import re
from datetime import datetime
from html import unescape
from pathlib import Path
import os

from PIL import Image, ImageOps

MAX_PIXELS = 40_000_000
MAX_EDGE = 12_000
SUPPORTED = {"JPEG", "PNG"}
OUTPUT_FORMATS = {"jpeg", "png"}
_BAD = re.compile(
    r"<(?:script|iframe|object|form|link|meta|style)\b"
    r"|\bon\w+\s*=|(?:javascript|file|https?|data)\s*:"
    r"|@import|expression\s*\(|-moz-binding|behavior\s*:",
    re.I,
)
_STYLE_NAME = re.compile(r"^--(?:face-(?:font-size|font-family|text-color|bg-color|bg-opacity|bg-rgba|radius|padding-x|padding-y|shadow|label-(?:[sml]-image|image(?:-(?:vertical|horizontal))?))|viewer-signature-h|viewer-image-inset|signature-tone)$")
_LABEL_URL = re.compile(r"^/api/face-label-bg/[1-9]\.png$", re.I)
_URL_VALUE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
_CAMERA_BRANDS = (
    ("eastman kodak", "kodak"),
    ("fujifilm", "fujifilm"),
    ("panasonic", "panasonic"),
    ("hasselblad", "hasselblad"),
    ("motorola", "motorola"),
    ("microsoft", "microsoft"),
    ("oneplus", "oneplus"),
    ("olympus", "olympus"),
    ("huawei", "huawei"),
    ("honor", "honor"),
    ("samsung", "samsung"),
    ("xiaomi", "xiaomi"),
    ("redmi", "xiaomi"),
    ("iphone", "apple"),
    ("apple", "apple"),
    ("pixel", "google"),
    ("google", "google"),
    ("nikon", "nikon"),
    ("canon", "canon"),
    ("sony", "sony"),
    ("lumix", "panasonic"),
    ("pentax", "pentax"),
    ("leica", "leica"),
    ("ricoh", "ricoh"),
    ("meizu", "meizu"),
    ("oppo", "oppo"),
    ("vivo", "vivo"),
    ("nokia", "nokia"),
    ("gopro", "gopro"),
    ("kodak", "kodak"),
    ("casio", "casio"),
    ("sigma", "sigma"),
    ("dji", "dji"),
)


def _filename_datetime(value):
    text = str(value or "").strip()
    match = re.match(
        r"^(\d{4})[:-](\d{2})[:-](\d{2})[T ](\d{2}):(\d{2})",
        text,
    )
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups()))
    except ValueError:
        return None


def _camera_make(metadata):
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    if not isinstance(metadata, dict):
        return ""
    exiftool = metadata.get("ExifTool")
    if isinstance(exiftool, dict):
        for key, value in exiftool.items():
            if key.split(":")[-1].casefold() == "make" and value:
                return str(value).strip()
    ifd0 = metadata.get("IFD0")
    if isinstance(ifd0, dict) and ifd0.get("Make"):
        return str(ifd0["Make"]).strip()
    return ""


def _camera_brand(metadata, camera):
    make = _camera_make(metadata)
    combined = f"{make} {camera or ''}".casefold()
    for needle, brand in _CAMERA_BRANDS:
        if needle in combined:
            return brand
    if make:
        match = re.search(r"[a-z0-9\u4e00-\u9fff]+", make.casefold())
        if match:
            return match.group(0)
    return ""


def build_annotated_export_filename(asset, original, output_format):
    captured = None
    if str(asset.get("date_source") or "") != "文件修改时间参考":
        captured = _filename_datetime(asset.get("captured_at"))
    if captured is None:
        try:
            captured = datetime.fromtimestamp(Path(original).stat().st_ctime)
        except OSError:
            captured = _filename_datetime(asset.get("created_at"))
    if captured is None:
        captured = _filename_datetime(asset.get("created_at")) or datetime.now()
    brand = _camera_brand(asset.get("metadata"), asset.get("camera"))
    extension = "jpg" if output_format == "jpeg" else "png"
    stem = captured.strftime("%Y-%m-%d-%H%M")
    return f"{stem}{'-' + brand if brand else ''}.{extension}"


def _allowed_label_url(value):
    candidate = unescape(str(value).strip())
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
        candidate = candidate[1:-1].strip()
    return bool(_LABEL_URL.fullmatch(candidate))


def _export_url_allowed(url, base_url, aid):
    if url in {"about:blank"}:
        return True
    if url.startswith("data:") or url.startswith("file:"):
        return False
    if not str(url).startswith(str(base_url)):
        return False
    path = str(url)[len(str(base_url)):].split("?", 1)[0]
    if path in {"", "api/health", f"api/original/{int(aid)}"}:
        return True
    if _LABEL_URL.fullmatch("/" + path):
        return True
    if path.startswith("vendor/fonts/") and ".." not in path:
        return True
    return False


def _snapshot_html(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("缺少冻结版式")
    if snapshot.get("snapshot_version") != 2:
        raise ValueError("导出版式版本已更新，请刷新页面后重试")
    mat = snapshot.get("mat_html")
    attrs = snapshot.get("dialog_attrs", {})
    if not isinstance(mat, str) or not 100 < len(mat) <= 300_000 or _BAD.search(mat):
        raise ValueError("冻结版式不符合导出安全限制")
    if not re.search(r'<figure\b[^>]*\bid=["\']photo-mat["\']', mat, re.I):
        raise ValueError("冻结版式缺少照片成品区域")
    if not re.search(r'<img\b[^>]*\bid=["\']detail-img["\']', mat, re.I):
        raise ValueError("冻结版式缺少原图占位")
    for _, value in _URL_VALUE.findall(mat):
        if not _allowed_label_url(value):
            raise ValueError("冻结版式包含不允许的图片资源")
    if not isinstance(attrs, dict) or len(attrs) > 24:
        raise ValueError("冻结版式属性不符合导出安全限制")
    cleaned = {
        str(k): str(v)[:300]
        for k, v in attrs.items()
        if re.fullmatch(r"data-[a-z0-9-]+", str(k))
    }
    style = str(snapshot.get("dialog_style", ""))[:4000]
    for declaration in filter(None, (part.strip() for part in style.split(";"))):
        if ":" not in declaration:
            raise ValueError("冻结版式样式不符合导出安全限制")
        name, value = (part.strip() for part in declaration.split(":", 1))
        if not _STYLE_NAME.fullmatch(name) or len(value) > 500:
            raise ValueError("冻结版式样式不符合导出安全限制")
        for _, url in _URL_VALUE.findall(value):
            if not _allowed_label_url(url):
                raise ValueError("冻结版式样式不符合导出安全限制")
        if re.search(r"(?:javascript|file|https?)\s*:", value, re.I):
            raise ValueError("冻结版式样式不符合导出安全限制")
    pairs = [f'{k}="{v.replace(chr(34), "")}"' for k, v in cleaned.items()]
    if style:
        pairs.append(f'style="{style.replace(chr(34), "")}"')
    return mat, " ".join(pairs)


def render_annotated_image(
    *,
    original: Path,
    snapshot: dict,
    base_url: str,
    web_root: Path,
    aid: int,
    output_format: str = "jpeg",
) -> bytes:
    """Render a v2 frozen viewer snapshot at the original photo pixel scale."""
    output_format = str(output_format or "jpeg").strip().lower()
    if output_format == "jpg":
        output_format = "jpeg"
    if output_format not in OUTPUT_FORMATS:
        raise ValueError("导出格式只支持 JPEG 或 PNG")
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
    mat_h = float(geometry.get("mat_height", 0))
    if not (
        40 <= css_w <= 12000
        and 40 <= css_h <= 12000
        and css_w <= mat_w <= 16000
        and css_h <= mat_h <= 18000
    ):
        raise ValueError("冻结版式尺寸无效")
    scale = source_w / css_w
    if not (0.25 <= scale <= 32) or abs(source_h / css_h - scale) > 0.02:
        raise ValueError("原图与冻结版式比例不一致")
    # Inline computed styles are authoritative. Project stylesheets remain
    # available only for the same controlled local webfonts and SVG defaults.
    css = "\n".join(
        (web_root / name).read_text(encoding="utf-8")
        for name in ("style.css", "appearance.css", "viewer-overrides.css", "face-labels.css")
    )
    html = f'''<!doctype html><html lang="zh-CN"><head><base href="{base_url}"><style>{css}</style>
    <style>html,body{{margin:0;padding:0;background:transparent;overflow:hidden}}body{{zoom:{scale}}}#detail-dialog{{display:block!important;position:relative!important;width:max-content!important;height:max-content!important;overflow:visible!important;background:transparent!important}}#detail-dialog .dialog-shell,#detail-dialog .detail-image,#detail-dialog .viewer-body,#detail-dialog .viewer-stage,#detail-dialog #image-viewport,#detail-dialog #image-canvas{{position:relative!important;inset:auto!important;width:max-content!important;height:max-content!important;overflow:visible!important;padding:0!important}}#detail-dialog #photo-mat{{margin:0!important;width:{mat_w}px!important;height:{mat_h}px!important}}#detail-dialog #detail-img{{width:{css_w}px!important;height:{css_h}px!important;display:block!important}}#detail-dialog .viewer-photo-close,#detail-dialog .photo-favorite,#detail-dialog .photo-place-map,#detail-dialog .photo-export-control,#detail-dialog .face-hover-guide,#detail-dialog .face-hover-box,#detail-dialog .signature-switch{{display:none!important}}</style></head><body><dialog id="detail-dialog" {dialog_attrs}><div class="dialog-shell"><div class="detail-image"><div class="viewer-body"><div class="viewer-stage"><div id="image-viewport"><div id="image-canvas">{mat}</div></div></div></div></div></div></dialog></body></html>'''
    # Force the controlled original endpoint after sanitised frozen markup is in place.
    html = re.sub(r'<img\b[^>]*\bid=["\']detail-img["\'][^>]*>', f'<img id="detail-img" src="{base_url}api/original/{aid}" alt="照片">', html, count=1, flags=re.I)
    try:
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise RuntimeError("本机未安装可选的 Playwright/Chromium 渲染组件") from exc
    with sync_playwright() as p:
        launch_kwargs = {}
        chromium = os.environ.get("PHOTO_CHROMIUM")
        if chromium:
            launch_kwargs["executable_path"] = chromium
        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page(
                viewport={
                    "width": max(1, round(mat_w * scale) + 2),
                    "height": max(1, round(mat_h * scale) + 2),
                },
                device_scale_factor=1,
            )
            try:
                page.route(
                    "**/*",
                    lambda route: route.continue_()
                    if _export_url_allowed(route.request.url, base_url, aid)
                    else route.abort(),
                )
                # Give the frozen document the same localhost origin as the
                # viewer so bundled webfonts are not blocked as cross-origin.
                page.goto(f"{base_url}api/health", wait_until="load", timeout=20_000)
                page.set_content(html, wait_until="load", timeout=20_000)
                page.wait_for_function(
                    """() => {
                      const image=document.getElementById('detail-img');
                      return image && image.complete && image.naturalWidth>0;
                    }""",
                    timeout=20_000,
                )
            except PlaywrightTimeoutError as exc:
                raise RuntimeError("原图未能读取或渲染，未生成导出文件") from exc
            page.wait_for_function("document.fonts && document.fonts.status === 'loaded'", timeout=20_000)
            missing_fonts = page.evaluate(
                """() => [...new Set(
                  [...document.querySelectorAll('.face-name:not(.unnamed)')]
                    .map(node => getComputedStyle(node).fontFamily.match(/^\\s*(?:"[^"]+"|'[^']+'|[^,]+)/)?.[0]?.trim())
                    .filter(Boolean)
                )].filter(family => !document.fonts.check(`16px ${family}`, '人物'))"""
            )
            if missing_fonts:
                raise RuntimeError("人名标签字体未能加载，未生成不一致的导出文件")
            box = page.locator("#photo-mat").bounding_box()
            if not box or box["width"] <= 0 or box["height"] <= 0:
                raise RuntimeError("冻结版式无法完成渲染")
            if box["width"] * box["height"] > MAX_PIXELS:
                raise ValueError("带底签成品超过 40MP 上限，未自动缩小")
            rendered = page.locator("#photo-mat").screenshot(
                type="png", animations="disabled", timeout=30_000
            )
        finally:
            browser.close()
    if output_format == "png":
        return rendered
    with Image.open(io.BytesIO(rendered)) as flattened:
        rgb = Image.new("RGB", flattened.size, "white")
        if flattened.mode == "RGBA":
            rgb.paste(flattened, mask=flattened.getchannel("A"))
        else:
            rgb.paste(flattened.convert("RGB"))
        output = io.BytesIO()
        rgb.save(output, format="JPEG", quality=92, subsampling=0, optimize=True)
        return output.getvalue()


def render_annotated_png(*, original: Path, snapshot: dict, base_url: str, web_root: Path, aid: int) -> bytes:
    """Compatibility wrapper for callers that explicitly require lossless PNG."""
    return render_annotated_image(
        original=original,
        snapshot=snapshot,
        base_url=base_url,
        web_root=web_root,
        aid=aid,
        output_format="png",
    )
