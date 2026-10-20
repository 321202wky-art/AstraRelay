from pathlib import Path
import subprocess
import textwrap


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_start_scripts_install_requirements_and_launch_flask_app():
    for script_name in ("start.bat", "start.sh"):
        content = (PROJECT_ROOT / script_name).read_text(encoding="utf-8")
        assert "requirements.txt" in content
        assert "python app.py" in content
        assert "python main.py" not in content


def test_readme_describes_the_public_multi_model_preview():
    content = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for expected in ("Flask", "MySQL", "Adapter Gateway", "5000", "python public_demo.py"):
        assert expected in content


def test_readme_documents_preview_debug_is_disabled():
    content = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "debug" in content
    assert "关闭" in content


def test_chat_page_defines_dual_role_avatar_markup():
    page = (PROJECT_ROOT / "templates" / "chat" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "workspace.js").read_text(encoding="utf-8")

    assert "filename='workspace.js'" in page
    assert 'id="messages"' in page
    assert "avatar user-avatar" in script
    assert "avatar assistant-avatar" in script
    assert "avatar.textContent = '我';" in script


def test_chat_page_defines_keyboard_accessible_model_mention_menu():
    page = (PROJECT_ROOT / "templates" / "chat" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "workspace.js").read_text(encoding="utf-8")

    assert 'id="mention-menu"' in page
    for expected in (
        "const models = [",
        "name: 'DeepSeek'",
        "name: 'Kimi'",
        "name: 'GLM'",
        "name: 'Qwen'",
        "name: '豆包'",
        "input.addEventListener('keydown'",
        "ArrowDown",
        "ArrowUp",
        "event.key === 'Escape'",
        "insertMention(models[selectedModel])",
        "closeMentionMenu();",
    ):
        assert expected in script


def test_workspace_mention_input_tracks_the_active_listbox_option():
    page = (PROJECT_ROOT / "templates" / "chat" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "workspace.js").read_text(encoding="utf-8")

    assert 'id="message"' in page
    assert 'role="combobox"' in page
    assert 'aria-autocomplete="list"' in page
    assert 'aria-controls="mention-menu"' in page
    assert 'aria-expanded="false"' in page
    for expected in (
        'option.id = `mention-option-${model.id}`;',
        "input.setAttribute('aria-expanded', 'true');",
        "input.setAttribute('aria-activedescendant', `mention-option-${models[selectedModel].id}`);",
        "input.setAttribute('aria-expanded', 'false');",
        "input.removeAttribute('aria-activedescendant');",
    ):
        assert expected in script


def test_workspace_header_keeps_navigation_usable_on_narrow_screens():
    styles = (PROJECT_ROOT / "static" / "workspace.css").read_text(encoding="utf-8")

    for expected in (
        ".chat-heading {",
        ".task-heading-actions { display: flex; min-width: 0;",
        ".session-sidebar { position: fixed;",
        ".app-shell.session-panel-open .session-sidebar {",
        ".mobile-session-toggle { display: inline-grid;",
    ):
        assert expected in styles


def test_chat_page_exposes_all_connected_final_models_in_the_mention_menu():
    script = (PROJECT_ROOT / "static" / "workspace.js").read_text(encoding="utf-8")

    for expected in (
        "name: 'DeepSeek'",
        "name: 'Kimi'",
        "name: 'GLM'",
        "name: 'Qwen'",
        "name: '豆包'",
        "status: '已接入'",
    ):
        assert expected in script
    model_declarations = script[script.index("const models = ["):script.index("const modelPresentation = {")]
    assert model_declarations.count("status: '已接入'") == 6


def test_chat_page_renders_reply_model_metadata_with_text_only_identity():
    script = (PROJECT_ROOT / "static" / "workspace.js").read_text(encoding="utf-8")

    for expected in (
        "const modelPresentation = {",
        "function createModelIdentity(presentation, className)",
        "function addBubble(role, content, model = 'deepseek')",
        "const avatar = createModelIdentity(presentation, 'avatar assistant-avatar');",
        "label.textContent = presentation.name;",
        "function typeWriter(element, text, index = 0)",
        "const replyContent = addBubble('assistant', '', data.model);",
        "typeWriter(replyContent, data.reply);",
    ):
        assert expected in script
    assert "/static/model-logos/" not in script
    assert "createModelAvatar(" not in script


def test_mention_menu_uses_the_caret_active_token_and_range_replacement():
    script = (PROJECT_ROOT / "static" / "workspace.js").read_text(encoding="utf-8")

    for expected in (
        "function getActiveMention()",
        "const cursor = input.selectionStart;",
        "const beforeCursor = input.value.slice(0, cursor);",
        "beforeCursor.match(/(?:^|\\s)@[^\\s@]*$/)",
        "const activeMention = getActiveMention();",
        "input.setRangeText(`@${model.name} `, mention.start, mention.end, 'end');",
    ):
        assert expected in script


def test_escape_dismisses_only_the_current_active_mention_token():
    script = textwrap.dedent(
        """
        const fs = require('fs');
        const source = fs.readFileSync('static/workspace.js', 'utf8')
          .replace(/\\n\\s*loadHistory\\(\\);/, '');
        const handlers = {};
        const input = {
          value: '', selectionStart: 0,
          addEventListener(type, handler) { handlers[type] = handler; },
          setAttribute() {},
          removeAttribute() {},
          focus() {},
          setRangeText(value, start, end) {
            this.value = this.value.slice(0, start) + value + this.value.slice(end);
            this.selectionStart = start + value.length;
          },
        };
        const menu = { hidden: true, replaceChildren() {}, append() {} };
        const form = { addEventListener() {} };
        const send = {};
        global.document = {
          querySelector(selector) {
            return { '#message': input, '#mention-menu': menu,
              '#chat-form': form, '#send': send, '#messages': {} }[selector];
          },
          createElement() { return { setAttribute() {}, addEventListener() {} }; },
        };
        eval(source);
        input.value = 'draft @ki later';
        input.selectionStart = 9;
        handlers.input();
        if (menu.hidden) throw new Error('unfinished token did not open');
        handlers.keydown({ key: 'Escape', preventDefault() {} });
        input.value = 'draft @kim later';
        input.selectionStart = 10;
        handlers.input();
        if (!menu.hidden) throw new Error('Esc dismissal reopened for same token');
        input.value = 'draft @kim later @';
        input.selectionStart = input.value.length;
        handlers.input();
        if (menu.hidden) throw new Error('new token did not reset dismissal');
        """
    )

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
