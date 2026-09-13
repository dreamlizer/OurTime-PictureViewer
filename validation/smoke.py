"""快速查错：静态防呆 + 现役接口冒烟。

日常小改默认跑这个，目标几秒内结束。
不扫描、不启新服务、不停正式识别、不跑 Playwright。
现役 8765 接口忙就跳过接口检查，不算失败。
入库 / 人物 / 扫描合同仍用 validation/validate.py。
"""
from pathlib import Path
import json, re, time, urllib.error, urllib.request

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
REPORT = ROOT / "validation" / "reports" / "smoke.json"
LIVE = "http://127.0.0.1:8765"
REQUIRED = [
    "esc", "prettyPlace", "personLabel", "basename", "fmt", "api", "action",
    "showDialog", "toast", "dateSource", "readableError", "renderPhoto", "openPerson", "openQuickName",
]
CROSS = {
    "viewer.js": ["esc", "prettyPlace", "basename", "fmt", "api", "action", "toast", "hasPhotoCoordinates", "showPhotoPlaceMap"],
    "waterfall.js": ["esc", "basename", "fmt", "api", "action"],
}
passed = []
skipped = []
started = time.perf_counter()


def fail(msg):
    raise AssertionError(msg)


def ok(msg):
    passed.append(msg)
    print("PASS", msg, flush=True)


def skip(msg):
    skipped.append(msg)
    print("SKIP", msg, flush=True)


def read(path):
    return path.read_text(encoding="utf-8")


def first_def(text, name):
    pats = [
        r"function\s+%s\s*\(" % re.escape(name),
        r"async\s+function\s+%s\s*\(" % re.escape(name),
        r"(?:const|let|var)\s+%s\s*=" % re.escape(name),
    ]
    hits = [m.start() for pat in pats for m in re.finditer(pat, text)]
    return min(hits) if hits else None


def first_use(text, name):
    hits = []
    for match in re.finditer(r"\b%s\s*\(" % re.escape(name), text):
        prefix = text[max(0, match.start() - 16):match.start()]
        if re.search(r"(?:async\s+)?function\s+$", prefix):
            continue
        hits.append(match.start())
    return hits[0] if hits else None


def visible_undefined(source):
    hits = []
    for n, line in enumerate(source.splitlines(), 1):
        if "undefined" not in line:
            continue
        if re.search(r"typeof|void |!==\s*undefined|!=\s*undefined|===\s*undefined|==\s*undefined|undefined\s*!==|undefined\s*!=|undefined\s*===|undefined\s*==", line):
            continue
        if re.search(r"(textContent|innerHTML|innerText)\s*=.*undefined", line):
            hits.append("L%s: %s" % (n, line.strip()[:160]))
            continue
        if "'undefined'" in line or '"undefined"' in line or "${undefined}" in line:
            hits.append("L%s: %s" % (n, line.strip()[:160]))
    return hits


def req(path, timeout=4):
    with urllib.request.urlopen(LIVE + path, timeout=timeout) as response:
        return json.load(response)


