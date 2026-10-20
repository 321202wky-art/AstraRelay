"""Integer-points billing orchestration for model-provider calls."""

from threading import Lock
from uuid import uuid4

import config


class BillingGateway:
    """Freeze a wallet estimate before a provider call and always clean it up."""

    def __init__(self, wallet_database, config_module=config):
        self._wallet_database = wallet_database
        self._config = config_module
        self._key_indices = {}
        self._key_lock = Lock()

    def next_api_key(self, model="deepseek"):
        metadata = getattr(self._config, "MODEL_METADATA", {}).get(model.lower(), {})
        api_key_setting = metadata.get("api_key_setting")
        if not api_key_setting and isinstance(model, str) and ":" in model:
            provider_key = model.partition(":")[0]
            provider = getattr(self._config, "MODEL_PROVIDER_CONNECTIONS", {}).get(
                provider_key, {}
            )
            api_key_setting = provider.get("api_key_setting")
        pool_setting = "GATEWAY_SECONDARY_API_KEYS" if api_key_setting == "GATEWAY_SECONDARY_API_KEY" else "GATEWAY_COMPAT_API_KEYS"
        keys = tuple(getattr(self._config, pool_setting, ()))
        if not keys:
            raise RuntimeError("No provider API key is configured")
        with self._key_lock:
            index = self._key_indices.get(pool_setting, 0)
            self._key_indices[pool_setting] = index + 1
        return keys[index % len(keys)]

    def estimate_points(self, model, messages=None):
        """Reserve estimated input tokens plus the configured output-token ceiling."""
        return self._points_for_tokens(
            model, self._estimate_message_tokens(messages) + self._setting("MAX_OUTPUT_TOKENS")
        )

    def actual_points(self, model, messages, output_text):
        """Price estimated prompt tokens and the returned plain-text output tokens."""
        return self._points_for_tokens(
            model,
            self._estimate_message_tokens(messages) + self._estimate_text_tokens(output_text),
        )

    def _points_for_tokens(self, model, tokens):
        micro_points = self._model_price(model)
        exchange_rate = self._setting("EXCHANGE_RATE")
        if (
            not isinstance(micro_points, int)
            or isinstance(micro_points, bool)
            or micro_points <= 0
            or not isinstance(exchange_rate, int)
            or isinstance(exchange_rate, bool)
            or exchange_rate <= 0
        ):
            raise ValueError("Model prices and exchange rate must be positive integers")
        return (tokens * micro_points + exchange_rate - 1) // exchange_rate

    def _model_price(self, model):
        prices = getattr(self._config, "MODEL_PRICES_MICRO_POINTS", {})
        try:
            return prices[model.lower()]
        except (AttributeError, KeyError) as exc:
            raise ValueError(f"No price is configured for model {model}") from exc

    def _setting(self, name):
        value = getattr(self._config, name, None)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    def _estimate_message_tokens(self, messages):
        if messages is None:
            return 0
        if not isinstance(messages, (list, tuple)):
            raise ValueError("messages must be a list or tuple")
        return sum(
            self._estimate_text_tokens(item.get("content", ""))
            for item in messages
            if isinstance(item, dict)
        )

    def _estimate_text_tokens(self, text):
        if not isinstance(text, str) or not text:
            return 0
        chars_per_token = self._setting("TOKEN_CHARS_PER_TOKEN")
        return (len(text) + chars_per_token - 1) // chars_per_token

    def complete(self, username, model, messages, provider_call):
        """Call a provider with a rotated key, settling or releasing its estimate."""
        points = self.estimate_points(model, messages)
        if not isinstance(username, str) or not username.strip():
            raise ValueError("A wallet username is required")
        if not callable(provider_call):
            raise TypeError("provider_call must be callable")

        self._wallet_database.ensure_wallet(username)
        request_id = uuid4().hex
        if not self._wallet_database.freeze_points(username, request_id, points):
            raise RuntimeError("Insufficient wallet points")
        try:
            result = provider_call(self.next_api_key(model))
            actual_points = min(points, self.actual_points(model, messages, result))
            self._wallet_database.settle_points(username, request_id, actual_points)
        except Exception:
            self._wallet_database.release_points(username, request_id)
            raise
        return result

    def stream(self, username, model, messages, provider_stream):
        """Yield provider chunks, settling actual text only after clean exhaustion."""
        points = self.estimate_points(model, messages)
        if not isinstance(username, str) or not username.strip():
            raise ValueError("A wallet username is required")
        if not callable(provider_stream):
            raise TypeError("provider_stream must be callable")

        self._wallet_database.ensure_wallet(username)
        request_id = uuid4().hex
        if not self._wallet_database.freeze_points(username, request_id, points):
            raise RuntimeError("Insufficient wallet points")
        chunks = []
        try:
            for chunk in provider_stream(self.next_api_key(model)):
                if isinstance(chunk, str):
                    chunks.append(chunk)
                yield chunk
            actual_points = min(points, self.actual_points(model, messages, "".join(chunks)))
            self._wallet_database.settle_points(username, request_id, actual_points)
        except GeneratorExit:
            self._wallet_database.release_points(username, request_id)
            raise
        except Exception:
            self._wallet_database.release_points(username, request_id)
            raise
