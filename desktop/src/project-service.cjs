const fs = require('node:fs/promises');
const path = require('node:path');

const LIMITS = Object.freeze({
  treeDepth: 6,
  treeEntries: 1000,
  searchFiles: 2000,
  searchBytes: 128 * 1024,
  searchResults: 100,
  previewBytes: 256 * 1024,
  previewLines: 5000,
});

const FATAL_UTF8_DECODER = new TextDecoder('utf-8', { fatal: true });

class ProjectError extends Error {}

class ProjectService {
  constructor() {
    this.rootPath = null;
  }

  status() {
    if (!this.rootPath) {
      return { authorized: false };
    }

    return {
      authorized: true,
      rootName: path.basename(this.rootPath),
    };
  }

  async authorizeRoot(rootPath) {
    return this.runSafely(async () => {
      if (typeof rootPath !== 'string' || !rootPath.trim()) {
        throw new ProjectError('invalid project root');
      }

      const resolvedRoot = path.resolve(rootPath);
      const rootType = entryTypeForLstat(await fs.lstat(resolvedRoot));
      if (rootType !== 'directory') {
        throw new ProjectError('project root must be a normal directory');
      }

      this.rootPath = resolvedRoot;
      return this.status();
    });
  }

  async listTree() {
    return this.runSafely(async () => {
      const root = this.requireAuthorizedRoot();
      const entries = [];
      let truncated = false;
      let stopWalking = false;

      const walk = async (currentPath, depth) => {
        if (depth > LIMITS.treeDepth || stopWalking) {
          if (depth > LIMITS.treeDepth) {
            truncated = true;
          }
          return;
        }

        const dirEntries = await fs.readdir(currentPath, { withFileTypes: true });
        dirEntries.sort((left, right) => left.name.localeCompare(right.name, 'en'));

        for (const dirEntry of dirEntries) {
          const relativePath = this.toRelativeProjectPath(path.join(currentPath, dirEntry.name));
          if (isProtectedName(relativePath)) {
            continue;
          }

          const fullPath = path.join(currentPath, dirEntry.name);
          const type = entryTypeForLstat(await fs.lstat(fullPath));
          if (!type) {
            continue;
          }

          if (entries.length >= LIMITS.treeEntries) {
            truncated = true;
            stopWalking = true;
            return;
          }

          const normalizedPath = normalizeSeparators(relativePath);

          entries.push({
            path: normalizedPath,
            name: path.basename(normalizedPath),
            depth,
            type,
          });

          if (type === 'directory') {
            await walk(fullPath, depth + 1);
            if (stopWalking) {
              return;
            }
          }
        }
      };

      await walk(root, 1);

      return {
        rootName: path.basename(root),
        entries,
        truncated,
      };
    });
  }

  async search(query) {
    return this.runSafely(async () => {
      const root = this.requireAuthorizedRoot();
      if (typeof query !== 'string' || !query.trim()) {
        throw new ProjectError('search query is required');
      }

      const needle = query.toLowerCase();
      const results = [];
      let filesScanned = 0;
      let truncated = false;
      let stopWalking = false;

      const walk = async (currentPath, depth) => {
        if (depth > LIMITS.treeDepth || stopWalking) {
          if (depth > LIMITS.treeDepth) {
            truncated = true;
          }
          return;
        }

        const dirEntries = await fs.readdir(currentPath, { withFileTypes: true });
        dirEntries.sort((left, right) => left.name.localeCompare(right.name, 'en'));

        for (const dirEntry of dirEntries) {
          if (stopWalking) {
            return;
          }

          const fullPath = path.join(currentPath, dirEntry.name);
          const relativePath = this.toRelativeProjectPath(fullPath);
          if (isProtectedName(relativePath)) {
            continue;
          }

          const type = entryTypeForLstat(await fs.lstat(fullPath));
          if (!type) {
            continue;
          }

          if (type === 'directory') {
            await walk(fullPath, depth + 1);
            continue;
          }

          filesScanned += 1;
          if (filesScanned > LIMITS.searchFiles) {
            truncated = true;
            stopWalking = true;
            return;
          }

          let preview;
          try {
            preview = await readTextFile(fullPath, LIMITS.searchBytes);
          } catch (error) {
            if (error instanceof ProjectError && /text file/.test(error.message)) {
              continue;
            }
            throw error;
          }
          if (preview.truncated) {
            truncated = true;
          }

          const lines = preview.content.split('\n');
          for (let index = 0; index < lines.length; index += 1) {
            const line = lines[index];
            if (!line.toLowerCase().includes(needle)) {
              continue;
            }

            results.push({
              path: normalizeSeparators(relativePath),
              line: index + 1,
              preview: line.slice(0, 500),
            });

            if (results.length >= LIMITS.searchResults) {
              truncated = true;
              stopWalking = true;
              return;
            }
          }
        }
      };

      await walk(root, 1);

      return {
        query,
        results,
        truncated,
      };
    });
  }

