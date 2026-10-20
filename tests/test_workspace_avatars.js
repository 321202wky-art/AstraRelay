const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const workspacePath = path.join(__dirname, '..', 'static', 'workspace.js');

class FakeClassList {
  constructor(element) {
    this.element = element;
    this.values = new Set();
  }

  add(...names) {
    names.forEach((name) => this.values.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }

  contains(name) {
    return this.values.has(name);
  }

  toggle(name, force) {
    if (force === undefined ? !this.values.has(name) : force) this.values.add(name);
    else this.values.delete(name);
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = new Map();
    this.classList = new FakeClassList(this);
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this._textContent = '';
  }

  set className(value) {
    this._className = value;
    this.classList.values = new Set(String(value).split(/\s+/).filter(Boolean));
  }

  get className() {
    return this._className || '';
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  get textContent() {
    return this._textContent;
  }

  append(...children) {
    this._textContent = '';
    children.forEach((child) => {
      child.parentNode = this;
      this.children.push(child);
    });
  }

  replaceChildren(...children) {
    this.children = [];
    this._textContent = '';
    this.append(...children);
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) || null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  dispatch(type) {
    this.listeners[type]?.();
  }

  dispatchEvent(event) {
    this.listeners[event.type]?.(event);
  }

  querySelector(selector) {
    const tag = selector.toUpperCase();
    return this.children.find((child) => child.tagName === tag) || null;
  }

  contains(child) {
    return this.children.includes(child);
  }

  focus() {}

  click() {
    this.dispatch('click');
  }
}

function jsonResponse(payload, ok = true, status = ok ? 200 : 500) {
  return {
    ok,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
  };
}

function elementText(element) {
  return `${element.textContent}${element.children.map(elementText).join('')}`;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((next, fail) => { resolve = next; reject = fail; });
  return { promise, resolve, reject };
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}

function loadConversationApi(fetchImpl) {
  const source = fs.readFileSync(workspacePath, 'utf8').replace(
    /if \(messages && typeof messages\.append === 'function' && conversationList\) \{\s*loadConversations\(\)\.catch\(\(error\) => addBubble\('system', error\.message\)\);\s*\}/,
    '',
  ).replace(/\n\s*loadModelCatalog\(\);/, '');
  const elements = {
    '#messages': new FakeElement('section'),
    '#chat-form': new FakeElement('form'),
    '#message': new FakeElement('input'),
    '#send': new FakeElement('button'),
    '#mention-menu': new FakeElement('div'),
    '#conversation-list': new FakeElement('section'),
    '#workspace-csrf-token': new FakeElement('input'),
    '#workspace-attachment-list': new FakeElement('div'),
    '#workspace-attachment-status': new FakeElement('p'),
    '#collaboration-groups': new FakeElement('section'),
    '#workspace-empty-state': new FakeElement('section'),
    '#selected-model-list': new FakeElement('ul'),
    '#provider-select': new FakeElement('select'),
    '#model-select': new FakeElement('select'),
    '#add-model': new FakeElement('button'),
    '#stop-generation': new FakeElement('button'),
    '#no-model-notice': new FakeElement('p'),
    '#workspace-catalog-status': new FakeElement('section'),
    '#workspace-catalog-message': new FakeElement('strong'),
    '#workspace-catalog-detail': new FakeElement('p'),
    '#workspace-catalog-retry': new FakeElement('button'),
    '.chat-panel': new FakeElement('main'),
  };
  elements['#message'].value = '';
  elements['#workspace-csrf-token'].value = 'csrf-token';

  global.fetch = fetchImpl;
  global.window = { ReadableStream: class ReadableStream {} };
  globalThis.location = { search: '' };
  globalThis.matchMedia = () => ({ matches: false });
  global.document = {
    querySelector(selector) { return elements[selector] || null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement(tagName) { return new FakeElement(tagName); },
  };

  eval(`${source}\n;globalThis.WorkspaceConversationTestApi = { selectConversation, createConversation, deleteConversation, loadConversations, renderConversations, streamWorkspace, uploadWorkspaceAttachments, loadCatalog, replaceModels: (settings) => workspaceModelStore.replace(settings), applyModels: (settings) => workspaceModelStore.apply(settings), get attachmentTokens() { return pendingAttachmentTokens(); }, get currentConversationId() { return currentConversationId; }, get modelSettings() { return workspaceModelStore.settings; }, get messages() { return messages; }, get conversationList() { return conversationList; }, get collaborationGroups() { return collaborationGroups; }, get selectionStatus() { return workspaceAttachmentStatus; }, get selectedModelList() { return selectedModelList; }, get isEmpty() { return chatPanel.classList.contains('is-empty'); }, get abortController() { return activeWorkspaceAbortController; }, get form() { return form; }, get input() { return input; } };`);
  return globalThis.WorkspaceConversationTestApi;
}

function loadSelectorApi(search = '') {
  const source = fs.readFileSync(workspacePath, 'utf8');
  const input = {
    addEventListener() {}, setAttribute() {}, removeAttribute() {}, focus() {}, value: '', selectionStart: 0,
  };
  const providerSelect = new FakeElement('select');
  const modelSelect = new FakeElement('select');
  const addModelButton = new FakeElement('button');
  const messages = { append() {}, scrollHeight: 0, scrollTop: 0 };
  const elements = {
    '#messages': messages,
    '#chat-form': { addEventListener() {} },
    '#message': input,
    '#send': {},
    '#mention-menu': { hidden: true, replaceChildren() {}, append() {} },
    '#provider-select': providerSelect,
    '#model-select': modelSelect,
    '#add-model': addModelButton,
    '#workspace-catalog-status': new FakeElement('section'),
    '#workspace-catalog-message': new FakeElement('strong'),
    '#workspace-catalog-detail': new FakeElement('p'),
    '#workspace-catalog-retry': new FakeElement('button'),
  };

  global.fetch = async () => ({ ok: false, json: async () => ({}) });
  global.document = {
    querySelector(selector) {
      return elements[selector] || {
        '#model-picker': null,
        '#agent-status-list': null,
        '#task-progress': null,
        '#app-shell': null,
        '#mobile-agent-toggle': null,
        '#new-session': null,
        '#conversation-list': null,
      }[selector] || null;
    },
    createElement(tagName) { return new FakeElement(tagName); },
  };

  globalThis.__WORKSPACE_TEST__ = true;
  globalThis.location = { search };
  eval(source);
  delete globalThis.__WORKSPACE_TEST__;
  globalThis.WorkspaceModelSelector.elements = elements;
  return globalThis.WorkspaceModelSelector;
}

function loadAvatarApi() {
  const source = fs.readFileSync(workspacePath, 'utf8').replace(/\n\s*loadHistory\(\);/, '');
  const input = {
    addEventListener() {},
    setAttribute() {},
    removeAttribute() {},
  };
  const form = { addEventListener() {} };
  const mentionMenu = { hidden: true, replaceChildren() {}, append() {} };

  global.document = {
    querySelector(selector) {
      return {
        '#messages': new FakeElement('section'),
        '#chat-form': form,
        '#message': input,
        '#send': {},
        '#mention-menu': mentionMenu,
        '#model-picker': null,
        '#agent-status-list': null,
        '#task-progress': null,
        '#app-shell': null,
        '#mobile-agent-toggle': null,
      }[selector] || null;
    },
    createElement(tagName) { return new FakeElement(tagName); },
  };

  eval(`${source}\nglobalThis.WorkspaceAvatarTestApi = { models, modelPresentation, modelPresentationFor, createModelIdentity, addBubble, messages };`);
  return globalThis.WorkspaceAvatarTestApi;
}

test('each connected model exposes a text-only public identity', () => {
  const { models, modelPresentation } = loadAvatarApi();
  const expected = {
    deepseek: 'DeepSeek',
    kimi: 'Kimi',
    zhipu: 'GLM',
    qwen: 'Qwen',
    bytedance: '豆包',
    qwen_official: 'Qwen',
  };

  assert.deepEqual(Object.fromEntries(models.map((model) => [model.id, model.name])), expected);
  assert.deepEqual(Object.fromEntries(Object.entries(modelPresentation).map(([id, presentation]) => [id, presentation.name])), expected);
  assert.doesNotMatch(JSON.stringify({ models, modelPresentation }), /avatarSrc|avatarAlt|fallback|model-logos/);
});

test('completed replies are rendered with the typewriter helper', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /function typeWriter\(element, text/);
  assert.match(source, /typeWriter\(replyContent, data\.reply\)/);
});

test('chat submission consumes SSE tokens through a ReadableStream reader', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /fetch\('\/api\/chat\/stream'/);
  assert.match(source, /response\.body\.getReader\(\)/);
  assert.match(source, /content\.textContent \+= event\.token/);
});

test('assistant replies render their public model name without an image element', () => {
  const { addBubble, messages } = loadAvatarApi();
  addBubble('assistant', 'Reply', 'deepseek');

  const row = messages.children[0];
  assert.match(elementText(row), /DeepSeek/);
  assert.equal(row.querySelector('img'), null);
});

test('unknown task failures use the AI label instead of a raw model id', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');
  const { modelPresentationFor } = loadAvatarApi();

  assert.match(source, /const name = modelPresentationFor\(event\.model\)\.name;/);
  assert.equal(modelPresentationFor('upstream/private-model-id').name, 'AI');
});

