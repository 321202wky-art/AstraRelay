"""Safe, cache-backed provider model catalog utilities."""

from __future__ import annotations

import json
from copy import deepcopy
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from config import (
    GATEWAY_SECONDARY_API_KEY,
    MODEL_CATALOG_PROVIDERS,
    GATEWAY_MAIN_API_KEY,
    GATEWAY_COMPAT_API_KEY,
)


CatalogFetcher = Callable[[], Iterable[Mapping[str, Any]]]

CATALOG_DEGRADED_MESSAGE = "模型目录暂时无法更新，当前仅展示最近一次可用信息"
CATALOG_UNAVAILABLE_MESSAGE = "模型目录暂不可用，请稍后重试"
_FAILURE_CATEGORY_ORDER = (
    "configuration",
    "transport",
    "http",
    "invalid_response",
    "cache_write",
    "unknown",
)
_RETRYABLE_FAILURES = {"transport", "http", "invalid_response", "cache_write"}


class CatalogConfigurationError(ValueError):
    """A required catalog setting is missing or rejected."""

_CAPABILITIES = {"chat", "image", "audio", "video", "embedding", "reranker"}
_PROVIDER_NAMES = {
    "gateway_main": "GatewayMain",
    "gateway_compat": "GatewayCompat",
    "gateway_secondary": "GatewaySecondary",
}
_GATEWAY_MAIN_VIDEO_PREFIXES = (
    "sora-",
    "veo-",
    "vidu",
    "kling-",
    "wan2.",
    "doubao-seedance",
)
_GATEWAY_MAIN_IMAGE_PREFIXES = (
    "gpt-image",
    "dall-e",
    "imagen-",
    "cogview-",
    "qwen-image",
    "doubao-seedream",
    "flux",
    "stable-",
    "kolors",
    "minimax-image",
    "glm-image",
    "z-image",
    "wanx",
    "wan2.7-image",
    "joyai-image",
    "uni-1-",
    "reve-",
)
_GATEWAY_MAIN_IMAGE_MARKERS = (
    "-image-edit",
    "-image-preview",
    "-image-01",
    "-t2i-",
    "-i2i-",
)
_GATEWAY_MAIN_AUDIO_MARKERS = (
    "-tts",
    "-speech",
    "whisper",
    "sensevoice",
    "-audio",
    "realtime-audio",
)


def normalize_provider_models(
    provider_key: str, records: Iterable[Mapping[str, Any]]
) -> list[dict[str, str | bool]]:
    """Return the public model fields supported by the unified catalog."""
    normalized: list[dict[str, str | bool]] = []
    for record in records:
        model_id = record.get("id") or record.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            continue

        capability = _normalize_capability(provider_key, record)
        model = {
            "platform_model_id": f"{provider_key}:{model_id}",
            "model_id": model_id,
            "display_name": model_id.rsplit("/", 1)[-1],
            "capability": capability,
            "callable": capability == "chat",
        }
        released_at = _normalize_release_time(record.get("created"))
        if released_at is not None:
            model["released_at"] = released_at
        normalized.append(model)
    return normalized


