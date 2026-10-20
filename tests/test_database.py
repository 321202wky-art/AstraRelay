import pytest

from database import Database


def test_group_history_model_column_is_widened_for_dynamic_model_ids():
    executed = []

    class ExistingSchemaCursor:
        def execute(self, sql, params=None):
            executed.append(sql)

        def fetchall(self):
            if executed[-1] == "SHOW COLUMNS FROM chat_history LIKE 'model'":
                return [("model", "varchar(32)")]
            return [("existing", "varchar(255)")]

    Database._initialize_group_tables(ExistingSchemaCursor())

    assert (
        "ALTER TABLE chat_history MODIFY COLUMN model VARCHAR(255) NULL"
        in executed
    )


def test_recent_messages_are_limited_and_returned_in_chronological_order():
    db = Database.for_testing()
    db.initialize()
    for index in range(10):
        db.add_message("user", f"message-{index}")

    assert [row["content"] for row in db.get_recent_messages(8)] == [
        f"message-{index}" for index in range(2, 10)
    ]


def test_all_messages_are_not_limited_by_default():
    db = Database.for_testing()
    db.initialize()
    for index in range(201):
        db.add_message("user", f"message-{index}")

    assert len(db.get_all_messages()) == 201


def test_conversations_are_owned_by_users_and_messages_are_isolated():
    db = Database.for_testing()
    db.initialize()
    first = db.create_conversation("alice", "第一个会话")
    second = db.create_conversation("alice", "第二个会话")
    other = db.create_conversation("bob", "Bob 会话")

    db.add_message("user", "one", model="deepseek", conversation_id=first["id"])
    db.add_message("user", "two", model="deepseek", conversation_id=second["id"])

    assert [item["id"] for item in db.list_conversations("alice")] == [second["id"], first["id"]]
    assert db.conversation_exists(first["id"], "alice") is True
    assert db.conversation_exists(first["id"], "bob") is False
    assert [item["content"] for item in db.get_recent_messages(model="deepseek", conversation_id=first["id"])] == ["one"]
    assert [item["content"] for item in db.get_conversation_messages(first["id"], "alice")] == ["one"]
    assert db.get_conversation_messages(other["id"], "alice") == []


def test_group_history_preserves_model_and_message_type_metadata():
    db = Database.for_testing()
    db.initialize()

    db.add_message("assistant", "summary", model="deepseek", message_type="summary")

    assert db.get_all_messages()[-1]["model"] == "deepseek"
    assert db.get_all_messages()[-1]["message_type"] == "summary"


def test_shared_conversation_ledger_tracks_model_messages_and_sync_cursor():
    db = Database.for_testing()
    db.initialize()
    conversation = db.create_conversation("alice")

    db.add_collaboration_message(
        "user", "分析需求", conversation_id=conversation["id"]
    )
    db.add_collaboration_message(
        "assistant",
        "先列风险",
        model="deepseek",
        conversation_id=conversation["id"],
    )
    messages = db.get_shared_messages(conversation["id"])
    db.mark_model_context_synced(
        conversation["id"], "gateway_compat:org/model", messages[-1]["id"]
    )

    assert [(message["id"], message["model"]) for message in messages] == [
        (1, None),
        (2, "deepseek"),
    ]
    assert db.get_model_context_sync(
        conversation["id"], "gateway_compat:org/model"
    ) == 2


def test_conversation_model_settings_are_owned_and_replaced_atomically():
    db = Database.for_testing()
    db.initialize()
    conversation = db.create_conversation("alice")

    saved = db.replace_conversation_model_settings(
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
                "message_ids": [1, 2],
            },
        ],
    )

    assert [item["model_id"] for item in saved] == [
        "gateway_main:deepseek-v3",
        "gateway_main:qwen3-32b",
    ]
    assert db.get_conversation_model_settings(conversation["id"], "alice") == saved
    assert db.get_conversation_model_settings(conversation["id"], "bob") == []

    replacement = db.replace_conversation_model_settings(
        conversation["id"],
        "alice",
        [
            {
                "model_id": "gateway_main:kimi-k2",
                "context_mode": "from_now",
                "message_ids": [],
            }
        ],
    )
    assert replacement == db.get_conversation_model_settings(conversation["id"], "alice")
    assert [item["model_id"] for item in replacement] == ["gateway_main:kimi-k2"]


def test_model_preset_round_trip_keeps_context_mode_and_is_user_scoped():
    db = Database.for_testing()
    db.initialize()

    preset = db.create_model_preset(
        "alice",
        "研究组合",
        [
            {
                "model_id": "gateway_main:deepseek-v3",
                "context_mode": "smart",
                "message_ids": [],
            },
            {
                "model_id": "gateway_main:qwen3-32b",
                "context_mode": "custom",
                "message_ids": [3, 5],
            },
        ],
    )

    assert preset["name"] == "研究组合"
    assert preset["items"][1]["context_mode"] == "custom"
    assert preset["items"][1]["message_ids"] == [3, 5]
    assert db.list_model_presets("alice") == [preset]
    assert db.list_model_presets("bob") == []
    assert db.delete_model_preset(preset["id"], "bob") is False
    assert db.delete_model_preset(preset["id"], "alice") is True
    assert db.list_model_presets("alice") == []


