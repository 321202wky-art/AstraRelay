'use strict';

const state = {
  phase: 'loading',
  activeView: 'workspace',
  session: null,
  conversations: [],
  catalog: { providers: [] },
  activeId: null,
  messages: [],
  modelSettings: [],
  loadingConversation: false,
  creatingConversation: false,
  deletingConversationIds: new Set(),
  conversationNotice: '',
  conversationListRevision: 0,
  defaultConversationRequested: false,
  menuId: null,
  renameDialog: {
    conversationId: null,
    draft: '',
    saving: false,
    error: '',
  },
  activeStream: null,
  search: {
    query: '',
    conversations: [],
    projectFiles: [],
    projectText: [],
    pending: false,
    searched: false,
  },
  project: {
    status: { authorized: false },
    tree: [],
    query: '',
    results: [],
    tabs: [],
    activeTabId: null,
    rootGeneration: 0,
  },
};

const workspaceApi = window.astraDesktop && window.astraDesktop.workspace;
const projectApi = window.astraDesktop && window.astraDesktop.project;
const projectTabs = window.AstraRelayProjectTabs;
const conversationOperations = window.AstraRelayConversationOperations;
const scheduleStore = window.AstraRelaySchedule && typeof window.AstraRelaySchedule.createStore === 'function'
  ? window.AstraRelaySchedule.createStore()
  : null;
const viewState = window.AstraRelayViews.createViewState(state.activeView);
const {
  activeProjectTab: findActiveProjectTab,
  applyProjectLifecycleResult,
  canSaveActiveProjectTab,
  captureProjectRootLifecycle,
  hasDirtyProjectTabs,
  isProjectRootLifecycleCurrent,
  projectInspectorState,
  replaceProjectRoot,
  rootStatusMatchesProject,
  saveActiveProjectTab,
} = projectTabs;

let refreshEpoch = 0;
let searchEpoch = 0;
let conversationSelectionEpoch = 0;
let searchDebounceTimer = null;
let activeOverlay = null;
let overlayTrigger = null;

const SEARCH_DEBOUNCE_MS = 250;
const sidebarMedia = window.matchMedia('(min-width: 820px)');
const inspectorMedia = window.matchMedia('(min-width: 1100px)');
const VIEW_LABELS = Object.freeze({
  workspace: '工作台',
  search: '搜索',
  'model-plaza': '模型广场',
  schedule: '已安排',
  project: '本地项目',
});
const VIEW_DETAILS = Object.freeze({
  workspace: '云端会话与桌面启动状态',
  search: '受限检索云端任务与项目文本',
  'model-plaza': '只读公开模型目录与能力说明',
  schedule: '仅显示当前桌面的本地任务队列',
  project: '受限目录树、文件打开与显式保存',
});

const elements = {
  accountAvatar: document.getElementById('account-avatar'),
  accountName: document.getElementById('account-name'),
  accountStatus: document.getElementById('account-status'),
  conversationDelete: document.getElementById('conversation-delete'),
  conversationMenu: document.getElementById('conversation-menu'),
  conversationMenuPanel: document.getElementById('conversation-menu-panel'),
  conversationRename: document.getElementById('conversation-rename'),
  renameConversationCancel: document.getElementById('rename-conversation-cancel'),
  renameConversationClose: document.getElementById('rename-conversation-close'),
  renameConversationDialog: document.getElementById('rename-conversation-dialog'),
  renameConversationError: document.getElementById('rename-conversation-error'),
  renameConversationForm: document.getElementById('rename-conversation-form'),
  renameConversationInput: document.getElementById('rename-conversation-input'),
  renameConversationSave: document.getElementById('rename-conversation-save'),
  cloudMiniStatus: document.getElementById('cloud-mini-status'),
  connectionChip: document.getElementById('connection-chip'),
  connectionLabel: document.getElementById('connection-label'),
  conversationList: document.getElementById('conversation-list'),
  environmentAccount: document.getElementById('environment-account'),
  inspectorCloudSummary: document.getElementById('inspector-cloud-summary'),
  environmentConversations: document.getElementById('environment-conversations'),
  environmentModels: document.getElementById('environment-models'),
  globalSidebar: document.getElementById('global-sidebar'),
  inspectorClose: document.getElementById('inspector-close'),
  inspectorOpen: document.getElementById('inspector-open'),
  inspectorProjectActiveTab: document.getElementById('inspector-project-active-tab'),
  inspectorProjectChanges: document.getElementById('inspector-project-changes'),
  inspectorProjectSummary: document.getElementById('inspector-project-summary'),
  inspectorScheduleDetail: document.getElementById('inspector-schedule-detail'),
  inspectorScheduleSummary: document.getElementById('inspector-schedule-summary'),
  inspectorViewDetail: document.getElementById('inspector-view-detail'),
  inspectorViewSummary: document.getElementById('inspector-view-summary'),
  loginButton: document.getElementById('login-button'),
  liveWorkspace: document.getElementById('live-workspace'),
  modelOptions: document.getElementById('model-options'),
  modelPlazaList: document.getElementById('model-plaza-list'),
  modelPlazaSummary: document.getElementById('model-plaza-summary'),
  modelPopover: document.getElementById('model-popover'),
  modelPlazaView: document.getElementById('model-plaza-view'),
  modelPopoverClose: document.getElementById('model-popover-close'),
  modelSettings: document.getElementById('model-settings'),
  modelSummary: document.getElementById('model-summary'),
  modelSummaryOverflow: document.getElementById('model-summary-overflow'),
  modelSummaryOverflowPanel: document.getElementById('model-summary-overflow-panel'),
  newTask: document.getElementById('new-task'),
  phaseBadge: document.getElementById('phase-badge'),
  primaryAction: document.getElementById('primary-action'),
  projectEditor: document.getElementById('project-editor'),
  projectMiniStatus: document.getElementById('project-mini-status'),
  projectInspectorCopy: document.getElementById('project-inspector-copy'),
  projectOpen: document.getElementById('project-open'),
  projectOpenStatus: document.getElementById('project-open-status'),
  projectSave: document.getElementById('project-save'),
  projectSearch: document.getElementById('project-search'),
  projectStatus: document.getElementById('project-status'),
  projectTabs: document.getElementById('project-tabs'),
  projectTitle: document.getElementById('project-title'),
  projectTree: document.getElementById('project-tree'),
  projectView: document.getElementById('project-view'),
  refreshButton: document.getElementById('refresh-button'),
  retryButton: document.getElementById('retry-button'),
  scrim: document.getElementById('scrim'),
  scheduleForm: document.getElementById('schedule-form'),
  scheduleList: document.getElementById('schedule-list'),
  scheduleTitle: document.getElementById('schedule-title'),
  scheduleSummary: document.getElementById('schedule-summary'),
  scheduleView: document.getElementById('schedule-view'),
  searchConversations: document.getElementById('search-conversations'),
  searchForm: document.getElementById('search-form'),
  searchProjectFiles: document.getElementById('search-project-files'),
  searchProjectText: document.getElementById('search-project-text'),
  searchQuery: document.getElementById('search-query'),
  searchStatus: document.getElementById('search-status'),
  searchView: document.getElementById('search-view'),
  sessionCheckpoint: document.querySelector('[data-checkpoint="session"]'),
  sessionCheckpointCopy: document.getElementById('session-checkpoint-copy'),
  sessionCheckpointState: document.getElementById('session-checkpoint-state'),
  selectedModelsList: document.getElementById('selected-models-list'),
  sidebarClose: document.getElementById('sidebar-close'),
  sidebarOpen: document.getElementById('sidebar-open'),
  statusMessage: document.getElementById('status-message'),
  statusTitle: document.getElementById('status-title'),
  timelineSummary: document.getElementById('timeline-summary'),
  timelineTitle: document.getElementById('timeline-title'),
  workspaceCheckpoint: document.querySelector('[data-checkpoint="workspace"]'),
  workspaceCheckpointCopy: document.getElementById('workspace-checkpoint-copy'),
  workspaceCheckpointState: document.getElementById('workspace-checkpoint-state'),
  workspaceComposer: document.getElementById('workspace-composer'),
  workspaceEmpty: document.getElementById('workspace-empty'),
  workspaceMessage: document.getElementById('workspace-message'),
  workspaceMain: document.getElementById('workspace-main'),
  workspaceSend: document.getElementById('workspace-send'),
  workspaceThread: document.getElementById('workspace-thread'),
  workspaceTitle: document.getElementById('workspace-title'),
  workspaceTranscript: document.getElementById('workspace-transcript'),
  workspaceView: document.getElementById('workspace-view'),
};
elements.viewButtons = Array.from(document.querySelectorAll('[data-view]'));

const surfaceViews = Object.freeze({
  workspace: elements.workspaceView,
  search: elements.searchView,
  'model-plaza': elements.modelPlazaView,
  schedule: elements.scheduleView,
  project: elements.projectView,
});

function setText(element, value) {
  if (element) element.textContent = value;
}

function appendEmptyMessage(container, className, message) {
  if (!container) return;
  const copy = document.createElement('p');
  copy.className = className;
  copy.textContent = message;
  container.append(copy);
}

function normalizeSearchValue(value) {
  return String(value ?? '').trim();
}

