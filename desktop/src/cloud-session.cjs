const SAFE_PATHS = new Set([
  '/api/desktop/session',
  '/api/conversations',
  '/api/models/catalog',
]);
const CONVERSATION_ID = /^[a-f0-9]{32}$/;
const SIGNED_OUT = Symbol('signed-out');

function conversationPath(conversationId, suffix = '') {
  if (typeof conversationId !== 'string' || !CONVERSATION_ID.test(conversationId)) {
    throw safeCloudError('会话标识无效');
  }
  return `/api/conversations/${conversationId}${suffix}`;
}

function isReviewedPath(pathname) {
  return SAFE_PATHS.has(pathname)
    || /^\/api\/conversations\/[a-f0-9]{32}(?:\/(?:messages|models))?$/.test(pathname)
    || pathname === '/api/workspace/stream';
}

function normalizeBaseUrl(rawBaseUrl, production) {
  let url;
  try {
    url = new URL(rawBaseUrl);
  } catch {
    throw new Error('invalid cloud URL');
  }

  if (
    url.username
    || url.password
    || url.search
    || url.hash
    || (url.pathname !== '/' && url.pathname !== '')
  ) {
    throw new Error('invalid cloud URL');
  }

  const loopback = new Set(['127.0.0.1', 'localhost', '[::1]']);
  if (url.protocol !== 'https:') {
    const developmentLoopback = !production
      && url.protocol === 'http:'
      && loopback.has(url.hostname);
    if (!developmentLoopback) {
      throw new Error('生产云端地址必须使用 HTTPS');
    }
  }

  return url.origin;
}

function safeCloudError(message) {
  return new Error(message);
}

function safeDisplayName(username) {
  return username.includes('@') ? '已登录账户' : username;
}

function waitForAbort(promise, signal) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', onAbort);
      callback(value);
    };
    const onAbort = () => finish(reject, safeCloudError('连接云端超时，请稍后重试'));

    if (signal.aborted) {
      onAbort();
      return;
    }
    signal.addEventListener('abort', onAbort, { once: true });
    Promise.resolve(promise).then(
      (value) => finish(resolve, value),
      (error) => finish(reject, error),
    );
  });
}

class CloudSessionService {
  constructor(electronSession, baseUrl, options = {}) {
    if (!electronSession || typeof electronSession.fetch !== 'function') {
      throw new TypeError('invalid Electron session');
    }
    this.electronSession = electronSession;
    this.production = Boolean(options.production);
    this.packaged = Boolean(options.packaged);
    this.baseUrl = normalizeBaseUrl(baseUrl, this.production);
    this.timeoutMs = options.timeoutMs || 5_000;
    this.BrowserWindow = options.BrowserWindow || null;
    this.partition = options.partition || 'persist:astrarelay-cloud';
    this.onSessionChanged = options.onSessionChanged || (() => {});
    this.loginWindow = null;
    this.csrfToken = null;
  }

