"""Conservative, public-safe model capability profiles.

The upstream catalog does not expose a uniform modalities field.  The rules in
this module are therefore based on model-vendor documentation.  Unknown future
models remain pending, while every model in the published catalog has an
explicit position.
"""

from __future__ import annotations

from typing import Any


_PURE_TEXT_MARKERS = (
    "360gpt",
    "360zhinao",
    "deepseek",
    "devstral-",
    "doubao-1.5-",
    "doubao-seed-code-preview-",
    "ernie-4.5",
    "ernie-x1",
    "ernie-5.1",
    "gpt-oss",
    "grok-",
    "qwen2",
    "qwen3-",
    "qwen-max",
    "qwen-plus",
    "qwen-turbo",
    "qwen-flash",
    "qwq-",
    "glm-",
    "glm-z1",
    "hunyuan-",
    "hy3",
    "mimo-v2-flash",
    "mimo-v2-pro",
    "mimo-v2.5-pro",
    "minimax-text",
    "minimax-m1",
    "minimax-m2",
    "seed-oss-",
    "step-3.5-",
    "baichuan",
    "internlm",
    "llama-",
    "mistral",
    "mixtral",
    "phi-",
    "xunfei",
    "spark-",
)


def capability_profile(model_id: str, catalog_capability: str) -> dict[str, Any]:
    """Return a stable public profile without upstream identifiers or claims."""
    normalized = str(model_id or "").casefold()
    capability = str(catalog_capability or "other").casefold()

    if capability == "image":
        return _profile(
            "图像生成/编辑模型",
            ["image_generation"],
            ["text_chat", "vision", "audio", "video"],
            "注：此模型用于图像生成或编辑",
        )
    if capability == "video":
        return _profile(
            "视频生成模型",
            ["video_generation"],
            ["text_chat", "vision", "image_generation", "audio"],
            "注：此模型用于视频生成",
        )
    if capability == "audio":
        return _profile(
            "音频模型",
            ["audio"],
            ["text_chat", "vision", "image_generation", "video"],
            "注：此模型用于音频处理",
        )
    if capability == "embedding":
        return _profile(
            "向量模型",
            ["embedding"],
            ["text_chat", "vision", "image_generation", "audio", "video"],
            "注：此模型用于向量化，不支持直接对话",
        )
    if capability == "reranker":
        return _profile(
            "重排模型",
            ["reranking"],
            ["text_chat", "vision", "image_generation", "audio", "video"],
            "注：此模型用于检索重排，不支持直接对话",
        )

    if capability != "chat":
        return _pending_profile([])

    # 渠道把以下视觉工具放在 chat 目录中；按原厂用途纠正，避免作为对话模型推荐。
    if normalized == "qwen-mt-image":
        return _profile(
            "图像翻译模型",
            ["image_translation"],
            ["text_chat", "audio", "video"],
            "注：此模型用于图片内文字翻译，不是通用对话模型",
        )
    if normalized.startswith("riverflow-v2-") or normalized == "uni-1":
        return _profile(
            "图像生成/编辑模型",
            ["image_generation"],
            ["text_chat", "audio", "video"],
            "注：此模型用于图像生成或编辑，不是通用对话模型",
        )
    if normalized in {"step-1x-medium", "step-2x-large"}:
        return _profile(
            "图像生成模型",
            ["image_generation"],
            ["text_chat", "audio", "video"],
            "注：此模型用于图像生成，不是通用对话模型",
        )

    if normalized.startswith("kimi-k3"):
        return _profile(
            "通用多模态模型",
            ["text_chat", "vision", "video", "reasoning", "code"],
            ["image_generation", "audio"],
            "注：此模型支持图片、视频与文档理解",
        )

    if _is_verified_full_multimodal(normalized):
        return _profile(
            "通用多模态模型",
            ["text_chat", "vision", "audio", "video", "reasoning", "code"],
            ["image_generation"],
            "注：此模型支持文本、图片、音频和视频理解",
        )

    if _is_verified_video_multimodal(normalized):
        return _profile(
            "通用多模态模型",
            ["text_chat", "vision", "video", "reasoning", "code"],
            ["image_generation", "audio"],
            "注：此模型支持文本、图片和视频理解",
        )

    if _is_verified_multimodal(normalized):
        return _profile(
            "多模态理解模型",
            ["text_chat", "vision", "reasoning", "code"],
            ["image_generation", "audio", "video"],
            "注：此模型支持图片理解",
        )

    if _is_verified_pure_text(normalized):
        return _profile(
            "纯文本模型",
            ["text_chat", "reasoning", "code"],
            ["vision", "image_generation", "audio", "video"],
            "注：此模型为纯文本模型",
        )

    return _pending_chat_profile()


