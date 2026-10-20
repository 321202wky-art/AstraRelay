import json

import pytest

from public_model_directory import PUBLIC_MODEL_UNAVAILABLE, PublicModelDirectory


class FakeCatalogService:
    def load(self):
        return {
            "availability": "degraded",
            "last_attempt_at": "2026-07-28T00:05:00Z",
            "last_success_at": "2026-07-28T00:00:00Z",
            "failure_categories": ["transport"],
            "retryable": True,
            "message": "模型目录暂时无法更新，当前仅展示最近一次可用信息",
            "updated_at": "2026-07-28T00:00:00Z",
            "providers": [
                {
                    "key": "gateway_main",
                    "name": "GatewayMain",
                    "status": "fresh",
                    "capabilities": {
                        "chat": [
                            {
                                "platform_model_id": "gateway_main:deepseek/deepseek-v3",
                                "model_id": "deepseek/deepseek-v3",
                                "display_name": "DeepSeek V3",
                                "capability": "chat",
                                "callable": True,
                                "released_at": "2026-07-01T00:00:00Z",
                            },
                            {
                                "platform_model_id": "gateway_main:kimi-k3",
                                "model_id": "kimi-k3",
                                "display_name": "Kimi K3",
                                "capability": "chat",
                                "callable": True,
                            },
                            {
                                "platform_model_id": "gateway_main:gpt-5.4-mini",
                                "model_id": "gpt-5.4-mini",
                                "display_name": "GPT 5.4 Mini",
                                "capability": "chat",
                                "callable": True,
                            },
                            {
                                "platform_model_id": "gateway_main:qwen/qwen-max",
                                "model_id": "qwen/qwen-max",
                                "display_name": "Qwen Max",
                                "capability": "chat",
                                "callable": True,
                            },
                            {
                                "platform_model_id": "gateway_main:disabled-model",
                                "model_id": "disabled-model",
                                "display_name": "Disabled Model",
                                "capability": "chat",
                                "callable": False,
                            },
                        ],
                        "image": [
                            {
                                "platform_model_id": "gateway_main:gpt-image-2",
                                "model_id": "gpt-image-2",
                                "display_name": "GPT Image 2",
                                "capability": "image",
                                "callable": True,
                            }
                        ],
                    },
                },
                {
                    "key": "gateway_secondary",
                    "name": "GatewaySecondary",
                    "status": "stale",
                    "capabilities": {
                        "chat": [
                            {
                                "platform_model_id": "gateway_secondary:qwen-plus",
                                "model_id": "qwen-plus",
                                "display_name": "Qwen Plus",
                                "capability": "chat",
                                "callable": True,
                            }
                        ]
                    },
                },
            ],
        }


def test_public_catalog_groups_models_by_brand_without_upstream_metadata():
    catalog = PublicModelDirectory(FakeCatalogService()).catalog()

    assert [group["name"] for group in catalog["providers"]] == [
        "DeepSeek",
        "Kimi",
        "GPT",
        "千问",
    ]
    serialized = json.dumps(catalog, ensure_ascii=False).lower()
    for forbidden in (
        "gateway_main",
        "gateway_secondary",
        "gateway_compat",
        "base_url",
        "api_key",
        '"provider"',
    ):
        assert forbidden not in serialized


def test_public_catalog_contains_fresh_chat_and_specialized_models():
    catalog = PublicModelDirectory(FakeCatalogService()).catalog()
    models = [
        model
        for group in catalog["providers"]
        for entries in group["capabilities"].values()
        for model in entries
    ]
    model_ids = {model["platform_model_id"] for model in models}

    assert len(model_ids) == 6
    assert all(not model_id.startswith("gateway_main:") for model_id in model_ids)
    assert any("image" in model_id for model_id in model_ids)
    assert not any("disabled" in model_id for model_id in model_ids)


def test_stale_models_are_safe_read_only_metadata_and_fresh_models_remain_selectable():
    directory = PublicModelDirectory(FakeCatalogService())
    catalog = directory.catalog()
    qwen_group = next(group for group in catalog["providers"] if group["key"] == "qwen")
    models = qwen_group["capabilities"]["chat"]
    stale = next(model for model in models if model["display_name"] == "Qwen Plus")
    fresh = next(model for model in models if model["display_name"] == "Qwen Max")

    assert catalog["availability"] == "degraded"
    assert catalog["last_attempt_at"] == "2026-07-28T00:05:00Z"
    assert catalog["last_success_at"] == "2026-07-28T00:00:00Z"
    assert catalog["updated_at"] == catalog["last_attempt_at"]
    assert catalog["failure_categories"] == ["transport"]
    assert catalog["retryable"] is True
    assert qwen_group["status"] == "stale"
    assert stale["availability"] == "degraded"
    assert stale["callable"] is False
    assert stale["selectable"] is False
    assert fresh["availability"] == "fresh"
    assert fresh["callable"] is True
    assert fresh["selectable"] is True
    with pytest.raises(ValueError, match=PUBLIC_MODEL_UNAVAILABLE):
        directory.resolve(stale["platform_model_id"])


def test_stale_known_models_can_be_serialized_but_not_selected():
    directory = PublicModelDirectory(FakeCatalogService())
    stale_internal = "gateway_secondary:qwen-plus"

    exposed = directory.to_public_settings(
        [{"model_id": stale_internal, "context_mode": "summary", "position": 0}]
    )

    assert len(exposed) == 1
    assert exposed[0]["model_id"].startswith("qwen/")
    with pytest.raises(ValueError, match=PUBLIC_MODEL_UNAVAILABLE):
        directory.to_internal_settings(exposed)