test('public brand catalog requires exact selectable and callable chat flags', () => {
  const api = loadSelectorApi();
  api.loadCatalog({
    availability: 'fresh',
    providers: [
      {
        key: 'deepseek', name: 'DeepSeek', status: 'fresh', capabilities: {
          chat: [
            { platform_model_id: 'deepseek/deepseek-v4-flash', model_id: 'deepseek-v4-flash', display_name: 'DeepSeek V4 Flash', capability: 'chat', callable: true, selectable: true },
            { platform_model_id: 'deepseek/deepseek-r1', model_id: 'deepseek-r1', display_name: 'DeepSeek R1', capability: 'chat', callable: true, selectable: false },
          ],
          image: [{ platform_model_id: 'other/flux', model_id: 'flux', display_name: 'Flux', capability: 'image', callable: false }],
        },
      },
      {
        key: 'qwen', name: '千问', status: 'fresh', capabilities: {
          chat: [{ platform_model_id: 'qwen/qwen-plus', model_id: 'qwen-plus', display_name: 'Qwen Plus', capability: 'chat', callable: true, selectable: true }],
        },
      },
    ],
  });

  assert.equal(api.providerSelect.value, 'deepseek');
  assert.equal(api.modelSelect.value, 'deepseek/deepseek-v4-flash');
  assert.deepEqual(api.modelSelect.children.map((option) => option.value), ['deepseek/deepseek-v4-flash']);
  assert.equal(api.selectedModels.size, 0);

  api.providerSelect.value = 'qwen';
  api.providerSelect.dispatchEvent({ type: 'change' });
  assert.deepEqual(api.modelSelect.children.map((option) => option.value), ['qwen/qwen-plus']);
  assert.equal(api.modelSelect.value, 'qwen/qwen-plus');

  api.addSelectedModel();
  api.addSelectedModel();
  assert.equal(api.selectedDynamicModelId, 'qwen/qwen-plus');
  assert.deepEqual([...api.selectedModels], []);
});

test('degraded workspace catalog keeps only its selectable subset and shows safe freshness', () => {
  const api = loadSelectorApi();
  const settings = [
    { model_id: 'deepseek/fresh', context_mode: 'smart', message_ids: [] },
    { model_id: 'deepseek/stale', context_mode: 'smart', message_ids: [] },
  ];

  api.loadCatalog({
    availability: 'degraded',
    message: '模型目录暂时无法更新，当前仅展示最近一次可用信息',
    last_success_at: '2026-08-10T07:00:00Z',
    retryable: true,
    providers: [{
      key: 'deepseek', name: 'DeepSeek', status: 'stale', capabilities: { chat: [
        { platform_model_id: 'deepseek/fresh', display_name: 'Fresh', capability: 'chat', availability: 'fresh', callable: true, selectable: true },
        { platform_model_id: 'deepseek/stale', display_name: 'Stale', capability: 'chat', availability: 'degraded', callable: false, selectable: false },
      ] },
    }],
  });

  assert.deepEqual(api.modelSelect.children.map((option) => option.value), ['deepseek/fresh']);
  assert.deepEqual(api.actionableModelSettings(settings).map((item) => item.model_id), ['deepseek/fresh']);
  assert.deepEqual(api.unavailableModelSettings(settings).map((item) => item.model_id), ['deepseek/stale']);
  assert.equal(api.elements['#workspace-catalog-status'].hidden, false);
  assert.equal(api.elements['#workspace-catalog-status'].dataset.state, 'degraded');
  assert.equal(api.elements['#workspace-catalog-message'].textContent, '模型目录暂时无法更新，当前仅展示最近一次可用信息');
  assert.match(api.elements['#workspace-catalog-detail'].textContent, /最近成功更新/);
  assert.match(api.elements['#workspace-catalog-detail'].textContent, /2026/);
  assert.equal(api.elements['#workspace-catalog-retry'].hidden, false);
});

test('unavailable workspace catalog disables even malformed selectable records', () => {
  const api = loadSelectorApi('?model=deepseek/unsafe');

  api.loadCatalog({
    availability: 'unavailable',
    message: '模型目录暂不可用，请稍后重试',
    last_success_at: null,
    retryable: false,
    providers: [{
      key: 'deepseek', name: 'DeepSeek', status: 'stale', capabilities: { chat: [
        { platform_model_id: 'deepseek/unsafe', display_name: 'Unsafe', capability: 'chat', callable: true, selectable: true },
      ] },
    }],
  });

  assert.equal(api.providerSelect.disabled, true);
  assert.equal(api.modelSelect.disabled, true);
  assert.equal(api.addModelButton.disabled, true);
  assert.equal(api.selectedDynamicModelId, null);
  assert.deepEqual(api.actionableModelSettings([{ model_id: 'deepseek/unsafe' }]), []);
  assert.equal(api.elements['#workspace-catalog-status'].dataset.state, 'unavailable');
  assert.equal(api.elements['#workspace-catalog-retry'].hidden, true);
  assert.doesNotMatch(api.elements['#workspace-catalog-detail'].textContent, /最近成功更新/);
});

