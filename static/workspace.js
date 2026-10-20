const messages = document.querySelector('#messages');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const mentionMenu = document.querySelector('#mention-menu');
const providerSelect = document.querySelector('#provider-select');
const modelSelect = document.querySelector('#model-select');
const addModelButton = document.querySelector('#add-model');
const agentStatusList = document.querySelector('#agent-status-list');
const taskProgress = document.querySelector('#task-progress');
const workspaceTaskMode = document.querySelector('#workspace-task-mode');
const appShell = document.querySelector('#app-shell');
const mobileAgentToggle = document.querySelector('#mobile-agent-toggle');
const mobileSessionToggle = document.querySelector('#mobile-session-toggle');
const sessionSidebarClose = document.querySelector('#session-sidebar-close');
const sessionBackdrop = document.querySelector('#session-backdrop');
const newSessionButton = document.querySelector('#new-session');
const conversationList = document.querySelector('#conversation-list');
const workspaceCsrfToken = document.querySelector('#workspace-csrf-token');
const modelDrawer = document.querySelector('#model-drawer');
const modelDrawerToggle = document.querySelector('#model-drawer-toggle');
const modelDrawerClose = document.querySelector('#model-drawer-close');
const modelDrawerBackdrop = document.querySelector('#model-drawer-backdrop');
const composerToolsToggle = document.querySelector('#composer-tools-toggle');
const workspaceAttachmentInput = document.querySelector('#workspace-attachment-input');
const workspaceAttachmentList = document.querySelector('#workspace-attachment-list');
const workspaceAttachmentStatus = document.querySelector('#workspace-attachment-status');
const taskComposer = document.querySelector('#task-composer');
const chatPanel = document.querySelector('.chat-panel');
const workspaceEmptyState = document.querySelector('#workspace-empty-state');
const selectedModelList = document.querySelector('#selected-model-list');
const modelTree = document.querySelector('#model-tree');
const modelSearch = document.querySelector('#model-search');
const contextMode = document.querySelector('#context-mode');
const noModelNotice = document.querySelector('#no-model-notice');
const stopGenerationButton = document.querySelector('#stop-generation');
const collaborationGroups = document.querySelector('#collaboration-groups');
const capabilityDialog = document.querySelector('#capability-dialog');
const capabilityDialogMessage = document.querySelector('#capability-dialog-message');
const capabilityRecommendations = document.querySelector('#capability-recommendations');
const capabilityDialogCancel = document.querySelector('#capability-dialog-cancel');
const capabilityDialogContinue = document.querySelector('#capability-dialog-continue');
const workspaceCatalogStatus = document.querySelector('#workspace-catalog-status');
const workspaceCatalogMessage = document.querySelector('#workspace-catalog-message');
const workspaceCatalogDetail = document.querySelector('#workspace-catalog-detail');
const workspaceCatalogRetry = document.querySelector('#workspace-catalog-retry');
const selectedModels = new Set();
const currentTaskModels = new Set();
const terminalStates = new Set(['completed', 'failed', 'timed_out']);
let modelCatalog = [];
let catalogAvailability = 'unavailable';
let catalogLoaded = false;
let requestedCatalogModelHandled = false;
const catalogModelsById = new Map();
const actionableModelIds = new Set();
let selectedDynamicModelId = null;
const agentStatusItems = new Map();
const models = [
  { id: 'deepseek', name: 'DeepSeek', status: '已接入' },
  { id: 'kimi', name: 'Kimi', status: '已接入' },
  { id: 'zhipu', name: 'GLM', status: '已接入' },
  { id: 'qwen', name: 'Qwen', status: '已接入' },
  { id: 'bytedance', name: '豆包', status: '已接入' },
  { id: 'qwen_official', name: 'Qwen', status: '已接入' },
];
const modelPresentation = {
  deepseek: { name: 'DeepSeek' },
  kimi: { name: 'Kimi' },
  zhipu: { name: 'GLM' },
  qwen: { name: 'Qwen' },
  bytedance: { name: '豆包' },
  qwen_official: { name: 'Qwen' },
};
const stateLabels = {
  idle: '待命',
  selected: '已选择',
  queued: '已排队',
  thinking: '思考中',
  completed: '已完成',
  timed_out: '已超时',
  failed: '失败',
};
const taskModeLabels = {
  chat: '文字对话',
  image: '图像任务',
  video: '视频任务',
  embedding: '向量任务',
  audio: '音频任务',
  reranker: '检索任务',
};
let selectedModel = 0;
let dismissedMention = null;
let currentConversationId = null;
let conversations = [];
let activeWorkspaceAbortController = null;
let workspaceStopRequested = false;
let selectionAttemptVersion = 0;
let conversationActivationEpoch = 0;
let workspaceStreamVersion = 0;
const workspaceAttachments = new Map();
const conversationSnapshotCache = new Map();
const MAX_CONVERSATION_SNAPSHOTS = 12;
let conversationDeletionQueue = Promise.resolve();
const pendingConversationDeletions = new Map();
let pendingCapabilitySubmission = null;
let pendingDraftConversationCreation = null;

function cacheConversationSnapshot(conversationId, snapshot) {
  conversationSnapshotCache.delete(conversationId);
  conversationSnapshotCache.set(conversationId, snapshot);
  while (conversationSnapshotCache.size > MAX_CONVERSATION_SNAPSHOTS) {
    conversationSnapshotCache.delete(conversationSnapshotCache.keys().next().value);
  }
}

async function readWorkspaceJson(response, endpoint) {
  const contentType = response.headers?.get?.('content-type') || '';
  if (!contentType.toLowerCase().includes('application/json')) {
    throw new Error(`${endpoint} 会话模型接口返回了非 JSON 响应（HTTP ${response.status || '未知'}）。请刷新页面；若问题仍存在，请重启工作台服务。`);
  }
  return response.json();
}

function normalizeWorkspaceCatalogAvailability(catalog) {
  return catalog?.availability === 'fresh' || catalog?.availability === 'degraded'
    ? catalog.availability
    : 'unavailable';
}

function safeWorkspaceCatalogMessage(catalog, availability) {
  if (
    catalog?.availability === availability
    && typeof catalog?.message === 'string'
    && catalog.message.trim()
  ) return catalog.message.trim();
  if (availability === 'degraded') return '模型目录暂时无法更新，当前仅展示最近一次可用信息';
  return '模型目录暂不可用，请稍后重试';
}

function formattedLastCatalogSuccess(value) {
  if (typeof value !== 'string' || !value.trim()) return '';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return '';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(timestamp));
}

