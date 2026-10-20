const test = require('node:test');
const assert = require('node:assert/strict');

const { createViewState } = require('../src/renderer/view-state.js');

test('view state keeps exactly one known active view', () => {
  const views = createViewState('workspace');
  assert.equal(views.active(), 'workspace');
  assert.equal(views.select('project'), 'project');
  assert.equal(views.active(), 'project');
  assert.equal(views.isActive('workspace'), false);
  assert.equal(views.isActive('project'), true);
  assert.throws(() => views.select('terminal'), /未知工作视图/);
});

test('view state rejects an unknown initial view', () => {
  assert.throws(() => createViewState('terminal'), /未知工作视图/);
});
