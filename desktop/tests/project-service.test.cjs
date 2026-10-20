const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');

const {
  ProjectService,
  ProjectError,
  LIMITS,
  entryTypeForLstat,
} = require('../src/project-service.cjs');

async function makeProject(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'astrarelay-project-'));
  t.after(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });
  return root;
}

async function createJunction(linkPath, targetPath) {
  await fs.symlink(targetPath, linkPath, 'junction');
}

async function assertSafeProjectError(operation, forbiddenPaths) {
  await assert.rejects(operation, (error) => {
    assert.ok(error instanceof ProjectError);
    assert.equal(error.message, 'project filesystem operation failed');
    for (const forbiddenPath of forbiddenPaths) {
      assert.equal(error.message.includes(forbiddenPath), false);
    }
    return true;
  });
}

test('requires explicit run-lifetime authorization before project access', async (t) => {
  const root = await makeProject(t);
  const service = new ProjectService();

  assert.deepEqual(service.status(), { authorized: false });
  await assert.rejects(() => service.listTree(), /not authorized/);
  await assert.rejects(() => service.search('hello'), /not authorized/);
  await assert.rejects(() => service.preview('safe.txt'), /not authorized/);
  await assert.rejects(() => service.writeText('safe.txt', 'hello'), /not authorized/);

  await service.authorizeRoot(root);

  assert.deepEqual(service.status(), {
    authorized: true,
    rootName: path.basename(root),
  });
});

test('rejects traversal, protected files, binary data, and links leaving the root', async (t) => {
  const root = await makeProject(t);
  await fs.writeFile(path.join(root, 'safe.txt'), 'hello');
  await fs.writeFile(path.join(root, '.env'), 'SECRET=value');
  await fs.writeFile(path.join(root, 'image.bin'), Buffer.from([0, 1, 2]));
  const outsideDir = await fs.mkdtemp(path.join(os.tmpdir(), 'astrarelay-outside-'));
  t.after(async () => {
    await fs.rm(outsideDir, { recursive: true, force: true });
  });
  await fs.writeFile(path.join(outsideDir, 'outside.txt'), 'outside');
  const linkPath = path.join(root, 'outside-link');
  await createJunction(linkPath, outsideDir);
  const service = new ProjectService();

  await service.authorizeRoot(root);

  await assert.rejects(() => service.preview('../safe.txt'), /outside project/);
  await assert.rejects(() => service.preview('.env'), /protected file/);
  await assert.rejects(() => service.preview('image.bin'), /not a text file/);
  await assert.rejects(() => service.preview('outside-link/outside.txt'), /reparse point/);
});

test('converts filesystem failures to redacted ProjectError messages', async (t) => {
  const root = await makeProject(t);
  const missingRoot = path.join(root, 'missing-root');
  const service = new ProjectService();

  await assertSafeProjectError(() => service.authorizeRoot(missingRoot), [root, missingRoot]);

  await service.authorizeRoot(root);
  await fs.rm(root, { recursive: true, force: true });
  await assertSafeProjectError(() => service.listTree(), [root]);
});

test('classifies only normal non-reparse lstat entries as accessible', () => {
  const normalFile = {
    isSymbolicLink: () => false,
    isFile: () => true,
    isDirectory: () => false,
  };
  const normalDirectory = {
    isSymbolicLink: () => false,
    isFile: () => false,
    isDirectory: () => true,
  };
  const symbolicLink = {
    isSymbolicLink: () => true,
    isFile: () => false,
    isDirectory: () => false,
  };
  const unclassifiedReparseLikeEntry = {
    isSymbolicLink: () => false,
    isFile: () => false,
    isDirectory: () => false,
  };

  assert.equal(entryTypeForLstat(normalFile), 'file');
  assert.equal(entryTypeForLstat(normalDirectory), 'directory');
  assert.equal(entryTypeForLstat(symbolicLink), null);
  assert.equal(entryTypeForLstat(unclassifiedReparseLikeEntry), null);
});

