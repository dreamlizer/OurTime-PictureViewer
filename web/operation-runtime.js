/* Shared ownership rules for entity writes and replace-style queries. */
(function (global) {
  'use strict';

  function operationId() {
    if (global.crypto && typeof global.crypto.randomUUID === 'function') {
      return global.crypto.randomUUID();
    }
    return 'op-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }

  function createEntitySessions() {
    const generations = new Map();
    return Object.freeze({
      open(kind, id) {
        const key = String(kind);
        const generation = (generations.get(key) || 0) + 1;
        generations.set(key, generation);
        return Object.freeze({ kind: key, id: Number(id), generation });
      },
      current(token) {
        return Boolean(token) && generations.get(token.kind) === token.generation;
      },
      close(kind) {
        const key = String(kind);
        generations.set(key, (generations.get(key) || 0) + 1);
      }
    });
  }

  function captureEntityWrite(entitySession, payload, draftRevision) {
    if (!entitySession || !Number.isFinite(Number(entitySession.id))) {
      throw new TypeError('A write must capture an entity session.');
    }
    return Object.freeze({
      entitySession,
      entityId: Number(entitySession.id),
      payload: JSON.parse(JSON.stringify(payload || {})),
      draftRevision: Number(draftRevision || 0),
      operationId: operationId()
    });
  }

  function createWriteQueue() {
    const queues = new Map();
    return function enqueue(kind, id, write) {
      const key = String(kind) + ':' + String(id);
      const previous = queues.get(key) || Promise.resolve();
      const current = previous.catch(() => {}).then(write);
      queues.set(key, current.finally(() => {
        if (queues.get(key) === current) queues.delete(key);
      }));
      return current;
    };
  }

  function createQuerySession(initial) {
    let generation = 0;
    let state = {
      actualQuery: { ...((initial && initial.query) || {}) },
      pendingQuery: null,
      result: initial && Object.prototype.hasOwnProperty.call(initial, 'result') ? initial.result : null,
      generation: 0
    };
    return Object.freeze({
      begin(query) {
        const token = Object.freeze({ generation: ++generation, query: { ...(query || {}) } });
        state = { ...state, pendingQuery: token.query, generation };
        return token;
      },
      commit(token, result) {
        if (!token || token.generation !== generation) return false;
        state = {
          actualQuery: { ...token.query }, pendingQuery: null, result,
          generation
        };
        return true;
      },
      fail(token) {
        if (!token || token.generation !== generation) return false;
        state = { ...state, pendingQuery: null, generation };
        return true;
      },
      read() {
        return {
          actualQuery: { ...state.actualQuery },
          pendingQuery: state.pendingQuery && { ...state.pendingQuery },
          result: state.result,
          generation: state.generation
        };
      }
    });
  }

  global.OurTimeOperationRuntime = Object.freeze({
    version: '1.0.0', operationId, createEntitySessions, captureEntityWrite,
    createWriteQueue, createQuerySession
  });
})(window);
