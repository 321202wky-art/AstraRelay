from model_capabilities import capability_profile


def test_pure_text_model_profile_is_explicit_about_unsupported_media():
    profile = capability_profile("DeepSeek-V4-Flash", "chat")

    assert profile == {
        "model_type": "纯文本模型",
        "supports": ["text_chat", "reasoning", "code"],
        "unsupported": ["vision", "image_generation", "audio", "video"],
        "capability_note": "注：此模型为纯文本模型",
        "capability_status": "verified",
    }


def test_verified_multimodal_families_support_visual_inputs():
    for model_id in (
        "qwen3.6-flash",
        "qwen3-vl-plus",
        "GLM-4.6V",
        "kimi-k2.6",
        "gemini-2.5-pro",
        "claude-sonnet-4",
        "claude-opus-5",
        "claude-sonnet-5-thinking",
        "claude-fable-5",
        "gpt-4o",
    ):
        profile = capability_profile(model_id, "chat")
        assert profile["model_type"] == "多模态理解模型"
        assert "vision" in profile["supports"]
        assert profile["capability_status"] == "verified"


def test_qwen3_8_max_alias_is_verified_visual_chat_model():
    profile = capability_profile("qwen3.8-max", "chat")

    assert profile["capability_status"] == "verified"
    assert "text_chat" in profile["supports"]
    assert "vision" in profile["supports"]
    assert "vision" not in profile["unsupported"]


def test_kimi_k3_is_verified_general_multimodal_without_media_generation_claims():
    profile = capability_profile("kimi-k3", "chat")

    assert profile["capability_status"] == "verified"
    assert profile["model_type"] == "通用多模态模型"
    assert profile["supports"] == [
        "text_chat",
        "vision",
        "video",
        "reasoning",
        "code",
    ]
    assert profile["unsupported"] == ["image_generation", "audio"]
    assert profile["capability_note"] == "注：此模型支持图片、视频与文档理解"


def test_specialized_model_profiles_do_not_claim_chat_support():
    cases = {
        ("qwen-image-edit-plus", "image"): "图像生成/编辑模型",
        ("sora-2", "video"): "视频生成模型",
        ("gpt-4o-mini-tts", "audio"): "音频模型",
        ("Qwen3-Embedding-8B", "embedding"): "向量模型",
        ("bge-reranker-v2", "reranker"): "重排模型",
    }

    for (model_id, catalog_capability), model_type in cases.items():
        profile = capability_profile(model_id, catalog_capability)
        assert profile["model_type"] == model_type
        assert "text_chat" not in profile["supports"]
        assert profile["capability_status"] == "verified"


def test_unknown_chat_family_has_a_safe_baseline_text_position():
    profile = capability_profile("future-mystery-model", "chat")

    assert profile["model_type"] == "通用文本对话模型"
    assert profile["supports"] == ["text_chat"]
    assert profile["unsupported"] == []
    assert profile["capability_note"] == "注：支持文本对话，其他能力待确认"
    assert profile["capability_status"] == "pending"


def test_every_model_in_the_published_catalog_has_an_explicit_position():
    # Representative public families, never an operator's private runtime cache.
    catalog = {"providers": [{"capabilities": {
        "chat": [{"model_id": value} for value in (
            "deepseek-v4-flash", "kimi-k2.6", "qwen3.8-max", "claude-sonnet-4", "gpt-4o",
        )],
        "image": [{"model_id": "qwen-image-edit-plus"}],
        "audio": [{"model_id": "gpt-4o-mini-tts"}],
        "video": [{"model_id": "sora-2"}],
        "embedding": [{"model_id": "Qwen3-Embedding-8B"}],
    }}]}

    unresolved = []
    for provider in catalog["providers"]:
        for capability, models in provider["capabilities"].items():
            for model in models:
                profile = capability_profile(model["model_id"], capability)
                if profile["capability_status"] != "verified":
                    unresolved.append(model["model_id"])

    assert unresolved == []


def test_officially_documented_multimodal_families_are_not_marked_as_text_only():
    model_ids = (
        "gemma-3-27b-it",
        "gemma-4-31b-it",
        "grok-4.5",
        "o3",
        "o3-deep-research",
        "o4-mini-deep-research",
        "Ministral-3-14B-Instruct-2512",
        "ERNIE-5.0",
        "ERNIE-5.0-Thinking-Preview",
        "step-3",
        "step-3.7-flash",
        "Doubao-Seed-2.0-pro",
        "doubao-seed-2-1-pro-260628",
        "MiniMax-M3",
        "MiMo-V2-Omni",
        "mimo-v2.5",
    )

    for model_id in model_ids:
        profile = capability_profile(model_id, "chat")
        assert profile["capability_status"] == "verified"
        assert "vision" in profile["supports"]


def test_channel_chat_aliases_for_visual_generation_are_classified_as_tools():
    cases = {
        "riverflow-v2-max-preview": "图像生成/编辑模型",
        "uni-1": "图像生成/编辑模型",
        "step-1x-medium": "图像生成模型",
        "step-2x-large": "图像生成模型",
        "qwen-mt-image": "图像翻译模型",
    }

    for model_id, expected_type in cases.items():
        profile = capability_profile(model_id, "chat")
        assert profile["model_type"] == expected_type
        assert profile["capability_status"] == "verified"
        assert "text_chat" not in profile["supports"]


def test_officially_documented_text_models_stay_text_only():
    model_ids = (
        "360gpt2-pro",
        "360zhinao2-o1",
        "GLM-5.1",
        "MiniMax-M2.7",
        "Devstral-2-123B-Instruct-2512",
        "MiMo-V2-Pro",
        "ernie-5.1",
        "hy3",
        "Seed-OSS-36B-Instruct",
    )

    for model_id in model_ids:
        profile = capability_profile(model_id, "chat")
        assert profile["capability_status"] == "verified"
        assert "vision" not in profile["supports"]
        assert "vision" in profile["unsupported"]
