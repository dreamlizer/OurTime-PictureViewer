const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {spawnSync} = require('child_process');

const root = path.resolve(__dirname, '..');
const reportDir = path.join(root, 'validation', 'reports', 'code-quality-repair-20260910-r2', 'runs', 'b1-extra-1');
fs.mkdirSync(reportDir, {recursive: true});

const helperSource = fs.readFileSync(path.join(root, 'validation', 'repair_viewer_checks.js'), 'utf8');
const sliced = helperSource.slice(0, helperSource.indexOf('async function run()'));
const helper = {require, __dirname: path.join(root, 'validation'), console, URL, URLSearchParams, setTimeout, clearTimeout, process};
vm.runInNewContext(sliced.replace("fs.mkdirSync(reportDir, {recursive: true});", ''), helper);

function makeApi(photos, {delayMs = 0, gate} = {}) {
  const calls = [];
  const pending = [];
  const api = async (url) => {
    const u = new URL('http://x' + (url.startsWith('/') ? url : '/' + url));
    const around = Number(u.searchParams.get('around') || 0);
    const offset = Number(u.searchParams.get('offset') || 0);
    const limit = Number(u.searchParams.get('limit') || 200);
    let start = offset;
    if (around) start = Math.max(0, photos.indexOf(around) - Math.floor(limit / 2));
    const rec = {url, around, offset: start, requestedOffset: offset, limit, seq: u.searchParams.get('sequence')};
    calls.push(rec);
    const payload = {ids: photos.slice(start, start + limit), offset: start, total: photos.length, max_id: photos[photos.length - 1], has_more: start + limit < photos.length};
    if (gate) await gate(rec, payload);
    else if (delayMs) await new Promise(r => setTimeout(r, delayMs));
    return payload;
  };
  api.calls = calls;
  api.pending = pending;
  return api;
}

async function run() {
  const photos = Array.from({length: 500}, (_, i) => i + 1);
  const results = [];

  // Cross-window: around 250 with window 200 starts near 150; going to 1 must fetch a new offset.
  const apiA = makeApi(photos);
  const a = helper.loadViewer(apiA);
  a.__viewer.window = 200;
  await a.__openPhoto(250);
  const firstCall = apiA.calls[0];
  const firstOffset = a.__viewer.offset;
  await a.__movePhoto(1 - 1 - a.__viewer.absolute); // Home
  results.push({
    id: 'R03-cross-window-home',
    expected: {id: 1, offsetChanged: true, newWindowRequest: true},
    actual: {id: a.state.detail.id, offset: a.__viewer.offset, firstOffset, calls: apiA.calls.length, last: apiA.calls.at(-1)},
    pass: a.state.detail.id === 1 && a.__viewer.offset !== firstOffset && apiA.calls.length >= 2 && Number(apiA.calls.at(-1).requestedOffset) === 0
  });
  await a.__movePhoto((a.__viewer.total - 1) - a.__viewer.absolute);
  results.push({
    id: 'R03-end-id',
    expected: {id: 500, posAbs: 499},
    actual: {id: a.state.detail.id, absolute: a.__viewer.absolute, pos: a.$('#viewer-position').textContent},
    pass: a.state.detail.id === 500 && a.__viewer.absolute === 499 && a.$('#viewer-position').textContent === '500 / 500'
  });

  // Stale window must not overwrite a newer session.
  let releaseOld;
  const oldHold = new Promise(r => releaseOld = r);
  let firstWindow = true;
  const apiB = makeApi(photos, {gate: async (rec) => {
    if (firstWindow && rec.around === 100) {
      firstWindow = false;
      await oldHold;
    }
  }});
  const b = helper.loadViewer(apiB);
  const open100 = b.__openPhoto(100);
  await new Promise(r => setTimeout(r, 20));
  await b.__openPhoto(400);
  const idsAfter400 = b.__viewer.ids.slice();
  const offsetAfter400 = b.__viewer.offset;
  releaseOld();
  await open100.catch(() => {});
  await new Promise(r => setTimeout(r, 20));
  results.push({
    id: 'R03-stale-window',
    expected: 'ids remain around 400',
    actual: {id: b.state.detail.id, offset: b.__viewer.offset, offsetAfter400, ids0: b.__viewer.ids[0], ids0After400: idsAfter400[0], generation: b.__viewer.generation},
    pass: b.state.detail.id === 400 && b.__viewer.offset === offsetAfter400 && b.__viewer.ids[0] === idsAfter400[0] && !b.__viewer.ids.includes(100)
  });

  const failed = results.some(r => !r.pass);
  fs.writeFileSync(path.join(reportDir, 'repair_viewer_r2.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  process.exit(failed ? 1 : 0);
}

run().catch(err => { console.error(err); process.exit(1); });