test('skips supported symbolic links as well as Windows junctions', async (t) => {
  const root = await makeProject(t);
  const target = path.join(root, 'target');
  await fs.mkdir(target);
  await fs.writeFile(path.join(target, 'safe.txt'), 'safe');
  await createJunction(path.join(root, 'junction'), target);

  const symbolicLink = path.join(root, 'symbolic-link');
  try {
    await fs.symlink(target, symbolicLink, 'dir');
  } catch (error) {
    if (error && (error.code === 'EPERM' || error.code === 'EACCES')) {
      t.diagnostic('Directory symlink creation is unavailable in this Windows test environment.');
    } else {
      throw error;
    }
  }

  const service = new ProjectService();
  await service.authorizeRoot(root);
  const tree = await service.listTree();

  assert.equal(tree.entries.some((entry) => entry.path === 'junction'), false);
  assert.equal(tree.entries.some((entry) => entry.path === 'symbolic-link'), false);
  await assert.rejects(() => service.preview('junction/safe.txt'), /reparse point/);
});

test('listTree returns only bounded safe relative metadata and skips protected entries', async (t) => {
  const root = await makeProject(t);
  await fs.mkdir(path.join(root, 'src', 'nested'), { recursive: true });
  await fs.writeFile(path.join(root, 'src', 'nested', 'safe.txt'), 'hello');
  await fs.writeFile(path.join(root, 'secret-token.txt'), 'blocked');
  await fs.mkdir(path.join(root, '.git'));
  await fs.writeFile(path.join(root, '.git', 'config'), 'blocked');
  await fs.writeFile(path.join(root, '.env.production'), 'blocked');
  await createJunction(path.join(root, 'src-link'), path.join(root, 'src', 'nested'));
  const service = new ProjectService();

  await service.authorizeRoot(root);
  const tree = await service.listTree();

  assert.equal(tree.truncated, false);
  assert.equal(tree.rootName, path.basename(root));
  assert.equal(tree.entries.some((entry) => entry.path.includes(root)), false);
  assert.deepEqual(
    tree.entries.map((entry) => entry.path).sort(),
    ['src', 'src/nested', 'src/nested/safe.txt'],
  );
  assert.ok(tree.entries.every((entry) => entry.depth <= LIMITS.treeDepth));
  assert.ok(tree.entries.every((entry) => entry.type === 'directory' || entry.type === 'file'));
});

test('search honors protected-name filters, relative results, and result limits', async (t) => {
  const root = await makeProject(t);
  const service = new ProjectService();
  await fs.writeFile(path.join(root, 'match.txt'), 'alpha beta\ngamma alpha');
  await fs.writeFile(path.join(root, 'credential-notes.txt'), 'alpha should never appear');

  for (let index = 0; index < LIMITS.searchResults + 5; index += 1) {
    await fs.writeFile(path.join(root, `file-${index}.txt`), `alpha result ${index}`);
  }

  await service.authorizeRoot(root);
  const results = await service.search('alpha');

  assert.equal(results.results.length, LIMITS.searchResults);
  assert.equal(results.truncated, true);
  assert.ok(results.results.every((entry) => !entry.path.includes(root)));
  assert.ok(results.results.every((entry) => entry.path !== 'credential-notes.txt'));
  assert.ok(results.results.every((entry) => typeof entry.line === 'number'));
  assert.ok(results.results.every((entry) => entry.preview.toLowerCase().includes('alpha')));
});

test('listTree stops at the configured entry cap', async (t) => {
  const root = await makeProject(t);
  for (let index = 0; index < LIMITS.treeEntries + 1; index += 1) {
    await fs.writeFile(path.join(root, `tree-${String(index).padStart(4, '0')}.txt`), 'safe');
  }
  const service = new ProjectService();

  await service.authorizeRoot(root);
  const tree = await service.listTree();

  assert.equal(tree.entries.length, LIMITS.treeEntries);
  assert.equal(tree.truncated, true);
});

