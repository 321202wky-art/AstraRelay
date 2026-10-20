(function initScheduleStore(globalScope) {
  'use strict';

  function normalizeTitle(value) {
    const title = String(value ?? '').trim();
    if (title.length < 1 || title.length > 120) {
      throw new RangeError('任务标题长度必须在 1 到 120 个字符之间');
    }
    return title;
  }

  function normalizeId(value) {
    const id = String(value ?? '').trim();
    if (!id) {
      throw new RangeError('任务标识不存在');
    }
    return id;
  }

  function defaultNow() {
    return new Date().toISOString();
  }

  function defaultRandom() {
    if (globalScope.crypto && typeof globalScope.crypto.randomUUID === 'function') {
      return globalScope.crypto.randomUUID();
    }
    return `task-${Math.random().toString(16).slice(2, 10)}`;
  }

  function freezeRecord(task) {
    return Object.freeze({ ...task });
  }

  function createStore(now = defaultNow, random = defaultRandom) {
    let tasks = [];

    function snapshot() {
      return Object.freeze(tasks.map((task) => freezeRecord(task)));
    }

    function create(title) {
      const stamp = now();
      const task = freezeRecord({
        id: random(),
        title: normalizeTitle(title),
        status: 'open',
        createdAt: stamp,
        updatedAt: stamp,
      });
      tasks = [...tasks, task];
      return { ...task };
    }

    function updateTask(id, updater) {
      const taskId = normalizeId(id);
      const index = tasks.findIndex((task) => task.id === taskId);
      if (index < 0) {
        throw new RangeError(`任务不存在: ${taskId}`);
      }
      const current = tasks[index];
      const next = freezeRecord(updater(current));
      tasks = tasks.map((task, taskIndex) => (taskIndex === index ? next : task));
      return { ...next };
    }

    function rename(id, title) {
      const nextTitle = normalizeTitle(title);
      return updateTask(id, (task) => ({
        ...task,
        title: nextTitle,
        updatedAt: now(),
      }));
    }

    function complete(id) {
      return updateTask(id, (task) => ({
        ...task,
        status: 'completed',
        updatedAt: now(),
      }));
    }

    function cancel(id) {
      return updateTask(id, (task) => ({
        ...task,
        status: 'cancelled',
        updatedAt: now(),
      }));
    }

    function reopen(id) {
      return updateTask(id, (task) => ({
        ...task,
        status: 'open',
        updatedAt: now(),
      }));
    }

    function remove(id) {
      const taskId = normalizeId(id);
      const index = tasks.findIndex((task) => task.id === taskId);
      if (index < 0) {
        throw new RangeError(`任务不存在: ${taskId}`);
      }
      const removed = tasks[index];
      tasks = tasks.filter((task) => task.id !== taskId);
      return { ...removed };
    }

    return Object.freeze({
      snapshot,
      create,
      rename,
      complete,
      cancel,
      reopen,
      remove,
    });
  }

  const api = Object.freeze({ createStore });

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  globalScope.AstraRelaySchedule = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