function fuzzyConversationMatches(title, query) {
  const normalizedTitle = normalizeSearchValue(title).toLocaleLowerCase();
  const normalizedQuery = normalizeSearchValue(query).toLocaleLowerCase();
  if (!normalizedTitle || !normalizedQuery) return false;

  let cursor = 0;
  for (const character of normalizedQuery) {
    cursor = normalizedTitle.indexOf(character, cursor);
    if (cursor < 0) return false;
    cursor += 1;
  }
  return true;
}

function projectFilenameMatches(path, query) {
  const normalizedPath = normalizeSearchValue(path).toLocaleLowerCase();
  const normalizedQuery = normalizeSearchValue(query).toLocaleLowerCase();
  return Boolean(normalizedPath && normalizedQuery && normalizedPath.includes(normalizedQuery));
}

function invalidateSearchState() {
  searchEpoch += 1;
  if (searchDebounceTimer !== null) {
    window.clearTimeout(searchDebounceTimer);
    searchDebounceTimer = null;
  }
  state.search.query = '';
  state.search.conversations = [];
  state.search.projectFiles = [];
  state.search.projectText = [];
  state.search.pending = false;
  state.search.searched = false;
  elements.searchQuery.value = '';
  renderSearchView();
}

function scheduleSummary(tasks) {
  return (Array.isArray(tasks) ? tasks : []).reduce((value, task) => {
    if (task && task.status === 'completed') value.completed += 1;
    else if (task && task.status === 'cancelled') value.cancelled += 1;
    else value.open += 1;
    return value;
  }, { open: 0, completed: 0, cancelled: 0 });
}

function scheduleHeading(summary) {
  const total = summary.open + summary.completed + summary.cancelled;
  if (total === 0) return '本地已安排任务';
  return `本地已安排任务 · ${summary.open} 进行中 · ${summary.completed} 已完成 · ${summary.cancelled} 已取消`;
}

function cloudSummaryLabel() {
  if (state.phase === 'ready') {
    return `已连接 · ${state.conversations.length} 会话 · ${callableModelCount(state.catalog)} 模型`;
  }
  if (state.phase === 'signed-out') return '等待登录 AstraRelay';
  if (state.phase === 'offline') return '云端暂时不可用';
  return '正在建立安全会话';
}

function publicPlazaModels(catalog) {
  const models = [];
  for (const provider of catalog && Array.isArray(catalog.providers) ? catalog.providers : []) {
    const chat = provider && provider.capabilities && provider.capabilities.chat;
    if (!Array.isArray(chat)) continue;
    for (const model of chat) {
      if (!model || typeof model.model_id !== 'string') continue;
      models.push({
        model_id: model.model_id,
        display_name: typeof model.display_name === 'string' ? model.display_name : model.model_id,
        capability_note: typeof model.capability_note === 'string' && model.capability_note.trim()
          ? model.capability_note.trim()
          : (typeof model.model_type === 'string' && model.model_type.trim()
            ? model.model_type.trim()
            : '能力信息待确认'),
        callable_label: model.callable === true ? '可调用聊天模型' : '仅供查看',
      });
    }
  }
  return models.sort((left, right) => left.display_name.localeCompare(right.display_name, 'zh-CN'));
}

async function searchWorkspace(rawQuery) {
  const normalized = normalizeSearchValue(rawQuery);
  if (!normalized) return { conversations: [], projectFiles: [], projectText: [] };

  const conversations = (Array.isArray(state.conversations) ? state.conversations : []).filter((item) => {
    const title = item && typeof item.title === 'string' ? item.title : '';
    return fuzzyConversationMatches(title, normalized);
  });

  const projectFiles = state.project.status.authorized
    ? (Array.isArray(state.project.tree) ? state.project.tree : []).filter((entry) => (
      entry
      && entry.type === 'file'
      && typeof entry.path === 'string'
      && projectFilenameMatches(entry.path, normalized)
    ))
    : [];

  const query = normalized;
  let projectText = [];
  if (state.project.status.authorized
    && window.astraDesktop
    && window.astraDesktop.project
    && typeof window.astraDesktop.project.search === 'function') {
    const response = await window.astraDesktop.project.search(query);
    projectText = Array.isArray(response)
      ? response
      : (response && Array.isArray(response.results) ? response.results : []);
  }
  return {
    conversations,
    projectFiles,
    projectText: Array.isArray(projectText) ? projectText : [],
  };
}

function renderSearchResultButton(container, title, detail, onClick) {
  const button = document.createElement('button');
  const heading = document.createElement('strong');
  const meta = document.createElement('span');
  button.type = 'button';
  button.className = 'search-result-row';
  heading.textContent = title;
  meta.className = 'search-result-meta';
  meta.textContent = detail;
  button.append(heading, meta);
  button.addEventListener('click', onClick);
  container.append(button);
}

function renderSearchView() {
  const query = state.search.query;
  const normalized = normalizeSearchValue(query);
  elements.searchQuery.disabled = false;
  elements.searchQuery.value = query;
  elements.searchConversations.replaceChildren();
  elements.searchProjectFiles.replaceChildren();
  elements.searchProjectText.replaceChildren();

  if (!normalized) {
    setText(elements.searchStatus, '输入关键词后分组检索云端任务标题与已授权项目文本。');
    appendEmptyMessage(elements.searchConversations, 'search-empty', '尚未开始搜索。');
    appendEmptyMessage(
      elements.searchProjectFiles,
      'search-empty',
      state.project.status.authorized
        ? '尚未开始项目文件名搜索。'
        : '授权本地项目后可在这里查看文件名匹配。',
    );
    appendEmptyMessage(
      elements.searchProjectText,
      'search-empty',
      state.project.status.authorized
        ? '尚未开始项目文本搜索。'
        : '授权本地项目后可在这里查看文本匹配。',
    );
    return;
  }

  if (state.search.pending) {
    setText(elements.searchStatus, `正在通过受限桥接搜索 “${normalized}”。`);
    appendEmptyMessage(elements.searchConversations, 'search-empty', '正在加载云端任务结果…');
    appendEmptyMessage(elements.searchProjectFiles, 'search-empty', '正在筛选项目文件名…');
    appendEmptyMessage(elements.searchProjectText, 'search-empty', '正在加载项目文本结果…');
    return;
  }

  const conversationCount = state.search.conversations.length;
  const projectFileCount = state.search.projectFiles.length;
  const projectCount = state.search.projectText.length;
  setText(
    elements.searchStatus,
    `“${normalized}” 共找到 ${conversationCount + projectFileCount + projectCount} 条结果（云端任务 ${conversationCount}，项目文件 ${projectFileCount}，项目文本 ${projectCount}）。`,
  );

  if (conversationCount === 0) {
    appendEmptyMessage(elements.searchConversations, 'search-empty', '没有匹配的云端任务标题。');
  } else {
    for (const conversation of state.search.conversations) {
      const title = typeof conversation.title === 'string' && conversation.title.trim()
        ? conversation.title.trim()
        : '未命名任务';
      renderSearchResultButton(
        elements.searchConversations,
        title,
        '打开云端任务',
        () => {
          selectActiveView('workspace');
          selectConversation(conversation).catch(() => {});
        },
      );
    }
  }

  if (!state.project.status.authorized) {
    appendEmptyMessage(elements.searchProjectFiles, 'search-empty', '授权本地项目后才会显示文件名搜索结果。');
    appendEmptyMessage(elements.searchProjectText, 'search-empty', '授权本地项目后才会显示文本搜索结果。');
    return;
  }

  if (projectFileCount === 0) {
    appendEmptyMessage(elements.searchProjectFiles, 'search-empty', '没有匹配的项目文件名。');
  } else {
    for (const entry of state.search.projectFiles) {
      renderSearchResultButton(
        elements.searchProjectFiles,
        entry.path,
        '打开项目文件',
        () => {
          selectActiveView('project');
          openProjectFile(entry.path).catch(renderProjectFailure);
        },
      );
    }
  }

  if (projectCount === 0) {
    appendEmptyMessage(elements.searchProjectText, 'search-empty', '没有匹配的项目文本。');
    return;
  }

  for (const entry of state.search.projectText) {
    if (!entry || typeof entry.path !== 'string') continue;
    const lineNumber = Number.isFinite(entry.line) ? entry.line : 0;
    const preview = typeof entry.preview === 'string' && entry.preview.trim()
      ? entry.preview.trim()
      : '打开文件预览';
    renderSearchResultButton(
      elements.searchProjectText,
      entry.path,
      lineNumber > 0 ? `第 ${lineNumber} 行 · ${preview}` : preview,
      () => {
        selectActiveView('project');
        openProjectFile(entry.path).catch(renderProjectFailure);
      },
    );
  }
}

async function performSearch(query, currentEpoch) {
  try {
    const results = await searchWorkspace(query);
    if (currentEpoch !== searchEpoch) return;
    state.search.conversations = Array.isArray(results.conversations) ? results.conversations : [];
    state.search.projectFiles = Array.isArray(results.projectFiles) ? results.projectFiles : [];
    state.search.projectText = Array.isArray(results.projectText) ? results.projectText : [];
  } catch {
    if (currentEpoch !== searchEpoch) return;
    state.search.conversations = [];
    state.search.projectFiles = [];
    state.search.projectText = [];
    setText(elements.searchStatus, `搜索 “${query}” 时未能完成受限桥接请求。`);
  } finally {
    if (currentEpoch !== searchEpoch) return;
    state.search.pending = false;
    renderSearchView();
  }
}