test('historical unavailable models remain read-only and never enter workspace payloads', async () => {
  let submitted;
  const api = loadConversationApi(async (url, options = {}) => {
    if (url === '/api/workspace/stream') {
      submitted = JSON.parse(options.body);
      return { ok: true, body: { getReader: () => ({ read: async () => ({ done: true }) }) } };
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  api.loadCatalog({
    availability: 'degraded',
    message: '模型目录暂时无法更新，当前仅展示最近一次可用信息',
    providers: [{
      key: 'deepseek', name: 'DeepSeek', status: 'stale', capabilities: { chat: [
        { platform_model_id: 'deepseek/fresh', display_name: 'Fresh Model', capability: 'chat', callable: true, selectable: true },
        { platform_model_id: 'deepseek/stale', display_name: 'Stale Model', capability: 'chat', callable: false, selectable: false },
      ] },
    }],
  });
  api.applyModels([
    { model_id: 'deepseek/fresh', context_mode: 'smart', message_ids: [] },
    { model_id: 'deepseek/stale', context_mode: 'smart', message_ids: [] },
  ]);

  await api.streamWorkspace('hello');

  assert.deepEqual(submitted.models.map((item) => item.model_id), ['deepseek/fresh']);
  assert.match(elementText(api.selectedModelList), /Stale Model/);
  assert.match(elementText(api.selectedModelList), /暂不可用/);
  const staleItem = api.selectedModelList.children[1];
  assert.equal(staleItem.classList.contains('is-unavailable'), true);
  assert.equal(staleItem.children[1].disabled, true);
  assert.equal(staleItem.children[2].disabled, true);
});

test('historical unavailable model settings block whole-replacement mutations without deleting history', async () => {
  let putRequests = 0;
  const originalSettings = [
    { model_id: 'deepseek/fresh', context_mode: 'smart', message_ids: [] },
    { model_id: 'deepseek/stale', context_mode: 'smart', message_ids: [] },
  ];
  const api = loadConversationApi(async (url, options = {}) => {
    if (url.endsWith('/saved/messages')) return jsonResponse([]);
    if (url.endsWith('/saved/models') && options.method !== 'PUT') {
      return jsonResponse({ models: originalSettings });
    }
    if (url.endsWith('/saved/models') && options.method === 'PUT') {
      putRequests += 1;
      return jsonResponse({ models: JSON.parse(options.body).models });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  api.loadCatalog({
    availability: 'degraded',
    providers: [{ key: 'deepseek', name: 'DeepSeek', status: 'stale', capabilities: { chat: [
      { platform_model_id: 'deepseek/fresh', display_name: 'Fresh Model', capability: 'chat', callable: true, selectable: true },
      { platform_model_id: 'deepseek/stale', display_name: 'Stale Model', capability: 'chat', callable: false, selectable: false },
    ] } }],
  });
  await api.selectConversation({ id: 'saved', title: 'Saved' });

  await assert.rejects(
    api.replaceModels([{ model_id: 'deepseek/fresh', context_mode: 'full', message_ids: [] }]),
    /目录恢复后再修改模型组合/,
  );

  assert.equal(putRequests, 0);
  assert.deepEqual(api.modelSettings, originalSettings);
});

test('catalog failure retains last-known public names while making selections unavailable', () => {
  const api = loadConversationApi(async () => { throw new Error('Unexpected request'); });
  api.loadCatalog({
    availability: 'fresh',
    providers: [{ key: 'deepseek', name: 'DeepSeek', status: 'fresh', capabilities: { chat: [
      { platform_model_id: 'deepseek/v4', display_name: 'DeepSeek V4', capability: 'chat', callable: true, selectable: true },
    ] } }],
  });
  api.applyModels([{ model_id: 'deepseek/v4', context_mode: 'smart', message_ids: [] }]);

  api.loadCatalog({ availability: 'unavailable', retryable: true, providers: [] });

  assert.match(elementText(api.selectedModelList), /DeepSeek V4/);
  assert.match(elementText(api.selectedModelList), /暂不可用/);
});

test('a server-side stale rejection refreshes the catalog without replaying the task', async () => {
  let streamRequests = 0;
  let catalogRequests = 0;
  const api = loadConversationApi(async (url) => {
    if (url === '/api/workspace/stream') {
      streamRequests += 1;
      return jsonResponse({ error: '该模型暂时不可用，请重新选择' }, false, 400);
    }
    if (url === '/api/models/catalog') {
      catalogRequests += 1;
      return jsonResponse({
        availability: 'unavailable', message: '模型目录暂不可用，请稍后重试', retryable: true, providers: [],
      });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  api.loadCatalog({
    availability: 'fresh',
    providers: [{ key: 'deepseek', name: 'DeepSeek', status: 'fresh', capabilities: { chat: [
      { platform_model_id: 'deepseek/fresh', display_name: 'Fresh', capability: 'chat', callable: true, selectable: true },
    ] } }],
  });
  api.applyModels([{ model_id: 'deepseek/fresh', context_mode: 'smart', message_ids: [] }]);

  await assert.rejects(api.streamWorkspace('hello'), /该模型暂时不可用，请重新选择/);

  assert.equal(streamRequests, 1);
  assert.equal(catalogRequests, 1);
});

test('workspace client never reads internal catalog failure categories', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');
  assert.doesNotMatch(source, /failure_categories/);
});

test('workspace derives image requirements and recommends only verified compatible helpers', () => {
  const api = loadSelectorApi();
  api.loadCatalog({
    availability: 'fresh',
    providers: [
      {
        key: 'deepseek', name: 'DeepSeek', status: 'fresh', capabilities: {
          chat: [{
            platform_model_id: 'deepseek/v4', display_name: 'DeepSeek V4', capability: 'chat', callable: true, selectable: true,
            capability_status: 'verified', supports: ['text_chat', 'reasoning', 'code'], unsupported: ['vision'],
            capability_note: '注：此模型为纯文本模型',
          }],
        },
      },
      {
        key: 'kimi', name: 'Kimi', status: 'fresh', capabilities: {
          chat: [{
            platform_model_id: 'kimi/k2.6', display_name: 'Kimi K2.6', capability: 'chat', callable: true, selectable: true,
            capability_status: 'verified', supports: ['text_chat', 'vision', 'reasoning', 'code'], unsupported: [],
            capability_note: '注：此模型支持图片理解',
          }],
        },
      },
      {
        key: 'other', name: '其他', status: 'fresh', capabilities: {
          chat: [{
            platform_model_id: 'other/pending', display_name: 'Pending', capability: 'chat', callable: true, selectable: true,
            capability_status: 'pending', supports: ['text_chat', 'vision'], unsupported: [],
            capability_note: '注：此模型能力信息待确认',
          }],
        },
      },
    ],
  });

  assert.equal(api.requiredCapabilityForAttachments([{ media_type: 'image/png' }]), 'vision');
  assert.equal(api.requiredCapabilityForAttachments([{ media_type: 'text/plain' }]), 'text_chat');
  assert.equal(api.settingsSupportCapability([{ model_id: 'deepseek/v4' }], 'vision'), false);
  assert.equal(api.settingsSupportCapability([{ model_id: 'deepseek/v4' }, { model_id: 'kimi/k2.6' }], 'vision'), true);
  assert.deepEqual(api.recommendHelperModels('vision').map((model) => model.platform_model_id), ['kimi/k2.6']);
});

test('workspace capability mismatch flow is explicit and resumable', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /function openCapabilityDialog\(requirement\)/);
  assert.match(source, /capabilityDialog\.showModal\(\)/);
  assert.match(source, /await workspaceModelStore\.replace/);
  assert.match(source, /pendingCapabilitySubmission/);
  assert.match(source, /event\.key === 'Escape'/);
  assert.doesNotMatch(source, /autoSwitchModel/);
});

test('workspace treats an intentional stream stop as a friendly status', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /workspaceStopRequested/);
  assert.match(source, /function isIntentionalWorkspaceAbort\(error\)/);
  assert.match(source, /setWorkspaceAttachmentStatus\('已停止生成'\)/);
  assert.match(source, /if \(isIntentionalWorkspaceAbort\(error\)\)/);
  assert.doesNotMatch(source, /addBubble\('system', error\.message[^\n]+BodyStreamBuffer/);
});

test('workspace uses the dedicated SSE endpoint for a selected dynamic model', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /fetch\('\/api\/model-chat\/stream'/);
  assert.match(source, /selectedDynamicModelId/);
  assert.match(source, /streamDynamicModelChat\(message, selectedDynamicModelId\)/);
});

test('workspace routes selected models through its session-scoped stream endpoint', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /fetch\('\/api\/workspace\/stream'/);
  assert.match(source, /workspaceModelStore\.selectedIds\(\)/);
  assert.match(source, /activeWorkspaceAbortController\?\.abort\(\)/);
});

test('workspace reports a non-JSON model response without leaking its HTML body', async () => {
  const api = loadSelectorApi();

  await assert.rejects(
    api.readWorkspaceJson(
      {
        status: 404,
        headers: { get: () => 'text/html; charset=utf-8' },
        text: async () => '<!doctype html><title>Not Found</title>',
      },
      '/api/conversations/demo/models',
    ),
    /会话模型接口返回了非 JSON 响应.*404/,
  );
});

test('workspace exposes dedicated session and attachment controls', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /function setSessionDrawerOpen\(isOpen\)/);
  assert.match(source, /workspaceAttachmentInput/);
  assert.match(source, /workspaceAttachmentList/);
});

test('workspace closes its session sidebar from button backdrop and Escape', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /sessionSidebarClose\?\.addEventListener\('click'/);
  assert.match(source, /sessionBackdrop\?\.addEventListener\('click'/);
  assert.match(source, /event\.key === 'Escape'/);
  assert.match(source, /function setWorkspaceEmptyState\(isEmpty\)/);
});

test('workspace exposes task mode presentation without changing selected model payloads', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /function setWorkspaceTaskMode\(capability\)/);
  assert.match(source, /workspaceTaskMode\.textContent/);
  assert.match(source, /selectedModels/);
});

