const path = require('node:path');

const APP_SCHEME = 'astrarelay:';
const APP_HOST = 'app';

function createWindowOptions(preloadPath, options = {}) {
  const packaged = Boolean(options.packaged);
  return {
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    backgroundColor: '#f6f7fb',
    webPreferences: {
      preload: preloadPath,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      devTools: !packaged,
    },
  };
}

function decodePath(rawPath) {
  let decoded = rawPath;

  for (let pass = 0; pass < 5; pass += 1) {
    let next;
    try {
      next = decodeURIComponent(decoded);
    } catch {
      throw new Error('invalid app asset');
    }
    if (next === decoded) return decoded;
    decoded = next;
  }

  if (/%[0-9a-f]{2}/i.test(decoded)) throw new Error('invalid app asset');
  return decoded;
}

function resolveAppAsset(rendererRoot, requestUrl) {
  let url;
  try {
    url = new URL(requestUrl);
  } catch {
    throw new Error('invalid app URL');
  }

  if (
    url.protocol !== APP_SCHEME
    || url.hostname !== APP_HOST
    || url.username
    || url.password
    || url.port
  ) {
    throw new Error('invalid app URL');
  }

  const authorityStart = requestUrl.indexOf('://') + 3;
  const pathStart = requestUrl.indexOf('/', authorityStart);
  const rawPath = pathStart === -1
    ? ''
    : requestUrl.slice(pathStart).split(/[?#]/, 1)[0];
  const decodedPath = decodePath(rawPath);

  if (decodedPath.includes('\\') || decodedPath.includes('\0')) {
    throw new Error('invalid app asset');
  }

  const segments = decodedPath.replace(/^\/+/, '').split('/');
  if (segments.some((segment) => segment === '.' || segment === '..')) {
    throw new Error('invalid app asset');
  }

  const relative = segments.filter(Boolean).join('/') || 'index.html';
  const resolvedRoot = path.resolve(rendererRoot);
  const resolved = path.resolve(resolvedRoot, relative);
  if (resolved !== resolvedRoot && !resolved.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error('invalid app asset');
  }
  return resolved;
}

function isTrustedRenderer(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === APP_SCHEME
      && url.hostname === APP_HOST
      && !url.username
      && !url.password
      && !url.port;
  } catch {
    return false;
  }
}

module.exports = {
  createWindowOptions,
  resolveAppAsset,
  isTrustedRenderer,
};
