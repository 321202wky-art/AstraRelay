import pytest

from model_registry import ModelRegistry


class FakeCatalogService:
    def __init__(self, catalog):
        self.catalog = catalog

    def load(self):
        return self.catalog


def test_registry_resolves_only_fresh_callable_chat_models():
    catalog = {
        "providers": [
            {
                "key": "gateway_compat",
                "name": "GatewayCompat",
                "status": "fresh",
                "capabilities": {
                    "chat": [
                        {
                            "platform_model_id": "gateway_compat:org/model",
                            "model_id": "org/model",
                            "display_name": "model",
                            "capability": "chat",
                            "callable": True,
                        }
                    ]
                },
            }
        ],
        "updated_at": "2026-07-19T00:00:00Z",
    }
    registry = ModelRegistry(
        FakeCatalogService(catalog),
        {"gateway_compat": {"base_url": "https://example.test/v1", "api_key_setting": "GATEWAY_COMPAT_API_KEY"}},
    )

    assert registry.resolve("gateway_compat:org/model") == {
        "model_id": "org/model",
        "base_url": "https://example.test/v1",
        "api_key_setting": "GATEWAY_COMPAT_API_KEY",
    }


def test_registry_resolves_a_fresh_gateway_main_gpt_chat_model():
    catalog = {
        "providers": [
            {
                "key": "gateway_main",
                "name": "GatewayMain",
                "status": "fresh",
                "capabilities": {
                    "chat": [
                        {
                            "platform_model_id": "gateway_main:gpt-5.4-mini",
                            "model_id": "gpt-5.4-mini",
                            "display_name": "gpt-5.4-mini",
                            "capability": "chat",
                            "callable": True,
                        }
                    ]
                },
            }
        ],
        "updated_at": "2026-07-23T00:00:00Z",
    }
    registry = ModelRegistry(
        FakeCatalogService(catalog),
        {
            "gateway_main": {
                "base_url": "https://gateway-main.example.invalid/v1",
                "api_key_setting": "GATEWAY_MAIN_API_KEY",
            }
        },
    )

    assert registry.resolve("gateway_main:gpt-5.4-mini") == {
        "model_id": "gpt-5.4-mini",
        "base_url": "https://gateway-main.example.invalid/v1",
        "api_key_setting": "GATEWAY_MAIN_API_KEY",
    }


@pytest.mark.parametrize("status, callable_model", [("stale", True), ("fresh", False)])
def test_registry_rejects_stale_or_non_callable_models(status, callable_model):
    catalog = {
        "providers": [
            {
                "key": "gateway_compat",
                "name": "GatewayCompat",
                "status": status,
                "capabilities": {
                    "chat": [
                        {
                            "platform_model_id": "gateway_compat:org/model",
                            "model_id": "org/model",
                            "display_name": "model",
                            "capability": "chat",
                            "callable": callable_model,
                        }
                    ]
                },
            }
        ],
        "updated_at": None,
    }
    registry = ModelRegistry(FakeCatalogService(catalog), {"gateway_compat": {}})

    with pytest.raises(ValueError, match="模型不存在或暂不可用"):
        registry.resolve("gateway_compat:org/model")
