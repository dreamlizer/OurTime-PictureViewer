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
    "viewer.js": ["esc", "prettyPlace", "basename", "fmt", "api", "action", "toast"],
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
    waterfall = read(WEB / "waterfall.js")
    sources = {"app.js": app, "viewer.js": viewer, "waterfall.js": waterfall, "index.html": html}

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
    if not all(path in viewer for path in (
        "/api/face-label-bg/1.png",
        "/api/face-label-bg/2.png",
        "/api/face-label-bg/3.png",
    )):
        fail("素笺主题没有固定映射 1.png / 2.png / 3.png")
    if "faceLabelProfile" not in viewer or "[...normalized].length" not in viewer:
        fail("素笺主题缺少按 Unicode 字符数选择 S/M/L 的规则")
    ok("素笺主题名称、固定底图映射和姓名长度规则已接入")

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

    for item in ["photo-grid", "people-grid", "person-dialog", "person-title", "person-notice", "person-faces-status", "merge-person", "quick-name-dialog", "quick-name-form", "detail-dialog", "no-results", "empty", "stream-status", "places-view", "timeline-tools", "groups-view", "groups-list"]:
        if ('id="%s"' % item) not in html:
            fail("index.html 缺少必要节点: %s" % item)
    ok("人物页、大图、时间流、地点流的关键节点都在")
    if 'id="merge-person"' in html and 'id="merge-person" class' in html and 'type="button" id="merge-person"' not in html:
        fail("合并按钮缺少 type=button，详情页点击可能被表单吞掉")
    ok("人物详情合并按钮是 type=button")

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
