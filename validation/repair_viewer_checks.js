const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const reportDir = path.join(root, 'validation', 'reports', 'code-quality-repair-20260910', 'logs');
fs.mkdirSync(reportDir, {recursive: true});

function el(init = {}) {
  const node = {
    hidden: false,
    disabled: false,
    textContent: '',
    innerHTML: '',
    className: '',
    value: '',
    style: {setProperty() {}, getPropertyValue() {return ''; }},
    classList: {toggle() {}, add() {}, remove() {}, contains() {return false;}},
    dataset: {},
    children: [],
    offsetWidth: 40,
    offsetHeight: 24,
    clientWidth: 800,
    clientHeight: 600,
    open: init.open || false,
    naturalWidth: 100,
    naturalHeight: 80,
    src: '',
    decode() {return Promise.resolve();},
    href: '',
    focus() {},
    getBoundingClientRect() {return {left: 0, top: 0, width: this.clientWidth || 800, height: this.clientHeight || 600};},
    querySelector() {return null;},
    querySelectorAll() {return [];},
    closest() {return null;},
    addEventListener() {},
    setAttribute() {},
    getAttribute() {return '';},
    removeAttribute() {},
    appendChild(child) {this.children.push(child); return child;},
    replaceChildren(...children) {this.children = children; this.innerHTML = '';},
    insertAdjacentHTML() {},
    ...init,
  };
  return node;
}

function loadAppHelpers() {
  const appJs = fs.readFileSync(path.join(root, 'web', 'app.js'), 'utf8');
  const start = appJs.indexOf('function pickMergeTarget');
  const renderStart = appJs.indexOf('function renderPersonFaces');
  const loadStart = appJs.indexOf('async function loadPersonFaces');
  const loadEnd = appJs.indexOf('function updateBatch');
  const pickEnd = appJs.indexOf('async function resolveMergeTarget');
  const resolveEnd = appJs.indexOf('function faceItemHtml');
  const sandbox = {
    state: {people: [], peopleSelected: new Set(), personId: 1, personDetail: null, personFaces: {generation: 0, offset: 0, loading: false}, view: 'people'},
    apiCalls: [],
    document: {querySelector() {return el();}, querySelectorAll() {return [];}},
    console,
  };
  sandbox.$ = () => sandbox.grid || (sandbox.grid = el({querySelector(sel){ return this._ids && this._ids.has(String(sel).replace(/\D/g,'')) ? el() : null;}, _ids: new Set()}));
  sandbox.api = async (url) => {
    sandbox.apiCalls.push(url);
    const u = new URL('http://local' + url);
    const offset = Number(u.searchParams.get('offset') || 0);
    const limit = Number(u.searchParams.get('limit') || 48);
    const total = sandbox.faceTotal;
    const faces = [];
    for (let i = offset; i < Math.min(total, offset + limit); i++) faces.push({id: i + 1, asset_id: 1000 + i, score: 0.9, reviewed: 0});
    return {id: 1, faces, face_count: total, more: offset + faces.length < total, offset, limit};
  };
  sandbox.peopleQuery = () => '/api/people';
  vm.createContext(sandbox);
  vm.runInContext(appJs.slice(start, loadEnd), sandbox);
  return sandbox;
}

