const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('desktop package pins the reviewed Electron toolchain', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
  assert.equal(pkg.main, 'src/main.cjs');
  assert.equal(pkg.devDependencies.electron, '43.2.0');
  assert.equal(pkg.devDependencies['@electron-forge/cli'], '7.11.2');
  assert.equal(pkg.devDependencies['@electron-forge/maker-squirrel'], '7.11.2');
  assert.equal(pkg.devDependencies['@electron-forge/plugin-fuses'], '7.11.2');
  assert.equal(pkg.devDependencies['@electron/fuses'], '1.8.0');
  assert.equal(pkg.devDependencies['@electron/fuses-modern'], 'npm:@electron/fuses@2.1.3');
  assert.equal(pkg.devDependencies.esbuild, '0.25.0');
  assert.equal(pkg.dependencies['@codemirror/commands'], '6.8.1');
  assert.equal(pkg.dependencies['@codemirror/lang-javascript'], '6.2.3');
  assert.equal(pkg.dependencies['@codemirror/state'], '6.5.2');
  assert.equal(pkg.dependencies['@codemirror/view'], '6.36.5');
  assert.equal(pkg.dependencies['electron-squirrel-startup'], '1.0.1');
  assert.equal(pkg.scripts['build:editor'], 'node scripts/build-editor.cjs');
  assert.equal(pkg.scripts.test, 'node --test tests/*.test.cjs');
  assert.equal(pkg.scripts.prestart, 'npm run build:editor');
  assert.equal(pkg.scripts.start, 'electron-forge start');
  assert.equal(pkg.scripts.prepackage, 'npm run build:editor && node scripts/check-packaging-node.cjs');
  assert.equal(pkg.scripts.package, 'electron-forge package --arch=x64');
  assert.equal(pkg.scripts.premake, 'node scripts/check-packaging-node.cjs');
  assert.equal(pkg.scripts.make, 'electron-forge make --arch=x64');
});

test('desktop package lock pins the reviewed editor build dependencies', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
  const lock = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package-lock.json'), 'utf8'));
  const reviewedDependencies = [
    ['dependencies', '@codemirror/commands', '6.8.1'],
    ['dependencies', '@codemirror/lang-javascript', '6.2.3'],
    ['dependencies', '@codemirror/state', '6.5.2'],
    ['dependencies', '@codemirror/view', '6.36.5'],
    ['devDependencies', 'esbuild', '0.25.0'],
  ];

  assert.equal(lock.lockfileVersion, 3);
  for (const [section, name, version] of reviewedDependencies) {
    assert.equal(pkg[section][name], version, `package.json must pin ${name}`);
    assert.equal(lock.packages[''][section][name], version, `package-lock.json root must pin ${name}`);
    assert.equal(lock.packages[`node_modules/${name}`].version, version, `package-lock.json must resolve ${name}`);
  }
});

test('Windows packaging accepts the verified Node 22 runtime and rejects Node 24', () => {
  const { supportsPackagingNode } = require('../scripts/check-packaging-node.cjs');
  assert.equal(supportsPackagingNode('22.23.2'), true);
  assert.equal(supportsPackagingNode('22.23.1'), false);
  assert.equal(supportsPackagingNode('24.17.0'), false);
  assert.equal(supportsPackagingNode('invalid'), false);
});

test('desktop main process handles Squirrel lifecycle events before normal startup', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'main.cjs'), 'utf8');
  const squirrelHandler = "if (require('electron-squirrel-startup')) app.quit();";
  const handlerIndex = source.indexOf(squirrelHandler);
  assert.notEqual(handlerIndex, -1);
  assert.ok(handlerIndex < source.indexOf('protocol.registerSchemesAsPrivileged'));
  assert.ok(handlerIndex < source.indexOf('app.whenReady()'));
});

test('desktop dependency overrides pin the reviewed secure archive and temp packages', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
  const lock = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package-lock.json'), 'utf8'));

  assert.deepEqual(pkg.overrides, {
    tar: '7.5.22',
    tmp: '0.2.7',
  });
  assert.equal(lock.packages['node_modules/tar'].version, '7.5.22');
  assert.equal(lock.packages['node_modules/tmp'].version, '0.2.7');
});