test('workspace uploads attachments and submits their tokens with stream requests', () => {
  const source = fs.readFileSync(workspacePath, 'utf8');

  assert.match(source, /\/api\/workspace\/attachments/);
  assert.match(source, /X-CSRF-Token/);
  assert.match(source, /new FormData\(\)/);
  assert.match(source, /addEventListener\('drop'/);
  assert.match(source, /attachment_tokens:\s*pendingAttachmentTokens\(\)/);
});

test('workspace preselects a safe dynamic model requested by the model plaza', () => {
  const api = loadSelectorApi('?model=gateway_secondary%3Aqwen-plus');
  api.loadCatalog({
    availability: 'fresh',
    providers: [{
      key: 'gateway_secondary', name: 'GatewaySecondary', status: 'fresh', capabilities: {
        chat: [{ platform_model_id: 'gateway_secondary:qwen-plus', model_id: 'qwen-plus', display_name: 'Qwen Plus', capability: 'chat', callable: true, selectable: true }],
      },
    }],
  });

  assert.equal(api.providerSelect.value, 'gateway_secondary');
  assert.equal(api.selectedDynamicModelId, 'gateway_secondary:qwen-plus');
});

test('workspace consumes a model-plaza deep link only once across catalog retries', () => {
  const api = loadSelectorApi('?model=gateway_secondary%3Aqwen-plus');
  const catalog = {
    availability: 'fresh',
    providers: [{
      key: 'gateway_secondary', name: 'GatewaySecondary', status: 'fresh', capabilities: {
        chat: [{ platform_model_id: 'gateway_secondary:qwen-plus', model_id: 'qwen-plus', display_name: 'Qwen Plus', capability: 'chat', callable: true, selectable: true }],
      },
    }],
  };

  api.loadCatalog(catalog);
  api.clearDynamicModelSelection();
  api.loadCatalog(catalog);

  assert.equal(api.selectedDynamicModelId, null);
});

test('workspace restores a selected conversation, models, context mode, and active accessible row', async () => {
  const conversation = { id: 'saved', title: 'Saved conversation' };
  const stylesheet = fs.readFileSync(path.join(__dirname, '..', 'static', 'workspace.css'), 'utf8');
  const api = loadConversationApi(async (url) => {
    if (url === '/api/conversations/saved/messages') return jsonResponse([
      { role: 'user', content: 'saved question' },
      { role: 'assistant', content: 'saved reply', model: 'deepseek' },
    ]);
    if (url === '/api/conversations/saved/models') {
      return jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'full', message_ids: [] }] });
    }
    throw new Error(`Unexpected request: ${url}`);
  });

  api.renderConversations([conversation]);
  await api.selectConversation(conversation);

  assert.equal(api.currentConversationId, 'saved');
  assert.match(elementText(api.messages), /saved question/);
  assert.match(elementText(api.messages), /saved reply/);
  assert.deepEqual(api.modelSettings, [{ model_id: 'deepseek', context_mode: 'full', message_ids: [] }]);
  const row = api.conversationList.children[0];
  const [select, remove] = row.children;
  assert.equal(row.classList.contains('session-row'), true);
  assert.equal(select.classList.contains('is-current'), true);
  assert.equal(select.getAttribute('aria-current'), 'page');
  assert.equal(select.listeners.click instanceof Function, true);
  assert.equal(select.tagName, 'BUTTON');
  assert.equal(remove.classList.contains('session-delete'), true);
  assert.match(remove.getAttribute('aria-label'), /删除对话/);
  assert.match(stylesheet, /\.session-item \{[^}]*min-height: 2\.75rem;/);
});

test('conversation messages and model settings begin in parallel', async () => {
  const messages = deferred();
  const models = deferred();
  const calls = [];
  const api = loadConversationApi((url) => {
    calls.push(url);
    if (url.endsWith('/parallel/messages')) return messages.promise;
    if (url.endsWith('/parallel/models')) return models.promise;
    throw new Error(`Unexpected request: ${url}`);
  });
  calls.length = 0;

  const switching = api.selectConversation({ id: 'parallel', title: 'Parallel' });
  await flush();
  assert.deepEqual(calls, [
    '/api/conversations/parallel/messages',
    '/api/conversations/parallel/models',
  ]);
  messages.resolve(jsonResponse([{ role: 'user', content: 'ready' }]));
  models.resolve(jsonResponse({ models: [] }));
  await switching;
});

test('a cached conversation restores without another transport request', async () => {
  let messageCalls = 0;
  let modelCalls = 0;
  const api = loadConversationApi(async (url) => {
    if (url.endsWith('/cached/messages')) {
      messageCalls += 1;
      return jsonResponse([{ role: 'user', content: 'cached transcript' }]);
    }
    if (url.endsWith('/cached/models')) {
      modelCalls += 1;
      return jsonResponse({ models: [] });
    }
    if (url.endsWith('/other/messages')) return jsonResponse([]);
    if (url.endsWith('/other/models')) return jsonResponse({ models: [] });
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'cached', title: 'Cached' });
  await api.selectConversation({ id: 'other', title: 'Other' });
  await api.selectConversation({ id: 'cached', title: 'Cached' });

  assert.equal(messageCalls, 1);
  assert.equal(modelCalls, 1);
  assert.match(elementText(api.messages), /cached transcript/);
});

