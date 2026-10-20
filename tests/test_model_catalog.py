from urllib.error import HTTPError

from model_catalog import ModelCatalogService, normalize_provider_models


def test_normalize_gateway_main_models_preserves_public_release_time():
    models = normalize_provider_models(
        "gateway_main", [{"id": "kimi-k3", "created": 1_753_000_000}]
    )

    assert models[0]["released_at"].endswith("Z")


def test_normalize_catalog_models_assigns_namespaced_ids_and_callability():
    records = [
        {
            "id": "deepseek-ai/DeepSeek-V4-Flash",
            "type": "text",
            "sub_type": "chat",
        },
        {"id": "black-forest-labs/FLUX.1-schnell", "type": "image"},
    ]

    assert normalize_provider_models("gateway_compat", records) == [
        {
            "platform_model_id": "gateway_compat:deepseek-ai/DeepSeek-V4-Flash",
            "model_id": "deepseek-ai/DeepSeek-V4-Flash",
            "display_name": "DeepSeek-V4-Flash",
            "capability": "chat",
            "callable": True,
        },
        {
            "platform_model_id": "gateway_compat:black-forest-labs/FLUX.1-schnell",
            "model_id": "black-forest-labs/FLUX.1-schnell",
            "display_name": "FLUX.1-schnell",
            "capability": "image",
            "callable": False,
        },
    ]


def test_catalog_cache_rejects_a_model_with_secret_field(tmp_path):
    cache = tmp_path / "catalog.json"
    cache.write_text(
        """{
            "providers": [{
                "key": "gateway_compat",
                "name": "GatewayCompat",
                "status": "fresh",
                "capabilities": {"chat": [{
                    "platform_model_id": "gateway_compat:org/model",
                    "model_id": "org/model",
                    "display_name": "model",
                    "capability": "chat",
                    "callable": true,
                    "api_key": "<synthetic-credential>"
                }]}
            }]
        }""",
        encoding="utf-8",
    )

    catalog = ModelCatalogService(cache, fetchers={}).load()

    assert catalog["availability"] == "unavailable"
    assert catalog["providers"] == []
    assert catalog["updated_at"] is None


def test_normalize_gateway_compat_only_exposes_safe_capability_fields():
    records = [
        {
            "id": "deepseek-ai/DeepSeek-V4-Flash",
            "object": "model",
            "type": "text",
            "sub_type": "chat",
            "api_key": "<synthetic-credential>",
        }
    ]

    models = normalize_provider_models("gateway_compat", records)

    assert models == [
        {
            "platform_model_id": "gateway_compat:deepseek-ai/DeepSeek-V4-Flash",
            "model_id": "deepseek-ai/DeepSeek-V4-Flash",
            "display_name": "DeepSeek-V4-Flash",
            "capability": "chat",
            "callable": True,
        }
    ]


def test_normalize_gateway_compat_preserves_known_non_chat_capabilities():
    records = [{"id": "black-forest-labs/FLUX.1-schnell", "type": "image"}]

    models = normalize_provider_models("gateway_compat", records)

    assert models == [
        {
            "platform_model_id": "gateway_compat:black-forest-labs/FLUX.1-schnell",
            "model_id": "black-forest-labs/FLUX.1-schnell",
            "display_name": "FLUX.1-schnell",
            "capability": "image",
            "callable": False,
        }
    ]


def test_normalize_gateway_main_models_keeps_gpt_and_marks_chat_callable():
    assert normalize_provider_models("gateway_main", [{"id": "gpt-5.4-mini"}]) == [
        {
            "platform_model_id": "gateway_main:gpt-5.4-mini",
            "model_id": "gpt-5.4-mini",
            "display_name": "gpt-5.4-mini",
            "capability": "chat",
            "callable": True,
        }
    ]


def test_normalize_gateway_main_models_marks_documented_video_model_non_callable():
    assert normalize_provider_models("gateway_main", [{"id": "sora-2"}]) == [
        {
            "platform_model_id": "gateway_main:sora-2",
            "model_id": "sora-2",
            "display_name": "sora-2",
            "capability": "video",
            "callable": False,
        }
    ]


def test_normalize_gateway_main_models_separates_image_and_editing_families():
    records = [
        {"id": "minimax-image-01"},
        {"id": "glm-image"},
        {"id": "joyai-image-edit"},
        {"id": "wanx2.1-imageedit"},
        {"id": "wan2.7-image-pro"},
        {"id": "gemini-3-pro-image-preview"},
    ]

    models = normalize_provider_models("gateway_main", records)

    assert {model["capability"] for model in models} == {"image"}
    assert not any(model["callable"] for model in models)


def test_normalize_gateway_main_models_keeps_visual_understanding_models_callable_chat():
    records = [
        {"id": "qwen3-vl-plus"},
        {"id": "GLM-4.6V"},
        {"id": "hunyuan-t1-vision-20250619"},
    ]

    models = normalize_provider_models("gateway_main", records)

    assert {model["capability"] for model in models} == {"chat"}
    assert all(model["callable"] for model in models)