  async preview(relativePath) {
    return this.runSafely(async () => {
      const { fullPath, projectPath } = await this.resolveProjectFile(relativePath);
      const preview = await readTextFile(fullPath, LIMITS.previewBytes);
      const lines = preview.content.split('\n').slice(0, LIMITS.previewLines);

      return {
        path: projectPath,
        content: lines.join('\n'),
        lineCount: lines.length,
        truncated: preview.truncated || preview.content.split('\n').length > LIMITS.previewLines,
      };
    });
  }

  async writeText(relativePath, content) {
    return this.runSafely(async () => {
      if (typeof content !== 'string') {
        throw new ProjectError('content must be a UTF-8 string');
      }

      const contentBytes = Buffer.byteLength(content, 'utf8');
      if (contentBytes > LIMITS.previewBytes) {
        throw new ProjectError('content too large');
      }

      const { fullPath, projectPath, entryType } = await this.resolveProjectFile(relativePath);
      if (entryType !== 'file') {
        throw new ProjectError('file not found');
      }

      await readTextFile(fullPath, LIMITS.previewBytes);
      await fs.writeFile(fullPath, content, 'utf8');

      return {
        path: projectPath,
        bytesWritten: contentBytes,
      };
    });
  }

  requireAuthorizedRoot() {
    if (!this.rootPath) {
      throw new ProjectError('project root is not authorized');
    }

    return this.rootPath;
  }

  toRelativeProjectPath(targetPath) {
    return normalizeSeparators(path.relative(this.requireAuthorizedRoot(), targetPath));
  }

  async resolveProjectFile(relativePath) {
    const root = this.requireAuthorizedRoot();
    const projectPath = normalizeRelativeInput(relativePath);
    if (isProtectedName(projectPath)) {
      throw new ProjectError('protected file');
    }

    const fullPath = path.resolve(root, projectPath);
    if (!isWithinRoot(root, fullPath)) {
      throw new ProjectError('path is outside project');
    }

    const segments = projectPath.split('/');
    let cursor = root;
    let entryType = null;

    for (let index = 0; index < segments.length; index += 1) {
      cursor = path.join(cursor, segments[index]);
      try {
        entryType = entryTypeForLstat(await fs.lstat(cursor));
      } catch (error) {
        if (error && error.code === 'ENOENT') {
          throw new ProjectError('file not found');
        }
        throw error;
      }

      if (!entryType) {
        throw new ProjectError('reparse point or unsupported file type paths are not allowed');
      }
      if (index < segments.length - 1 && entryType !== 'directory') {
        throw new ProjectError('file not found');
      }
    }

    return { fullPath, projectPath, entryType };
  }

  async runSafely(operation) {
    try {
      return await operation();
    } catch (error) {
      if (error instanceof ProjectError) {
        throw error;
      }
      throw new ProjectError('project filesystem operation failed');
    }
  }
}

function normalizeRelativeInput(relativePath) {
  if (typeof relativePath !== 'string') {
    throw new ProjectError('path is outside project');
  }

  const trimmed = relativePath.trim();
  if (!trimmed || path.isAbsolute(trimmed) || trimmed.includes('\0')) {
    throw new ProjectError('path is outside project');
  }

  const normalized = normalizeSeparators(trimmed);
  const segments = normalized.split('/');
  if (segments.some((segment) => segment === '..' || segment === '')) {
    throw new ProjectError('path is outside project');
  }

  return normalized;
}

function normalizeSeparators(value) {
  return value.split(path.sep).join('/').replace(/\\/g, '/');
}

function isWithinRoot(rootPath, candidatePath) {
  const relative = path.relative(rootPath, candidatePath);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function entryTypeForLstat(stats) {
  if (stats.isSymbolicLink()) {
    return null;
  }
  if (stats.isFile()) {
    return 'file';
  }
  if (stats.isDirectory()) {
    return 'directory';
  }
  return null;
}

function isProtectedName(projectPath) {
  const segments = normalizeSeparators(projectPath).split('/').filter(Boolean);
  return segments.some((segment) => isProtectedSegment(segment));
}

function isProtectedSegment(segment) {
  const lower = segment.toLowerCase();
  return (
    lower === '.git'
    || lower === '.ssh'
    || lower === '.env'
    || lower.startsWith('.env.')
    || lower === 'id_rsa'
    || lower.includes('credential')
    || lower.includes('secret')
    || lower.includes('token')
    || lower.includes('key')
    || lower.includes('password')
    || lower.includes('private')
  );
}

async function readTextFile(fullPath, byteLimit) {
  const handle = await fs.open(fullPath, 'r');

  try {
    const buffer = Buffer.alloc(byteLimit + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    const slice = buffer.subarray(0, Math.min(bytesRead, byteLimit));
    if (slice.includes(0)) {
      throw new ProjectError('file is not a text file');
    }

    let content;
    try {
      content = FATAL_UTF8_DECODER.decode(slice);
    } catch {
      throw new ProjectError('file is not a text file');
    }

    return {
      content,
      truncated: bytesRead > byteLimit,
    };
  } finally {
    await handle.close();
  }
}

module.exports = {
  LIMITS,
  ProjectError,
  ProjectService,
  entryTypeForLstat,
};