function renderWorkspaceCatalogAvailability(catalog) {
  const availability = normalizeWorkspaceCatalogAvailability(catalog);
  if (!workspaceCatalogStatus || !workspaceCatalogMessage || !workspaceCatalogDetail || !workspaceCatalogRetry) {
    return availability;
  }
  workspaceCatalogStatus.hidden = availability === 'fresh';
  workspaceCatalogStatus.dataset.state = availability;
  workspaceCatalogMessage.textContent = availability === 'fresh'
    ? ''
    : safeWorkspaceCatalogMessage(catalog, availability);
  const lastSuccess = availability === 'fresh' ? '' : formattedLastCatalogSuccess(catalog?.last_success_at);
  const availabilityDetail = availability === 'degraded'
    ? '当前仅可选择状态正常的模型。'
    : availability === 'unavailable'
      ? '目录恢复前不能选择或发送模型任务。'
      : '';
  workspaceCatalogDetail.textContent = [
    availabilityDetail,
    lastSuccess ? `最近成功更新：${lastSuccess}` : '',
  ].filter(Boolean).join(' ');
  workspaceCatalogRetry.hidden = availability === 'fresh' || catalog?.retryable !== true;
  return availability;
}

function rebuildCatalogModelSets(catalog, availability) {
  actionableModelIds.clear();
  if (availability !== 'unavailable') catalogModelsById.clear();
  (catalog?.providers || []).forEach((provider) => {
    Object.entries(provider.capabilities || {}).forEach(([capability, entries]) => {
      (entries || []).forEach((model) => {
        if (typeof model?.platform_model_id !== 'string') return;
        if (availability !== 'unavailable') {
          catalogModelsById.set(model.platform_model_id, model);
        }
        if (
          availability !== 'unavailable'
          && capability === 'chat'
          && model.selectable === true
          && model.callable === true
        ) actionableModelIds.add(model.platform_model_id);
      });
    });
  });
}

function actionableModelSettings(settings) {
  return Array.isArray(settings)
    ? settings.filter((setting) => actionableModelIds.has(setting?.model_id))
    : [];
}

function unavailableModelSettings(settings) {
  return Array.isArray(settings)
    ? settings.filter((setting) => !actionableModelIds.has(setting?.model_id))
    : [];
}

const workspaceModelStore = {
  settings: [],
  apply(settings) {
    this.settings = settings;
    renderSelectedWorkspaceModels();
    return this.settings;
  },
  async load(conversationId, applySettings = true) {
    const response = await fetch(`/api/conversations/${conversationId}/models`, { credentials: 'same-origin' });
    const payload = await readWorkspaceJson(response, `/api/conversations/${conversationId}/models`);
    if (!response.ok) throw new Error(payload.error || '无法加载会话模型');
    return applySettings ? this.apply(payload.models) : payload.models;
  },
  async replace(next) {
    if (
      currentConversationId
      && catalogLoaded
      && unavailableModelSettings(this.settings).length > 0
    ) {
      throw new Error('存在暂不可用的历史模型，请在目录恢复后再修改模型组合。');
    }
    const safeNext = actionableModelSettings(next);
    if (!currentConversationId) {
      this.settings = safeNext;
      renderSelectedWorkspaceModels();
      return this.settings;
    }
    const conversationId = currentConversationId;
    const activationEpoch = conversationActivationEpoch;
    const isCurrentActivation = () => conversationId === currentConversationId && activationEpoch === conversationActivationEpoch;
    try {
      const response = await fetch(`/api/conversations/${conversationId}/models`, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': workspaceCsrfToken?.value || '' },
        body: JSON.stringify({ models: safeNext }),
      });
      let payload;
      try {
        payload = await readWorkspaceJson(response, `/api/conversations/${conversationId}/models`);
      } catch (error) {
        if (!isCurrentActivation()) return this.settings;
        throw error;
      }
      if (response.ok) conversationSnapshotCache.delete(conversationId);
      if (!isCurrentActivation()) return this.settings;
      if (!response.ok) throw new Error(payload.error || '无法保存会话模型');
      return this.apply(payload.models);
    } catch (error) {
      if (!isCurrentActivation()) return this.settings;
      throw error;
    }
  },
  actionableSettings() { return actionableModelSettings(this.settings); },
  selectedIds() { return this.actionableSettings().map(({ model_id }) => model_id); },
};

function modelDisplayName(modelId) {
  const catalogModel = catalogModelsById.get(modelId);
  if (catalogModel) return catalogModel.display_name;
  return modelPresentation[modelId]?.name || 'AI';
}

function findCatalogModel(modelId) {
  return catalogModelsById.get(modelId) || null;
}

function modelCapabilityLabel(model) {
  if (!model) return '';
  return model.capability_note || (model.capability_status === 'pending' ? '注：能力信息待确认' : '');
}

function requiredCapabilityForAttachments(attachments) {
  return (attachments || []).some((attachment) => String(attachment.media_type || '').toLowerCase().startsWith('image/'))
    ? 'vision'
    : 'text_chat';
}

function settingsSupportCapability(settings, capability) {
  const actionableSettings = actionableModelSettings(settings);
  if (capability === 'text_chat' && !actionableSettings.length) return false;
  return actionableSettings.some((setting) => {
    const model = findCatalogModel(setting.model_id);
    if (!model) return capability === 'text_chat';
    return (model.supports || []).includes(capability);
  });
}

function recommendHelperModels(requirement, limit = 3) {
  const selectedIds = new Set(workspaceModelStore.selectedIds());
  return modelCatalog
    .flatMap((provider) => provider.models)
    .filter((model) => (
      model.callable === true
      && model.selectable === true
      && model.capability_status === 'verified'
      && (model.supports || []).includes(requirement)
      && !selectedIds.has(model.platform_model_id)
    ))
    .slice(0, limit);
}

function settleCapabilityDialog(model = null) {
  if (!pendingCapabilitySubmission) return;
  const { resolve } = pendingCapabilitySubmission;
  pendingCapabilitySubmission = null;
  if (capabilityDialog?.open) capabilityDialog.close();
  resolve(model);
}

function openCapabilityDialog(requirement) {
  if (!capabilityDialog || typeof capabilityDialog.showModal !== 'function') {
    setModelDrawerOpen(true);
    return Promise.resolve(null);
  }
  if (pendingCapabilitySubmission) settleCapabilityDialog(null);

  const recommendations = recommendHelperModels(requirement);
  const requirementName = requirement === 'vision' ? '图片理解' : requirement;
  capabilityDialogMessage.textContent = `当前已选模型不支持${requirementName}。请选择一个辅助模型；原有模型会继续保留。`;
  capabilityRecommendations.replaceChildren();
  recommendations.forEach((model, index) => {
    const label = document.createElement('label');
    const radio = document.createElement('input');
    const copy = document.createElement('span');
    radio.type = 'radio';
    radio.name = 'capability-helper-model';
    radio.value = model.platform_model_id;
    radio.checked = index === 0;
    copy.textContent = `${model.display_name}${modelCapabilityLabel(model) ? `（${modelCapabilityLabel(model)}）` : ''}`;
    label.append(radio, copy);
    capabilityRecommendations.append(label);
  });
  if (!recommendations.length) {
    const empty = document.createElement('p');
    empty.textContent = '暂无已验证的兼容模型，请在模型抽屉中手动选择。';
    capabilityRecommendations.append(empty);
  }
  capabilityDialogContinue.disabled = recommendations.length === 0;
  capabilityDialog.showModal();
  return new Promise((resolve) => {
    pendingCapabilitySubmission = { resolve, requirement, recommendations };
  });
}

