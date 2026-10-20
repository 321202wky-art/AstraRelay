"""Server-side public aliases for private upstream model identities."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

from model_catalog import CATALOG_DEGRADED_MESSAGE, CATALOG_UNAVAILABLE_MESSAGE
from model_capabilities import capability_profile


PUBLIC_MODEL_UNAVAILABLE = "该模型暂时不可用，请重新选择"
UNAVAILABLE_PUBLIC_MODEL_ID = "other/unavailable-model"

_UPSTREAM_WORDS = re.compile(
    r"(?i)\b(?:gateway_main|gateway[\s_-]*main|gateway_compat|gateway_secondary)\b"
)
_SLUG_PARTS = re.compile(r"[^a-z0-9._-]+")

_BRANDS = (
    ("deepseek", "DeepSeek", ("deepseek",)),
    ("kimi", "Kimi", ("kimi", "moonshot")),
    ("gpt", "GPT", ("gpt", "openai", "o1-", "o3-", "o4-")),
    ("qwen", "千问", ("qwen", "tongyi", "千问")),
    ("glm", "智谱", ("glm", "chatglm", "zhipu", "智谱")),
    ("doubao", "豆包", ("doubao", "bytedance", "byte-dance", "seed")),
    ("minimax", "MiniMax", ("minimax",)),
    ("yi", "零一万物", ("01.ai", "yi-", "/yi", "零一万物")),
    ("baichuan", "百川", ("baichuan", "百川")),
    ("internlm", "书生", ("internlm", "书生")),
    ("stepfun", "阶跃星辰", ("stepfun", "step-", "阶跃")),
)
_BRAND_PRIORITY = {key: index for index, (key, _name, _needles) in enumerate(_BRANDS)}
_BRAND_PRIORITY["other"] = len(_BRANDS)


class PublicModelDirectory:
    """Build reversible public aliases without exposing upstream metadata."""

    def __init__(self, catalog_service: Any) -> None:
        self.catalog_service = catalog_service

    def catalog(self) -> dict[str, Any]:
        public_catalog, _public_to_internal, _internal_to_public = self._build()
        return public_catalog

    def resolve(self, public_model_id: str) -> str:
        _catalog, public_to_internal, _internal_to_public = self._build()
        if not isinstance(public_model_id, str):
            raise ValueError(PUBLIC_MODEL_UNAVAILABLE)
        try:
            return public_to_internal[public_model_id]
        except KeyError as error:
            raise ValueError(PUBLIC_MODEL_UNAVAILABLE) from error

    def expose(self, internal_model_id: str) -> str:
        _catalog, _public_to_internal, internal_to_public = self._build()
        if not isinstance(internal_model_id, str):
            raise ValueError(PUBLIC_MODEL_UNAVAILABLE)
        try:
            return internal_to_public[internal_model_id]
        except KeyError as error:
            raise ValueError(PUBLIC_MODEL_UNAVAILABLE) from error

    def to_internal_settings(self, settings: Any) -> list[dict[str, Any]]:
        if not isinstance(settings, list):
            raise ValueError(PUBLIC_MODEL_UNAVAILABLE)
        _catalog, public_to_internal, _internal_to_public = self._build()
        translated: list[dict[str, Any]] = []
        for item in settings:
            if not isinstance(item, Mapping) or not isinstance(item.get("model_id"), str):
                raise ValueError(PUBLIC_MODEL_UNAVAILABLE)
            try:
                internal_id = public_to_internal[item["model_id"]]
            except KeyError as error:
                raise ValueError(PUBLIC_MODEL_UNAVAILABLE) from error
            copied = dict(item)
            copied["model_id"] = internal_id
            translated.append(copied)
        return translated

    def to_public_settings(self, settings: Any) -> list[dict[str, Any]]:
        if not isinstance(settings, list):
            return []
        _catalog, _public_to_internal, internal_to_public = self._build()
        translated: list[dict[str, Any]] = []
        for item in settings:
            if not isinstance(item, Mapping):
                continue
            public_id = internal_to_public.get(item.get("model_id"))
            if public_id is None:
                continue
            copied = dict(item)
            copied["model_id"] = public_id
            translated.append(copied)
        return translated

    def expose_messages(self, messages: Any) -> list[dict[str, Any]]:
        if not isinstance(messages, list):
            return []
        _catalog, _public_to_internal, internal_to_public = self._build()
        translated: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, Mapping):
                continue
            copied = dict(item)
            internal_id = copied.get("model")
            if isinstance(internal_id, str) and internal_id:
                copied["model"] = internal_to_public.get(
                    internal_id, UNAVAILABLE_PUBLIC_MODEL_ID
                )
            translated.append(copied)
        return translated

    def expose_presets(self, presets: Any) -> list[dict[str, Any]]:
        if not isinstance(presets, list):
            return []
        translated: list[dict[str, Any]] = []
        for preset in presets:
            if not isinstance(preset, Mapping):
                continue
            copied = dict(preset)
            copied["items"] = self.to_public_settings(preset.get("items"))
            translated.append(copied)
        return translated

    def _build(self):
        raw_catalog = self.catalog_service.load()
        records = list(self._public_records(raw_catalog))
        aliases = self._aliases(records)
        public_to_internal = {
            public_id: record["platform_model_id"]
            for record, public_id in aliases
            if record.get("_catalog_status") == "fresh"
            and record.get("capability") == "chat"
            and record.get("callable") is True
        }
        internal_to_public = {
            record["platform_model_id"]: public_id for record, public_id in aliases
        }

        groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        brand_names: dict[str, str] = {}
        brand_statuses: defaultdict[str, set[str]] = defaultdict(set)
        for record, public_id in aliases:
            brand_key, brand_name = self._brand(record)
            brand_names[brand_key] = brand_name
            source_status = record.get("_catalog_status")
            is_fresh = source_status == "fresh"
            brand_statuses[brand_key].add("fresh" if is_fresh else "stale")
            capability = str(record.get("capability") or "other")
            profile = capability_profile(str(record.get("model_id") or ""), capability)
            callable_model = (
                is_fresh
                and capability == "chat"
                and record.get("callable") is True
            )
            public_model = {
                "platform_model_id": public_id,
                "model_id": public_id.rsplit("/", 1)[-1],
                "display_name": self._safe_display_name(record, brand_name),
                "capability": capability,
                "availability": "fresh" if is_fresh else "degraded",
                "callable": callable_model,
                "selectable": callable_model,
                **profile,
            }
            released_at = record.get("released_at")
            if isinstance(released_at, str):
                public_model["released_at"] = released_at
            groups[brand_key].append(public_model)

        providers = []
        for brand_key in sorted(groups, key=lambda key: _BRAND_PRIORITY.get(key, 999)):
            capability_groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            for model in groups[brand_key]:
                capability_groups[model["capability"]].append(model)
            sorted_capabilities = {
                capability: sorted(
                    models,
                    key=lambda item: (
                        item.get("released_at", ""),
                        item["display_name"].casefold(),
                    ),
                    reverse=True,
                )
                for capability, models in capability_groups.items()
            }
            providers.append(
                {
                    "key": brand_key,
                    "name": brand_names[brand_key],
                    "status": (
                        "stale" if "stale" in brand_statuses[brand_key] else "fresh"
                    ),
                    "capabilities": sorted_capabilities,
                }
            )

        updated_at = raw_catalog.get("updated_at") if isinstance(raw_catalog, Mapping) else None
        availability = self._catalog_availability(raw_catalog, records)
        last_attempt_at = (
            raw_catalog.get("last_attempt_at") if isinstance(raw_catalog, Mapping) else None
        )
        if not isinstance(last_attempt_at, str):
            last_attempt_at = updated_at if isinstance(updated_at, str) else None
        last_success_at = (
            raw_catalog.get("last_success_at") if isinstance(raw_catalog, Mapping) else None
        )
        if not isinstance(last_success_at, str):
            last_success_at = last_attempt_at if availability == "fresh" else None
        failure_categories = self._safe_failure_categories(raw_catalog)
        return (
            {
                "availability": availability,
                "last_attempt_at": last_attempt_at,
                "last_success_at": last_success_at,
                "failure_categories": failure_categories,
                "retryable": bool(
                    isinstance(raw_catalog, Mapping)
                    and raw_catalog.get("retryable") is True
                    and availability != "fresh"
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
            },
            public_to_internal,
            internal_to_public,
        )

    @staticmethod
    def _public_records(catalog: Any) -> Iterable[dict[str, Any]]:
        if not isinstance(catalog, Mapping):
            return
        providers = catalog.get("providers")
        if not isinstance(providers, list):
            return
        for provider in providers:
            if (
                not isinstance(provider, Mapping)
                or provider.get("status") not in {"fresh", "stale"}
            ):
                continue
            source_status = provider["status"]
            capabilities = provider.get("capabilities")
            if not isinstance(capabilities, Mapping):
                continue
            for capability, models in capabilities.items():
                if not isinstance(capability, str) or not isinstance(models, list):
                    continue
                for model in models:
                    if not (
                        isinstance(model, Mapping)
                        and model.get("capability") == capability
                        and isinstance(model.get("platform_model_id"), str)
                        and isinstance(model.get("model_id"), str)
                    ):
                        continue
                    if capability == "chat" and model.get("callable") is not True:
                        continue
                    public_record = dict(model)
                    public_record["_catalog_status"] = source_status
                    yield public_record

    @staticmethod
    def _catalog_availability(
        catalog: Any, records: Iterable[Mapping[str, Any]]
    ) -> str:
        if isinstance(catalog, Mapping) and catalog.get("availability") in {
            "fresh",
            "degraded",
            "unavailable",
        }:
            return str(catalog["availability"])
        statuses = {record.get("_catalog_status") for record in records}
        if statuses == {"fresh"}:
            return "fresh"
        if statuses:
            return "degraded"
        return "unavailable"

    @staticmethod
    def _safe_failure_categories(catalog: Any) -> list[str]:
        allowed = {
            "configuration",
            "transport",
            "http",
            "invalid_response",
            "cache_write",
            "unknown",
        }
        if not isinstance(catalog, Mapping) or not isinstance(
            catalog.get("failure_categories"), list
        ):
            return []
        return [
            category
            for category in catalog["failure_categories"]
            if isinstance(category, str) and category in allowed
        ]

    def _aliases(self, records: list[dict[str, Any]]):
        proposed: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            brand_key, _brand_name = self._brand(record)
            leaf = record["model_id"].rsplit("/", 1)[-1]
            slug = self._slug(leaf) or "model"
            proposed[f"{brand_key}/{slug}"].append(record)

        aliases: list[tuple[dict[str, Any], str]] = []
        for base_id in sorted(proposed):
            collisions = sorted(
                proposed[base_id], key=lambda item: item["platform_model_id"]
            )
            for record in collisions:
                public_id = base_id
                if len(collisions) > 1:
                    digest = hashlib.sha256(
                        record["platform_model_id"].encode("utf-8")
                    ).hexdigest()[:8]
                    public_id = f"{base_id}-{digest}"
                aliases.append((record, public_id))
        return aliases

    @staticmethod
    def _brand(record: Mapping[str, Any]) -> tuple[str, str]:
        haystack = " ".join(
            str(record.get(field, ""))
            for field in ("model_id", "display_name")
        ).casefold()
        for brand_key, brand_name, needles in _BRANDS:
            if any(needle.casefold() in haystack for needle in needles):
                return brand_key, brand_name
        return "other", "其他模型"

    @staticmethod
    def _slug(value: str) -> str:
        normalized = _SLUG_PARTS.sub("-", value.casefold()).strip("-._")
        return normalized[:96]

    @staticmethod
    def _safe_display_name(record: Mapping[str, Any], brand_name: str) -> str:
        raw_name = record.get("display_name") or record.get("model_id") or brand_name
        clean = _UPSTREAM_WORDS.sub("", str(raw_name))
        clean = re.sub(r"\s{2,}", " ", clean).strip(" -_:/")
        return clean[:128] or f"{brand_name} Model"
