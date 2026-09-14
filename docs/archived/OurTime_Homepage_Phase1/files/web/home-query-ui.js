/* OurTime homepage query controls, Phase 1.
 * Plain browser JavaScript. No HTTP, storage, database, viewer or grid ownership.
 * The host adapter is the single source of applied query/selection state.
 */
(function (global) {
  'use strict';
  const VERSION = '1.0.0';
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
    directory: text(value && value.directory).trim()
  });
  const sameQuery = (a, b) => a.q === b.q && a.person === b.person && a.directory === b.directory;
  const shortPath = value => {
    if (/^[a-z]:[\\/]?$/i.test(value)) return value;
    const parts = value.replace(/[\\/]+$/, '').split(/[\\/]/);
    return parts[parts.length - 1] || value;
  };
  const svg = (body) => '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + body + '</svg>';
  const ICONS = {
    search: svg('<circle cx="10.7" cy="10.7" r="6.7"/><path d="m16 16 4.5 4.5"/>'),
    filter: svg('<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2.3"/><circle cx="15" cy="17" r="2.3"/>'),
    select: svg('<rect x="4" y="4" width="16" height="16" rx="4"/><path d="m8 12 3 3 5-6"/>'),
    close: svg('<path d="m7 7 10 10M17 7 7 17"/>')
  };

  function mount(options) {
    const host = options && options.host;
    const adapter = options && options.adapter;
    if (!host || host.nodeType !== 1 || !host.ownerDocument) {
      throw new TypeError('OurTimeHomeUI.mount requires a host element.');
    }
    if (mounts.has(host)) throw new Error('This homepage host is already mounted.');
    for (const key of REQUIRED) {
      if (!adapter || typeof adapter[key] !== 'function') {
        throw new TypeError('Homepage adapter is missing: ' + key);
      }
    }
    const document = host.ownerDocument;
    const window = document.defaultView;
    const life = new window.AbortController();
    const root = document.createElement('section');
    root.className = 'ot-home-ui';
    root.setAttribute('aria-label', '照片查询与选择');
    // Only constant, owned markup is assigned with innerHTML. User data uses textContent.
    root.innerHTML = `
      <form class="ot-home-query" role="search" aria-label="搜索照片">
        <label class="ot-home-search">
          <span class="ot-home-icon">${ICONS.search}</span>
          <span class="ot-home-sr-only">搜索照片</span>
          <input data-ot="search" type="search" placeholder="搜索照片…" autocomplete="off"
            title="按现有关键词搜索；精确人物和文件夹条件请使用筛选">
        </label>
        <button data-ot="filters" class="ot-home-button" type="button" aria-haspopup="dialog" aria-expanded="false">
          ${ICONS.filter}<span>筛选</span><span data-ot="badge" class="ot-home-badge" hidden></span>
        </button>
        <button data-ot="select" class="ot-home-button" type="button">${ICONS.select}<span>选择</span></button>
        <label class="ot-home-sort"><span class="ot-home-sr-only">照片排序</span>
          <select data-ot="sort" aria-label="照片排序"></select>
        </label>
      </form>
      <div data-ot="batch" class="ot-home-batch" role="group" aria-label="批量选择操作" hidden>
        <strong data-ot="selected-count" class="ot-home-selected-count" role="status" aria-live="polite"></strong>
        <button data-ot="visible" class="ot-home-button ot-home-quiet" type="button">选择当前屏幕</button>
        <button data-ot="clear-selection" class="ot-home-button ot-home-quiet" type="button">清空</button>
        <span class="ot-home-spacer" aria-hidden="true"></span>
        <button data-ot="exclude" class="ot-home-button ot-home-danger" type="button">排除所选…</button>
        <button data-ot="restore" class="ot-home-button" type="button" hidden>恢复所选</button>
        <button data-ot="edit" class="ot-home-button" type="button">批量补录</button>
        <button data-ot="cancel-selection" class="ot-home-button" type="button">取消</button>
      </div>
      <div data-ot="chips" class="ot-home-chips" aria-label="已应用的查询条件" hidden></div>
      <div data-ot="notice" class="ot-home-notice" hidden>
        <span>照片库有更新，可刷新当前列表。</span>
        <button data-ot="refresh" class="ot-home-link" type="button">刷新照片</button>
      </div>
      <p data-ot="error" class="ot-home-error" role="alert" hidden></p>`;

    const dialog = document.createElement('dialog');
    dialog.className = 'ot-home-filter-dialog';
    dialog.setAttribute('aria-label', '筛选照片');
    do { dialog.id = 'ot-home-filter-' + (++instanceCounter); } while (document.getElementById(dialog.id));
    root.querySelector('[data-ot="filters"]').setAttribute('aria-controls', dialog.id);
    dialog.innerHTML = `
      <form class="ot-home-filter-form" aria-label="精确筛选">
        <header class="ot-home-filter-header">
          <div><h2>筛选照片</h2><p>条件同时满足时，照片才会显示。</p></div>
          <button data-ot="close" class="ot-home-icon-button" type="button" aria-label="关闭筛选">${ICONS.close}</button>
        </header>
        <div class="ot-home-filter-body">
          <fieldset class="ot-home-fieldset">
            <legend>人物</legend>
            <label class="ot-home-label ot-home-people-search"><span class="ot-home-sr-only">查找已有人物</span>
              <input data-ot="people-search" type="search" placeholder="搜索已有人物…" autocomplete="off"
                aria-label="查找已有人物"></label>
            <label class="ot-home-label"><span class="ot-home-sr-only">筛选人物</span>
              <select data-ot="person" size="5" aria-label="筛选人物"><option value="">全部人物</option></select>
            </label>
            <p data-ot="people-status" class="ot-home-help" role="status"></p>
          </fieldset>
          <fieldset class="ot-home-fieldset">
            <legend>文件夹</legend>
            <label class="ot-home-label"><span class="ot-home-sr-only">文件夹路径</span>
              <input data-ot="directory" type="text" placeholder="粘贴照片文件夹路径" autocomplete="off" spellcheck="false">
            </label>
            <button data-ot="choose-folder" class="ot-home-button ot-home-folder-button" type="button" hidden>选择文件夹…</button>
            <p class="ot-home-help">包含子文件夹；只筛选现有资料，不会扫描或添加照片。</p>
          </fieldset>
          <p data-ot="filter-error" class="ot-home-error" role="alert" hidden></p>
        </div>
        <footer class="ot-home-filter-footer">
          <button data-ot="reset-draft" class="ot-home-link" type="button">重置条件</button>
          <span class="ot-home-spacer" aria-hidden="true"></span>
          <button data-ot="cancel" class="ot-home-button" type="button">取消</button>
          <button data-ot="apply" class="ot-home-button ot-home-primary" type="submit">应用筛选</button>
        </footer>
      </form>`;
    const all = name => root.querySelector(`[data-ot="${name}"]`) || dialog.querySelector(`[data-ot="${name}"]`);
    for (const [value, label] of SORTS) all('sort').add(new window.Option(label, value));
    const queryForm = root.querySelector('form');
    const filterForm = dialog.querySelector('form');
    let destroyed = false;
    let busy = false;
    let composing = false;
    let searchDirty = false;
    let searchTimer = null;
    let draft = null;
    let openedState = null;
    let openToken = 0;
    let peopleController = null;
    let people = [];
    let peopleLoaded = false;
    let folderPending = false;
    let lastFocus = null;
    let lastScope = null;

    function listen(element, event, handler) {
      element.addEventListener(event, handler, { signal: life.signal });
    }
    function snapshot() {
      const raw = adapter.readState();
      if (!raw || typeof raw !== 'object' || raw.then) throw new TypeError('readState must return a synchronous object.');
      const sort = text(raw.sort || 'date_desc');
      if (!SORTS.has(sort)) throw new Error('Unsupported homepage sort: ' + sort);
      return {
        active: raw.active === true,
        scopeKey: text(raw.scopeKey),
        query: queryOf(raw.query), sort,
        selecting: raw.selecting === true,
        selectedCount: Number.isFinite(Number(raw.selectedCount)) ? Math.max(0, Math.floor(Number(raw.selectedCount))) : 0,
        selectionKind: raw.selectionKind === 'restore' ? 'restore' : 'exclude',
        refreshAvailable: raw.refreshAvailable === true,
        personLabel: text(raw.personLabel)
      };
    }
    function report(error, inDialog) {
      const el = all(inDialog ? 'filter-error' : 'error');
      el.textContent = error ? (text(error.message || error) || '操作未完成，请重试。') : '';
      el.hidden = !error;
    }
    function currentPersonLabel(s) {
      if (s.personLabel) return s.personLabel;
      const ids = s.query.person.split(',').filter(Boolean);
      return ids.map(id => {
        const found = people.find(person => person.id === id);
        return found ? found.label : 'ID ' + id;
      }).join('、');
    }
    function chips(s) {
      const box = all('chips');
      box.replaceChildren();
      const entries = [
        ['q', '关键词', s.query.q, s.query.q],
        ['person', '人物', s.query.person, currentPersonLabel(s)],
        ['directory', '文件夹', s.query.directory, shortPath(s.query.directory)]
      ].filter(entry => entry[2] !== '');
      box.hidden = !entries.length;
      for (const [key, label, raw, display] of entries) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'ot-home-chip';
        button.dataset.condition = key;
        button.title = label + '：' + raw;
        button.setAttribute('aria-label', '移除' + label + '条件：' + display);
        const caption = document.createElement('span');
        caption.textContent = label + '：' + display;
        button.appendChild(caption);
        const close = document.createElement('span');
        close.textContent = '×'; close.setAttribute('aria-hidden', 'true');
        button.appendChild(close);
        button.disabled = busy || s.selecting;
        box.appendChild(button);
      }
      if (entries.length) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'ot-home-link';
        button.dataset.condition = 'all'; button.textContent = '清除全部';
        button.disabled = busy || s.selecting;
        box.appendChild(button);
      }
    }
    function sync() {
      if (destroyed) return;
      const s = snapshot();
      if (lastScope !== null && (lastScope !== s.scopeKey || !s.active)) {
        clearTimeout(searchTimer); searchTimer = null; searchDirty = false;
        if (dialog.open) closeFilters(true);
      }
      lastScope = s.scopeKey;
      root.hidden = !s.active;
      if (!searchDirty && !composing) all('search').value = s.query.q;
      all('sort').value = s.sort;
      queryForm.hidden = s.selecting;
      all('batch').hidden = !s.selecting;
      root.setAttribute('aria-busy', String(busy));
      const count = Number(Boolean(s.query.person)) + Number(Boolean(s.query.directory));
      all('badge').textContent = text(count); all('badge').hidden = !count;
      all('filters').setAttribute('aria-expanded', String(dialog.open));
      all('filters').classList.toggle('ot-home-filter-active', count > 0);
      all('selected-count').textContent = '已选择 ' + s.selectedCount + ' 张';
      all('exclude').hidden = s.selectionKind === 'restore';
      all('edit').hidden = s.selectionKind === 'restore';
      all('restore').hidden = s.selectionKind !== 'restore';
      all('restore').disabled = busy || !s.selectedCount || typeof adapter.restoreSelected !== 'function';
      for (const key of ['filters', 'select', 'sort', 'visible', 'cancel-selection', 'refresh']) all(key).disabled = busy;
      for (const key of ['clear-selection', 'exclude', 'edit']) all(key).disabled = busy || !s.selectedCount;
      all('notice').hidden = !s.refreshAvailable || s.selecting || typeof adapter.refresh !== 'function';
      for (const key of ['apply', 'reset-draft', 'person', 'directory', 'people-search', 'choose-folder']) {
        all(key).disabled = busy || folderPending;
      }
      all('cancel').disabled = busy; all('close').disabled = busy;
      chips(s);
    }
    async function runAction(callback, inDialog) {
      if (busy || destroyed) return false;
      busy = true; report(null, inDialog); sync();
      let succeeded = false;
      try {
        await callback(life.signal);
        succeeded = !destroyed;
      } catch (error) {
        if (!destroyed && error.name !== 'AbortError') report(error, inDialog && dialog.open);
      } finally {
        busy = false;
        if (!destroyed) {
          sync();
          if (searchDirty && !composing && !dialog.open) scheduleSearch();
        }
      }
      return succeeded;
    }
    function context(s, signal) {
      return { expectedScopeKey: s.scopeKey, expectedQuery: Object.freeze({ ...s.query }), signal };
    }
    async function applyQuery(next, inDialog) {
      const before = snapshot();
      if (!before.active || before.selecting) return false;
      const query = queryOf(next);
      if (sameQuery(before.query, query)) return true;
      return runAction(signal => adapter.applyQuery(Object.freeze(query), context(before, signal)), inDialog);
    }
    function scheduleSearch() {
      clearTimeout(searchTimer);
      if (destroyed || composing) return;
      searchTimer = window.setTimeout(() => {
        searchTimer = null;
        if (!busy) void commitSearch();
      }, 300);
    }
    async function commitSearch() {
      clearTimeout(searchTimer); searchTimer = null;
      if (composing || busy || destroyed) return false;
      if (!searchDirty) return true;
      const before = snapshot();
      if (!before.active || before.selecting) return false;
      const value = all('search').value;
      searchDirty = false;
      const ok = await applyQuery({ ...before.query, q: value });
      // Failure leaves authoritative state unchanged; typed text remains available for retry.
      if (!ok && !destroyed && !searchDirty) {
        all('search').value = value;
      }
      return ok;
    }
    function closeFilters(force = false) {
      if (busy && !force) return;
      if (!dialog.open && !draft) return;
      openToken += 1;
      if (peopleController) peopleController.abort();
      peopleController = null; folderPending = false;
      draft = null; openedState = null;
      if (dialog.open) dialog.close();
      all('filters').setAttribute('aria-expanded', 'false');
      if (lastFocus && lastFocus.isConnected && !destroyed) lastFocus.focus({ preventScroll: true });
    }
    function renderPeople() {
      if (!draft || destroyed) return;
      const needle = all('people-search').value.trim().toLocaleLowerCase();
      const matches = people.filter(person => !needle || person.label.toLocaleLowerCase().includes(needle));
      const select = all('person');
      select.replaceChildren(new window.Option('全部人物', ''));
      const visible = matches.slice(0, 200);
      if (draft.person && !visible.some(person => person.id === draft.person)) {
        const known = people.find(person => person.id === draft.person);
        select.add(new window.Option(known ? known.label : '当前已选人物（' + draft.person + '）', draft.person));
      }
      for (const person of visible) select.add(new window.Option(person.label, person.id));
      select.value = draft.person;
      if (peopleLoaded) {
        all('people-status').textContent = matches.length > 200
          ? '匹配 ' + matches.length + ' 位，显示前 200 位。可继续输入姓名缩小范围。'
          : (people.length ? '只显示包含所选人物的照片。' : '当前没有可选人物。');
      }
    }
    async function loadPeople(token) {
      peopleController = new window.AbortController();
      const controller = peopleController;
      all('people-status').textContent = '正在读取已有人物…';
      peopleLoaded = false;
      try {
        const result = await adapter.getPeople({ signal: controller.signal });
        if (destroyed || !draft || token !== openToken || controller.signal.aborted) return;
        if (!Array.isArray(result)) throw new TypeError('人物数据格式不正确。');
        const unique = new Map();
        for (const item of result) {
          if (!item || item.id == null || !text(item.id).trim()) continue;
          const id = text(item.id).trim();
          unique.set(id, { id, label: text(item.label).trim() || 'ID ' + id });
        }
        people = [...unique.values()]; peopleLoaded = true;
        renderPeople(); sync();
      } catch (error) {
        if (!destroyed && draft && token === openToken && error.name !== 'AbortError') {
          all('people-status').textContent = '人物列表暂时无法读取；现有的人物条件仍保留。';
        }
      }
    }
    async function openFilters() {
      if (destroyed || busy || composing) return false;
      if (!await commitSearch()) return false;
      const s = snapshot();
      if (!s.active || s.selecting || dialog.open) return false;
      draft = { ...s.query }; openedState = s;
      openToken += 1;
      all('directory').value = draft.directory;
      all('people-search').value = '';
      all('choose-folder').hidden = typeof adapter.chooseFolder !== 'function';
      report(null, true); renderPeople();
      lastFocus = document.activeElement;
      dialog.showModal(); sync();
      all('people-search').focus();
      void loadPeople(openToken);
      return true;
    }
    async function applyDraft() {
      if (!draft || busy || folderPending || destroyed) return;
      const s = snapshot();
      if (!openedState || s.scopeKey !== openedState.scopeKey || !sameQuery(s.query, openedState.query)) {
        report('当前查询范围已变化，请关闭筛选后重新打开。', true); return;
      }
      const token = openToken;
      const next = { ...s.query, person: draft.person, directory: all('directory').value.trim() };
      const ok = await applyQuery(next, true);
      if (ok && !destroyed && token === openToken) closeFilters();
    }
    async function chooseFolder() {
      if (!draft || busy || folderPending || typeof adapter.chooseFolder !== 'function') return;
      const token = openToken;
      folderPending = true; report(null, true); sync();
      try {
        const path = await adapter.chooseFolder({ current: all('directory').value, signal: life.signal });
        if (destroyed || !draft || token !== openToken) return;
        // Null means canceled. Empty text means an explicit clear, never a scan request.
        if (path != null) all('directory').value = text(path);
      } catch (error) {
        if (!destroyed && draft && token === openToken && error.name !== 'AbortError') report(error, true);
      } finally {
        if (!destroyed && token === openToken) { folderPending = false; sync(); }
      }
    }
    async function doSelection(method) {
      if (busy || destroyed) return;
      const s = snapshot();
      if (!s.active || !s.selecting) return;
      if (['editSelected', 'excludeSelected', 'restoreSelected', 'clearSelection'].includes(method) && !s.selectedCount) return;
      if (method === 'excludeSelected' && s.selectionKind === 'restore') return;
      if (method === 'restoreSelected' && s.selectionKind !== 'restore') return;
      if (typeof adapter[method] !== 'function') return;
      await runAction(signal => adapter[method](context(s, signal)));
    }
    listen(all('search'), 'compositionstart', () => { composing = true; clearTimeout(searchTimer); });
    listen(all('search'), 'compositionend', () => { composing = false; searchDirty = true; scheduleSearch(); });
    listen(all('search'), 'input', () => { searchDirty = true; if (!composing) scheduleSearch(); });
    listen(queryForm, 'submit', event => {
      event.preventDefault(); if (event.isComposing || composing) return;
      searchDirty = true; void commitSearch();
    });
    listen(all('filters'), 'click', () => void openFilters());
    listen(all('select'), 'click', async () => {
      if (busy || !await commitSearch()) return;
      const s = snapshot();
      if (!s.active) return;
      const ok = await runAction(signal => adapter.setSelecting(true, context(s, signal)));
      if (ok && !destroyed) all('visible').focus({ preventScroll: true });
    });
    listen(all('sort'), 'change', async () => {
      const value = all('sort').value;
      if (busy || !await commitSearch()) { sync(); return; }
      const s = snapshot();
      if (!s.active || !SORTS.has(value)) return;
      await runAction(signal => adapter.applySort(value, context(s, signal)));
    });
    listen(all('chips'), 'click', async event => {
      const button = event.target.closest('button[data-condition]');
      if (!button || button.disabled || busy) return;
      if (!await commitSearch()) return;
      const s = snapshot();
      if (!s.active || s.selecting) return;
      const key = button.dataset.condition;
      const next = key === 'all' ? queryOf({}) : { ...s.query, [key]: '' };
      const ok = await applyQuery(next);
      if (ok && !destroyed) all('filters').focus({ preventScroll: true });
    });
    for (const [key, method] of [
      ['visible', 'selectVisible'], ['clear-selection', 'clearSelection'],
      ['edit', 'editSelected'], ['exclude', 'excludeSelected'], ['restore', 'restoreSelected']
    ]) listen(all(key), 'click', () => void doSelection(method));
    listen(all('cancel-selection'), 'click', async () => {
      const s = snapshot();
      if (s.active) {
        const ok = await runAction(signal => adapter.setSelecting(false, context(s, signal)));
        if (ok && !destroyed) all('select').focus({ preventScroll: true });
      }
    });
    listen(all('refresh'), 'click', async () => {
      if (busy || !await commitSearch()) return;
      const s = snapshot();
      if (s.active && !s.selecting && typeof adapter.refresh === 'function') {
        await runAction(signal => adapter.refresh(context(s, signal)));
      }
    });
    listen(filterForm, 'submit', event => { event.preventDefault(); if (!event.isComposing) void applyDraft(); });
    for (const key of ['cancel', 'close']) listen(all(key), 'click', () => closeFilters());
    listen(dialog, 'cancel', event => { event.preventDefault(); event.stopPropagation(); closeFilters(); });
    listen(dialog, 'keydown', event => { if (event.key === 'Escape') event.stopPropagation(); });
    listen(dialog, 'click', event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeFilters();
    });
    listen(all('people-search'), 'input', renderPeople);
    listen(all('people-search'), 'keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
    listen(all('person'), 'change', () => { if (draft) draft.person = all('person').value; });
    listen(all('reset-draft'), 'click', () => {
      if (!draft || busy) return;
      draft.person = ''; all('directory').value = ''; all('people-search').value = ''; renderPeople();
    });
    listen(all('choose-folder'), 'click', () => void chooseFolder());
    // Validate before touching the real page so a missing host contract leaves old controls intact.
    snapshot();
    host.appendChild(root);
    document.body.appendChild(dialog);
    const controller = Object.freeze({
      sync, openFilters,
      closeFilters,
      destroy() {
        if (destroyed) return;
        closeFilters(true); destroyed = true;
        life.abort(); clearTimeout(searchTimer);
        if (peopleController) peopleController.abort();
        root.remove(); dialog.remove(); mounts.delete(host);
      }
    });
    mounts.set(host, controller);
    sync();
    return controller;
  }
  if (global.OurTimeHomeUI) throw new Error('OurTimeHomeUI was loaded twice.');
  global.OurTimeHomeUI = Object.freeze({ version: VERSION, mount });
})(window);
