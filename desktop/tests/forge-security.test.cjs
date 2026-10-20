const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

const config = require('../forge.config.cjs');
const packageMetadata = require('../package.json');
const { FuseV1Options } = require('@electron/fuses');

test('Windows installer has the public package metadata required by Squirrel', () => {
  assert.equal(packageMetadata.author, 'AstraRelay');
});

test('packaging enables ASAR and one Windows maker', () => {
  assert.equal(config.packagerConfig.asar, true);
  assert.equal(config.packagerConfig.name, 'AstraRelay');
  assert.equal(config.packagerConfig.executableName, 'AstraRelay');
  assert.equal(config.packagerConfig.arch, 'x64');
  assert.equal(config.makers.length, 1);
  assert.equal(config.makers[0].name, '@electron-forge/maker-squirrel');
  assert.deepEqual(config.makers[0].platforms, ['win32']);
  assert.equal(config.makers[0].config.setupExe, 'AstraRelay-0.1.0 Setup.exe');
});

test('fuses disable Node entry points and require packaged ASAR', () => {
  const fuses = config.plugins.find(
    (plugin) => plugin.name === 'fuses' && plugin.constructor.name === 'FusesPlugin',
  );
  assert.ok(fuses);
  assert.equal(fuses.config[FuseV1Options.RunAsNode], false);
  assert.equal(fuses.config[FuseV1Options.EnableNodeOptionsEnvironmentVariable], false);
  assert.equal(fuses.config[FuseV1Options.EnableNodeCliInspectArguments], false);
  assert.equal(fuses.config[FuseV1Options.EnableCookieEncryption], true);
  assert.equal(fuses.config[FuseV1Options.EnableEmbeddedAsarIntegrityValidation], true);
  assert.equal(fuses.config[FuseV1Options.OnlyLoadAppFromAsar], true);
  assert.equal(fuses.config[FuseV1Options.GrantFileProtocolExtraPrivileges], false);
});

test('Electron 43 fuse wire is fully configured by the strict packaging hook', () => {
  const hook = config.hooks && config.hooks.packageAfterCopy;
  assert.equal(typeof hook, 'function');
  assert.deepEqual(hook.strictFuseStates, {
    RunAsNode: false,
    EnableCookieEncryption: true,
    EnableNodeOptionsEnvironmentVariable: false,
    EnableNodeCliInspectArguments: false,
    EnableEmbeddedAsarIntegrityValidation: true,
    OnlyLoadAppFromAsar: true,
    LoadBrowserProcessSpecificV8Snapshot: false,
    GrantFileProtocolExtraPrivileges: false,
    WasmTrapHandlers: false,
  });
});

test('verified local Electron archives can be selected without changing runtime config', () => {
  const cacheDir = path.resolve('desktop/.verified-electron-cache');
  const output = execFileSync(
    process.execPath,
    ['-e', "console.log(require('./desktop/forge.config.cjs').packagerConfig.electronZipDir)"],
    {
      cwd: path.resolve(__dirname, '..', '..'),
      encoding: 'utf8',
      env: { ...process.env, ASTRARELAY_ELECTRON_ZIP_DIR: cacheDir },
    },
  ).trim();
  assert.equal(output, cacheDir);
});
