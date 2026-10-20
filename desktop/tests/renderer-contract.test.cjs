const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const renderer = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(renderer, 'index.html'), 'utf8');
const cssPath = path.join(renderer, 'styles.css');
const scriptPath = path.join(renderer, 'app.js');

test('desktop shell exposes the approved information architecture', () => {
  assert.equal(fs.existsSync(cssPath), true);
  assert.equal(fs.existsSync(scriptPath), true);
  for (const id of [
    'global-sidebar',
    'task-header',
    'task-timeline',
    'schedule-view',
    'schedule-form',
    'schedule-title',
    'schedule-list',
    'schedule-summary',
    'environment-inspector',
    'desktop-composer',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /模型广场/);
  assert.match(html, /协作模型/);
  assert.match(html, /文件和本地项目将在后续阶段启用/);
  assert.match(html, /styles\.css/);
  const scheduleStoreIndex = html.indexOf('<script src="schedule-store.js" defer></script>');
  const viewStateIndex = html.indexOf('<script src="view-state.js" defer></script>');
  const appScriptIndex = html.indexOf('<script src="app.js" defer></script>');
  assert.notEqual(scheduleStoreIndex, -1);
  assert.notEqual(viewStateIndex, -1);
  assert.notEqual(appScriptIndex, -1);
  assert.ok(scheduleStoreIndex < appScriptIndex);
  assert.ok(viewStateIndex < appScriptIndex);
});

test('desktop shell includes a cloud transcript, composer, and model controls', () => {
  for (const id of [
    'workspace-transcript',
    'workspace-composer',
    'workspace-message',
    'workspace-send',
    'model-settings',
    'conversation-menu',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  const script = fs.readFileSync(scriptPath, 'utf8');
  assert.match(script, /workspaceApi\.openConversation/);
  assert.match(script, /workspaceApi\.createConversation/);
  assert.match(script, /workspaceApi\.renameConversation/);
  assert.match(script, /workspaceApi\.replaceModels/);
});

test('project view contains the authorized project, search, tree, tabs, editor, and save controls', () => {
  for (const id of [
    'project-open',
    'project-view',
    'project-search',
    'project-tree',
    'project-tabs',
    'project-editor',
    'project-save',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  const script = fs.readFileSync(scriptPath, 'utf8');
  assert.match(script, /projectApi\.chooseRoot/);
  assert.match(script, /projectApi\.writeText/);
  assert.match(script, /canSaveActiveProjectTab\(state\.project\)/);
  assert.match(script, /saveActiveProjectTab\(state\.project, projectApi\.writeText, lifecycleToken\)/);
  assert.match(script, /rootGeneration:\s*0/);
  assert.match(script, /captureProjectRootLifecycle\(state\.project\)/);
  assert.match(script, /applyProjectLifecycleResult\(state\.project, lifecycleToken/);
  assert.match(script, /replaceProjectRoot\(state\.project, status\)/);
  assert.match(script, /status\.changed === false\) return/);
  assert.match(script, /clearProjectWorkspace\(status\);\s+renderProjectState\(\);\s+if \(state\.project\.status\.authorized\)/);
  assert.match(script, /AstraRelayEditor\.mount/);
  assert.match(html, /project-tabs\.js/);
  assert.doesNotMatch(script, /\bfetch\s*\(|require\s*\(|\.innerHTML\s*=/);
});

test('conversation context menu is a viewport-owned overlay near the clicked row', () => {
  const script = fs.readFileSync(scriptPath, 'utf8');
  const css = fs.readFileSync(cssPath, 'utf8');
  assert.match(script, /openConversationMenu\(conversation\.id, event\.clientX, event\.clientY\)/);
  assert.match(script, /function positionConversationMenu\(clientX, clientY\)/);
  assert.match(script, /Math\.min\(Number\(clientX\) \|\| 0, viewportWidth - panelWidth - gutter\)/);
  assert.match(script, /Math\.min\(Number\(clientY\) \|\| 0, viewportHeight - panelHeight - gutter\)/);
  assert.match(script, /conversationMenuPanel\.style\.left\s*=/);
  assert.match(script, /conversationMenuPanel\.style\.top\s*=/);
  assert.doesNotMatch(script, /append\(elements\.conversationMenuPanel\)/);
  assert.match(css, /\.conversation-menu\s*\{[\s\S]*position:\s*fixed/);
  assert.doesNotMatch(css, /\.conversation-menu\s*\{[^}]*position:\s*absolute/);
  assert.match(script, /event\.key === 'Escape'[\s\S]{0,180}closeConversationMenu\(\)/);
  assert.match(script, /!elements\.conversationMenuPanel\.contains\(event\.target\)[\s\S]{0,140}closeConversationMenu\(\)/);
});

test('conversation rename uses an accessible native dialog shell', () => {
  for (const id of [
    'rename-conversation-dialog',
    'rename-conversation-input',
    'rename-conversation-error',
    'rename-conversation-cancel',
    'rename-conversation-save',
    'rename-conversation-close',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /role="dialog"/);
  assert.match(html, /aria-modal="true"/);
});

test('renderer replaces browser prompt with dialog-backed rename', () => {
  const source = fs.readFileSync(scriptPath, 'utf8');
  assert.match(source, /function openRenameConversationDialog\(conversation\)/);
  assert.match(source, /async function submitRenameConversation\(event\)/);
  assert.match(source, /workspaceApi\.renameConversation\(conversation\.id, title\)/);
  assert.doesNotMatch(source, /conversationRename\.addEventListener\([\s\S]{0,160}renameActiveConversation/);
  assert.doesNotMatch(source, /async function renameActiveConversation\(\)[\s\S]{0,500}window\.prompt\(/);
  assert.match(source, /renameConversationError\.hidden = false/);
});

test('dialog-backed rename preserves draft and supports native modal controls', () => {
  const source = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
  const openDialog = source.match(/function openRenameConversationDialog\(conversation\) \{([\s\S]*?)\n\}/);
  const closeDialog = source.match(/function closeRenameConversationDialog\(restoreFocus = true\) \{([\s\S]*?)\n\}/);
  const submitDialog = source.match(/async function submitRenameConversation\(event\) \{([\s\S]*?)\n\}/);

  for (const match of [openDialog, closeDialog, submitDialog]) assert.ok(match);
  assert.match(html, /id="rename-conversation-form"[^>]*novalidate/);
  assert.match(source, /renameDialog:\s*\{[\s\S]*conversationId:\s*null,[\s\S]*draft:\s*'',[\s\S]*saving:\s*false,[\s\S]*error:\s*''/);
  assert.match(openDialog[1], /elements\.renameConversationInput\.value = state\.renameDialog\.draft;/);
  assert.match(openDialog[1], /elements\.renameConversationInput\.focus\(\);\s+elements\.renameConversationInput\.select\(\);/);
  assert.match(submitDialog[1], /state\.renameDialog\.draft = elements\.renameConversationInput\.value;/);
  assert.match(submitDialog[1], /state\.conversations = state\.conversations\.map/);
  assert.match(submitDialog[1], /catch[\s\S]*state\.renameDialog\.saving = false;[\s\S]*state\.renameDialog\.error = '重命名失败，请稍后重试。';/);
  assert.doesNotMatch(submitDialog[1], /renameConversationInput\.value\s*=\s*['"]{2}/);
  assert.match(source, /renameConversationForm\.addEventListener\('submit', \(event\) => \{ submitRenameConversation\(event\); \}\)/);
  assert.match(source, /renameConversationCancel\.addEventListener\('click', \(\) => closeRenameConversationDialog\(\)\)/);
  assert.match(source, /renameConversationClose\.addEventListener\('click', \(\) => closeRenameConversationDialog\(\)\)/);
  assert.match(source, /event\.target === elements\.renameConversationDialog[\s\S]{0,120}closeRenameConversationDialog\(\)/);
  assert.match(source, /event\.key === 'Escape' && !elements\.renameConversationDialog\.hidden[\s\S]{0,120}closeRenameConversationDialog\(\)/);
});

test('startup opens history before empty fallback creation', () => {
  const source = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
  const ensure = source.match(/async function ensureDefaultConversation\(\) \{([\s\S]*?)\n\}/);
  assert.ok(ensure);
  assert.match(ensure[1], /state\.conversations\.length > 0/);
  assert.match(ensure[1], /selectConversation\(state\.conversations\[0\]/);
  assert.ok(ensure[1].indexOf('state.conversations.length > 0') < ensure[1].indexOf('createConversation()'));
  assert.match(ensure[1], /if \(state\.conversations\.length > 0\) \{[\s\S]*return;[\s\S]*const conversation = await createConversation\(\);/);
  assert.match(source, /requestedSelectionEpoch === null\s*\? \+\+conversationSelectionEpoch/);
  assert.match(source, /if \(selectionEpoch !== conversationSelectionEpoch\) return;/);
});

test('conversation creation and deletion provide immediate local feedback without serial list refreshes', () => {
  const script = fs.readFileSync(scriptPath, 'utf8');
  assert.match(script, /deletingConversationIds:\s*new Set\(\)/);
  assert.match(script, /elements\.newTask\.disabled\s*=\s*state\.phase !== 'ready' \|\| conversationMutationInProgress\(\)/);
  assert.match(script, /state\.creatingConversation\s*=\s*true;[\s\S]{0,160}render\(\);[\s\S]{0,160}const conversation = await workspaceApi\.createConversation\(\);/);
  assert.match(script, /state\.conversations\s*=\s*\[conversation, \.\.\.state\.conversations\.filter/);
  assert.doesNotMatch(script, /createConversation\(\)[\s\S]{0,700}await refreshConversationList\(\)/);
  assert.match(script, /state\.deletingConversationIds\.add\(conversation\.id\);/);
  assert.match(script, /state\.conversations\s*=\s*state\.conversations\.filter\(\(item\) => item && item\.id !== conversation\.id\);/);
  assert.match(script, /state\.conversationNotice\s*=\s*'正在删除对话…';/);
  const deleteStart = script.indexOf('const deleteTransition = deletingActiveConversation');
  const deleteRender = script.indexOf('  render();', deleteStart);
  const remoteDelete = script.indexOf('await workspaceApi.deleteConversation(conversation.id);', deleteStart);
  assert.ok(deleteStart >= 0 && deleteRender > deleteStart && remoteDelete > deleteRender);
  assert.match(script, /catch\s*\{[\s\S]{0,420}restoreDeletedConversation/);
  assert.match(script, /conversationListRevision:\s*0/);
  assert.match(script, /if \(conversationMutationInProgress\(\)\) return;/);
  assert.match(script, /if \(listRevision === state\.conversationListRevision\) \{/);
  assert.match(html, /conversation-operations\.js/);
  assert.match(script, /conversationOperations\.beginActiveConversationDeletion/);
  assert.match(script, /conversationOperations\.canRestoreDeletedActiveConversation/);
  assert.doesNotMatch(script, /if \(deletingActiveConversation\) \{\s+\+\+conversationSelectionEpoch;/);
  assert.match(script, /requestedSelectionEpoch === null\s*\? \+\+conversationSelectionEpoch/);
  assert.match(script, /if \(selectionEpoch !== conversationSelectionEpoch\) return;/);
});

test('only the active conversation receives an emphasized border', () => {
  const css = fs.readFileSync(cssPath, 'utf8');
  assert.match(css, /\.conversation-row\s*\{[\s\S]*border:\s*1px solid transparent/);
  assert.match(css, /\.conversation-row\.is-active\s*\{[\s\S]*border-color:\s*#1f232b/);
});

test('desktop workspace exposes selected public model names and an overflow card', () => {
  for (const id of [
    'selected-models-card',
    'selected-models-list',
    'model-summary-overflow',
    'model-summary-overflow-panel',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /aria-expanded="false"/);
});

test('selected model rendering uses public display names with an accessible overflow control', () => {
  const script = fs.readFileSync(scriptPath, 'utf8');
  assert.match(script, /function selectedPublicModels\(\)/);
  assert.match(script, /model\.display_name\.trim\(\)/);
  assert.match(script, /modelSummaryOverflow\.setAttribute\('aria-expanded'/);
  assert.match(script, /closeModelSummaryOverflow\(\)/);
  assert.doesNotMatch(script, /selectedModelsList[\s\S]{0,240}model_id/);
});

test('packaged renderer has no Node or direct network access', () => {
  const script = fs.readFileSync(scriptPath, 'utf8');
  const scheduleScript = fs.readFileSync(path.join(renderer, 'schedule-store.js'), 'utf8');
  assert.doesNotMatch(script, /require\s*\(/);
  assert.doesNotMatch(script, /\bfetch\s*\(/);
  assert.doesNotMatch(script, /\.innerHTML\s*=/);
  const styleWrites = script.match(/conversationMenuPanel\.style\.(?:left|top)\s*=/g) || [];
  assert.deepEqual(styleWrites, [
    'conversationMenuPanel.style.left =',
    'conversationMenuPanel.style.top =',
  ]);
  assert.doesNotMatch(
    script.replace(/elements\.conversationMenuPanel\.style\.(?:left|top)\s*=.*;/g, ''),
    /\.style\./,
  );
  assert.match(script, /astraDesktop\.cloud\.bootstrap/);
  assert.match(script, /astraDesktop\.session\.login/);
  assert.match(script, /astraDesktop\.session\.onChanged/);
  assert.match(script, /astraDesktop\.workspace/);
  assert.match(script, /display_name/);
  assert.doesNotMatch(script, /session\.username/);
  assert.doesNotMatch(scheduleScript, /require\s*\(/);
  assert.doesNotMatch(scheduleScript, /\bfetch\s*\(/);
  assert.doesNotMatch(scheduleScript, /\bchild_process\b/);
  assert.doesNotMatch(scheduleScript, /\bipcRenderer\b/);
  assert.doesNotMatch(scheduleScript, /\bastraDesktop\b/);
});

test('preload exposes only reviewed project methods', () => {
  const preload = fs.readFileSync(path.join(__dirname, '..', 'src', 'preload.cjs'), 'utf8');
  assert.match(preload, /project:\s*Object\.freeze\(\{/);
  for (const method of ['chooseRoot', 'status', 'listTree', 'search', 'preview', 'writeText']) {
    assert.match(preload, new RegExp(`${method}:`));
  }
  assert.doesNotMatch(preload, /\bfs\b|showOpenDialog|absolutePath/);
});

test('desktop layout keeps three clear regions and explicit compact fallbacks', () => {
  const css = fs.readFileSync(cssPath, 'utf8');
  assert.match(
    css,
    /grid-template-columns:\s*15\.5rem\s+minmax\(0,\s*1fr\)\s+18\.25rem/,
  );
  assert.match(css, /@media\s*\(min-width:\s*820px\)/);
  assert.match(css, /@media\s*\(min-width:\s*1100px\)/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /:focus-visible/);
});

test('compact overlays manage focus and future controls are honest', () => {
  const script = fs.readFileSync(scriptPath, 'utf8');
  assert.match(html, /id="global-sidebar"[^>]*inert/);
  assert.match(html, /id="environment-inspector"[^>]*inert/);
  assert.doesNotMatch(html, /data-future-action/);
  assert.match(script, /trapOverlayFocus/);
  assert.match(script, /\.inert\s*=/);
  assert.match(script, /aria-modal/);
  assert.match(script, /matchMedia/);
});

test('new task is actionable and startup opens a direct conversation composer', () => {
  const script = fs.readFileSync(scriptPath, 'utf8');
  assert.match(html, /<button class="new-task-button" id="new-task" type="button">/);
  assert.doesNotMatch(html, /id="workspace-new"/);
  assert.match(script, /async function ensureDefaultConversation\(\)/);
  assert.match(script, /ensureDefaultConversation\(\)\.catch/);
  assert.match(script, /elements\.newTask\.addEventListener\('click', \(\) => \{\s+selectActiveView\('workspace'\);\s+createConversation\(\)\.catch/s);
});

test('navigation uses reviewed view buttons and passive unavailable rows', () => {
  for (const id of [
    'workspace-view',
    'search-view',
    'model-plaza-view',
    'schedule-view',
    'project-view',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const view of ['workspace', 'search', 'model-plaza', 'schedule', 'project']) {
    assert.match(html, new RegExp(`data-view="${view}"`));
  }
  assert.match(html, /aria-disabled="true"[^>]*>[^<]*插件/);
  assert.match(html, /aria-disabled="true"[^>]*>[^<]*站点/);
  assert.match(html, /aria-disabled="true"[^>]*>[^<]*拉取请求/);
  const viewButtons = Array.from(html.matchAll(/<button[^>]*data-view="[^"]+"[^>]*>([\s\S]*?)<\/button>/g));
  assert.equal(viewButtons.length, 5);
  for (const [, markup] of viewButtons) {
    assert.doesNotMatch(markup, /稍后/);
  }
});

test('renderer owns active view state through the pure navigation module', () => {
  const script = fs.readFileSync(scriptPath, 'utf8');
  const viewStateScript = fs.readFileSync(path.join(renderer, 'view-state.js'), 'utf8');
  assert.match(viewStateScript, /const VIEWS = new Set\(\['workspace', 'search', 'model-plaza', 'schedule', 'project'\]\)/);
  assert.doesNotMatch(viewStateScript, /require\s*\(|\bfetch\s*\(|\bastraDesktop\b|\bipcRenderer\b/);
  assert.match(script, /activeView:\s*'workspace'/);
  assert.match(script, /const viewState = window\.AstraRelayViews/);
  assert.match(script, /function selectActiveView\(view\)/);
  assert.match(script, /state\.activeView = viewState\.select\(view\)/);
  assert.match(script, /\.hidden = !viewState\.isActive\(/);
  assert.match(script, /\.classList\.toggle\('is-active'/);
});

test('renderer routes project text search only through the constrained bridge', () => {
  const source = fs.readFileSync(scriptPath, 'utf8');
  assert.match(source, /window\.astraDesktop\.project\.search\(query\)/);
  assert.doesNotMatch(source, /ipcRenderer|require\(['"]node:fs|fetch\(/);
});

test('renderer uses case-normalized ordered fuzzy matching for conversations', () => {
  const source = fs.readFileSync(scriptPath, 'utf8');
  assert.match(source, /function fuzzyConversationMatches\(title, query\)/);
  assert.match(source, /fuzzyConversationMatches\(title, normalized\)/);
  assert.match(source, /normalizeSearchValue\(title\)\.toLocaleLowerCase\(\)/);
  assert.match(source, /for \(const character of normalizedQuery\)/);
  assert.doesNotMatch(source, /title\.toLocaleLowerCase\(\)\.includes\(normalizedLower\)/);
});

test('renderer debounces input search and ignores superseded search work', () => {
  const source = fs.readFileSync(scriptPath, 'utf8');
  assert.match(source, /const SEARCH_DEBOUNCE_MS = \d+;/);
  assert.match(source, /searchDebounceTimer = window\.setTimeout\(/);
  assert.match(source, /window\.clearTimeout\(searchDebounceTimer\)/);
  assert.match(source, /if \(currentEpoch !== searchEpoch\) return;/);
  assert.match(source, /elements\.searchQuery\.addEventListener\('input', \(\) => \{\s+scheduleSearchFromInput\(\);/);
});

test('renderer invalidates cached search state for cloud and project-root transitions', () => {
  const source = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
  const invalidate = source.match(/function invalidateSearchState\(\) \{([\s\S]*?)\n\}/);
  const bootstrap = source.match(/async function refreshBootstrap\(\) \{([\s\S]*?)\n\}\n\nasync function openLogin/);
  const clearProject = source.match(/function clearProjectWorkspace\(status\) \{([\s\S]*?)\n\}/);
  const login = source.match(/async function openLogin\(\) \{([\s\S]*?)\n\}\n\nfunction overlayPanel/);

  for (const match of [invalidate, bootstrap, clearProject, login]) assert.ok(match);
  assert.match(invalidate[1], /searchEpoch \+= 1;/);
  assert.match(invalidate[1], /window\.clearTimeout\(searchDebounceTimer\)/);
  for (const group of ['query', 'conversations', 'projectFiles', 'projectText']) {
    assert.match(invalidate[1], new RegExp(`state\\.search\\.${group} = ${group === 'query' ? "''" : '\\[\\]'}`));
  }
  assert.match(bootstrap[1], /invalidateSearchState\(\);\s+const currentEpoch = \+\+refreshEpoch;/);
  assert.match(clearProject[1], /const lifecycleToken = replaceProjectRoot\(state\.project, status\);\s+invalidateSearchState\(\);/);
  assert.match(login[1], /state\.phase = 'offline';\s+invalidateSearchState\(\);/);
  assert.match(source, /session\.onChanged\(\(\) => refreshBootstrap\(\)\)/);
});

test('renderer includes a case-normalized project filename result group', () => {
  const source = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
  const searchWorkspace = source.match(/async function searchWorkspace\(rawQuery\) \{([\s\S]*?)\n\}/);
  const searchView = source.match(/function renderSearchView\(\) \{([\s\S]*?)\n\}\n\nasync function performSearch/);

  assert.match(html, /id="search-project-files"/);
  assert.match(source, /searchProjectFiles: document\.getElementById\('search-project-files'\)/);
  assert.match(source, /projectFiles: \[\]/);
  assert.match(source, /function projectFilenameMatches\(path, query\)/);
  assert.match(source, /normalizeSearchValue\(path\)\.toLocaleLowerCase\(\)/);
  assert.ok(searchWorkspace);
  assert.match(searchWorkspace[1], /state\.project\.tree/);
  assert.match(searchWorkspace[1], /entry\.type === 'file'/);
  assert.match(searchWorkspace[1], /projectFilenameMatches\(entry\.path, normalized\)/);
  assert.match(searchWorkspace[1], /projectFiles/);
  assert.ok(searchView);
  assert.match(searchView[1], /elements\.searchProjectFiles\.replaceChildren\(\)/);
  assert.match(searchView[1], /!state\.project\.status\.authorized[\s\S]*?elements\.searchProjectFiles/);
  assert.doesNotMatch(source, /ipcRenderer|require\(['"]node:fs|fetch\(/);
});

test('renderer confirms before removing a local scheduled task', () => {
  const source = fs.readFileSync(scriptPath, 'utf8');
  const deletion = source.match(/function deleteScheduleTask\(task\) \{([\s\S]*?)\n\}/);
  assert.ok(deletion);
  assert.match(source, /scheduleActionButton\('删除', \(\) => \{\s+deleteScheduleTask\(task\);/);
  assert.match(deletion[1], /window\.confirm\(/);
  assert.match(deletion[1], /scheduleStore\.remove\(task\.id\)/);
  assert.ok(deletion[1].indexOf('window.confirm(') < deletion[1].indexOf('scheduleStore.remove(task.id)'));
});

test('renderer has a public model-plaza renderer', () => {
  assert.match(fs.readFileSync(scriptPath, 'utf8'), /function renderModelPlazaView\(\)/);
});

test('inspector has contextual status cards instead of blank change and terminal placeholders', () => {
  const markup = fs.readFileSync(path.join(renderer, 'index.html'), 'utf8');
  assert.match(markup, /id="inspector-view-summary"/);
  assert.match(markup, /id="inspector-schedule-summary"/);
  assert.doesNotMatch(markup, />\s*稍后启用\s*</);
});

test('inspector refreshes through focused view, schedule, and project-root transition paths', () => {
  const source = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
  const selectView = source.match(/function selectActiveView\(view\) \{([\s\S]*?)\n\}/);
  const scheduleRefresh = source.match(/function renderScheduleState\(\) \{([\s\S]*?)\n\}/);
  const deleteSchedule = source.match(/function deleteScheduleTask\(task\) \{([\s\S]*?)\n\}/);
  const submitSchedule = source.match(/function submitScheduleForm\(event\) \{([\s\S]*?)\n\}/);
  const scheduleView = source.match(/function renderScheduleView\(\) \{([\s\S]*?)\n\}\n\nfunction renderActiveView/);
  const projectRefresh = source.match(/function renderProjectState\(\) \{([\s\S]*?)\n\}/);
  const projectStatus = source.match(/async function refreshProjectStatus\(\) \{([\s\S]*?)\n\}\n\nasync function openProject/);
  const openProject = source.match(/async function openProject\(\) \{([\s\S]*?)\n\}\n\nasync function searchProject/);

  for (const match of [selectView, scheduleRefresh, deleteSchedule, submitSchedule, scheduleView, projectRefresh, projectStatus, openProject]) {
    assert.ok(match);
  }
  assert.match(selectView[1], /renderActiveView\(\);\s+renderInspector\(\);/);
  assert.match(scheduleRefresh[1], /renderScheduleView\(\);\s+renderInspector\(\);/);
  assert.match(deleteSchedule[1], /scheduleStore\.remove\(task\.id\);\s+renderScheduleState\(\);/);
  assert.match(submitSchedule[1], /scheduleStore\.create\(title\);[\s\S]*renderScheduleState\(\);/);
  for (const mutation of ['rename', 'complete', 'cancel', 'reopen']) {
    assert.match(scheduleView[1], new RegExp(`scheduleStore\\.${mutation}\\([\\s\\S]*?renderScheduleState\\(\\);`));
  }
  assert.match(projectRefresh[1], /renderProject\(\);\s+renderInspector\(\);/);
  assert.match(projectStatus[1], /clearProjectWorkspace\(status\);\s+renderProjectState\(\);/);
  assert.match(projectStatus[1], /clearProjectWorkspace\(\{ authorized: false \}\);\s+renderProjectState\(\);/);
  assert.match(openProject[1], /clearProjectWorkspace\(status\);\s+renderProjectState\(\);/);
});

test('project inspector renders safe editor facts and refreshes after tab state transitions', () => {
  const source = fs.readFileSync(scriptPath, 'utf8').replace(/\r\n/g, '\n');
  const tabs = source.match(/function renderProjectTabs\(\) \{([\s\S]*?)\n\}\n\nfunction mountProjectEditor/);
  const editor = source.match(/function mountProjectEditor\(tab\) \{([\s\S]*?)\n\}\n\nfunction renderProject/);
  const inspector = source.match(/function renderInspector\(\) \{([\s\S]*?)\n\}\n\nfunction renderProjectFailure/);
  const openFile = source.match(/async function openProjectFile\(projectPath\) \{([\s\S]*?)\n\}\n\nasync function saveActiveTab/);
  const save = source.match(/async function saveActiveTab\(\) \{([\s\S]*?)\n\}\n\nfunction submitScheduleForm/);
  const close = source.match(/function closeProjectTab\(projectPath\) \{([\s\S]*?)\n\}\n\nfunction setCheckpoint/);

  for (const match of [tabs, editor, inspector, openFile, save, close]) assert.ok(match);
  for (const id of ['inspector-project-changes', 'inspector-project-active-tab']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(inspector[1], /projectInspectorState\(state\.project\)/);
  assert.match(inspector[1], /本地修改的已打开文件：\$\{projectFacts\.dirtyFileCount\}/);
  assert.match(inspector[1], /活动编辑器：/);
  assert.doesNotMatch(inspector[1], /projectApi|fetch\s*\(|ipcRenderer|require\s*\(/);
  assert.match(tabs[1], /state\.project\.activeTabId = tab\.path;\s+renderProjectState\(\);/);
  assert.match(editor[1], /tab\.dirty = content !== tab\.originalContent;\s+renderProjectTabs\(\);\s+renderInspector\(\);/);
  assert.match(openFile[1], /state\.project\.activeTabId = tab\.path;[\s\S]*?\}\)\) renderProjectState\(\);/);
  assert.match(save[1], /renderProjectTabs\(\);\s+renderInspector\(\);/);
  assert.match(close[1], /renderProjectState\(\);/);
});
