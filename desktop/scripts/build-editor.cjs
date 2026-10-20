const fs = require('node:fs');
const path = require('node:path');
const esbuild = require('esbuild');

const desktopRoot = path.join(__dirname, '..');
const outfile = path.join(desktopRoot, 'src', 'renderer', 'vendor', 'editor.bundle.js');

fs.mkdirSync(path.dirname(outfile), { recursive: true });

esbuild.buildSync({
  entryPoints: [path.join(desktopRoot, 'src', 'renderer', 'editor-entry.js')],
  bundle: true,
  format: 'iife',
  globalName: 'AstraRelayEditor',
  target: ['chrome142'],
  outfile,
  legalComments: 'none',
});

const bundle = fs.readFileSync(outfile, 'utf8');
fs.writeFileSync(outfile, bundle.replace(/[ \t]+$/gm, ''));