def main():
    html = read(WEB / "index.html")
    app = read(WEB / "app.js")
    viewer = read(WEB / "viewer.js")
    appearance = read(WEB / "appearance.css")
    viewer_overrides = read(WEB / "viewer-overrides.css")
    home_ui = read(WEB / "home-query-ui.js")
    home_init = read(WEB / "home-query-init.js")
    backend = read(ROOT / "app.py")
    browse_queries = read(ROOT / "browse_queries.py")
    waterfall = read(WEB / "waterfall.js")
    sources = {"app.js": app, "viewer.js": viewer, "viewer-overrides.css": viewer_overrides, "waterfall.js": waterfall, "index.html": html}

    order = re.findall(r'src="/(app\.js|viewer\.js|waterfall\.js)"', html)
    if order != ["app.js", "viewer.js", "waterfall.js"]:
        fail("index.html 脚本顺序应为 app.js -> viewer.js -> waterfall.js，实际 %s" % order)
    ok("index.html 先加载 app.js，再加载 viewer.js / waterfall.js")

    for name in REQUIRED:
        defined = first_def(app, name)
        if defined is None:
            fail("app.js 缺少 %s()" % name)
        used = first_use(app, name)
        if used is not None and defined > used:
            fail("app.js 里 %s() 在首次调用之后才定义" % name)
    ok("app.js 已定义 esc / prettyPlace / personLabel 等公共函数，且先定义再调用")

    for filename, names in CROSS.items():
        text = sources[filename]
        for name in names:
            if first_use(text, name) is None:
                continue
            if first_def(text, name) is not None:
                continue
            if first_def(app, name) is None:
                fail("%s 用到 %s()，但 app.js 没有定义" % (filename, name))
    ok("viewer.js / waterfall.js 用到的公共函数由 app.js 提供")

    if '<option value="ivory">素笺</option>' not in viewer:
        fail("人名标签缺少素笺主题名称")
    if '<option value="classic">默认</option>' not in viewer:
        fail("人名标签原始主题没有改名为默认")
    if '<option value="tea">茶棕</option>' not in viewer:
        fail("人名标签第三主题没有接入茶棕")
    classic_match = re.search(r"classic:\s*\{(?P<body>.*?)\n\s*\},\s*\n\s*ivory:", viewer, re.S)
    classic = classic_match.group("body") if classic_match else ""
    if not all(value in classic for value in (
        "fontFamily:'kai'",
        "backgroundOpacity:.5",
        "radius:10",
        "paddingX:5",
    )):
        fail("默认主题没有使用楷体、50% 底色、圆角和收窄后的左右留白")
    if (
        '<option value="other">其他…</option>' not in viewer
        or "queryLocalFonts" not in viewer
        or "local-font-preview-label" not in viewer
    ):
        fail("字体菜单缺少本机其他字体选择或预览")
    if not all(value in viewer for value in (
        'id="face-unnamed-marker"',
        '<option value="plus">默认加号</option>',
        '<option value="pulse">呼吸绿点</option>',
        '<option value="ring">静态绿环</option>',
        "FACE_LABEL_OVERLAP_LIMIT=.12",
        "FACE_LABEL_FACE_OVERLAP_LIMIT=.25",
        "rectOverlapRatio",
        "maxFaceRatio",
        "labelOverlapRatio",
    )):
        fail("待命名标记或标签重叠比例防碰撞机制缺失")
    if (
        'id="quick-ignore-person"' not in html
        or "/api/people/'+id+'/ignore" not in app
        or "applyIgnoredPersonToOpenPhoto" not in app
        or "function visibleFaces(photo){return (photo&&photo.faces||[]);}" not in viewer
        or "face-name.unnamed.passerby" not in viewer_overrides
    ):
        fail("照片快捷命名缺少路人操作，或路人淡色加号显示规则缺失")
    quick_dialog = html.split('<dialog aria-label="快速命名"', 1)[-1].split('</dialog>', 1)[0]
    if (
        quick_dialog.count('data-close="quick-name-dialog"') != 1
        or 'id="quick-name-confirm" class="primary quick-main-action">确认姓名</button>' not in quick_dialog
        or 'id="quick-merge-title">合并到已有姓名</h2>' not in quick_dialog
        or 'id="quick-merge-submit" class="secondary quick-main-action">合并到所选人物</button>' not in quick_dialog
        or 'id="quick-merge-toggle"' in quick_dialog
        or 'id="quick-merge-panel" class="quick-merge-panel" hidden' in quick_dialog
        or '>取消</button>' in quick_dialog
        or ".quick-name-dialog .quick-main-action" not in appearance
        or ".quick-name-dialog { width: 344px" not in appearance
        or "peopleQuery({ignored:0,named:1,q:query,limit:40})" not in app
    ):
        fail("照片快捷命名布局退化，或合并候选没有限定为已命名人物")
    if not all(value in viewer for value in (
        '<option value="top">优先上方</option>',
        '<option value="bottom">优先下方</option>',
        'data-face-id=',
        'face-hover-guide',
        'face-action-popover',
        'face-action-photos',
        'face-action-edit-name',
        "viewerCaptureEntityWrite('photo'",
        "queueEntityWrite('person',personId,()=>rememberPersonName(",
        'photo-people-popover',
        '/passersby`,',
    )) or "@app.post('/api/photos/{aid}/passersby')" not in backend:
        fail("照片人物缺少上下标签位置、悬停指向、单张纠错或批量路人能力")
    if not all(value in home_ui for value in (
        'const filterButton',
        "filterButton('group', '合影人数')",
        'data-ot="group-min"',
        'data-ot="group-max"',
        "'upto' + max",
        'adapter.applyGroup',
    )) or "applyGroup: async (group" not in home_init or "re.fullmatch(r'(\\d+)-(\\d+)'" not in browse_queries:
        fail("全部照片或合影结果页缺少可与人物叠加的合影人数区间筛选")
    if not all(value in waterfall for value in (
        'data-group-exclude-arm',
        'data-group-exclude-confirm',
        'data-group-exclude-cancel',
        "state.view==='timeline'",
    )) or not all(value in app for value in (
        "'在合影页排除显示'",
        "'在全部照片页排除显示'",
        'display_only:true',
        'await streamRemovePhoto(id,Number(card.dataset.position))',
    )) or not all(value in waterfall for value in (
        'async function streamRemovePhotos',
        'async function streamRemovePhoto',
        "grid.classList.add('stream-reflowing')",
    )) or 'locallyRemoved=await streamRemovePhotos(body.ids)' not in app or 'display_only:bool=False' not in backend:
        fail("全部照片或合影详情缺少局部退场的仅排除显示入口，或仍可能整页重载、清理识别缓存")
    if not all(value in html for value in (
        'id="photo-place-map"',
        'id="places-photo-back"',
        'id="places-heading"',
        'id="places-photo-edit"',
        'id="photo-place-editor"',
        'id="photo-place-samples"',
    )) or not all(value in app for value in (
        "const PHOTO_PLACE_FACTOR=.15",
        "state.placeCluster.clearLayers()",
        "data-map-photo=",
        "showPhotoPlaceMap",
        "radiusM:PHOTO_PLACE_RADIUS_DEFAULT",
        "/nearby?radius_m=",
        "/nearby-place",
        "function wgs84ToGcj02",
        "function gcj02ToWgs84",
        "function placeGpsBounds",
        "placeMapPoint(photo.latitude,photo.longitude)",
    )) or not all(value in backend for value in (
        "def nearby_photos(",
        "def set_nearby_place(",
        "radius_m:int=Field(default=100,ge=1,le=500)",
    )) or "syncPhotoPlaceButton(state.detail)" not in viewer:
        fail("单张照片地图缺少 0.15 倍聚焦、GCJ/WGS 坐标适配、500 米范围复核、九张预览或返回大图链路")
    ok("单张照片地图已接入 GCJ/WGS 坐标适配、500 米内复核、九张预览和完整大图浏览")
    if not all(path in viewer for path in (
        "/api/face-label-bg/1.png",
        "/api/face-label-bg/2.png",
        "/api/face-label-bg/3.png",
        "/api/face-label-bg/4.png",
        "/api/face-label-bg/5.png",
        "/api/face-label-bg/6.png",
    )):
        fail("素笺或茶棕主题没有固定映射对应 PNG")
    if "faceLabelProfile" not in viewer or "[...normalized].length" not in viewer:
        fail("素笺主题缺少按 Unicode 字符数选择 S/M/L 的规则")
    if not all(value in viewer for value in (
        "fontFamily:'ma-shan-zheng'",
        "TEA_FACE_FONT_OPTIONS",
        "ma-shan-zheng",
        "long-cang",
        "liu-jian-mao-cao",
    )):
        fail("茶棕主题没有固定接入三款毛笔字体或默认 Ma Shan Zheng")
    if not all(value in viewer for value in (
        "fontFamily:'zcool-xiaowei'",
        "ACCENT_FACE_FONT_OPTIONS",
        "zcool-xiaowei",
        "noto-serif-sc",
        "zhi-mang-xing",
    )):
        fail("暗朱主题没有固定接入三款题签字体或默认 ZCOOL XiaoWei")
    overrides = read(WEB / "viewer-overrides.css")
    if "prefers-reduced-motion:reduce" not in overrides:
        fail("呼吸绿点没有尊重系统减少动态效果设置")
    ok("默认主题、图片标签、人物指向与纠错、批量路人和防碰撞机制已接入")

    font_path = WEB / "vendor" / "fonts" / "lxgw-wenkai-screen" / "LXGWWenKaiGBScreen.ttf"
    license_path = font_path.with_name("OFL.txt")
    if not font_path.is_file() or font_path.stat().st_size != 26037854:
        fail("素笺主题缺少完整的霞鹜文楷屏幕阅读版字体文件")
    if not license_path.is_file() or "SIL OPEN FONT LICENSE" not in read(license_path):
        fail("内置字体缺少 OFL 授权文件")
    if (
        '@font-face' not in overrides
        or 'font-family:"LXGW WenKai GB Screen"' not in overrides
        or "/vendor/fonts/lxgw-wenkai-screen/LXGWWenKaiGBScreen.ttf" not in overrides
        or "/vendor/fonts/lxgw-wenkai-screen/LXGWWenKaiGBScreen.ttf" not in html
    ):
        fail("素笺主题没有预加载并使用项目内置屏幕阅读版文楷")
    ok("素笺主题内置屏幕阅读版文楷及 OFL 授权")

    brush_fonts = {
        "ma-shan-zheng": "MaShanZheng-Regular.ttf",
        "long-cang": "LongCang-Regular.ttf",
        "liu-jian-mao-cao": "LiuJianMaoCao-Regular.ttf",
    }
    for directory, filename in brush_fonts.items():
        path = WEB / "vendor" / "fonts" / directory / filename
        license_file = path.with_name("OFL.txt")
        if not path.is_file() or path.stat().st_size < 4_000_000:
            fail("茶棕主题缺少完整毛笔字体：%s" % filename)
        if not license_file.is_file() or "SIL OPEN FONT LICENSE" not in read(license_file):
            fail("茶棕毛笔字体缺少 OFL 授权：%s" % filename)
        if ("/vendor/fonts/%s/%s" % (directory, filename)) not in overrides:
            fail("茶棕毛笔字体没有通过本地 @font-face 接入：%s" % filename)
    ok("茶棕主题内置三款毛笔字体及各自 OFL 授权")

    accent_fonts = {
        "zcool-xiaowei": "ZCOOLXiaoWei-Regular.ttf",
        "noto-serif-sc": "NotoSerifSC-wght.ttf",
        "zhi-mang-xing": "ZhiMangXing-Regular.ttf",
    }
    for directory, filename in accent_fonts.items():
        path = WEB / "vendor" / "fonts" / directory / filename
        license_file = path.with_name("OFL.txt")
        if not path.is_file() or path.stat().st_size < 4_000_000:
            fail("暗朱主题缺少完整题签字体：%s" % filename)
        if not license_file.is_file() or "SIL OPEN FONT LICENSE" not in read(license_file):
            fail("暗朱题签字体缺少 OFL 授权：%s" % filename)
        if ("/vendor/fonts/%s/%s" % (directory, filename)) not in overrides:
            fail("暗朱题签字体没有通过本地 @font-face 接入：%s" % filename)
    ok("暗朱主题内置三款题签字体及各自 OFL 授权")

    for name, text in sources.items():
        bad = visible_undefined(text)
        if bad:
            fail("%s 可能把 undefined 写进可见文案：%s" % (name, bad[0]))
    ok("页面源码没有把 undefined 当成可见文案")

    combined = app + "\n" + waterfall
    if "personLabel is not defined" in app or "Person label is not defined" in app:
        fail("app.js 仍含 personLabel is not defined")
    compact = combined.replace(" ", "")
    if "$('#no-results').hidden=true" not in compact and not re.search(r"\$\('#no-results'\)\.hidden\s*=\s*true", combined):
        fail("切换照片流时没有立刻隐藏 #no-results，加载中可能闪出没有照片")
    if "正在加载照片" not in app or "正在加载照片" not in waterfall:
        fail("照片流缺少加载中文案")
    ok("切到时间/地点时先显示加载中，不先说没有照片")

    if (
        "if(loading){\n  clearViewerImage();" not in viewer
        or "#detail-dialog.is-loading #image-viewport" not in viewer_overrides
        or "#detail-dialog.is-loading .viewer-tools" not in viewer_overrides
        or "#detail-dialog.is-loading .detail-info" not in viewer_overrides
    ):
        fail("大图切换加载态没有清空旧照片，或仍会露出旧图控件")
    ok("大图切换时只保留加载提示，不显示上一张照片及控件")

    if (
        'placeholder="搜索已记录地点"' not in html
        or 'class="place-search-results"' not in html
        or "没有找到已记录的地点" not in app
        or "#places-view .view-chrome { z-index: 1100; }" not in appearance
        or "isolation: isolate" not in appearance
        or "默认北京地图，可缩小到全国和世界" in html
    ):
        fail("地点页标题层级、精简文案或已记录地点搜索反馈没有完整接入")
    ok("地点标题压住地图，搜索已记录地点时在输入框下即时反馈")

    for item in ["photo-grid", "people-grid", "person-dialog", "person-title", "person-notice", "person-faces-status", "merge-person", "quick-name-dialog", "quick-name-form", "quick-ignore-person", "detail-dialog", "photo-favorite", "favorites-count", "no-results", "empty", "stream-status", "places-view", "timeline-tools", "groups-view", "groups-list"]:
        if ('id="%s"' % item) not in html:
            fail("index.html 缺少必要节点: %s" % item)
    ok("人物页、大图、时间流、地点流的关键节点都在")
    if 'id="merge-person"' in html and 'id="merge-person" class' in html and 'type="button" id="merge-person"' not in html:
        fail("合并按钮缺少 type=button，详情页点击可能被表单吞掉")
    ok("人物详情合并按钮是 type=button")
    if (
        'data-view="favorites"' not in html
        or "favorites:['收藏','收藏的照片']" not in app
        or "@app.put('/api/photos/{aid}/favorite')" not in backend
        or "coalesce(a.favorite,0)=1" not in read(ROOT / "browse_queries.py")
        or "id=\"photo-favorite\"" not in html
        or "togglePhotoFavorite" not in viewer
        or "#detail-dialog #photo-favorite" not in viewer_overrides
    ):
        fail("收藏缺少左侧入口、照片按钮、持久化接口或独立筛选")
    ok("照片收藏的入口、持久化、筛选和视觉控件已接入")
    if not all(value in html for value in (
        'id="scan-folder-picker"',
        'id="scan-root-list"',
        'id="scan-add-folder"',
        'id="start-scan"',
        'id="scan-view-errors"',
        'id="scan-progress-track"',
        'id="scan-heartbeat"',
        'id="scan-remaining"',
        'id="scan-face-photos"',
        'id="scan-faces-found"',
        'id="scan-face-average"',
        '添加并扫描',
    )) or 'id="last-scan-details"' in html:
        fail("添加照片页未收口为多目录极简流程，或仍显示最近一次扫描")
    if ("state.folderTarget==='scan'?'scan':'browse'" not in app
        or "const drives=await api('/api/drives')" not in app
        or "card.hidden=active" not in app
        or "继续上次扫描" not in app
        or "await openAddPhotos(path)" not in app
        or "const scanStageLabels=" not in app
        or "function syncScanActivity()" not in app
        or "setInterval(syncScanActivity,1000)" not in app
        or ".scan-progress.is-live .scan-heartbeat" not in appearance
        or "face_average_seconds" not in backend
        or "progress['phase']='processing'" not in backend):
        fail("添加照片目录选择与文件夹页扫描入口尚未按用途统一")
    if not all(value in backend for value in (
        "FACE_GROUP_THRESHOLD = 0.52",
        "FACE_MATCH_MARGIN = 0.08",
        "def choose_face_person(",
        "target.confirmed AS suggested_confirmed",
        "suggested_person_id and (suggested_confirmed or suggested_ignored)",
        "p.suggested_person_id IS NOT NULL",
        "if item['route']!='suggested'",
        "'ignored' if row['ignored'] else 'confirmed'",
        "UPDATE faces SET reviewed=1,ignored=0",
    )):
        fail("人脸自动分流缺少统一阈值、真实人物差值、候选人物折叠、路人状态或恢复闭环")
    ok("添加照片入口和统一人脸归类合同已接入")

    live = {}
    try:
        status = req("/api/status", 4)
        stats, job = status.get("stats") or {}, status.get("job") or {}
        live["status"] = {
            "assets": stats.get("assets"),
            "faces_pending": stats.get("faces_pending"),
            "people": stats.get("people"),
            "named_people": stats.get("named_people"),
            "job_status": job.get("status"),
            "processed": job.get("processed"),
            "discovered": job.get("discovered"),
        }
        if "stats" not in status or "job" not in status:
            fail("/api/status 缺少 stats/job")
        ok("/api/status 可访问")
        photos = req("/api/photos?limit=4", 6)
        items = photos.get("items") or []
        if photos.get("items") is None:
            fail("/api/photos 没有 items")
        ok("/api/photos 分页返回 items")
        if items:
            detail = req("/api/photos/%s" % items[0]["id"], 8)
            if not isinstance(detail.get("files"), list):
                fail("照片详情缺少 files")
            if "effective_place" not in detail or "faces" not in detail:
                fail("照片详情缺少 effective_place/faces")
            if "undefined" in json.dumps(detail, ensure_ascii=False):
                fail("照片详情 JSON 含 undefined 字符串")
            ok("照片详情含 files / faces / effective_place")
        try:
            people = req("/api/people?limit=1", 5)
        except (TimeoutError, urllib.error.URLError) as exc:
            skip("现役人脸扫描忙，跳过 /api/people：%s" % type(exc).__name__)
        else:
            if people.get("items") is None:
                fail("/api/people 没有 items")
            ok("/api/people 分页返回 items")
            if people["items"]:
                person = req("/api/people/%s?limit=8" % people["items"][0]["id"], 8)
                if person.get("faces") is None:
                    fail("人物详情缺少 faces")
                faces = person.get("faces") or []
                if "face_count" in person and not isinstance(person.get("face_count"), int):
                    fail("人物详情 face_count 不是数字")
                if not isinstance(faces, list):
                    fail("人物详情缺少 faces")
                if faces:
                    if not all(isinstance(face.get("asset_id"), int) for face in faces):
                        fail("人物详情有脸缺少数字 asset_id")
                    photo = req("/api/photos/%s" % faces[0]["asset_id"], 8)
                    if not isinstance(photo.get("files"), list) or "faces" not in photo:
                        fail("人物页对应原图打不开")
                    ok("人物页点开原图所需的 asset_id 可用")
                else:
                    skip("抽到的人物还没有 faces，跳过点开原图检查")
    except TimeoutError:
        skip("现役 8765 接口超时，只保留静态防呆")
    except urllib.error.URLError as exc:
        skip("现役 8765 未响应，只保留静态防呆：%s" % exc)

    elapsed = round(time.perf_counter() - started, 3)
    payload = {
        "passed": True,
        "checks": passed,
        "skipped": skipped,
        "seconds": elapsed,
        "live": live,
        "note": "smoke 只防页面级低级错误；入库/人物合同仍要 python validation/validate.py",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SMOKE_OK", len(passed), "checks,", len(skipped), "skipped,", elapsed, "s", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        elapsed = round(time.perf_counter() - started, 3)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps({"passed": False, "error": str(exc), "checks": passed, "skipped": skipped, "seconds": elapsed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("SMOKE_FAIL", exc, flush=True)
        raise SystemExit(1)
