import ast
from pathlib import Path
import re

import pytest


README = Path(__file__).resolve().parents[1] / "README.md"
ENV_EXAMPLE = README.parent / ".env.example"
ARCHITECTURE = README.parent / "docs" / "architecture.md"
SECURITY = README.parent / "SECURITY.md"
PUBLIC_FILES = (README, ENV_EXAMPLE, ARCHITECTURE, SECURITY)
# Synthetic only: actual release policy is enforced by the staging scanner.
SYNTHETIC_TRANSPORT_MARKERS = ("private-transport-example.invalid",)


def assert_public_transport_neutrality(public_files, forbidden_markers):
    normalized_markers = tuple(marker.casefold() for marker in forbidden_markers)
    for filename, contents in public_files.items():
        if any(marker in contents.casefold() for marker in normalized_markers):
            raise AssertionError(f"{filename}: private transport marker detected")


def test_public_files_are_neutral_under_supplied_transport_marker_policy():
    public_files = {path.name: path.read_text(encoding="utf-8") for path in PUBLIC_FILES}

    assert_public_transport_neutrality(public_files, SYNTHETIC_TRANSPORT_MARKERS)


@pytest.mark.parametrize("public_file", PUBLIC_FILES, ids=lambda path: path.name)
def test_public_files_reject_injected_private_transport_marker_without_echoing_it(public_file):
    public_files = {path.name: path.read_text(encoding="utf-8") for path in PUBLIC_FILES}
    marker = SYNTHETIC_TRANSPORT_MARKERS[0]
    public_files[public_file.name] += "\n# " + marker.upper() + "\n"

    with pytest.raises(AssertionError) as failure:
        assert_public_transport_neutrality(public_files, SYNTHETIC_TRANSPORT_MARKERS)

    assert str(failure.value) == f"{public_file.name}: private transport marker detected"
    assert marker.casefold() not in str(failure.value).casefold()


def test_public_readme_describes_generic_collaboration_architecture():
    readme = README.read_text(encoding="utf-8")

    for concept in (
        "AstraRelay",
        "orchestrator-worker",
        "主脑",
        "工作者",
        "Adapter Gateway",
        "有界共享上下文",
        "token 压缩",
        "统一工作台",
        "多租户 SaaS",
    ):
        assert concept in readme


def test_public_readme_separates_project_practice_from_audited_benchmarks():
    readme = README.read_text(encoding="utf-8")

    assert "个人全职探索" in readme
    assert "峰值超过 100M tokens" in readme
    assert "作者自述" in readme
    assert "内部项目实践指标" in readme
    assert "未经第三方审计" in readme
    assert "不代表并发、吞吐或性能基准" in readme


def test_public_readme_states_scope_and_user_control_explicitly():
    readme = README.read_text(encoding="utf-8")

    assert "AI Team OS" in readme
    assert "未实现 Director / Agent / Memory 的业务集成" in readme
    assert "不硬编码供应商或模型" in readme
    assert "用户显式选择" in readme
    assert "新会话不预选模型" in readme
    assert "UI" in readme
    assert "未开放真实公众充值、收费或提现" in readme
    assert "尚未完成商业化" in readme


def test_public_readme_distinguishes_full_runtime_from_optional_disabled_preview():
    readme = README.read_text(encoding="utf-8")

    assert "模型调用保持禁用" in readme
    assert "发布侧部署契约" in readme
    assert "public_demo.py" in readme
    assert "内存" in readme
    assert "不创建" in readme
    assert "不能仅复制模板就启动完整服务" in readme
    assert "python app.py" in readme
    assert "http://127.0.0.1:5000" in readme
    assert "PYTHON_DOTENV_DISABLED" in readme
    assert "python -m pytest -q" in readme
    assert "完整脱敏开发源码" in readme
    assert "不是五文件摘录" in readme
    assert "npm test" in readme
    assert "不自动读取" in readme
    assert "不能把全部空值直接导入" in readme
    assert "单日" in readme
    for route in ("/", "/login", "/workspace"):
        assert route in readme


def test_public_env_example_contains_only_empty_generic_server_placeholders():
    assignments = [
        line.partition("=")
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    expected = {
        "MODEL_GATEWAY_BASE_URL",
        "MODEL_GATEWAY_API_KEY",
        "DATABASE_URL",
        "FLASK_SECRET_KEY",
        "GATEWAY_COMPAT_API_KEY", "GATEWAY_COMPAT_API_KEYS", "GATEWAY_COMPAT_BASE_URL",
        "GATEWAY_SECONDARY_API_KEY", "GATEWAY_SECONDARY_API_KEYS",
        "GATEWAY_SECONDARY_BASE_URL", "GATEWAY_SECONDARY_CATALOG_URL",
        "DEFAULT_MODEL_RUNTIME", "USE_LEGACY_API", "MODEL_CATALOG_CACHE_PATH",
        "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE",
        "USER_DATABASE", "MEMORY_DATABASE", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME",
        "SMTP_PASSWORD", "SMTP_FROM_EMAIL", "SMTP_USE_TLS", "EMAIL_CODE_PEPPER",
        "SMS_AUTH_ENABLED", "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "ALIYUN_SMS_SIGN_NAME", "ALIYUN_SMS_BIND_TEMPLATE_CODE", "ADMIN_USERNAME",
        "ADMIN_PASSWORD", "MOCK_PAYMENT_CALLBACK_TOKEN", "ENABLE_MOCK_PAYMENTS",
        "EXCHANGE_RATE", "TOKEN_CHARS_PER_TOKEN", "MAX_OUTPUT_TOKENS",
        "MAX_IMAGE_UPLOAD_BYTES", "MODEL_PRICES_MICRO_POINTS", "HOST", "FLASK_DEBUG",
    }

    assert len(assignments) == len(expected)
    assert {name for name, separator, value in assignments} == expected
    assert all(separator == "=" and value == "" for name, separator, value in assignments)
    # Read source syntax, never import config or inspect process/local configuration.
    syntax = ast.parse((README.parent / "config.py").read_text(encoding="utf-8"))
    supported = {
        node.args[0].value
        for node in ast.walk(syntax)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
        and node.func.attr == "getenv" and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert expected == supported | {"DATABASE_URL"}
    readme = README.read_text(encoding="utf-8")
    architecture = ARCHITECTURE.read_text(encoding="utf-8")
    assert all(name in readme + architecture for name in expected)
    assert "仅服务端" in readme
    assert "不纳入版本控制" in readme


def test_full_source_docs_link_real_modules_and_disclose_deployment_risk():
    readme = README.read_text(encoding="utf-8")
    architecture = ARCHITECTURE.read_text(encoding="utf-8")
    for path in (
        "app.py", "ai_adapter.py", "billing_gateway.py", "database.py", "user_database.py",
        "memory/access.py", "payments/providers.py", "desktop/src/main.cjs",
        "tests/test_app.py", "docs/architecture.md", "SECURITY.md",
    ):
        assert f"]({path})" in readme
    for document in (README, ARCHITECTURE, SECURITY):
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            assert (document.parent / target.split("#", 1)[0]).exists(), target
    for fact in ("history_limit", "24", "from_now", "system", "Model Router", "DATABASE_URL"):
        assert fact in architecture
    security = SECURITY.read_text(encoding="utf-8")
    for fact in ("2026-10-20", "30", "25", "5", "0", "Electron", "CVE"):
        assert fact in security
    assert "SECURITY.md" in readme
