const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('astraDesktop', Object.freeze({
  runtime: () => Object.freeze({ platform: process.platform }),
  session: Object.freeze({
    status: () => ipcRenderer.invoke('session:status'),
    login: () => ipcRenderer.invoke('session:login'),
    onChanged: (listener) => {
      if (typeof listener !== 'function') throw new TypeError('listener must be a function');
      const wrapped = (_event, value) => listener(value);
      ipcRenderer.on('session:changed', wrapped);
      return () => ipcRenderer.removeListener('session:changed', wrapped);
    },
  }),
  cloud: Object.freeze({
    bootstrap: () => ipcRenderer.invoke('cloud:bootstrap'),
  }),
  project: Object.freeze({
    chooseRoot: () => ipcRenderer.invoke('project:choose-root'),
    status: () => ipcRenderer.invoke('project:status'),
    listTree: () => ipcRenderer.invoke('project:list-tree'),
    search: (query) => ipcRenderer.invoke('project:search', query),
    preview: (relativePath) => ipcRenderer.invoke('project:preview', relativePath),
    writeText: (relativePath, content) => ipcRenderer.invoke('project:write-text', relativePath, content),
  }),
  workspace: Object.freeze({
    listConversations: () => ipcRenderer.invoke('workspace:list-conversations'),
    openConversation: (conversationId) => ipcRenderer.invoke('workspace:open-conversation', conversationId),
    createConversation: () => ipcRenderer.invoke('workspace:create-conversation'),
    renameConversation: (conversationId, title) => (
      ipcRenderer.invoke('workspace:rename-conversation', conversationId, title)
    ),
    deleteConversation: (conversationId) => ipcRenderer.invoke('workspace:delete-conversation', conversationId),
    replaceModels: (conversationId, settings) => (
      ipcRenderer.invoke('workspace:replace-models', conversationId, settings)
    ),
    stream: (requestId, payload) => ipcRenderer.invoke('workspace:stream', requestId, payload),
    onStreamEvent: (listener) => {
      if (typeof listener !== 'function') throw new TypeError('listener must be a function');
      const wrapped = (_event, value) => listener(value);
      ipcRenderer.on('workspace:stream-event', wrapped);
      return () => ipcRenderer.removeListener('workspace:stream-event', wrapped);
    },
  }),
}));