function startSearch(rawQuery, debounced) {
  const query = normalizeSearchValue(rawQuery);
  const currentEpoch = ++searchEpoch;
  if (searchDebounceTimer !== null) {
    window.clearTimeout(searchDebounceTimer);
    searchDebounceTimer = null;
  }
  state.search.query = query;
  state.search.searched = query.length > 0;
  state.search.pending = query.length > 0;
  state.search.conversations = [];
  state.search.projectFiles = [];
  state.search.projectText = [];
  renderSearchView();

  if (!query) {
    state.search.pending = false;
    renderSearchView();
    return;
  }

  if (debounced) {
    searchDebounceTimer = window.setTimeout(() => {
      searchDebounceTimer = null;
      performSearch(query, currentEpoch).catch(() => {});
    }, SEARCH_DEBOUNCE_MS);
    return;
  }

  performSearch(query, currentEpoch).catch(() => {});
}

function scheduleSearchFromInput() {
  startSearch(elements.searchQuery.value, true);
}

function submitSearchForm(event) {
  event.preventDefault();
  startSearch(elements.searchQuery.value, false);
}

function renderModelPlazaView() {
  const models = publicPlazaModels(state.catalog);
  elements.modelPlazaList.replaceChildren();
  setText(
    elements.modelPlazaSummary,
    models.length
      ? `公开目录中有 ${models.length} 个聊天模型条目。这里只展示名称、能力说明与可调用状态。`
      : '公开目录暂时没有可显示的聊天模型条目。',
  );

  if (models.length === 0) {
    appendEmptyMessage(elements.modelPlazaList, 'model-plaza-empty', '等待云端目录加载完成。');
    return;
  }

  for (const model of models) {
    const card = document.createElement('article');
    const name = document.createElement('h2');
    const note = document.createElement('p');
    const stateCopy = document.createElement('span');
    card.className = 'model-plaza-card';
    name.textContent = model.display_name;
    note.className = 'model-plaza-note';
    note.textContent = model.capability_note;
    stateCopy.className = 'model-plaza-state';
    stateCopy.textContent = model.callable_label;
    card.append(name, note, stateCopy);
    elements.modelPlazaList.append(card);
  }
}

function scheduleActionButton(label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'secondary-button schedule-action';
  button.textContent = label;
  button.addEventListener('click', onClick);
  return button;
}

function deleteScheduleTask(task) {
  if (!task || !scheduleStore) return;
  if (!window.confirm(`删除本地任务“${task.title}”？此操作仅影响当前本地队列。`)) return;
  scheduleStore.remove(task.id);
  renderScheduleState();
}

function renderScheduleState() {
  renderScheduleView();
  renderInspector();
}

function renderScheduleView() {
  const tasks = scheduleStore ? scheduleStore.snapshot() : [];
  const summary = scheduleSummary(tasks);
  setText(elements.scheduleSummary, scheduleHeading(summary));
  elements.scheduleTitle.disabled = !scheduleStore;
  elements.scheduleList.replaceChildren();

  if (!scheduleStore) {
    appendEmptyMessage(elements.scheduleList, 'search-empty', '当前环境未提供本地任务队列。');
    return;
  }

  if (tasks.length === 0) {
    appendEmptyMessage(elements.scheduleList, 'search-empty', '还没有本地安排的任务。');
    return;
  }

  for (const task of tasks) {
    const item = document.createElement('li');
    const header = document.createElement('div');
    const title = document.createElement('strong');
    const meta = document.createElement('span');
    const actions = document.createElement('div');
    item.className = 'schedule-item';
    header.className = 'schedule-item-header';
    title.textContent = task.title;
    meta.className = 'search-result-meta';
    meta.textContent = `状态：${task.status} · 更新于 ${task.updatedAt}`;
    header.append(title, meta);
    actions.className = 'schedule-item-actions';
    actions.append(
      scheduleActionButton('重命名', () => {
        const nextTitle = window.prompt('输入新的任务标题', task.title);
        if (!nextTitle || !nextTitle.trim()) return;
        scheduleStore.rename(task.id, nextTitle);
        renderScheduleState();
      }),
      scheduleActionButton('删除', () => {
        deleteScheduleTask(task);
      }),
    );
    if (task.status === 'open') {
      actions.append(
        scheduleActionButton('完成', () => {
          scheduleStore.complete(task.id);
          renderScheduleState();
        }),
        scheduleActionButton('取消', () => {
          scheduleStore.cancel(task.id);
          renderScheduleState();
        }),
      );
    } else {
      actions.append(
        scheduleActionButton('重新打开', () => {
          scheduleStore.reopen(task.id);
          renderScheduleState();
        }),
      );
    }
    item.append(header, actions);
    elements.scheduleList.append(item);
  }
}

function renderActiveView() {
  for (const [view, element] of Object.entries(surfaceViews)) {
    if (!element) continue;
    element.hidden = !viewState.isActive(view);
    element.classList.toggle('is-active', viewState.isActive(view));
  }
  for (const button of elements.viewButtons) {
    if (!(button instanceof HTMLElement)) continue;
    const view = button.dataset.view;
    button.classList.toggle('is-active', viewState.isActive(view));
  }
  document.body.classList.toggle('workspace-active', viewState.isActive('workspace') && state.phase === 'ready');
  document.body.classList.toggle('project-active', viewState.isActive('project'));
}

function selectActiveView(view) {
  state.activeView = viewState.select(view);
  renderActiveView();
  renderInspector();
  return state.activeView;
}

function projectRootName() {
  const status = state.project.status || {};
  return status.authorized && typeof status.rootName === 'string' && status.rootName.trim()
    ? status.rootName.trim()
    : '';
}

function projectStatusLabel() {
  const rootName = projectRootName();
  return rootName ? `已授权 · ${rootName}` : '未授权';
}

function projectInspectorDetail() {
  return state.project.status && state.project.status.authorized
    ? '受限根目录内的文本文件可打开、编辑并显式保存。'
    : '选择本地项目后启用受限目录树与显式保存。';
}

function activeProjectTab() {
  return findActiveProjectTab(state.project);
}

function clearProjectWorkspace(status) {
  const lifecycleToken = replaceProjectRoot(state.project, status);
  invalidateSearchState();
  elements.projectEditor.replaceChildren();
  elements.projectSearch.value = '';
  return lifecycleToken;
}

function renderProjectEmpty(message, detail = '') {
  elements.projectEditor.replaceChildren();
  const empty = document.createElement('div');
  const title = document.createElement('strong');
  const copy = document.createElement('span');
  empty.className = 'project-empty-state';
  title.textContent = message;
  copy.textContent = detail;
  empty.append(title, copy);
  elements.projectEditor.append(empty);
}

function renderProjectTree() {
  elements.projectTree.replaceChildren();

  if (!projectApi) {
    const message = document.createElement('p');
    message.className = 'project-tree-empty';
    message.textContent = '当前运行环境未提供项目桥接。';
    elements.projectTree.append(message);
    return;
  }

  if (!state.project.status.authorized) {
    const message = document.createElement('p');
    message.className = 'project-tree-empty';
    message.textContent = '选择本地项目后显示受限文件树。';
    elements.projectTree.append(message);
    return;
  }

  const entries = state.project.query ? state.project.results : state.project.tree;
  if (!Array.isArray(entries) || entries.length === 0) {
    const message = document.createElement('p');
    message.className = 'project-tree-empty';
    message.textContent = state.project.query ? '没有匹配结果。' : '当前项目没有可显示的文本文件。';
    elements.projectTree.append(message);
    return;
  }

  for (const entry of entries) {
    if (!entry || typeof entry.path !== 'string') continue;
    const row = document.createElement('button');
    const label = document.createElement('span');
    row.type = 'button';
    row.className = `project-tree-row is-${entry.type === 'directory' ? 'directory' : 'file'}`;
    row.dataset.depth = String(Math.max(0, Math.min(Number(entry.depth) || 0, 6)));
    label.textContent = state.project.query && typeof entry.preview === 'string'
      ? `${entry.path}:${entry.line} — ${entry.preview}`
      : entry.path;
    row.append(label);
    if (entry.type === 'directory') {
      row.disabled = true;
    } else {
      row.addEventListener('click', () => { openProjectFile(entry.path).catch(renderProjectFailure); });
    }
    elements.projectTree.append(row);
  }
}

function renderProjectTabs() {
  elements.projectTabs.replaceChildren();

  for (const tab of state.project.tabs) {
    const item = document.createElement('div');
    const activate = document.createElement('button');
    const close = document.createElement('button');
    item.className = 'project-tab-shell';
    activate.type = 'button';
    activate.className = 'project-tab';
    activate.classList.toggle('is-active', tab.path === state.project.activeTabId);
    activate.classList.toggle('is-dirty', tab.dirty);
    activate.textContent = tab.path;
    activate.addEventListener('click', () => {
      state.project.activeTabId = tab.path;
      renderProjectState();
    });
    close.type = 'button';
    close.className = 'project-tab-close';
    close.textContent = '×';
    close.setAttribute('aria-label', `关闭 ${tab.path}`);
    close.addEventListener('click', (event) => {
      event.stopPropagation();
      closeProjectTab(tab.path);
    });
    item.append(activate, close);
    elements.projectTabs.append(item);
  }

  elements.projectSave.disabled = !canSaveActiveProjectTab(state.project);
}

