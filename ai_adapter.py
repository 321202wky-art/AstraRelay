"""OpenAI-compatible adapter for the configured model providers."""

from contextlib import contextmanager
from threading import local

import config
from openai import OpenAI


class AIAdapter:
    def __init__(self, database, client=None, billing_gateway=None, model_registry=None):
        self.database = database
        self.model_name = self._get_model_name("deepseek")
        self._injected_client = client
        self._clients = {}
        self.client = client if client is not None else self._create_client("deepseek")
        self._clients["deepseek"] = self.client
        self._billing_gateway = billing_gateway
        self._billing_context = local()
        self._model_registry = model_registry

    @contextmanager
    def billing_user(self, username):
        """Associate calls in the current thread with one wallet user temporarily."""
        if not isinstance(username, str) or not username.strip():
            raise ValueError("A wallet username is required")
        previous_username = getattr(self._billing_context, "username", None)
        self._billing_context.username = username
        try:
            yield self
        finally:
            if previous_username is None:
                del self._billing_context.username
            else:
                self._billing_context.username = previous_username

    def _get_metadata(self, model):
        normalized_model = model.lower() if isinstance(model, str) else model
        if (
            normalized_model in getattr(config, "GATEWAY_MAIN_MODEL_ALIASES", {})
            and getattr(config, "DEFAULT_MODEL_RUNTIME", "legacy") == "gateway_main"
            and not getattr(config, "USE_LEGACY_API", False)
        ):
            return {
                "model_id": config.GATEWAY_MAIN_MODEL_ALIASES[normalized_model],
                **config.MODEL_PROVIDER_CONNECTIONS["gateway_main"],
            }
        try:
            return config.MODEL_METADATA[normalized_model]
        except (AttributeError, KeyError):
            if self._model_registry is not None and isinstance(model, str) and ":" in model:
                return self._model_registry.resolve(model)
            raise ValueError(f"Unknown chat model: {model}") from None

    def _get_model_name(self, model):
        normalized_model = model.lower()
        model_name = self._get_metadata(model).get("model_id")
        if not isinstance(model_name, str) or not model_name.strip():
            raise RuntimeError(f"Model configuration is missing for {model}")
        legacy_settings = {
            "deepseek": ("MODEL_NAME", "deepseek-ai/DeepSeek-V4-Flash"),
            "kimi": ("KIMI_MODEL_NAME", "Pro/moonshotai/Kimi-K2.6"),
            "zhipu": ("ZHIPU_MODEL_NAME", "zai-org/GLM-5.2"),
            "qwen": ("QWEN_MODEL_NAME", "Qwen/Qwen3.6-35B-A3B"),
            "bytedance": ("BYTEDANCE_MODEL_NAME", "ByteDance-Seed/Seed-OSS-36B-Instruct"),
        }
        if normalized_model in legacy_settings:
            setting, expected = legacy_settings[normalized_model]
            configured_name = getattr(config, setting, expected) or expected
            if configured_name != expected:
                raise RuntimeError(f"GatewayCompat 模型必须是 {expected}")
        return model_name

    def _create_client(self, model="deepseek", api_key=None):
        metadata = self._get_metadata(model)
        setting = metadata.get("api_key_setting")
        api_key = api_key if api_key is not None else (
            getattr(config, setting, None) if isinstance(setting, str) else None
        )
        base_url = metadata.get("base_url")
        if setting == "GATEWAY_COMPAT_API_KEY":
            base_url = getattr(config, "GATEWAY_COMPAT_BASE_URL", base_url)
        if not isinstance(api_key, str) or not api_key.strip():
            raise RuntimeError(f"缺少 API Key configuration for {model}")
        if not isinstance(base_url, str) or not base_url.strip():
            raise RuntimeError(f"缺少 Base URL configuration for {model}")
        return OpenAI(api_key=api_key.strip(), base_url=base_url.strip(), max_retries=0)

    def _get_client(self, model):
        cache_key = model if ":" in model else model.lower()
        if self._injected_client is not None:
            return self._injected_client
        if cache_key not in self._clients:
            self._clients[cache_key] = self._create_client(model)
        return self._clients[cache_key]

    def reply(self, message, model="deepseek"):
        history = self.database.get_recent_messages(limit=8, model=model)
        return self.reply_with_history(message, history, model=model)

    def reply_with_history(self, message, history, model="deepseek"):
        """Return plain model text while preserving the existing public contract."""
        messages = [{"role": item["role"], "content": item["content"]} for item in history[-8:]]
        messages.append({"role": "user", "content": message})
        if getattr(config, "USE_LEGACY_API", False):
            return self.legacy_api_handler(messages, model)
        username = getattr(self._billing_context, "username", None)
        if self._billing_gateway is not None and username is not None:
            try:
                return self._billing_gateway.complete(
                    username,
                    model,
                    messages,
                    lambda api_key: self._request_response(messages, model, api_key),
                )
            except (TimeoutError, RuntimeError):
                raise
            except Exception as exc:
                if "timeout" in exc.__class__.__name__.lower():
                    raise TimeoutError("模型响应超时，请稍后重试") from exc
                raise RuntimeError("模型 API 或网络请求失败，请稍后重试") from exc
        return self._request_response(messages, model)

    def legacy_api_handler(self, messages, model):
        """Compatibility handler selected by ``USE_LEGACY_API``."""
        return self._request_response(messages, model)

    def _request_response(self, messages, model, api_key=None):
        try:
            client = (
                self._get_client(model)
                if api_key is None or self._injected_client is not None
                else self._create_client(model, api_key)
            )
            response = client.chat.completions.create(
                model=self._get_model_name(model),
                messages=messages,
                max_tokens=config.MAX_OUTPUT_TOKENS,
                timeout=30,
            )
        except TimeoutError as exc:
            raise TimeoutError("模型响应超时，请稍后重试") from exc
        except Exception as exc:
            if "timeout" in exc.__class__.__name__.lower():
                raise TimeoutError("模型响应超时，请稍后重试") from exc
            raise RuntimeError("模型 API 或网络请求失败，请稍后重试") from exc
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError("模型返回了空响应") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("模型返回了空响应")
        return content

    def stream_reply_with_history(self, message, history, model="deepseek"):
        """Yield text deltas from an OpenAI-compatible streaming response."""
        messages = [{"role": item["role"], "content": item["content"]} for item in history[-8:]]
        messages.append({"role": "user", "content": message})

        def provider_stream(api_key=None):
            client = (
                self._get_client(model)
                if api_key is None or self._injected_client is not None
                else self._create_client(model, api_key)
            )
            stream = client.chat.completions.create(
                model=self._get_model_name(model),
                messages=messages,
                stream=True,
                max_tokens=config.MAX_OUTPUT_TOKENS,
                timeout=30,
            )
            emitted = False
            for chunk in stream:
                try:
                    content = chunk.choices[0].delta.content
                except (AttributeError, IndexError, TypeError):
                    continue
                if isinstance(content, str) and content:
                    emitted = True
                    yield content
            if not emitted:
                raise RuntimeError("模型未返回可显示的流式内容")

        try:
            username = getattr(self._billing_context, "username", None)
            if (
                not getattr(config, "USE_LEGACY_API", False)
                and self._billing_gateway is not None
                and username is not None
            ):
                yield from self._billing_gateway.stream(
                    username, model, messages, provider_stream
                )
            else:
                yield from provider_stream()
        except TimeoutError as exc:
            raise TimeoutError("模型流式响应超时，请稍后重试") from exc
        except Exception as exc:
            if "timeout" in exc.__class__.__name__.lower():
                raise TimeoutError("模型流式响应超时，请稍后重试") from exc
            raise RuntimeError("模型流式请求失败，请稍后重试") from exc

    def health_check(self, model):
        """Perform a non-persistent lightweight provider connectivity check."""
        self._get_client(model).chat.completions.create(
            model=self._get_model_name(model),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            timeout=30,
        )
        return True
