const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const helperPath = path.join(__dirname, '..', 'static', 'task-polling.js');

test('task polling emits every reply and summary once while failures preserve successful siblings', () => {
  assert.ok(fs.existsSync(helperPath), 'task polling helper should exist');
  const { createTaskResultTracker } = require(helperPath);
  const tracker = createTaskResultTracker();

  const thinking = tracker.consume({
    state: 'running',
    summary: null,
    models: {
      deepseek: { state: 'thinking', message: null },
      kimi: { state: 'thinking', message: null },
      qwen: { state: 'thinking', message: null },
    },
  });
  assert.deepEqual(thinking.events, []);
  assert.deepEqual(thinking.thinkingModels, ['deepseek', 'kimi', 'qwen']);
  assert.equal(thinking.isTerminal, false);

  const replyCompleted = tracker.consume({
    state: 'running',
    summary: null,
    models: {
      deepseek: { state: 'completed', message: 'DeepSeek reply' },
      kimi: { state: 'thinking', message: null },
      qwen: { state: 'thinking', message: null },
    },
  });
  assert.deepEqual(replyCompleted.events, [
    { type: 'reply', model: 'deepseek', content: 'DeepSeek reply' },
  ]);
  assert.deepEqual(replyCompleted.thinkingModels, ['kimi', 'qwen']);

  const duplicateReply = tracker.consume({
    state: 'running',
    summary: null,
    models: {
      deepseek: { state: 'completed', message: 'DeepSeek reply' },
      kimi: { state: 'thinking', message: null },
      qwen: { state: 'thinking', message: null },
    },
  });
  assert.deepEqual(duplicateReply.events, []);

  const terminalFailure = tracker.consume({
    state: 'running',
    summary: null,
    models: {
      deepseek: { state: 'completed', message: 'DeepSeek reply' },
      kimi: { state: 'thinking', message: null },
      qwen: { state: 'failed', message: null },
    },
  });
  assert.deepEqual(terminalFailure.events, [{ type: 'failure', model: 'qwen', state: 'failed' }]);
  assert.deepEqual(terminalFailure.thinkingModels, ['kimi']);
  assert.equal(terminalFailure.isTerminal, false);

  const summary = tracker.consume({
    state: 'done',
    summary: {
      id: 'summary-1',
      model: 'deepseek',
      content: 'Collaboration summary',
      message_type: 'summary',
    },
    models: {
      deepseek: { state: 'completed', message: 'DeepSeek reply' },
      kimi: { state: 'completed', message: 'Kimi reply' },
      qwen: { state: 'failed', message: null },
    },
  });
  assert.deepEqual(summary.events, [
    { type: 'reply', model: 'kimi', content: 'Kimi reply' },
    {
      type: 'summary',
      id: 'summary-1',
      model: 'deepseek',
      content: 'Collaboration summary',
      message_type: 'summary',
    },
  ]);
  assert.deepEqual(summary.thinkingModels, []);
  assert.equal(summary.isTerminal, true);

  const duplicateSummary = tracker.consume({
    state: 'done',
    summary: {
      id: 'summary-1',
      model: 'deepseek',
      content: 'Collaboration summary',
      message_type: 'summary',
    },
    models: {
      deepseek: { state: 'completed', message: 'DeepSeek reply' },
      kimi: { state: 'completed', message: 'Kimi reply' },
      qwen: { state: 'failed', message: null },
    },
  });
  assert.deepEqual(duplicateSummary.events, []);
});

test('task polling exposes observable terminal-state progress', () => {
  const { createTaskResultTracker } = require(helperPath);
  const tracker = createTaskResultTracker();

  const transition = tracker.consume({
    state: 'running',
    summary: null,
    models: {
      deepseek: { state: 'completed', message: 'DeepSeek reply' },
      kimi: { state: 'thinking', message: null },
      qwen: { state: 'timed_out', message: null },
    },
  });
  const modelStates = {
    deepseek: 'completed',
    kimi: 'thinking',
    qwen: 'timed_out',
  };
  const terminalStates = new Set(['completed', 'failed', 'timed_out']);
  const terminalCount = Object.values(modelStates).filter((state) => terminalStates.has(state)).length;

  assert.deepEqual(transition.thinkingModels, ['kimi']);
  assert.equal(terminalCount, 2);
  assert.equal(Object.keys(modelStates).length, 3);
  assert.equal(`${terminalCount}/${Object.keys(modelStates).length}`, '2/3');
});

test('workspace progress helper displays observable terminal progress', () => {
  const workspacePath = path.join(__dirname, '..', 'static', 'workspace.js');
  const source = fs.readFileSync(workspacePath, 'utf8').replace(/\n\s*loadHistory\(\);/, '');
  const taskProgress = { hidden: true, textContent: '' };
  const input = { addEventListener() {}, setAttribute() {}, removeAttribute() {} };
  const form = { addEventListener() {} };
  const mentionMenu = { hidden: true, replaceChildren() {}, append() {} };

  global.document = {
    querySelector(selector) {
      return {
        '#messages': { append() {}, scrollHeight: 0, scrollTop: 0 },
        '#chat-form': form,
        '#message': input,
        '#send': {},
        '#mention-menu': mentionMenu,
        '#model-picker': null,
        '#agent-status-list': null,
        '#task-progress': taskProgress,
        '#app-shell': null,
        '#mobile-agent-toggle': null,
      }[selector];
    },
    createElement() { return { setAttribute() {}, addEventListener() {} }; },
  };

  eval(source);
  const result = globalThis.WorkspacePresentation.updateTaskProgress({
    models: {
      deepseek: { state: 'completed' },
      kimi: { state: 'thinking' },
      qwen: { state: 'timed_out' },
    },
  });

  assert.deepEqual(result, { terminalCount: 2, targetCount: 3 });
  assert.equal(taskProgress.hidden, false);
  assert.equal(taskProgress.textContent, '任务进度 2 / 3');
});