function mountProjectEditor(tab) {
  if (!tab) {
    renderProjectEmpty('选择项目文件开始编辑', '文件只会通过已授权项目桥接读取和显式保存。');
    return;
  }

  if (!tab.editorHost) {
    tab.editorHost = document.createElement('div');
    tab.editorHost.className = 'project-editor-host';
  }
  elements.projectEditor.replaceChildren(tab.editorHost);

  if (!tab.editor) {
    tab.editor = AstraRelayEditor.mount(tab.editorHost, tab.content, (content) => {
      tab.content = content;
      tab.dirty = content !== tab.originalContent;
      renderProjectTabs();
      renderInspector();
    });
  }
  tab.editor.focus();
}

function renderProject() {
  const authorized = Boolean(state.project.status && state.project.status.authorized);
  const rootName = projectRootName();
  elements.projectSearch.disabled = !authorized;
  elements.projectOpen.disabled = !projectApi;
  setText(elements.projectTitle, rootName || '本地项目');
  setText(elements.projectStatus, projectStatusLabel());
  setText(elements.projectOpenStatus, authorized ? '已授权' : '未授权');
  setText(elements.projectMiniStatus, projectStatusLabel());
  setText(elements.projectInspectorCopy, projectInspectorDetail());
  elements.projectMiniStatus.className = authorized ? 'mini-status is-ready' : 'mini-status is-muted';
  elements.projectStatus.className = authorized ? 'mini-status is-ready' : 'mini-status is-muted';
  renderProjectTree();
  renderProjectTabs();
  mountProjectEditor(activeProjectTab());
}

function renderProjectState() {
  renderProject();
  renderInspector();
}

function renderInspector() {
  const activeView = VIEW_LABELS[state.activeView] || VIEW_LABELS.workspace;
  const tasks = scheduleStore ? scheduleStore.snapshot() : [];
  const summary = scheduleSummary(tasks);
  const total = summary.open + summary.completed + summary.cancelled;
  const projectFacts = projectInspectorState(state.project);
  const activeTab = projectFacts.activeTabPath
    ? `${projectFacts.activeTabPath} · ${projectFacts.activeTabDirty ? '未保存' : '已保存'}`
    : '未打开编辑文件';

  setText(elements.inspectorViewSummary, activeView);
  setText(elements.inspectorViewDetail, VIEW_DETAILS[state.activeView] || VIEW_DETAILS.workspace);
  setText(elements.inspectorCloudSummary, cloudSummaryLabel());
  setText(
    elements.inspectorProjectSummary,
    state.project.status && state.project.status.authorized ? projectRootName() : '未选择本地项目',
  );
  setText(elements.inspectorProjectChanges, `本地修改的已打开文件：${projectFacts.dirtyFileCount}`);
  setText(elements.inspectorProjectActiveTab, `活动编辑器：${activeTab}`);
  setText(elements.inspectorScheduleSummary, `待办 ${summary.open} · 已完成 ${summary.completed}`);
  setText(
    elements.inspectorScheduleDetail,
    total === 0
      ? '当前没有本地排队任务。'
      : (summary.cancelled > 0
        ? `已取消 ${summary.cancelled} 项；任务只保存在当前桌面队列。`
        : '任务只保存在当前桌面队列。'),
  );
}

function renderProjectFailure() {
  renderProjectTree();
  if (!activeProjectTab()) {
    renderProjectEmpty('项目操作失败', '请确认仍有授权，或重新选择本地项目。');
  }
}

async function refreshProjectStatus() {
  if (!projectApi) {
    renderProject();
    return;
  }
  const statusToken = captureProjectRootLifecycle(state.project);
  try {
    const status = await projectApi.status();
    if (!isProjectRootLifecycleCurrent(state.project, statusToken)) return;
    if (!status || !status.authorized) {
      clearProjectWorkspace(status);
      renderProjectState();
      return;
    }
    if (!rootStatusMatchesProject(state.project, status)) {
      const lifecycleToken = clearProjectWorkspace(status);
      renderProjectState();
      const tree = await projectApi.listTree();
      if (applyProjectLifecycleResult(state.project, lifecycleToken, () => {
        state.project.tree = tree && Array.isArray(tree.entries) ? tree.entries : [];
      })) renderProject();
      return;
    }
    state.project.status = status;
    const lifecycleToken = captureProjectRootLifecycle(state.project);
    const tree = await projectApi.listTree();
    if (applyProjectLifecycleResult(state.project, lifecycleToken, () => {
      state.project.tree = tree && Array.isArray(tree.entries) ? tree.entries : [];
    })) renderProject();
  } catch (error) {
    if (!isProjectRootLifecycleCurrent(state.project, statusToken)) return;
    clearProjectWorkspace({ authorized: false });
    renderProjectState();
  }
}

async function openProject() {
  if (!projectApi) return;
  if (hasDirtyProjectTabs(state.project)
    && !window.confirm('切换项目将关闭所有未保存的文件。是否继续？')) return;
  const selectionToken = captureProjectRootLifecycle(state.project);
  let status;
  try {
    status = await projectApi.chooseRoot();
  } catch (error) {
    if (isProjectRootLifecycleCurrent(state.project, selectionToken)) throw error;
    return;
  }
  if (!isProjectRootLifecycleCurrent(state.project, selectionToken)) return;
  if (!status || status.changed === false) return;
  const lifecycleToken = clearProjectWorkspace(status);
  renderProjectState();
  if (state.project.status.authorized) {
    let tree;
    try {
      tree = await projectApi.listTree();
    } catch (error) {
      if (isProjectRootLifecycleCurrent(state.project, lifecycleToken)) throw error;
      return;
    }
    if (applyProjectLifecycleResult(state.project, lifecycleToken, () => {
      state.project.tree = tree && Array.isArray(tree.entries) ? tree.entries : [];
    })) renderProject();
  }
}

async function searchProject() {
  state.project.query = elements.projectSearch.value.trim();
  state.project.results = [];
  if (!projectApi || !state.project.status.authorized || !state.project.query) {
    renderProjectTree();
    return;
  }
  const lifecycleToken = captureProjectRootLifecycle(state.project);
  const query = state.project.query;
  let response;
  try {
    response = await projectApi.search(query);
  } catch (error) {
    if (isProjectRootLifecycleCurrent(state.project, lifecycleToken)) throw error;
    return;
  }
  if (response && response.query === state.project.query && Array.isArray(response.results)
    && applyProjectLifecycleResult(state.project, lifecycleToken, () => {
      state.project.results = response.results.map((entry) => ({ ...entry, type: 'file' }));
    })) renderProjectTree();
}

async function openProjectFile(projectPath) {
  if (!projectApi || typeof projectPath !== 'string') return;
  const lifecycleToken = captureProjectRootLifecycle(state.project);
  let preview;
  try {
    preview = await projectApi.preview(projectPath);
  } catch (error) {
    if (isProjectRootLifecycleCurrent(state.project, lifecycleToken)) throw error;
    return;
  }
  if (!preview || typeof preview.path !== 'string' || typeof preview.content !== 'string') return;
  if (applyProjectLifecycleResult(state.project, lifecycleToken, () => {
    let tab = state.project.tabs.find((item) => item && item.path === preview.path);
    if (!tab) {
      tab = {
        path: preview.path,
        originalContent: preview.content,
        content: preview.content,
        dirty: false,
        editor: null,
        rootGeneration: lifecycleToken.generation,
      };
      state.project.tabs = [...state.project.tabs, tab];
    } else if (!tab.dirty) {
      tab.originalContent = preview.content;
      tab.content = preview.content;
      if (tab.editor) tab.editor.setValue(preview.content);
    }
    state.project.activeTabId = tab.path;
  })) renderProjectState();
}

async function saveActiveTab() {
  if (!projectApi) return;
  const lifecycleToken = captureProjectRootLifecycle(state.project);
  try {
    if (await saveActiveProjectTab(state.project, projectApi.writeText, lifecycleToken)
      && isProjectRootLifecycleCurrent(state.project, lifecycleToken)) {
      renderProjectTabs();
      renderInspector();
    }
  } catch (error) {
    if (isProjectRootLifecycleCurrent(state.project, lifecycleToken)) throw error;
  }
}

function submitScheduleForm(event) {
  event.preventDefault();
  if (!scheduleStore) return;
  const title = normalizeSearchValue(elements.scheduleTitle.value);
  if (!title) return;
  scheduleStore.create(title);
  elements.scheduleTitle.value = '';
  renderScheduleState();
}

function closeProjectTab(projectPath) {
  const tab = state.project.tabs.find((item) => item && item.path === projectPath);
  if (!tab) return;
  if (tab.dirty && !window.confirm(`关闭未保存的文件“${tab.path}”？`)) return;
  if (tab.editor) {
    tab.editor.destroy();
    tab.editor = null;
  }
  state.project.tabs = state.project.tabs.filter((item) => item && item.path !== projectPath);
  if (state.project.activeTabId === projectPath) {
    const next = state.project.tabs[state.project.tabs.length - 1];
    state.project.activeTabId = next ? next.path : null;
  }
  renderProjectState();
}