test('conversation snapshots retain only the twelve most recently used conversations', async () => {
  const messageCalls = new Map();
  const api = loadConversationApi(async (url) => {
    const match = url.match(/\/api\/conversations\/(conversation-\d+)\/(messages|models)$/);
    if (!match) throw new Error(`Unexpected request: ${url}`);
    const [, id, resource] = match;
    if (resource === 'messages') {
      messageCalls.set(id, (messageCalls.get(id) || 0) + 1);
      return jsonResponse([{ role: 'user', content: id }]);
    }
    return jsonResponse({ models: [] });
  });

  for (let index = 0; index < 13; index += 1) {
    await api.selectConversation({ id: `conversation-${index}`, title: `Conversation ${index}` });
  }
  await api.selectConversation({ id: 'conversation-0', title: 'Conversation 0' });

  assert.equal(messageCalls.get('conversation-0'), 2);
  assert.equal(messageCalls.get('conversation-1'), 1);
});

test('revisiting a snapshot promotes it before the least recently used entry is evicted', async () => {
  const messageCalls = new Map();
  const api = loadConversationApi(async (url) => {
    const match = url.match(/\/api\/conversations\/(conversation-\d+)\/(messages|models)$/);
    if (!match) throw new Error(`Unexpected request: ${url}`);
    const [, id, resource] = match;
    if (resource === 'messages') {
      messageCalls.set(id, (messageCalls.get(id) || 0) + 1);
      return jsonResponse([{ role: 'user', content: id }]);
    }
    return jsonResponse({ models: [] });
  });

  for (let index = 0; index < 12; index += 1) {
    await api.selectConversation({ id: `conversation-${index}`, title: `Conversation ${index}` });
  }
  await api.selectConversation({ id: 'conversation-0', title: 'Conversation 0' });
  await api.selectConversation({ id: 'conversation-12', title: 'Conversation 12' });
  await api.selectConversation({ id: 'conversation-1', title: 'Conversation 1' });
  await api.selectConversation({ id: 'conversation-0', title: 'Conversation 0' });

  assert.equal(messageCalls.get('conversation-0'), 1);
  assert.equal(messageCalls.get('conversation-1'), 2);
});

test('saving model settings invalidates the active conversation snapshot', async () => {
  let messageCalls = 0;
  let modelCalls = 0;
  const api = loadConversationApi(async (url, options = {}) => {
    if (url.endsWith('/cached/messages')) {
      messageCalls += 1;
      return jsonResponse([{ role: 'user', content: 'cached transcript' }]);
    }
    if (url.endsWith('/cached/models')) {
      modelCalls += 1;
      if (options.method === 'PUT') return jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'full' }] });
      return jsonResponse({ models: [] });
    }
    if (url.endsWith('/other/messages')) return jsonResponse([]);
    if (url.endsWith('/other/models')) return jsonResponse({ models: [] });
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'cached', title: 'Cached' });
  await api.replaceModels([{ model_id: 'deepseek', context_mode: 'full' }]);
  await api.selectConversation({ id: 'other', title: 'Other' });
  await api.selectConversation({ id: 'cached', title: 'Cached' });

  assert.equal(messageCalls, 2);
  assert.equal(modelCalls, 3);
});

test('starting a workspace message invalidates the active conversation snapshot', async () => {
  let messageCalls = 0;
  const api = loadConversationApi(async (url) => {
    if (url === '/api/workspace/stream') {
      return { ok: true, body: { getReader: () => ({ read: async () => ({ value: new Uint8Array(), done: true }) }) } };
    }
    if (url.endsWith('/cached/messages')) {
      messageCalls += 1;
      return jsonResponse([{ role: 'user', content: 'cached transcript' }]);
    }
    if (url.endsWith('/cached/models')) return jsonResponse({ models: [] });
    if (url.endsWith('/other/messages')) return jsonResponse([]);
    if (url.endsWith('/other/models')) return jsonResponse({ models: [] });
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'cached', title: 'Cached' });
  await api.streamWorkspace('new message');
  await api.selectConversation({ id: 'other', title: 'Other' });
  await api.selectConversation({ id: 'cached', title: 'Cached' });

  assert.equal(messageCalls, 2);
});

