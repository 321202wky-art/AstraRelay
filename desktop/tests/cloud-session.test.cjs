const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  CloudSessionService,
  normalizeBaseUrl,
} = require('../src/cloud-session.cjs');

test('production cloud URL requires HTTPS and development HTTP stays loopback-only', () => {
  assert.equal(
    normalizeBaseUrl('https://app.astrarelay.example/', true),
    'https://app.astrarelay.example',
  );
  assert.throws(
    () => normalizeBaseUrl('http://app.astrarelay.example', true),
    /HTTPS/,
  );
  assert.equal(
    normalizeBaseUrl('http://127.0.0.1:5000', false),
    'http://127.0.0.1:5000',
  );
  assert.throws(
    () => normalizeBaseUrl('http://example.com', false),
    /HTTPS/,
  );
  const credentialUrl = new URL('https://example.invalid');
  credentialUrl.username = '<synthetic-user>';
  credentialUrl.password = '<synthetic-password>';
  assert.throws(
    () => normalizeBaseUrl(credentialUrl.toString(), false),
    /invalid cloud URL/,
  );
});

test('bootstrap requests only reviewed safe endpoints with credentials', async () => {
  const seen = [];
  const fakeSession = {
    fetch: async (url, options) => {
      const pathname = new URL(url).pathname;
      seen.push({ pathname, options });
      const payload = pathname.endsWith('/session')
        ? { authenticated: true, username: 'alice', csrf_token: 'csrf' }
        : pathname.endsWith('/conversations')
          ? []
          : { providers: [], updated_at: null };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'content-type': 'application/json; charset=utf-8' },
      });
    },
  };
  const service = new CloudSessionService(
    fakeSession,
    'http://127.0.0.1:5000',
    { production: false },
  );

  const result = await service.bootstrap();

  assert.deepEqual(
    seen.map((request) => request.pathname),
    ['/api/desktop/session', '/api/conversations', '/api/models/catalog'],
  );
  assert.ok(seen.every((request) => request.options.method === 'GET'));
  assert.ok(seen.every((request) => request.options.credentials === 'include'));
  assert.ok(seen.every((request) => request.options.redirect === 'error'));
  assert.ok(seen.every((request) => request.options.cache === 'no-store'));
  assert.ok(seen.every(
    (request) => request.options.headers['cache-control'] === 'no-store',
  ));
  assert.deepEqual(result.session, { authenticated: true, display_name: 'alice' });
  assert.equal('csrf_token' in result.session, false);
  assert.equal('username' in result.session, false);
});

