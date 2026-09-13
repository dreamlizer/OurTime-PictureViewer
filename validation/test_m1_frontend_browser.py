"""Real-page browser checks for M1 entity, draft, query, and retry ownership.

The application runs against a synthetic library under validation/work on a
random localhost port.  Fetch timing/failure injection happens in the real page
without replacing application functions.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library_db import init_schema


RUN = ROOT / "validation" / "work" / ("m1-browser-" + time.strftime("%Y%m%d-%H%M%S"))
DATA = RUN / "data"
PHOTOS = RUN / "photos"
REPORT = ROOT / "validation" / "reports" / "m1-frontend-browser.json"
checks: list[str] = []


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)
    checks.append(message)
    print("PASS", message, flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(url: str, path: str, body=None, method: str | None = None):
    call = urllib.request.Request(
        url + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(call, timeout=10) as response:
        return json.load(response)


def seed() -> None:
    DATA.mkdir(parents=True)
    PHOTOS.mkdir(parents=True)
    with sqlite3.connect(DATA / "library.sqlite3") as connection:
        init_schema(connection)
        for aid in range(1, 5):
            path = PHOTOS / f"fixture-{aid}.jpg"
            Image.new("RGB", (320, 240), (aid * 40, aid * 30, aid * 20)).save(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            connection.execute(
                """INSERT INTO assets(
                     id,sha256,width,height,format,metadata,captured_at,date_source,
                     date_precision,category,created_at,face_state
                   ) VALUES (?,?,?,?,?,'{}',?,'EXIF','日','照片',?,1)""",
                (
                    aid,
                    digest,
                    320,
                    240,
                    "JPEG",
                    f"2026-09-{aid:02}T12:00:00",
                    "2026-09-14T12:00:00",
                ),
            )
            connection.execute(
                """INSERT INTO files(
                     asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
                   ) VALUES (?,?,?,?,?,1,0)""",
                (
                    aid,
                    str(path),
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                    "2026-09-14T12:00:00",
                ),
            )
        people = ((1, "人物甲", 1), (2, "人物乙", 1), (3, None, 0), (4, "候选乙", 1))
        for pid, name, confirmed in people:
            connection.execute(
                "INSERT INTO people(id,name,confirmed,ignored) VALUES (?,?,?,0)",
                (pid, name, confirmed),
            )
            connection.execute(
                """INSERT INTO faces(
                     id,asset_id,person_id,bbox,embedding,score,reviewed,ignored
                   ) VALUES (?,?,?,?,x'00',0.95,?,0)""",
                (pid, pid, pid, "[0,0,20,20,320,240]", confirmed),
            )
        connection.commit()
    (DATA / "faces").mkdir()
    (DATA / "thumbs").mkdir()
    for face_id in range(1, 5):
        Image.new("RGB", (40, 40), "#889988").save(DATA / "faces" / f"{face_id}.jpg")


def read_one(sql: str, params=()):
    connection = sqlite3.connect(DATA / "library.sqlite3")
    try:
        return connection.execute(sql, params).fetchone()
    finally:
        connection.close()


FETCH_HARNESS = r"""
(() => {
  const original = window.fetch.bind(window);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  window.__m1Harness = { calls: [], plan: null };
  window.fetch = async (input, options = {}) => {
    const raw = typeof input === 'string' ? input : input.url;
    const url = new URL(raw, location.href);
    const method = String(options.method || (input && input.method) || 'GET').toUpperCase();
    window.__m1Harness.calls.push({ path: url.pathname + url.search, method, at: performance.now() });
    const effect = typeof window.__m1Harness.plan === 'function'
      ? (window.__m1Harness.plan(url, method, options) || {}) : {};
    if (effect.kind === 'never') {
      return new Promise((resolve, reject) => {
        const signal = options.signal;
        if (signal && signal.aborted) return reject(new DOMException('Aborted', 'AbortError'));
        if (signal) signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    }
    if (effect.before) await sleep(effect.before);
    if (effect.kind === 'json') {
      return new Response(JSON.stringify(effect.body || {}), {
        status: effect.status || 200, headers: {'Content-Type': 'application/json'}
      });
    }
    if (effect.kind === 'invalid') return new Response('{invalid', {status: 200});
    const response = await original(input, options);
    if (effect.after) await sleep(effect.after);
    if (effect.lose) {
      await response.clone().text();
      throw new TypeError('simulated response loss');
    }
    return response;
  };
})();
"""


def main() -> int:
    seed()
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        PHOTO_LIBRARY_DATA=str(DATA),
        PHOTO_LIBRARY_PORT=str(port),
        PHOTO_WEB_ROOT=str(ROOT / "web"),
        PHOTO_MODEL_ROOT=str(DATA / "no-model"),
        PHOTO_GEO_ROOT=str(DATA / "no-geo"),
        PHOTO_NO_BROWSER="1",
        NO_ALBUMENTATIONS_UPDATE="1",
        PYTHONIOENCODING="utf-8",
    )
    log_path = RUN / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result = {"passed": False, "checks": checks, "run": str(RUN), "port": port}
    browser = None
    try:
        for _ in range(150):
            try:
                request(url, "/api/health")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            log.flush()
            raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script(FETCH_HARNESS)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function("window.__ourTimeHomeUI && document.querySelectorAll('#photo-grid [data-photo]').length===4")

            page.evaluate(
                """() => {
                  const queues={1:[320],2:[20]};
                  window.__m1Harness.plan=(url,method)=>{
                    const hit=url.pathname.match(/^\\/api\\/people\\/(\\d+)$/);
                    if(method==='GET'&&hit&&queues[hit[1]]?.length)return {after:queues[hit[1]].shift()};
                    return {};
                  };
                  return Promise.all([openPerson(1),openPerson(2)]);
                }"""
            )
            check(page.locator("#person-title").inner_text() == "人物乙", "M1-B01 人物A慢、B快时仅B会话更新窗口")
            page.fill("#person-name", "人物乙新")
            page.locator("#person-form").evaluate("(form)=>form.requestSubmit()")
            page.wait_for_function("document.querySelector('#person-save-state').textContent.includes('人物乙新')")
            check(read_one("SELECT name FROM people WHERE id=2")[0] == "人物乙新", "M1-B01 保存目标固定为当前人物B")

            page.evaluate(
                """async () => {
                  const dialog=document.querySelector('#person-dialog');
                  if(dialog.open)await new Promise(resolve=>{
                    dialog.addEventListener('close',resolve,{once:true});dialog.close();
                  });
                  const queues={1:[340,25],2:[100]};
                  window.__m1Harness.plan=(url,method)=>{
                    const hit=url.pathname.match(/^\\/api\\/people\\/(\\d+)$/);
                    if(method==='GET'&&hit&&queues[hit[1]]?.length)return {after:queues[hit[1]].shift()};
                    return {};
                  };
                  const first=openPerson(1); await new Promise(r=>setTimeout(r,5));
                  const middle=openPerson(2); await new Promise(r=>setTimeout(r,5));
                  const latest=openPerson(1);
                  await Promise.all([first,middle,latest]);
                }"""
            )
            b02_title = page.locator("#person-title").inner_text()
            check(b02_title == "人物甲", f"M1-B02 A到B再到A时旧A响应不能冒充最新A会话（实际：{b02_title}）")

            before_notice = page.locator("#person-notice").inner_text()
            page.evaluate(
                """async () => {
                  let used=false;
                  window.__m1Harness.plan=(url,method)=>{
                    if(!used&&method==='GET'&&url.pathname==='/api/people/2'){used=true;return {after:250};}
                    return {};
                  };
                  const pending=openPerson(2);
                  await new Promise(r=>setTimeout(r,25));
                  document.querySelector('#person-dialog').close();
                  await pending;
                }"""
            )
            check(not page.locator("#person-dialog").is_visible() and page.locator("#person-notice").inner_text() == before_notice, "M1-B03 关闭窗口使迟到人物响应失效且不重开提示")

            page.evaluate(
                """() => {
                  let delayed=true;
                  window.__m1Harness.plan=(url,method)=>{
                    if(delayed&&method==='PATCH'&&url.pathname==='/api/photos'){delayed=false;return {after:300};}
                    return {};
                  };
                }"""
            )
            page.evaluate("openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})")
            page.wait_for_selector("#detail-dialog[open]")
            page.locator("#viewer-info").click()
            page.locator(".info-edit > summary").click()
            page.fill("#edit-notes", "A已提交")
            page.dispatch_event("#edit-notes", "input")
            page.locator("#detail-form").evaluate("(form)=>form.requestSubmit()")
            page.wait_for_timeout(30)
            page.evaluate("openPhoto(2,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})")
            page.wait_for_function("window.__ourTimeApp.state.detail && window.__ourTimeApp.state.detail.id===2")
            page.fill("#edit-notes", "B未提交草稿")
            page.dispatch_event("#edit-notes", "input")
            page.focus("#edit-notes")
            page.wait_for_timeout(380)
            current = page.evaluate(
                """() => ({
                  id:window.__ourTimeApp.state.detail.id,
                  value:document.querySelector('#edit-notes').value,
                  focused:document.activeElement===document.querySelector('#edit-notes')
                })"""
            )
            check(read_one("SELECT notes FROM assets WHERE id=1")[0] == "A已提交" and current == {"id": 2, "value": "B未提交草稿", "focused": True}, "M1-B04 保存A期间切B不改写B草稿和焦点")

            page.evaluate(
                """() => {
                  let first=true;
                  window.__m1Harness.plan=(url,method)=>{
                    if(method==='PATCH'&&url.pathname==='/api/photos'&&first){first=false;return {after:240};}
                    return {};
                  };
                }"""
            )
            page.evaluate("openPhoto(1,{q:'',filter:'all',person:'',directory:'',sort:'date_desc'})")
            page.wait_for_function("window.__ourTimeApp.state.detail && window.__ourTimeApp.state.detail.id===1")
            page.fill("#edit-notes", "A-v1")
            page.dispatch_event("#edit-notes", "input")
            page.locator("#detail-form").evaluate("(form)=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
            page.wait_for_timeout(25)
            page.fill("#edit-notes", "A-v2")
            page.dispatch_event("#edit-notes", "input")
            page.locator("#detail-form").evaluate("(form)=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
            page.wait_for_function(
                """() => document.querySelector('#edit-notes').value==='A-v2'
                  && !viewer.drafts.has(1)""",
                timeout=5000,
            )
            b05_state = page.evaluate(
                """() => ({
                  id:window.__ourTimeApp.state.detail?.id,
                  value:document.querySelector('#edit-notes').value,
                  hasDraft:viewer.drafts.has(1)
                })"""
            )
            check(
                read_one("SELECT notes FROM assets WHERE id=1")[0] == "A-v2"
                and b05_state == {"id": 1, "value": "A-v2", "hasDraft": False},
                f"M1-B05 同一照片v1/v2按队列落库且v1不清v2（实际：{b05_state}）",
            )

            page.evaluate(
                """async () => {
                  document.querySelector('#detail-dialog').close();
                  await openQuickName(3);
                  let slow=true;
                  window.__m1Harness.plan=(url,method)=>{
                    if(method==='GET'&&url.pathname==='/api/people'&&url.searchParams.get('q')==='人物甲'&&slow){slow=false;return {after:280};}
                    if(method==='GET'&&url.pathname==='/api/people'&&url.searchParams.get('q')==='候选乙')return {after:20};
                    return {};
                  };
                  document.querySelector('#quick-merge-search').value='人物甲';
                  const old=loadQuickMergeTargets();
                  await new Promise(r=>setTimeout(r,10));
                  document.querySelector('#quick-merge-search').value='候选乙';
                  const latest=loadQuickMergeTargets();
                  await Promise.all([old,latest]);
                }"""
            )
            options = page.locator("#quick-merge-target option").all_inner_texts()
            check(any("候选乙" in item for item in options) and not any("人物甲" in item for item in options), "M1-B06 迟到候选查询不覆盖最新搜索")
            page.locator("#quick-name-dialog").evaluate("(dialog)=>dialog.close()")

            initial_ids = page.locator("#photo-grid [data-photo]").evaluate_all("(items)=>items.map(x=>x.dataset.photo)")
            page.evaluate(
                """() => {
                  window.__m1Harness.plan=(url,method)=>{
                    if(method==='GET'&&url.pathname==='/api/photos'&&url.searchParams.get('sort')==='date_asc')
                      return {kind:'json',status:500,body:{detail:'injected',error_code:'server_error'}};
                    return {};
                  };
                  const sort=document.querySelector('[data-ot="sort"]');sort.value='date_asc';
                  sort.dispatchEvent(new Event('change',{bubbles:true}));
                }"""
            )
            page.wait_for_function("!document.querySelector('[data-ot=\"sort\"]').disabled && !document.querySelector('[data-ot=\"error\"]').hidden")
            failed_state = page.evaluate(
                """() => ({
                  sort:window.__ourTimeApp.state.sort,
                  wall:waterfall.query.sort,
                  ids:[...document.querySelectorAll('#photo-grid [data-photo]')].map(x=>x.dataset.photo)
                })"""
            )
            check(failed_state == {"sort": "date_desc", "wall": "date_desc", "ids": initial_ids}, "M1-B07 查询500时条件、墙和viewer范围回到同一成功版本")
            page.evaluate(
                """() => {
                  window.__m1Harness.plan=(url,method)=>{
                    if(method==='GET'&&url.pathname==='/api/photos'&&url.searchParams.get('sort')==='date_asc')
                      return {kind:'invalid'};
                    return {};
                  };
                  const sort=document.querySelector('[data-ot="sort"]');sort.value='date_asc';
                  sort.dispatchEvent(new Event('change',{bubbles:true}));
                }"""
            )
            page.wait_for_function("!document.querySelector('[data-ot=\"sort\"]').disabled && !document.querySelector('[data-ot=\"error\"]').hidden")
            invalid_state = page.evaluate(
                "() => ({sort:window.__ourTimeApp.state.sort,wall:waterfall.query.sort,error:document.querySelector('[data-ot=\"error\"]').textContent})"
            )
            check(invalid_state["sort"] == "date_desc" and invalid_state["wall"] == "date_desc" and "加载失败" in invalid_state["error"], "M1-B07 非法JSON按查询失败回滚且busy恢复")

            timeout_state = page.evaluate(
                """async () => {
                  window.__m1Harness.plan=(url)=>url.pathname==='/api/m1-never'?{kind:'never'}:{};
                  const external=new AbortController();
                  let added=0,removed=0;
                  const add=external.signal.addEventListener.bind(external.signal);
                  const remove=external.signal.removeEventListener.bind(external.signal);
                  external.signal.addEventListener=(...args)=>{added++;return add(...args);};
                  external.signal.removeEventListener=(...args)=>{removed++;return remove(...args);};
                  let timeoutCode='';
                  try{await window.__ourTimeApp.api('/api/m1-never',{timeoutMs:35,signal:external.signal});}
                  catch(error){timeoutCode=error.errorCode;}
                  window.__m1Harness.plan=(url)=>url.pathname==='/api/m1-never'?{kind:'never'}:{};
                  const aborter=new AbortController();let abortCode='';
                  const pending=window.__ourTimeApp.api('/api/m1-never',{timeoutMs:500,signal:aborter.signal});
                  aborter.abort();
                  try{await pending;}catch(error){abortCode=error.errorCode;}
                  return {timeoutCode,abortCode,added,removed};
                }"""
            )
            check(timeout_state == {"timeoutCode": "timeout", "abortCode": "aborted", "added": 1, "removed": 1}, "M1-E03 外部AbortSignal与timeout组合会分类取消并清理监听")

            page.evaluate(
                """() => {
                  window.__m1Harness.plan=(url,method)=>{
                    if(method==='GET'&&url.pathname==='/api/photos'&&url.searchParams.get('sort')==='date_asc')
                      return {kind:'json',body:{total:0,items:[],max_id:4}};
                    return {};
                  };
                  const sort=document.querySelector('[data-ot="sort"]');sort.value='date_asc';
                  sort.dispatchEvent(new Event('change',{bubbles:true}));
                }"""
            )
            page.wait_for_function("!document.querySelector('[data-ot=\"sort\"]').disabled && waterfall.total===0")
            check(page.locator("#photo-grid [data-photo]").count() == 0 and not page.locator("#no-results").is_hidden(), "M1-B08 成功空结果提交为空墙而不是恢复旧照片")

            page.evaluate(
                """async () => {
                  let slow=true;
                  window.__m1Harness.plan=(url,method)=>{
                    const sort=url.searchParams.get('sort');
                    if(method==='GET'&&url.pathname==='/api/photos'&&sort==='name_asc'&&slow){slow=false;return {after:260,kind:'json',status:500,body:{detail:'late'}};}
                    if(method==='GET'&&url.pathname==='/api/photos'&&sort==='name_desc')return {after:20};
                    return {};
                  };
                  const adapter=window.__ourTimeHomeAdapter;
                  const context=()=>({expectedScopeKey:'home',expectedQuery:{q:'',person:'',directory:'',place:'',dateFrom:'',dateTo:''}});
                  const old=adapter.applySort('name_asc',context()).catch(()=>false);
                  await new Promise(r=>setTimeout(r,10));
                  const latest=adapter.applySort('name_desc',context());
                  await Promise.allSettled([old,latest]);
                }"""
            )
            latest_state = page.evaluate("() => ({sort:window.__ourTimeApp.state.sort,wall:waterfall.query.sort,total:waterfall.total})")
            check(latest_state == {"sort": "name_desc", "wall": "name_desc", "total": 4}, "M1-B09 旧失败晚于新成功时不回滚新范围")

            page.evaluate(
                """async () => {
                  window.__m1Harness.plan=(url,method)=>method==='GET'&&url.pathname==='/api/photos'?{after:220}:{};
                  const sort=document.querySelector('[data-ot="sort"]');sort.value='date_desc';
                  sort.dispatchEvent(new Event('change',{bubbles:true}));
                  await new Promise(r=>setTimeout(r,25));
                }"""
            )
            pending_guard = page.evaluate(
                """() => {
                  const button=document.querySelector('[data-ot="select"]');
                  const disabled=button.disabled;button.click();
                  return {disabled,selecting:window.__ourTimeApp.state.selecting};
                }"""
            )
            page.wait_for_function("!document.querySelector('[data-ot=\"sort\"]').disabled")
            check(pending_guard == {"disabled": True, "selecting": False}, "M1-B11 查询待提交时选择与批量入口不可提交旧范围")

            page.evaluate(
                """() => {
                  window.__m1Harness.calls=[];
                  window.__m1Harness.plan=()=>({});
                  document.querySelector('#refresh-photos').click();
                }"""
            )
            page.wait_for_timeout(250)
            reloads = page.evaluate(
                "() => window.__m1Harness.calls.filter(x=>x.method==='GET'&&x.path.startsWith('/api/photos?')&&x.path.includes('offset=0')).length"
            )
            check(reloads == 1, "M1-B10 旧刷新控件一次点击只触发一次首屏重载")

            operation_id = "browser-response-loss"
            recovered = page.evaluate(
                """async operationId => {
                  let lost=false;
                  window.__m1Harness.plan=(url,method)=>{
                    if(!lost&&method==='POST'&&url.pathname==='/api/exclusions/assets'){lost=true;return {lose:true};}
                    return {};
                  };
                  const result=await window.__ourTimeApp.operationRequest(
                    '/api/exclusions/assets',
                    {method:'POST',body:JSON.stringify({ids:[4],excluded:true,display_only:true,operation_id:operationId})},
                    operationId
                  );
                  const posts=window.__m1Harness.calls.filter(x=>x.method==='POST'&&x.path==='/api/exclusions/assets').length;
                  return {operationId:result.operation_id,recovered:result.recoveredAfterResponseLoss===true,posts};
                }""",
                operation_id,
            )
            check(recovered == {"operationId": operation_id, "recovered": True, "posts": 1} and read_one("SELECT excluded FROM assets WHERE id=4")[0] == 1, "M1-B12 响应丢失后查询持久回执且不重复非幂等POST")

            page.evaluate(
                """async () => {
                  let used=false;
                  window.__m1Harness.plan=(url,method)=>{
                    if(!used&&method==='GET'&&url.pathname==='/api/photos'){used=true;return {after:180};}
                    return {};
                  };
                  const pending=window.__ourTimeHomeAdapter.applySort('date_asc',{
                    expectedScopeKey:'home',
                    expectedQuery:{q:'',person:'',directory:'',place:'',dateFrom:'',dateTo:''}
                  }).catch(()=>false);
                  await new Promise(r=>setTimeout(r,15));
                  await setView('people');
                  await pending;
                }"""
            )
            check(page.evaluate("window.__ourTimeApp.state.view") == "people" and page.locator("#person-dialog").is_hidden(), "M1-B03/B09 离开页面后迟到查询不复活旧页面")
            check(errors == [], "真实页面无pageerror或未处理Promise rejection")
            browser.close()
            browser = None

        result["passed"] = True
        result["checks"] = checks
        return 0
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        try:
            request(url, "/api/shutdown", {}, "POST")
        except (OSError, urllib.error.URLError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