test('deleting the current conversation switches to the next conversation', async () => {
  globalThis.confirm = () => true;
  const api = loadConversationApi(async (url, options = {}) => {
    if (url.endsWith('/first/messages')) return jsonResponse([]);
    if (url.endsWith('/first/models')) return jsonResponse({ models: [] });
    if (url.endsWith('/second/messages')) return jsonResponse([{ role: 'user', content: 'second' }]);
    if (url.endsWith('/second/models')) return jsonResponse({ models: [] });
    if (url === '/api/conversations/first' && options.method === 'DELETE') {
      assert.equal(options.headers['X-CSRF-Token'], 'csrf-token');
      assert.equal(options.credentials, 'same-origin');
      return jsonResponse({ deleted: true, conversation_id: 'first' });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  api.renderConversations([
    { id: 'first', title: 'First' },
    { id: 'second', title: 'Second' },
  ]);
  await api.selectConversation({ id: 'first', title: 'First' });

  await api.deleteConversation({ id: 'first', title: 'First' });

  assert.equal(api.currentConversationId, 'second');
  assert.match(elementText(api.messages), /second/);
});

test('deleting a background conversation preserves the active conversation', async () => {
  globalThis.confirm = () => true;
  const api = loadConversationApi(async (url, options = {}) => {
    if (url.endsWith('/first/messages')) return jsonResponse([{ role: 'user', content: 'first' }]);
    if (url.endsWith('/first/models')) return jsonResponse({ models: [] });
    if (url === '/api/conversations/second' && options.method === 'DELETE') {
      return jsonResponse({ deleted: true, conversation_id: 'second' });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  api.renderConversations([
    { id: 'first', title: 'First' },
    { id: 'second', title: 'Second' },
  ]);
  await api.selectConversation({ id: 'first', title: 'First' });

  await api.deleteConversation({ id: 'second', title: 'Second' });

  assert.equal(api.currentConversationId, 'first');
  assert.match(elementText(api.messages), /first/);
  assert.equal(api.conversationList.children.length, 1);
});

test('queued distinct deletions serialize their switches and ignore a duplicate click', async () => {
  globalThis.confirm = () => true;
  const deleteA = deferred();
  const deleteB = deferred();
  const loadBMessages = deferred();
  const loadBModels = deferred();
  const deleteCalls = [];
  const api = loadConversationApi((url, options = {}) => {
    if (url.endsWith('/a/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'a' }]));
    if (url.endsWith('/a/models')) return Promise.resolve(jsonResponse({ models: [] }));
    if (url.endsWith('/b/messages')) return loadBMessages.promise;
    if (url.endsWith('/b/models')) return loadBModels.promise;
    if (url.endsWith('/c/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'c' }]));
    if (url.endsWith('/c/models')) return Promise.resolve(jsonResponse({ models: [] }));
    if (options.method === 'DELETE' && url === '/api/conversations/a') {
      deleteCalls.push('a');
      return deleteA.promise;
    }
    if (options.method === 'DELETE' && url === '/api/conversations/b') {
      deleteCalls.push('b');
      return deleteB.promise;
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  const a = { id: 'a', title: 'A' };
  const b = { id: 'b', title: 'B' };
  const c = { id: 'c', title: 'C' };
  api.renderConversations([a, b, c]);
  await api.selectConversation(a);

  const removeA = api.deleteConversation(a);
  const duplicateA = api.deleteConversation(a);
  const removeB = api.deleteConversation(b);
  await flush();

  assert.deepEqual(deleteCalls, ['a']);
  assert.equal(api.conversationList.children.every((row) => row.children[1].disabled), true);
  deleteA.resolve(jsonResponse({ deleted: true, conversation_id: 'a' }));
  await flush();
  assert.deepEqual(deleteCalls, ['a']);
  loadBMessages.resolve(jsonResponse([{ role: 'user', content: 'b' }]));
  loadBModels.resolve(jsonResponse({ models: [] }));
  await flush();
  assert.deepEqual(deleteCalls, ['a', 'b']);
  deleteB.resolve(jsonResponse({ deleted: true, conversation_id: 'b' }));

  await Promise.all([removeA, duplicateA, removeB]);
  assert.equal(api.currentConversationId, 'c');
  assert.match(elementText(api.messages), /c/);
});

test('a failed successor selection clears a deleted conversation before allowing a retry', async () => {
  globalThis.confirm = () => true;
  let bMessageRequests = 0;
  const api = loadConversationApi(async (url, options = {}) => {
    if (url.endsWith('/a/messages')) return jsonResponse([{ role: 'user', content: 'deleted transcript' }]);
    if (url.endsWith('/a/models')) return jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'smart' }] });
    if (url.endsWith('/b/messages')) {
      bMessageRequests += 1;
      return bMessageRequests === 1
        ? jsonResponse({ error: 'cannot load b' }, false)
        : jsonResponse([{ role: 'user', content: 'retry transcript' }]);
    }
    if (url.endsWith('/b/models')) return jsonResponse({ models: [] });
    if (url === '/api/conversations/a' && options.method === 'DELETE') {
      return jsonResponse({ deleted: true, conversation_id: 'a' });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  const a = { id: 'a', title: 'A' };
  const b = { id: 'b', title: 'B' };
  api.renderConversations([a, b]);
  await api.selectConversation(a);
  api.collaborationGroups.append(new FakeElement('article'));

  await api.deleteConversation(a);

  assert.equal(api.currentConversationId, null);
  assert.equal(api.messages.children.length, 0);
  assert.equal(api.collaborationGroups.children.length, 0);
  assert.deepEqual(api.modelSettings, []);
  assert.equal(api.isEmpty, true);
  assert.match(api.selectionStatus.textContent, /cannot load b/);

  await api.selectConversation(b);
  assert.equal(api.currentConversationId, 'b');
  assert.match(elementText(api.messages), /retry transcript/);
});

test('workspace keeps the displayed transcript unchanged when a selection fails', async () => {
  const api = loadConversationApi(async (url) => {
    if (url.endsWith('/saved/messages')) return jsonResponse([{ role: 'user', content: 'keep this' }]);
    if (url.endsWith('/saved/models')) return jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'smart' }] });
    if (url.endsWith('/broken/messages')) return jsonResponse({ error: 'cannot load conversation' }, false);
    if (url.endsWith('/broken/models')) return jsonResponse({ models: [] });
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'saved', title: 'Saved' });
  const transcript = elementText(api.messages);
  const models = api.modelSettings;
  await api.selectConversation({ id: 'broken', title: 'Broken' });

  assert.equal(api.currentConversationId, 'saved');
  assert.equal(elementText(api.messages), transcript);
  assert.deepEqual(api.modelSettings, models);
  assert.match(api.selectionStatus.textContent, /cannot load conversation/);
});

test('workspace keeps the most recently selected conversation when earlier requests finish last', async () => {
  const slowMessages = deferred();
  const slowModels = deferred();
  const api = loadConversationApi((url) => {
    if (url.endsWith('/slow/messages')) return slowMessages.promise;
    if (url.endsWith('/slow/models')) return slowModels.promise;
    if (url.endsWith('/fast/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'fast' }]));
    if (url.endsWith('/fast/models')) return Promise.resolve(jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'from_now' }] }));
    throw new Error(`Unexpected request: ${url}`);
  });

  const slowSelection = api.selectConversation({ id: 'slow', title: 'Slow' });
  const fastSelection = api.selectConversation({ id: 'fast', title: 'Fast' });
  await fastSelection;
  slowMessages.resolve(jsonResponse([{ role: 'user', content: 'slow' }]));
  await flush();
  slowModels.resolve(jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'full' }] }));
  await slowSelection;

  assert.equal(api.currentConversationId, 'fast');
  assert.match(elementText(api.messages), /fast/);
  assert.doesNotMatch(elementText(api.messages), /slow/);
});

test('workspace ignores delayed model settings from an older selection', async () => {
  const slowModels = deferred();
  const api = loadConversationApi((url) => {
    if (url.endsWith('/slow/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'slow' }]));
    if (url.endsWith('/slow/models')) return slowModels.promise;
    if (url.endsWith('/fast/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'fast' }]));
    if (url.endsWith('/fast/models')) return Promise.resolve(jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'from_now' }] }));
    throw new Error(`Unexpected request: ${url}`);
  });

  const slowSelection = api.selectConversation({ id: 'slow', title: 'Slow' });
  await flush();
  await api.selectConversation({ id: 'fast', title: 'Fast' });
  slowModels.resolve(jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'full' }] }));
  await slowSelection;

  assert.deepEqual(api.modelSettings, [{ model_id: 'deepseek', context_mode: 'from_now' }]);
});

test('workspace aborts and isolates an in-flight stream before switching transcripts', async () => {
  const streamRead = deferred();
  const reader = { read: () => streamRead.promise };
  let requestedStream = false;
  const api = loadConversationApi(async (url) => {
    if (url === '/api/workspace/stream') {
      requestedStream = true;
      return { ok: true, body: { getReader: () => reader } };
    }
    if (url.endsWith('/first/messages')) return jsonResponse([{ role: 'user', content: 'first' }]);
    if (url.endsWith('/first/models')) return jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'smart' }] });
    if (url.endsWith('/next/messages')) return jsonResponse([{ role: 'user', content: 'next' }]);
    if (url.endsWith('/next/models')) return jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'smart' }] });
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'first', title: 'First' });
  const stream = api.streamWorkspace('work');
  await flush();
  assert.equal(requestedStream, true);
  const controller = api.abortController;
  await api.selectConversation({ id: 'next', title: 'Next' });

  assert.equal(controller.signal.aborted, true);
  streamRead.resolve({ value: new TextEncoder().encode('event: start\ndata: {"models":["deepseek"]}\n\n'), done: false });
  await flush();
  assert.equal(api.collaborationGroups.children.length, 0);
});

test('workspace preserves the active stream and transcript when a target selection fails', async () => {
  const streamRead = deferred();
  const reader = { read: () => streamRead.promise };
  const api = loadConversationApi(async (url) => {
    if (url === '/api/workspace/stream') return { ok: true, body: { getReader: () => reader } };
    if (url.endsWith('/first/messages')) return jsonResponse([{ role: 'user', content: 'first' }]);
    if (url.endsWith('/first/models')) return jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'smart' }] });
    if (url.endsWith('/broken/messages')) return jsonResponse({ error: 'cannot load target' }, false);
    if (url.endsWith('/broken/models')) return jsonResponse({ models: [] });
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'first', title: 'First' });
  const stream = api.streamWorkspace('work');
  await flush();
  const controller = api.abortController;
  const transcript = elementText(api.messages);
  await api.selectConversation({ id: 'broken', title: 'Broken' });

  assert.equal(controller.signal.aborted, false);
  assert.equal(elementText(api.messages), transcript);
  assert.doesNotMatch(elementText(api.messages), /cannot load target/);
  assert.match(api.selectionStatus.textContent, /cannot load target/);
  streamRead.resolve({ value: new Uint8Array(), done: true });
  await stream;
});

