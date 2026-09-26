/* OurTime structured photo query controls. */
(function (global) {
  'use strict';
  const VERSION = '2.0.0';
  const SORTS = new Map([
    ['date_desc', '最新优先'], ['date_asc', '最早优先'],
    ['name_asc', '文件名顺序'], ['name_desc', '文件名倒序'],
    ['recognized_desc', '已识别人物最多']
  ]);
  const REQUIRED = ['readState', 'applyQuery', 'applySort', 'getPeople',
    'setSelecting', 'selectVisible', 'clearSelection', 'editSelected', 'excludeSelected'];
  const mounts = new WeakMap();
  let instanceCounter = 0;
  const text = value => value == null ? '' : String(value);
  const queryOf = value => ({
    q: text(value && value.q),
    person: text(value && value.person).trim(),
    directory: text(value && value.directory).trim(),
    place: text(value && value.place).trim(),
    dateFrom: text(value && value.dateFrom).trim(),
    dateTo: text(value && value.dateTo).trim()
  });
  const sameQuery = (a, b) => a.q === b.q && a.person === b.person && a.directory === b.directory && a.place === b.place && a.dateFrom === b.dateFrom && a.dateTo === b.dateTo;
  const shortPath = value => {
    if (/^[a-z]:[\/]?$/i.test(value)) return value;
    const parts = value.replace(/[\/]+$/, '').split(/[\/]/);
    return parts[parts.length - 1] || value;
  };
  const svg = body => '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + body + '</svg>';
  const ICONS = {
    person: svg('<circle cx="12" cy="8" r="4"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>'),
    time: svg('<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4m8-4v4M4 10h16"/>'),
    group: svg('<circle cx="9" cy="8" r="3"/><circle cx="17" cy="10" r="2.5"/><path d="M3 21v-1.5a6 6 0 0 1 12 0V21m1-5a5 5 0 0 1 5 4v1"/>'),
    place: svg('<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2.4"/>'),
    folder: svg('<path d="M3 7h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/><path d="M3 10h18"/>'),
    chevron: svg('<path d="m8 10 4 4 4-4"/>'),
    select: svg('<rect x="4" y="4" width="16" height="16" rx="4"/><path d="m8 12 3 3 5-6"/>'),
    close: svg('<path d="m7 7 10 10M17 7 7 17"/>')
  };
  const filterButton = (kind, label) => '<button data-ot="' + kind + '" class="ot-home-button ot-home-filter" type="button">'
    + ICONS[kind] + '<span>' + label + '</span>' + ICONS.chevron + '</button>';
  function personIds(raw) { return text(raw).split(',').map(item => item.trim()).filter(Boolean); }
  function dateLabel(from, to) {
    from = text(from); to = text(to);
    if (!from && !to) return '';
    if (/^\d{4}$/.test(from) && (!to || to === from || to === from + '-12-31')) return from + '年';
    if (/^\d{4}-\d{2}$/.test(from) && (!to || to === from || to.slice(0, 7) === from)) return from.slice(0, 4) + '年' + Number(from.slice(5, 7)) + '月';
    if (from && to && from !== to) return from + ' ~ ' + to;
    return from || to;
  }
  function mount(options) {
    const host = options && options.host;
    const adapter = options && options.adapter;
    if (!host || host.nodeType !== 1 || !host.ownerDocument) throw new TypeError('OurTimeHomeUI.mount requires a host element.');
    if (mounts.has(host)) throw new Error('This homepage host is already mounted.');
    for (const key of REQUIRED) {
      if (!adapter || typeof adapter[key] !== 'function') throw new TypeError('Homepage adapter is missing: ' + key);
    }
    const document = host.ownerDocument;
    const window = document.defaultView;
    const life = new window.AbortController();
    const root = document.createElement('section');
    root.className = 'ot-home-ui';
    root.setAttribute('aria-label', '照片查询与选择');
    root.innerHTML = '<div class="ot-home-query" role="toolbar" aria-label="照片筛选">'
      + '<span class="ot-home-query-left">'
      + filterButton('person', '人物')
      + filterButton('time', '时间')
      + filterButton('group', '合影人数')
      + filterButton('place', '地点')
      + filterButton('folder', '文件夹')
      + '</span><span class="ot-home-query-right">'
      + '<button data-ot="select" class="ot-home-button" type="button">' + ICONS.select + '<span>选择</span></button>'
      + '<label class="ot-home-sort"><span class="ot-home-sr-only">照片排序</span><select data-ot="sort" aria-label="照片排序"></select></label>'
      + '</span></div>'
      + '<div data-ot="batch" class="ot-home-batch" hidden>'
      + '<strong data-ot="selected-count" class="ot-home-selected-count"></strong>'
      + '<button data-ot="visible" class="ot-home-button ot-home-quiet" type="button">选择当前屏幕</button>'
      + '<button data-ot="clear-selection" class="ot-home-button ot-home-quiet" type="button">清空</button>'
      + '<span class="ot-home-spacer"></span>'
      + '<button data-ot="exclude" class="ot-home-button ot-home-danger" type="button">排除所选…</button>'
      + '<button data-ot="restore" class="ot-home-button" type="button" hidden>恢复所选</button>'
      + '<button data-ot="edit" class="ot-home-button" type="button">批量补录</button>'
      + '<button data-ot="cancel-selection" class="ot-home-button" type="button">取消</button></div>'
      + '<div data-ot="chips" class="ot-home-chips" hidden></div>'
      + '<div data-ot="notice" class="ot-home-notice" hidden><span>照片库有更新，可刷新当前列表。</span>'
      + '<button data-ot="refresh" class="ot-home-link" type="button">刷新照片</button></div>'
      + '<p data-ot="error" class="ot-home-error" hidden></p>';
    const popover = document.createElement('div');
    popover.className = 'ot-home-popover';
    popover.hidden = true;
    popover.setAttribute('role', 'dialog');
    do { popover.id = 'ot-home-pop-' + (++instanceCounter); } while (document.getElementById(popover.id));
    const all = name => root.querySelector('[data-ot="' + name + '"]') || popover.querySelector('[data-ot="' + name + '"]');
    for (const key of ['person', 'time', 'group', 'place']) {
      const control = all(key);
      control.setAttribute('aria-haspopup', 'dialog');
      control.setAttribute('aria-expanded', 'false');
      control.setAttribute('aria-controls', popover.id);
    }
    const folderControl = all('folder');
    folderControl.setAttribute('aria-haspopup', 'dialog');
    folderControl.setAttribute('aria-controls', 'folder-dialog');
    folderControl.setAttribute('aria-expanded', 'false');
    for (const [value, label] of SORTS) all('sort').add(new window.Option(label, value));
    let destroyed = false, busy = false, lastScope = null, lastFocus = null, openKind = '', openAnchor = null;
    let people = [], peopleLabels = new Map(), places = [], timeline = { years: [], months: [] };
    let peopleTimer = null, placeTimer = null, peopleController = null, placeController = null, folderPending = false, timeYear = '';
    let timelinePromise = null, timelineLoading = false;
    timeline = readTimelineCache() || timeline;
    function listen(el, event, handler) { if (el) el.addEventListener(event, handler, { signal: life.signal }); }
    function snapshot() {
      const raw = adapter.readState();
      if (!raw || typeof raw !== 'object' || raw.then) throw new TypeError('readState must return a synchronous object.');
      const sort = text(raw.sort || 'date_desc');
      if (!SORTS.has(sort)) throw new Error('Unsupported homepage sort: ' + sort);
      const scopeKey = text(raw.scopeKey);
      return {
        active: raw.active === true, scopeKey: text(raw.scopeKey), query: queryOf(raw.query), sort,
        group: scopeKey.startsWith('group:') ? scopeKey.slice(6) : (scopeKey.startsWith('public-figures:') ? scopeKey.slice(15) : ''),
        selecting: raw.selecting === true,
        selectedCount: Number.isFinite(Number(raw.selectedCount)) ? Math.max(0, Math.floor(Number(raw.selectedCount))) : 0,
        selectionKind: raw.selectionKind === 'restore' ? 'restore' : 'exclude',
        refreshAvailable: raw.refreshAvailable === true
      };
    }
    const isGroup = s => String(s.scopeKey || '').startsWith('group:');
    const groupRange = key => {
      key = text(key); if (key === '10plus') return { min: 10, max: null };
      let match = key.match(/^(\d+)$/); if (match) return { min: Number(match[1]), max: Number(match[1]) };
      match = key.match(/^(\d+)-(\d+)$/); if (match) return { min: Number(match[1]), max: Number(match[2]) };
      match = key.match(/^(\d+)plus$/); if (match) return { min: Number(match[1]), max: null };
      match = key.match(/^upto(\d+)$/); if (match) return { min: null, max: Number(match[1]) };
      return { min: null, max: null };
    };
    const groupLabel = key => { const { min, max } = groupRange(key); if (min && max) return min === max ? min + '人' : min + '–' + max + '人'; if (min) return min + '人以上'; if (max) return max + '人以下'; return ''; };
    const groupKey = (min, max) => min && max ? (min === max ? String(min) : min + '-' + max) : min ? (min === 10 ? '10plus' : min + 'plus') : max ? 'upto' + max : '';
    function report(error) {
      const el = all('error');
      el.textContent = error ? (text(error.message || error) || '操作未完成，请重试。') : '';
      el.hidden = !error;
    }
    function personLabel(id) { return peopleLabels.get(id) || (people.find(item => item.id === id) || {}).label || ('ID ' + id); }
    function rememberLabel(id, label) {
      const key = text(id).trim();
      const name = text(label).trim();
      if (!key || !name) return;
      peopleLabels.set(key, name);
    }
    function resolvePersonLabel(id) {
      const key = text(id).trim();
      return peopleLabels.get(key)
        || (people.find(item => item.id === key) || {}).label
        || (typeof adapter.personLabel === 'function' ? text(adapter.personLabel(key)).trim() : '')
        || ('ID ' + key);
    }
    function chips(s) {
      const box = all('chips'); box.replaceChildren();
      const entries = [];
      for (const id of personIds(s.query.person)) entries.push(['person:' + id, resolvePersonLabel(id)]);
      const time = dateLabel(s.query.dateFrom, s.query.dateTo);
      if (time) entries.push(['time', time]);
      if (s.query.place) entries.push(['place', '地点：' + s.query.place]);
      if (s.query.directory) entries.push(['directory', '文件夹：' + shortPath(s.query.directory)]);
      if (s.group) entries.push(['group', '合影人数：' + groupLabel(s.group)]);
      box.hidden = !entries.length;
      for (const [key, display] of entries) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'ot-home-chip'; button.dataset.condition = key;
        button.setAttribute('aria-label', '移除' + display);
        button.innerHTML = '<span></span><span aria-hidden="true">×</span>';
        button.firstChild.textContent = display;
        button.disabled = busy || s.selecting;
        box.appendChild(button);
      }
    }
    function mark(name, on) { const el = all(name); if (el) el.classList.toggle('ot-home-filter-active', Boolean(on)); }
    function sync() {
      if (destroyed) return;
      const s = snapshot();
      if (lastScope !== null && (lastScope !== s.scopeKey || !s.active)) closePopover(true);
      lastScope = s.scopeKey;
      root.hidden = !s.active;
      all('sort').value = s.sort;
      const grouped = isGroup(s);
      root.classList.toggle('ot-home-group', grouped);
      all('group').hidden = typeof adapter.applyGroup !== 'function';
      const recognizedSort=all('sort').querySelector('option[value="recognized_desc"]');
      if(recognizedSort){recognizedSort.hidden=!grouped;recognizedSort.disabled=!grouped;}
      all('place').hidden = grouped; all('folder').hidden = grouped; all('select').hidden = grouped;
      root.querySelector('.ot-home-query').hidden = s.selecting;
      all('batch').hidden = !s.selecting;
      mark('person', s.query.person); mark('time', s.query.dateFrom || s.query.dateTo); mark('group', s.group);
      mark('place', s.query.place); mark('folder', s.query.directory);
      all('selected-count').textContent = '已选择 ' + s.selectedCount + ' 张';
      all('exclude').hidden = s.selectionKind === 'restore';
      all('edit').hidden = s.selectionKind === 'restore';
      all('restore').hidden = s.selectionKind !== 'restore';
      all('restore').disabled = busy || !s.selectedCount || typeof adapter.restoreSelected !== 'function';
      for (const key of ['person','time','group','place','folder','select','sort','visible','cancel-selection','refresh']) if (all(key)) all(key).disabled = busy || folderPending;
      for (const key of ['clear-selection','exclude','edit']) all(key).disabled = busy || !s.selectedCount;
      all('notice').hidden = !s.refreshAvailable || s.selecting || typeof adapter.refresh !== 'function';
      chips(s);
    }
    async function runAction(callback) {
      if (busy || destroyed) return false;
      busy = true; report(null); sync();
      let ok = false;
      try { await callback(life.signal); ok = !destroyed; }
      catch (error) { if (!destroyed && error.name !== 'AbortError') report(error); }
      finally { busy = false; if (!destroyed) sync(); }
      return ok;
    }
    function context(s, signal) { return { expectedScopeKey: s.scopeKey, expectedQuery: Object.freeze({ ...s.query }), signal }; }
    async function applyQuery(next) {
      const before = snapshot();
      if (!before.active || before.selecting) return false;
      const query = queryOf(next);
      if (sameQuery(before.query, query)) return true;
      return runAction(signal => adapter.applyQuery(Object.freeze(query), context(before, signal)));
    }
     function closePopover(force) {
       if (destroyed) return;
        if (openAnchor && openAnchor.isConnected) openAnchor.setAttribute('aria-expanded', 'false');
        openAnchor = null;
        openKind = '';
       if (peopleController) peopleController.abort();
       if (placeController) placeController.abort();
      peopleController = null; placeController = null;
      popover.hidden = true; popover.replaceChildren();
      if (lastFocus && lastFocus.isConnected && !destroyed) lastFocus.focus({ preventScroll: true });
    }
    const popoverPreferredWidths = { person: 288, time: 196, group: 280, place: 288 };
    function positionPopover(anchor, kind) {
      const rect = anchor.getBoundingClientRect();
      // Each filter has a different natural density.  Keep list/search filters
      // comfortable, but do not give a seven-item year list the same wide panel.
      // 288px is 80% of the previous 360px desktop ceiling.
      const width = Math.min(popoverPreferredWidths[kind] || 288, Math.max(160, window.innerWidth - 24));
      popover.style.width = width + 'px';
      popover.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + 'px';
      popover.style.top = (rect.bottom + 8) + 'px';
      popover.hidden = false;
      const box = popover.getBoundingClientRect();
      if (box.bottom > window.innerHeight - 12) popover.style.top = Math.max(12, rect.top - box.height - 8) + 'px';
    }
      function setPopover(kind, anchor, html) {
        if (openAnchor && openAnchor !== anchor && openAnchor.isConnected) openAnchor.setAttribute('aria-expanded', 'false');
        openAnchor = anchor;
        openAnchor.setAttribute('aria-expanded', 'true');
        openKind = kind; lastFocus = document.activeElement;
       popover.innerHTML = html; positionPopover(anchor, kind);
     }
    async function loadPeople(q) {
      if (peopleController) peopleController.abort();
      peopleController = new window.AbortController();
      const status = popover.querySelector('[data-ot="people-status"]');
      if (status) status.textContent = '正在读取人物…';
      try {
        const result = await adapter.getPeople({ q: q || '', signal: peopleController.signal });
        people = Array.isArray(result) ? result.map(item => {
          const id = text(item && item.id).trim();
          const label = text(item && item.label).trim() || ('ID ' + id);
          if (id) rememberLabel(id, label);
          return { id, label, photoCount: Number(item && item.photoCount || 0) };
        }).filter(item => item.id) : [];
        renderPeople();
        if (status) status.textContent = '';
      } catch (error) { if (error.name !== 'AbortError' && status) status.textContent = '人物列表暂时无法读取。'; }
    }
    function renderPeople() {
      if (openKind !== 'person') return;
      const s = snapshot(); const selected = new Set(personIds(s.query.person));
      const list = popover.querySelector('[data-ot="people-list"]'); if (!list) return;
      const known = people.slice();
      for (const id of selected) if (!known.some(item => item.id === id)) known.unshift({ id, label: personLabel(id), photoCount: 0 });
      list.replaceChildren();
      if (!known.length) { list.innerHTML = '<p class="ot-home-help">还没有已命名人物。</p>'; return; }
      for (const person of known) {
        const row = document.createElement('label'); row.className = 'ot-home-option';
        const input = document.createElement('input'); input.type = 'checkbox'; input.value = person.id; input.checked = selected.has(person.id);
        const caption = document.createElement('span'); caption.textContent = person.label + (person.photoCount ? ' · ' + person.photoCount + ' 张' : '');
        row.append(input, caption); list.appendChild(row);
      }
    }
    async function togglePerson(id, on) {
      const s = snapshot(); const ids = personIds(s.query.person);
      const next = on ? [...ids, id].filter((item, i, all) => all.indexOf(item) === i) : ids.filter(item => item !== id);
      await applyQuery({ ...s.query, person: next.join(',') }); renderPeople();
    }
    function openPerson(anchor) {
      setPopover('person', anchor, '<header class="ot-home-pop-header"><strong>人物</strong><button data-ot="close" class="ot-home-close" type="button" aria-label="关闭筛选">关闭</button></header>'
        + '<label class="ot-home-label"><input data-ot="people-search" type="search" placeholder="搜索姓名或别名" autocomplete="off"></label>'
        + '<div data-ot="people-list" class="ot-home-option-list"></div><p data-ot="people-status" class="ot-home-help"></p>');
      listen(popover.querySelector('[data-ot="close"]'), 'click', () => closePopover());
      const search = popover.querySelector('[data-ot="people-search"]');
      listen(search, 'input', () => { window.clearTimeout(peopleTimer); peopleTimer = window.setTimeout(() => void loadPeople(search.value.trim()), 180); });
      listen(search, 'keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
      listen(popover.querySelector('[data-ot="people-list"]'), 'change', event => {
        const input = event.target.closest('input[type="checkbox"]'); if (input) void togglePerson(input.value, input.checked);
      });
      search.focus(); void loadPeople('');
    }
    function renderTime() {
      if (openKind !== 'time') return;
      const s = snapshot();
      const years = (timeline.years || []).filter(item => item.year && item.year !== 'unknown');
      const months = (timeline.months || []).filter(item => item.month && item.month.startsWith(timeYear + '-'));
      const list = popover.querySelector('[data-ot="time-list"]'); if (!list) return;
      list.replaceChildren();
      const add = (label, value, current) => {
        const button = document.createElement('button'); button.type = 'button';
        button.className = 'ot-home-option' + (current ? ' is-current' : '');
        button.dataset.time = value; button.textContent = label; list.appendChild(button);
      };
      add('全部时间', '', !s.query.dateFrom && !s.query.dateTo);
      if (!timeYear) {
        if (!years.length) {
          const wait = document.createElement('p');
          wait.className = 'ot-home-empty';
          wait.textContent = timelineLoading ? '正在读取年份…' : '还没有记录年份';
          list.appendChild(wait);
          return;
        }
        for (const item of years.slice(0, 24)) add(item.year + '年', 'y:' + item.year, s.query.dateFrom === item.year);
      } else {
        add('返回年份', 'back', false);
        add(timeYear + '年全年', 'y:' + timeYear, s.query.dateFrom === timeYear);
        const have = new Set(months.map(item => item.month));
        for (let month = 1; month <= 12; month += 1) {
          const key = timeYear + '-' + String(month).padStart(2, '0');
          if (have.size && !have.has(key)) continue;
          add(timeYear + '年' + month + '月', 'm:' + key, s.query.dateFrom === key);
        }
      }
    }
    function readTimelineCache() {
      try {
        const raw = window.sessionStorage.getItem('ot-timeline');
        if (!raw) return null;
        const data = JSON.parse(raw);
        return data && Array.isArray(data.years) ? data : null;
      } catch (_) { return null; }
    }
    function writeTimelineCache(data) {
      try {
        window.sessionStorage.setItem('ot-timeline', JSON.stringify({ years: data.years || [], months: data.months || [] }));
      } catch (_) { /* quota or private mode */ }
    }
    function ensureTimeline() {
      if (timeline && Array.isArray(timeline.years) && timeline.years.length) return Promise.resolve(timeline);
      if (timelinePromise) return timelinePromise;
      const cached = readTimelineCache();
      if (cached && cached.years.length) {
        timeline = cached;
        renderTime();
      } else {
        timelineLoading = true;
      }
      timelinePromise = Promise.resolve(typeof adapter.getTimeline === 'function' ? adapter.getTimeline({ signal: life.signal }) : { years: [], months: [] })
        .then(data => {
          if (data && Array.isArray(data.years)) {
            timeline = data;
            writeTimelineCache(data);
          }
          return timeline;
        })
        .catch(error => {
          if (error && error.name !== 'AbortError') report(error);
          return timeline;
        })
        .finally(() => {
          timelinePromise = null;
          timelineLoading = false;
          renderTime();
        });
      return timelinePromise;
    }
    async function applyTime(token) {
      const s = snapshot();
      if (token === '') { await applyQuery({ ...s.query, dateFrom: '', dateTo: '' }); closePopover(); return; }
      if (token === 'back') { timeYear = ''; renderTime(); return; }
      if (token.startsWith('y:')) {
        const year = token.slice(2);
        if (!timeYear) { timeYear = year; renderTime(); return; }
        await applyQuery({ ...s.query, dateFrom: year, dateTo: year }); closePopover(); return;
      }
      if (token.startsWith('m:')) { await applyQuery({ ...s.query, dateFrom: token.slice(2), dateTo: token.slice(2) }); closePopover(); }
    }
    async function openTime(anchor) {
      const s = snapshot();
      timeYear = /^\d{4}-\d{2}/.test(s.query.dateFrom) ? s.query.dateFrom.slice(0, 4) : '';
      setPopover('time', anchor, '<header class="ot-home-pop-header"><strong>时间</strong><button data-ot="close" class="ot-home-close" type="button" aria-label="关闭筛选">关闭</button></header><div data-ot="time-list" class="ot-home-option-list"></div>');
      listen(popover.querySelector('[data-ot="close"]'), 'click', () => closePopover());
      listen(popover.querySelector('[data-ot="time-list"]'), 'click', event => {
        const button = event.target.closest('[data-time]'); if (button) void applyTime(button.dataset.time);
      });
      renderTime();
      void ensureTimeline().then(() => renderTime());
    }
    function openGroup(anchor) {
      const s = snapshot();
      const range = groupRange(s.group);
      setPopover('group', anchor, '<header class="ot-home-pop-header"><strong>合影人数</strong><button data-ot="close" class="ot-home-close" type="button" aria-label="关闭筛选">关闭</button></header>'
        + '<form data-ot="group-form" class="ot-home-range-form"><div class="ot-home-range-fields">'
        + '<label><span>从</span><span class="ot-home-number-field"><input data-ot="group-min" type="number" min="1" max="9999" step="1" inputmode="numeric" placeholder="不限" aria-label="最少人数"><b>人</b></span></label>'
        + '<i aria-hidden="true">—</i><label><span>到</span><span class="ot-home-number-field"><input data-ot="group-max" type="number" min="1" max="9999" step="1" inputmode="numeric" placeholder="不限" aria-label="最多人数"><b>人</b></span></label></div>'
        + '<p data-ot="group-error" class="ot-home-range-error" hidden></p>'
        + '<div class="ot-home-range-actions"><button data-ot="group-clear" class="ot-home-button ot-home-quiet" type="button">清除</button><button class="ot-home-button ot-home-primary" type="submit">应用</button></div></form>');
      const form = popover.querySelector('[data-ot="group-form"]'), minInput = popover.querySelector('[data-ot="group-min"]'), maxInput = popover.querySelector('[data-ot="group-max"]');
      minInput.value = range.min || ''; maxInput.value = range.max || '';
      listen(popover.querySelector('[data-ot="close"]'), 'click', () => closePopover());
      const read = input => input.value === '' ? null : Number(input.value);
      listen(form, 'submit', event => {
        event.preventDefault(); if (typeof adapter.applyGroup !== 'function') return;
        const min = read(minInput), max = read(maxInput), error = popover.querySelector('[data-ot="group-error"]');
        const invalid = (min !== null && (!Number.isInteger(min) || min < 1 || min > 9999)) || (max !== null && (!Number.isInteger(max) || max < 1 || max > 9999));
        if (invalid || (min !== null && max !== null && min > max)) { error.textContent = invalid ? '人数请填写 1–9999 的整数。' : '起始人数不能大于结束人数。'; error.hidden = false; return; }
        error.hidden = true; const before = snapshot(); void runAction(signal => adapter.applyGroup(groupKey(min, max), context(before, signal))).then(ok => { if (ok) closePopover(); });
      });
      listen(popover.querySelector('[data-ot="group-clear"]'), 'click', () => { const before = snapshot(); void runAction(signal => adapter.applyGroup('', context(before, signal))).then(ok => { if (ok) closePopover(); }); });
      minInput.focus();
    }
    function renderPlaces() {
      if (openKind !== 'place') return;
      const s = snapshot(); const list = popover.querySelector('[data-ot="place-list"]'); if (!list) return;
      list.replaceChildren();
      const add = (label, value, current) => {
        const button = document.createElement('button'); button.type = 'button';
        button.className = 'ot-home-option' + (current ? ' is-current' : '');
        button.dataset.place = value; button.textContent = label; list.appendChild(button);
      };
      add('全部地点', '', !s.query.place);
      for (const item of places) add((item.label || item.place) + (item.count ? ' · ' + item.count + ' 张' : ''), item.place, s.query.place === item.place);
    }
    async function loadPlaces(q) {
      if (typeof adapter.getPlaces !== 'function') return;
      if (placeController) placeController.abort();
      placeController = new window.AbortController();
      try {
        const result = await adapter.getPlaces({ q: q || '', signal: placeController.signal });
        places = Array.isArray(result) ? result : [];
        renderPlaces();
      } catch (error) { if (error.name !== 'AbortError') report(error); }
    }
    function openPlace(anchor) {
      setPopover('place', anchor, '<header class="ot-home-pop-header"><strong>地点</strong><button data-ot="close" class="ot-home-close" type="button" aria-label="关闭筛选">关闭</button></header>'
        + '<label class="ot-home-label"><input data-ot="place-search" type="search" placeholder="搜索已有地点" autocomplete="off"></label>'
        + '<div data-ot="place-list" class="ot-home-option-list"></div>');
      listen(popover.querySelector('[data-ot="close"]'), 'click', () => closePopover());
      const search = popover.querySelector('[data-ot="place-search"]');
      listen(search, 'input', () => { window.clearTimeout(placeTimer); placeTimer = window.setTimeout(() => void loadPlaces(search.value.trim()), 180); });
      listen(search, 'keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
      listen(popover.querySelector('[data-ot="place-list"]'), 'click', event => {
        const button = event.target.closest('[data-place]');
        if (!button) return;
        const s = snapshot(); void applyQuery({ ...s.query, place: button.dataset.place }).then(() => closePopover());
      });
      search.focus(); void loadPlaces('');
    }
    async function chooseFolder() {
      if (folderPending || typeof adapter.chooseFolder !== 'function') return;
      const s = snapshot(); folderPending = true; report(null); sync();
      try {
        window.setTimeout(() => {
          const dialog = document.querySelector('#folder-dialog');
          folderControl.setAttribute('aria-expanded', String(Boolean(dialog && dialog.open)));
        }, 0);
        const path = await adapter.chooseFolder({ current: s.query.directory, signal: life.signal });
        if (path != null) await applyQuery({ ...s.query, directory: text(path) });
      } catch (error) { if (error.name !== 'AbortError') report(error); }
      finally { folderPending = false; folderControl.setAttribute('aria-expanded', 'false'); if (!destroyed) sync(); }
    }
    async function doSelection(method) {
      const s = snapshot();
      if (!s.active || !s.selecting || typeof adapter[method] !== 'function') return;
      await runAction(signal => adapter[method](context(s, signal)));
    }
    listen(all('person'), 'click', () => { if (openKind === 'person') closePopover(); else openPerson(all('person')); });
    listen(all('time'), 'click', () => { if (openKind === 'time') closePopover(); else void openTime(all('time')); });
    listen(all('group'), 'click', () => { if (openKind === 'group') closePopover(); else openGroup(all('group')); });
    listen(all('place'), 'click', () => { if (openKind === 'place') closePopover(); else openPlace(all('place')); });
    listen(all('folder'), 'click', () => void chooseFolder());
    listen(document.querySelector('#folder-dialog'), 'close', () => folderControl.setAttribute('aria-expanded', 'false'));
    listen(all('select'), 'click', async () => {
      const s = snapshot(); if (!s.active) return;
      const ok = await runAction(signal => adapter.setSelecting(true, context(s, signal)));
      if (ok && !destroyed) all('visible').focus({ preventScroll: true });
    });
    listen(all('sort'), 'change', async () => {
      const value = all('sort').value; const s = snapshot();
      if (!s.active || !SORTS.has(value) || busy) { sync(); return; }
      await runAction(signal => adapter.applySort(value, context(s, signal)));
    });
    listen(all('chips'), 'click', async event => {
      const button = event.target.closest('button[data-condition]'); if (!button || busy) return;
      const s = snapshot(); if (!s.active || s.selecting) return;
      const key = button.dataset.condition; const next = { ...s.query };
      if (key === 'time') { next.dateFrom = ''; next.dateTo = ''; }
      else if (key === 'place') next.place = '';
      else if (key === 'directory') next.directory = '';
      else if (key === 'group') { if (typeof adapter.applyGroup === 'function') await runAction(signal => adapter.applyGroup('', context(s, signal))); return; }
      else if (key.startsWith('person:')) next.person = personIds(s.query.person).filter(id => id !== key.slice(7)).join(',');
      await applyQuery(next);
    });
    for (const [key, method] of [['visible','selectVisible'],['clear-selection','clearSelection'],['edit','editSelected'],['exclude','excludeSelected'],['restore','restoreSelected']]) listen(all(key), 'click', () => void doSelection(method));
    listen(all('cancel-selection'), 'click', async () => {
      const s = snapshot();
      if (s.active) { const ok = await runAction(signal => adapter.setSelecting(false, context(s, signal))); if (ok) all('select').focus({ preventScroll: true }); }
    });
    listen(all('refresh'), 'click', async () => {
      const s = snapshot();
      if (s.active && !s.selecting && typeof adapter.refresh === 'function') await runAction(signal => adapter.refresh(context(s, signal)));
    });
    listen(document, 'keydown', event => { if (event.key === 'Escape' && openKind) { event.stopPropagation(); closePopover(); } });
    listen(document, 'mousedown', event => { if (openKind && !popover.contains(event.target) && !root.contains(event.target)) closePopover(); });
    snapshot(); host.appendChild(root); document.body.appendChild(popover);
    void ensureTimeline();
    const controller = Object.freeze({
      sync,
      rememberPersonLabel: rememberLabel,
      destroy() {
        if (destroyed) return;
        closePopover(true); destroyed = true; life.abort();
        window.clearTimeout(peopleTimer); window.clearTimeout(placeTimer);
        root.remove(); popover.remove(); mounts.delete(host);
      }
    });
    mounts.set(host, controller); sync(); return controller;
  }
  if (global.OurTimeHomeUI) throw new Error('OurTimeHomeUI was loaded twice.');
  global.OurTimeHomeUI = Object.freeze({ version: VERSION, mount });
})(window);
