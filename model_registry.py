"""Resolve safe public platform model ids into server-only provider metadata."""

from __future__ import annotations


class ModelRegistry:
    """Allow calls only for fresh, callable chat models in the cached directory."""

    def __init__(self, catalog_service, provider_connections):
        self.catalog_service = catalog_service
        self.provider_connections = dict(provider_connections)

    def resolve(self, platform_model_id):
        """Return connection metadata without exposing it through the public catalog."""
        if not isinstance(platform_model_id, str):
            raise ValueError("模型不存在或暂不可用")
        provider_key, separator, model_id = platform_model_id.partition(":")
        if not separator or not provider_key or not model_id:
            raise ValueError("模型不存在或暂不可用")
        connection = self.provider_connections.get(provider_key)
        if not isinstance(connection, dict):
            raise ValueError("模型不存在或暂不可用")

        for provider in self.catalog_service.load().get("providers", []):
            if provider.get("key") != provider_key or provider.get("status") != "fresh":
                continue
            for model in provider.get("capabilities", {}).get("chat", []):
                if (
                    model.get("platform_model_id") == platform_model_id
                    and model.get("model_id") == model_id
                    and model.get("callable") is True
                ):
                    return {"model_id": model_id, **connection}
        raise ValueError("模型不存在或暂不可用")
