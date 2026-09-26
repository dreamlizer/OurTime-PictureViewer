/* Viewer display controller: one owner for "what is on screen".
 * Session/request/commit tickets; only commitDisplay adopts a new photo.
 * Shell entries (open/seed/close) never paint business identity from candidates.
 */
(function (global) {
  'use strict';

  function createViewerDisplay() {
    let sessionEpoch = 0;
    let requestSeq = 0;
    let commitSeq = 0;
    let lastRequestSeq = 0;
    let lifecycle = 'closed';
    let content = 'empty';
    let committed = null;
    let pending = null;
    let visualRevision = 0;
    let disabledReasons = new Set();
    const listeners = new Set();

    function emit(type, detail) {
      const event = { type, detail, at: Date.now() };
      for (const fn of [...listeners]) {
        try { fn(event); } catch (_) { /* keep display path alive */ }
      }
    }

    function nextRequestToken(targetKey) {
      requestSeq += 1;
      lastRequestSeq = requestSeq;
      return Object.freeze({
        sessionEpoch,
        requestSeq,
        targetKey: String(targetKey || ''),
      });
    }

    function isLiveRequest(token) {
      return Boolean(token)
        && token.sessionEpoch === sessionEpoch
        && pending
        && pending.token
        && pending.token.requestSeq === token.requestSeq
        && lifecycle === 'open';
    }

    function currentLease() {
      if (!committed || lifecycle !== 'open') return null;
      return Object.freeze({
        sessionEpoch: committed.sessionEpoch,
        commitSeq: committed.commitSeq,
        photoKey: committed.photoKey,
      });
    }

    function isLiveLease(lease) {
      if (!lease || !committed || lifecycle !== 'open') return false;
      return lease.sessionEpoch === committed.sessionEpoch
        && lease.commitSeq === committed.commitSeq
        && lease.photoKey === committed.photoKey;
    }

    function getCommittedFrame() {
      if (lifecycle !== 'open' || !committed) return null;
      return committed;
    }

    function getPending() {
      return pending;
    }

    function beginOpen() {
      const previous = lifecycle;
      sessionEpoch += 1;
      requestSeq = 0;
      commitSeq = 0;
      lastRequestSeq = 0;
      pending = null;
      committed = null;
      content = 'empty';
      lifecycle = 'open';
      disabledReasons.clear();
      emit('open', { previous, sessionEpoch });
      return sessionEpoch;
    }

    function beginClose(reason) {
      if (lifecycle === 'closed') {
        return { ok: true, alreadyClosed: true };
      }
      const exit = committed
        ? {
          photoKey: committed.photoKey,
          photoId: committed.photoId,
          commitSeq: committed.commitSeq,
          sourceKey: committed.sourceKey,
          absolute: committed.absolute,
        }
        : null;
      lifecycle = 'closing';
      pending = null;
      // Invalidate every display lease immediately; DOM may still fade.
      sessionEpoch += 1;
      disabledReasons.add('closing');
      emit('close-begin', { reason: reason || '', exit });
      return { ok: true, exit };
    }

    function finishClose() {
      committed = null;
      pending = null;
      content = 'empty';
      lifecycle = 'closed';
      disabledReasons.clear();
      emit('close-finish', {});
    }

    function markSeeded(requestToken) {
      if (!isLiveRequest(requestToken)) return { ok: false, status: 'stale' };
      content = 'seed';
      emit('seed', { targetKey: requestToken.targetKey });
      return { ok: true, status: 'seed' };
    }

    function beginPending(requestToken, target) {
      if (lifecycle !== 'open') return { ok: false, status: 'cancelled' };
      requestSeq = Math.max(requestSeq, requestToken.requestSeq);
      pending = {
        token: requestToken,
        target: target || requestToken.targetKey,
        startedAt: Date.now(),
      };
      disabledReasons.add('photo-loading');
      emit('pending', { targetKey: requestToken.targetKey });
      return { ok: true, status: 'pending' };
    }

    function clearPending(requestToken, status) {
      if (pending && requestToken && pending.token.requestSeq !== requestToken.requestSeq) {
        return { ok: false, status: 'stale' };
      }
      if (!pending && status !== 'failed') {
        disabledReasons.delete('photo-loading');
        return { ok: true, status: status || 'idle' };
      }
      if (status === 'failed' || status === 'cancelled' || status === 'stale') {
        if (pending && requestToken && pending.token.requestSeq === requestToken.requestSeq) {
          pending = null;
        }
        disabledReasons.delete('photo-loading');
        emit('pending-clear', { status });
        return { ok: true, status };
      }
      return { ok: true, status: status || 'ready' };
    }

    function bumpVisualRevision() {
      visualRevision += 1;
      return visualRevision;
    }

    function getVisualRevision() {
      return visualRevision;
    }

    /**
     * Adopt a new photo. `paint` runs only when tickets match and must be sync.
     * It receives the candidate frame data; it must not re-fetch photo detail.
     */
    function commitDisplay(candidate, ticket, options) {
      const opts = options || {};
      if (lifecycle !== 'open') {
        return { ok: false, status: 'cancelled', adopted: false };
      }
      if (!ticket || ticket.sessionEpoch !== sessionEpoch) {
        emit('stale-commit', { reason: 'session' });
        return { ok: false, status: 'stale', adopted: false };
      }
      if (ticket.requestSeq != null && ticket.requestSeq !== lastRequestSeq) {
        emit('stale-commit', { reason: 'request-seq' });
        return { ok: false, status: 'stale', adopted: false };
      }
      if (pending && ticket.requestSeq != null && pending.token.requestSeq !== ticket.requestSeq) {
        emit('stale-commit', { reason: 'pending-mismatch' });
        return { ok: false, status: 'stale', adopted: false };
      }
      if (!candidate || !candidate.photoKey) {
        return { ok: false, status: 'failed', adopted: false };
      }
      commitSeq += 1;
      const frame = {
        photoKey: candidate.photoKey,
        photoId: Number(candidate.photoId) || 0,
        sourceKey: candidate.sourceKey || '',
        commitSeq,
        sessionEpoch,
        detail: candidate.detail || null,
        file: candidate.file || null,
        src: candidate.src || '',
        visualRevision: candidate.visualRevision || visualRevision,
        absolute: Number.isFinite(candidate.absolute) ? candidate.absolute : null,
        capabilities: candidate.capabilities || {},
        paintedAt: 0,
      };
      try {
        if (typeof opts.paint === 'function') opts.paint(frame, candidate);
      } catch (error) {
        emit('commit-error', { message: String(error && error.message || error) });
        commitSeq -= 1;
        return { ok: false, status: 'failed', adopted: false, error };
      }
      frame.paintedAt = Date.now();
      committed = frame;
      content = 'committed';
      pending = null;
      disabledReasons.delete('photo-loading');
      if (opts.resyncCompanion !== false && typeof opts.syncCompanion === 'function') {
        try { opts.syncCompanion(frame); } catch (_) { /* companion only */ }
      }
      emit('commit', {
        photoKey: frame.photoKey,
        photoId: frame.photoId,
        commitSeq: frame.commitSeq,
        sourceKey: frame.sourceKey,
      });
      return {
        ok: true,
        status: 'committed',
        adopted: true,
        frame,
        lease: currentLease(),
      };
    }

    function requestDisplayRefresh(reason, lease, dirty) {
      if (!isLiveLease(lease)) {
        emit('stale-refresh', { reason: String(reason || '') });
        return { ok: false, status: 'stale' };
      }
      const flags = Array.isArray(dirty) ? dirty.slice() : [dirty || reason || 'content'];
      emit('refresh', { reason: String(reason || ''), flags, photoKey: lease.photoKey });
      return { ok: true, status: 'queued', flags };
    }

    function setDisabledReason(reason, on) {
      if (on) disabledReasons.add(String(reason));
      else disabledReasons.delete(String(reason));
    }

    function isActionBlocked(reason) {
      if (lifecycle !== 'open') return 'closed';
      if (disabledReasons.has('closing')) return 'closing';
      if (reason && disabledReasons.has(reason)) return reason;
      if (!committed) return 'uncommitted';
      return null;
    }

    function subscribe(fn) {
      if (typeof fn !== 'function') return function () {};
      listeners.add(fn);
      return function () { listeners.delete(fn); };
    }

    function debugState() {
      return {
        lifecycle,
        content,
        sessionEpoch,
        requestSeq,
        commitSeq,
        visualRevision,
        photoId: committed ? committed.photoId : 0,
        photoKey: committed ? committed.photoKey : '',
        pendingKey: pending ? pending.target : '',
        disabled: [...disabledReasons],
      };
    }

    return {
      beginOpen,
      beginClose,
      finishClose,
      nextRequestToken,
      beginPending,
      clearPending,
      markSeeded,
      isLiveRequest,
      currentLease,
      isLiveLease,
      commitDisplay,
      requestDisplayRefresh,
      getCommittedFrame,
      getPending,
      bumpVisualRevision,
      getVisualRevision,
      setDisabledReason,
      isActionBlocked,
      subscribe,
      debugState,
    };
  }

  global.OurTimeViewerDisplay = { createViewerDisplay };
})(window);