  async requestJson(pathname, options = {}) {
    const method = options.method || 'GET';
    if (!isReviewedPath(pathname) || !['GET', 'POST', 'PUT', 'DELETE'].includes(method)) {
      throw safeCloudError('不允许访问该云端接口');
    }
    if (options.csrf && !this.csrfToken) {
      const session = await this.status();
      if (!session.authenticated) throw safeCloudError('登录状态已失效');
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const headers = {
      accept: 'application/json',
      'cache-control': 'no-store',
    };
    if (options.body !== undefined) headers['content-type'] = 'application/json';
    if (options.csrf && this.csrfToken) headers['X-CSRF-Token'] = this.csrfToken;
    let response;
    try {
      response = await waitForAbort(
        this.electronSession.fetch(`${this.baseUrl}${pathname}`, {
          method,
          credentials: 'include',
          redirect: 'error',
          cache: 'no-store',
          headers,
          ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
          signal: controller.signal,
        }),
        controller.signal,
      );
    } catch (error) {
      clearTimeout(timeout);
      if (controller.signal.aborted) throw safeCloudError('连接云端超时，请稍后重试');
      throw safeCloudError('暂时无法连接 AstraRelay 云端');
    }

    try {
      if (response.status === 401) {
        this.csrfToken = null;
        return SIGNED_OUT;
      }
      if (!response.ok) throw safeCloudError('AstraRelay 云端暂时不可用');

      const contentType = response.headers.get('content-type') || '';
      if (!contentType.toLowerCase().startsWith('application/json')) {
        throw safeCloudError('云端返回了无法识别的响应');
      }

      try {
        return await waitForAbort(response.json(), controller.signal);
      } catch {
        if (controller.signal.aborted) {
          throw safeCloudError('连接云端超时，请稍后重试');
        }
        throw safeCloudError('云端返回了无法识别的响应');
      }
    } finally {
      clearTimeout(timeout);
    }
  }

  async status() {
    const payload = await this.requestJson('/api/desktop/session');
    if (payload === SIGNED_OUT) return { authenticated: false };
    if (
      !payload
      || payload.authenticated !== true
      || typeof payload.username !== 'string'
      || !payload.username
      || typeof payload.csrf_token !== 'string'
      || !payload.csrf_token
    ) {
      throw safeCloudError('云端会话响应无效');
    }
    this.csrfToken = payload.csrf_token;
    return {
      authenticated: true,
      display_name: safeDisplayName(payload.username),
    };
  }

  async bootstrap() {
    const session = await this.status();
    if (!session.authenticated) return { authenticated: false };

    const [conversations, catalog] = await Promise.all([
      this.requestJson('/api/conversations'),
      this.requestJson('/api/models/catalog'),
    ]);
    if (conversations === SIGNED_OUT || catalog === SIGNED_OUT) {
      return { authenticated: false };
    }
    if (!Array.isArray(conversations)) throw safeCloudError('云端会话列表响应无效');
    if (!catalog || !Array.isArray(catalog.providers)) {
      throw safeCloudError('云端模型目录响应无效');
    }

    return {
      authenticated: true,
      session,
      conversations,
      catalog,
    };
  }

  async listConversations() {
    const conversations = await this.requestJson('/api/conversations');
    if (conversations === SIGNED_OUT) throw safeCloudError('登录状态已失效');
    if (!Array.isArray(conversations)) throw safeCloudError('云端会话列表响应无效');
    return conversations.flatMap((conversation) => {
      if (!conversation || typeof conversation.id !== 'string' || !CONVERSATION_ID.test(conversation.id)
        || typeof conversation.title !== 'string') {
        return [];
      }
      return [{ id: conversation.id, title: conversation.title }];
    });
  }

  async createConversation() {
    const conversation = await this.requestJson('/api/conversations', {
      method: 'POST',
      body: { title: '新对话' },
      csrf: true,
    });
    if (conversation === SIGNED_OUT) throw safeCloudError('登录状态已失效');
    if (!conversation || typeof conversation.id !== 'string' || !CONVERSATION_ID.test(conversation.id)
      || typeof conversation.title !== 'string') {
      throw safeCloudError('云端会话响应无效');
    }
    return { id: conversation.id, title: conversation.title };
  }

  async openConversation(conversationId) {
    const path = conversationPath(conversationId);
    const [messages, modelSettings] = await Promise.all([
      this.requestJson(`${path}/messages`),
      this.requestJson(`${path}/models`),
    ]);
    if (messages === SIGNED_OUT || modelSettings === SIGNED_OUT) {
      throw safeCloudError('登录状态已失效');
    }
    if (!Array.isArray(messages) || !modelSettings || !Array.isArray(modelSettings.models)) {
      throw safeCloudError('云端会话响应无效');
    }
    return {
      id: conversationId,
      messages,
      model_settings: { models: modelSettings.models },
    };
  }

  async renameConversation(conversationId, title) {
    const normalizedTitle = typeof title === 'string' ? title.trim().slice(0, 255) : '';
    if (!normalizedTitle) throw safeCloudError('会话名称无效');
    const conversation = await this.requestJson(conversationPath(conversationId), {
      method: 'PUT',
      body: { title: normalizedTitle },
      csrf: true,
    });
    if (conversation === SIGNED_OUT) throw safeCloudError('登录状态已失效');
    if (!conversation || conversation.id !== conversationId || typeof conversation.title !== 'string') {
      throw safeCloudError('云端会话响应无效');
    }
    return { id: conversation.id, title: conversation.title };
  }

  async deleteConversation(conversationId) {
    const result = await this.requestJson(conversationPath(conversationId), {
      method: 'DELETE',
      csrf: true,
    });
    if (result === SIGNED_OUT) throw safeCloudError('登录状态已失效');
    if (!result || result.deleted !== true || result.conversation_id !== conversationId) {
      throw safeCloudError('云端会话响应无效');
    }
    return { deleted: true, conversation_id: conversationId };
  }

  async replaceConversationModels(conversationId, settings) {
    const models = settings && Array.isArray(settings.models) ? settings.models : null;
    if (!models) throw safeCloudError('模型设置无效');
    const result = await this.requestJson(conversationPath(conversationId, '/models'), {
      method: 'PUT',
      body: { models },
      csrf: true,
    });
    if (result === SIGNED_OUT) throw safeCloudError('登录状态已失效');
    if (!result || !Array.isArray(result.models)) throw safeCloudError('云端模型设置响应无效');
    return { models: result.models };
  }

  async streamWorkspace(payload, onEvent) {
    if (!payload || typeof payload.message !== 'string' || !payload.message.trim()
      || !Array.isArray(payload.models) || payload.models.length === 0
      || typeof onEvent !== 'function') {
      throw safeCloudError('工作台请求无效');
    }
    conversationPath(payload.conversation_id);
    if (!this.csrfToken) {
      const session = await this.status();
      if (!session.authenticated) throw safeCloudError('登录状态已失效');
    }
    let response;
    try {
      response = await this.electronSession.fetch(`${this.baseUrl}/api/workspace/stream`, {
        method: 'POST',
        credentials: 'include',
        redirect: 'error',
        cache: 'no-store',
        headers: {
          accept: 'text/event-stream',
          'content-type': 'application/json',
          'cache-control': 'no-store',
          'X-CSRF-Token': this.csrfToken,
        },
        body: JSON.stringify(payload),
      });
    } catch {
      throw safeCloudError('暂时无法连接 AstraRelay 云端');
    }
    if (response.status === 401) {
      this.csrfToken = null;
      throw safeCloudError('登录状态已失效');
    }
    if (!response.ok || !response.body || typeof response.body.getReader !== 'function') {
      throw safeCloudError('云端工作台暂时不可用');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = '';
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value || new Uint8Array(), { stream: !done });
      const records = pending.split(/\r?\n\r?\n/);
      pending = records.pop();
      for (const record of records) {
        const name = (record.match(/^event:\s*(.+)$/m) || [])[1];
        const data = (record.match(/^data:\s*(.+)$/m) || [])[1];
        if (!name || !data || !['start', 'token', 'done', 'error'].includes(name)) continue;
        try {
          const details = JSON.parse(data);
          if (details && typeof details === 'object' && !Array.isArray(details)) {
            onEvent({ event: name, ...details });
          }
        } catch {
          // Malformed stream events are discarded without exposing response bodies.
        }
      }
      if (done) break;
    }
  }