def _is_verified_multimodal(model_id: str) -> bool:
    explicit_visual = (
        "-vision",
        "_vision",
        ".vision",
        "-vl-",
        "_vl_",
        "/vl-",
        "qwen-vl",
        "qwen3-vl",
        "glm-4v",
        "glm-4.1v",
        "glm-4.5v",
        "glm-4.6v",
        "glm-5v",
        "hunyuan-turbos-vision",
        "hunyuan-t1-vision",
    )
    if any(marker in model_id for marker in explicit_visual):
        return True
    if model_id.startswith("gemini-"):
        return True
    # Anthropic 当前 Claude 对话模型均支持文本和图片输入；不要按大版本号漏判。
    if model_id.startswith("claude-"):
        return True
    if model_id.startswith(("gpt-4o", "gpt-4.1", "gpt-5")):
        return True
    if model_id.startswith(("kimi-k2.5", "kimi-k2.6")):
        return True
    if model_id.startswith(("qwen3.5-", "qwen3.6-", "qwen3.7-", "qwen3.8-")):
        return True
    if model_id.startswith(("gemma-3-", "gemma-4-")):
        return True
    if model_id == "grok-4.5":
        return True
    if model_id in {"o3", "o3-deep-research", "o4-mini-deep-research"}:
        return True
    if model_id.startswith("ministral-3-"):
        return True
    if model_id == "step-3":
        return True
    if model_id.startswith(("doubao-seed-2.0-", "doubao-seed-2-0-")):
        return True
    if model_id.startswith("doubao-seed-2-1-"):
        return True
    if model_id == "doubao-seed-evolving":
        return True
    return False


def _is_verified_video_multimodal(model_id: str) -> bool:
    return (
        model_id == "minimax-m3"
        or model_id == "step-3.7-flash"
        or model_id.startswith("doubao-seed-1-6-")
    )


def _is_verified_full_multimodal(model_id: str) -> bool:
    return (
        model_id in {"ernie-5.0", "ernie-5.0-thinking-preview"}
        or model_id == "mimo-v2-omni"
        or model_id == "mimo-v2.5"
        or model_id.startswith("doubao-seed-2.0-lite")
        or model_id.startswith("doubao-seed-2-0-lite")
    )


def _is_verified_pure_text(model_id: str) -> bool:
    if model_id.startswith("kimi-"):
        return not model_id.startswith(("kimi-k2.5", "kimi-k2.6", "kimi-k3"))
    return any(marker in model_id for marker in _PURE_TEXT_MARKERS)


def _profile(
    model_type: str,
    supports: list[str],
    unsupported: list[str],
    note: str,
) -> dict[str, Any]:
    return {
        "model_type": model_type,
        "supports": supports,
        "unsupported": unsupported,
        "capability_note": note,
        "capability_status": "verified",
    }


def _pending_profile(supports: list[str]) -> dict[str, Any]:
    return {
        "model_type": "能力信息待确认",
        "supports": supports,
        "unsupported": [],
        "capability_note": "注：此模型能力信息待确认",
        "capability_status": "pending",
    }


def _pending_chat_profile() -> dict[str, Any]:
    return {
        "model_type": "通用文本对话模型",
        "supports": ["text_chat"],
        "unsupported": [],
        "capability_note": "注：支持文本对话，其他能力待确认",
        "capability_status": "pending",
    }