function setCheckpoint(element, statusElement, status, label) {
  element.classList.remove('is-ready', 'is-warning');
  if (status) element.classList.add(status);
  setText(statusElement, label);
}

function callableModelCount(catalog) {
  if (!catalog || !Array.isArray(catalog.providers)) return 0;
  let total = 0;
  for (const provider of catalog.providers) {
    const capabilities = provider && provider.capabilities;
    if (!capabilities || !Array.isArray(capabilities.chat)) continue;
    total += capabilities.chat.filter((model) => model && model.callable === true).length;
  }
  return total;
}

function conversationMutationInProgress() {
  return state.creatingConversation || state.deletingConversationIds.size > 0;
}

function renderConversationOperationNotice() {
  if (!state.conversationNotice) return;
  const notice = document.createElement('p');
  notice.className = 'conversation-operation-status';
  notice.setAttribute('role', 'status');
  notice.textContent = state.conversationNotice;
  elements.conversationList.append(notice);
}

function renderConversationList() {
  elements.conversationList.replaceChildren();
  elements.newTask.disabled = state.phase !== 'ready' || conversationMutationInProgress();
  elements.newTask.setAttribute('aria-busy', String(conversationMutationInProgress()));
  elements.refreshButton.disabled = conversationMutationInProgress();

  if (state.phase === 'loading' || state.phase === 'authenticating') {
    for (let index = 0; index < 4; index += 1) {
      const skeleton = document.createElement('div');
      skeleton.className = `conversation-skeleton skeleton-${index + 1}`;
      elements.conversationList.append(skeleton);
    }
    return;
  }

  if (state.phase !== 'ready') {
    const message = document.createElement('p');
    message.className = 'conversation-empty';
    message.textContent = state.phase === 'offline'
      ? '云端暂时不可用。重新连接后将在这里恢复最近任务。'
      : '登录后将在这里显示最近的云端任务。';
    elements.conversationList.append(message);
    return;
  }

  const conversations = Array.isArray(state.conversations)
    ? state.conversations.slice(0, 9)
    : [];
  renderConversationOperationNotice();
  if (conversations.length === 0) {
    const message = document.createElement('p');
    message.className = 'conversation-empty';
    message.textContent = '暂无云端任务。真实任务交互将在下一阶段接入。';
    elements.conversationList.append(message);
    return;
  }

  for (const conversation of conversations) {
    const item = document.createElement('div');
    const row = document.createElement('button');
    const title = document.createElement('span');
    const deleting = state.deletingConversationIds.has(conversation.id);
    item.className = 'conversation-list-item';
    row.className = 'conversation-row';
    row.type = 'button';
    row.disabled = deleting;
    row.classList.toggle('is-deleting', deleting);
    row.classList.toggle('is-active', conversation.id === state.activeId);
    const conversationTitle = typeof conversation.title === 'string' && conversation.title.trim()
      ? conversation.title.trim()
      : '未命名任务';
    title.textContent = deleting ? `${conversationTitle}（正在删除…）` : conversationTitle;
    row.append(title);
    row.addEventListener('click', () => {
      selectActiveView('workspace');
      selectConversation(conversation);
    });
    row.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      if (deleting) return;
      openConversationMenu(conversation.id, event.clientX, event.clientY);
    });
    item.append(row);
    elements.conversationList.append(item);
  }
}

function callableModels(catalog) {
  const models = [];
  for (const provider of catalog && Array.isArray(catalog.providers) ? catalog.providers : []) {
    const chat = provider && provider.capabilities && provider.capabilities.chat;
    if (!Array.isArray(chat)) continue;
    for (const model of chat) {
      if (model && model.callable === true && typeof model.model_id === 'string') {
        models.push({
          model_id: model.model_id,
          display_name: typeof model.display_name === 'string' ? model.display_name : model.model_id,
          provider: typeof provider.name === 'string' ? provider.name : '',
        });
      }
    }
  }
  return models;
}

function selectedModelIds() {
  return state.modelSettings
    .filter((item) => item && typeof item.model_id === 'string')
    .map((item) => item.model_id);
}

function selectedPublicModels() {
  const namesById = new Map();
  for (const provider of state.catalog && Array.isArray(state.catalog.providers) ? state.catalog.providers : []) {
    const chat = provider && provider.capabilities && provider.capabilities.chat;
    if (!Array.isArray(chat)) continue;
    for (const model of chat) {
      if (!model || model.callable !== true || typeof model.model_id !== 'string'
        || typeof model.display_name !== 'string' || !model.display_name.trim()) continue;
      namesById.set(model.model_id, model.display_name.trim());
    }
  }
  return selectedModelIds().map((modelId) => namesById.get(modelId)).filter(Boolean);
}

function closeModelSummaryOverflow() {
  elements.modelSummaryOverflowPanel.hidden = true;
  elements.modelSummaryOverflow.setAttribute('aria-expanded', 'false');
}

function openModelSummaryOverflow(models = selectedPublicModels()) {
  if (models.length <= 2) return;
  elements.modelSummaryOverflowPanel.replaceChildren(...models.map((name) => {
    const item = document.createElement('span');
    item.className = 'model-summary-overflow-item';
    item.textContent = name;
    return item;
  }));
  elements.modelSummaryOverflowPanel.hidden = false;
  elements.modelSummaryOverflow.setAttribute('aria-expanded', 'true');
}

function renderSelectedModels() {
  const models = selectedPublicModels();
  const compactNames = models.slice(0, 2);
  closeModelSummaryOverflow();
  setText(elements.modelSummary, compactNames.length ? compactNames.join(' · ') : '未选择模型');
  elements.modelSummaryOverflow.hidden = models.length <= 2;
  elements.modelSummaryOverflow.textContent = `+${Math.max(0, models.length - 2)}`;
  elements.selectedModelsList.replaceChildren();
  if (models.length === 0) {
    appendEmptyMessage(elements.selectedModelsList, 'selected-models-empty', '尚未选择协作模型');
    return;
  }
  for (const name of models) {
    const chip = document.createElement('span');
    chip.className = 'selected-model-chip';
    chip.textContent = name;
    elements.selectedModelsList.append(chip);
  }
}

function renderWorkspace() {
  const ready = state.phase === 'ready';
  const workspaceIsActive = viewState.isActive('workspace');
  elements.liveWorkspace.hidden = !ready || !workspaceIsActive;
  elements.workspaceComposer.closest('.desktop-composer').hidden = !ready || !workspaceIsActive;
  if (!ready) return;

  const active = state.conversations.find((item) => item && item.id === state.activeId);
  elements.workspaceEmpty.hidden = Boolean(active);
  elements.workspaceThread.hidden = !active;
  setText(elements.workspaceTitle, active && typeof active.title === 'string' ? active.title : '新对话');
  elements.workspaceMessage.disabled = state.loadingConversation || conversationMutationInProgress();
  elements.workspaceSend.disabled = state.loadingConversation || conversationMutationInProgress();
  elements.conversationMenu.disabled = !active || state.deletingConversationIds.has(state.activeId);

  const selected = selectedModelIds();
  renderSelectedModels();
  setText(elements.modelSettings, selected.length ? `模型 · ${selected.length}` : '选择协作模型');

  elements.workspaceTranscript.replaceChildren();
  for (const message of Array.isArray(state.messages) ? state.messages : []) {
    const row = document.createElement('article');
    const meta = document.createElement('span');
    const bubble = document.createElement('div');
    const isUser = message && message.role === 'user';
    row.className = `message-row ${isUser ? 'is-user' : 'is-assistant'}`;
    meta.className = 'message-meta';
    meta.textContent = isUser ? '你' : (message && typeof message.model === 'string' ? message.model : '协作模型');
    bubble.className = 'message-bubble';
    bubble.textContent = message && typeof message.content === 'string' ? message.content : '';
    row.append(meta, bubble);
    elements.workspaceTranscript.append(row);
  }

  elements.modelOptions.replaceChildren();
  for (const model of callableModels(state.catalog)) {
    const label = document.createElement('label');
    const checkbox = document.createElement('input');
    const copy = document.createElement('span');
    const name = document.createElement('strong');
    const provider = document.createElement('span');
    label.className = 'model-option';
    checkbox.type = 'checkbox';
    checkbox.checked = selected.includes(model.model_id);
    checkbox.addEventListener('change', () => toggleModel(model, checkbox.checked));
    copy.className = 'model-option-copy';
    name.textContent = model.display_name;
    provider.textContent = model.provider;
    copy.append(name, provider);
    label.append(checkbox, copy);
    elements.modelOptions.append(label);
  }
}

async function refreshConversationList() {
  if (!workspaceApi) return;
  const listRevision = state.conversationListRevision;
  const conversations = await workspaceApi.listConversations();
  if (listRevision === state.conversationListRevision && Array.isArray(conversations)) {
    state.conversations = conversations;
  }
}

