"""Server configuration from the process environment only; disabled by default."""

import os
from pathlib import Path


def _parse_key_pool(value):
    """Return non-empty comma-separated API keys without logging their values."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_model_prices(value):
    """Parse ``model=integer_micro_points`` entries into integer-only prices."""
    prices = {}
    for entry in (item.strip() for item in value.split(",") if item.strip()):
        model, separator, amount = entry.partition("=")
        if not separator or not model.strip():
            raise ValueError("MODEL_PRICES_MICRO_POINTS entries must use model=integer")
        try:
            parsed_amount = int(amount.strip())
        except ValueError as exc:
            raise ValueError("MODEL_PRICES_MICRO_POINTS values must be integers") from exc
        if parsed_amount <= 0:
            raise ValueError("MODEL_PRICES_MICRO_POINTS values must be positive")
        prices[model.strip().lower()] = parsed_amount
    return prices

# Generic channels remain distinct; no upstream endpoints are bundled.
GATEWAY_COMPAT_API_KEY = os.getenv("GATEWAY_COMPAT_API_KEY", "")
GATEWAY_SECONDARY_API_KEY = os.getenv("GATEWAY_SECONDARY_API_KEY", "")
GATEWAY_MAIN_API_KEY = os.getenv("MODEL_GATEWAY_API_KEY", "").strip()
DEFAULT_MODEL_RUNTIME = os.getenv(
    "DEFAULT_MODEL_RUNTIME", "gateway_main" if GATEWAY_MAIN_API_KEY else "legacy"
).strip().lower()
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
EXCHANGE_RATE = int(os.getenv("EXCHANGE_RATE", "100"))
TOKEN_CHARS_PER_TOKEN = int(os.getenv("TOKEN_CHARS_PER_TOKEN", "4"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "1024"))
MAX_IMAGE_UPLOAD_BYTES = int(os.getenv("MAX_IMAGE_UPLOAD_BYTES", str(5 * 1024 * 1024)))
USE_LEGACY_API = os.getenv("USE_LEGACY_API", "").lower() in {"1", "true", "yes", "on"}
GATEWAY_COMPAT_API_KEYS = _parse_key_pool(
    os.getenv("GATEWAY_COMPAT_API_KEYS", os.getenv("GATEWAY_COMPAT_API_KEY", ""))
)
GATEWAY_SECONDARY_API_KEYS = _parse_key_pool(
    os.getenv("GATEWAY_SECONDARY_API_KEYS", os.getenv("GATEWAY_SECONDARY_API_KEY", ""))
)
MODEL_PRICES_MICRO_POINTS = _parse_model_prices(
    os.getenv("MODEL_PRICES_MICRO_POINTS", "")
)

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "chat_db")
USER_DATABASE = os.getenv("USER_DATABASE", "user_db")
MEMORY_DATABASE = os.getenv("MEMORY_DATABASE", "memory_db")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME)
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}
EMAIL_CODE_PEPPER = os.getenv("EMAIL_CODE_PEPPER", "")

SMS_AUTH_ENABLED = os.getenv("SMS_AUTH_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
ALIBABA_CLOUD_ACCESS_KEY_ID = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
ALIBABA_CLOUD_ACCESS_KEY_SECRET = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
ALIYUN_SMS_SIGN_NAME = os.getenv("ALIYUN_SMS_SIGN_NAME", "")
ALIYUN_SMS_BIND_TEMPLATE_CODE = os.getenv("ALIYUN_SMS_BIND_TEMPLATE_CODE", "100005")

GATEWAY_COMPAT_BASE_URL = os.getenv("GATEWAY_COMPAT_BASE_URL", "").strip()
GATEWAY_SECONDARY_BASE_URL = os.getenv("GATEWAY_SECONDARY_BASE_URL", "").strip()
GATEWAY_MAIN_BASE_URL = os.getenv("MODEL_GATEWAY_BASE_URL", "").strip()
MODEL_CATALOG_CACHE_PATH = Path(
    os.getenv("MODEL_CATALOG_CACHE_PATH", Path(__file__).with_name("data") / "model_catalog.json")
)
MODEL_CATALOG_PROVIDERS = {
    "gateway_main": {"url": f"{GATEWAY_MAIN_BASE_URL.rstrip('/')}/models" if GATEWAY_MAIN_BASE_URL else ""},
    "gateway_compat": {"url": f"{GATEWAY_COMPAT_BASE_URL.rstrip('/')}/models" if GATEWAY_COMPAT_BASE_URL else ""},
    "gateway_secondary": {
        "url": os.getenv("GATEWAY_SECONDARY_CATALOG_URL", "")
    },
}
MODEL_PROVIDER_CONNECTIONS = {
    "gateway_main": {
        "base_url": GATEWAY_MAIN_BASE_URL,
        "api_key_setting": "GATEWAY_MAIN_API_KEY",
    },
    "gateway_compat": {
        "base_url": GATEWAY_COMPAT_BASE_URL,
        "api_key_setting": "GATEWAY_COMPAT_API_KEY",
    },
    "gateway_secondary": {
        "base_url": GATEWAY_SECONDARY_BASE_URL,
        "api_key_setting": "GATEWAY_SECONDARY_API_KEY",
    },
}
MODEL_NAME = "deepseek-ai/DeepSeek-V4-Flash"
KIMI_MODEL_NAME = "Pro/moonshotai/Kimi-K2.6"
ZHIPU_MODEL_NAME = "zai-org/GLM-5.2"
QWEN_MODEL_NAME = "Qwen/Qwen3.6-35B-A3B"
BYTEDANCE_MODEL_NAME = "ByteDance-Seed/Seed-OSS-36B-Instruct"
QWEN_OFFICIAL_MODEL_NAME = "qwen-max"
GATEWAY_MAIN_MODEL_ALIASES = {
    "deepseek": "deepseek-v4-flash",
    "kimi": "kimi-k2.6",
    "zhipu": "glm-5.2",
    "qwen": "Qwen3.6-35B-A3B",
    "bytedance": "Seed-OSS-36B-Instruct",
    "qwen_official": "qwen3.6-max-preview",
}

GROUP_CHAT_DATABASE = "group_chat_db"
DEEPSEEK_DATABASE = "deepseek_db"
KIMI_DATABASE = "kimi_db"
ZHIPU_DATABASE = "zhipu_db"
QWEN_DATABASE = "qwen_db"
BYTEDANCE_DATABASE = "bytedance_db"

FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
MOCK_PAYMENT_CALLBACK_TOKEN = os.getenv("MOCK_PAYMENT_CALLBACK_TOKEN", "")
ENABLE_MOCK_PAYMENTS = os.getenv("ENABLE_MOCK_PAYMENTS", "").lower() in {
    "1", "true", "yes", "on"
}

# Each entry declares the provider connection used by AIAdapter.
MODEL_METADATA = {
    "deepseek": {"label": "DeepSeek", "provider": "DeepSeek", "display_name": "DeepSeek V4 Flash", "model_id": MODEL_NAME, "base_url": GATEWAY_COMPAT_BASE_URL, "api_key_setting": "GATEWAY_COMPAT_API_KEY"},
    "kimi": {"label": "Kimi", "provider": "Kimi", "display_name": "Kimi K2.6", "model_id": KIMI_MODEL_NAME, "base_url": GATEWAY_COMPAT_BASE_URL, "api_key_setting": "GATEWAY_COMPAT_API_KEY"},
    "zhipu": {"label": "智谱", "provider": "智谱", "display_name": "GLM-5.2", "model_id": ZHIPU_MODEL_NAME, "base_url": GATEWAY_COMPAT_BASE_URL, "api_key_setting": "GATEWAY_COMPAT_API_KEY"},
    "qwen": {"label": "千问", "provider": "千问", "display_name": "Qwen3.6-35B-A3B", "model_id": QWEN_MODEL_NAME, "base_url": GATEWAY_COMPAT_BASE_URL, "api_key_setting": "GATEWAY_COMPAT_API_KEY"},
    "bytedance": {"label": "字节", "provider": "字节跳动", "display_name": "Seed-OSS-36B-Instruct", "model_id": BYTEDANCE_MODEL_NAME, "base_url": GATEWAY_COMPAT_BASE_URL, "api_key_setting": "GATEWAY_COMPAT_API_KEY"},
    "qwen_official": {"label": "百炼千问", "provider": "阿里云百炼", "display_name": "Qwen-Max", "model_id": QWEN_OFFICIAL_MODEL_NAME, "base_url": GATEWAY_SECONDARY_BASE_URL, "api_key_setting": "GATEWAY_SECONDARY_API_KEY"},
}

HOST = os.getenv("HOST", "127.0.0.1")
PORT = 5000
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes", "on"}
