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
    function enqueue(kind, id, write) {
      const key = String(kind) + ':' + String(id);
      const previous = queues.get(key) || Promise.resolve();
      const caller = previous.then(write);
      const tail = caller.catch(() => {});
      queues.set(key, tail);
      tail.then(() => {
        if (queues.get(key) === tail) queues.delete(key);
      });
      return caller;
    }
    enqueue.pendingCount = () => queues.size;
    return enqueue;
  }

  function operationReceiptError(receipt, code, message) {
    const error = new Error(message);
    error.errorCode = code;
    error.operationId = receipt && receipt.operation_id;
    error.receipt = receipt;
    return error;
  }

  function interpretOperationReceipt(receipt) {
    if (!receipt || typeof receipt !== 'object') {
      throw operationReceiptError(receipt, 'operation_status_invalid', '操作回执无效');
    }
    if (receipt.state_committed === true) {
      const cleanupIncomplete = receipt.cleanup_status === 'pending' || receipt.cleanup_status === 'failed';
      return {
        ...receipt,
        cleanupIncomplete,
        ...(cleanupIncomplete
          ? {message: receipt.error_detail || '状态已保存，但缓存清理尚未完成'}
          : {})
      };
    }
    if (receipt.status === 'pending') {
      throw operationReceiptError(
        receipt, 'operation_pending',
        '操作仍在处理中，结果待确认（操作编号：' + (receipt.operation_id || '未知') + '）'
      );
    }
    throw operationReceiptError(
      receipt,
      receipt.error_code || 'operation_failed',
      receipt.error_detail || '操作未完成'
    );
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
    createWriteQueue, createQuerySession, interpretOperationReceipt
  });
})(window);
