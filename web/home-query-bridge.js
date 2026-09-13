/* Bridge for OurTime's existing flat state (q/person/directory/sort/selected).
 * Existing loader, folder picker and batch actions are supplied by the host.
 * This file neither locates DOM by guessed IDs nor issues HTTP requests.
 */
(function (global) {
  'use strict';
  const value = input => input == null ? '' : String(input);
  const fields = ['q', 'person', 'directory', 'place', 'dateFrom', 'dateTo'];
  const allowedSort = new Set(['date_desc', 'date_asc', 'name_asc', 'name_desc', 'recognized_desc']);
  const same = (a, b) => fields.every(key => value(a[key]) === value(b[key]));

  function create(bindings) {
    if (!bindings || !bindings.source || typeof bindings.source !== 'object') {
      throw new TypeError('Bridge requires the existing state as source.');
    }
    for (const key of ['isActive', 'scopeKey', 'reloadPhotos', 'getPeople', 'setSelecting',
      'selectVisible', 'clearSelection', 'editSelected', 'excludeSelected']) {
      if (typeof bindings[key] !== 'function') throw new TypeError('Bridge binding is missing: ' + key);
    }
    const source = bindings.source;
    const inputs = bindings.legacyInputs || {};
    let working = 0;
    const query = () => Object.fromEntries(fields.map(key => [key, value(source[key])]));
    const scope = () => value(bindings.scopeKey(source));
    const querySession = global.OurTimeOperationRuntime.createQuerySession({
      query: { ...query(), sort: value(source.sort), scope: scope() }, result: null
    });
    function writeInputs() {
      for (const key of [...fields, 'sort']) {
        if (inputs[key] && 'value' in inputs[key]) inputs[key].value = value(source[key]);
      }
      // No input/change event dispatch: one user operation -> one existing loader call.
    }
    function notify() {
      writeInputs();
      if (typeof bindings.onStateChange === 'function') bindings.onStateChange();
    }
    function guard(context) {
      if (context && context.signal && context.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (!bindings.isActive(source)) throw new Error('当前页面已离开照片查询范围。');
      if (context && context.expectedScopeKey != null && value(context.expectedScopeKey) !== scope()) {
        throw new Error('当前查询范围已变化，请重新操作。');
      }
      if (context && context.expectedQuery && !same(context.expectedQuery, query())) {
        throw new Error('查询条件已被其他操作更新，请重新操作。');
      }
    }
    async function update(next, context, reason) {
      guard(context);
      if (source.selecting) throw new Error('请先退出批量选择，再修改查询。');
      const token = querySession.begin({ ...query(), ...next, sort: value(next.sort ?? source.sort), scope: scope() });
      working += 1;
      Object.assign(source, next);
      try {
        notify();
        // Must bind to the existing reset/reload path, NOT append/next-page loading.
        const result=await bindings.reloadPhotos({ reason, signal: context && context.signal });
        querySession.commit(token,result);
      } catch (error) {
        if (querySession.fail(token)) {
          const actual=querySession.read().actualQuery;
          for (const key of fields) source[key]=value(actual[key]);
          source.sort=value(actual.sort||'date_desc');
          notify();
        }
        throw error;
      } finally {
        working = Math.max(0,working-1);
        notify();
      }
    }
    function delegate(key, extraGuard) {
      return async context => {
        guard(context);
        if (extraGuard) extraGuard();
        try { return await bindings[key](context); }
        finally { notify(); }
      };
    }
    const selectedCount = () => source.selected && Number.isFinite(source.selected.size) ? source.selected.size : 0;
    const requireSelection = () => {
      if (!source.selecting || !selectedCount()) throw new Error('请先选择照片。');
    };
    const adapter = {
      readState() {
        const rawQuery = query();
        return {
          active: Boolean(bindings.isActive(source)), scopeKey: scope(), query: rawQuery,
          sort: value(source.sort || 'date_desc'), selecting: Boolean(source.selecting),
          selectedCount: selectedCount(),
          personLabel: typeof bindings.personLabel === 'function' ? value(bindings.personLabel(source.person)) : '',
          selectionKind: typeof bindings.selectionKind === 'function' ? bindings.selectionKind(source) : 'exclude',
          refreshAvailable: typeof bindings.refreshAvailable === 'function' && Boolean(bindings.refreshAvailable(source))
        };
      },
      applyQuery(next, context) {
        const cleaned = {
          q: value(next.q),
          person: value(next.person).trim(),
          directory: value(next.directory).trim(),
          place: value(next.place).trim(),
          dateFrom: value(next.dateFrom).trim(),
          dateTo: value(next.dateTo).trim()
        };
        return update(cleaned, context, 'query');
      },
      applySort(sort, context) {
        if (!allowedSort.has(sort)) return Promise.reject(new Error('不支持的照片排序。'));
        if (sort === 'recognized_desc' && !scope().startsWith('group:')) return Promise.reject(new Error('这个排序只用于合影。'));
        return update({ sort }, context, 'sort');
      },
      getPeople: context => bindings.getPeople(context),
      getPlaces: context => typeof bindings.getPlaces === 'function' ? bindings.getPlaces(context) : Promise.resolve([]),
      getTimeline: context => typeof bindings.getTimeline === 'function' ? bindings.getTimeline(context) : Promise.resolve({years:[], months:[]}),
      async setSelecting(on, context) {
        guard(context);
        try { return await bindings.setSelecting(Boolean(on), context); }
        finally { notify(); }
      },
      selectVisible: delegate('selectVisible', () => { if (!source.selecting) throw new Error('请先进入选择模式。'); }),
      clearSelection: delegate('clearSelection'),
      editSelected: delegate('editSelected', requireSelection),
      excludeSelected: delegate('excludeSelected', requireSelection)
    };
    if (typeof bindings.restoreSelected === 'function') adapter.restoreSelected = delegate('restoreSelected', requireSelection);
    if (typeof bindings.chooseFolder === 'function') adapter.chooseFolder = context => bindings.chooseFolder(context);
    if (typeof bindings.applyGroup === 'function') adapter.applyGroup = (group, context) => {
      guard(context);
      return bindings.applyGroup(value(group), context);
    };
    if (typeof bindings.refreshPhotos === 'function') adapter.refresh = delegate('refreshPhotos');
    return Object.freeze(adapter);
  }
  if (global.OurTimeHomeBridge) throw new Error('OurTimeHomeBridge was loaded twice.');
  global.OurTimeHomeBridge = Object.freeze({ version: '1.0.0', create });
})(window);