  async openLogin(parentWindow) {
    if (!this.BrowserWindow) throw safeCloudError('登录窗口暂时不可用');
    if (this.loginWindow && !this.loginWindow.isDestroyed()) {
      this.loginWindow.focus();
      return { opened: false, already_open: true };
    }

    const cloudOrigin = new URL(this.baseUrl).origin;
    let loginCompleted = false;
    const loginWindow = new this.BrowserWindow({
      width: 520,
      height: 720,
      minWidth: 420,
      minHeight: 600,
      parent: parentWindow && !parentWindow.isDestroyed() ? parentWindow : undefined,
      show: false,
      backgroundColor: '#f6f7fb',
      webPreferences: {
        partition: this.partition,
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true,
        allowRunningInsecureContent: false,
        webviewTag: false,
        devTools: !this.packaged,
      },
    });
    this.loginWindow = loginWindow;

    const allowCloudNavigation = (event, targetUrl) => {
      try {
        if (new URL(targetUrl).origin !== cloudOrigin) event.preventDefault();
      } catch {
        event.preventDefault();
      }
    };
    const detectWorkspace = (_event, targetUrl) => {
      try {
        const url = new URL(targetUrl);
        if (url.origin === cloudOrigin && url.pathname === '/workspace') {
          loginCompleted = true;
          loginWindow.close();
          this.onSessionChanged({ authenticated: true });
        }
      } catch {
        // Navigation validation handles malformed targets.
      }
    };

    loginWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    loginWindow.webContents.on('will-navigate', allowCloudNavigation);
    loginWindow.webContents.on('will-redirect', allowCloudNavigation);
    loginWindow.webContents.on('did-navigate', detectWorkspace);
    loginWindow.once('ready-to-show', () => loginWindow.show());
    loginWindow.on('closed', () => {
      if (this.loginWindow === loginWindow) this.loginWindow = null;
      if (!loginCompleted) this.onSessionChanged({ authenticated: false });
    });
    try {
      await loginWindow.loadURL(`${this.baseUrl}/login`);
    } catch {
      loginCompleted = true;
      if (!loginWindow.isDestroyed()) loginWindow.close();
      if (this.loginWindow === loginWindow) this.loginWindow = null;
      throw safeCloudError('暂时无法打开登录页面');
    }
    return { opened: true, already_open: false };
  }
}

module.exports = {
  CloudSessionService,
  normalizeBaseUrl,
};
