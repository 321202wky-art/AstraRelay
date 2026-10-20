import importlib
import threading
import time
from contextlib import contextmanager

import pytest

from collaboration_context import CollaborationContextService
from database import Database


def broadcast_module():
    return importlib.import_module("broadcast")


class FakeDatabase:
    def __init__(self, histories=None):
        self.histories = histories or {}
        self.saved = []
        self._lock = threading.Lock()

    def get_recent_messages(self, limit=8, model="deepseek"):
        assert limit == 8
        return list(self.histories.get(model, []))[-8:]

    def add_message(self, role, content, model="deepseek", message_type="reply"):
        with self._lock:
            self.saved.append((role, content, model, message_type))


class FakeAdapter:
    def __init__(self, replies=None, errors=None):
        self.replies = replies or {}
        self.errors = errors or {}
        self.calls = []
        self._lock = threading.Lock()

    def reply_with_history(self, message, history, model="deepseek"):
        with self._lock:
            self.calls.append((message, list(history), model))
        error = self.errors.get(model)
        if error:
            raise error
        if message.startswith("Summarize the following"):
            return "group summary"
        return self.replies[model]


def wait_for_terminal_states(service, task_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = service.status(task_id)
        if all(
            result["state"] in {"completed", "timed_out", "failed"}
            for result in status["models"].values()
        ):
            return status
        time.sleep(0.01)
    raise AssertionError("broadcast did not finish within two seconds")


def wait_for_task_done(service, task_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = service.status(task_id)
        if status.get("state") == "done":
            return status
        time.sleep(0.01)
    raise AssertionError("broadcast task did not finish within two seconds")


def test_parse_targets_removes_all_mentions_and_orders_unique_supported_models():
    module = broadcast_module()

    targets, cleaned_message = module.parse_targets(
        "Compare @\u5343\u95ee with @Kimi and @\u8c46\u5305, then ask @DeepSeek again.",
        ["bytedance", "kimi", "doubao"],
    )

    assert targets == ["deepseek", "kimi", "qwen", "bytedance"]
    assert cleaned_message == "Compare with and, then ask again."


def test_parse_targets_falls_back_to_deepseek_without_supported_targets():
    module = broadcast_module()

    assert module.parse_targets("Hello @\u8c46\u5305", ["doubao"]) == (["deepseek"], "Hello")


@pytest.mark.parametrize(
    ("message", "target", "body"),
    [
        ("\u8bf7\u95ee@\u667a\u8c31\u4f60\u597d", "zhipu", "\u8bf7\u95ee\u4f60\u597d"),
        ("\u8bf7\u95ee@Kimi\u4f60\u597d", "kimi", "\u8bf7\u95ee\u4f60\u597d"),
    ],
)
def test_parse_targets_removes_mentions_adjacent_to_chinese_text(message, target, body):
    module = broadcast_module()

    assert module.parse_targets(message, []) == ([target], body)


def test_parse_targets_does_not_match_a_model_name_prefix_in_ascii_text():
    module = broadcast_module()

    assert module.parse_targets("Ask @KimiNote", []) == (["deepseek"], "Ask @KimiNote")


def test_broadcast_keeps_sibling_results_when_one_model_fails():
    module = broadcast_module()
    database = FakeDatabase({"kimi": [{"role": "assistant", "content": "Kimi private"}]})
    adapter = FakeAdapter(
        replies={"deepseek": "DeepSeek reply", "kimi": "unused"},
        errors={"kimi": RuntimeError("Kimi failed")},
    )
    service = module.BroadcastService(database, adapter)

    task_id = service.submit("hello", ["deepseek", "kimi"])
    status = wait_for_terminal_states(service, task_id)

    assert status["models"] == {
        "deepseek": {"state": "completed", "message": "DeepSeek reply"},
        "kimi": {
            "state": "failed",
            "message": None,
            "error": "\u6a21\u578b\u56de\u590d\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5",
        },
    }
    assert ("user", "hello", "deepseek", "reply") in database.saved
    assert ("assistant", "DeepSeek reply", "deepseek", "reply") in database.saved
    assert ("user", "hello", "kimi", "reply") in database.saved
    kimi_call = next(call for call in adapter.calls if call[2] == "kimi")
    assert kimi_call[1] == [{"role": "assistant", "content": "Kimi private"}]


def test_broadcast_propagates_the_authenticated_wallet_user_to_worker_threads():
    module = broadcast_module()

    class BillingAwareAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(replies={"deepseek": "paid reply"})
            self.active_username = None
            self.billed_users = []

        @contextmanager
        def billing_user(self, username):
            self.active_username = username
            try:
                yield
            finally:
                self.active_username = None

        def reply_with_history(self, message, history, model="deepseek"):
            self.billed_users.append(self.active_username)
            return super().reply_with_history(message, history, model)

    adapter = BillingAwareAdapter()
    service = module.BroadcastService(FakeDatabase(), adapter)
    task_id = service.submit("hello", ["deepseek"], username="alice")

    wait_for_task_done(service, task_id)

    assert adapter.billed_users == ["alice"]


def test_stream_events_yields_tokens_and_persists_the_completed_reply():
    module = broadcast_module()

    class StreamingAdapter:
        def stream_reply_with_history(self, message, history, model="deepseek"):
            assert message == "hello"
            assert model == "deepseek"
            yield "你"
            yield "好"

    database = FakeDatabase()
    events = list(module.BroadcastService(database, StreamingAdapter()).stream_events("hello", ["deepseek"]))

    assert events == [
        {"event": "start", "models": ["deepseek"]},
        {"event": "token", "model": "deepseek", "token": "你"},
        {"event": "token", "model": "deepseek", "token": "好"},
        {"event": "done", "model": "deepseek"},
    ]
    assert ("user", "hello", "deepseek", "reply") in database.saved
    assert ("assistant", "你好", "deepseek", "reply") in database.saved


def test_stream_events_propagate_the_authenticated_wallet_user():
    module = broadcast_module()

    class BillingAwareStreamingAdapter:
        active_username = None

        @contextmanager
        def billing_user(self, username):
            self.active_username = username
            try:
                yield
            finally:
                self.active_username = None

        def stream_reply_with_history(self, message, history, model="deepseek"):
            assert self.active_username == "alice"
            yield "paid token"

    events = list(
        module.BroadcastService(FakeDatabase(), BillingAwareStreamingAdapter()).stream_events(
            "hello", ["deepseek"], username="alice"
        )
    )

    assert {"event": "token", "model": "deepseek", "token": "paid token"} in events


def test_stream_broadcast_uses_one_shared_conversation_ledger_for_all_models():
    """Models selected for one workspace turn see the same prior task context."""
    module = broadcast_module()
    database = Database.for_testing()
    conversation = database.create_conversation("alice")
    database.add_collaboration_message(
        "user", "请先梳理这个项目的风险", conversation_id=conversation["id"]
    )
    database.add_collaboration_message(
        "assistant", "先确认账户与计费边界", model="deepseek", conversation_id=conversation["id"]
    )

    class SharedContextAdapter:
        def __init__(self):
            self.calls = []
            self._lock = threading.Lock()

        def stream_reply_with_history(self, message, history, model="deepseek"):
            with self._lock:
                self.calls.append((message, history, model))
            yield f"{model} reply"

    adapter = SharedContextAdapter()
    service = module.BroadcastService(
        database, adapter, context_service=CollaborationContextService(database)
    )

    events = list(
        service.stream_events(
            "继续落实方案", ["deepseek", "kimi"], conversation_id=conversation["id"]
        )
    )

    assert {event["model"] for event in events if event["event"] == "done"} == {
        "deepseek",
        "kimi",
    }
    assert len(adapter.calls) == 2
    for message, history, _ in adapter.calls:
        assert message == "继续落实方案"
        assert history == [
            {
                "role": "system",
                "content": "当前协作上下文：\n"
                "用户: 请先梳理这个项目的风险\n"
                "DeepSeek: 先确认账户与计费边界",
            }
        ]

    ledger = database.get_shared_messages(conversation["id"])
    assert [(item["role"], item["model"], item["content"]) for item in ledger] == [
        ("user", None, "请先梳理这个项目的风险"),
        ("assistant", "deepseek", "先确认账户与计费边界"),
        ("user", None, "继续落实方案"),
        ("assistant", "deepseek", "deepseek reply"),
        ("assistant", "kimi", "kimi reply"),
    ]


def test_background_broadcast_finishes_when_conversation_is_deleted_during_context_build():
    module = broadcast_module()
    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("alice")

    class BlockingContext:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def build(self, conversation_id, model_id):
            self.started.set()
            assert self.release.wait(2)
            return []

        def mark_synced(self, *args):
            raise AssertionError("deleted conversation must not sync")

    context = BlockingContext()
    service = module.BroadcastService(
        database, FakeAdapter(replies={"deepseek": "unused"}), context_service=context
    )

    task_id = service.submit("hello", ["deepseek"], conversation_id=conversation["id"])
    assert context.started.wait(1)
    assert database.delete_conversation(conversation["id"], "alice") is True
    context.release.set()
    status = wait_for_task_done(service, task_id)

    assert status["models"]["deepseek"]["state"] == "failed"
    assert database.get_shared_messages(conversation["id"]) == []


def test_deleted_conversation_cannot_publish_a_reply_after_context_sync():
    module = broadcast_module()
    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("alice")

    class BlockingSyncContext:
        def __init__(self):
            self.synced = threading.Event()
            self.release = threading.Event()

        def build(self, *args):
            return []

        def mark_synced(self, *args):
            self.synced.set()
            assert self.release.wait(2)

    context = BlockingSyncContext()
    service = module.BroadcastService(
        database, FakeAdapter(replies={"deepseek": "late reply"}), context_service=context
    )
    task_id = service.submit("hello", ["deepseek"], conversation_id=conversation["id"])

    assert context.synced.wait(1)
    assert database.delete_conversation(conversation["id"], "alice") is True
    service.cancel_conversation(conversation["id"])
    context.release.set()
    status = wait_for_task_done(service, task_id)

    assert status["models"]["deepseek"]["message"] is None
    assert status["models"]["deepseek"]["state"] == "failed"


def test_cancelled_conversation_never_calls_summary_adapter_for_late_replies():
    module = broadcast_module()
    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("alice")

    class BlockingSecondSyncContext:
        def __init__(self):
            self.sync_count = 0
            self.second_synced = threading.Event()
            self.release = threading.Event()
            self.lock = threading.Lock()

        def build(self, *args):
            return []

        def mark_synced(self, *args):
            with self.lock:
                self.sync_count += 1
                is_second = self.sync_count == 2
            if is_second:
                self.second_synced.set()
                assert self.release.wait(2)

    class SummaryRecordingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(replies={"deepseek": "one", "kimi": "two"})
            self.summary_calls = 0

        def reply_with_history(self, message, history, model="deepseek"):
            if message.startswith("Summarize the following"):
                self.summary_calls += 1
                return "must not publish"
            return super().reply_with_history(message, history, model)

    context = BlockingSecondSyncContext()
    adapter = SummaryRecordingAdapter()
    service = module.BroadcastService(database, adapter, context_service=context)
    service._task_executor.shutdown(wait=True)
    service._task_executor = module.ThreadPoolExecutor(max_workers=1)
    task_id = service.submit(
        "compare", ["deepseek", "kimi"], conversation_id=conversation["id"]
    )

    assert context.second_synced.wait(1)
    assert database.delete_conversation(conversation["id"], "alice") is True
    service.cancel_conversation(conversation["id"])
    context.release.set()
    service._task_executor.shutdown(wait=True)
    status = service.status(task_id)

    assert adapter.summary_calls == 0
    assert status["summary"] is None
    assert status["state"] == "done"
    assert all(result["state"] == "failed" for result in status["models"].values())
    assert all(result["message"] is None for result in status["models"].values())


def test_cancellation_wins_atomically_after_summary_check_before_adapter_call():
    module = broadcast_module()
    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("alice")

    class BillingGateAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(replies={"deepseek": "one", "kimi": "two"})
            self.billing_entries = 0
            self.billing_lock = threading.Lock()
            self.summary_billing_entered = threading.Event()
            self.release_summary_billing = threading.Event()
            self.summary_calls = 0

        @contextmanager
        def billing_user(self, username):
            with self.billing_lock:
                self.billing_entries += 1
                is_summary = self.billing_entries == 3
            if is_summary:
                self.summary_billing_entered.set()
                assert self.release_summary_billing.wait(2)
            yield

        def reply_with_history(self, message, history, model="deepseek"):
            if message.startswith("Summarize the following"):
                self.summary_calls += 1
                return "must not publish"
            return super().reply_with_history(message, history, model)

    adapter = BillingGateAdapter()
    service = module.BroadcastService(
        database, adapter, context_service=CollaborationContextService(database)
    )
    service._task_executor.shutdown(wait=True)
    service._task_executor = module.ThreadPoolExecutor(max_workers=1)
    task_id = service.submit(
        "compare", ["deepseek", "kimi"], conversation_id=conversation["id"], username="alice"
    )

    assert adapter.summary_billing_entered.wait(1)
    assert database.delete_conversation(conversation["id"], "alice") is True
    service.cancel_conversation(conversation["id"])
    adapter.release_summary_billing.set()
    service._task_executor.shutdown(wait=True)
    status = service.status(task_id)

    assert adapter.summary_calls == 0
    assert status["summary"] is None
    assert all(result["message"] is None for result in status["models"].values())
    assert task_id not in service._summary_gates


def test_polled_broadcast_uses_the_shared_ledger_when_streaming_is_unavailable():
    """The compatibility polling route cannot reintroduce private model context."""
    module = broadcast_module()
    database = Database.for_testing()
    conversation = database.create_conversation("alice")
    database.add_collaboration_message(
        "user", "先定义共同目标", conversation_id=conversation["id"]
    )

    class RecordingAdapter(FakeAdapter):
        pass

    adapter = RecordingAdapter(replies={"deepseek": "已接收共同目标"})
    service = module.BroadcastService(
        database, adapter, context_service=CollaborationContextService(database)
    )
    task_id = service.submit(
        "继续执行", ["deepseek"], conversation_id=conversation["id"]
    )

    wait_for_terminal_states(service, task_id)

    assert adapter.calls[0][1] == [
        {"role": "system", "content": "当前协作上下文：\n用户: 先定义共同目标"}
    ]
    ledger = database.get_shared_messages(conversation["id"])
    assert [(item["role"], item["model"], item["content"]) for item in ledger] == [
        ("user", None, "先定义共同目标"),
        ("user", None, "继续执行"),
        ("assistant", "deepseek", "已接收共同目标"),
    ]


def test_overlapping_same_model_broadcasts_wait_for_prior_private_reply():
    module = broadcast_module()

    class ContextRecordingDatabase(FakeDatabase):
        def __init__(self):
            super().__init__()
            self.read_count = 0
            self.second_context_read = threading.Event()

        def get_recent_messages(self, limit=8, model="deepseek"):
            with self._lock:
                self.read_count += 1
                if self.read_count == 2:
                    self.second_context_read.set()
            return super().get_recent_messages(limit=limit, model=model)

    class BlockingAdapter:
        def __init__(self):
            self.calls = []
            self.first_call_started = threading.Event()
            self.release_first_call = threading.Event()
            self._lock = threading.Lock()

        def reply_with_history(self, message, history, model="deepseek"):
            with self._lock:
                self.calls.append((message, list(history), model))
            if message == "first":
                self.first_call_started.set()
                assert self.release_first_call.wait(2)
            return f"{message} reply"

    database = ContextRecordingDatabase()
    adapter = BlockingAdapter()
    service = module.BroadcastService(database, adapter)

    first_task = service.submit("first", ["deepseek"])
    assert adapter.first_call_started.wait(1)

    second_task = service.submit("second", ["deepseek"])
    try:
        assert not database.second_context_read.wait(0.25)
        assert [call[0] for call in adapter.calls] == ["first"]
    finally:
        adapter.release_first_call.set()

    wait_for_terminal_states(service, first_task)
    wait_for_terminal_states(service, second_task)


def test_deepseek_summary_does_not_overlap_another_deepseek_history_sequence():
    module = broadcast_module()

    class SummaryLockDatabase(FakeDatabase):
        def __init__(self):
            super().__init__()
            self.watch_for_second_history_read = False
            self.second_history_read = threading.Event()

        def get_recent_messages(self, limit=8, model="deepseek"):
            if model == "deepseek" and self.watch_for_second_history_read:
                self.second_history_read.set()
            return super().get_recent_messages(limit=limit, model=model)

    class SummaryBlockingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(replies={"deepseek": "DeepSeek reply", "kimi": "Kimi reply"})
            self.summary_started = threading.Event()
            self.release_summary = threading.Event()

        def reply_with_history(self, message, history, model="deepseek"):
            if message.startswith("Summarize the following"):
                self.summary_started.set()
                assert self.release_summary.wait(2)
                return "group summary"
            return super().reply_with_history(message, history, model)

    database = SummaryLockDatabase()
    adapter = SummaryBlockingAdapter()
    service = module.BroadcastService(database, adapter)

    first_task = service.submit("first", ["deepseek", "kimi"])
    assert adapter.summary_started.wait(1)

    database.watch_for_second_history_read = True
    second_task = service.submit("second", ["deepseek"])
    try:
        assert not database.second_history_read.wait(0.25)
    finally:
        adapter.release_summary.set()

    wait_for_task_done(service, first_task)
    assert wait_for_terminal_states(service, second_task)["models"]["deepseek"]["state"] == "completed"


def test_broadcast_saves_one_deepseek_summary_for_two_successful_replies():
    module = broadcast_module()
    database = FakeDatabase()
    adapter = FakeAdapter(replies={"deepseek": "DeepSeek reply", "kimi": "Kimi reply"})
    service = module.BroadcastService(database, adapter)

    task_id = service.submit("compare", ["deepseek", "kimi"])
    status = wait_for_task_done(service, task_id)

    summary_calls = [call for call in adapter.calls if call[0].startswith("Summarize the following")]
    summaries = [saved for saved in database.saved if saved[3] == "summary"]
    assert status["models"]["deepseek"]["state"] == "completed"
    assert status["models"]["kimi"]["state"] == "completed"
    assert len(summary_calls) == 1
    assert "DeepSeek reply" in summary_calls[0][0]
    assert "Kimi reply" in summary_calls[0][0]
    assert summaries == [("assistant", "group summary", "deepseek", "summary")]
    assert status["summary"] == {
        "id": status["summary"]["id"],
        "model": "deepseek",
        "content": "group summary",
        "message_type": "summary",
    }


def test_broadcast_skips_summary_when_fewer_than_two_models_succeed():
    module = broadcast_module()
    database = FakeDatabase()
    adapter = FakeAdapter(
        replies={"deepseek": "DeepSeek reply", "kimi": "unused"},
        errors={"kimi": TimeoutError("too slow")},
    )
    service = module.BroadcastService(database, adapter)

    task_id = service.submit("compare", ["deepseek", "kimi"])
    status = wait_for_task_done(service, task_id)

    assert status["models"]["kimi"] == {
        "state": "timed_out",
        "message": None,
        "error": "\u6a21\u578b\u54cd\u5e94\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5",
    }
    assert not [call for call in adapter.calls if call[0].startswith("Summarize the following")]
    assert not [saved for saved in database.saved if saved[3] == "summary"]
    assert status["summary"] is None


def test_each_task_status_exposes_only_its_own_summary():
    module = broadcast_module()

    class TaskSpecificSummaryAdapter(FakeAdapter):
        def reply_with_history(self, message, history, model="deepseek"):
            if message.startswith("Summarize the following"):
                return "first summary" if "User message: first" in message else "second summary"
            return super().reply_with_history(message, history, model)

    database = FakeDatabase()
    adapter = TaskSpecificSummaryAdapter(
        replies={"deepseek": "DeepSeek reply", "kimi": "Kimi reply"}
    )
    service = module.BroadcastService(database, adapter)

    first_task = service.submit("first", ["deepseek", "kimi"])
    second_task = service.submit("second", ["deepseek", "kimi"])

    first_status = wait_for_task_done(service, first_task)
    second_status = wait_for_task_done(service, second_task)

    assert first_status["summary"]["content"] == "first summary"
    assert second_status["summary"]["content"] == "second summary"
    assert first_status["summary"]["id"] != second_status["summary"]["id"]


def test_status_returns_copies_that_cannot_mutate_in_memory_task_state():
    module = broadcast_module()
    database = FakeDatabase()
    adapter = FakeAdapter(replies={"deepseek": "reply"})
    service = module.BroadcastService(database, adapter)

    task_id = service.submit("hello", ["deepseek"])
    original = service.status(task_id)
    original["models"]["deepseek"]["state"] = "corrupted"

    assert service.status(task_id)["models"]["deepseek"]["state"] != "corrupted"


@pytest.mark.parametrize(
    ("error", "expected_state", "expected_error"),
    [
        (
            RuntimeError("upstream credential: secret-value"),
            "failed",
            "\u6a21\u578b\u56de\u590d\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5",
        ),
        (
            TimeoutError("upstream request id: secret-value"),
            "timed_out",
            "\u6a21\u578b\u54cd\u5e94\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5",
        ),
    ],
)
def test_terminal_task_states_expose_safe_chinese_errors(error, expected_state, expected_error):
    module = broadcast_module()
    service = module.BroadcastService(
        FakeDatabase(), FakeAdapter(replies={"deepseek": "unused"}, errors={"deepseek": error})
    )

    task_id = service.submit("hello", ["deepseek"])
    result = wait_for_terminal_states(service, task_id)["models"]["deepseek"]

    assert result["state"] == expected_state
    assert result["error"] == expected_error
    assert "secret-value" not in result["error"]
