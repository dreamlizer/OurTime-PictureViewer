"""Isolated and live regression checks for the fast, basic people listing."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
WORK_ROOT = ROOT / "validation" / "work"
REPORT = ROOT / "validation" / "reports" / "people-listing-20260911.json"
LIVE = "http://127.0.0.1:8765"
OLD_RELATION_FIELDS = {
    "known_connection_count", "has_known_connection", "best_known_person_id",
    "best_known_person_name", "best_known_photo_count", "best_known_shared_count",
}


def fetch(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode("utf-8", errors="replace")) from exc


def add_person(app, name="", confirmed=0, ignored=0):
    with app.db() as conn:
        return conn.execute(
            "INSERT INTO people(name,alias,confirmed,ignored) VALUES (?,?,?,?)",
            (name, "", confirmed, ignored),
        ).lastrowid


def add_asset(app, root: Path, key: str) -> int:
    with app.db() as conn:
        aid = conn.execute(
            "INSERT INTO assets(sha256,metadata,created_at,width,height,format) VALUES (?,?,?,?,?,?)",
            (hashlib.sha256(key.encode()).hexdigest(), "{}", app.now(), 100, 100, "JPEG"),
        ).lastrowid
        conn.execute(
            "INSERT INTO files(asset_id,path,size,mtime_ns,modified_at,exists_now,excluded) VALUES (?,?,?,?,?,?,?)",
            (aid, str(root / f"{key}.jpg"), 100, 0, app.now(), 1, 0),
        )
        return aid


def add_face(app, asset_id: int, person_id: int) -> None:
    with app.db() as conn:
        conn.execute(
            "INSERT INTO faces(asset_id,person_id,bbox,embedding,score,ignored) VALUES (?,?,?,?,?,?)",
            (asset_id, person_id, "[0,0,100,100,100,100]", b"check", 0.9, 0),
        )


def assert_basic(items: list[dict]) -> None:
    for item in items:
        assert not (OLD_RELATION_FIELDS & item.keys()), f"旧人物关系字段仍由 API 返回：{OLD_RELATION_FIELDS & item.keys()}"


def isolated_check() -> dict:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="people-listing-", dir=WORK_ROOT))
    os.environ["PHOTO_LIBRARY_DATA"] = str(data)
    os.environ["PHOTO_WEB_ROOT"] = str((ROOT / "web").resolve())
    from validation.repair_checks import import_app

    app = import_app(data)
    named = add_person(app, "已命名", 1)
    candidate_a = add_person(app, "候选甲")
    candidate_b = add_person(app, "候选乙")
    candidate_c = add_person(app, "候选丙")
    ignored = add_person(app, "已忽略", 0, 1)
    for i in range(3):
        add_face(app, add_asset(app, data, f"named-{i}"), named)
    for i in range(3):
        add_face(app, add_asset(app, data, f"a-{i}"), candidate_a)
    for i in range(2):
        add_face(app, add_asset(app, data, f"b-{i}"), candidate_b)
    add_face(app, add_asset(app, data, "c-0"), candidate_c)
    add_face(app, add_asset(app, data, "ignored-0"), ignored)

    env = os.environ.copy()
    server = subprocess.Popen([sys.executable, "app.py", "--port", "8783"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                fetch("http://127.0.0.1:8783/api/status")
                break
            except Exception:
                time.sleep(0.25)
        page = fetch("http://127.0.0.1:8783/api/people?limit=48&offset=0")
        assert_basic(page["items"])
        names = [item["name"] for item in page["items"]]
        assert names[:4] == ["已命名", "候选甲", "候选乙", "候选丙"], names
        assert "已忽略" not in names
        assert fetch("http://127.0.0.1:8783/api/people?q=" + urllib.parse.quote("候选丙") + "&limit=48")["items"][0]["name"] == "候选丙"
        pages = [fetch(f"http://127.0.0.1:8783/api/people?limit=2&offset={offset}")["items"] for offset in (0, 2)]
        page_ids = [item["id"] for page_items in pages for item in page_items]
        assert len(page_ids) == len(set(page_ids))
        ignored_page = fetch("http://127.0.0.1:8783/api/people?ignored=1&limit=48")
        assert [item["name"] for item in ignored_page["items"]] == ["已忽略"]
        return {"status": "PASS", "order": names[:4], "pagination_unique": True,
                "old_relation_fields": sorted(OLD_RELATION_FIELDS)}
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


def live_benchmark() -> list[dict]:
    results = []
    for offset in (0, 48):
        started = time.perf_counter()
        page = fetch(f"{LIVE}/api/people?limit=48&offset={offset}")
        results.append({"offset": offset, "ms": round((time.perf_counter() - started) * 1000, 2),
                        "items": len(page.get("items", [])), "total": page.get("total")})
    live = fetch(f"{LIVE}/api/people?limit=48&offset=0")
    name = next((item.get("name") for item in live.get("items", []) if item.get("name")), "")
    started = time.perf_counter()
    page = fetch(f"{LIVE}/api/people?q={urllib.parse.quote(name)}&limit=48")
    results.append({"q": name, "ms": round((time.perf_counter() - started) * 1000, 2),
                    "items": len(page.get("items", [])), "total": page.get("total")})
    assert_basic(page.get("items", []))
    return results


if __name__ == "__main__":
    isolated = isolated_check()
    benchmark = live_benchmark()
    report = {"isolated": isolated, "live_benchmark": benchmark}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