test('search stops before reading a file beyond the configured scan cap', async (t) => {
  const root = await makeProject(t);
  for (let index = 0; index < LIMITS.searchFiles; index += 1) {
    await fs.writeFile(path.join(root, `scan-${String(index).padStart(4, '0')}.txt`), 'safe');
  }
  await fs.writeFile(path.join(root, 'zz-beyond-scan-cap.txt'), 'needle beyond scan cap');
  const service = new ProjectService();

  await service.authorizeRoot(root);
  const results = await service.search('needle');

  assert.deepEqual(results.results, []);
  assert.equal(results.truncated, true);
});

test('search reports truncation and omits matches beyond the per-file byte cap', async (t) => {
  const root = await makeProject(t);
  const content = Buffer.concat([
    Buffer.alloc(LIMITS.searchBytes, 'a'),
    Buffer.from('needle beyond byte cap'),
  ]);
  await fs.writeFile(path.join(root, 'large-search.txt'), content);
  const service = new ProjectService();

  await service.authorizeRoot(root);
  const results = await service.search('needle');

  assert.deepEqual(results.results, []);
  assert.equal(results.truncated, true);
});

test('preview enforces text decoding and preview truncation without exposing absolute paths', async (t) => {
  const root = await makeProject(t);
  const service = new ProjectService();
  const content = Array.from({ length: LIMITS.previewLines + 20 }, (_, index) => `line-${index}`).join('\n');
  await fs.writeFile(path.join(root, 'long.txt'), content, 'utf8');

  await service.authorizeRoot(root);
  const preview = await service.preview('long.txt');

  assert.equal(preview.path, 'long.txt');
  assert.equal(preview.truncated, true);
  assert.equal(preview.content.includes(root), false);
  assert.equal(preview.lineCount, LIMITS.previewLines);
  assert.equal(preview.content.split('\n').length, LIMITS.previewLines);
});

test('preview reads exactly the configured byte boundary and truncates one byte beyond it', async (t) => {
  const root = await makeProject(t);
  const exactContent = 'a'.repeat(LIMITS.previewBytes);
  const overLimitContent = `${exactContent}b`;
  await fs.writeFile(path.join(root, 'exact.txt'), exactContent, 'utf8');
  await fs.writeFile(path.join(root, 'over-limit.txt'), overLimitContent, 'utf8');
  const service = new ProjectService();

  await service.authorizeRoot(root);
  const exact = await service.preview('exact.txt');
  const overLimit = await service.preview('over-limit.txt');

  assert.equal(exact.content.length, LIMITS.previewBytes);
  assert.equal(exact.truncated, false);
  assert.equal(overLimit.content.length, LIMITS.previewBytes);
  assert.equal(overLimit.truncated, true);
});

test('writeText allows only existing normal text files within size limits', async (t) => {
  const root = await makeProject(t);
  const service = new ProjectService();
  await fs.writeFile(path.join(root, 'editable.txt'), 'before', 'utf8');
  await fs.writeFile(path.join(root, 'blob.bin'), Buffer.from([0, 1, 2]));
  const linkedDir = await fs.mkdtemp(path.join(os.tmpdir(), 'astrarelay-write-link-'));
  t.after(async () => {
    await fs.rm(linkedDir, { recursive: true, force: true });
  });
  await fs.writeFile(path.join(linkedDir, 'editable.txt'), 'before', 'utf8');
  await createJunction(path.join(root, 'editable-link'), linkedDir);

  await service.authorizeRoot(root);

  const result = await service.writeText('editable.txt', 'after');
  assert.deepEqual(result, { path: 'editable.txt', bytesWritten: 5 });
  assert.equal(await fs.readFile(path.join(root, 'editable.txt'), 'utf8'), 'after');

  await assert.rejects(() => service.writeText('missing.txt', 'nope'), /not found/);
  await assert.rejects(() => service.writeText('blob.bin', 'nope'), /not a text file/);
  await assert.rejects(() => service.writeText('editable-link/editable.txt', 'nope'), /reparse point/);
  await assert.rejects(
    () => service.writeText('editable.txt', 'x'.repeat(LIMITS.previewBytes + 1)),
    /too large/,
  );
});
