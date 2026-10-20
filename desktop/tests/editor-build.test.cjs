const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const renderer = path.join(__dirname, '..', 'src', 'renderer');

test('editor bundle is local and exposes only the editor facade', () => {
  const bundle = fs.readFileSync(path.join(renderer, 'vendor', 'editor.bundle.js'), 'utf8');
  assert.match(bundle, /AstraRelayEditor/);
  assert.doesNotMatch(bundle, /\bfetch\s*\(|XMLHttpRequest|require\s*\(/);
});

test('renderer loads the local editor bundle before the application', () => {
  const html = fs.readFileSync(path.join(renderer, 'index.html'), 'utf8');
  const editorBundle = '<script src="vendor/editor.bundle.js" defer></script>';
  const application = '<script src="app.js" defer></script>';
  const editorIndex = html.indexOf(editorBundle);
  const applicationIndex = html.indexOf(application);

  assert.notEqual(editorIndex, -1, 'index.html must load the local editor bundle');
  assert.notEqual(applicationIndex, -1, 'index.html must load app.js');
  assert.ok(editorIndex < applicationIndex, 'the editor bundle must load before app.js');
});

test('editor entry exposes only the mount facade and its four methods', () => {
  const source = fs.readFileSync(path.join(renderer, 'editor-entry.js'), 'utf8');
  const returnIndex = source.indexOf('  return {');

  assert.match(source, /^export function mount\(/m);
  assert.doesNotMatch(source, /^export\s+(?!function\s+mount\b)/m);
  assert.notEqual(returnIndex, -1, 'mount must return an editor facade');

  const facadeMethods = Array.from(
    source.slice(returnIndex).matchAll(/^ {4}([A-Za-z_$][\w$]*):/gm),
    ([, name]) => name,
  );
  assert.deepEqual(facadeMethods, ['getValue', 'setValue', 'focus', 'destroy']);
});

test('editor bundle excludes IPC and Node runtime capabilities', () => {
  const bundle = fs.readFileSync(path.join(renderer, 'vendor', 'editor.bundle.js'), 'utf8');
  const forbiddenCapabilities = [
    [/\b(?:ipcRenderer|ipcMain|contextBridge)\b/, 'Electron IPC or preload API'],
    [/\b(?:electron|child_process)\b/i, 'Electron or child-process module'],
    [/\bnode:(?:assert|buffer|child_process|cluster|crypto|dgram|dns|fs|http|https|module|net|os|path|process|readline|stream|tls|util|worker_threads)\b/i, 'Node built-in module specifier'],
    [/\b(?:window|globalThis|global)\s*\.\s*(?:require|process|Buffer)\b/, 'Node global bridge'],
    [/\bprocess\s*\.\s*(?:binding|dlopen|exit|cwd|getBuiltinModule|mainModule|versions)\b/, 'Node process capability'],
    [/\bmodule\s*\.\s*exports\b|\bexports\s*\./, 'CommonJS module export'],
    [/\b(?:__dirname|__filename)\b/, 'Node path global'],
    [/\bBuffer\s*\.\s*(?:alloc|allocUnsafe|concat|from)\b/, 'Node Buffer constructor'],
  ];

  for (const [pattern, description] of forbiddenCapabilities) {
    assert.doesNotMatch(bundle, pattern, `editor bundle must not contain ${description}`);
  }
});
