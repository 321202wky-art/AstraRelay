(function exposeProjectTabs(scope) {
  function rootNameForStatus(status) {
    return status && status.authorized && typeof status.rootName === 'string'
      ? status.rootName.trim()
      : '';
  }

  function currentRootGeneration(project) {
    return Number.isSafeInteger(project && project.rootGeneration) ? project.rootGeneration : 0;
  }

  function captureProjectRootLifecycle(project) {
    const status = project && project.status ? project.status : { authorized: false };
    return {
      generation: currentRootGeneration(project),
      authorized: Boolean(status.authorized),
      rootName: rootNameForStatus(status),
    };
  }

  function isProjectRootLifecycleCurrent(project, token) {
    if (!project || !token) return false;
    const current = captureProjectRootLifecycle(project);
    return current.generation === token.generation
      && current.authorized === token.authorized
      && current.rootName === token.rootName;
  }

  function rootStatusMatchesProject(project, status) {
    const current = captureProjectRootLifecycle(project);
    return current.authorized === Boolean(status && status.authorized)
      && current.rootName === rootNameForStatus(status);
  }

  function applyProjectLifecycleResult(project, token, apply) {
    if (!isProjectRootLifecycleCurrent(project, token) || typeof apply !== 'function') return false;
    apply();
    return true;
  }

  function replaceProjectRoot(project, status) {
    if (!project) return null;
    project.rootGeneration = currentRootGeneration(project) + 1;
    resetProjectTabs(project);
    project.status = status || { authorized: false };
    project.tree = [];
    project.query = '';
    project.results = [];
    return captureProjectRootLifecycle(project);
  }

  function activeProjectTab(project) {
    if (!project || !Array.isArray(project.tabs)) return null;
    return project.tabs.find((tab) => tab && tab.path === project.activeTabId) || null;
  }

  function canSaveActiveProjectTab(project) {
    const tab = activeProjectTab(project);
    return Boolean(tab && tab.dirty && tab.rootGeneration === currentRootGeneration(project)
      && project && project.status && project.status.authorized);
  }

  function hasDirtyProjectTabs(project) {
    return Boolean(project && Array.isArray(project.tabs) && project.tabs.some((tab) => (
      tab && tab.dirty && tab.rootGeneration === currentRootGeneration(project)
    )));
  }

  function projectInspectorState(project) {
    if (!project || !project.status || !project.status.authorized) {
      return Object.freeze({ dirtyFileCount: 0, activeTabPath: '', activeTabDirty: false });
    }
    const generation = currentRootGeneration(project);
    const tabs = Array.isArray(project.tabs)
      ? project.tabs.filter((tab) => tab && typeof tab.path === 'string' && tab.rootGeneration === generation)
      : [];
    const active = tabs.find((tab) => tab.path === project.activeTabId) || null;
    return Object.freeze({
      dirtyFileCount: tabs.filter((tab) => tab.dirty).length,
      activeTabPath: active ? active.path : '',
      activeTabDirty: Boolean(active && active.dirty),
    });
  }

  async function saveActiveProjectTab(project, writeText, lifecycleToken = captureProjectRootLifecycle(project)) {
    const tab = activeProjectTab(project);
    if (!canSaveActiveProjectTab(project) || !isProjectRootLifecycleCurrent(project, lifecycleToken)
      || typeof writeText !== 'function') return false;
    const content = tab.editor && typeof tab.editor.getValue === 'function'
      ? tab.editor.getValue()
      : tab.content;
    await writeText(tab.path, content);
    if (!isProjectRootLifecycleCurrent(project, lifecycleToken) || activeProjectTab(project) !== tab
      || tab.rootGeneration !== currentRootGeneration(project)) return false;
    tab.originalContent = content;
    tab.content = content;
    tab.dirty = false;
    return true;
  }

  function resetProjectTabs(project) {
    if (!project) return;
    const tabs = Array.isArray(project.tabs) ? project.tabs : [];
    for (const tab of tabs) {
      if (!tab || !tab.editor || typeof tab.editor.destroy !== 'function') continue;
      try {
        tab.editor.destroy();
      } catch {
        // A failed editor teardown must not keep stale tabs alive across project roots.
      }
    }
    project.tabs = [];
    project.activeTabId = null;
  }

  const api = Object.freeze({
    activeProjectTab,
    applyProjectLifecycleResult,
    canSaveActiveProjectTab,
    captureProjectRootLifecycle,
    hasDirtyProjectTabs,
    isProjectRootLifecycleCurrent,
    projectInspectorState,
    replaceProjectRoot,
    resetProjectTabs,
    rootStatusMatchesProject,
    saveActiveProjectTab,
  });

  if (scope) scope.AstraRelayProjectTabs = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