function loadViewer(apiImpl) {
  const viewerJs = fs.readFileSync(path.join(root, 'web', 'viewer.js'), 'utf8');
  const els = {};
  const make = (id) => els[id] || (els[id] = el({id, open: id === 'detail-dialog'}));
  const sandbox = {
    state: {q: '', view: 'all', person: '', directory: '', sort: 'date_desc', maxId: 500, people: [], thumbRevision: 1, detail: {id: 1, faces: []}},
    viewer: {window: 200, ids: [], offset: 0, total: 0, index: 0, target: 0, generation: 0, playing: false, timer: null, drafts: new Map(), faceNames: true, faceAlias: false, faceVertical: false, fit: true, scale: 1, loader: null, busy: false},
    api: apiImpl,
    action: (fn) => fn,
    fmt: (n) => String(n),
    toast() {},
    currentBrowseContext() {return {q: '', filter: 'all', person: '', directory: '', sort: 'date_desc', max_id: sandbox.state.maxId};},
    document: {
      hidden: false,
      fullscreenElement: null,
      addEventListener() {},
      createElement(tag) {return el({tagName: String(tag).toUpperCase()});},
      querySelector: (sel) => make(sel.replace('#','')),
      querySelectorAll: () => [],
      head: el(),
      body: el(),
      documentElement: {requestFullscreen() {return Promise.resolve();}},
      exitFullscreen() {return Promise.resolve();},
    },
    window: {scrollY: 0, innerHeight: 800},
    localStorage: {
      _data: new Map(),
      getItem(key) {return this._data.has(key) ? this._data.get(key) : null;},
      setItem(key, value) {this._data.set(String(key), String(value));},
      removeItem(key) {this._data.delete(String(key));},
    },
    scrollY: 0,
    innerHeight: 800,
    titles: {all:['all photos', 'x']},
    esc: (v) => String(v??''),
    prettyPlace: (v) => String(v||''),
    dateSource: (v) => String(v||''),
    readableError: (v) => String(v||''),
    basename: (p) => String(p||'').split(/[\/]/).pop(),
    bytes: (n) => String(n),
    Number,
    Math,
    JSON,
    getComputedStyle: () => ({width: '100px', height: '80px', getPropertyValue: () => '42px'}),
    Image: class { set src(v){ this.onload && this.onload(); } },
    URLSearchParams,
    clearTimeout,
    setTimeout,
    requestAnimationFrame: (fn) => fn(),
    ResizeObserver: class {observe() {}},
    console,
  };
  sandbox.window = sandbox;
  sandbox.$ = (sel) => {
    const id = String(sel).replace('#','');
    if (sel === 'dialog[open]') return [make('detail-dialog')];
    return make(id);
  };
  sandbox.$$ = () => [];
  sandbox.renderPhoto = Object.assign(async (id) => { sandbox.state.detail = {id, files:[{id:1,path:'C:/t/' + id + '.jpg', excluded:0, exists_now:1, size:1, modified_at:''}], faces:[], history:[], effective_date:'2500-01-01', format:'JPEG', width:100, height:80, camera:'', category:'照片'}; return true; }, {ticket: 0});
  sandbox.basename = (p) => String(p||'').split(/[\/]/).pop();
  sandbox.renderSignature = () => {};
  sandbox.renderFaceNames = () => {};
  vm.createContext(sandbox);
  // viewer.js binds events at load; provide enough DOM ids.
  ['detail-dialog','viewer-message','viewer-play','viewer-position','viewer-prev','viewer-next','viewer-context','viewer-info','image-viewport','detail-img','photo-mat','face-name-layer','zoom-level','zoom-in','zoom-out','zoom-fit','zoom-actual','toggle-face-names','toggle-face-alias','toggle-face-dir','close-info','viewer-fullscreen','slide-delay','detail-form','edit-date','edit-precision','edit-place','edit-notes','sort-order','refresh-photos','browse-directory'].forEach(id => make(id));
  make('detail-dialog').open = true;
  vm.runInContext(viewerJs + '\nthis.__viewer = viewer; this.__openPhoto = openPhoto; this.__movePhoto = movePhoto;', sandbox);
  sandbox.viewer = sandbox.__viewer;
  return sandbox;
}

