"""Concurrent in-memory orchestration for multi-model chat broadcasts."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from copy import deepcopy
from queue import Queue
import re
import threading
from uuid import uuid4


MODEL_ORDER = ("deepseek", "kimi", "zhipu", "qwen", "bytedance")
_MODEL_NAMES = {
    "deepseek": "deepseek",
    "kimi": "kimi",
    "\u667a\u8c31": "zhipu",
    "\u5343\u95ee": "qwen",
    "\u5b57\u8282": "bytedance",
    "\u8c46\u5305": "doubao",
}
_MENTION_PATTERN = re.compile(
    r"@(DeepSeek|Kimi|\u667a\u8c31|\u5343\u95ee|\u5b57\u8282|\u8c46\u5305)(?![A-Za-z0-9_])", re.IGNORECASE
)
FAILED_MODEL_ERROR = "\u6a21\u578b\u56de\u590d\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5"
TIMED_OUT_MODEL_ERROR = "\u6a21\u578b\u54cd\u5e94\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5"


def parse_targets(message, selected_models):
    """Return supported targets and a message with all model mentions removed."""
    mentioned = []

    def remove_mention(match):
        mentioned.append(_MODEL_NAMES[match.group(1).lower()])
        return ""

    cleaned_message = re.sub(r"\s+", " ", _MENTION_PATTERN.sub(remove_mention, message)).strip()
    cleaned_message = re.sub(r"\s+([,.;:!?])", r"\1", cleaned_message)
    selected = selected_models if isinstance(selected_models, (list, tuple, set)) else ()
    requested = set(mentioned)
    requested.update(model.lower() for model in selected if isinstance(model, str))
    targets = [model for model in MODEL_ORDER if model in requested]
    return (targets or ["deepseek"]), cleaned_message


def _completed_history(messages):
    completed = list(messages)
    while completed and completed[-1].get("role") == "user":
        completed.pop()
    return completed


class BroadcastService:
    """Schedule independent model replies and retain task state in process memory."""

    def __init__(self, database, adapter, context_service=None):
        self.database = database
        self.adapter = adapter
        self.context_service = context_service
        self._tasks = {}
        self._task_conversations = {}
        self._cancelled_tasks = set()
        self._summary_gates = {}
        self._lock = threading.Lock()
        self._model_locks = {}
        self._conversation_ledger_lock = threading.Lock()
        self._task_executor = ThreadPoolExecutor()

    def submit(self, message, selected_models, conversation_id=None, username=None):
        targets, cleaned_message = parse_targets(message, selected_models)
        task_id = uuid4().hex
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "state": "running",
                "summary": None,
                "models": {
                    model: {"state": "thinking", "message": None} for model in targets
                },
            }
            self._task_conversations[task_id] = conversation_id
            self._summary_gates[task_id] = threading.Lock()
        self._task_executor.submit(
            self._run_task, task_id, cleaned_message, targets, conversation_id, username
        )
        return task_id

    def cancel_conversation(self, conversation_id):
        """Prevent deleted conversations from publishing delayed task results."""
        with self._lock:
            task_ids = [
                task_id
                for task_id in self._tasks
                if self._task_conversations.get(task_id) == conversation_id
            ]
        for task_id in task_ids:
            with self._lock:
                gate = self._summary_gates.get(task_id)
            if gate is None:
                self._cancel_task(task_id)
                continue
            with gate:
                self._cancel_task(task_id)

    def _cancel_task(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            self._cancelled_tasks.add(task_id)
            task["summary"] = None
            task["state"] = "done"
            for result in task["models"].values():
                result["message"] = None
                if result["state"] != "failed":
                    result["state"] = "failed"
                    result["error"] = FAILED_MODEL_ERROR

    def status(self, task_id):
        """Return a copy of task state without exposing mutable internal data."""
        with self._lock:
            task = self._tasks.get(task_id)
            return deepcopy(task) if task is not None else None

    def stream_events(self, message, selected_models, conversation_id=None, username=None):
        """Yield SSE-ready events, using one shared ledger for workspace conversations."""
        targets, cleaned_message = parse_targets(message, selected_models)
        events = Queue()
        yield {"event": "start", "models": targets}
        shared_history = None
        shared_context = conversation_id is not None and self.context_service is not None
        if shared_context:
            shared_history = self.context_service.build(conversation_id, targets[0])
            with self._conversation_ledger_lock:
                self.database.add_collaboration_message(
                    "user", cleaned_message, conversation_id=conversation_id
                )

        def stream_for_model(model):
            reply_parts = []
            with self._get_model_lock(model):
                try:
                    if shared_context:
                        history = shared_history
                    else:
                        history = _completed_history(
                            self.database.get_recent_messages(limit=8, model=model, **({"conversation_id": conversation_id} if conversation_id is not None else {}))
                        )
                        self.database.add_message("user", cleaned_message, model=model, **({"conversation_id": conversation_id} if conversation_id is not None else {}))
                    with self._billing_user(username):
                        if hasattr(self.adapter, "stream_reply_with_history"):
                            for token in self.adapter.stream_reply_with_history(
                                cleaned_message, history, model=model
                            ):
                                reply_parts.append(token)
                                events.put({"event": "token", "model": model, "token": token})
                        else:
                            reply = self.adapter.reply_with_history(cleaned_message, history, model=model)
                            reply_parts.append(reply)
                            events.put({"event": "token", "model": model, "token": reply})
                    reply = "".join(reply_parts)
                    if shared_context:
                        with self._conversation_ledger_lock:
                            saved = self.database.add_collaboration_message(
                                "assistant", reply, model=model, conversation_id=conversation_id
                            )
                            self.context_service.mark_synced(
                                conversation_id, model, saved["id"]
                            )
                    else:
                        self.database.add_message("assistant", reply, model=model, **({"conversation_id": conversation_id} if conversation_id is not None else {}))
                    events.put({"event": "done", "model": model})
                except TimeoutError:
                    events.put({"event": "error", "model": model, "message": TIMED_OUT_MODEL_ERROR})
                except Exception:
                    events.put({"event": "error", "model": model, "message": FAILED_MODEL_ERROR})

        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            for model in targets:
                executor.submit(stream_for_model, model)
            finished_models = 0
            while finished_models < len(targets):
                event = events.get()
                yield event
                if event["event"] in {"done", "error"}:
                    finished_models += 1

    def _run_task(self, task_id, message, targets, conversation_id=None, username=None):
        successful_replies = {}
        shared_context = conversation_id is not None and self.context_service is not None
        shared_history = None
        try:
            if shared_context:
                shared_history = self.context_service.build(conversation_id, targets[0])
                with self._conversation_ledger_lock:
                    self.database.add_collaboration_message(
                        "user", message, conversation_id=conversation_id
                    )
            with ThreadPoolExecutor(max_workers=len(targets)) as executor:
                futures = {
                    executor.submit(
                        self._reply_for_model,
                        task_id,
                        message,
                        model,
                        conversation_id,
                        username,
                        shared_context,
                        shared_history,
                    ): model
                    for model in targets
                }
                for future in as_completed(futures):
                    model = futures[future]
                    try:
                        successful_replies[model] = future.result()
                    except Exception:
                        continue
            if len(successful_replies) >= 2 and not self._is_task_cancelled(task_id):
                self._create_summary(
                    task_id,
                    message,
                    successful_replies,
                    conversation_id,
                    username,
                    shared_context,
                )
        except Exception:
            self._fail_unfinished_models(task_id)
        finally:
            with self._lock:
                self._tasks[task_id]["state"] = "done"
                self._summary_gates.pop(task_id, None)

    def _fail_unfinished_models(self, task_id):
        with self._lock:
            for result in self._tasks[task_id]["models"].values():
                if result["state"] == "thinking":
                    result["state"] = "failed"
                    result["message"] = None
                    result["error"] = FAILED_MODEL_ERROR

    def _is_task_cancelled(self, task_id):
        with self._lock:
            return task_id in self._cancelled_tasks

    def _summary_gate(self, task_id):
        with self._lock:
            return self._summary_gates.get(task_id)

    def _reply_for_model(
        self,
        task_id,
        message,
        model,
        conversation_id=None,
        username=None,
        shared_context=False,
        shared_history=None,
    ):
        with self._get_model_lock(model):
            try:
                if shared_context:
                    history = shared_history
                else:
                    history = _completed_history(
                        self.database.get_recent_messages(limit=8, model=model, **({"conversation_id": conversation_id} if conversation_id is not None else {}))
                    )
                    self.database.add_message("user", message, model=model, **({"conversation_id": conversation_id} if conversation_id is not None else {}))
                with self._billing_user(username):
                    if hasattr(self.adapter, "reply_with_history"):
                        reply = self.adapter.reply_with_history(message, history, model=model)
                    else:
                        reply = self.adapter.reply(message)
                if shared_context:
                    with self._conversation_ledger_lock:
                        saved = self.database.add_collaboration_message(
                            "assistant", reply, model=model, conversation_id=conversation_id
                        )
                        self.context_service.mark_synced(
                            conversation_id, model, saved["id"]
                        )
                else:
                    self.database.add_message("assistant", reply, model=model, **({"conversation_id": conversation_id} if conversation_id is not None else {}))
                self._set_state(task_id, model, "completed", reply)
                return reply
            except TimeoutError:
                self._set_state(task_id, model, "timed_out", error=TIMED_OUT_MODEL_ERROR)
                raise
            except Exception:
                self._set_state(task_id, model, "failed", error=FAILED_MODEL_ERROR)
                raise

    def _get_model_lock(self, model):
        with self._lock:
            return self._model_locks.setdefault(model, threading.Lock())

    def _billing_user(self, username):
        context_factory = getattr(self.adapter, "billing_user", None)
        if username is None or not callable(context_factory):
            return nullcontext()
        return context_factory(username)

    def _create_summary(
        self,
        task_id,
        message,
        successful_replies,
        conversation_id=None,
        username=None,
        shared_context=False,
    ):
        if self._is_task_cancelled(task_id):
            return
        reply_lines = "\n".join(
            f"{model}: {successful_replies[model]}"
            for model in MODEL_ORDER
            if model in successful_replies
        )
        summary_prompt = (
            "Summarize the following independent model replies for the user.\n"
            f"User message: {message}\n"
            f"Replies:\n{reply_lines}"
        )
        try:
            with self._get_model_lock("deepseek"):
                if self._is_task_cancelled(task_id):
                    return
                with self._billing_user(username):
                    gate = self._summary_gate(task_id)
                    if gate is None:
                        return
                    with gate:
                        if self._is_task_cancelled(task_id):
                            return
                        if hasattr(self.adapter, "reply_with_history"):
                            summary = self.adapter.reply_with_history(summary_prompt, [], model="deepseek")
                        else:
                            summary = self.adapter.reply(summary_prompt)
                if shared_context:
                    with self._conversation_ledger_lock:
                        saved = self.database.add_collaboration_message(
                            "assistant",
                            summary,
                            model="deepseek",
                            message_type="summary",
                            conversation_id=conversation_id,
                        )
                        self.context_service.mark_synced(
                            conversation_id, "deepseek", saved["id"]
                        )
                else:
                    self.database.add_message(
                        "assistant", summary, model="deepseek", message_type="summary", **({"conversation_id": conversation_id} if conversation_id is not None else {})
                    )
            with self._lock:
                if task_id in self._cancelled_tasks:
                    return
                self._tasks[task_id]["summary"] = {
                    "id": uuid4().hex,
                    "model": "deepseek",
                    "content": summary,
                    "message_type": "summary",
                }
        except Exception:
            return

    def _set_state(self, task_id, model, state, message=None, error=None):
        with self._lock:
            task = self._tasks[task_id]
            result = task["models"][model]
            if task_id in self._cancelled_tasks:
                result["state"] = "failed"
                result["message"] = None
                result["error"] = FAILED_MODEL_ERROR
                return
            result["state"] = state
            result["message"] = message
            if error is not None:
                result["error"] = error
