const { FusesPlugin } = require('@electron-forge/plugin-fuses');
const { FuseVersion, FuseV1Options } = require('@electron/fuses');
const path = require('node:path');

const electronZipDir = process.env.ASTRARELAY_ELECTRON_ZIP_DIR;
const strictFuseStates = Object.freeze({
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

async function applyStrictElectron43Fuses(
  _config,
  buildPath,
  _electronVersion,
  platform,
) {
  if (platform !== 'win32') throw new Error('AstraRelay checkpoint packaging is Windows-only');
  const modernFuses = await import('@electron/fuses-modern');
  const fuseConfig = {
    version: modernFuses.FuseVersion.V1,
    strictlyRequireAllFuses: true,
  };
  for (const [name, state] of Object.entries(strictFuseStates)) {
    const fuseIndex = modernFuses.FuseV1Options[name];
    if (!Number.isInteger(fuseIndex)) throw new Error(`Unsupported Electron fuse: ${name}`);
    fuseConfig[fuseIndex] = state;
  }
  const executablePath = path.resolve(buildPath, '..', '..', 'electron.exe');
  await modernFuses.flipFuses(executablePath, fuseConfig);
}
applyStrictElectron43Fuses.strictFuseStates = strictFuseStates;

module.exports = {
  packagerConfig: {
    asar: true,
    name: 'AstraRelay',
    executableName: 'AstraRelay',
    arch: 'x64',
    ...(electronZipDir ? { electronZipDir: path.resolve(electronZipDir) } : {}),
  },
  rebuildConfig: {},
  makers: [{
    name: '@electron-forge/maker-squirrel',
    platforms: ['win32'],
    config: {
      name: 'AstraRelay',
      setupExe: 'AstraRelay-0.1.0 Setup.exe',
    },
  }],
  hooks: {
    packageAfterCopy: applyStrictElectron43Fuses,
  },
  plugins: [new FusesPlugin({
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
    [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
  })],
};
