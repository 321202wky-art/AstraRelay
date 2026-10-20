"""Independent concurrent SSE event producer for the ChatGPT-style workspace."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from queue import Queue
from threading import Lock


class WorkspaceStreamService:
    """Stream selected workspace models without using legacy BroadcastService behavior."""

    def __init__(self, database, adapter, context_service, model_registry):
        self.database = database
        self.adapter = adapter
        self.context_service = context_service
        self.model_registry = model_registry

    def stream(self, message, conversation_id, username, model_settings, history_message=None):
        """Yield structured events while each selected model runs in its own worker."""
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Message cannot be empty")
        if not isinstance(model_settings, list) or not model_settings:
            raise ValueError("At least one model must be selected")

        model_ids = []
        for setting in model_settings:
            if not isinstance(setting, dict) or not isinstance(
                setting.get("model_id"), str
            ):
                raise ValueError("Invalid workspace model setting")
            model_id = setting["model_id"].strip()
            if not model_id or model_id in model_ids:
                raise ValueError("Invalid workspace model setting")
            self.model_registry.resolve(model_id)
            model_ids.append(model_id)

        stored_message = history_message if isinstance(history_message, str) else message
        self.database.add_collaboration_message(
            "user", stored_message.strip(), conversation_id=conversation_id
        )
        yield {"event": "start", "models": model_ids}

        events = Queue()
        ledger_lock = Lock()

        def invoke(setting):
            model_id = setting["model_id"].strip()
            try:
                history = self.context_service.build(
                    conversation_id,
                    model_id,
                    mode=setting.get("context_mode", "smart"),
                    message_ids=setting.get("message_ids") or [],
                )
                parts = []
                billing_user = getattr(self.adapter, "billing_user", None)
                billing_context = (
                    billing_user(username) if callable(billing_user) else nullcontext()
                )
                with billing_context:
                    for token in self.adapter.stream_reply_with_history(
                        message.strip(), history, model=model_id
                    ):
                        parts.append(token)
                        events.put({"event": "token", "model": model_id, "token": token})
                with ledger_lock:
                    saved = self.database.add_collaboration_message(
                        "assistant",
                        "".join(parts),
                        model=model_id,
                        conversation_id=conversation_id,
                    )
                    self.context_service.mark_synced(
                        conversation_id, model_id, saved["id"]
                    )
                events.put({"event": "done", "model": model_id})
            except TimeoutError:
                events.put(
                    {
                        "event": "error",
                        "model": model_id,
                        "message": "模型响应超时，请稍后重试",
                    }
                )
            except Exception:
                events.put(
                    {
                        "event": "error",
                        "model": model_id,
                        "message": "模型回复失败，请稍后重试",
                    }
                )

        with ThreadPoolExecutor(max_workers=len(model_settings)) as executor:
            for setting in model_settings:
                executor.submit(invoke, setting)
            remaining = len(model_settings)
            while remaining:
                event = events.get()
                yield event
                if event["event"] in {"done", "error"}:
                    remaining -= 1