async function run() {
  const results = [];
  const viewerSource = fs.readFileSync(path.join(root, 'web', 'viewer.js'), 'utf8');
  const fallbackTerms = ['rectOverflow(candidate', 'otherFaceOverlap', 'placedOverlap', 'compareFaceLabelScore(score,bestScore)'];
  results.push({id: 'R06-fallback-score', status: fallbackTerms.every(term => viewerSource.includes(term)) ? 'PASS' : 'FAIL', actual: {terms: fallbackTerms}});
  results.push({id: 'R06-position-copy', status: viewerSource.includes('优先左侧') && viewerSource.includes('优先右侧') ? 'PASS' : 'FAIL'});
  results.push({id: 'R06-reset-position', status: viewerSource.includes("viewer.faceLabelPosition='auto'") ? 'PASS' : 'FAIL'});
  // R01 target selection
  const people = loadAppHelpers();
  const pool = [
    {id: 1, confirmed: 1, name: '甲', photo_count: 9, ignored: 0},
    {id: 3, confirmed: 0, name: '', photo_count: 2, ignored: 0},
    {id: 4, confirmed: 1, name: '乙', photo_count: 3, ignored: 0},
  ];
  const target = people.pickMergeTarget([3,4], pool.concat([{id:1, confirmed:1, name:'甲', photo_count:99}]));
  results.push({id: 'R01-B-front', status: target && target.id !== 1 && [3,4].includes(target.id) ? 'PASS' : 'FAIL', actual: target});

  // R06.2 pagination uniqueness
  people.faceTotal = 97;
  people.state.personId = 1;
  people.state.personFaces = {generation: 0, offset: 0, loading: false};
  people.grid = el({_ids: new Set(), querySelector(sel){const id=String(sel).match(/\d+/); return id && this._ids.has(id[0]) ? el() : null;}, insertAdjacentHTML(_, html){const m=html.match(/data-face-id="(\d+)"/); if(m) this._ids.add(m[1]); if(!this.innerHTML) this.innerHTML=''; this.innerHTML += html;}});
  people.$ = () => people.grid;
  await people.loadPersonFaces(true);
  await people.loadPersonFaces(false);
  await people.loadPersonFaces(false);
  const faces = (people.state.personDetail && people.state.personDetail.faces) || [];
  const unique = new Set(faces.map(f => f.id));
  results.push({id: 'R06-2', status: unique.size === 97 && faces.length === 97 && people.state.personDetail.more === false ? 'PASS' : 'FAIL', actual: {nodes: faces.length, unique: unique.size, more: people.state.personDetail && people.state.personDetail.more}});

  // R03 window movement with fake API
  const photos = Array.from({length: 500}, (_, i) => i + 1);
  const apiImpl = async (url) => {
    const u = new URL('http://x/' + url.replace(/^\//,''));
    const around = Number(u.searchParams.get('around') || 0);
    const offset = Number(u.searchParams.get('offset') || 0);
    const limit = Number(u.searchParams.get('limit') || 200);
    let start = offset;
    if (around) {
      const idx = photos.indexOf(around);
      start = Math.max(0, idx - Math.floor(limit / 2));
    }
    const ids = photos.slice(start, start + limit);
    return {ids, offset: start, total: photos.length, max_id: 500, has_more: start + ids.length < photos.length};
  };
  const ctx = loadViewer(apiImpl);
  const shownId = () => ctx.__viewer.ids[ctx.__viewer.index];
  await ctx.__openPhoto(199);
  const seq = [];
  for (const step of [1,1,1]) {
    await ctx.__movePhoto(step);
    seq.push(shownId());
  }
  const back = [];
  for (const step of [-1,-1,-1]) {
    await ctx.__movePhoto(step);
    back.push(shownId());
  }
  results.push({id: 'R03-A', status: JSON.stringify(seq) === JSON.stringify([200,201,202]) && JSON.stringify(back) === JSON.stringify([201,200,199]) ? 'PASS' : 'FAIL', actual: {seq, back, abs: ctx.viewer.absolute}});
  await ctx.__movePhoto(-(ctx.__viewer.absolute));
  const first = shownId();
  await ctx.__movePhoto((ctx.__viewer.total - 1) - ctx.__viewer.absolute);
  const last = shownId();
  results.push({id: 'R03-B', status: first === 1 && last === 500 ? 'PASS' : 'FAIL', actual: {first, last, pos: ctx.$('#viewer-position').textContent}});

  const failed = results.some(r => r.status !== 'PASS');
  fs.writeFileSync(path.join(reportDir, 'repair_viewer_checks.json'), JSON.stringify({results}, null, 2));
  console.log(JSON.stringify(results, null, 2));
  process.exit(failed ? 1 : 0);
}

run().catch((err) => { console.error(err); process.exit(1); });
