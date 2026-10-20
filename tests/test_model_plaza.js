import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const script = readFileSync(new URL('../static/model-plaza.js', import.meta.url), 'utf8');

class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.textContent = '';
    this.value = '';
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  addEventListener(type, listener) { this[`on${type}`] = listener; }
}

function loadPlazaApi(fetchImpl = async () => ({ ok: true, json: async () => ({ availability: 'fresh', providers: [] }) })) {
  const elements = {
    '#plaza-grid': new FakeElement('section'),
    '#plaza-search': new FakeElement('input'),
    '#plaza-status': new FakeElement('p'),
    '#plaza-directory-status': new FakeElement('section'),
    '#plaza-directory-message': new FakeElement('strong'),
    '#plaza-directory-detail': new FakeElement('p'),
    '#plaza-directory-retry': new FakeElement('button'),
  };
  global.document = {
    querySelector(selector) { return elements[selector] || null; },
    createElement(tagName) { return new FakeElement(tagName); },
  };
  global.fetch = fetchImpl;
  const source = script.replace(/\nloadPlaza\(\);\s*$/, '');
  eval(`${source}\n;globalThis.ModelPlazaTestApi = { applyPlazaCatalog, loadPlaza, renderCatalogAvailability };`);
  return { ...globalThis.ModelPlazaTestApi, elements };
}

function oneModelCatalog(availability, overrides = {}) {
  return {
    availability,
    message: availability === 'degraded' ? '模型目录暂时无法更新，当前仅展示最近一次可用信息' : null,
    retryable: false,
    providers: [{
      key: 'deepseek', name: 'DeepSeek', status: availability === 'degraded' ? 'stale' : 'fresh', capabilities: {
        chat: [{ platform_model_id: 'deepseek/v4', model_id: 'v4', display_name: 'DeepSeek V4', callable: availability === 'fresh', selectable: availability === 'fresh' }],
      },
    }],
    ...overrides,
  };
}

test('model plaza exposes release ordering and never creates per-card workspace links', () => {
  assert.match(script, /function sortPlazaModels\(entries\)/);
  assert.match(script, /发布时间未提供/);
  assert.doesNotMatch(script, /\/workspace\?model=/);
});

test('model plaza keeps one release-ordered directory without capability filtering', () => {
  assert.doesNotMatch(script, /function populateCapabilityFilter/);
  assert.doesNotMatch(script, /capability-filter/);
});

test('model plaza renders inline capability notes and support boundaries', () => {
  assert.match(script, /model\.capability_note/);
  assert.match(script, /function capabilitySummary\(model\)/);
  assert.match(script, /model\.supports/);
  assert.match(script, /model\.unsupported/);
  assert.match(script, /capability_status/);
  assert.match(script, /已确认：/);
  assert.match(script, /其他能力待确认，暂不自动推荐/);
  assert.doesNotMatch(script, /return \['能力信息待确认，暂不自动推荐', ''\]/);
});

test('fresh plaza catalog renders normally without an availability banner', () => {
  const api = loadPlazaApi();

  api.applyPlazaCatalog(oneModelCatalog('fresh'));

  assert.equal(api.elements['#plaza-directory-status'].hidden, true);
  assert.equal(api.elements['#plaza-search'].disabled, false);
  assert.equal(api.elements['#plaza-grid'].children.length, 1);
  assert.match(api.elements['#plaza-status'].textContent, /已显示 1 个模型/);
});

test('degraded plaza catalog keeps cached cards behind a non-blocking safe banner', () => {
  const api = loadPlazaApi();

  api.applyPlazaCatalog(oneModelCatalog('degraded'));

  assert.equal(api.elements['#plaza-directory-status'].hidden, false);
  assert.equal(api.elements['#plaza-directory-status'].dataset.state, 'degraded');
  assert.equal(api.elements['#plaza-directory-message'].textContent, '模型目录暂时无法更新，当前仅展示最近一次可用信息');
  assert.match(api.elements['#plaza-directory-detail'].textContent, /不可用模型不会用于工作台选择/);
  assert.equal(api.elements['#plaza-grid'].children.length, 1);
  assert.equal(api.elements['#plaza-search'].disabled, false);
});

test('unavailable plaza catalog uses a dedicated empty state instead of search-empty copy', () => {
  const api = loadPlazaApi();
  api.applyPlazaCatalog(oneModelCatalog('fresh'));

  api.applyPlazaCatalog({
    availability: 'unavailable',
    message: '模型目录暂不可用，请稍后重试',
    retryable: true,
    providers: [],
  });

  assert.equal(api.elements['#plaza-directory-status'].dataset.state, 'unavailable');
  assert.equal(api.elements['#plaza-grid'].children.length, 0);
  assert.equal(api.elements['#plaza-search'].disabled, true);
  assert.equal(api.elements['#plaza-directory-retry'].hidden, false);
  assert.doesNotMatch(api.elements['#plaza-status'].textContent, /没有匹配的模型/);
});

test('unknown and network failures expose only fixed safe plaza copy', async () => {
  const api = loadPlazaApi(async () => { throw new Error('https://private-upstream.test/catalog?key=secret'); });

  api.applyPlazaCatalog({ availability: 'mystery', message: 'unsafe raw detail', providers: [] });
  assert.equal(api.elements['#plaza-directory-message'].textContent, '模型目录暂不可用，请稍后重试');
  assert.equal(api.elements['#plaza-directory-retry'].hidden, true);

  await api.loadPlaza();
  assert.equal(api.elements['#plaza-directory-message'].textContent, '模型目录暂不可用，请稍后重试');
  assert.equal(api.elements['#plaza-directory-retry'].hidden, false);
  assert.doesNotMatch(api.elements['#plaza-directory-message'].textContent, /private-upstream|secret|https:/);
});

test('plaza client never reads internal catalog failure categories', () => {
  assert.doesNotMatch(script, /failure_categories/);
});
