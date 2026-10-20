"""Build shared, speaker-labelled context for collaborating models."""

from __future__ import annotations


_MODEL_LABELS = {
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "zhipu": "智谱",
    "qwen": "千问",
    "bytedance": "字节跳动",
    "qwen_official": "Qwen-Max",
}


class CollaborationContextService:
    """Use the conversation ledger as the only model collaboration context."""

    def __init__(self, database, history_limit=24):
        self.database = database
        self.history_limit = history_limit

    def build(self, conversation_id, model_id, mode="smart", message_ids=None):
        """Return the selected shared history as one speaker-labelled system message."""
        if mode not in {"smart", "full", "from_now", "custom"}:
            raise ValueError("Unsupported context mode")
        if mode == "from_now":
            return []

        messages = self.database.get_shared_messages(conversation_id, limit=None)
        if mode == "smart":
            summaries = [
                message
                for message in messages
                if message.get("message_type") == "summary"
            ]
            recent = [
                message
                for message in messages
                if message.get("message_type") != "summary"
            ][-self.history_limit :]
            selected_ids = {message.get("id") for message in summaries + recent}
            messages = [
                message for message in messages if message.get("id") in selected_ids
            ]
        elif mode == "custom":
            selected_ids = set(message_ids or [])
            messages = [
                message for message in messages if message.get("id") in selected_ids
            ]
        if not messages:
            return []
        lines = [self._format_message(message) for message in messages]
        return [{"role": "system", "content": "当前协作上下文：\n" + "\n".join(lines)}]

    def mark_synced(self, conversation_id, model_id, message_id):
        """Record the latest shared event acknowledged by a model request."""
        self.database.mark_model_context_synced(conversation_id, model_id, message_id)

    @staticmethod
    def _format_message(message):
        role = message.get("role")
        content = message.get("content", "")
        if role == "user":
            return f"用户: {content}"
        model = message.get("model")
        label = _MODEL_LABELS.get(model, model or "协作模型")
        return f"{label}: {content}"
