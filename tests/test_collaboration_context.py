from collaboration_context import CollaborationContextService


class FakeCollaborationDatabase:
    def __init__(self, messages):
        self.messages = messages
        self.synced = []

    def get_shared_messages(self, conversation_id, limit=24):
        assert conversation_id == "conversation-1"
        if limit is None:
            return self.messages
        return self.messages[-limit:]

    def mark_model_context_synced(self, conversation_id, model_id, message_id):
        self.synced.append((conversation_id, model_id, message_id))


def test_new_model_receives_prior_shared_conversation_context():
    database = FakeCollaborationDatabase(
        [
            {"id": 1, "role": "user", "content": "请分析这个需求", "model": None},
            {
                "id": 2,
                "role": "assistant",
                "content": "先拆分风险",
                "model": "deepseek",
            },
        ]
    )

    context = CollaborationContextService(database).build(
        "conversation-1", "gateway_compat:org/model"
    )

    assert context == [
        {
            "role": "system",
            "content": "当前协作上下文：\n用户: 请分析这个需求\nDeepSeek: 先拆分风险",
        }
    ]


def test_successful_model_response_advances_only_its_sync_cursor():
    database = FakeCollaborationDatabase(
        [{"id": 7, "role": "user", "content": "继续", "model": None}]
    )
    context = CollaborationContextService(database)

    context.mark_synced("conversation-1", "gateway_compat:org/model", 7)

    assert database.synced == [("conversation-1", "gateway_compat:org/model", 7)]


def test_context_modes_return_expected_history():
    database = FakeCollaborationDatabase(
        [
            {"id": 1, "role": "user", "content": "requirement", "model": None},
            {
                "id": 2,
                "role": "assistant",
                "content": "summary",
                "model": "deepseek",
                "message_type": "summary",
            },
            {
                "id": 3,
                "role": "assistant",
                "content": "detail",
                "model": "qwen",
                "message_type": "reply",
            },
        ]
    )
    service = CollaborationContextService(database, history_limit=1)

    assert service.build("conversation-1", "qwen", mode="from_now") == []
    assert "requirement" in service.build("conversation-1", "qwen", mode="full")[0]["content"]
    assert "summary" in service.build("conversation-1", "qwen", mode="smart")[0]["content"]
    assert "detail" in service.build(
        "conversation-1", "qwen", mode="custom", message_ids=[3]
    )[0]["content"]
