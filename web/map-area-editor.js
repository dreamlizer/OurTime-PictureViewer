/* One-rectangle place editor shared by the overview and single-photo map. */
(function (global) {
  'use strict';

  const interactionNames = [
    'dragging', 'boxZoom', 'scrollWheelZoom', 'doubleClickZoom',
    'keyboard', 'touchZoom'
  ];
  const controlSelector = [
    '#places-beijing', '#places-world', '#places-basemap', '#places-zoom-out',
    '#places-zoom-in', '#places-zoom-reset', '#places-zoom',
    '#places-unknown', '#places-search', '#places-photo-back',
    '#places-photo-edit'
  ].join(',');
  let map = null;
  let container = null;
  let active = false;
  let drawing = false;
  let submitting = false;
  let pointerId = null;
  let startPoint = null;
  let startLatLng = null;
  let rectangle = null;
  let bounds = null;
  let preview = null;
  let previewController = null;
  let generation = 0;
  let interactionState = null;
  let controlState = null;
  let previousTouchAction = '';

  const element = id => document.getElementById(id);
  const buttonPhases = {
    idle: { pressed: false, label: '框选修改地点' },
    armed: { pressed: true, label: '框选中（点此退出）' },
    drawing: { pressed: true, label: '正在框选…' },
    previewing: { pressed: true, label: '正在计算选区…' },
    selected: { pressed: true, label: '框选已完成（点此退出）' },
    stale: { pressed: true, label: '选区已变化（点此退出）' }
  };

  function setButtonPhase(phase) {
    const button = element('map-area-start');
    const config = buttonPhases[phase] || buttonPhases.idle;
    button.dataset.phase = phase in buttonPhases ? phase : 'idle';
    button.setAttribute('aria-pressed', String(config.pressed));
    button.querySelector('span').textContent = config.label;
  }

  function bind(nextMap) {
    if (!nextMap || map === nextMap) return;
    map = nextMap;
    container = map.getContainer();
    container.addEventListener('pointerdown', pointerDown, true);
    container.addEventListener('pointermove', pointerMove, true);
    container.addEventListener('pointerup', pointerUp, true);
    container.addEventListener('pointercancel', pointerCancel, true);
  }

  function mapSpace() {
    return state.placeBasemap === 'gaode' ? 'gcj02' : 'wgs84';
  }

  function freezeMap() {
    interactionState = {};
    interactionNames.forEach(name => {
      const handler = map && map[name];
      interactionState[name] = Boolean(handler && handler.enabled());
      if (handler) handler.disable();
    });
    controlState = new Map();
    document.querySelectorAll(controlSelector).forEach(control => {
      controlState.set(control, Boolean(control.disabled));
      control.disabled = true;
    });
    previousTouchAction = container.style.touchAction;
    container.style.touchAction = 'none';
    container.closest('.places-map-shell')?.classList.add('map-area-mode');
  }

  function restoreMap() {
    if (interactionState && map) {
      interactionNames.forEach(name => {
        const handler = map[name];
        if (!handler) return;
        if (interactionState[name]) handler.enable();
        else handler.disable();
      });
    }
    if (controlState) {
      controlState.forEach((wasDisabled, control) => {
        control.disabled = wasDisabled;
      });
    }
    if (container) container.style.touchAction = previousTouchAction;
    container?.closest('.places-map-shell')?.classList.remove('map-area-mode');
    interactionState = null;
    controlState = null;
  }

  function removeRectangle() {
    if (rectangle && map && map.hasLayer(rectangle)) map.removeLayer(rectangle);
    rectangle = null;
  }

  function abortPreview() {
    generation += 1;
    if (previewController) previewController.abort();
    previewController = null;
  }

  function resetPreview({ keepName = true } = {}) {
    abortPreview();
    preview = null;
    element('map-area-samples').replaceChildren();
    element('map-area-summary').textContent = '';
    element('map-area-preview-note').textContent = '';
    element('map-area-action-summary').textContent = '';
    element('map-area-many').checked = false;
    element('map-area-many-wrap').hidden = true;
    element('map-area-retry').hidden = true;
    element('map-area-save').disabled = true;
    if (!keepName) {
      element('map-area-name').value = '';
      element('map-area-name').dataset.suggestedValue = '';
    }
  }

  function setBusy(busy) {
    element('map-area-editor').classList.toggle('is-loading', busy);
    element('map-area-name').disabled = busy;
    element('map-area-redraw').disabled = busy;
    element('map-area-retry').disabled = busy;
    element('map-area-cancel').disabled = busy;
    element('map-area-close').disabled = busy;
    element('map-area-many').disabled = busy;
    element('map-area-save').disabled = busy || !canSave();
  }

  function canSave() {
    if (!active || submitting || !preview || !preview.matched) return false;
    if (!element('map-area-name').value.trim()) return false;
    return !preview.requires_large_confirmation || element('map-area-many').checked;
  }

  function updateSaveState() {
    const name = element('map-area-name').value.trim();
    const count = preview ? Number(preview.matched) : 0;
    element('map-area-action-summary').textContent =
      name && count ? `将所选 ${fmt(count)} 张照片的地点统一设为“${name}”` : '';
    element('map-area-save').textContent =
      count ? `确认修改 ${fmt(count)} 张` : '确认修改';
    element('map-area-save').disabled = !canSave();
  }

  function abortDrawing(message = '') {
    drawing = false;
    if (container && pointerId !== null && container.hasPointerCapture(pointerId)) {
      container.releasePointerCapture(pointerId);
    }
    pointerId = null;
    startPoint = null;
    startLatLng = null;
    removeRectangle();
    bounds = null;
    resetPreview();
    element('map-area-editor').hidden = true;
    element('map-area-hint').hidden = false;
    element('map-area-hint').textContent =
      message || '拖动框选照片范围，Esc 取消';
    setButtonPhase('armed');
  }

  async function start() {
    if (submitting) {
      toast('地点保存仍在处理中，请等待结果', true);
      return;
    }
    const nextMap = ensurePlaceMap();
    if (!nextMap) return;
    bind(nextMap);
    if (!element('photo-place-editor').hidden) {
      await closePhotoPlaceEditor();
    }
    if (active) return;
    active = true;
    map.stop();
    map.closePopup();
    clearTimeout(state.placeMapTimer);
    freezeMap();
    resetPreview({ keepName: false });
    removeRectangle();
    bounds = null;
    element('map-area-editor').hidden = true;
    element('map-area-hint').hidden = false;
    element('map-area-hint').textContent = '拖动框选照片范围，Esc 取消';
    setButtonPhase('armed');
  }

  function cancel({ silent = false } = {}) {
    if (!active) return;
    active = false;
    drawing = false;
    pointerId = null;
    startPoint = null;
    startLatLng = null;
    abortPreview();
    removeRectangle();
    bounds = null;
    preview = null;
    element('map-area-hint').hidden = true;
    element('map-area-editor').hidden = true;
    element('map-area-samples').replaceChildren();
    setBusy(false);
    restoreMap();
    const button = element('map-area-start');
    setButtonPhase('idle');
    button.disabled = submitting;
    if (!silent) toast('已取消框选，没有修改地点');
  }

  function beforeViewChange(nextView) {
    if (!active) return;
    const staysOnMap = nextView === 'places' || String(nextView).startsWith('photo-place:');
    if (!staysOnMap) cancel({ silent: true });
  }

  function eventPoint(event) {
    const rect = container.getBoundingClientRect();
    return L.point(event.clientX - rect.left, event.clientY - rect.top);
  }

  function stopPointerEvent(event) {
    event.preventDefault();
    event.stopPropagation();
    if (typeof event.stopImmediatePropagation === 'function') {
      event.stopImmediatePropagation();
    }
  }

  function pointerDown(event) {
    if (!active || submitting || drawing || event.button !== 0 || !event.isPrimary) return;
    stopPointerEvent(event);
    drawing = true;
    pointerId = event.pointerId;
    startPoint = eventPoint(event);
    startLatLng = map.containerPointToLatLng(startPoint);
    resetPreview();
    removeRectangle();
    bounds = null;
    element('map-area-editor').hidden = true;
    element('map-area-hint').hidden = false;
    element('map-area-hint').textContent = '正在框选；松开鼠标后计算完整选集';
    setButtonPhase('drawing');
    container.setPointerCapture(pointerId);
  }

  function pointerMove(event) {
    if (!active || !drawing || event.pointerId !== pointerId) return;
    stopPointerEvent(event);
    const current = map.containerPointToLatLng(eventPoint(event));
    const latLngBounds = L.latLngBounds(startLatLng, current);
    if (!rectangle) {
      rectangle = L.rectangle(latLngBounds, {
        className: 'map-area-selection',
        color: '#275f44',
        weight: 2,
        opacity: 0.95,
        fillColor: '#5d9473',
        fillOpacity: 0.16,
        interactive: false
      }).addTo(map);
    } else {
      rectangle.setBounds(latLngBounds);
    }
  }

  function pointerUp(event) {
    if (!active || !drawing || event.pointerId !== pointerId) return;
    stopPointerEvent(event);
    const endPoint = eventPoint(event);
    drawing = false;
    if (container.hasPointerCapture(pointerId)) container.releasePointerCapture(pointerId);
    pointerId = null;
    const width = Math.abs(endPoint.x - startPoint.x);
    const height = Math.abs(endPoint.y - startPoint.y);
    if (width < 8 || height < 8 || !rectangle) {
      abortDrawing('选区太小，请重新拖动框选');
      return;
    }
    const selected = rectangle.getBounds();
    bounds = {
      west: selected.getWest(),
      south: selected.getSouth(),
      east: selected.getEast(),
      north: selected.getNorth()
    };
    startPoint = null;
    startLatLng = null;
    previewBounds();
  }

  function pointerCancel(event) {
    if (drawing && event.pointerId === pointerId) {
      stopPointerEvent(event);
      abortDrawing('框选已中断，请重新拖动');
    }
  }

  function renderSamples(samples) {
    const box = element('map-area-samples');
    box.replaceChildren();
    (samples || []).slice(0, 9).forEach(item => {
      const frame = document.createElement('div');
      frame.className = 'map-area-sample';
      const image = document.createElement('img');
      image.src = `/api/thumb/${Number(item.id)}`;
      image.alt = '';
      image.decoding = 'async';
      frame.appendChild(image);
      box.appendChild(frame);
    });
  }

  function renderPreview(data) {
    preview = data;
    const count = Number(data.matched) || 0;
    element('map-area-summary').textContent = count
      ? `框内找到 ${fmt(count)} 张照片`
      : '框内未找到有定位的照片';
    renderSamples(data.samples);
    const nameInput = element('map-area-name');
    const previousSuggestion = nameInput.dataset.suggestedValue || '';
    const suggestion = String(data.suggested_place || '').trim();
    if (!nameInput.value.trim() || nameInput.value.trim() === previousSuggestion) {
      nameInput.value = suggestion;
    }
    nameInput.dataset.suggestedValue = suggestion;
    const manual = Number(data.manual_place_count) || 0;
    const scopeNote = suggestion
      ? `根据选中照片判断，这一范围共同属于 ${suggestion}。`
      : count ? '选中照片跨越多个省市，未自动填写地点名称。' : '';
    element('map-area-preview-note').textContent = count
      ? `${scopeNote}${count > 9 ? '仅展示部分预览，保存影响全部照片。' : ''}其中 ${fmt(manual)} 张已有人工地点，本次确认会替换这些地点。`
      : '可以重新框选其他范围。';
    const many = Boolean(data.requires_large_confirmation);
    element('map-area-many-wrap').hidden = !many;
    element('map-area-many-text').textContent =
      many ? `我确认要统一修改这 ${fmt(count)} 张照片的地点` : '';
    element('map-area-many').checked = false;
    element('map-area-retry').hidden = true;
    element('map-area-editor').classList.remove('is-loading');
    setButtonPhase('selected');
    updateSaveState();
  }

  async function previewBounds() {
    if (!active || !bounds) return;
    abortPreview();
    const token = generation;
    previewController = new AbortController();
    preview = null;
    element('map-area-editor').hidden = false;
    element('map-area-editor').classList.add('is-loading');
    element('map-area-summary').textContent = '正在查找范围内照片…';
    element('map-area-preview-note').textContent = '';
    element('map-area-samples').replaceChildren();
    element('map-area-retry').hidden = true;
    element('map-area-save').disabled = true;
    element('map-area-hint').hidden = true;
    setButtonPhase('previewing');
    try {
      const data = await api('/api/places/area/preview', {
        method: 'POST',
        signal: previewController.signal,
        body: JSON.stringify({ coordinate_space: mapSpace(), bounds })
      });
      if (!active || token !== generation) return;
      renderPreview(data);
    } catch (error) {
      if (error && error.name === 'AbortError') return;
      if (!active || token !== generation) return;
      element('map-area-editor').classList.remove('is-loading');
      element('map-area-summary').textContent = '范围预览失败';
      element('map-area-preview-note').textContent = error.message || '请重试预览';
      element('map-area-retry').hidden = false;
      element('map-area-save').disabled = true;
      setButtonPhase('stale');
    } finally {
      if (token === generation) previewController = null;
    }
  }

  function beginRedraw() {
    if (!active || submitting) return;
    resetPreview();
    removeRectangle();
    bounds = null;
    element('map-area-editor').hidden = true;
    element('map-area-hint').hidden = false;
    element('map-area-hint').textContent = '拖动重新框选照片范围，Esc 取消';
    setButtonPhase('armed');
  }

  function photoInsidePreview(photo, data) {
    if (!photo || !hasPhotoCoordinates(photo) || !data) return false;
    const point = placeMapPoint(
      photo.latitude,
      photo.longitude,
      data.coordinate_space === 'gcj02' ? 'gaode' : 'osm'
    );
    const area = data.bounds;
    return point[0] >= area.south && point[0] <= area.north
      && point[1] >= area.west && point[1] <= area.east;
  }

  async function refreshAfterSuccess(data) {
    state.placeStream = null;
    if (isPhotoPlaceView() && state.photoMap && photoInsidePreview(state.photoMap.photo, data)) {
      const fresh = await api('/api/photos/' + Number(state.photoMap.id));
      if (isPhotoPlaceView() && state.photoMap) {
        state.photoMap.photo = fresh;
        syncPlaceModeChrome(fresh);
      }
    } else if (state.view === 'places') {
      await loadPlaceMap();
    }
  }

  async function submit() {
    if (!canSave()) return;
    const data = preview;
    const place = element('map-area-name').value.trim();
    const operationId = operationRuntime.operationId();
    const token = generation;
    submitting = true;
    setBusy(true);
    element('map-area-start').disabled = true;
    try {
      const result = await operationRequest(
        '/api/places/area/apply',
        {
          method: 'POST',
          body: JSON.stringify({
            coordinate_space: data.coordinate_space,
            bounds: data.bounds,
            selection_fingerprint: data.selection_fingerprint,
            place,
            confirm_many: Boolean(element('map-area-many').checked),
            operation_id: operationId
          })
        },
        operationId
      );
      const stillOpen = active && token === generation;
      if (stillOpen) cancel({ silent: true });
      toast(result.message || '地点已保存；原图 GPS 未改变');
      await refreshAfterSuccess(data);
    } catch (error) {
      if (active && token === generation && error.errorCode === 'selection_changed') {
        preview = null;
        element('map-area-summary').textContent = '所选照片发生变化';
        element('map-area-preview-note').textContent = '本次没有写入。请重新预览后再确认。';
        element('map-area-retry').hidden = false;
        setButtonPhase('stale');
      }
      throw error;
    } finally {
      submitting = false;
      element('map-area-start').disabled = false;
      if (active && token === generation) setBusy(false);
    }
  }

  element('map-area-start').addEventListener('click', action(async () => {
    if (active) cancel();
    else await start();
  }));
  ['map-area-close', 'map-area-cancel'].forEach(id => {
    element(id).addEventListener('click', () => cancel());
  });
  element('map-area-redraw').addEventListener('click', beginRedraw);
  element('map-area-retry').addEventListener('click', action(previewBounds));
  element('map-area-save').addEventListener('click', action(submit));
  element('map-area-name').addEventListener('input', updateSaveState);
  element('map-area-many').addEventListener('change', updateSaveState);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && active && !submitting) {
      event.preventDefault();
      event.stopPropagation();
      cancel();
    }
  }, true);
  global.addEventListener('blur', () => {
    if (drawing) abortDrawing('框选已中断，请重新拖动');
  });
  global.addEventListener('pagehide', () => cancel({ silent: true }));
  const placesView = element('places-view');
  new MutationObserver(() => {
    if (active && placesView.hidden) cancel({ silent: true });
  }).observe(placesView, { attributes: true, attributeFilter: ['hidden'] });
  if (state.placeMap) bind(state.placeMap);

  global.OurTimeMapAreaEditor = Object.freeze({
    bind,
    start,
    cancel,
    beforeViewChange,
    isActive: () => active,
    previewBounds: area => {
      if (area) bounds = { ...area };
      return previewBounds();
    }
  });
})(window);
