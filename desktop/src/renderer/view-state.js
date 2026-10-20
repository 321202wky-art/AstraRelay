(function initViewState(globalScope) {
  'use strict';

  const VIEWS = new Set(['workspace', 'search', 'model-plaza', 'schedule', 'project']);

  function createViewState(initial = 'workspace') {
    let current = null;

    function select(next) {
      if (!VIEWS.has(next)) {
        throw new RangeError('未知工作视图');
      }
      current = next;
      return current;
    }

    select(initial);

    return Object.freeze({
      active: () => current,
      select,
      isActive: (view) => current === view,
    });
  }

  const api = Object.freeze({ createViewState });

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  globalScope.AstraRelayViews = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