def test_stale_model_becomes_resolvable_after_the_same_directory_recovers_fresh():
    class MutableCatalog:
        def __init__(self):
            self.status = "stale"

        def load(self):
            return {
                "availability": "degraded" if self.status == "stale" else "fresh",
                "last_attempt_at": "2026-08-10T08:00:00Z",
                "last_success_at": "2026-08-10T07:00:00Z",
                "failure_categories": ["transport"] if self.status == "stale" else [],
                "retryable": self.status == "stale",
                "message": "safe" if self.status == "stale" else None,
                "updated_at": "2026-08-10T08:00:00Z",
                "providers": [
                    {
                        "key": "private",
                        "name": "Private",
                        "status": self.status,
                        "capabilities": {
                            "chat": [
                                {
                                    "platform_model_id": "private:kimi-recovery",
                                    "model_id": "kimi-recovery",
                                    "display_name": "Kimi Recovery",
                                    "capability": "chat",
                                    "callable": True,
                                }
                            ]
                        },
                    }
                ],
            }

    catalog_service = MutableCatalog()
    directory = PublicModelDirectory(catalog_service)
    public_id = directory.catalog()["providers"][0]["capabilities"]["chat"][0][
        "platform_model_id"
    ]

    with pytest.raises(ValueError, match=PUBLIC_MODEL_UNAVAILABLE):
        directory.resolve(public_id)

    catalog_service.status = "fresh"

    assert directory.resolve(public_id) == "private:kimi-recovery"


def test_public_catalog_exposes_capability_profiles_without_private_metadata():
    catalog = PublicModelDirectory(FakeCatalogService()).catalog()
    models = [
        model
        for group in catalog["providers"]
        for entries in group["capabilities"].values()
        for model in entries
    ]
    deepseek = next(model for model in models if model["display_name"] == "DeepSeek V3")
    kimi_k3 = next(model for model in models if model["display_name"] == "Kimi K3")
    image = next(model for model in models if model["display_name"] == "GPT Image 2")

    assert deepseek["model_type"] == "纯文本模型"
    assert deepseek["capability_note"] == "注：此模型为纯文本模型"
    assert "text_chat" in deepseek["supports"]
    assert "vision" in deepseek["unsupported"]
    assert kimi_k3["capability_status"] == "verified"
    assert "vision" in kimi_k3["supports"]
    assert "video" in kimi_k3["supports"]
    assert "image_generation" not in kimi_k3["supports"]
    assert image["model_type"] == "图像生成/编辑模型"
    assert image["supports"] == ["image_generation"]


def test_specialized_public_models_cannot_be_resolved_as_chat_models():
    directory = PublicModelDirectory(FakeCatalogService())
    catalog = directory.catalog()
    image_id = next(
        model["platform_model_id"]
        for group in catalog["providers"]
        for model in group["capabilities"].get("image", [])
    )

    with pytest.raises(ValueError, match=PUBLIC_MODEL_UNAVAILABLE):
        directory.resolve(image_id)


def test_public_ids_round_trip_without_exposing_private_ids():
    directory = PublicModelDirectory(FakeCatalogService())

    public_id = directory.expose("gateway_main:deepseek/deepseek-v3")

    assert public_id.startswith("deepseek/")
    assert "gateway_main" not in public_id
    assert directory.resolve(public_id) == "gateway_main:deepseek/deepseek-v3"


def test_colliding_public_slugs_get_deterministic_distinct_ids():
    class CollisionCatalog:
        def load(self):
            return {
                "providers": [
                    {
                        "key": "one",
                        "name": "Provider One",
                        "status": "fresh",
                        "capabilities": {
                            "chat": [
                                {
                                    "platform_model_id": "one:openai/gpt-5",
                                    "model_id": "openai/gpt-5",
                                    "display_name": "GPT 5",
                                    "capability": "chat",
                                    "callable": True,
                                },
                                {
                                    "platform_model_id": "one:gpt-5",
                                    "model_id": "gpt-5",
                                    "display_name": "GPT 5",
                                    "capability": "chat",
                                    "callable": True,
                                },
                            ]
                        },
                    }
                ],
                "updated_at": None,
            }

    first = PublicModelDirectory(CollisionCatalog()).catalog()
    second = PublicModelDirectory(CollisionCatalog()).catalog()
    first_ids = [model["platform_model_id"] for model in first["providers"][0]["capabilities"]["chat"]]
    second_ids = [model["platform_model_id"] for model in second["providers"][0]["capabilities"]["chat"]]

    assert first_ids == second_ids
    assert len(set(first_ids)) == 2
    assert all(model_id.startswith("gpt/") for model_id in first_ids)


def test_unknown_public_model_uses_one_safe_error():
    directory = PublicModelDirectory(FakeCatalogService())

    with pytest.raises(ValueError, match=PUBLIC_MODEL_UNAVAILABLE):
        directory.resolve("unknown/private-model")


def test_settings_messages_and_presets_translate_without_mutating_inputs():
    directory = PublicModelDirectory(FakeCatalogService())
    public_id = directory.expose("gateway_main:kimi-k3")
    public_settings = [
        {
            "model_id": public_id,
            "context_mode": "summary",
            "message_ids": [1, 2],
            "position": 0,
        }
    ]

    internal = directory.to_internal_settings(public_settings)
    exposed = directory.to_public_settings(internal)
    messages = directory.expose_messages(
        [{"id": 1, "role": "assistant", "model": "gateway_main:kimi-k3"}]
    )
    presets = directory.expose_presets([{"id": 9, "name": "组合", "items": internal}])

    assert public_settings[0]["model_id"] == public_id
    assert internal[0]["model_id"] == "gateway_main:kimi-k3"
    assert exposed[0]["model_id"] == public_id
    assert messages[0]["model"] == public_id
    assert presets[0]["items"][0]["model_id"] == public_id