test('new conversation invalidates slower selections and isolates the prior stream', async () => {
  const slowMessages = deferred();
  const slowModels = deferred();
  const streamRead = deferred();
  const reader = { read: () => streamRead.promise };
  const api = loadConversationApi(async (url, options = {}) => {
    if (url === '/api/workspace/stream') return { ok: true, body: { getReader: () => reader } };
    if (url === '/api/conversations' && options.method === 'POST') return { ok: true, json: async () => ({ id: 'new', title: 'New' }) };
    if (url === '/api/conversations') return jsonResponse([{ id: 'new', title: 'New' }]);
    if (url.endsWith('/first/messages')) return jsonResponse([{ role: 'user', content: 'first' }]);
    if (url.endsWith('/first/models')) return jsonResponse({ models: [] });
    if (url.endsWith('/slow/messages')) return slowMessages.promise;
    if (url.endsWith('/slow/models')) return slowModels.promise;
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'first', title: 'First' });
  const stream = api.streamWorkspace('work');
  await flush();
  const controller = api.abortController;
  const slowSelection = api.selectConversation({ id: 'slow', title: 'Slow' });
  await api.createConversation();
  slowMessages.resolve(jsonResponse([{ role: 'user', content: 'slow' }]));
  await flush();
  slowModels.resolve(jsonResponse({ models: [] }));
  await slowSelection;

  assert.equal(controller.signal.aborted, true);
  assert.equal(api.currentConversationId, 'new');
  assert.equal(api.messages.children.length, 0);
  streamRead.resolve({ value: new Uint8Array(), done: true });
  await stream;
});

test('workspace ignores a stale model-settings save after switching conversations', async () => {
  const save = deferred();
  const api = loadConversationApi((url, options = {}) => {
    if (url.endsWith('/a/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'a' }]));
    if (url.endsWith('/a/models')) {
      if (options.method === 'PUT') return save.promise;
      return Promise.resolve(jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'smart' }] }));
    }
    if (url.endsWith('/b/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'b' }]));
    if (url.endsWith('/b/models')) return Promise.resolve(jsonResponse({ models: [{ model_id: 'kimi/kimi-chat', context_mode: 'full' }] }));
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'a', title: 'A' });
  const replace = api.replaceModels([{ model_id: 'deepseek', context_mode: 'full' }]);
  await api.selectConversation({ id: 'b', title: 'B' });
  save.resolve(jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'full' }] }));
  await replace;

  assert.deepEqual(api.modelSettings, [{ model_id: 'kimi/kimi-chat', context_mode: 'full' }]);
});

