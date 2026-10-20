import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_project_file(*parts: str) -> str:
    return (PROJECT_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_templates_define_the_document_and_asset_contracts():
    login = read_project_file("templates", "login.html")
    register = read_project_file("templates", "register.html")
    base = read_project_file("templates", "base.html")
    workspace = read_project_file("templates", "chat", "index.html")
    workspace_base = read_project_file("templates", "workspace_base.html")

    for template in (login, register, base):
        assert '<html lang="zh-CN">' in template
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in template

    assert "filename='login.css'" in login
    assert "filename='login.css'" in register
    assert 'name="password_confirm"' in register
    assert 'extends "workspace_base.html"' in workspace
    assert "filename='workspace.css'" in workspace_base
    assert "filename='workspace.js'" in workspace


def test_templates_expose_the_semantic_login_card_and_mobile_agent_toggle():
    login = read_project_file("templates", "login.html")
    register = read_project_file("templates", "register.html")
    workspace = read_project_file("templates", "chat", "index.html")

    assert '<section class="login-card" aria-labelledby="login-title">' in login
    assert '<section class="login-card" aria-labelledby="register-title">' in register
    assert 'id="mobile-agent-toggle"' in workspace


def test_workspace_template_integrates_the_immersive_chat_class_contract():
    workspace = read_project_file("templates", "chat", "index.html")
    script = read_project_file("static", "workspace.js")
    stylesheet = read_project_file("static", "workspace.css")

    assert 'extends "workspace_base.html"' in workspace
    assert 'class="chat-panel chat-container"' in workspace
    assert 'class="composer input-area"' in workspace
    assert 'type="text"' in workspace
    assert ".message-bubble" in stylesheet
    assert ".assistant-message" in stylesheet
    assert ".user-message" in stylesheet
    assert "message-bubble" in script
    assert "ai-message" in script
    assert "user-message" in script


def test_workspace_uses_light_surface_tokens():
    stylesheet = read_project_file("static", "workspace.css")

    for token in (
        "--page:",
        "--surface:",
        "--surface-muted:",
        "--text:",
        "--text-muted:",
        "--brand:",
        "--line:",
    ):
        assert token in stylesheet

    assert "color-scheme: light;" in stylesheet
    assert "--navy-950" not in stylesheet


def test_authentication_styles_share_the_workspace_light_design_language():
    auth_css = read_project_file("static", "login.css")

    for token in (
        "--page: #f6f8fc;",
        "--surface:",
        "--text: #172033;",
        "--brand: #5267d6;",
        "--line: #dbe3ef;",
    ):
        assert token in auth_css
    assert "color-scheme: light;" in auth_css
    assert "background: #060b17" not in auth_css


def test_workspace_keeps_playful_light_depth_in_primary_regions():
    stylesheet = read_project_file("static", "workspace.css")

    for selector in (
        ".session-sidebar, .agent-sidebar",
        ".chat-panel",
        ".composer",
        ".model-drawer",
        ".collaboration-group",
    ):
        assert selector in stylesheet

    assert "--surface-raised:" in stylesheet
    assert "linear-gradient(180deg, var(--surface), var(--surface-raised))" in stylesheet
    assert "linear-gradient(180deg, #f2f6fc, #eaf0f9)" in stylesheet
    assert "box-shadow: 0 .5rem 1.25rem rgb(82 103 214 / 8%);" in stylesheet


def test_workspace_contains_desktop_task_navigation_and_collaboration_panel():
    workspace = read_project_file("templates", "chat", "index.html")

    assert 'id="task-navigation"' in workspace
    assert 'id="task-composer"' in workspace
    assert 'id="collaboration-panel"' in workspace


def test_workspace_exposes_a_closed_model_drawer_and_no_model_guard():
    workspace = read_project_file("templates", "chat", "index.html")
    script = read_project_file("static", "workspace.js")

    for identifier in (
        "model-drawer",
        "selected-model-list",
        "model-tree",
        "model-search",
        "context-mode",
        "no-model-notice",
        "stop-generation",
        "collaboration-groups",
        "capability-dialog",
        "capability-dialog-message",
        "capability-recommendations",
        "capability-dialog-cancel",
        "capability-dialog-continue",
    ):
        assert f'id="{identifier}"' in workspace
    assert 'aria-controls="model-drawer"' in workspace
    assert 'placeholder="&#32473; AstraRelay &#21457;&#36865;&#28040;&#24687;"' in workspace
    assert "const workspaceModelStore" in script
    assert "workspaceModelStore.actionableSettings()" in script
    assert "activeSettings.length === 0" in script
    assert "setModelDrawerOpen(true)" in script


def test_workspace_capability_dialog_is_accessible_and_touch_friendly():
    workspace = read_project_file("templates", "chat", "index.html")
    stylesheet = read_project_file("static", "workspace.css")

    assert '<dialog id="capability-dialog"' in workspace
    assert 'aria-labelledby="capability-dialog-title"' in workspace
    assert 'aria-describedby="capability-dialog-message"' in workspace
    assert '.capability-dialog' in stylesheet
    assert '.capability-dialog button' in stylesheet
    assert 'min-height: 2.75rem;' in stylesheet


def test_workspace_model_drawer_exposes_safe_catalog_availability_status():
    workspace = read_project_file("templates", "chat", "index.html")

    assert 'id="workspace-catalog-status"' in workspace
    assert 'id="workspace-catalog-message"' in workspace
    assert 'id="workspace-catalog-detail"' in workspace
    assert 'id="workspace-catalog-retry"' in workspace
    assert 'aria-live="polite"' in workspace


def test_workspace_navigation_is_admin_safe_and_quick_prompts_are_utf8():
    workspace = read_project_file("templates", "chat", "index.html")

    assert "{% if not session.get('is_admin') %}" in workspace
    assert 'class="rail-home-link" href="{{ url_for(\'home\') }}"' in workspace
    assert 'href="/account/security"' in workspace
    assert 'data-prompt="帮我梳理今天最重要的三项工作"' in workspace
    assert '>梳理今日工作</button>' in workspace


def test_public_navigation_contains_no_unimplemented_routes():
    base = read_project_file("templates", "base.html")

    for dead_route in ('/app-download', '/pricing', '/api-docs'):
        assert dead_route not in base


def test_workspace_composer_uses_the_responsive_grid_without_inline_overrides():
    workspace = read_project_file("templates", "chat", "index.html")
    workspace_css = read_project_file("static", "workspace.css")

    assert ".composer.input-area" not in workspace
    assert ".composer { position: sticky;" in workspace_css
    assert "min-height: 0;" in workspace_css


def test_registration_auth_switch_uses_a_plain_semantic_paragraph():
    register = read_project_file("templates", "register.html")

    assert '<p class="auth-switch">' in register
    assert '<p class="auth-switch" aria-label=' not in register


def test_email_registration_and_verification_forms_are_accessible():
    register = read_project_file("templates", "register.html")
    verify_email = read_project_file("templates", "verify_email.html")
    login_css = read_project_file("static", "login.css")

    assert 'type="email"' in register
    assert 'autocomplete="email"' in register
    assert 'name="password_confirm"' in register
    assert 'name="_csrf_token" value="{{ csrf_token() }}"' in register
    assert 'role="alert"' in register

    assert 'class="auth-layout"' in verify_email
    assert 'name="code"' in verify_email
    assert 'autocomplete="one-time-code"' in verify_email
    assert 'inputmode="numeric"' in verify_email
    assert 'pattern="[0-9]{6}"' in verify_email
    assert 'name="_csrf_token" value="{{ csrf_token() }}"' in verify_email
    assert 'role="alert"' in verify_email
    assert ".verification-code" in login_css


def test_stylesheets_define_focus_and_reduced_motion_contracts():
    login_css = read_project_file("static", "login.css")
    workspace_css = read_project_file("static", "workspace.css")

    for stylesheet in (login_css, workspace_css):
        assert ":focus-visible" in stylesheet
        assert "@media (prefers-reduced-motion: reduce)" in stylesheet


def test_responsive_workspace_and_login_safety_contracts():
    login_css = read_project_file("static", "login.css")
    workspace_css = read_project_file("static", "workspace.css")

    assert "@media (min-width: 768px)" in workspace_css
    assert "@media (min-width: 1024px)" in workspace_css
    assert "overflow-x: hidden;" in workspace_css
    assert "min-width: 2.75rem;" in workspace_css
    assert "min-height: 2.75rem;" in workspace_css
    assert "overflow-y: auto;" in login_css


def test_workspace_uses_text_only_model_identity_without_provider_logo_assets():
    workspace_js = read_project_file("static", "workspace.js")
    workspace_css = read_project_file("static", "workspace.css")
    template = read_project_file("templates", "chat", "index.html")

    assert "文本协作" not in workspace_js
    assert "文字对话" in workspace_js or "&#25991;&#23383;&#23545;&#35805;" in template
    assert "createModelIdentity(" in workspace_js
    assert "createModelAvatar(" not in workspace_js
    assert "/static/model-logos/" not in workspace_js
    assert "model-logo" not in workspace_css
    assert 'id="model-picker"' not in template
    text_chip_rules = workspace_css[
        workspace_css.index(".assistant-avatar, .agent-status-avatar {"):
        workspace_css.index("}", workspace_css.index(".assistant-avatar, .agent-status-avatar {"))
        + 1
    ]
    assert "min-width: 2rem;" in text_chip_rules
    assert "max-width: 7.5rem;" in text_chip_rules
    assert "width: auto;" in text_chip_rules
    assert "overflow: visible;" in text_chip_rules


def test_auth_templates_expose_csrf_protected_administrator_entry_points():
    login = read_project_file("templates", "login.html")
    register = read_project_file("templates", "register.html")
    admin_login = read_project_file("templates", "admin_login.html")

    for template in (login, register, admin_login):
        assert 'name="_csrf_token" value="{{ csrf_token() }}"' in template

    assert "url_for('admin_login')" in login
    assert 'method="post"' in admin_login
    assert 'autocomplete="username"' in admin_login
    assert 'autocomplete="current-password"' in admin_login
    assert 'role="alert"' in admin_login
    assert "url_for('login')" in admin_login


def test_password_recovery_templates_expose_accessible_email_and_code_forms():
    login = read_project_file("templates", "login.html")
    forgot = read_project_file("templates", "forgot_password.html")
    reset = read_project_file("templates", "reset_password.html")

    assert 'id="email" type="email" name="email" autocomplete="email" required' in login
    assert "url_for('forgot_password')" in login
    for template in (forgot, reset):
        assert 'name="_csrf_token" value="{{ csrf_token() }}"' in template
        assert 'type="email" name="email" autocomplete="email" required' in template
        assert 'role="alert"' in template
    assert 'autocomplete="one-time-code"' in reset
    assert 'pattern="[0-9]{6}"' in reset
    assert reset.count('autocomplete="new-password"') == 2


def test_login_card_exposes_an_always_visible_touch_friendly_home_link():
    login = read_project_file("templates", "login.html")
    stylesheet = read_project_file("static", "login.css")

    assert 'class="auth-home-link" href="{{ url_for(\'home\') }}"' in login
    assert login.index('class="login-card"') < login.index('class="auth-home-link"')
    assert ".auth-home-link" in stylesheet
    assert "min-height: 44px;" in stylesheet


def test_reset_password_template_has_same_page_code_delivery_controls():
    reset = read_project_file("templates", "reset_password.html")

    assert 'id="send-reset-code"' in reset
    assert 'id="reset-code-status"' in reset
    assert "filename='reset_password.js'" in reset


def test_admin_users_template_limits_visible_data_and_uses_the_existing_mutation_endpoints():
    template = read_project_file("templates", "admin_users.html")

    assert "password_hash" not in template
    assert "url_for('admin_set_active'" in template
    assert "url_for('admin_set_role'" in template
    assert "url_for('admin_reset_password'" in template
    assert "url_for('logout')" in template
    assert 'name="_csrf_token" value="{{ csrf_token() }}"' in template
    assert 'name="is_active" value="true"' in template
    assert 'name="is_active" value="false"' in template
    assert '<option value="admin"' in template
    assert '<option value="user"' in template
    assert 'autocomplete="new-password"' in template
    assert "{% if user.username == current_username %}" in template
    assert "当前登录管理员" in template
    assert "{% if user.role == 'admin' %}管理员{% else %}用户{% endif %}" in template


def test_admin_styles_provide_accessible_responsive_table_behavior():
    admin_css = read_project_file("static", "admin.css")

    assert "overflow-x: auto;" in admin_css
    assert "min-height: 44px;" in admin_css
    assert ":focus-visible" in admin_css
    assert "status-chip" in admin_css
    assert "@media (prefers-reduced-motion: reduce)" in admin_css


def test_admin_users_explains_owner_scope_and_restricted_promotion():
    template = read_project_file("templates", "admin_users.html")
    styles = read_project_file("static", "admin.css")

    assert "所有者 / 超级管理员" in template
    assert "提升为管理员只授予登录资格，不授予所有者权限" in template
    assert "current_principal.access_tier == 'owner'" in template
    assert "owner-chip" in styles
    assert "admin-permission-warning" in styles


def test_each_administrator_mutation_form_has_its_own_post_action_and_csrf_token():
    template = read_project_file("templates", "admin_users.html")

    for endpoint in ("admin_set_active", "admin_set_role", "admin_reset_password"):
        action = "{{ url_for('" + endpoint + "', username=user.username) }}"
        form_pattern = (
            r'<form\b(?=[^>]*\bmethod="post")'
            + r'(?=[^>]*\baction="'
            + re.escape(action)
            + r'")[^>]*>'
            + r'(?:(?!</form>).)*?'
            + r'<input type="hidden" name="_csrf_token" value="{{ csrf_token\(\) }}">'
            + r'(?:(?!</form>).)*?</form>'
        )
        assert re.search(form_pattern, template, re.DOTALL), endpoint


def test_auth_and_administrator_templates_use_chinese_kickers():
    expected_kickers = {
        "login.html": "智能协作中心",
        "register.html": "智能协作中心",
        "admin_login.html": "管理中心",
        "admin_users.html": "管理中心",
    }

    for template_name, kicker in expected_kickers.items():
        template = read_project_file("templates", template_name)
        assert f">{kicker}</p>" in template
        assert "COMMAND CENTER" not in template
        assert "ADMINISTRATION" not in template


def test_home_and_workspace_templates_keep_visible_non_model_copy_in_chinese():
    home = read_project_file("templates", "index.html")
    workspace = read_project_file("templates", "chat", "index.html")

    assert "MULTI-AGENT COLLABORATION" in home
    assert "让多个 AI" in home
    assert "&#21327;&#20316;&#31354;&#38388;" in workspace
    assert "&#21327;&#20316;&#27169;&#22411;" in workspace

    for text in (
        "AI Chat",
        "Sign in to access the protected chat workspace.",
        "Log in",
        "Command center",
        "LIVE COLLABORATION",
        "AGENT ROSTER",
    ):
        assert text not in home
        assert text not in workspace


def test_home_and_auth_templates_expose_starfield_and_split_layout_contracts():
    home = read_project_file("templates", "index.html")
    login = read_project_file("templates", "login.html")
    register = read_project_file("templates", "register.html")
    login_css = read_project_file("static", "login.css")

    assert 'class="starfield" aria-hidden="true"' in home
    assert 'class="orbital-system" aria-hidden="true"' in home
    for template in (login, register):
        assert 'class="auth-layout"' in template
        assert 'class="auth-showcase"' in template
        assert 'class="login-card"' in template
        assert 'name="_csrf_token" value="{{ csrf_token() }}"' in template
    assert "@media (min-width: 768px)" in login_css
    assert ".auth-showcase" in login_css
    assert "@media (prefers-reduced-motion: reduce)" in login_css


def test_workspace_uses_a_dedicated_shell_without_public_site_chrome():
    workspace = read_project_file("templates", "chat", "index.html")
    shell_path = PROJECT_ROOT / "templates" / "workspace_base.html"

    assert shell_path.is_file()
    shell = shell_path.read_text(encoding="utf-8")
    assert '{% extends "workspace_base.html" %}' in workspace
    assert "topbar" not in shell
    assert "<footer" not in shell
    assert 'class="workspace-page"' in shell


def test_workspace_keeps_primary_controls_compact_and_drawer_based():
    workspace = read_project_file("templates", "chat", "index.html")
    stylesheet = read_project_file("static", "workspace.css")

    assert 'id="mobile-session-toggle"' in workspace
    assert 'id="session-sidebar-close"' in workspace
    assert 'id="session-backdrop"' in workspace
    assert 'id="workspace-empty-state"' in workspace
    assert 'id="composer-tools-toggle"' in workspace
    assert 'id="workspace-attachment-input"' in workspace
    assert 'id="workspace-attachment-list"' in workspace
    assert 'id="composer-tools-popover"' not in workspace
    assert ".model-drawer { position: fixed;" in stylesheet


def test_workspace_sidebar_and_empty_canvas_have_responsive_contracts():
    workspace = read_project_file("templates", "chat", "index.html")
    stylesheet = read_project_file("static", "workspace.css")

    assert 'aria-controls="task-navigation"' in workspace
    assert 'aria-label="关闭会话侧栏"' in workspace
    assert ".session-backdrop" in stylesheet
    assert ".app-shell.session-panel-collapsed" in stylesheet
    assert ".chat-panel.is-empty" in stylesheet
    assert "min-width: 44px" in stylesheet or "min-width: 2.75rem" in stylesheet
    assert "@media (min-width: 1024px)" in stylesheet


def test_model_plaza_hides_capability_filters_and_card_labels():
    template = read_project_file("templates", "model_plaza.html")
    script = read_project_file("static", "model-plaza.js")

    assert 'id="plaza-capability-filter"' not in template
    assert "function catalogModels(catalog)" in script
    assert "function populateCapabilityFilter" not in script
    assert "capabilityLabel(model.capability)" not in script


def test_model_plaza_exposes_a_separate_accessible_directory_status():
    template = read_project_file("templates", "model_plaza.html")

    assert 'id="plaza-directory-status"' in template
    assert 'id="plaza-directory-message"' in template
    assert 'id="plaza-directory-detail"' in template
    assert 'id="plaza-directory-retry"' in template
    assert 'aria-live="polite"' in template


def test_model_plaza_keeps_cards_in_release_order_without_workspace_links():
    template = read_project_file("templates", "model_plaza.html")
    script = read_project_file("static", "model-plaza.js")

    assert "url_for('workspace')" in template
    assert "function sortPlazaModels(entries)" in script
    assert "发布时间未提供" in script
    assert "/workspace?model=" not in script


def test_workspace_exposes_compact_attachment_controls_and_a_csrf_token():
    workspace = read_project_file("templates", "chat", "index.html")

    assert 'id="workspace-csrf-token"' in workspace
    assert 'id="workspace-attachment-input"' in workspace
    assert 'id="workspace-attachment-list"' in workspace
    assert 'accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md,.csv,.json' in workspace
    assert 'id="image-generate"' not in workspace
    assert 'id="image-analyze"' not in workspace


def test_account_security_exposes_phone_binding_forms_with_csrf():
    source = (PROJECT_ROOT / "templates" / "account_security.html").read_text(encoding="utf-8")

    assert 'action="{{ url_for(\'send_phone_binding_code\') }}"' in source
    assert 'action="{{ url_for(\'verify_phone_binding_code\') }}"' in source
    assert 'name="_csrf_token" value="{{ csrf_token() }}"' in source
    assert 'name="phone"' in source
    assert 'name="code"' in source


def test_account_security_template_keeps_phone_data_masked_and_navigation_authenticated():
    base = read_project_file("templates", "base.html")
    security = read_project_file("templates", "account_security.html")

    assert "{% if session.get('username') %}" in base
    assert "url_for('account_security')" in base
    assert "\u90ae\u7bb1\u5df2\u9a8c\u8bc1" in security
    assert "\u5fae\u4fe1\u7ed1\u5b9a\u6682\u672a\u5f00\u653e" in security
    assert "alice@example.com" not in security
    for forbidden in (
        "password_hash",
        "phone_number",
        "wechat_openid",
        "SMTP_",
    ):
        assert forbidden not in security


def test_public_and_admin_pages_use_the_shared_light_shell_contract():
    base = read_project_file("templates", "base.html")
    model_plaza = read_project_file("templates", "model_plaza.html")
    site_css = read_project_file("static", "site.css")
    admin_css = read_project_file("static", "admin.css")

    assert "filename='site.css'" in base
    assert 'class="site-shell"' in base
    assert 'class="plaza-shell"' in model_plaza
    for token in ("--surface", "--ink", "--accent", "--border"):
        assert token in site_css
    assert "var(--surface" in admin_css


def test_auth_and_admin_templates_use_shared_light_surface_markers():
    login = read_project_file("templates", "login.html")
    admin_login = read_project_file("templates", "admin_login.html")
    admin_users = read_project_file("templates", "admin_users.html")

    for template in (login, admin_login, admin_users):
        assert 'class="light-page"' in template


def test_account_security_phone_binding_controls_have_44px_minimum_height():
    source = read_project_file("templates", "account_security.html")

    for control_id in ("phone-binding-code", "phone"):
        pattern = (
            r'<input\b(?=[^>]*\bid="'
            + control_id
            + r'")(?=[^>]*\bclass="[^"]*\bmin-h-11\b)[^>]*>'
        )
        assert re.search(pattern, source)

    assert source.count("min-h-11") == 4
