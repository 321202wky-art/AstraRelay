from threading import Event, Thread

from database import Database


class FakeRegistry:
    def resolve(self, model_id):
        if model_id not in {"gateway_main:deepseek-v3", "gateway_main:qwen3-32b"}:
            raise ValueError("unknown model")
        return {"model_id": model_id}


class FakeAdapter:
    def __init__(self, timeout_models=()):
        self.timeout_models = set(timeout_models)
        self.calls = []

    def stream_reply_with_history(self, message, history, model="deepseek"):
        self.calls.append((message, history, model))
        if model in self.timeout_models:
            raise TimeoutError("upstream timeout")
        yield f"{model}:one"
        yield f"{model}:two"


def test_workspace_stream_persists_successful_models_and_keeps_sibling_after_timeout():
    from collaboration_context import CollaborationContextService
    from workspace_stream_service import WorkspaceStreamService

    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("alice")
    adapter = FakeAdapter(timeout_models={"gateway_main:qwen3-32b"})
    service = WorkspaceStreamService(
        database, adapter, CollaborationContextService(database), FakeRegistry()
    )

    events = list(
        service.stream(
            "analyse this",
            conversation["id"],
            "alice",
            [
                {
                    "model_id": "gateway_main:deepseek-v3",
                    "context_mode": "smart",
                    "message_ids": [],
                },
                {
                    "model_id": "gateway_main:qwen3-32b",
                    "context_mode": "full",
                    "message_ids": [],
                },
            ],
        )
    )

    assert events[0] == {
        "event": "start",
        "models": ["gateway_main:deepseek-v3", "gateway_main:qwen3-32b"],
    }
    assert {event["model"] for event in events if event["event"] == "done"} == {
        "gateway_main:deepseek-v3"
    }
    assert {
        event["model"] for event in events if event["event"] == "error"
    } == {"gateway_main:qwen3-32b"}
    assert [
        event["token"] for event in events if event["event"] == "token"
    ] == ["gateway_main:deepseek-v3:one", "gateway_main:deepseek-v3:two"]

    ledger = database.get_shared_messages(conversation["id"])
    assert [(entry["role"], entry["model"]) for entry in ledger] == [
        ("user", None),
        ("assistant", "gateway_main:deepseek-v3"),
    ]
    assert adapter.calls[0][0] == "analyse this"


def test_workspace_stream_stores_attachment_name_summary_but_sends_context_to_model():
    from collaboration_context import CollaborationContextService
    from workspace_stream_service import WorkspaceStreamService

    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("alice")
    adapter = FakeAdapter()
    service = WorkspaceStreamService(
        database, adapter, CollaborationContextService(database), FakeRegistry()
    )

    list(
        service.stream(
            "Question\n附件 notes.txt：\nprivate attachment body",
            conversation["id"],
            "alice",
            [{"model_id": "gateway_main:deepseek-v3", "context_mode": "smart"}],
            history_message="Question\n[附件：notes.txt]",
        )
    )

    ledger = database.get_shared_messages(conversation["id"])
    assert ledger[0]["content"] == "Question\n[附件：notes.txt]"
    assert adapter.calls[0][0].endswith("private attachment body")


def test_workspace_stream_does_not_restore_state_after_conversation_is_deleted():
    from collaboration_context import CollaborationContextService
    from workspace_stream_service import WorkspaceStreamService

    class BlockingAdapter:
        def __init__(self):
            self.started = Event()
            self.release = Event()

        def stream_reply_with_history(self, message, history, model="deepseek"):
            self.started.set()
            assert self.release.wait(timeout=2)
            yield "late reply"

    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("alice")
    adapter = BlockingAdapter()
    service = WorkspaceStreamService(
        database, adapter, CollaborationContextService(database), FakeRegistry()
    )
    stream = service.stream(
        "analyse this",
        conversation["id"],
        "alice",
        [{"model_id": "gateway_main:deepseek-v3", "context_mode": "smart"}],
    )
    events = []
    runner = Thread(target=lambda: events.extend(stream))
    runner.start()

    assert adapter.started.wait(timeout=2)
    assert database.delete_conversation(conversation["id"], "alice") is True
    adapter.release.set()
    runner.join(timeout=2)

    assert events[0] == {"event": "start", "models": ["gateway_main:deepseek-v3"]}
    assert events[1] == {
        "event": "token",
        "model": "gateway_main:deepseek-v3",
        "token": "late reply",
    }
    assert events[2]["event"] == "error"
    assert events[2]["model"] == "gateway_main:deepseek-v3"
    assert database.get_shared_messages(conversation["id"]) == []
    assert (
        database.get_model_context_sync(
            conversation["id"], "gateway_main:deepseek-v3"
        )
        is None
    )
