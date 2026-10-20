const plazaGrid = document.querySelector('#plaza-grid');
const plazaSearch = document.querySelector('#plaza-search');
const plazaStatus = document.querySelector('#plaza-status');
const plazaDirectoryStatus = document.querySelector('#plaza-directory-status');
const plazaDirectoryMessage = document.querySelector('#plaza-directory-message');
const plazaDirectoryDetail = document.querySelector('#plaza-directory-detail');
const plazaDirectoryRetry = document.querySelector('#plaza-directory-retry');
let plazaProviders = [];
const PLAZA_UNAVAILABLE_MESSAGE = '模型目录暂不可用，请稍后重试';

function normalizeCatalogAvailability(catalog) {
  return catalog?.availability === 'fresh' || catalog?.availability === 'degraded'
    ? catalog.availability
    : 'unavailable';
}

function safeCatalogMessage(catalog, availability) {
  if (
    catalog?.availability === availability
    && typeof catalog?.message === 'string'
    && catalog.message.trim()
  ) return catalog.message.trim();
  if (availability === 'degraded') return '模型目录暂时无法更新，当前仅展示最近一次可用信息';
  return PLAZA_UNAVAILABLE_MESSAGE;
}

function renderCatalogAvailability(catalog) {
  const availability = normalizeCatalogAvailability(catalog);
  if (!plazaDirectoryStatus || !plazaDirectoryMessage || !plazaDirectoryDetail || !plazaDirectoryRetry) {
    return availability;
  }
  plazaDirectoryStatus.hidden = availability === 'fresh';
  plazaDirectoryStatus.dataset.state = availability;
  plazaDirectoryMessage.textContent = availability === 'fresh' ? '' : safeCatalogMessage(catalog, availability);
  plazaDirectoryDetail.textContent = availability === 'degraded'
    ? '当前展示最近一次可用信息；不可用模型不会用于工作台选择。'
    : availability === 'unavailable'
      ? '目录恢复后可重新加载公开模型信息。'
      : '';
  plazaDirectoryRetry.hidden = availability === 'fresh' || catalog?.retryable !== true;
  return availability;
}

function catalogModels(catalog) {
  return (catalog?.providers || [])
    .filter((provider) => provider.status !== 'unavailable')
    .flatMap((provider) => Object.values(provider.capabilities || {})
      .flatMap((models) => models.map((model) => ({
        provider,
        model,
      }))));
}

function releaseTimestamp(model) {
  const timestamp = Date.parse(model.released_at || '');
  return Number.isFinite(timestamp) ? timestamp : null;
}

function sortPlazaModels(entries) {
  return [...entries].sort((left, right) => {
    const leftTimestamp = releaseTimestamp(left.model);
    const rightTimestamp = releaseTimestamp(right.model);
    if (leftTimestamp !== null && rightTimestamp !== null && leftTimestamp !== rightTimestamp) {
      return rightTimestamp - leftTimestamp;
    }
    if (leftTimestamp !== null) return -1;
    if (rightTimestamp !== null) return 1;
    return `${left.provider.name} ${left.model.display_name}`.localeCompare(
      `${right.provider.name} ${right.model.display_name}`,
      'zh-CN',
    );
  });
}

function releaseLabel(model) {
  const timestamp = releaseTimestamp(model);
  if (timestamp === null) return '发布时间未提供';
  return `发布时间：${new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date(timestamp))}`;
}

const capabilityLabels = {
  text_chat: '文本对话',
  reasoning: '推理',
  code: '代码',
  vision: '图片理解',
  image_generation: '图片生成/编辑',
  image_translation: '图片文字翻译',
  audio: '音频处理',
  video: '视频处理',
  video_generation: '视频生成',
  embedding: '向量化',
  reranking: '检索重排',
};

function capabilitySummary(model) {
  const supported = (model.supports || []).map((item) => capabilityLabels[item] || item);
  const unsupported = (model.unsupported || []).map((item) => capabilityLabels[item] || item);
  if (model.capability_status !== 'verified') {
    return [
      supported.length ? `已确认：${supported.join('、')}` : '',
      '其他能力待确认，暂不自动推荐',
    ];
  }
  return [
    supported.length ? `仅支持：${supported.join('、')}` : '',
    unsupported.length ? `不支持：${unsupported.join('、')}` : '',
  ];
}

function renderPlaza(query = '') {
  const normalizedQuery = query.trim().toLowerCase();
  const entries = sortPlazaModels(plazaProviders.filter(({ provider, model }) => {
    const searchable = `${provider.name} ${model.display_name} ${model.model_id}`.toLowerCase();
    return searchable.includes(normalizedQuery);
  }));
  plazaGrid.replaceChildren();
  entries.forEach(({ provider, model }) => {
    const card = document.createElement('article');
    const providerName = document.createElement('p');
    const modelName = document.createElement('h2');
    const modelTitle = document.createElement('span');
    const capabilityNote = document.createElement('span');
    const support = document.createElement('p');
    const unsupported = document.createElement('p');
    const release = document.createElement('p');
    card.className = 'plaza-card';
    providerName.className = 'plaza-provider';
    providerName.textContent = provider.name;
    modelTitle.textContent = model.display_name;
    capabilityNote.className = 'plaza-capability-note';
    capabilityNote.textContent = model.capability_note || '注：此模型能力信息待确认';
    modelName.append(modelTitle, capabilityNote);
    const [supportText, unsupportedText] = capabilitySummary(model);
    support.className = 'plaza-meta plaza-supports';
    support.textContent = supportText;
    unsupported.className = 'plaza-meta plaza-unsupported';
    unsupported.textContent = unsupportedText;
    unsupported.hidden = !unsupportedText;
    release.className = 'plaza-release';
    release.textContent = releaseLabel(model);
    card.append(providerName, modelName, support, unsupported, release);
    plazaGrid.append(card);
  });
  plazaStatus.textContent = entries.length
    ? `已显示 ${entries.length} 个模型（目录共 ${plazaProviders.length} 个）`
    : '没有匹配的模型';
}

function applyPlazaCatalog(catalog) {
  const availability = renderCatalogAvailability(catalog);
  if (availability === 'unavailable') {
    plazaProviders = [];
    plazaGrid.replaceChildren();
    plazaSearch.disabled = true;
    plazaStatus.textContent = '';
    return availability;
  }
  plazaProviders = catalogModels(catalog);
  plazaSearch.disabled = false;
  renderPlaza(plazaSearch.value || '');
  return availability;
}

async function loadPlaza() {
  if (plazaDirectoryRetry) plazaDirectoryRetry.disabled = true;
  try {
    const response = await fetch('/api/public/models/catalog', { credentials: 'same-origin' });
    const catalog = await response.json();
    if (!response.ok) {
      applyPlazaCatalog({ availability: 'unavailable', retryable: true, providers: [] });
      return;
    }
    applyPlazaCatalog(catalog);
  } catch (_error) {
    applyPlazaCatalog({ availability: 'unavailable', retryable: true, providers: [] });
  } finally {
    if (plazaDirectoryRetry) plazaDirectoryRetry.disabled = false;
  }
}

plazaSearch?.addEventListener('input', () => renderPlaza(plazaSearch.value));
plazaDirectoryRetry?.addEventListener('click', () => loadPlaza());
loadPlaza();
