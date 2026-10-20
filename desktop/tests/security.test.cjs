const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
  createWindowOptions,
  resolveAppAsset,
  isTrustedRenderer,
} = require('../src/security.cjs');

test('main window keeps renderer sandboxed and isolated', () => {
  const options = createWindowOptions('C:\\app\\preload.cjs', { packaged: true });

  assert.equal(options.webPreferences.nodeIntegration, false);
  assert.equal(options.webPreferences.contextIsolation, true);
  assert.equal(options.webPreferences.sandbox, true);
  assert.equal(options.webPreferences.webSecurity, true);
  assert.equal(options.webPreferences.allowRunningInsecureContent, false);
  assert.equal(options.webPreferences.devTools, false);
  assert.equal(
    createWindowOptions('C:\\app\\preload.cjs', { packaged: false }).webPreferences.devTools,
    true,
  );
});

test('app protocol resolves packaged assets but rejects traversal', () => {
  const root = path.resolve('desktop/src/renderer');

  assert.equal(
    resolveAppAsset(root, 'astrarelay://app/index.html'),
    path.join(root, 'index.html'),
  );
  assert.throws(
    () => resolveAppAsset(root, 'astrarelay://app/%2e%2e/preload.cjs'),
    /invalid app asset/,
  );
  assert.throws(
    () => resolveAppAsset(root, 'astrarelay://app/%252e%252e/preload.cjs'),
    /invalid app asset/,
  );
  assert.throws(
    () => resolveAppAsset(root, 'astrarelay://app/assets%5c..%5cpreload.cjs'),
    /invalid app asset/,
  );
  assert.throws(
    () => resolveAppAsset(root, 'https://example.com/index.html'),
    /invalid app URL/,
  );
});

test('only the packaged renderer is a trusted IPC sender', () => {
  assert.equal(isTrustedRenderer('astrarelay://app/index.html'), true);
  assert.equal(isTrustedRenderer('astrarelay://app/assets/app.js'), true);
  assert.equal(isTrustedRenderer('astrarelay://app.evil/index.html'), false);
  assert.equal(isTrustedRenderer('https://example.com/'), false);
  assert.equal(isTrustedRenderer('file:///tmp/index.html'), false);
});
