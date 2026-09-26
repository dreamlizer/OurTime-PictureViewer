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

  const collectionHeading = host.closest('.toolbar')?.querySelector('#collection-title')?.closest('.section-heading');
  const homeYearsLink = document.querySelector('#home-years-link');
  const legacy = [collectionHeading, ...['.filters', '.directory-filter', '#batch-bar', '#timeline-tools', '.browse-options']
    .map(selector => document.querySelector(selector))].filter(Boolean);
  const originalHidden = new Map(legacy.map(element => [element, element.hidden]));
  const isHomeView = source => source && (source.view === 'timeline' || source.view === 'public-figures' || String(source.view || '').startsWith('group:'));
  function syncLegacyControls() {
    const active = isHomeView(app.state);
    if (homeYearsLink) homeYearsLink.hidden = true;
    document.body.classList.toggle('is-home-view', Boolean(active));
    document.body.classList.toggle('is-group-query', Boolean(active && String(app.state.view || '').startsWith('group:')));
    document.body.classList.toggle('is-add-photos-view', app.state.view === 'scan');
    for (const element of legacy) {
      element.hidden = active ? true : originalHidden.get(element);
    }
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
    scopeKey: source => {
      if (!source) return '';
      if (source.view === 'timeline') return 'home';
      if (source.view === 'public-figures') return source.publicGroup ? 'public-figures:' + source.publicGroup : 'public-figures';
      if (String(source.view || '').startsWith('group:')) return source.view;
      return '';
    },
    reloadPhotos: ({ signal } = {}) => {
      if (signal && signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
      return global.loadPhotos({ signal }).then(ok => {
        if (!ok && !(signal && signal.aborted)) throw new Error('照片查询加载失败');
        return ok;
      });
    },
    getPeople: async ({ signal, q } = {}) => {
      if (app.state.view === 'public-figures') {
        const page = await app.api('/api/public-figures/people?limit=40' + (q ? '&q=' + encodeURIComponent(q) : ''), { signal });
        return (page.items || []).map(person => {
          const id = String(person.id);
          const label = person.label || id;
          app.state.personLabels = app.state.personLabels || {};
          app.state.personLabels[id] = label;
          return { id, label, photoCount: Number(person.photo_count || 0) };
        });
      }
      const ids = String(app.state.person || '').split(',').filter(Boolean).filter(id => !id.startsWith('pf:')).join(',');
      const query = q ? '&q=' + encodeURIComponent(q) : '';
      const [page, publics] = await Promise.all([
        app.api(app.peopleQuery({ offset: 0, limit: 40, named: 1, q: q || '', ids }), { signal }),
        app.api('/api/public-figures/people?limit=40' + query, { signal }).catch(() => ({ items: [] }))
      ]);
      const remember = (id, label) => {
        app.state.personLabels = app.state.personLabels || {};
        app.state.personLabels[id] = label;
      };
      const privatePeople = (page.items || []).map(person => {
        const id = String(person.id);
        const label = app.personLabel(person);
        remember(id, label);
        return { id, label, photoCount: Number(person.photo_count || 0) };
      });
      const publicPeople = (publics.items || []).map(person => {
        const id = String(person.id);
        const label = person.label || id;
        remember(id, label);
        return { id, label, photoCount: Number(person.photo_count || 0) };
      });
      const pool = q ? privatePeople.concat(publicPeople) : privatePeople.slice(0, 24).concat(publicPeople.slice(0, 16));
      return pool.sort((a, b) => b.photoCount - a.photoCount || a.label.localeCompare(b.label, 'zh')).slice(0, 40);
    },
    getPlaces: async ({ signal, q } = {}) => {
      const scoped = app.state.view === 'public-figures';
      const path = (scoped ? '/api/public-figures/places?limit=40' : '/api/places?limit=40') + (q ? '&q=' + encodeURIComponent(q) : '');
      const page = await app.api(path, { signal });
      return (page.places || []).map(item => ({
        place: item.place,
        label: app.prettyPlace ? app.prettyPlace(item.place) : item.place,
        count: Number(item.count || 0)
      }));
    },
    getTimeline: async ({ signal } = {}) => app.api('/api/timeline', { signal }),
    applyGroup: async (group, { signal } = {}) => {
      if (signal && signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (app.state.view === 'public-figures') {
        app.state.publicGroup = group || '';
        const ok = await global.loadPhotos({ signal });
        if (!ok && !(signal && signal.aborted)) throw new Error('photo query failed');
        return ok;
      }
      await app.setView(group ? 'group:' + group : 'timeline');
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
      const key = String(id);
      const cached = app.state.personLabels && app.state.personLabels[key];
      if (cached) return cached;
      const person = (app.state.people || []).find(item => String(item.id) === key)
        || (app.state.personDetail && String(app.state.personDetail.id) === key ? app.state.personDetail : null);
      return person ? app.personLabel(person) : '';
    },
    selectionKind: source => source.view === 'excluded' ? 'restore' : 'exclude',
    onStateChange: sync
  });
  global.__ourTimeHomeAdapter = adapter;

  try {
    homeUI = global.OurTimeHomeUI.mount({ host, adapter });
  } catch (error) {
    console.error('[homepage-phase1] mount failed', error);
    return;
  }
  global.__ourTimeHomeUI = homeUI;
  global.__ourTimeRememberPersonLabel = (id, label) => {
    if (homeUI && typeof homeUI.rememberPersonLabel === 'function') homeUI.rememberPersonLabel(id, label);
    if (homeUI) homeUI.sync();
  };
  syncLegacyControls();
  document.addEventListener('click', () => global.setTimeout(sync, 0), { passive: true });
})(window);
