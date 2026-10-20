const test = require('node:test');
const assert = require('node:assert/strict');

const { createStore } = require('../src/renderer/schedule-store.js');

test('scheduled tasks are local data without executable payloads', () => {
  const store = createStore(() => '2026-08-08T00:00:00.000Z', () => 'task-1');

  assert.deepEqual(store.create('  检查项目  '), {
    id: 'task-1',
    title: '检查项目',
    status: 'open',
    createdAt: '2026-08-08T00:00:00.000Z',
    updatedAt: '2026-08-08T00:00:00.000Z',
  });
});

test('scheduled tasks reject blank or oversized titles', () => {
  const store = createStore(() => '2026-08-08T00:00:00.000Z', () => 'task-1');

  assert.throws(() => store.create('   '), {
    name: 'RangeError',
  });
  assert.throws(() => store.create('x'.repeat(121)), {
    name: 'RangeError',
  });
});

test('scheduled tasks reject blank or oversized rename titles', () => {
  const store = createStore(() => '2026-08-08T00:00:00.000Z', () => 'task-1');
  store.create('检查项目');

  assert.throws(() => store.rename('task-1', '   '), {
    name: 'RangeError',
  });
  assert.throws(() => store.rename('task-1', 'x'.repeat(121)), {
    name: 'RangeError',
  });
});

test('scheduled tasks support rename, complete, cancel, and reopen transitions', () => {
  const stamps = [
    '2026-08-08T00:00:00.000Z',
    '2026-08-08T00:01:00.000Z',
    '2026-08-08T00:02:00.000Z',
    '2026-08-08T00:03:00.000Z',
    '2026-08-08T00:04:00.000Z',
  ];
  const store = createStore(() => stamps.shift(), () => 'task-1');

  assert.equal(store.create('检查项目').status, 'open');
  assert.deepEqual(store.rename('task-1', '  复查项目  '), {
    id: 'task-1',
    title: '复查项目',
    status: 'open',
    createdAt: '2026-08-08T00:00:00.000Z',
    updatedAt: '2026-08-08T00:01:00.000Z',
  });
  assert.equal(store.complete('task-1').status, 'completed');
  assert.equal(store.reopen('task-1').status, 'open');
  assert.equal(store.cancel('task-1').status, 'cancelled');
  assert.equal(store.reopen('task-1').status, 'open');
});

test('scheduled tasks reject unknown ids', () => {
  const store = createStore(() => '2026-08-08T00:00:00.000Z', () => 'task-1');

  assert.throws(() => store.rename('missing', '任务'), {
    name: 'RangeError',
  });
  assert.throws(() => store.complete('missing'), {
    name: 'RangeError',
  });
  assert.throws(() => store.cancel('missing'), {
    name: 'RangeError',
  });
  assert.throws(() => store.reopen('missing'), {
    name: 'RangeError',
  });
  assert.throws(() => store.remove('missing'), {
    name: 'RangeError',
  });
});

test('scheduled tasks can be removed from the local-only store', () => {
  const store = createStore(() => '2026-08-08T00:00:00.000Z', () => 'task-1');
  store.create('检查项目');

  assert.deepEqual(store.remove('task-1'), {
    id: 'task-1',
    title: '检查项目',
    status: 'open',
    createdAt: '2026-08-08T00:00:00.000Z',
    updatedAt: '2026-08-08T00:00:00.000Z',
  });
  assert.deepEqual(store.snapshot(), []);
});

test('scheduled task snapshots are immutable plain data copies', () => {
  const store = createStore(() => '2026-08-08T00:00:00.000Z', () => 'task-1');
  store.create('检查项目');

  const firstSnapshot = store.snapshot();
  assert.equal(Object.isFrozen(firstSnapshot[0]), true);
  assert.deepEqual(Object.getOwnPropertyDescriptors(firstSnapshot[0]), {
    id: {
      value: 'task-1',
      writable: false,
      enumerable: true,
      configurable: false,
    },
    title: {
      value: '检查项目',
      writable: false,
      enumerable: true,
      configurable: false,
    },
    status: {
      value: 'open',
      writable: false,
      enumerable: true,
      configurable: false,
    },
    createdAt: {
      value: '2026-08-08T00:00:00.000Z',
      writable: false,
      enumerable: true,
      configurable: false,
    },
    updatedAt: {
      value: '2026-08-08T00:00:00.000Z',
      writable: false,
      enumerable: true,
      configurable: false,
    },
  });

  assert.equal(Reflect.set(firstSnapshot[0], 'title', 'Changed'), false);

  const secondSnapshot = store.snapshot();
  assert.equal(Reflect.set(firstSnapshot[0], 'title', 'ignored'), false);
  assert.equal(secondSnapshot[0].title, '检查项目');
  assert.notEqual(firstSnapshot[0], secondSnapshot[0]);
});