test('a successful stale model save invalidates its cached conversation before revisit', async () => {
  const save = deferred();
  let aModelGets = 0;
  const oldModels = [{ model_id: 'deepseek', context_mode: 'smart' }];
  const newModels = [{ model_id: 'deepseek', context_mode: 'full' }];
  const api = loadConversationApi((url, options = {}) => {
    if (url.endsWith('/a/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'a' }]));
    if (url.endsWith('/a/models')) {
      if (options.method === 'PUT') return save.promise;
      aModelGets += 1;
      return Promise.resolve(jsonResponse({ models: aModelGets === 1 ? oldModels : newModels }));
    }
    if (url.endsWith('/b/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'b' }]));
    if (url.endsWith('/b/models')) return Promise.resolve(jsonResponse({ models: [{ model_id: 'kimi/kimi-chat', context_mode: 'smart' }] }));
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'a', title: 'A' });
  const replace = api.replaceModels(newModels);
  await api.selectConversation({ id: 'b', title: 'B' });
  save.resolve(jsonResponse({ models: newModels }));
  await replace;
  await api.selectConversation({ id: 'a', title: 'A' });

  assert.equal(aModelGets, 2);
  assert.deepEqual(api.modelSettings, newModels);
});

test('workspace ignores delayed attachment tokens from a prior conversation', async () => {
  const upload = deferred();
  const api = loadConversationApi((url) => {
    if (url === '/api/workspace/attachments') return upload.promise;
    if (url.endsWith('/a/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'a' }]));
    if (url.endsWith('/a/models')) return Promise.resolve(jsonResponse({ models: [] }));
    if (url.endsWith('/b/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'b' }]));
    if (url.endsWith('/b/models')) return Promise.resolve(jsonResponse({ models: [] }));
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'a', title: 'A' });
  const attachment = api.uploadWorkspaceAttachments([new Blob(['attachment'])]);
  await api.selectConversation({ id: 'b', title: 'B' });
  upload.resolve(jsonResponse({ attachments: [{ token: 'a-token', name: 'a.txt', size: 1 }] }));
  await attachment;

  assert.deepEqual(api.attachmentTokens, []);
});

test('workspace presents historical public Kimi and Qwen model ids without DeepSeek attribution', async () => {
  const api = loadConversationApi(async (url) => {
    if (url.endsWith('/history/messages')) return jsonResponse([
      { role: 'assistant', content: 'Kimi reply', model: 'kimi/kimi-chat' },
      { role: 'assistant', content: 'Qwen reply', model: 'qwen/qwen-plus' },
    ]);
    if (url.endsWith('/history/models')) return jsonResponse({ models: [] });
    throw new Error(`Unexpected request: ${url}`);
  });
  api.loadCatalog({ providers: [
    { key: 'kimi', name: 'Kimi', capabilities: { chat: [{ platform_model_id: 'kimi/kimi-chat', display_name: 'Kimi Chat', callable: true, selectable: true }] } },
    { key: 'qwen', name: 'Qwen', capabilities: { chat: [{ platform_model_id: 'qwen/qwen-plus', display_name: 'Qwen Plus', callable: true, selectable: true }] } },
  ], availability: 'fresh' });

  await api.selectConversation({ id: 'history', title: 'History' });

  const labels = api.messages.children.map((row) => elementText(row));
  assert.match(labels[0], /Kimi Chat/);
  assert.match(labels[1], /Qwen Plus/);
  assert.doesNotMatch(labels.join(' '), /DeepSeek/);
});

test('clicking the current conversation leaves its active stream and transcript untouched', async () => {
  const streamRead = deferred();
  const reader = { read: () => streamRead.promise };
  let currentMessageFetches = 0;
  const conversation = { id: 'current', title: 'Current' };
  const api = loadConversationApi(async (url) => {
    if (url === '/api/workspace/stream') return { ok: true, body: { getReader: () => reader } };
    if (url.endsWith('/current/messages')) { currentMessageFetches += 1; return jsonResponse([{ role: 'user', content: 'current' }]); }
    if (url.endsWith('/current/models')) return jsonResponse({ models: [] });
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation(conversation);
  api.renderConversations([conversation]);
  const stream = api.streamWorkspace('work');
  await flush();
  const controller = api.abortController;
  const transcript = elementText(api.messages);
  api.conversationList.children[0].click();
  await flush();

  assert.equal(controller.signal.aborted, false);
  assert.equal(currentMessageFetches, 1);
  assert.equal(elementText(api.messages), transcript);
  streamRead.resolve({ value: new Uint8Array(), done: true });
  await stream;
});

test('a failed target selection does not discard a valid pending model save for the current conversation', async () => {
  const save = deferred();
  const api = loadConversationApi((url, options = {}) => {
    if (url.endsWith('/a/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'a' }]));
    if (url.endsWith('/a/models')) return options.method === 'PUT'
      ? save.promise
      : Promise.resolve(jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'smart' }] }));
    if (url.endsWith('/broken/messages')) return Promise.resolve(jsonResponse({ error: 'target failed' }, false));
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'a', title: 'A' });
  const replace = api.replaceModels([{ model_id: 'deepseek', context_mode: 'full' }]);
  await api.selectConversation({ id: 'broken', title: 'Broken' });
  save.resolve(jsonResponse({ models: [{ model_id: 'deepseek', context_mode: 'full' }] }));
  await replace;

  assert.equal(api.currentConversationId, 'a');
  assert.deepEqual(api.modelSettings, [{ model_id: 'deepseek', context_mode: 'full' }]);
});

test('a failed target selection does not discard a valid pending attachment upload for the current conversation', async () => {
  const upload = deferred();
  const api = loadConversationApi((url) => {
    if (url === '/api/workspace/attachments') return upload.promise;
    if (url.endsWith('/a/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'a' }]));
    if (url.endsWith('/a/models')) return Promise.resolve(jsonResponse({ models: [] }));
    if (url.endsWith('/broken/messages')) return Promise.resolve(jsonResponse({ error: 'target failed' }, false));
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'a', title: 'A' });
  const attachment = api.uploadWorkspaceAttachments([new Blob(['attachment'])]);
  await api.selectConversation({ id: 'broken', title: 'Broken' });
  upload.resolve(jsonResponse({ attachments: [{ token: 'a-token', name: 'a.txt', size: 1 }] }));
  await attachment;

  assert.equal(api.currentConversationId, 'a');
  assert.deepEqual(api.attachmentTokens, ['a-token']);
});

test('a successful switch suppresses stale model and attachment transport failures', async () => {
  const save = deferred();
  const upload = deferred();
  const api = loadConversationApi((url, options = {}) => {
    if (url === '/api/workspace/attachments') return upload.promise;
    if (url.endsWith('/a/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'a' }]));
    if (url.endsWith('/a/models')) return options.method === 'PUT'
      ? save.promise
      : Promise.resolve(jsonResponse({ models: [] }));
    if (url.endsWith('/b/messages')) return Promise.resolve(jsonResponse([{ role: 'user', content: 'b' }]));
    if (url.endsWith('/b/models')) return Promise.resolve(jsonResponse({ models: [] }));
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation({ id: 'a', title: 'A' });
  const replace = api.replaceModels([{ model_id: 'deepseek', context_mode: 'full' }]);
  const attachment = api.uploadWorkspaceAttachments([new Blob(['attachment'])]);
  await api.selectConversation({ id: 'b', title: 'B' });
  save.reject(new Error('stale network failure'));
  upload.resolve({ ok: false, status: 502, headers: { get: () => 'text/html' } });

  await assert.doesNotReject(() => replace);
  await assert.doesNotReject(() => attachment);
  assert.equal(api.currentConversationId, 'b');
  assert.equal(api.selectionStatus.textContent, '');
});

test('workspace refreshes the conversation list without reselecting its active conversation', async () => {
  let messageRequests = 0;
  const conversation = { id: 'saved', title: 'Saved' };
  const api = loadConversationApi(async (url) => {
    if (url === '/api/conversations') return jsonResponse([conversation]);
    if (url.endsWith('/saved/messages')) { messageRequests += 1; return jsonResponse([{ role: 'user', content: 'saved' }]); }
    if (url.endsWith('/saved/models')) return jsonResponse({ models: [] });
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.selectConversation(conversation);
  await api.loadConversations();

  assert.equal(messageRequests, 1);
});

test('creating a conversation sends the workspace CSRF token', async () => {
  let options;
  const api = loadConversationApi(async (url, request = {}) => {
    if (url === '/api/conversations' && request.method === 'POST') {
      options = request;
      return jsonResponse({ id: 'created', title: '新对话' }, true, 201);
    }
    if (url === '/api/conversations') return jsonResponse([]);
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.createConversation();
  assert.equal(options.headers['X-CSRF-Token'], 'csrf-token');
});

test('an empty conversation list stays a local draft without creating a server record', async () => {
  let createRequests = 0;
  const api = loadConversationApi(async (url, options = {}) => {
    if (url === '/api/conversations' && options.method === 'POST') {
      createRequests += 1;
      return jsonResponse({ id: 'ghost', title: 'New' }, true, 201);
    }
    if (url === '/api/conversations') return jsonResponse([]);
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.loadConversations();

  assert.equal(createRequests, 0);
  assert.equal(api.currentConversationId, null);
  assert.equal(api.conversationList.children.length, 0);
  assert.equal(api.isEmpty, true);
});

test('submitting a local draft creates one conversation and preserves its selected models', async () => {
  let createRequests = 0;
  let savedModels = null;
  let streamedPayload = null;
  const api = loadConversationApi(async (url, options = {}) => {
    if (url === '/api/conversations' && options.method === 'POST') {
      createRequests += 1;
      return jsonResponse({ id: 'created', title: '新对话' }, true, 201);
    }
    if (url === '/api/conversations') {
      return jsonResponse(createRequests ? [{ id: 'created', title: '新对话' }] : []);
    }
    if (url.endsWith('/created/models') && options.method === 'PUT') {
      savedModels = JSON.parse(options.body).models;
      return jsonResponse({ models: savedModels });
    }
    if (url === '/api/workspace/stream') {
      streamedPayload = JSON.parse(options.body);
      return { ok: true, body: { getReader: () => ({ read: async () => ({ value: new Uint8Array(), done: true }) }) } };
    }
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.loadConversations();
  api.loadCatalog({
    availability: 'fresh',
    providers: [{
      key: 'deepseek', name: 'DeepSeek', status: 'fresh', capabilities: { chat: [
        { platform_model_id: 'deepseek/v4', display_name: 'DeepSeek V4', capability: 'chat', callable: true, selectable: true, supports: ['text_chat'] },
      ] },
    }],
  });
  await api.replaceModels([{ model_id: 'deepseek/v4', context_mode: 'smart', message_ids: [] }]);
  api.input.value = 'hello';
  await api.form.listeners.submit({ preventDefault() {} });

  assert.equal(createRequests, 1);
  assert.equal(api.currentConversationId, 'created');
  assert.deepEqual(savedModels, [{ model_id: 'deepseek/v4', context_mode: 'smart', message_ids: [] }]);
  assert.equal(streamedPayload.conversation_id, 'created');
});

test('uploading the first attachment lazily creates the draft conversation', async () => {
  let createRequests = 0;
  let uploadConversationId = null;
  const api = loadConversationApi(async (url, options = {}) => {
    if (url === '/api/conversations' && options.method === 'POST') {
      createRequests += 1;
      return jsonResponse({ id: 'created', title: '新对话' }, true, 201);
    }
    if (url === '/api/conversations') {
      return jsonResponse(createRequests ? [{ id: 'created', title: '新对话' }] : []);
    }
    if (url === '/api/workspace/attachments') {
      uploadConversationId = options.body.get('conversation_id');
      return jsonResponse({ attachments: [{ token: 'token-1', name: 'brief.txt', size: 5, media_type: 'text/plain' }] });
    }
    throw new Error(`Unexpected request: ${url}`);
  });

  await api.loadConversations();
  await api.uploadWorkspaceAttachments([new Blob(['brief'])]);

  assert.equal(createRequests, 1);
  assert.equal(api.currentConversationId, 'created');
  assert.equal(uploadConversationId, 'created');
  assert.deepEqual(api.attachmentTokens, ['token-1']);
});