test('email-shaped account identifiers never reach the renderer-facing session', async () => {
  const fakeSession = {
    fetch: async () => new Response(JSON.stringify({
      authenticated: true,
      username: 'private@example.test',
      csrf_token: 'main-process-only',
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  };
  const service = new CloudSessionService(
    fakeSession,
    'http://127.0.0.1:5000',
    { production: false },
  );

  assert.deepEqual(
    await service.status(),
    { authenticated: true, display_name: '已登录账户' },
  );
});

test('HTML and unauthenticated responses become safe signed-out states', async () => {
  let requests = 0;
  const fakeSession = {
    fetch: async () => {
      requests += 1;
      return new Response('<html>login</html>', {
        status: 401,
        headers: { 'content-type': 'text/html' },
      });
    },
  };
  const service = new CloudSessionService(
    fakeSession,
    'http://127.0.0.1:5000',
    { production: false },
  );

  assert.deepEqual(await service.status(), { authenticated: false });
  assert.deepEqual(await service.bootstrap(), { authenticated: false });
  assert.equal(requests, 2);
});

test('successful non-JSON responses become safe errors without leaking their body', async () => {
  const fakeSession = {
    fetch: async () => new Response('<html>private upstream body</html>', {
      status: 200,
      headers: { 'content-type': 'text/html' },
    }),
  };
  const service = new CloudSessionService(
    fakeSession,
    'http://127.0.0.1:5000',
    { production: false },
  );

  await assert.rejects(
    service.status(),
    (error) => error.message === '云端返回了无法识别的响应',
  );
});

test('timeout covers a stalled JSON response body', { timeout: 500 }, async () => {
  const fakeSession = {
    fetch: async () => ({
      status: 200,
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => new Promise(() => {}),
    }),
  };
  const service = new CloudSessionService(
    fakeSession,
    'http://127.0.0.1:5000',
    { production: false, timeoutMs: 10 },
  );

  await assert.rejects(
    service.status(),
    (error) => error.message === '连接云端超时，请稍后重试',
  );
});

test('workspace methods use only reviewed conversation endpoints with main-process CSRF', async () => {
  const seen = [];
  const conversationId = 'a'.repeat(32);
  const fakeSession = {
    fetch: async (url, options = {}) => {
      const pathname = new URL(url).pathname;
      seen.push({ pathname, options });
      const payload = pathname === '/api/desktop/session'
        ? { authenticated: true, username: 'alice', csrf_token: 'csrf-token' }
        : pathname === '/api/conversations' && options.method === 'POST'
          ? { id: conversationId, title: '新对话' }
          : pathname === '/api/conversations'
            ? [{ id: conversationId, title: '新对话', username: 'private@example.test' }]
          : pathname === `/api/conversations/${conversationId}/messages`
            ? [{ role: 'user', content: 'hello' }]
            : pathname === `/api/conversations/${conversationId}/models` && options.method === 'GET'
              ? { models: [{ model_id: 'deepseek/demo', context_mode: 'smart', message_ids: [] }] }
              : pathname === `/api/conversations/${conversationId}/models`
                ? { models: [] }
                : pathname === `/api/conversations/${conversationId}` && options.method === 'PUT'
                  ? { id: conversationId, title: 'Renamed' }
                  : { deleted: true, conversation_id: conversationId };
      return new Response(JSON.stringify(payload), {
        status: pathname === '/api/conversations' && options.method === 'POST' ? 201 : 200,
        headers: { 'content-type': 'application/json' },
      });
    },
  };
  const service = new CloudSessionService(fakeSession, 'http://127.0.0.1:5000', { production: false });

  await service.status();
  assert.deepEqual(await service.listConversations(), [{ id: conversationId, title: '新对话' }]);
  assert.deepEqual(await service.createConversation(), { id: conversationId, title: '新对话' });
  assert.deepEqual(await service.openConversation(conversationId), {
    id: conversationId,
    messages: [{ role: 'user', content: 'hello' }],
    model_settings: { models: [{ model_id: 'deepseek/demo', context_mode: 'smart', message_ids: [] }] },
  });
  assert.deepEqual(await service.replaceConversationModels(conversationId, { models: [] }), { models: [] });
  assert.deepEqual(await service.renameConversation(conversationId, 'Renamed'), { id: conversationId, title: 'Renamed' });
  assert.deepEqual(await service.deleteConversation(conversationId), { deleted: true, conversation_id: conversationId });

  assert.deepEqual(seen.map((request) => request.pathname), [
    '/api/desktop/session',
    '/api/conversations',
    '/api/conversations',
    `/api/conversations/${conversationId}/messages`,
    `/api/conversations/${conversationId}/models`,
    `/api/conversations/${conversationId}/models`,
    `/api/conversations/${conversationId}`,
    `/api/conversations/${conversationId}`,
  ]);
  for (const request of seen.slice(1).filter((request) => request.options.method !== 'GET')) {
    assert.equal(request.options.credentials, 'include');
    assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-token');
  }
});

test('workspace methods reject malformed conversation ids before issuing a request', async () => {
  let calls = 0;
  const service = new CloudSessionService({
    fetch: async () => { calls += 1; throw new Error('unexpected'); },
  }, 'http://127.0.0.1:5000', { production: false });

  await assert.rejects(service.openConversation('../outside'), /会话标识无效/);
  assert.equal(calls, 0);
});

test('workspace streaming relays only reviewed SSE event payloads through the existing bridge', async () => {
  const conversationId = 'b'.repeat(32);
  const chunks = [
    new TextEncoder().encode('event: token\ndata: {"model":"deepseek/demo","token":"hi"}\n\n'),
    new TextEncoder().encode('event: done\ndata: {"model":"deepseek/demo"}\n\n'),
  ];
  let streamOptions;
  const service = new CloudSessionService({
    fetch: async (url, options = {}) => {
      if (new URL(url).pathname === '/api/desktop/session') {
        return new Response(JSON.stringify({ authenticated: true, username: 'alice', csrf_token: 'csrf-token' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      streamOptions = options;
      return {
        ok: true,
        status: 200,
        body: {
          getReader: () => ({
            read: async () => (chunks.length
              ? { done: false, value: chunks.shift() }
              : { done: true, value: undefined }),
          }),
        },
      };
    },
  }, 'http://127.0.0.1:5000', { production: false });

  await service.status();
  const events = [];
  await service.streamWorkspace({
    message: 'hello',
    conversation_id: conversationId,
    models: [{ model_id: 'deepseek/demo', context_mode: 'smart', message_ids: [] }],
  }, (event) => events.push(event));

  assert.deepEqual(events, [
    { event: 'token', model: 'deepseek/demo', token: 'hi' },
    { event: 'done', model: 'deepseek/demo' },
  ]);
  assert.equal(streamOptions.headers['X-CSRF-Token'], 'csrf-token');
  assert.equal(streamOptions.credentials, 'include');
});

test('preload and main process expose only reviewed cloud IPC capabilities', () => {
  const sourceRoot = path.join(__dirname, '..', 'src');
  const preload = fs.readFileSync(path.join(sourceRoot, 'preload.cjs'), 'utf8');
  const main = fs.readFileSync(path.join(sourceRoot, 'main.cjs'), 'utf8');

  for (const channel of ['session:status', 'session:login', 'session:changed', 'cloud:bootstrap']) {
    assert.match(preload, new RegExp(channel));
  }
  assert.doesNotMatch(preload, /\bipcRenderer\s*:/);
  assert.doesNotMatch(preload, /\belectron\s*:/);
  assert.match(main, /persist:astrarelay-cloud/);
  assert.match(main, /senderFrame/);
  assert.match(main, /isTrustedRenderer/);
  assert.match(main, /setPermissionRequestHandler/);
});

test('login window shares only the persistent cloud session and cannot leave its origin', async () => {
  const changed = [];
  let createdWindow;
  class FakeBrowserWindow {
    constructor(options) {
      this.options = options;
      this.handlers = new Map();
      this.webHandlers = new Map();
      this.destroyed = false;
      this.webContents = {
        setWindowOpenHandler: (handler) => { this.windowOpenHandler = handler; },
        on: (name, handler) => { this.webHandlers.set(name, handler); },
      };
      createdWindow = this;
    }

    isDestroyed() { return this.destroyed; }
    once(name, handler) { this.handlers.set(name, handler); }
    on(name, handler) { this.handlers.set(name, handler); }
    async loadURL(url) { this.loadedUrl = url; }
    close() { this.destroyed = true; this.handlers.get('closed')?.(); }
    show() { this.shown = true; }
    focus() { this.focused = true; }
  }

  const service = new CloudSessionService(
    { fetch: async () => { throw new Error('unused'); } },
    'http://127.0.0.1:5000',
    {
      production: false,
      packaged: true,
      BrowserWindow: FakeBrowserWindow,
      onSessionChanged: (value) => changed.push(value),
    },
  );

  assert.deepEqual(await service.openLogin(null), { opened: true, already_open: false });
  assert.equal(createdWindow.options.webPreferences.partition, 'persist:astrarelay-cloud');
  assert.equal(createdWindow.options.webPreferences.nodeIntegration, false);
  assert.equal(createdWindow.options.webPreferences.contextIsolation, true);
  assert.equal(createdWindow.options.webPreferences.sandbox, true);
  assert.equal(createdWindow.options.webPreferences.allowRunningInsecureContent, false);
  assert.equal(createdWindow.options.webPreferences.devTools, false);
  assert.equal('preload' in createdWindow.options.webPreferences, false);
  assert.deepEqual(createdWindow.windowOpenHandler(), { action: 'deny' });

  let prevented = false;
  createdWindow.webHandlers.get('will-navigate')(
    { preventDefault: () => { prevented = true; } },
    'https://example.com/phishing',
  );
  assert.equal(prevented, true);

  createdWindow.webHandlers.get('did-navigate')(
    {},
    'http://127.0.0.1:5000/workspace',
  );
  assert.equal(createdWindow.destroyed, true);
  assert.deepEqual(changed, [{ authenticated: true }]);
});

test('failed login navigation closes the hidden window and returns a safe error', async () => {
  let createdWindow;
  class FailingBrowserWindow {
    constructor() {
      this.destroyed = false;
      this.webContents = {
        setWindowOpenHandler: () => {},
        on: () => {},
      };
      createdWindow = this;
    }

    isDestroyed() { return this.destroyed; }
    once() {}
    on() {}
    async loadURL() { throw new Error('private navigation failure'); }
    close() { this.destroyed = true; }
  }
  const service = new CloudSessionService(
    { fetch: async () => { throw new Error('unused'); } },
    'http://127.0.0.1:5000',
    { production: false, BrowserWindow: FailingBrowserWindow },
  );

  await assert.rejects(
    service.openLogin(null),
    (error) => error.message === '暂时无法打开登录页面',
  );
  assert.equal(createdWindow.destroyed, true);
});

test('closing login without authentication notifies the renderer to leave waiting state', async () => {
  const changed = [];
  let createdWindow;
  class ClosingBrowserWindow {
    constructor() {
      this.handlers = new Map();
      this.destroyed = false;
      this.webContents = {
        setWindowOpenHandler: () => {},
        on: () => {},
      };
      createdWindow = this;
    }

    isDestroyed() { return this.destroyed; }
    once(name, handler) { this.handlers.set(name, handler); }
    on(name, handler) { this.handlers.set(name, handler); }
    async loadURL() {}
    close() { this.destroyed = true; this.handlers.get('closed')?.(); }
  }
  const service = new CloudSessionService(
    { fetch: async () => { throw new Error('unused'); } },
    'http://127.0.0.1:5000',
    {
      production: false,
      BrowserWindow: ClosingBrowserWindow,
      onSessionChanged: (value) => changed.push(value),
    },
  );

  await service.openLogin(null);
  createdWindow.close();
  assert.deepEqual(changed, [{ authenticated: false }]);
});