function modelPresentationFor(modelId) {
  if (modelPresentation[modelId]) return modelPresentation[modelId];
  for (const provider of modelCatalog) {
    const match = provider.models.find((model) => model.platform_model_id === modelId);
    if (match) {
      return { name: match.display_name };
    }
  }
  return { name: 'AI' };
}

function setModelDrawerOpen(isOpen) {
  if (!modelDrawer) return;
  modelDrawer.classList.toggle('is-open', isOpen);
  modelDrawer.setAttribute('aria-hidden', String(!isOpen));
  modelDrawerToggle?.setAttribute('aria-expanded', String(isOpen));
  mobileAgentToggle?.setAttribute('aria-expanded', String(isOpen));
  if (modelDrawerBackdrop) modelDrawerBackdrop.hidden = !isOpen;
}

function setSessionDrawerOpen(isOpen) {
  if (!appShell) return;
  const isDesktop = globalThis.matchMedia?.('(min-width: 1024px)').matches === true;
  appShell.classList.toggle('session-panel-open', !isDesktop && isOpen);
  appShell.classList.toggle('session-panel-collapsed', isDesktop && !isOpen);
  if (sessionBackdrop) sessionBackdrop.hidden = isDesktop || !isOpen;
  mobileSessionToggle?.setAttribute('aria-expanded', String(isOpen));
  mobileSessionToggle?.setAttribute('aria-label', isOpen ? '关闭会话侧栏' : '打开会话侧栏');
}

function pendingAttachmentTokens() {
  return [...workspaceAttachments.values()].map((attachment) => attachment.token);
}

function setWorkspaceAttachmentStatus(message = '') {
  if (!workspaceAttachmentStatus) return;
  workspaceAttachmentStatus.textContent = message;
  workspaceAttachmentStatus.hidden = !message;
}