def test_default_catalog_uses_only_gateway_main_when_its_key_is_configured(monkeypatch, tmp_path):
    monkeypatch.setattr("model_catalog.GATEWAY_MAIN_API_KEY", "test-gateway_main-key")

    service = ModelCatalogService(tmp_path / "catalog.json")

    assert tuple(service.fetchers) == ("gateway_main",)


def test_refresh_uses_previous_cache_when_provider_request_fails(tmp_path):
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={"gateway_compat": lambda: (_ for _ in ()).throw(OSError("offline"))},
    )
    (tmp_path / "catalog.json").write_text(
        '{"providers":[{"key":"gateway_compat","name":"GatewayCompat","status":"fresh","capabilities":{}}]}',
        encoding="utf-8",
    )

    assert service.refresh()["providers"][0]["status"] == "stale"


def test_refresh_preserves_normalized_models_when_provider_request_fails(tmp_path):
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={"gateway_compat": lambda: (_ for _ in ()).throw(OSError("offline"))},
    )
    (tmp_path / "catalog.json").write_text(
        """{
            "providers": [{
                "key": "gateway_compat",
                "name": "GatewayCompat",
                "status": "fresh",
                "capabilities": {
                    "chat": [{
                        "platform_model_id": "gateway_compat:deepseek-ai/DeepSeek-V4-Flash",
                        "model_id": "deepseek-ai/DeepSeek-V4-Flash",
                        "display_name": "DeepSeek-V4-Flash",
                        "capability": "chat",
                        "callable": true
                    }]
                }
            }]
        }""",
        encoding="utf-8",
    )

    provider = service.refresh()["providers"][0]

    assert provider["status"] == "stale"
    assert provider["capabilities"]["chat"][0]["model_id"] == "deepseek-ai/DeepSeek-V4-Flash"


def test_refresh_marks_provider_unavailable_without_a_cache(tmp_path):
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={"gateway_secondary": lambda: (_ for _ in ()).throw(OSError("offline"))},
    )

    catalog = service.refresh()

    assert catalog["providers"] == [
        {
            "key": "gateway_secondary",
            "name": "GatewaySecondary",
            "status": "unavailable",
            "capabilities": {},
            "last_success_at": None,
        }
    ]
    assert "updated_at" in catalog


def test_refresh_keeps_unavailable_cached_provider_unavailable_after_fetch_failure(tmp_path):
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={"gateway_secondary": lambda: (_ for _ in ()).throw(OSError("offline"))},
    )
    (tmp_path / "catalog.json").write_text(
        """{
            "providers": [{
                "key": "gateway_secondary",
                "name": "GatewaySecondary",
                "status": "unavailable",
                "capabilities": {}
            }]
        }""",
        encoding="utf-8",
    )

    provider = service.refresh()["providers"][0]

    assert provider == {
        "key": "gateway_secondary",
        "name": "GatewaySecondary",
        "status": "unavailable",
        "capabilities": {},
        "last_success_at": None,
    }


def test_refresh_exposes_safe_in_memory_data_when_cache_cannot_be_written(tmp_path):
    cache_directory = tmp_path / "catalog"
    cache_directory.mkdir()
    service = ModelCatalogService(
        cache_directory,
        fetchers={
            "gateway_compat": lambda: [
                {"id": "deepseek-ai/DeepSeek-V4-Flash", "type": "text", "sub_type": "chat"}
            ]
        },
    )

    catalog = service.refresh()

    assert catalog["availability"] == "degraded"
    assert catalog["failure_categories"] == ["cache_write"]
    assert catalog["retryable"] is True
    assert catalog["providers"][0]["status"] == "fresh"
    assert catalog["providers"][0]["capabilities"]["chat"][0]["model_id"] == (
        "deepseek-ai/DeepSeek-V4-Flash"
    )
    assert service.load() == catalog


def test_load_returns_empty_safe_catalog_when_cache_is_missing(tmp_path):
    service = ModelCatalogService(tmp_path / "catalog.json", fetchers={})

    assert service.load() == {
        "availability": "unavailable",
        "last_attempt_at": None,
        "last_success_at": None,
        "failure_categories": [],
        "retryable": False,
        "message": "模型目录暂不可用，请稍后重试",
        "providers": [],
        "updated_at": None,
    }


def test_successful_refresh_records_fresh_state_and_both_timestamps(tmp_path):
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={"gateway_main": lambda: [{"id": "example-chat"}]},
        clock=lambda: "2026-08-10T08:00:00Z",
    )

    catalog = service.refresh()

    assert catalog["availability"] == "fresh"
    assert catalog["last_attempt_at"] == "2026-08-10T08:00:00Z"
    assert catalog["last_success_at"] == "2026-08-10T08:00:00Z"
    assert catalog["updated_at"] == catalog["last_attempt_at"]
    assert catalog["failure_categories"] == []
    assert catalog["retryable"] is False
    assert catalog["message"] is None
    assert catalog["providers"][0]["status"] == "fresh"
    assert catalog["providers"][0]["last_success_at"] == "2026-08-10T08:00:00Z"


