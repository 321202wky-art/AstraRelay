"""GatewayMain media operations with local input validation."""

from __future__ import annotations

import base64
from threading import Lock

import config
from openai import OpenAI


class MediaService:
    """Call only the configured server-side GatewayMain provider."""

    allowed_image_mimes = {"image/jpeg", "image/png", "image/webp"}

    def __init__(self, client=None, max_image_bytes=None):
        self.max_image_bytes = (
            config.MAX_IMAGE_UPLOAD_BYTES if max_image_bytes is None else max_image_bytes
        )
        self.client = client
        self._client_lock = Lock()

    @staticmethod
    def _create_client():
        if not config.GATEWAY_MAIN_API_KEY:
            raise RuntimeError("图像服务尚未配置")
        return OpenAI(
            api_key=config.GATEWAY_MAIN_API_KEY,
            base_url=config.GATEWAY_MAIN_BASE_URL,
            max_retries=0,
        )

    def _get_client(self):
        if self.client is None:
            with self._client_lock:
                if self.client is None:
                    self.client = self._create_client()
        return self.client

    def describe_image(self, model_id: str, prompt: str, image_bytes: bytes, mime_type: str) -> str:
        self._validate_image(model_id, prompt, image_bytes, mime_type)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt.strip()},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                            },
                        ],
                    }
                ],
                max_tokens=config.MAX_OUTPUT_TOKENS,
                timeout=30,
            )
            content = response.choices[0].message.content
        except TimeoutError as exc:
            raise TimeoutError("图像理解请求超时，请稍后重试") from exc
        except Exception as exc:
            if "timeout" in exc.__class__.__name__.lower():
                raise TimeoutError("图像理解请求超时，请稍后重试") from exc
            raise RuntimeError("图像理解服务暂时不可用") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("图像理解未返回有效内容")
        return content.strip()

    def generate_image(self, model_id: str, prompt: str, size: str | None = None) -> list[str]:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("请选择图像模型")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("请输入图像生成提示词")
        request = {"model": model_id.strip(), "prompt": prompt.strip(), "timeout": 30}
        if isinstance(size, str) and size.strip():
            request["size"] = size.strip()
        client = self._get_client()
        try:
            response = client.images.generate(**request)
        except TimeoutError as exc:
            raise TimeoutError("图像生成请求超时，请稍后重试") from exc
        except Exception as exc:
            if "timeout" in exc.__class__.__name__.lower():
                raise TimeoutError("图像生成请求超时，请稍后重试") from exc
            raise RuntimeError("图像生成服务暂时不可用") from exc
        urls = [item.url for item in getattr(response, "data", []) if isinstance(getattr(item, "url", None), str) and item.url]
        if not urls:
            raise RuntimeError("图像生成未返回可用图片")
        return urls

    def _validate_image(self, model_id: str, prompt: str, image_bytes: bytes, mime_type: str) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("请选择视觉模型")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("请输入图像分析提示词")
        if mime_type not in self.allowed_image_mimes:
            raise ValueError("不支持的图片格式")
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("请选择图片文件")
        if len(image_bytes) > self.max_image_bytes:
            raise ValueError("图片文件过大")