function formatAttachmentSize(size) {
  if (!Number.isFinite(size) || size < 1024) return `${size || 0} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function renderWorkspaceAttachments() {
  if (!workspaceAttachmentList) return;
  workspaceAttachmentList.replaceChildren();
  for (const attachment of workspaceAttachments.values()) {
    const chip = document.createElement('div');
    const name = document.createElement('span');
    const remove = document.createElement('button');
    chip.className = 'workspace-attachment-chip';
    name.className = 'workspace-attachment-chip-name';
    name.title = attachment.name;
    name.textContent = `${attachment.name} · ${formatAttachmentSize(attachment.size)}`;
    remove.type = 'button';
    remove.setAttribute('aria-label', `移除附件 ${attachment.name}`);
    remove.textContent = '×';
    remove.addEventListener('click', () => {
      workspaceAttachments.delete(attachment.token);
      renderWorkspaceAttachments();
      setWorkspaceAttachmentStatus('');
    });
    chip.append(name, remove);
    workspaceAttachmentList.append(chip);
  }
}

function clearWorkspaceAttachments() {
  workspaceAttachments.clear();
  renderWorkspaceAttachments();
  setWorkspaceAttachmentStatus('');
}

async function uploadWorkspaceAttachments(fileList) {
  const files = [...(fileList || [])];
  if (!files.length) return;
  await ensureConversation();
  const conversationId = currentConversationId;
  const activationEpoch = conversationActivationEpoch;
  const isCurrentActivation = () => conversationId === currentConversationId && activationEpoch === conversationActivationEpoch;
  const body = new FormData();
  body.append('conversation_id', conversationId);
  files.forEach((file) => body.append('files', file));
  setWorkspaceAttachmentStatus(`正在添加 ${files.length} 个附件…`);
  try {
    const response = await fetch('/api/workspace/attachments', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': workspaceCsrfToken?.value || '' },
      body,
    });
    let payload;
    try {
      payload = await readWorkspaceJson(response, '/api/workspace/attachments');
    } catch (error) {
      if (!isCurrentActivation()) return;
      throw error;
    }
    if (!isCurrentActivation()) return;
    if (!response.ok) throw new Error(payload.error || '附件添加失败');
    for (const attachment of payload.attachments || []) {
      workspaceAttachments.set(attachment.token, attachment);
    }
    renderWorkspaceAttachments();
    setWorkspaceAttachmentStatus(`已添加 ${payload.attachments?.length || 0} 个附件`);
  } catch (error) {
    if (!isCurrentActivation()) return;
    throw error;
  }
}

function setWorkspaceEmptyState(isEmpty) {
  chatPanel?.classList.toggle('is-empty', isEmpty);
  if (workspaceEmptyState) workspaceEmptyState.hidden = !isEmpty;
}

function renderSelectedWorkspaceModels() {
  if (!selectedModelList) return;
  selectedModelList.replaceChildren();
  workspaceModelStore.settings.forEach((setting) => {
    const item = document.createElement('li');
    const name = document.createElement('span');
    const mode = document.createElement('select');
    const remove = document.createElement('button');
    const catalogModel = findCatalogModel(setting.model_id);
    const isUnavailable = !actionableModelIds.has(setting.model_id);
    const note = modelCapabilityLabel(catalogModel);
    name.textContent = `${modelDisplayName(setting.model_id)}${note ? `（${note}）` : ''}`;
    if (isUnavailable) name.textContent += '（暂不可用）';
    name.title = name.textContent;
    mode.setAttribute('aria-label', `${name.textContent} 上下文`);
    ['smart', 'full', 'from_now', 'custom'].forEach((value) => {
      const option = document.createElement('option'); option.value = value;
      option.textContent = { smart: '智能摘要', full: '完整', from_now: '从现在开始', custom: '自选' }[value];
      mode.append(option);
    });
    mode.value = setting.context_mode;
    mode.disabled = isUnavailable;
    mode.addEventListener('change', () => workspaceModelStore.replace(
      workspaceModelStore.settings.map((item) => item.model_id === setting.model_id ? { ...item, context_mode: mode.value } : item)
    ).catch((error) => addBubble('system', error.message)));
    remove.type = 'button'; remove.textContent = '移除';
    remove.disabled = isUnavailable;
    remove.addEventListener('click', () => workspaceModelStore.replace(
      workspaceModelStore.settings.filter((item) => item.model_id !== setting.model_id)
    ).catch((error) => addBubble('system', error.message)));
    if (isUnavailable) item.classList.add('is-unavailable');
    item.append(name, mode, remove); selectedModelList.append(item);
  });
}

function addWorkspaceModel(modelId) {
  if (!actionableModelIds.has(modelId)) return;
  if (workspaceModelStore.selectedIds().includes(modelId)) return;
  const mode = contextMode?.value || 'smart';
  workspaceModelStore.replace([...workspaceModelStore.settings, { model_id: modelId, context_mode: mode, message_ids: [] }])
    .catch((error) => addBubble('system', error.message));
}

function renderModelTree() {
  if (!modelTree) return;
  const query = modelSearch?.value.trim().toLowerCase() || '';
  modelTree.replaceChildren();
  modelCatalog.forEach((provider) => {
    const matches = provider.models.filter((model) => `${provider.name} ${model.display_name}`.toLowerCase().includes(query));
    if (!matches.length) return;
    const details = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = provider.name; details.append(summary);
    matches.forEach((model) => {
      const button = document.createElement('button');
      const name = document.createElement('span');
      const note = document.createElement('small');
      button.type = 'button';
      name.className = 'model-tree-model-name';
      note.className = 'model-capability-note';
      name.textContent = model.display_name;
      note.textContent = modelCapabilityLabel(model);
      button.append(name, note);
      button.addEventListener('click', () => addWorkspaceModel(model.platform_model_id)); details.append(button);
    });
    modelTree.append(details);
  });
}

function createModelIdentity(presentation, className) {
  const identity = document.createElement('span');
  identity.className = className;
  identity.textContent = presentation.name || presentation.display_name || 'AI';
  return identity;
}

function renderAgentStatus(model, state) {
  const presentation = modelPresentationFor(model);
  const existing = agentStatusItems.get(model);
  const label = stateLabels[state] || state;

  if (!agentStatusList) return;

  const item = existing || document.createElement('li');
  if (!existing) {
    item.id = `agent-status-${model}`;
    item.className = 'agent-status-item';
    const avatar = createModelIdentity(presentation, 'agent-status-avatar');
    const name = document.createElement('span');
    const chip = document.createElement('span');
    name.className = 'agent-status-name';
    name.textContent = presentation.name;
    chip.className = 'status-chip';
    item.append(avatar, name, chip);
    agentStatusList.append(item);
    agentStatusItems.set(model, item);
  }
  item.setAttribute('data-state', state);
  const chip = item.querySelector('.status-chip');
  if (chip) chip.textContent = label;
}

function setAgentThinking(model, isThinking) {
  const statusItem = agentStatusItems.get(model);
  if (statusItem) statusItem.setAttribute('aria-busy', String(isThinking));
}

function updateTaskProgress(task) {
  if (!taskProgress) return;
  const results = task.models || {};
  const targetModels = Object.keys(results);
  const targetCount = targetModels.length;
  const terminalCount = targetModels.filter((model) => terminalStates.has(results[model].state)).length;
  taskProgress.hidden = targetCount === 0;
  taskProgress.textContent = targetCount ? `任务进度 ${terminalCount} / ${targetCount}` : '';
  return { terminalCount, targetCount };
}

function setWorkspaceTaskMode(capability) {
  if (!workspaceTaskMode) return;
  workspaceTaskMode.textContent = taskModeLabels[capability] || '协作任务';
}

globalThis.WorkspacePresentation = { updateTaskProgress, setWorkspaceTaskMode };

function chatProviders(catalog) {
  if (normalizeWorkspaceCatalogAvailability(catalog) === 'unavailable') return [];
  return (catalog?.providers || [])
    .filter((provider) => provider.status !== 'unavailable')
    .map((provider) => ({
      id: provider.key,
      name: provider.name,
      models: (provider.capabilities?.chat || []).filter((model) => (
        model.selectable === true && model.callable === true
      )),
    }))
    .filter((provider) => provider.models.length);
}

function providerModels(provider) {
  return modelCatalog.find((entry) => entry.id === provider)?.models || [];
}

function populateModels(provider) {
  if (!modelSelect) return;
  modelSelect.replaceChildren();
  const availableModels = providerModels(provider);
  availableModels.forEach((model) => {
    const option = document.createElement('option');
    option.value = model.platform_model_id;
    option.textContent = model.display_name;
    modelSelect.append(option);
  });
  const hasModels = availableModels.length > 0;
  modelSelect.disabled = !hasModels;
  if (addModelButton) addModelButton.disabled = !hasModels;
  modelSelect.value = hasModels ? availableModels[0].platform_model_id : '';
}

function ensureSelectableModel(modelId) {
  const existing = models.find((model) => model.id === modelId);
  if (existing) return existing;

  let catalogModel = null;
  modelCatalog.some((provider) => {
    catalogModel = provider.models.find((model) => model.platform_model_id === modelId) || null;
    return catalogModel !== null;
  });
  if (!catalogModel) return null;

  const model = {
    id: catalogModel.platform_model_id,
    name: catalogModel.display_name,
    status: '可调用',
    isDynamic: true,
    capability: catalogModel.capability,
    supports: catalogModel.supports || [],
    unsupported: catalogModel.unsupported || [],
    capability_status: catalogModel.capability_status,
    capability_note: catalogModel.capability_note,
  };
  models.push(model);
  modelPresentation[model.id] = { name: model.name };
  renderAgentStatus(model.id, 'idle');
  return model;
}

function clearDynamicModelSelection() {
  if (!selectedDynamicModelId) return;
  if (!currentTaskModels.has(selectedDynamicModelId)) {
    renderAgentStatus(selectedDynamicModelId, 'idle');
  }
  selectedDynamicModelId = null;
}

function selectDynamicModel(model) {
  if (selectedDynamicModelId !== model.id) clearDynamicModelSelection();
  selectedDynamicModelId = model.id;
  if (!currentTaskModels.has(model.id)) renderAgentStatus(model.id, 'selected');
  setWorkspaceTaskMode(model.capability || 'chat');
}

function addSelectedModel() {
  if (!modelSelect || !modelSelect.value) return;
  const model = ensureSelectableModel(modelSelect.value);
  if (model?.isDynamic) {
    selectDynamicModel(model);
    addWorkspaceModel(model.id);
  }
}

function loadCatalog(catalog) {
  catalogAvailability = normalizeWorkspaceCatalogAvailability(catalog);
  catalogLoaded = true;
  rebuildCatalogModelSets(catalog, catalogAvailability);
  renderWorkspaceCatalogAvailability(catalog);
  modelCatalog = chatProviders(catalog);
  if (selectedDynamicModelId && !actionableModelIds.has(selectedDynamicModelId)) {
    clearDynamicModelSelection();
  }
  renderSelectedWorkspaceModels();
  if (!providerSelect || !modelSelect || !addModelButton) return;
  providerSelect.replaceChildren();
  modelCatalog.forEach((provider) => {
    const option = document.createElement('option');
    option.value = provider.id;
    option.textContent = provider.name;
    providerSelect.append(option);
  });
  const hasProviders = modelCatalog.length > 0;
  providerSelect.disabled = !hasProviders;
  providerSelect.value = hasProviders ? modelCatalog[0].id : '';
  populateModels(providerSelect.value);
  renderModelTree();
  const requestedModelId = requestedCatalogModelHandled
    ? null
    : new URLSearchParams(globalThis.location?.search || '').get('model');
  const requestedProvider = modelCatalog.find((provider) => (
    provider.models.some((model) => model.platform_model_id === requestedModelId)
  ));
  if (requestedProvider) {
    requestedCatalogModelHandled = true;
    providerSelect.value = requestedProvider.id;
    populateModels(requestedProvider.id);
    modelSelect.value = requestedModelId;
    addSelectedModel();
  }
}

async function loadModelCatalog() {
  if (workspaceCatalogRetry) workspaceCatalogRetry.disabled = true;
  try {
    const response = await fetch('/api/models/catalog', { credentials: 'same-origin' });
    const catalog = await readWorkspaceJson(response, '/api/models/catalog');
    if (!response.ok) {
      loadCatalog({ availability: 'unavailable', retryable: true, providers: [] });
      return;
    }
    loadCatalog(catalog);
  } catch (_error) {
    loadCatalog({ availability: 'unavailable', retryable: true, providers: [] });
  } finally {
    if (workspaceCatalogRetry) workspaceCatalogRetry.disabled = false;
  }
}

if (providerSelect && modelSelect && addModelButton) {
  providerSelect.addEventListener('change', () => populateModels(providerSelect.value));
  addModelButton.addEventListener('click', addSelectedModel);
  workspaceCatalogRetry?.addEventListener('click', () => loadModelCatalog());
  loadModelCatalog();
}

if (globalThis.__WORKSPACE_TEST__) {
  globalThis.WorkspaceModelSelector = {
    addModelButton,
    addSelectedModel,
    actionableModelSettings,
    clearDynamicModelSelection,
    loadCatalog,
    modelSelect,
    populateModels,
    providerSelect,
    readWorkspaceJson,
    recommendHelperModels,
    requiredCapabilityForAttachments,
    selectedModels,
    settingsSupportCapability,
    unavailableModelSettings,
    get selectedDynamicModelId() { return selectedDynamicModelId; },
  };
}

function renderMentionMenu() {
  mentionMenu.replaceChildren();
  models.forEach((model, index) => {
    const option = document.createElement('button');
    const name = document.createElement('span');
    const status = document.createElement('span');
    option.type = 'button';
    option.id = `mention-option-${model.id}`;
    option.className = `mention-option${index === selectedModel ? ' is-selected' : ''}`;
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', String(index === selectedModel));
    name.textContent = model.name;
    status.className = 'mention-status';
    status.textContent = model.status;
    if (typeof option.append === 'function') option.append(name, status);
    option.addEventListener('click', () => insertMention(model));
    mentionMenu.append(option);
  });
  input.setAttribute('aria-activedescendant', `mention-option-${models[selectedModel].id}`);
}

function openMentionMenu() {
  selectedModel = 0;
  renderMentionMenu();
  mentionMenu.hidden = false;
  input.setAttribute('aria-expanded', 'true');
}

function closeMentionMenu() {
  mentionMenu.hidden = true;
  input.setAttribute('aria-expanded', 'false');
  input.removeAttribute('aria-activedescendant');
}

function getActiveMention() {
  const cursor = input.selectionStart;
  const beforeCursor = input.value.slice(0, cursor);
  const match = beforeCursor.match(/(?:^|\s)@[^\s@]*$/);
  if (!match) return null;
  const startsAfterWhitespace = match[0].startsWith(' ');
  const start = cursor - match[0].length + (startsAfterWhitespace ? 1 : 0);
  const suffix = input.value.slice(cursor).match(/^[^\s@]*/)[0];
  return { start, end: cursor + suffix.length, text: input.value.slice(start + 1, cursor + suffix.length) };
}

function insertMention(model) {
  const mention = getActiveMention();
  if (!mention) return;
  input.setRangeText(`@${model.name} `, mention.start, mention.end, 'end');
  dismissedMention = null;
  closeMentionMenu();
  input.focus();
}

input.addEventListener('input', () => {
  const activeMention = getActiveMention();
  if (!activeMention) {
    dismissedMention = null;
    closeMentionMenu();
  } else if (dismissedMention && dismissedMention.start === activeMention.start) {
    closeMentionMenu();
  } else {
    dismissedMention = null;
    openMentionMenu();
  }
});

input.addEventListener('keydown', (event) => {
  if (mentionMenu.hidden) return;
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    selectedModel = (selectedModel + 1) % models.length;
    renderMentionMenu();
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    selectedModel = (selectedModel - 1 + models.length) % models.length;
    renderMentionMenu();
  } else if (event.key === 'Enter') {
    const activeMention = getActiveMention();
    if (activeMention && models.some((model) => model.name === activeMention.text)) {
      closeMentionMenu();
      return;
    }
    event.preventDefault();
    insertMention(models[selectedModel]);
  } else if (event.key === 'Escape') {
    event.preventDefault();
    const activeMention = getActiveMention();
    dismissedMention = activeMention ? { start: activeMention.start } : null;
    closeMentionMenu();
  }
});

function scrollMessages() {
  messages.scrollTop = messages.scrollHeight;
}

function typeWriter(element, text, index = 0) {
  if (index === 0) element.textContent = '';
  if (index >= text.length) return;

  element.textContent += text[index];
  scrollMessages();
  setTimeout(() => typeWriter(element, text, index + 1), 20);
}

function addBubble(role, content, model = 'deepseek') {
  const messageType = arguments[3] || 'reply';
  const bubble = document.createElement('div');
  let assistantContent;
  bubble.className = `bubble message-bubble ${role}`;
  if (role === 'system') {
    bubble.textContent = content;
    messages.append(bubble);
    scrollMessages();
    return bubble;
  }
  setWorkspaceEmptyState(false);
  const row = document.createElement('div');
  row.className = `message-row ${role}-row`;
  if (role === 'user') {
    const avatar = document.createElement('div');
    avatar.className = 'avatar user-avatar';
    avatar.textContent = '我';
    bubble.classList.add('user-message');
    bubble.textContent = content;
    row.append(bubble, avatar);
  } else {
    const presentation = modelPresentationFor(model);
    const label = document.createElement('span');
    assistantContent = document.createElement('span');
    const avatar = createModelIdentity(presentation, 'avatar assistant-avatar');
    bubble.classList.add('assistant-bubble', 'ai-message');
    if (messageType === 'summary') bubble.classList.add('summary');
    label.className = 'assistant-model';
    label.textContent = presentation.name;
    if (messageType === 'summary') label.textContent = '协作总结 · DeepSeek';
    assistantContent.textContent = content;
    bubble.append(label, assistantContent);
    row.append(avatar, bubble);
  }
  messages.append(row);
  scrollMessages();
  return role === 'assistant' ? assistantContent : bubble;
}

capabilityDialogCancel?.addEventListener('click', () => settleCapabilityDialog(null));
capabilityDialogContinue?.addEventListener('click', () => {
  const selectedId = capabilityRecommendations?.querySelector('input[name="capability-helper-model"]:checked')?.value;
  const selected = pendingCapabilitySubmission?.recommendations.find((model) => model.platform_model_id === selectedId) || null;
  settleCapabilityDialog(selected);
});
capabilityDialog?.addEventListener('cancel', (event) => {
  event.preventDefault();
  settleCapabilityDialog(null);
});
document.addEventListener?.('keydown', (event) => {
  if (event.key === 'Escape' && pendingCapabilitySubmission) settleCapabilityDialog(null);
});

modelSearch?.addEventListener('input', renderModelTree);
modelDrawerToggle?.addEventListener('click', () => setModelDrawerOpen(!modelDrawer?.classList.contains('is-open')));
mobileAgentToggle?.addEventListener('click', () => setModelDrawerOpen(!modelDrawer?.classList.contains('is-open')));
modelDrawerClose?.addEventListener('click', () => setModelDrawerOpen(false));
modelDrawerBackdrop?.addEventListener('click', () => setModelDrawerOpen(false));
mobileSessionToggle?.addEventListener('click', () => {
  const isDesktop = globalThis.matchMedia?.('(min-width: 1024px)').matches === true;
  const isOpen = isDesktop
    ? !appShell?.classList.contains('session-panel-collapsed')
    : appShell?.classList.contains('session-panel-open');
  setSessionDrawerOpen(!isOpen);
});
sessionSidebarClose?.addEventListener('click', () => setSessionDrawerOpen(false));
sessionBackdrop?.addEventListener('click', () => setSessionDrawerOpen(false));
composerToolsToggle?.addEventListener('click', () => workspaceAttachmentInput?.click());
workspaceAttachmentInput?.addEventListener('change', async () => {
  try {
    await uploadWorkspaceAttachments(workspaceAttachmentInput.files);
  } catch (error) {
    setWorkspaceAttachmentStatus(error.message || '附件添加失败');
    addBubble('system', error.message || '附件添加失败');
  } finally {
    workspaceAttachmentInput.value = '';
  }
});
taskComposer?.addEventListener('dragover', (event) => {
  event.preventDefault();
  taskComposer.classList.add('is-drop-target');
});
taskComposer?.addEventListener('dragleave', (event) => {
  if (!taskComposer.contains(event.relatedTarget)) taskComposer.classList.remove('is-drop-target');
});
taskComposer?.addEventListener('drop', async (event) => {
  event.preventDefault();
  taskComposer.classList.remove('is-drop-target');
  try {
    await uploadWorkspaceAttachments(event.dataTransfer?.files);
  } catch (error) {
    setWorkspaceAttachmentStatus(error.message || '附件添加失败');
    addBubble('system', error.message || '附件添加失败');
  }
});
document.addEventListener?.('keydown', (event) => {
  if (event.key === 'Escape') {
    setModelDrawerOpen(false);
    if (appShell?.classList.contains('session-panel-open')) setSessionDrawerOpen(false);
  }
});
document.querySelectorAll?.('[data-prompt]').forEach((button) => {
  button.addEventListener('click', () => {
    input.value = button.dataset.prompt || '';
    input.focus();
  });
});
setSessionDrawerOpen(globalThis.matchMedia?.('(min-width: 1024px)').matches === true);
setWorkspaceEmptyState(true);

async function loadHistory() {
  try {
    const response = await fetch('/api/history');
    if (!response.ok) throw new Error('无法加载聊天记录');
    const history = await response.json();
    history.forEach((entry) => {
      const messageType = entry.message_type || 'reply';
      if (messageType === 'summary') return;
      addBubble(entry.role === 'user' ? 'user' : 'assistant', entry.content, entry.model, messageType);
    });
  } catch (error) {
    addBubble('system', error.message || '无法加载聊天记录');
  }
}

function renderConversations(nextConversations = conversations) {
  conversations = nextConversations;
  if (!conversationList) return;
  conversationList.replaceChildren();
  conversations.forEach((conversation) => {
    const row = document.createElement('div');
    const button = document.createElement('button');
    const remove = document.createElement('button');
    row.className = 'session-row';
    button.type = 'button'; button.className = 'session-item';
    button.textContent = conversation.title;
    remove.type = 'button';
    remove.className = 'session-delete';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `删除对话：${conversation.title}`);
    remove.disabled = pendingConversationDeletions.size > 0;
    const isCurrent = conversation.id === currentConversationId;
    button.classList.toggle('is-current', isCurrent);
    if (isCurrent) button.setAttribute('aria-current', 'page');
    button.addEventListener('click', () => selectConversation(conversation));
    remove.addEventListener('click', (event) => {
      event?.stopPropagation?.();
      deleteConversation(conversation).catch((error) => {
        setWorkspaceAttachmentStatus(error.message || '无法删除会话');
      });
    });
    row.append(button, remove);
    conversationList.append(row);
  });
}

async function loadConversationMessages(conversationId) {
  const response = await fetch(`/api/conversations/${conversationId}/messages`, { credentials: 'same-origin' });
  const history = await readWorkspaceJson(response, `/api/conversations/${conversationId}/messages`);
  if (!response.ok) throw new Error(history.error || '无法加载会话');
  return history;
}

async function loadConversationSnapshot(conversationId, { refresh = false } = {}) {
  const cached = conversationSnapshotCache.get(conversationId);
  if (cached && !refresh) {
    cacheConversationSnapshot(conversationId, cached);
    return cached;
  }
  const [history, settings] = await Promise.all([
    loadConversationMessages(conversationId),
    workspaceModelStore.load(conversationId, false),
  ]);
  const snapshot = { history, settings };
  cacheConversationSnapshot(conversationId, snapshot);
  return snapshot;
}

function stopActiveWorkspaceStream() {
  workspaceStreamVersion += 1;
  if (activeWorkspaceAbortController) {
    workspaceStopRequested = true;
  }
  activeWorkspaceAbortController?.abort();
  activeWorkspaceAbortController = null;
  stopGenerationButton?.setAttribute('hidden', '');
}

function isIntentionalWorkspaceAbort(error) {
  return workspaceStopRequested || error?.name === 'AbortError';
}

async function selectConversation(conversation) {
  if (conversation?.id === currentConversationId) return true;
  const selectionVersion = ++selectionAttemptVersion;
  try {
    const { history, settings } = await loadConversationSnapshot(conversation.id);
    if (selectionVersion !== selectionAttemptVersion) return false;
    stopActiveWorkspaceStream();
    currentConversationId = conversation.id;
    ++conversationActivationEpoch;
    workspaceModelStore.apply(settings);
    clearWorkspaceAttachments();
    messages.replaceChildren();
    collaborationGroups?.replaceChildren();
    history.forEach((entry) => addBubble(entry.role === 'user' ? 'user' : 'assistant', entry.content, entry.model, entry.message_type || 'reply'));
    setWorkspaceEmptyState(history.length === 0);
    renderConversations();
    if (globalThis.matchMedia?.('(max-width: 1023px)').matches === true) setSessionDrawerOpen(false);
    return true;
  } catch (error) {
    if (selectionVersion !== selectionAttemptVersion) return false;
    setWorkspaceAttachmentStatus(error.message || '无法切换会话');
    renderConversations();
    return false;
  }
}

function clearDeletedConversationUi() {
  stopActiveWorkspaceStream();
  currentConversationId = null;
  ++conversationActivationEpoch;
  clearWorkspaceAttachments();
  workspaceModelStore.settings = [];
  renderSelectedWorkspaceModels();
  messages.replaceChildren();
  collaborationGroups?.replaceChildren();
  setWorkspaceEmptyState(true);
}

async function deleteConversationNow(conversation) {
  if (!conversations.some((item) => item.id === conversation.id)) return false;
  const response = await fetch(`/api/conversations/${conversation.id}`, {
    method: 'DELETE',
    headers: { 'X-CSRF-Token': workspaceCsrfToken?.value || '' },
    credentials: 'same-origin',
  });
  const payload = await readWorkspaceJson(response, `/api/conversations/${conversation.id}`);
  if (!response.ok) throw new Error(payload.error || '无法删除会话');

  ++selectionAttemptVersion;
  conversationSnapshotCache.delete(conversation.id);
  const deletedCurrent = conversation.id === currentConversationId;
  conversations = conversations.filter((item) => item.id !== conversation.id);
  if (!deletedCurrent) {
    renderConversations();
    if (!currentConversationId && conversations.length) await selectConversation(conversations[0]);
    else if (!conversations.length) await createConversation();
    return true;
  }

  clearDeletedConversationUi();
  renderConversations();
  if (conversations.length) {
    await selectConversation(conversations[0]);
    return true;
  }
  await createConversation();
  return true;
}

function deleteConversation(conversation) {
  const pending = pendingConversationDeletions.get(conversation.id);
  if (pending) return pending;
  if (!globalThis.confirm(`确定删除“${conversation.title}”吗？此操作无法撤销。`)) return Promise.resolve(false);

  const task = conversationDeletionQueue.then(() => deleteConversationNow(conversation));
  const result = task.finally(() => {
    pendingConversationDeletions.delete(conversation.id);
    renderConversations();
  });
  pendingConversationDeletions.set(conversation.id, result);
  conversationDeletionQueue = result.catch(() => {});
  renderConversations();
  return result;
}

async function loadConversations() {
  const response = await fetch('/api/conversations');
  const loadedConversations = await readWorkspaceJson(response, '/api/conversations');
  if (!response.ok) throw new Error(loadedConversations.error || '无法加载会话');
  if (!loadedConversations.length) {
    conversations = [];
    clearDeletedConversationUi();
    return;
  }
  renderConversations(loadedConversations);
  const activeConversation = loadedConversations.find((conversation) => conversation.id === currentConversationId) || loadedConversations[0];
  if (activeConversation.id !== currentConversationId) await selectConversation(activeConversation);
}

async function createConversation({ preserveDraft = false } = {}) {
  const draftSettings = preserveDraft ? [...workspaceModelStore.settings] : [];
  const response = await fetch('/api/conversations', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': workspaceCsrfToken?.value || '',
    },
    body: JSON.stringify({ title: '新对话' }),
  });
  const conversation = await response.json();
  if (!response.ok) throw new Error(conversation.error || '无法新建会话');
  ++selectionAttemptVersion;
  stopActiveWorkspaceStream();
  currentConversationId = conversation.id;
  ++conversationActivationEpoch;
  if (preserveDraft && draftSettings.length) {
    await workspaceModelStore.replace(draftSettings);
  } else if (!preserveDraft) {
    clearWorkspaceAttachments();
    workspaceModelStore.settings = [];
    renderSelectedWorkspaceModels();
    messages.replaceChildren();
    collaborationGroups?.replaceChildren();
    setWorkspaceEmptyState(true);
  }
  await loadConversations();
  return conversation;
}

async function ensureConversation() {
  if (currentConversationId) return currentConversationId;
  if (!pendingDraftConversationCreation) {
    pendingDraftConversationCreation = createConversation({ preserveDraft: true })
      .finally(() => { pendingDraftConversationCreation = null; });
  }
  const conversation = await pendingDraftConversationCreation;
  return conversation.id;
}

function applyTaskSnapshot(task, thinkingModels) {
  const results = task.models || {};
  for (const model of Object.keys(results)) {
    const state = results[model].state;
    const isThinking = thinkingModels.includes(model);
    setAgentThinking(model, isThinking);
    renderAgentStatus(model, state);
    if (terminalStates.has(state)) currentTaskModels.delete(model);
  }
}

async function pollTask(taskId) {
  const tracker = TaskPolling.createTaskResultTracker();
  while (true) {
    const response = await fetch(`/api/tasks/${taskId}`);
    const task = await response.json();
    if (!response.ok) throw new Error(task.error || '无法获取协作任务状态');
    const transition = tracker.consume(task);
    applyTaskSnapshot(task, transition.thinkingModels);
    updateTaskProgress(task);
    for (const event of transition.events) {
      if (event.type === 'reply') {
        const data = { reply: event.content, model: event.model };
        const replyContent = addBubble('assistant', '', data.model);
        typeWriter(replyContent, data.reply);
      } else if (event.type === 'failure') {
        const name = modelPresentationFor(event.model).name;
        addBubble('system', `${name}${event.state === 'timed_out' ? ' 响应超时。' : ' 未能完成回复。'}`);
      } else if (event.type === 'summary') {
        addBubble('assistant', event.content, event.model, event.message_type);
      }
    }
    if (transition.isTerminal) {
      currentTaskModels.clear();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

async function streamChat(message, selected) {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, models: selected, selected_models: selected, conversation_id: currentConversationId }),
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || '无法建立流式对话连接');
  }

  return consumeStreamResponse(response);
}

async function streamDynamicModelChat(message, model) {
  const response = await fetch('/api/model-chat/stream', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, model, conversation_id: currentConversationId }),
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || '无法建立流式对话连接');
  }

  return consumeStreamResponse(response);
}

async function streamWorkspace(message) {
  if (currentConversationId) conversationSnapshotCache.delete(currentConversationId);
  const streamVersion = ++workspaceStreamVersion;
  workspaceStopRequested = false;
  activeWorkspaceAbortController = new AbortController();
  stopGenerationButton?.removeAttribute('hidden');
  const response = await fetch('/api/workspace/stream', {
    method: 'POST', credentials: 'same-origin', signal: activeWorkspaceAbortController.signal,
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': workspaceCsrfToken?.value || '' },
    body: JSON.stringify({
      message,
      conversation_id: currentConversationId,
      models: workspaceModelStore.actionableSettings(),
      attachment_tokens: pendingAttachmentTokens(),
    }),
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    const message = data.error || '无法建立工作台流式连接';
    if (message === '该模型暂时不可用，请重新选择') await loadModelCatalog();
    throw new Error(message);
  }
  return consumeWorkspaceStreamResponse(response, streamVersion);
}

// Legacy compatibility path remains available: streamDynamicModelChat(message, selectedDynamicModelId).

function createCollaborationGroup(modelIds) {
  const root = document.createElement('details');
  const summary = document.createElement('summary');
  const cards = document.createElement('div');
  root.className = 'collaboration-group'; root.open = true;
  summary.textContent = `协作回复 · ${modelIds.length} 个模型`;
  cards.className = 'collaboration-group-cards'; root.append(summary, cards);
  collaborationGroups?.append(root);
  return { root, cards, replies: new Map() };
}

function appendWorkspaceEvent(group, event) {
  if (event.event === 'start') return;
  let reply = group.replies.get(event.model);
  if (!reply && event.model) {
    const card = document.createElement('article');
    const heading = document.createElement('strong');
    const content = document.createElement('p');
    card.className = 'collaboration-model-reply'; heading.textContent = modelDisplayName(event.model);
    content.className = 'collaboration-model-content'; card.append(heading, content);
    group.cards.append(card); reply = { card, content }; group.replies.set(event.model, reply);
  }
  if (event.event === 'token' && reply) { reply.content.textContent += event.token; scrollMessages(); }
  else if (event.event === 'error' && reply) { reply.card.classList.add('is-error'); reply.content.textContent = event.message || '模型回复失败，请稍后重试。'; }
  else if (event.event === 'error') addBubble('system', event.message || '模型回复失败，请稍后重试。');
}

async function consumeWorkspaceStreamResponse(response, streamVersion) {
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let group = null;
  while (true) {
    const { value, done } = await reader.read();
    if (streamVersion !== workspaceStreamVersion) return;
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split('\n\n'); buffer = frames.pop();
    for (const frame of frames) {
      if (streamVersion !== workspaceStreamVersion) return;
      const eventName = frame.match(/^event: (.+)$/m)?.[1] || 'message';
      const data = frame.match(/^data: (.+)$/m)?.[1]; if (!data) continue;
      const event = { event: eventName, ...JSON.parse(data) };
      if (eventName === 'start') {
        group = createCollaborationGroup(event.models || []);
        (event.models || []).forEach((model) => { currentTaskModels.add(model); setAgentThinking(model, true); renderAgentStatus(model, 'thinking'); });
      } else {
        if (eventName === 'done' || eventName === 'error') { currentTaskModels.delete(event.model); setAgentThinking(event.model, false); renderAgentStatus(event.model, eventName === 'done' ? 'completed' : 'failed'); }
        if (group) appendWorkspaceEvent(group, event);
      }
    }
    if (done) break;
  }
}

async function consumeStreamResponse(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const bubbleContents = {};
  let buffer = '';

  function handleStreamEvent(eventName, event) {
    if (eventName === 'start') {
      event.models.forEach((model) => {
        currentTaskModels.add(model);
        setAgentThinking(model, true);
        renderAgentStatus(model, 'thinking');
        bubbleContents[model] = addBubble('assistant', '', model);
      });
      return;
    }
    if (eventName === 'token') {
      const content = bubbleContents[event.model] || addBubble('assistant', '', event.model);
      bubbleContents[event.model] = content;
      content.textContent += event.token;
      scrollMessages();
      return;
    }
    if (eventName === 'done') {
      currentTaskModels.delete(event.model);
      setAgentThinking(event.model, false);
      renderAgentStatus(event.model, 'completed');
      return;
    }
    if (eventName === 'error') {
      if (event.model) {
        currentTaskModels.delete(event.model);
        setAgentThinking(event.model, false);
        renderAgentStatus(event.model, 'failed');
      }
      addBubble('system', event.message || '模型流式回复失败，请稍后重试。');
    }
  }

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split('\n\n');
    buffer = frames.pop();
    for (const frame of frames) {
      const eventName = frame.match(/^event: (.+)$/m)?.[1] || 'message';
      const data = frame.match(/^data: (.+)$/m)?.[1];
      if (data) handleStreamEvent(eventName, JSON.parse(data));
    }
    if (done) break;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  let activeSettings = workspaceModelStore.actionableSettings();
  if (activeSettings.length === 0) {
    noModelNotice.textContent = workspaceModelStore.settings.length
      ? '已选模型暂不可用，请重新选择可用模型。'
      : '请先选择至少一个协作模型。';
    noModelNotice.hidden = false;
    setModelDrawerOpen(true);
    modelSearch?.focus();
    return;
  }
  noModelNotice.hidden = true;
  const requiredCapability = requiredCapabilityForAttachments([...workspaceAttachments.values()]);
  if (!settingsSupportCapability(activeSettings, requiredCapability)) {
    const helperModel = await openCapabilityDialog(requiredCapability);
    if (!helperModel) return;
    try {
      await workspaceModelStore.replace([
        ...activeSettings,
        { model_id: helperModel.platform_model_id, context_mode: contextMode?.value || 'smart', message_ids: [] },
      ]);
      activeSettings = workspaceModelStore.actionableSettings();
    } catch (error) {
      addBubble('system', error.message || '无法添加辅助模型');
      return;
    }
  }
  const attachmentNames = [...workspaceAttachments.values()].map((attachment) => attachment.name);
  const userMessage = attachmentNames.length
    ? `${message}\n[\u9644\u4ef6\uff1a${attachmentNames.join('\u3001')}]`
    : message;
  try {
    await ensureConversation();
  } catch (error) {
    addBubble('system', error.message || '无法新建会话');
    return;
  }
  conversationSnapshotCache.delete(currentConversationId);
  addBubble('user', userMessage);
  input.value = '';
  closeMentionMenu();
  input.focus();
  send.disabled = true;
  const submittedConversationId = currentConversationId;
  try {
    if (window.ReadableStream) {
      await streamWorkspace(message);
      clearWorkspaceAttachments();
      return;
    }

    if (pendingAttachmentTokens().length) {
      throw new Error('\u5f53\u524d\u6d4f\u89c8\u5668\u4e0d\u652f\u6301\u9644\u4ef6\u6d41\u5f0f\u4efb\u52a1\uff0c\u8bf7\u66f4\u6362\u73b0\u4ee3\u6d4f\u89c8\u5668\u540e\u91cd\u8bd5');
    }

    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Legacy fallback compatibility: models: [...selectedModels], selected_models: [...selectedModels]
      body: JSON.stringify({
        message,
        models: workspaceModelStore.selectedIds(),
        selected_models: workspaceModelStore.selectedIds(),
        conversation_id: currentConversationId,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '发送失败');
    data.models.forEach((model) => {
      currentTaskModels.add(model);
      setAgentThinking(model, true);
      renderAgentStatus(model, 'thinking');
    });
    updateTaskProgress({ models: Object.fromEntries(data.models.map((model) => [model, { state: 'thinking' }])) });
    await pollTask(data.task_id);
  } catch (error) {
    if (submittedConversationId !== currentConversationId) return;
    if (isIntentionalWorkspaceAbort(error)) {
      currentTaskModels.forEach((model) => {
        setAgentThinking(model, false);
        renderAgentStatus(model, 'idle');
      });
      currentTaskModels.clear();
      setWorkspaceAttachmentStatus('已停止生成');
      return;
    }
    currentTaskModels.forEach((model) => {
      setAgentThinking(model, false);
      renderAgentStatus(model, 'failed');
    });
    currentTaskModels.clear();
    addBubble('system', error.message || '发送失败');
  } finally {
    send.disabled = false;
    activeWorkspaceAbortController = null;
    workspaceStopRequested = false;
    stopGenerationButton?.setAttribute('hidden', '');
  }
});

stopGenerationButton?.addEventListener('click', () => {
  stopActiveWorkspaceStream();
});

if (newSessionButton) newSessionButton.addEventListener('click', () => createConversation().catch((error) => addBubble('system', error.message)));
if (messages && typeof messages.append === 'function' && conversationList) {
  loadConversations().catch((error) => addBubble('system', error.message));
}