def test_transport_failure_preserves_last_success_and_stale_metadata(tmp_path):
    attempt_times = iter(
        ["2026-08-10T08:00:00Z", "2026-08-10T08:05:00Z"]
    )
    outcomes = iter(
        [
            [{"id": "example-chat"}],
            OSError("sensitive transport detail"),
        ]
    )

    def fetch():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={"gateway_main": fetch},
        clock=lambda: next(attempt_times),
    )
    service.refresh()

    degraded = service.refresh()

    assert degraded["availability"] == "degraded"
    assert degraded["last_attempt_at"] == "2026-08-10T08:05:00Z"
    assert degraded["last_success_at"] == "2026-08-10T08:00:00Z"
    assert degraded["failure_categories"] == ["transport"]
    assert degraded["retryable"] is True
    assert degraded["message"] == "模型目录暂时无法更新，当前仅展示最近一次可用信息"
    assert degraded["providers"][0]["status"] == "stale"
    assert degraded["providers"][0]["last_success_at"] == "2026-08-10T08:00:00Z"
    assert degraded["providers"][0]["capabilities"]["chat"][0]["model_id"] == "example-chat"
    assert "sensitive transport detail" not in str(degraded)


def test_http_authentication_failure_is_safe_non_retryable_configuration(tmp_path):
    private_url = "https://private-upstream.invalid/models"
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={
            "gateway_main": lambda: (_ for _ in ()).throw(
                HTTPError(private_url, 401, "sensitive auth response", None, None)
            )
        },
        clock=lambda: "2026-08-10T08:00:00Z",
    )

    catalog = service.refresh()

    assert catalog["failure_categories"] == ["configuration"]
    assert catalog["retryable"] is False
    assert private_url not in str(catalog)
    assert "sensitive auth response" not in str(catalog)


def test_retryable_http_and_invalid_response_categories_are_stable(tmp_path):
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={
            "first": lambda: (_ for _ in ()).throw(ValueError("raw provider body")),
            "second": lambda: (_ for _ in ()).throw(
                HTTPError("https://private.invalid", 503, "private failure", None, None)
            ),
        },
        clock=lambda: "2026-08-10T08:00:00Z",
    )

    catalog = service.refresh()

    assert catalog["failure_categories"] == ["http", "invalid_response"]
    assert catalog["retryable"] is True
    assert "raw provider body" not in str(catalog)
    assert "private.invalid" not in str(catalog)


def test_unexpected_type_error_is_unknown_and_not_retryable(tmp_path):
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={
            "gateway_main": lambda: (_ for _ in ()).throw(
                TypeError("private implementation detail")
            )
        },
        clock=lambda: "2026-08-10T08:00:00Z",
    )

    catalog = service.refresh()

    assert catalog["failure_categories"] == ["unknown"]
    assert catalog["retryable"] is False
    assert "private implementation detail" not in str(catalog)


def test_legacy_fresh_cache_migrates_updated_at_to_success_timestamps(tmp_path):
    cache = tmp_path / "catalog.json"
    cache.write_text(
        """{
            "providers": [{
                "key": "gateway_main",
                "name": "Private Source",
                "status": "fresh",
                "capabilities": {}
            }],
            "updated_at": "2026-08-10T07:00:00Z"
        }""",
        encoding="utf-8",
    )

    catalog = ModelCatalogService(cache, fetchers={}).load()

    assert catalog["availability"] == "fresh"
    assert catalog["last_attempt_at"] == "2026-08-10T07:00:00Z"
    assert catalog["last_success_at"] == "2026-08-10T07:00:00Z"
    assert catalog["providers"][0]["last_success_at"] == "2026-08-10T07:00:00Z"


def test_missing_catalog_credential_is_configuration_without_network(monkeypatch, tmp_path):
    network_called = False

    def forbidden_urlopen(*_args, **_kwargs):
        nonlocal network_called
        network_called = True
        raise AssertionError("network must not be called")

    monkeypatch.setattr("model_catalog.urlopen", forbidden_urlopen)
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={},
        clock=lambda: "2026-08-10T08:00:00Z",
    )
    service.fetchers = {
        "private": lambda: service._request_models(
            "https://private-upstream.invalid/models", ""
        )
    }

    catalog = service.refresh()

    assert network_called is False
    assert catalog["failure_categories"] == ["configuration"]
    assert catalog["retryable"] is False
    assert "private-upstream" not in str(catalog)


def test_partial_success_with_empty_valid_models_is_degraded_not_unavailable(tmp_path):
    service = ModelCatalogService(
        tmp_path / "catalog.json",
        fetchers={
            "fresh-source": lambda: [],
            "failed-source": lambda: (_ for _ in ()).throw(OSError("private")),
        },
        clock=lambda: "2026-08-10T08:00:00Z",
    )

    catalog = service.refresh()

    assert catalog["availability"] == "degraded"
    assert [provider["status"] for provider in catalog["providers"]] == [
        "fresh",
        "unavailable",
    ]
    assert catalog["last_success_at"] is None