def _normalize_release_time(value: Any) -> str | None:
    """Convert a public provider Unix timestamp to a stable UTC value."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return None


def _normalize_capability(provider_key: str, record: Mapping[str, Any]) -> str:
    raw_type = record.get("type")
    if provider_key == "gateway_compat":
        if raw_type == "text" and record.get("sub_type") in {
            "chat",
            "reasoning",
            "code",
        }:
            return "chat"
        return raw_type if raw_type in _CAPABILITIES else "other"

    if provider_key == "gateway_main":
        model_id = str(record.get("id") or record.get("model_id") or "").lower()
        if "embedding" in model_id:
            return "embedding"
        if "reranker" in model_id or "rerank" in model_id:
            return "reranker"
        if model_id.startswith(_GATEWAY_MAIN_IMAGE_PREFIXES) or any(
            marker in model_id for marker in _GATEWAY_MAIN_IMAGE_MARKERS
        ):
            return "image"
        if model_id.startswith(_GATEWAY_MAIN_VIDEO_PREFIXES):
            return "video"
        if any(marker in model_id for marker in _GATEWAY_MAIN_AUDIO_MARKERS):
            return "audio"
        return "chat"

    raw_capability = record.get("capability") or raw_type
    if raw_capability in {"text", "generation", "reasoning", "code"}:
        return "chat"
    return raw_capability if raw_capability in _CAPABILITIES else "other"


class ModelCatalogService:
    """Fetch provider directories without leaking credentials into the cache."""

    def __init__(
        self,
        cache_path: str | Path,
        fetchers: Mapping[str, CatalogFetcher] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.fetchers = dict(fetchers) if fetchers is not None else self._default_fetchers()
        self._clock = clock or _timestamp
        self._runtime_catalog: dict[str, Any] | None = None

    def refresh(self) -> dict[str, Any]:
        """Refresh each provider, retaining its last cached data on failure."""
        attempted_at = self._clock()
        cached_providers = {
            provider["key"]: provider
            for provider in self.load()["providers"]
            if isinstance(provider.get("key"), str)
        }
        cached_catalog = self.load()
        providers: list[dict[str, Any]] = []
        failure_categories: list[str] = []
        success_count = 0

        for provider_key, fetcher in self.fetchers.items():
            try:
                models = normalize_provider_models(provider_key, fetcher())
            except Exception as error:
                failure_categories.append(_failure_category(error))
                cached_provider = cached_providers.get(provider_key)
                if cached_provider is None or cached_provider["status"] == "unavailable":
                    providers.append(
                        self._provider(provider_key, "unavailable", {}, None)
                    )
                else:
                    providers.append(
                        self._provider(
                            provider_key,
                            "stale",
                            cached_provider["capabilities"],
                            cached_provider.get("last_success_at"),
                        )
                    )
                continue

            success_count += 1
            capabilities: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
            for model in models:
                capabilities[model["capability"]].append(model)
            providers.append(
                self._provider(
                    provider_key, "fresh", dict(capabilities), attempted_at
                )
            )

        fully_fresh = bool(self.fetchers) and success_count == len(self.fetchers)
        availability = _catalog_availability(providers, fully_fresh=fully_fresh)
        categories = _ordered_failure_categories(failure_categories)
        catalog = self._catalog(
            providers=providers,
            availability=availability,
            last_attempt_at=attempted_at,
            last_success_at=(
                attempted_at if fully_fresh else cached_catalog.get("last_success_at")
            ),
            failure_categories=categories,
        )
        try:
            self._write_cache(catalog)
        except OSError:
            fallback = self._catalog(
                providers=providers,
                availability=(
                    "degraded"
                    if any(provider.get("capabilities") for provider in providers)
                    else "unavailable"
                ),
                last_attempt_at=attempted_at,
                last_success_at=cached_catalog.get("last_success_at"),
                failure_categories=_ordered_failure_categories(
                    [*categories, "cache_write"]
                ),
            )
            self._runtime_catalog = fallback
            return deepcopy(fallback)
        self._runtime_catalog = None
        return catalog

    def load(self) -> dict[str, Any]:
        """Load a valid public catalog, or an empty safe catalog on cache errors."""
        if self._runtime_catalog is not None:
            return deepcopy(self._runtime_catalog)
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_catalog()

        if not isinstance(data, dict) or not isinstance(data.get("providers"), list):
            return self._empty_catalog()

        providers = [
            provider
            for provider in (self._safe_provider(item) for item in data["providers"])
            if provider is not None
        ]
        updated_at = data.get("updated_at")
        updated_at = updated_at if isinstance(updated_at, str) else None
        last_attempt_at = data.get("last_attempt_at")
        if not isinstance(last_attempt_at, str):
            last_attempt_at = updated_at
        fully_fresh = bool(providers) and all(
            provider["status"] == "fresh" for provider in providers
        )
        availability = _catalog_availability(providers, fully_fresh=fully_fresh)
        last_success_at = data.get("last_success_at")
        if not isinstance(last_success_at, str):
            last_success_at = updated_at if fully_fresh else None
        if fully_fresh and isinstance(last_success_at, str):
            for provider in providers:
                if provider.get("last_success_at") is None:
                    provider["last_success_at"] = last_success_at
        raw_categories = data.get("failure_categories")
        categories = _ordered_failure_categories(
            raw_categories if isinstance(raw_categories, list) else []
        )
        if availability != "fresh" and not categories and providers:
            categories = ["unknown"]
        return self._catalog(
            providers=providers,
            availability=availability,
            last_attempt_at=last_attempt_at,
            last_success_at=last_success_at,
            failure_categories=categories,
        )

    def _default_fetchers(self) -> dict[str, CatalogFetcher]:
        if GATEWAY_MAIN_API_KEY:
            gateway_main = MODEL_CATALOG_PROVIDERS["gateway_main"]
            return {
                "gateway_main": lambda: self._request_models(
                    gateway_main["url"], GATEWAY_MAIN_API_KEY
                )
            }
        gateway_compat = MODEL_CATALOG_PROVIDERS["gateway_compat"]
        gateway_secondary = MODEL_CATALOG_PROVIDERS["gateway_secondary"]
        return {
            "gateway_compat": lambda: self._request_models(
                gateway_compat["url"], GATEWAY_COMPAT_API_KEY
            ),
            "gateway_secondary": lambda: self._request_models(gateway_secondary["url"], GATEWAY_SECONDARY_API_KEY),
        }

    @staticmethod
    def _request_models(url: str, api_key: str) -> Iterable[Mapping[str, Any]]:
        if not isinstance(api_key, str) or not api_key.strip():
            raise CatalogConfigurationError("Catalog credential is not configured")
        request = Request(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if isinstance(payload, dict):
            payload = payload.get("data", payload.get("models", []))
        if not isinstance(payload, list):
            raise ValueError("Provider model response must contain a model list")
        return [record for record in payload if isinstance(record, Mapping)]

    @staticmethod
    def _provider(
        key: str,
        status: str,
        capabilities: Mapping[str, list[dict[str, str]]],
        last_success_at: str | None,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "name": _PROVIDER_NAMES.get(key, key),
            "status": status,
            "capabilities": dict(capabilities),
            "last_success_at": last_success_at,
        }

    @staticmethod
    def _catalog(
        *,
        providers: list[dict[str, Any]],
        availability: str,
        last_attempt_at: str | None,
        last_success_at: str | None,
        failure_categories: list[str],
    ) -> dict[str, Any]:
        return {
            "availability": availability,
            "last_attempt_at": last_attempt_at,
            "last_success_at": last_success_at,
            "failure_categories": failure_categories,
            "retryable": any(
                category in _RETRYABLE_FAILURES for category in failure_categories
            ),
            "message": (
                None
                if availability == "fresh"
                else CATALOG_DEGRADED_MESSAGE
                if availability == "degraded"
                else CATALOG_UNAVAILABLE_MESSAGE
            ),
            "providers": providers,
            "updated_at": last_attempt_at,
        }

    @classmethod
    def _empty_catalog(cls) -> dict[str, Any]:
        return cls._catalog(
            providers=[],
            availability="unavailable",
            last_attempt_at=None,
            last_success_at=None,
            failure_categories=[],
        )

    @staticmethod
    def _safe_provider(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        key, name, status, capabilities = (
            item.get("key"),
            item.get("name"),
            item.get("status"),
            item.get("capabilities"),
        )
        if (
            not isinstance(key, str)
            or not isinstance(name, str)
            or status not in {"fresh", "stale", "unavailable"}
            or not isinstance(capabilities, dict)
        ):
            return None

        safe_capabilities: dict[str, list[dict[str, str]]] = {}
        for capability, models in capabilities.items():
            if not isinstance(capability, str) or not isinstance(models, list):
                return None
            safe_models = [ModelCatalogService._safe_cached_model(model) for model in models]
            if any(model is None for model in safe_models):
                return None
            if any(model["capability"] != capability for model in safe_models):
                return None
            safe_capabilities[capability] = safe_models

        last_success_at = item.get("last_success_at")
        if last_success_at is not None and not isinstance(last_success_at, str):
            return None
        return {
            "key": key,
            "name": name,
            "status": status,
            "capabilities": safe_capabilities,
            "last_success_at": last_success_at,
        }

    @staticmethod
    def _safe_cached_model(item: Any) -> dict[str, str | bool] | None:
        if not isinstance(item, dict):
            return None
        required_fields = {
            "platform_model_id",
            "model_id",
            "display_name",
            "capability",
            "callable",
        }
        if not required_fields.issubset(item) or set(item) - (required_fields | {"released_at"}):
            return None
        platform_model_id, model_id, display_name, capability, callable_model = (
            item.get("platform_model_id"),
            item.get("model_id"),
            item.get("display_name"),
            item.get("capability"),
            item.get("callable"),
        )
        if not all(
            isinstance(value, str)
            for value in (platform_model_id, model_id, display_name, capability)
        ) or not isinstance(callable_model, bool):
            return None
        released_at = item.get("released_at")
        if released_at is not None and not isinstance(released_at, str):
            return None
        model: dict[str, str | bool] = {
            "platform_model_id": platform_model_id,
            "model_id": model_id,
            "display_name": display_name,
            "capability": capability,
            "callable": callable_model,
        }
        if isinstance(released_at, str):
            model["released_at"] = released_at
        return model

    def _write_cache(self, catalog: Mapping[str, Any]) -> None:
        temporary_path = self.cache_path.with_suffix(f"{self.cache_path.suffix}.tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )
            temporary_path.replace(self.cache_path)
        except OSError:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _catalog_availability(
    providers: Iterable[Mapping[str, Any]], *, fully_fresh: bool
) -> str:
    provider_list = list(providers)
    if fully_fresh:
        return "fresh"
    if any(provider.get("status") in {"fresh", "stale"} for provider in provider_list):
        return "degraded"
    return "unavailable"


def _failure_category(error: Exception) -> str:
    if isinstance(error, CatalogConfigurationError):
        return "configuration"
    if isinstance(error, HTTPError):
        if error.code in {401, 403}:
            return "configuration"
        return "http"
    if isinstance(error, OSError):
        return "transport"
    if isinstance(error, ValueError):
        return "invalid_response"
    return "unknown"


def _ordered_failure_categories(categories: Iterable[Any]) -> list[str]:
    present = {
        category
        for category in categories
        if isinstance(category, str) and category in _FAILURE_CATEGORY_ORDER
    }
    return [category for category in _FAILURE_CATEGORY_ORDER if category in present]