async function selectConversation(conversation, requestedSelectionEpoch = null) {
  if (!workspaceApi || !conversation || typeof conversation.id !== 'string') return;
  const requestedId = conversation.id;
  const selectionEpoch = requestedSelectionEpoch === null
    ? ++conversationSelectionEpoch
    : requestedSelectionEpoch;
  const previous = {
    activeId: state.activeId,
    messages: state.messages,
    modelSettings: state.modelSettings,
  };
  state.loadingConversation = true;
  render();
  try {
    const snapshot = await workspaceApi.openConversation(requestedId);
    if (selectionEpoch !== conversationSelectionEpoch) return;
    if (!snapshot || snapshot.id !== requestedId || state.deletingConversationIds.has(requestedId)) return;
    state.activeId = requestedId;
    state.messages = Array.isArray(snapshot.messages) ? snapshot.messages : [];
    state.modelSettings = snapshot.model_settings && Array.isArray(snapshot.model_settings.models)
      ? snapshot.model_settings.models : [];
  } catch {
    if (selectionEpoch !== conversationSelectionEpoch) return;
    state.activeId = previous.activeId;
    state.messages = previous.messages;
    state.modelSettings = previous.modelSettings;
  } finally {
    if (selectionEpoch !== conversationSelectionEpoch) return;
    state.loadingConversation = false;
    render();
  }
}

async function createConversation() {
  if (!workspaceApi || conversationMutationInProgress()) return null;
  const creationEpoch = ++conversationSelectionEpoch;
  state.conversationListRevision += 1;
  state.creatingConversation = true;
  state.conversationNotice = '正在新建对话…';
  render();
  try {
    const conversation = await workspaceApi.createConversation();
    if (!conversation || typeof conversation.id !== 'string') throw new Error('invalid conversation');
    state.conversations = [conversation, ...state.conversations.filter((item) => item && item.id !== conversation.id)];
    if (creationEpoch === conversationSelectionEpoch) {
      state.activeId = conversation.id;
      state.messages = [];
      state.modelSettings = [];
      state.loadingConversation = false;
    }
    return conversation;
  } catch {
    state.conversationNotice = '新建对话失败，请重试。';
    return null;
  } finally {
    state.creatingConversation = false;
    if (state.conversationNotice === '正在新建对话…') state.conversationNotice = '';
    render();
  }
}

async function ensureDefaultConversation() {
  if (state.phase !== 'ready' || state.activeId || state.defaultConversationRequested) return;
  state.defaultConversationRequested = true;
  if (state.conversations.length > 0) {
    await selectConversation(state.conversations[0]);
    return;
  }
  const conversation = await createConversation();
  if (!conversation) state.defaultConversationRequested = false;
}

