/* OurTime structured photo query controls. */
(function (global) {
  'use strict';
  const VERSION = '2.0.0';
  const SORTS = new Map([
    ['date_desc', '最新优先'], ['date_asc', '最早优先'],
    ['name_asc', '文件名顺序'], ['name_desc', '文件名倒序']
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
    select: svg('<rect x="4" y="4" width="16" height="16" rx="4"/><path d="m8 12 3 3 5-6"/>'),
    close: svg('<path d="m7 7 10 10M17 7 7 17"/>')
  };
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
      + '<button data-ot="person" class="ot-home-button" type="button">人物</button>'
      + '<button data-ot="time" class="ot-home-button" type="button">时间</button>'
      + '<button data-ot="place" class="ot-home-button" type="button">地点</button>'
      + '<button data-ot="folder" class="ot-home-button" type="button">文件夹</button>'
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
    for (const [value, label] of SORTS) all('sort').add(new window.Option(label, value));
    let destroyed = false, busy = false, lastScope = null, lastFocus = null, openKind = '';
    let people = [], peopleLabels = new Map(), places = [], timeline = { years: [], months: [] };
    let peopleTimer = null, placeTimer = null, peopleController = null, placeController = null, folderPending = false, timeYear = '';
    function listen(el, event, handler) { if (el) el.addEventListener(event, handler, { signal: life.signal }); }
    function snapshot() {
      const raw = adapter.readState();
      if (!raw || typeof raw !== 'object' || raw.then) throw new TypeError('readState must return a synchronous object.');
      const sort = text(raw.sort || 'date_desc');
      if (!SORTS.has(sort)) throw new Error('Unsupported homepage sort: ' + sort);
      return {
        active: raw.active === true, scopeKey: text(raw.scopeKey), query: queryOf(raw.query), sort,
        selecting: raw.selecting === true,
        selectedCount: Number.isFinite(Number(raw.selectedCount)) ? Math.max(0, Math.floor(Number(raw.selectedCount))) : 0,
        selectionKind: raw.selectionKind === 'restore' ? 'restore' : 'exclude',
        refreshAvailable: raw.refreshAvailable === true
      };
    }
    const isGroup = s => String(s.scopeKey || '').startsWith('group:');
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
      all('place').hidden = grouped; all('folder').hidden = grouped; all('select').hidden = grouped;
      root.querySelector('.ot-home-query').hidden = s.selecting;
      all('batch').hidden = !s.selecting;
      mark('person', s.query.person); mark('time', s.query.dateFrom || s.query.dateTo);
      mark('place', s.query.place); mark('folder', s.query.directory);
      all('selected-count').textContent = '已选择 ' + s.selectedCount + ' 张';
      all('exclude').hidden = s.selectionKind === 'restore';
      all('edit').hidden = s.selectionKind === 'restore';
      all('restore').hidden = s.selectionKind !== 'restore';
      all('restore').disabled = busy || !s.selectedCount || typeof adapter.restoreSelected !== 'function';
      for (const key of ['person','time','place','folder','select','sort','visible','cancel-selection','refresh']) if (all(key)) all(key).disabled = busy || folderPending;
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
       openKind = '';
       if (peopleController) peopleController.abort();
       if (placeController) placeController.abort();
      peopleController = null; placeController = null;
      popover.hidden = true; popover.replaceChildren();
      if (lastFocus && lastFocus.isConnected && !destroyed) lastFocus.focus({ preventScroll: true });
    }
    function positionPopover(anchor) {
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(360, Math.max(260, window.innerWidth - 24));
      popover.style.width = width + 'px';
      popover.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + 'px';
      popover.style.top = (rect.bottom + 8) + 'px';
      popover.hidden = false;
      const box = popover.getBoundingClientRect();
      if (box.bottom > window.innerHeight - 12) popover.style.top = Math.max(12, rect.top - box.height - 8) + 'px';
    }
     function setPopover(kind, anchor, html) {
       openKind = kind; lastFocus = document.activeElement;
       popover.innerHTML = html; positionPopover(anchor);
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
      if (!timeYear) for (const item of years.slice(0, 24)) add(item.year + '年', 'y:' + item.year, s.query.dateFrom === item.year);
      else {
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
      if (typeof adapter.getTimeline === 'function') {
        try { timeline = await adapter.getTimeline({ signal: life.signal }) || timeline; } catch (error) { if (error.name !== 'AbortError') report(error); }
      }
      renderTime();
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
        const path = await adapter.chooseFolder({ current: s.query.directory, signal: life.signal });
        if (path != null) await applyQuery({ ...s.query, directory: text(path) });
      } catch (error) { if (error.name !== 'AbortError') report(error); }
      finally { folderPending = false; if (!destroyed) sync(); }
    }
    async function doSelection(method) {
      const s = snapshot();
      if (!s.active || !s.selecting || typeof adapter[method] !== 'function') return;
      await runAction(signal => adapter[method](context(s, signal)));
    }
    listen(all('person'), 'click', () => { if (openKind === 'person') closePopover(); else openPerson(all('person')); });
    listen(all('time'), 'click', () => { if (openKind === 'time') closePopover(); else void openTime(all('time')); });
    listen(all('place'), 'click', () => { if (openKind === 'place') closePopover(); else openPlace(all('place')); });
    listen(all('folder'), 'click', () => void chooseFolder());
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
