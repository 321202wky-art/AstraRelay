const test = require('node:test');
const assert = require('node:assert/strict');

const {
  applyProjectLifecycleResult,
  canSaveActiveProjectTab,
  captureProjectRootLifecycle,
  projectInspectorState,
  replaceProjectRoot,
  resetProjectTabs,
  saveActiveProjectTab,
} = require('../src/renderer/project-tabs.js');

test('project inspector state reports only authorized current-root editor facts', () => {
  assert.deepEqual(projectInspectorState({
    rootGeneration: 3,
    status: { authorized: false, rootName: 'private-root' },
    tabs: [{ path: 'private.txt', dirty: true, rootGeneration: 3 }],
    activeTabId: 'private.txt',
  }), {
    dirtyFileCount: 0,
    activeTabPath: '',
    activeTabDirty: false,
  });

  assert.deepEqual(projectInspectorState({
    rootGeneration: 3,
    status: { authorized: true, rootName: 'project-root' },
    tabs: [
      { path: 'src/dirty.js', dirty: true, rootGeneration: 3 },
      { path: 'README.md', dirty: false, rootGeneration: 3 },
      { path: 'stale.txt', dirty: true, rootGeneration: 2 },
    ],
    activeTabId: 'src/dirty.js',
  }), {
    dirtyFileCount: 1,
    activeTabPath: 'src/dirty.js',
    activeTabDirty: true,
  });
});

test('resetProjectTabs destroys mounted editors and clears the active project tab', () => {
  const destroyed = [];
  const project = {
    tabs: [
      { path: 'src/old.js', editor: { destroy: () => destroyed.push('src/old.js') } },
      { path: 'README.md', editor: { destroy: () => destroyed.push('README.md') } },
    ],
    activeTabId: 'src/old.js',
  };

  resetProjectTabs(project);

  assert.deepEqual(destroyed, ['src/old.js', 'README.md']);
  assert.deepEqual(project.tabs, []);
  assert.equal(project.activeTabId, null);
});

test('an active dirty tab cannot write after its project tabs are reset', async () => {
  const project = {
    rootGeneration: 1,
    status: { authorized: true, rootName: 'old-root' },
    tabs: [{ path: 'src/old.js', content: 'changed', dirty: true, rootGeneration: 1 }],
    activeTabId: 'src/old.js',
  };
  const writes = [];

  assert.equal(canSaveActiveProjectTab(project), true);
  assert.equal(await saveActiveProjectTab(project, async (path, content) => {
    writes.push({ path, content });
  }), true);
  assert.deepEqual(writes, [{ path: 'src/old.js', content: 'changed' }]);
  assert.equal(project.tabs[0].dirty, false);

  project.tabs[0].content = 'later change';
  project.tabs[0].dirty = true;
  resetProjectTabs(project);

  assert.equal(canSaveActiveProjectTab(project), false);
  assert.equal(await saveActiveProjectTab(project, async (path, content) => {
    writes.push({ path, content });
  }), false);
  assert.deepEqual(writes, [{ path: 'src/old.js', content: 'changed' }]);
});

test('a preview resolving after a root reset cannot add a tab, mount an editor, or write', async () => {
  const project = {
    rootGeneration: 1,
    status: { authorized: true, rootName: 'old-root' },
    tree: [],
    query: '',
    results: [],
    tabs: [],
    activeTabId: null,
  };
  const previewToken = captureProjectRootLifecycle(project);
  let resolvePreview;
  let mounts = 0;
  const pendingPreview = new Promise((resolve) => { resolvePreview = resolve; });
  const applied = pendingPreview.then((preview) => applyProjectLifecycleResult(project, previewToken, () => {
    project.tabs = [{ ...preview, dirty: true, rootGeneration: previewToken.generation }];
    project.activeTabId = preview.path;
    mounts += 1;
  }));

  replaceProjectRoot(project, { authorized: true, rootName: 'new-root' });
  resolvePreview({ path: 'src/old.js', content: 'old content' });

  assert.equal(await applied, false);
  assert.deepEqual(project.tabs, []);
  assert.equal(project.activeTabId, null);
  assert.equal(mounts, 0);
  assert.equal(canSaveActiveProjectTab(project), false);
  assert.equal(await saveActiveProjectTab(project, async () => {
    throw new Error('stale preview must not write');
  }, previewToken), false);
});

test('a tree result resolving after a root reset cannot replace the new root tree', async () => {
  const project = {
    rootGeneration: 5,
    status: { authorized: true, rootName: 'old-root' },
    tree: [{ path: 'current.js' }],
    query: '',
    results: [],
    tabs: [],
    activeTabId: null,
  };
  const treeToken = captureProjectRootLifecycle(project);
  let resolveTree;
  const pendingTree = new Promise((resolve) => { resolveTree = resolve; });
  const applied = pendingTree.then((entries) => applyProjectLifecycleResult(project, treeToken, () => {
    project.tree = entries;
  }));

  replaceProjectRoot(project, { authorized: true, rootName: 'new-root' });
  resolveTree([{ path: 'stale.js' }]);

  assert.equal(await applied, false);
  assert.deepEqual(project.tree, []);
});
