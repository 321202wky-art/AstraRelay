const path = require('node:path');
const { pathToFileURL } = require('node:url');
const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  net,
  protocol,
  session,
} = require('electron');

if (require('electron-squirrel-startup')) app.quit();

const { CloudSessionService } = require('./cloud-session.cjs');
const { ProjectService } = require('./project-service.cjs');
const {
  createWindowOptions,
  resolveAppAsset,
  isTrustedRenderer,
} = require('./security.cjs');

protocol.registerSchemesAsPrivileged([{
  scheme: 'astrarelay',
  privileges: {
    standard: true,
    secure: true,
    supportFetchAPI: true,
    stream: true,
  },
}]);

let mainWindow = null;
let cloudService = null;
const projectService = new ProjectService();

function registerTrustedHandler(channel, handler) {
  ipcMain.handle(channel, async (event, ...args) => {
    const senderUrl = event.senderFrame ? event.senderFrame.url : '';
    if (!isTrustedRenderer(senderUrl)) throw new Error('请求来源无效');
    return handler(event, ...args);
  });
}

function createMainWindow() {
  const preloadPath = path.join(__dirname, 'preload.cjs');
  const rendererRoot = path.join(__dirname, 'renderer');
  const window = new BrowserWindow(createWindowOptions(preloadPath, {
    packaged: app.isPackaged,
  }));

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, targetUrl) => {
    if (!isTrustedRenderer(targetUrl)) event.preventDefault();
  });
  window.once('ready-to-show', () => window.show());
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null;
  });

  window.loadURL('astrarelay://app/index.html');
  mainWindow = window;
  return window;
}

app.whenReady().then(async () => {
  const rendererRoot = path.join(__dirname, 'renderer');
  await protocol.handle('astrarelay', (request) => {
    try {
      const assetPath = resolveAppAsset(rendererRoot, request.url);
      return net.fetch(pathToFileURL(assetPath).toString());
    } catch {
      return new Response('Not found', {
        status: 404,
        headers: { 'content-type': 'text/plain; charset=utf-8' },
      });
    }
  });

  const cloudPartitionName = 'persist:astrarelay-cloud';
  const cloudPartition = session.fromPartition(cloudPartitionName);
  cloudPartition.setPermissionCheckHandler(() => false);
  cloudPartition.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  cloudPartition.on('will-download', (event) => event.preventDefault());

  const environmentBaseUrl = process.env.ASTRARELAY_BASE_URL;
  const configuredBaseUrl = environmentBaseUrl || 'http://127.0.0.1:5000';
  cloudService = new CloudSessionService(cloudPartition, configuredBaseUrl, {
    production: app.isPackaged && Boolean(environmentBaseUrl),
    packaged: app.isPackaged,
    BrowserWindow,
    partition: cloudPartitionName,
    onSessionChanged: (value) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('session:changed', value);
      }
    },
  });

  registerTrustedHandler('session:status', () => cloudService.status());
  registerTrustedHandler('session:login', () => cloudService.openLogin(mainWindow));
  registerTrustedHandler('cloud:bootstrap', () => cloudService.bootstrap());
  registerTrustedHandler('project:choose-root', async () => {
    const selected = await dialog.showOpenDialog(mainWindow, {
      title: '选择 AstraRelay 本地项目',
      properties: ['openDirectory', 'createDirectory'],
    });
    if (selected.canceled || selected.filePaths.length !== 1) {
      return { ...projectService.status(), changed: false };
    }
    return { ...(await projectService.authorizeRoot(selected.filePaths[0])), changed: true };
  });
  registerTrustedHandler('project:status', () => projectService.status());
  registerTrustedHandler('project:list-tree', () => projectService.listTree());
  registerTrustedHandler('project:search', (_event, query) => projectService.search(query));
  registerTrustedHandler('project:preview', (_event, relativePath) => projectService.preview(relativePath));
  registerTrustedHandler('project:write-text', (_event, relativePath, content) => (
    projectService.writeText(relativePath, content)
  ));
  registerTrustedHandler('workspace:list-conversations', () => cloudService.listConversations());
  registerTrustedHandler('workspace:open-conversation', (_event, conversationId) => cloudService.openConversation(conversationId));
  registerTrustedHandler('workspace:create-conversation', () => cloudService.createConversation());
  registerTrustedHandler('workspace:rename-conversation', (_event, conversationId, title) => (
    cloudService.renameConversation(conversationId, title)
  ));
  registerTrustedHandler('workspace:delete-conversation', (_event, conversationId) => (
    cloudService.deleteConversation(conversationId)
  ));
  registerTrustedHandler('workspace:replace-models', (_event, conversationId, settings) => (
    cloudService.replaceConversationModels(conversationId, settings)
  ));
  registerTrustedHandler('workspace:stream', async (event, requestId, payload) => {
    if (typeof requestId !== 'string' || !/^[a-f0-9]{32}$/.test(requestId)) {
      throw new Error('invalid workspace request');
    }
    const sender = event.sender;
    return cloudService.streamWorkspace(payload, (streamEvent) => {
      if (!sender.isDestroyed()) {
        sender.send('workspace:stream-event', {
          request_id: requestId,
          conversation_id: payload && payload.conversation_id,
          ...streamEvent,
        });
      }
    });
  });

  createMainWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