function makeRequestId() {
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function applyStreamEvent(event) {
  if (!event || !state.activeStream || event.request_id !== state.activeStream.requestId
    || event.conversation_id !== state.activeId) return;
  if (event.event === 'token' && typeof event.model === 'string' && typeof event.token === 'string') {
    let message = state.messages.find((item) => item && item.pending === true && item.model === event.model);
    if (!message) {
      message = { role: 'assistant', model: event.model, content: '', pending: true };
      state.messages = [...state.messages, message];
    }
    message.content += event.token;
    renderWorkspace();
  }
  if (event.event === 'error') {
    state.messages = [...state.messages, {
      role: 'assistant', model: event.model || '协作模型', content: event.message || '模型回复失败，请稍后重试',
    }];
    renderWorkspace();
  }
}

async function submitWorkspaceMessage(event) {
  event.preventDefault();
  const message = elements.workspaceMessage.value.trim();
  if (!message || state.activeStream) return;
  if (state.modelSettings.length === 0) {
    elements.modelPopover.hidden = false;
    return;
  }
  try {
    if (!state.activeId) {
      const pendingModels = state.modelSettings.map((item) => ({ ...item }));
      await createConversation();
      state.modelSettings = pendingModels;
      await workspaceApi.replaceModels(state.activeId, { models: state.modelSettings });
    }
    const requestId = makeRequestId();
    state.activeStream = { requestId, conversationId: state.activeId };
    state.messages = [...state.messages, { role: 'user', content: message }];
    elements.workspaceMessage.value = '';
    renderWorkspace();
    await workspaceApi.stream(requestId, {
      message,
      conversation_id: state.activeId,
      models: state.modelSettings,
    });
  } catch {
    state.messages = [...state.messages, { role: 'assistant', content: '消息暂未送达，请稍后重试。' }];
  } finally {
    if (state.activeStream) {
      state.messages = state.messages.map((item) => ({ ...item, pending: false }));
      state.activeStream = null;
      await refreshConversationList().catch(() => {});
    }
    render();
  }
}

async function toggleModel(model, enabled) {
  const existing = state.modelSettings.filter((item) => item && item.model_id !== model.model_id);
  state.modelSettings = enabled
    ? [...existing, { model_id: model.model_id, context_mode: 'smart', message_ids: [] }]
    : existing;
  renderWorkspace();
  if (state.activeId && workspaceApi) {
    try {
      const response = await workspaceApi.replaceModels(state.activeId, { models: state.modelSettings });
      if (response && Array.isArray(response.models)) state.modelSettings = response.models;
    } catch {
      // Keep current selection visible; next change or refresh reconciles cloud state.
    }
    renderWorkspace();
  }
}

function positionConversationMenu(clientX, clientY) {
  const gutter = 12;
  const panelWidth = Math.max(1, elements.conversationMenuPanel.offsetWidth);
  const panelHeight = Math.max(1, elements.conversationMenuPanel.offsetHeight);
  const viewportWidth = Math.max(1, Number(window.innerWidth) || 0);
  const viewportHeight = Math.max(1, Number(window.innerHeight) || 0);
  const left = Math.max(gutter, Math.min(Number(clientX) || 0, viewportWidth - panelWidth - gutter));
  const top = Math.max(gutter, Math.min(Number(clientY) || 0, viewportHeight - panelHeight - gutter));
  elements.conversationMenuPanel.style.left = `${Math.round(left)}px`;
  elements.conversationMenuPanel.style.top = `${Math.round(top)}px`;
}

function openConversationMenu(conversationId, clientX, clientY) {
  if (!conversationId || state.deletingConversationIds.has(conversationId)) return;
  closeConversationMenu();
  state.menuId = conversationId;
  elements.conversationMenuPanel.hidden = false;
  elements.conversationMenuPanel.setAttribute('data-conversation-id', conversationId);
  positionConversationMenu(clientX, clientY);
}

function closeConversationMenu() {
  state.menuId = null;
  elements.conversationMenuPanel.hidden = true;
  elements.conversationMenuPanel.removeAttribute('data-conversation-id');
}

function setRenameConversationBusy(saving) {
  elements.renameConversationInput.disabled = saving;
  elements.renameConversationCancel.disabled = saving;
  elements.renameConversationClose.disabled = saving;
  elements.renameConversationSave.disabled = saving;
}

function openRenameConversationDialog(conversation) {
  if (!conversation || typeof conversation.id !== 'string' || !workspaceApi) return;
  closeConversationMenu();
  state.renameDialog = {
    conversationId: conversation.id,
    draft: typeof conversation.title === 'string' && conversation.title.trim()
      ? conversation.title : '新对话',
    saving: false,
    error: '',
  };
  elements.renameConversationDialog.hidden = false;
  elements.renameConversationInput.value = state.renameDialog.draft;
  elements.renameConversationError.textContent = '';
  elements.renameConversationError.hidden = true;
  setRenameConversationBusy(false);
  elements.renameConversationInput.focus();
  elements.renameConversationInput.select();
}

function closeRenameConversationDialog(restoreFocus = true) {
  if (state.renameDialog.saving) return;
  state.renameDialog = {
    conversationId: null,
    draft: '',
    saving: false,
    error: '',
  };
  elements.renameConversationDialog.hidden = true;
  elements.renameConversationError.textContent = '';
  elements.renameConversationError.hidden = true;
  setRenameConversationBusy(false);
  if (restoreFocus) elements.conversationMenu.focus();
}

async function submitRenameConversation(event) {
  event.preventDefault();
  if (state.renameDialog.saving || !workspaceApi) return;
  const conversation = state.conversations.find(
    (item) => item && item.id === state.renameDialog.conversationId,
  );
  if (!conversation) {
    closeRenameConversationDialog();
    return;
  }

  state.renameDialog.draft = elements.renameConversationInput.value;
  const title = state.renameDialog.draft.trim();
  if (!title) {
    state.renameDialog.error = '请输入聊天名称。';
    elements.renameConversationError.textContent = state.renameDialog.error;
    elements.renameConversationError.hidden = false;
    elements.renameConversationInput.focus();
    return;
  }

  state.renameDialog.saving = true;
  state.renameDialog.error = '';
  elements.renameConversationError.textContent = '';
  elements.renameConversationError.hidden = true;
  setRenameConversationBusy(true);
  try {
    await workspaceApi.renameConversation(conversation.id, title);
    state.conversations = state.conversations.map((item) => (
      item && item.id === conversation.id ? { ...item, title } : item
    ));
    state.renameDialog.saving = false;
    closeRenameConversationDialog(false);
    render();
    try {
      await refreshConversationList();
    } catch {
      state.conversationNotice = '标题已保存；会话列表将在下次刷新时同步。';
    }
    render();
  } catch {
    state.renameDialog.saving = false;
    state.renameDialog.error = '重命名失败，请稍后重试。';
    elements.renameConversationError.textContent = state.renameDialog.error;
    elements.renameConversationError.hidden = false;
    setRenameConversationBusy(false);
    elements.renameConversationInput.focus();
    elements.renameConversationInput.select();
  }
}

function restoreDeletedConversation(conversation, originalIndex) {
  if (state.conversations.some((item) => item && item.id === conversation.id)) return;
  const insertionIndex = Math.max(0, Math.min(originalIndex, state.conversations.length));
  state.conversations = [
    ...state.conversations.slice(0, insertionIndex),
    conversation,
    ...state.conversations.slice(insertionIndex),
  ];
}

async function deleteActiveConversation() {
  const conversation = state.conversations.find((item) => item && item.id === state.menuId);
  if (!conversation || !workspaceApi || state.deletingConversationIds.has(conversation.id)) return;
  closeConversationMenu();
  if (!window.confirm(`删除“${conversation.title || '新对话'}”？此操作无法撤销。`)) return;
  const originalIndex = state.conversations.findIndex((item) => item && item.id === conversation.id);
  const deletingActiveConversation = conversation.id === state.activeId;
  const nextConversation = state.conversations.find((item) => item && item.id !== conversation.id) || null;
  const previousActiveState = {
    activeId: state.activeId,
    messages: state.messages,
    modelSettings: state.modelSettings,
  };
  const deleteTransition = deletingActiveConversation
    ? conversationOperations.beginActiveConversationDeletion(conversationSelectionEpoch, nextConversation)
    : null;
  const fallbackSelectionEpoch = deleteTransition && deleteTransition.fallbackSelectionEpoch;
  state.conversationListRevision += 1;
  state.deletingConversationIds.add(conversation.id);
  state.conversations = state.conversations.filter((item) => item && item.id !== conversation.id);
  state.conversationNotice = '正在删除对话…';
  if (deletingActiveConversation) {
    conversationSelectionEpoch = fallbackSelectionEpoch;
    state.activeId = deleteTransition.activeId;
    state.messages = [];
    state.modelSettings = [];
    state.loadingConversation = deleteTransition.loading;
  }
  render();
  if (deletingActiveConversation && nextConversation) {
    selectConversation(nextConversation, fallbackSelectionEpoch).catch(() => {});
  }
  try {
    await workspaceApi.deleteConversation(conversation.id);
  } catch {
    restoreDeletedConversation(conversation, originalIndex);
    if (deletingActiveConversation && conversationOperations.canRestoreDeletedActiveConversation(
      conversationSelectionEpoch, fallbackSelectionEpoch,
    )) {
      ++conversationSelectionEpoch;
      state.activeId = previousActiveState.activeId;
      state.messages = previousActiveState.messages;
      state.modelSettings = previousActiveState.modelSettings;
      state.loadingConversation = false;
    }
    state.conversationNotice = '删除对话失败，已恢复到列表。';
  } finally {
    state.deletingConversationIds.delete(conversation.id);
    if (state.conversationNotice === '正在删除对话…') state.conversationNotice = '';
    render();
  }
}

function renderLoading() {
  setText(elements.connectionLabel, state.phase === 'authenticating' ? '等待登录' : '连接中');
  elements.connectionChip.className = 'connection-chip';
  elements.phaseBadge.className = 'phase-badge';
  setText(elements.phaseBadge, state.phase === 'authenticating' ? '登录窗口已打开' : '连接中');
  setText(elements.statusTitle, state.phase === 'authenticating' ? '请在登录窗口完成验证' : '正在建立安全会话');
  setText(
    elements.statusMessage,
    state.phase === 'authenticating'
      ? '登录完成后，桌面工作台会自动刷新。请勿在主工作台输入账户凭据。'
      : '正在读取本地桌面边界并验证云端登录状态。',
  );
  setText(elements.timelineTitle, '正在连接 AstraRelay 云端');
  setText(elements.timelineSummary, '安全加载账户、会话和可用协作模型。凭据保存在隔离且加密的 Electron 持久会话中，不会暴露给工作台页面。');
  setText(elements.accountName, state.phase === 'authenticating' ? '等待登录' : '正在连接');
  setText(elements.accountStatus, '检查云端会话');
  setText(elements.accountAvatar, 'A');
  setText(elements.cloudMiniStatus, '连接中');
  elements.cloudMiniStatus.className = 'mini-status';
  setText(elements.environmentAccount, '待确认');
  setText(elements.environmentConversations, '待加载');
  setText(elements.environmentModels, '待加载');
  setCheckpoint(elements.sessionCheckpoint, elements.sessionCheckpointState, '', '检查中');
  setText(elements.sessionCheckpointCopy, '正在检查登录状态。');
  setCheckpoint(elements.workspaceCheckpoint, elements.workspaceCheckpointState, '', '等待');
  elements.primaryAction.hidden = true;
  elements.retryButton.hidden = true;
  elements.loginButton.hidden = true;
}

function renderSignedOut() {
  elements.connectionChip.className = 'connection-chip';
  setText(elements.connectionLabel, '需要登录');
  elements.phaseBadge.className = 'phase-badge is-warning';
  setText(elements.phaseBadge, '未登录');
  setText(elements.statusTitle, '连接你的 AstraRelay 账户');
  setText(elements.statusMessage, '登录将在独立安全窗口中完成。桌面渲染器不会读取密码、Cookie 或私密邮箱。');
  setText(elements.timelineTitle, '登录后恢复你的云端工作台');
  setText(elements.timelineSummary, '最近会话和可调用模型摘要会在验证成功后自动出现。');
  setText(elements.accountName, '未登录');
  setText(elements.accountStatus, '连接云端账户');
  setText(elements.accountAvatar, 'A');
  setText(elements.cloudMiniStatus, '未登录');
  elements.cloudMiniStatus.className = 'mini-status';
  setText(elements.environmentAccount, '未登录');
  setText(elements.environmentConversations, '未加载');
  setText(elements.environmentModels, '未加载');
  setCheckpoint(elements.sessionCheckpoint, elements.sessionCheckpointState, 'is-warning', '需要登录');
  setText(elements.sessionCheckpointCopy, '尚未检测到有效的云端账户。');
  setCheckpoint(elements.workspaceCheckpoint, elements.workspaceCheckpointState, '', '等待');
  elements.primaryAction.hidden = false;
  elements.retryButton.hidden = true;
  elements.loginButton.hidden = false;
}

function renderReady() {
  const username = state.session && typeof state.session.display_name === 'string'
    ? state.session.display_name
    : '已登录用户';
  const conversationCount = Array.isArray(state.conversations) ? state.conversations.length : 0;
  const modelCount = callableModelCount(state.catalog);

  elements.connectionChip.className = 'connection-chip is-ready';
  setText(elements.connectionLabel, '云端已连接');
  elements.phaseBadge.className = 'phase-badge is-ready';
  setText(elements.phaseBadge, '已就绪');
  setText(elements.statusTitle, '安全工作台已准备就绪');
  setText(elements.statusMessage, `已恢复 ${conversationCount} 个云端会话，并确认 ${modelCount} 个可调用协作模型。`);
  setText(elements.timelineTitle, `欢迎回来，${username}`);
  setText(elements.timelineSummary, '账户与云端摘要已恢复。当前检查点不会读取本地文件，也不会执行终端命令。');
  setText(elements.accountName, username);
  setText(elements.accountStatus, '云端账户已连接');
  setText(elements.accountAvatar, username.trim().slice(0, 1).toUpperCase() || 'A');
  setText(elements.cloudMiniStatus, '已连接');
  elements.cloudMiniStatus.className = 'mini-status is-ready';
  setText(elements.environmentAccount, username);
  setText(elements.environmentConversations, `${conversationCount} 个`);
  setText(elements.environmentModels, `${modelCount} 个可调用`);
  setCheckpoint(elements.sessionCheckpoint, elements.sessionCheckpointState, 'is-ready', '已验证');
  setText(elements.sessionCheckpointCopy, `已连接账户 ${username}。`);
  setCheckpoint(elements.workspaceCheckpoint, elements.workspaceCheckpointState, 'is-ready', '已加载');
  setText(elements.workspaceCheckpointCopy, `已加载 ${conversationCount} 个会话和 ${modelCount} 个协作模型摘要。`);
  elements.primaryAction.hidden = true;
  elements.retryButton.hidden = true;
  elements.loginButton.hidden = true;
}

function renderOffline() {
  elements.connectionChip.className = 'connection-chip is-offline';
  setText(elements.connectionLabel, '连接失败');
  elements.phaseBadge.className = 'phase-badge is-offline';
  setText(elements.phaseBadge, '离线');
  setText(elements.statusTitle, '暂时无法连接 AstraRelay 云端');
  setText(elements.statusMessage, '请确认本地 Flask 服务或 HTTPS 云端地址可用，然后重新连接。');
  setText(elements.timelineTitle, '桌面安全壳已启动');
  setText(elements.timelineSummary, '本地界面可以正常运行，但云端账户和会话摘要尚未加载。');
  setText(elements.accountName, '连接失败');
  setText(elements.accountStatus, '云端暂时不可用');
  setText(elements.accountAvatar, 'A');
  setText(elements.cloudMiniStatus, '离线');
  elements.cloudMiniStatus.className = 'mini-status is-offline';
  setText(elements.environmentAccount, '不可用');
  setText(elements.environmentConversations, '未加载');
  setText(elements.environmentModels, '未加载');
  setCheckpoint(elements.sessionCheckpoint, elements.sessionCheckpointState, 'is-warning', '连接失败');
  setText(elements.sessionCheckpointCopy, '未能确认当前登录状态。');
  setCheckpoint(elements.workspaceCheckpoint, elements.workspaceCheckpointState, '', '等待');
  elements.primaryAction.hidden = true;
  elements.retryButton.hidden = false;
  elements.loginButton.hidden = true;
}

function render() {
  if (state.phase === 'ready') renderReady();
  else if (state.phase === 'signed-out') renderSignedOut();
  else if (state.phase === 'offline') renderOffline();
  else renderLoading();
  renderActiveView();
  renderConversationList();
  renderWorkspace();
  renderSearchView();
  renderModelPlazaView();
  renderScheduleView();
  renderProject();
  renderInspector();
}

async function refreshBootstrap() {
  if (conversationMutationInProgress()) return;
  invalidateSearchState();
  const currentEpoch = ++refreshEpoch;
  const listRevision = state.conversationListRevision;
  state.phase = 'loading';
  render();
  try {
    if (!window.astraDesktop || !window.astraDesktop.cloud) throw new Error('desktop bridge unavailable');
    const result = await window.astraDesktop.cloud.bootstrap();
    if (currentEpoch !== refreshEpoch) return;
    if (!result || !result.authenticated) {
      state.phase = 'signed-out';
      state.session = null;
      if (listRevision === state.conversationListRevision) state.conversations = [];
      state.catalog = { providers: [] };
      state.defaultConversationRequested = false;
    } else {
      state.phase = 'ready';
      state.session = result.session;
      if (listRevision === state.conversationListRevision) {
        state.conversations = Array.isArray(result.conversations) ? result.conversations : [];
      }
      state.catalog = result.catalog || { providers: [] };
    }
  } catch {
    if (currentEpoch !== refreshEpoch) return;
    state.phase = 'offline';
    state.session = null;
    if (listRevision === state.conversationListRevision) state.conversations = [];
    state.catalog = { providers: [] };
  }
  render();
  ensureDefaultConversation().catch(() => {});
}

async function openLogin() {
  state.phase = 'authenticating';
  render();
  try {
    await window.astraDesktop.session.login();
  } catch {
    state.phase = 'offline';
    invalidateSearchState();
    render();
  }
}

function overlayPanel(kind) {
  return kind === 'sidebar' ? elements.globalSidebar : document.getElementById('environment-inspector');
}

function setPanelAvailability(panel, available) {
  panel.inert = !available;
  panel.setAttribute('aria-hidden', available ? 'false' : 'true');
}

function syncResponsivePanels() {
  if (activeOverlay === 'sidebar' && sidebarMedia.matches) closeOverlays(false);
  if (activeOverlay === 'inspector' && inspectorMedia.matches) closeOverlays(false);
  if (activeOverlay) return;

  setPanelAvailability(elements.globalSidebar, sidebarMedia.matches);
  setPanelAvailability(document.getElementById('environment-inspector'), inspectorMedia.matches);
  elements.workspaceMain.inert = false;
}

function openOverlay(kind, trigger) {
  if ((kind === 'sidebar' && sidebarMedia.matches)
    || (kind === 'inspector' && inspectorMedia.matches)) return;

  activeOverlay = kind;
  overlayTrigger = trigger;
  document.documentElement.classList.toggle('sidebar-visible', kind === 'sidebar');
  document.documentElement.classList.toggle('inspector-visible', kind === 'inspector');

  const panel = overlayPanel(kind);
  const otherPanel = overlayPanel(kind === 'sidebar' ? 'inspector' : 'sidebar');
  setPanelAvailability(panel, true);
  setPanelAvailability(otherPanel, false);
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  elements.workspaceMain.inert = true;
  const closeButton = kind === 'sidebar' ? elements.sidebarClose : elements.inspectorClose;
  closeButton.focus();
}

function closeOverlays(restoreFocus = true) {
  const previousTrigger = overlayTrigger;
  document.documentElement.classList.remove('sidebar-visible', 'inspector-visible');
  for (const panel of [elements.globalSidebar, document.getElementById('environment-inspector')]) {
    panel.removeAttribute('role');
    panel.removeAttribute('aria-modal');
  }
  activeOverlay = null;
  overlayTrigger = null;
  syncResponsivePanels();
  if (restoreFocus && previousTrigger) previousTrigger.focus();
}

function trapOverlayFocus(event) {
  if (!activeOverlay || event.key !== 'Tab') return;
  const panel = overlayPanel(activeOverlay);
  const focusable = Array.from(panel.querySelectorAll(
    'button:not(:disabled), [href], input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
  ));
  if (focusable.length === 0) {
    event.preventDefault();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

elements.primaryAction.addEventListener('click', openLogin);
elements.loginButton.addEventListener('click', openLogin);
elements.projectOpen.addEventListener('click', () => {
  selectActiveView('project');
  openProject().catch(renderProjectFailure);
});
elements.projectSave.addEventListener('click', () => { saveActiveTab().catch(renderProjectFailure); });
elements.projectSearch.addEventListener('input', () => { searchProject().catch(renderProjectFailure); });
elements.scheduleForm.addEventListener('submit', submitScheduleForm);
elements.searchForm.addEventListener('submit', submitSearchForm);
elements.searchQuery.addEventListener('input', () => {
  scheduleSearchFromInput();
});
elements.retryButton.addEventListener('click', refreshBootstrap);
elements.refreshButton.addEventListener('click', refreshBootstrap);
elements.newTask.addEventListener('click', () => {
  selectActiveView('workspace');
  createConversation().catch(() => {});
});
elements.workspaceComposer.addEventListener('submit', (event) => { submitWorkspaceMessage(event).catch(() => {}); });
elements.modelSettings.addEventListener('click', () => { elements.modelPopover.hidden = false; });
elements.modelPopoverClose.addEventListener('click', () => { elements.modelPopover.hidden = true; });
elements.modelSummaryOverflow.addEventListener('click', () => {
  if (elements.modelSummaryOverflowPanel.hidden) openModelSummaryOverflow();
  else closeModelSummaryOverflow();
});
elements.conversationMenu.addEventListener('click', (event) => {
  const anchor = event.currentTarget.getBoundingClientRect();
  openConversationMenu(state.activeId, anchor.right, anchor.bottom);
});
elements.conversationRename.addEventListener('click', () => {
  const conversation = state.conversations.find((item) => item && item.id === state.menuId);
  openRenameConversationDialog(conversation);
});
elements.conversationDelete.addEventListener('click', () => { deleteActiveConversation().catch(() => {}); });
elements.renameConversationForm.addEventListener('submit', (event) => { submitRenameConversation(event); });
elements.renameConversationInput.addEventListener('input', () => {
  state.renameDialog.draft = elements.renameConversationInput.value;
  if (state.renameDialog.error) {
    state.renameDialog.error = '';
    elements.renameConversationError.textContent = '';
    elements.renameConversationError.hidden = true;
  }
});
elements.renameConversationCancel.addEventListener('click', () => closeRenameConversationDialog());
elements.renameConversationClose.addEventListener('click', () => closeRenameConversationDialog());
elements.renameConversationDialog.addEventListener('click', (event) => {
  if (event.target === elements.renameConversationDialog) closeRenameConversationDialog();
});
elements.sidebarOpen.addEventListener('click', () => openOverlay('sidebar', elements.sidebarOpen));
elements.sidebarClose.addEventListener('click', closeOverlays);
elements.inspectorOpen.addEventListener('click', () => openOverlay('inspector', elements.inspectorOpen));
elements.inspectorClose.addEventListener('click', closeOverlays);
elements.scrim.addEventListener('click', closeOverlays);
for (const button of elements.viewButtons) {
  button.addEventListener('click', () => {
    selectActiveView(button.dataset.view);
    if (activeOverlay === 'sidebar') closeOverlays();
  });
}
document.addEventListener('keydown', (event) => {
  trapOverlayFocus(event);
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    saveActiveTab().catch(renderProjectFailure);
  }
  if (event.key === 'Escape' && activeOverlay) closeOverlays();
  if (event.key === 'Escape' && !elements.renameConversationDialog.hidden) {
    closeRenameConversationDialog();
  }
  if (event.key === 'Escape') {
    closeConversationMenu();
    elements.modelPopover.hidden = true;
    closeModelSummaryOverflow();
  }
});
document.addEventListener('click', (event) => {
  if (!elements.conversationMenuPanel.hidden
    && !elements.conversationMenuPanel.contains(event.target)
    && event.target !== elements.conversationMenu) {
    closeConversationMenu();
  }
  if (!elements.modelSummaryOverflowPanel.hidden
    && !elements.modelSummaryOverflowPanel.contains(event.target)
    && event.target !== elements.modelSummaryOverflow) {
    closeModelSummaryOverflow();
  }
});
sidebarMedia.addEventListener('change', syncResponsivePanels);
inspectorMedia.addEventListener('change', syncResponsivePanels);

if (window.astraDesktop && window.astraDesktop.session) {
  window.astraDesktop.session.onChanged(() => refreshBootstrap());
}
if (workspaceApi && workspaceApi.onStreamEvent) {
  workspaceApi.onStreamEvent(applyStreamEvent);
}

syncResponsivePanels();
render();
refreshProjectStatus();
refreshBootstrap();
