/* Local adapter for the Phase 1 homepage controls. Keep domain behavior in app.js/waterfall.js. */
(function (global) {
  'use strict';
  const host = document.querySelector('#home-query-host');
  const app = global.__ourTimeApp;
  if (!host || !app || !global.OurTimeHomeBridge || !global.OurTimeHomeUI) return;

  let homeUI = null;
  let folderResolve = null;
  let folderReject = null;
  const sync = () => { if (homeUI) homeUI.sync(); syncLegacyControls(); };
  global.__ourTimeHomeSync = sync;
  global.__ourTimeHomeFolderResolve = path => {
    const resolve = folderResolve;
    folderResolve = null;
    folderReject = null;
    if (resolve) resolve(path == null ? null : (path || ''));
  };
  const folderDialog = document.querySelector('#folder-dialog');
  folderDialog?.addEventListener('close', () => {
    if (app.state.folderTarget === 'home-query' && folderResolve) global.__ourTimeHomeFolderResolve(null);
  });

  const legacy = ['.filters', '.directory-filter', '#batch-bar', '#timeline-tools', '.browse-options']
    .map(selector => document.querySelector(selector)).filter(Boolean);
  const originalHidden = new Map(legacy.map(element => [element, element.hidden]));
  const timelineSortButtons = [...document.querySelectorAll('#timeline-tools [data-time-sort]')];
  const originalTimelineSortHidden = new Map(timelineSortButtons.map(element => [element, element.hidden]));
  const isHomeView = source => source && (source.view === 'timeline' || source.view === 'all');
  function syncLegacyControls() {
    const active = isHomeView(app.state);
    for (const element of legacy) {
      if (element.id === 'timeline-tools') element.hidden = active ? false : originalHidden.get(element);
      else element.hidden = active ? true : originalHidden.get(element);
    }
    for (const element of timelineSortButtons) element.hidden = active ? true : originalTimelineSortHidden.get(element);
  }

  const adapter = global.OurTimeHomeBridge.create({
    source: app.state,
    legacyInputs: {
      q: document.querySelector('#search'),
      person: document.querySelector('#person-filter'),
      directory: document.querySelector('#directory-filter'),
      sort: document.querySelector('#sort-order')
    },
    isActive: isHomeView,
    scopeKey: source => isHomeView(source) ? 'home' : '',
    reloadPhotos: ({ signal } = {}) => {
      if (signal && signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
      return global.loadPhotos();
    },
    getPeople: async ({ signal } = {}) => {
      const result = [];
      let offset = 0;
      let total = Infinity;
      while (offset < total) {
        if (signal && signal.aborted) throw new DOMException('Aborted', 'AbortError');
        const page = await app.api(app.peopleQuery({ offset, limit: 48 }), { signal });
        total = Number(page.total || 0);
        for (const person of page.items || []) {
          result.push({ id: String(person.id), label: app.personLabel(person) });
        }
        const next = (page.items || []).length;
        if (!next) break;
        offset += next;
      }
      return result;
    },
    setSelecting: async on => {
      app.state.selecting = Boolean(on);
      app.state.selected.clear();
      global.streamSelection();
    },
    selectVisible: async () => {
      for (const id of global.streamVisibleIds()) {
        if (app.state.selected.size >= 1000) break;
        app.state.selected.add(id);
      }
      global.streamSelection();
    },
    clearSelection: async () => {
      app.state.selected.clear();
      global.streamSelection();
    },
    editSelected: async () => {
      document.querySelector('#batch-description').textContent = `将补录 ${app.state.selected.size} 张照片。原始元数据会完整保留。`;
      document.querySelector('#batch-form').reset();
      app.showDialog('#batch-dialog');
    },
    excludeSelected: async () => app.exclusionDialog([...app.state.selected]),
    restoreSelected: async () => app.restoreAssets([...app.state.selected]),
    chooseFolder: ({ signal } = {}) => {
      if (signal && signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
      if (folderResolve) return Promise.reject(new Error('目录选择器正在打开。'));
      app.state.folderTarget = 'home-query';
      void app.openFolder(app.state.directory || '').catch(error => {
        const reject = folderReject;
        folderResolve = null;
        folderReject = null;
        if (reject) reject(error);
      });
      return new Promise((resolve, reject) => {
        folderResolve = resolve;
        folderReject = reject;
        if (signal) signal.addEventListener('abort', () => {
          if (folderReject === reject) {
            folderResolve = null;
            folderReject = null;
            reject(new DOMException('Aborted', 'AbortError'));
          }
        }, { once: true });
      });
    },
    personLabel: id => {
      const person = (app.state.people || []).find(item => String(item.id) === String(id));
      return person ? app.personLabel(person) : '';
    },
    selectionKind: source => source.view === 'excluded' ? 'restore' : 'exclude',
    onStateChange: sync
  });

  try {
    homeUI = global.OurTimeHomeUI.mount({ host, adapter });
  } catch (error) {
    console.error('[homepage-phase1] mount failed', error);
    return;
  }
  global.__ourTimeHomeUI = homeUI;
  syncLegacyControls();
  document.addEventListener('click', () => global.setTimeout(sync, 0), { passive: true });
})(window);