def test_registry_initializes_group_and_private_model_databases():
    db = Database.for_testing()

    db.initialize()

    assert db.database_name_for("group") == "group_chat_db"
    assert db.database_name_for("deepseek") == "deepseek_db"
    assert db.database_name_for("kimi") == "kimi_db"


def test_registry_initializes_all_final_model_private_databases():
    db = Database.for_testing()

    db.initialize()

    assert db.database_name_for("zhipu") == "zhipu_db"
    assert db.database_name_for("qwen") == "qwen_db"
    assert db.database_name_for("bytedance") == "bytedance_db"


def test_each_final_model_dual_writes_only_its_own_private_history():
    db = Database.for_testing()
    db.initialize()

    for model in ("zhipu", "qwen", "bytedance"):
        db.add_message("user", model, model=model)

    assert [row["content"] for row in db.get_all_messages()] == [
        "zhipu", "qwen", "bytedance"
    ]
    for model in ("zhipu", "qwen", "bytedance"):
        assert [row["content"] for row in db.get_recent_messages(model=model)] == [model]


def test_model_message_is_written_to_group_and_selected_private_history():
    db = Database.for_testing()
    db.initialize()

    db.add_message("user", "hello Kimi", model="kimi")

    assert [row["content"] for row in db.get_all_messages()] == ["hello Kimi"]
    assert [row["content"] for row in db.get_recent_messages(model="kimi")] == ["hello Kimi"]
    assert db.get_recent_messages(model="deepseek") == []


def test_dual_write_rolls_back_private_history_when_group_write_fails():
    db = Database.for_testing()
    db.initialize()
    original_connect = db._connection_factory

    class FailingGroupCursor:
        def __init__(self, cursor, database):
            self.cursor = cursor
            self.database = database

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, sql, params=None):
            if (
                self.database == "group_chat_db" or "`group_chat_db`." in sql
            ) and sql.startswith("INSERT"):
                raise RuntimeError("simulated group write failure")
            return self.cursor.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class FailingGroupConnection:
        def __init__(self, connection, database):
            self.connection = connection
            self.database = database

        def cursor(self):
            return FailingGroupCursor(self.connection.cursor(), self.database)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def connect(**options):
        database = options.get("database")
        return FailingGroupConnection(original_connect(**options), database)

    db._connection_factory = connect

    with pytest.raises(RuntimeError, match="simulated group write failure"):
        db.add_message("user", "must not persist", model="zhipu")

    assert db.get_recent_messages(model="zhipu") == []
    assert db.get_all_messages() == []


def test_initialize_migrates_legacy_chat_db_records_to_private_and_group_history():
    db = Database.for_testing(legacy_messages=[("user", "legacy DeepSeek message")])

    db.initialize()

    assert [row["content"] for row in db.get_recent_messages()] == ["legacy DeepSeek message"]
    group_messages = db.get_all_messages()
    assert len(group_messages) == 1
    assert group_messages[0]["content"] == "legacy DeepSeek message"
    assert group_messages[0]["model"] == "deepseek"
    assert group_messages[0]["message_type"] == "reply"
    assert [row[2] for row in db._testing_store["chat_db"]] == ["legacy DeepSeek message"]


def test_initialize_does_not_duplicate_legacy_migration_on_repeat():
    db = Database.for_testing(legacy_messages=[("user", "legacy DeepSeek message")])

    db.initialize()
    db.initialize()

    assert [row["content"] for row in db.get_recent_messages()] == ["legacy DeepSeek message"]
    assert [row["content"] for row in db.get_all_messages()] == ["legacy DeepSeek message"]


def test_upgrade_backfills_group_history_when_old_private_marker_already_exists():
    db = Database.for_testing(legacy_messages=[("user", "legacy DeepSeek message")])
    source_id, role, content, timestamp = db._testing_store["chat_db"][0]
    db._testing_store["_migrations"].add(("chat_db", source_id))
    db._testing_store["deepseek_db"] = [
        (1, role, content, timestamp, None, None)
    ]

    db.initialize()
    db.initialize()

    assert [row["content"] for row in db.get_recent_messages()] == ["legacy DeepSeek message"]
    assert db.get_all_messages() == [
        {
            "role": "user",
            "content": "legacy DeepSeek message",
            "timestamp": str(timestamp),
            "model": "deepseek",
            "message_type": "reply",
        }
    ]


def test_initialize_skips_a_missing_legacy_chat_db_without_creating_it():
    db = Database.for_testing(missing_legacy_database=True)

    db.initialize()

    assert db.get_recent_messages() == []
    assert db.get_all_messages() == []
