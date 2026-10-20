from collections import deque
from datetime import datetime, timezone
import json

import pytest

from memory.models import (
    MemoryActorType,
    MemoryAuditAction,
    MemoryCategory,
    MemoryStatus,
    MemoryType,
    NewMemory,
)
from memory.mysql_repository import MySQLMemoryRepository
from memory.repository import (
    MemoryConflictError,
    MemoryNotFoundError,
)


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


class ScriptedCursor:
    def __init__(self, database):
        self.database = database
        self.response = {}
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, parameters=None):
        normalized = " ".join(statement.split())
        self.database.executions.append((normalized, parameters))
        self.response = (
            self.database.responses.popleft() if self.database.responses else {}
        )
        error = self.response.get("error")
        if error is not None:
            raise error
        self.rowcount = self.response.get("rowcount", 0)

    def fetchone(self):
        return self.response.get("one")

    def fetchall(self):
        return self.response.get("all", [])


class ScriptedConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return ScriptedCursor(self.database)

    def commit(self):
        self.database.commit_count += 1

    def rollback(self):
        self.database.rollback_count += 1

    def close(self):
        self.database.close_count += 1


class ScriptedDatabase:
    def __init__(self, responses=()):
        self.responses = deque(responses)
        self.executions = []
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def connect(self):
        return ScriptedConnection(self)


def project_row(**overrides):
    values = {
        "id": "a" * 32,
        "external_project_id": None,
        "user_id": 7,
        "project_name": "AstraRelay",
        "description": "AI collaboration workbench",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values


def repository_with(responses=()):
    database = ScriptedDatabase(responses)
    repository = MySQLMemoryRepository(database, id_factory=lambda: "a" * 32)
    return repository, database


def new_user_rule(**overrides):
    values = {
        "user_id": 7,
        "project_id": None,
        "session_id": None,
        "memory_type": MemoryType.USER,
        "category": MemoryCategory.RULE,
        "content": {"rule": "核心架构修改前先确认"},
        "content_text": "核心架构修改前先确认",
        "importance": 0.9,
        "confidence": 1.0,
        "source": {"type": "explicit_user_rule", "ref": "conversation:abc"},
        "expires_at": None,
    }
    values.update(overrides)
    return NewMemory(**values)


def memory_row(**overrides):
    values = {
        "id": "b" * 32,
        "user_id": 7,
        "project_id": None,
        "session_id": None,
        "memory_type": "user",
        "category": "rule",
        "content": '{"rule":"核心架构修改前先确认"}',
        "content_text": "核心架构修改前先确认",
        "importance": 0.9,
        "confidence": 1.0,
        "source": '{"ref":"conversation:abc","type":"explicit_user_rule"}',
        "status": "active",
        "version": 1,
        "content_hash": "c" * 64,
        "embedding_id": None,
        "expires_at": None,
        "last_accessed_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values


def test_create_project_scope_persists_only_the_approved_fields():
    repository, database = repository_with(
        [{"rowcount": 1}, {"one": project_row()}]
    )

    project = repository.create_project_scope(
        7, "AstraRelay", "AI collaboration workbench"
    )

    insert_sql, parameters = database.executions[0]
    assert "INSERT INTO memory_projects" in insert_sql
    assert parameters == (
        "a" * 32,
        None,
        7,
        "AstraRelay",
        "AI collaboration workbench",
    )
    assert project.user_id == 7
    assert set(project.__dataclass_fields__) == {
        "id",
        "external_project_id",
        "user_id",
        "project_name",
        "description",
        "created_at",
        "updated_at",
    }


def test_get_project_scope_is_always_user_scoped():
    repository, database = repository_with([{"one": None}])

    assert repository.get_project_scope("a" * 32, 7) is None

    sql, parameters = database.executions[0]
    assert "WHERE id = %s AND user_id = %s" in sql
    assert parameters == ("a" * 32, 7)


def test_resolve_project_scope_uses_owner_and_external_id():
    repository, database = repository_with([{"one": project_row()}])

    project = repository.resolve_project_scope(7, "formal-project-9")

    sql, parameters = database.executions[0]
    assert "WHERE user_id = %s AND external_project_id = %s" in sql
    assert parameters == (7, "formal-project-9")
    assert project.id == "a" * 32


def test_link_external_project_updates_only_an_owned_scope():
    repository, database = repository_with([{"rowcount": 0}])

    with pytest.raises(MemoryNotFoundError, match="project scope"):
        repository.link_external_project("a" * 32, 7, "formal-project-9")

    sql, parameters = database.executions[0]
    assert "WHERE id = %s AND user_id = %s" in sql
    assert parameters == ("formal-project-9", "a" * 32, 7)
    assert database.rollback_count == 1


@pytest.mark.parametrize(
    "arguments,error",
    [
        ((0, "AstraRelay", None, None), "user_id"),
        ((7, " ", None, None), "project_name"),
        ((7, "AstraRelay", None, "x" * 129), "external_project_id"),
    ],
)
def test_create_project_scope_rejects_invalid_values_before_sql(arguments, error):
    repository, database = repository_with()

    with pytest.raises(ValueError, match=error):
        repository.create_project_scope(*arguments)

    assert database.executions == []


def test_create_memory_serializes_structured_fields_and_hashes_content():
    repository, database = repository_with(
        [
            {"rowcount": 1},
            {"one": memory_row()},
        ]
    )

    created = repository.create_memory(new_user_rule())

    insert_sql, parameters = next(
        execution
        for execution in database.executions
        if execution[0].startswith("INSERT INTO memories")
    )
    assert "embedding_id" not in insert_sql
    assert "project_scope_key" in insert_sql
    assert "session_scope_key" in insert_sql
    assert parameters[1] == 7
    assert parameters[4:6] == ("", "")
    assert json.loads(parameters[8]) == {"rule": "核心架构修改前先确认"}
    assert json.loads(parameters[12]) == {
        "ref": "conversation:abc",
        "type": "explicit_user_rule",
    }
    assert len(parameters[13]) == 64
    assert len(created.content_hash) == 64
    assert database.executions[0][0].startswith("INSERT INTO memories")


def test_create_memory_maps_database_unique_conflict_without_prequery():
    repository, database = repository_with(
        [{"error": RuntimeError(1062, "duplicate key")}]
    )

    with pytest.raises(MemoryConflictError, match="already exists"):
        repository.create_memory(new_user_rule())

    assert len(database.executions) == 1
    assert database.executions[0][0].startswith("INSERT INTO memories")
    assert database.rollback_count == 1


def test_create_memory_preserves_non_duplicate_database_errors():
    repository, database = repository_with(
        [{"error": RuntimeError(1205, "lock wait timeout")}]
    )

    with pytest.raises(RuntimeError, match="lock wait timeout"):
        repository.create_memory(new_user_rule())

    assert database.rollback_count == 1


def test_create_memory_revalidates_user_id_before_persistence():
    repository, database = repository_with()
    memory = new_user_rule()
    object.__setattr__(memory, "user_id", True)

    with pytest.raises(ValueError, match="user_id"):
        repository.create_memory(memory)

    assert database.executions == []
    assert database.close_count == 0


def test_create_project_memory_requires_an_owned_project_scope():
    repository, database = repository_with([{"one": None}])

    with pytest.raises(MemoryNotFoundError, match="project scope"):
        repository.create_memory(
            new_user_rule(
                project_id="a" * 32,
                memory_type=MemoryType.PROJECT,
                category=MemoryCategory.FACT,
            )
        )

    assert not any(
        sql.startswith("INSERT INTO memories") for sql, _ in database.executions
    )


def test_get_memory_never_queries_without_the_owner():
    repository, database = repository_with([{"one": None}])

    assert repository.get_memory("b" * 32, 7) is None

    sql, parameters = database.executions[0]
    assert "WHERE id = %s AND user_id = %s" in sql
    assert parameters == ("b" * 32, 7)


def test_list_memories_uses_cursor_pagination_and_bounded_limit():
    repository, database = repository_with([{"all": []}])

    assert repository.list_memories(
        7,
        project_id="a" * 32,
        memory_type=MemoryType.PROJECT,
        limit=25,
        after_updated_at=NOW,
        after_id="c" * 32,
    ) == []

    sql, parameters = database.executions[0]
    assert "user_id = %s" in sql
    assert "project_id = %s" in sql
    assert "memory_type = %s" in sql
    assert "updated_at < %s" in sql
    assert "updated_at = %s AND id < %s" in sql
    assert "ORDER BY updated_at DESC, id DESC LIMIT %s" in sql
    assert parameters == (
        7,
        "a" * 32,
        "project",
        NOW,
        NOW,
        "c" * 32,
        25,
    )


@pytest.mark.parametrize(
    "after_updated_at,after_id",
    [(NOW, None), (None, "c" * 32)],
)
def test_list_memories_requires_both_cursor_components(
    after_updated_at, after_id
):
    repository, database = repository_with()

    with pytest.raises(ValueError, match="cursor"):
        repository.list_memories(
            7, after_updated_at=after_updated_at, after_id=after_id
        )

    assert database.executions == []


@pytest.mark.parametrize("limit", [0, 101, True])
def test_list_memories_rejects_unbounded_limits(limit):
    repository, database = repository_with()

    with pytest.raises(ValueError, match="limit"):
        repository.list_memories(7, limit=limit)

    assert database.executions == []


def test_update_memory_records_revision_and_requires_expected_version():
    previous = memory_row(version=3)
    updated_row = memory_row(
        version=4,
        content='{"rule":"先提交设计再修改"}',
        content_text="先提交设计再修改",
        content_hash="d" * 64,
    )
    repository, database = repository_with(
        [
            {"one": previous},
            {"rowcount": 1},
            {"rowcount": 1},
            {"one": updated_row},
        ]
    )

    updated = repository.update_memory(
        "b" * 32,
        7,
        expected_version=3,
        changes={
            "content": {"rule": "先提交设计再修改"},
            "content_text": "先提交设计再修改",
        },
    )

    sql = "\n".join(statement for statement, _ in database.executions)
    assert "SELECT" in sql and "FOR UPDATE" in sql
    assert "INSERT INTO memory_revisions" in sql
    assert "version = version + 1" in sql
    assert updated.version == 4


def test_update_memory_reports_conflict_for_a_stale_version():
    repository, database = repository_with([{"one": memory_row(version=3)}])

    with pytest.raises(MemoryConflictError, match="version"):
        repository.update_memory(
            "b" * 32, 7, expected_version=2, changes={"importance": 0.8}
        )

    assert database.rollback_count == 1
    assert not any(
        sql.startswith("INSERT INTO memory_revisions")
        for sql, _ in database.executions
    )


def test_update_memory_maps_database_unique_conflict_and_rolls_back_revision():
    repository, database = repository_with(
        [
            {"one": memory_row(version=3)},
            {"rowcount": 1},
            {"error": RuntimeError(1062, "duplicate key")},
        ]
    )

    with pytest.raises(MemoryConflictError, match="already exists"):
        repository.update_memory(
            "b" * 32,
            7,
            expected_version=3,
            changes={"content": {"rule": "duplicate"}},
        )

    assert any(
        sql.startswith("INSERT INTO memory_revisions")
        for sql, _ in database.executions
    )
    assert database.rollback_count == 1
    assert database.commit_count == 0


@pytest.mark.parametrize(
    "forbidden",
    [
        "user_id",
        "project_id",
        "session_id",
        "memory_type",
        "status",
        "version",
        "embedding_id",
        "created_at",
        "updated_at",
    ],
)
def test_update_memory_rejects_scope_and_system_fields(forbidden):
    repository, database = repository_with()

    with pytest.raises(ValueError, match="unsupported memory changes"):
        repository.update_memory(
            "b" * 32, 7, expected_version=1, changes={forbidden: "value"}
        )

    assert database.executions == []


def test_delete_memory_is_owner_scoped_and_physical():
    repository, database = repository_with([{"rowcount": 1}])

    assert repository.delete_memory("b" * 32, 7) is True

    sql, parameters = database.executions[0]
    assert sql.startswith("DELETE FROM memories")
    assert "user_id = %s" in sql
    assert parameters == ("b" * 32, 7)
    assert database.commit_count == 1


def test_memory_row_mapping_preserves_private_experience_type_without_learning():
    repository, _ = repository_with(
        [{"one": memory_row(memory_type="experience", category="outcome")}]
    )

    item = repository.get_memory("b" * 32, 7)

    assert item.memory_type is MemoryType.EXPERIENCE
    assert item.category is MemoryCategory.OUTCOME
    assert item.status is MemoryStatus.ACTIVE


def test_replace_keywords_checks_ownership_and_replaces_in_one_transaction():
    repository, database = repository_with(
        [
            {"one": {"id": "b" * 32}},
            {"rowcount": 2},
            {"rowcount": 1},
            {"rowcount": 1},
        ]
    )

    repository.replace_keywords(
        "b" * 32, 7, {"架构": 1.0, "确认": 0.7}
    )

    executions = database.executions
    assert "FOR UPDATE" in executions[0][0]
    assert executions[0][1] == ("b" * 32, 7)
    assert executions[1][0].startswith("DELETE FROM memory_keywords")
    inserted = [
        parameters
        for sql, parameters in executions
        if sql.startswith("INSERT INTO memory_keywords")
    ]
    assert inserted == [
        ("b" * 32, "架构", 1.0),
        ("b" * 32, "确认", 0.7),
    ]
    assert database.commit_count == 1


@pytest.mark.parametrize(
    "keywords,error",
    [
        ({" ": 1.0}, "keyword"),
        ({"x" * 129: 1.0}, "keyword"),
        ({f"key-{index}": 0.5 for index in range(65)}, "64"),
        ({"架构": -0.1}, "weight"),
        ({"架构": 1.1}, "weight"),
    ],
)
def test_replace_keywords_rejects_invalid_metadata_before_sql(keywords, error):
    repository, database = repository_with()

    with pytest.raises(ValueError, match=error):
        repository.replace_keywords("b" * 32, 7, keywords)

    assert database.executions == []


@pytest.mark.parametrize(
    "project_id,memory_type,project_key,type_key",
    [
        (None, None, "*", "*"),
        ("a" * 32, None, "a" * 32, "*"),
        (None, MemoryType.USER, "*", "user"),
        ("a" * 32, MemoryType.PROJECT, "a" * 32, "project"),
    ],
)
def test_set_learning_policy_uses_a_stable_normalized_scope(
    project_id, memory_type, project_key, type_key
):
    responses = []
    if project_id is not None:
        responses.append({"one": project_row()})
    responses.append({"rowcount": 1})
    repository, database = repository_with(responses)

    repository.set_learning_policy(
        7,
        project_id=project_id,
        memory_type=memory_type,
        enabled=False,
        updated_by="user:7",
    )

    sql, parameters = next(
        execution
        for execution in database.executions
        if execution[0].startswith("INSERT INTO memory_learning_policies")
    )
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert parameters == (
        7,
        project_id,
        None if memory_type is None else memory_type.value,
        project_key,
        type_key,
        False,
        "user:7",
    )


def test_set_learning_policy_rejects_an_unowned_project():
    repository, database = repository_with([{"one": None}])

    with pytest.raises(MemoryNotFoundError, match="project scope"):
        repository.set_learning_policy(
            7,
            project_id="a" * 32,
            memory_type=MemoryType.PROJECT,
            enabled=False,
            updated_by="user:7",
        )

    assert not any(
        sql.startswith("INSERT INTO memory_learning_policies")
        for sql, _ in database.executions
    )


@pytest.mark.parametrize("stored,expected", [(None, None), (0, False), (1, True)])
def test_get_learning_policy_returns_only_the_exact_scope(stored, expected):
    row = None if stored is None else {"enabled": stored}
    repository, database = repository_with([{"one": row}])

    assert repository.get_learning_policy(
        7, project_id=None, memory_type=MemoryType.USER
    ) is expected

    sql, parameters = database.executions[0]
    assert "project_scope_key = %s" in sql
    assert "memory_type_scope_key = %s" in sql
    assert parameters == (7, "*", "user")


def test_audit_event_persists_only_sanitized_metadata():
    repository, database = repository_with([{"rowcount": 1}])

    repository.append_audit_event(
        event_id="d" * 32,
        user_id=7,
        memory_id="b" * 32,
        action=MemoryAuditAction.MEMORY_CREATED,
        actor_type=MemoryActorType.USER,
        actor_id="account:7",
        metadata={
            "memory_type": MemoryType.USER,
            "category": MemoryCategory.RULE,
            "project_scoped": False,
            "session_scoped": False,
        },
        created_at=NOW,
    )

    sql, parameters = database.executions[0]
    assert "INSERT INTO memory_audit_events" in sql
    assert json.loads(parameters[6]) == {
        "memory_type": "user",
        "category": "rule",
        "project_scoped": False,
        "session_scoped": False,
    }
    assert "核心架构修改前先确认" not in str(parameters)


def test_audit_event_rejects_secret_text_under_an_innocent_key():
    repository, database = repository_with()

    with pytest.raises(ValueError, match="audit metadata"):
        repository.append_audit_event(
            event_id="d" * 32,
            user_id=7,
            memory_id="b" * 32,
            action=MemoryAuditAction.MEMORY_CREATED,
            actor_type=MemoryActorType.USER,
            actor_id="account:7",
            metadata={
                "memory_type": "private prompt and secret value",
                "category": MemoryCategory.RULE,
                "project_scoped": False,
                "session_scoped": False,
            },
            created_at=NOW,
        )

    assert database.executions == []


@pytest.mark.parametrize(
    "action,actor_type",
    [
        ("memory.deleted", MemoryActorType.USER),
        (MemoryAuditAction.MEMORY_DELETED, "user"),
    ],
)
def test_audit_event_requires_typed_action_and_actor(action, actor_type):
    repository, database = repository_with()

    with pytest.raises(ValueError, match="action|actor_type"):
        repository.append_audit_event(
            event_id="d" * 32,
            user_id=7,
            memory_id=None,
            action=action,
            actor_type=actor_type,
            actor_id="account:7",
            metadata={},
            created_at=NOW,
        )

    assert database.executions == []


@pytest.mark.parametrize(
    "action,metadata",
    [
        (MemoryAuditAction.MEMORY_DELETED, {"project_scoped": False}),
        (MemoryAuditAction.MEMORY_PROJECT_SCOPE_LINKED, {"linked": True}),
        (MemoryAuditAction.MEMORY_UPDATED, {"changed_fields": ["user_id"]}),
        (MemoryAuditAction.MEMORY_UPDATED, {"changed_fields": []}),
        (MemoryAuditAction.MEMORY_KEYWORDS_REPLACED, {"keyword_count": 65}),
        (MemoryAuditAction.MEMORY_KEYWORDS_REPLACED, {"keyword_count": True}),
        (
            MemoryAuditAction.MEMORY_PROJECT_SCOPE_CREATED,
            {"external_project_linked": False, "extra": False},
        ),
    ],
)
def test_audit_event_rejects_non_allowlisted_metadata(action, metadata):
    repository, database = repository_with()

    with pytest.raises(ValueError, match="audit metadata"):
        repository.append_audit_event(
            event_id="d" * 32,
            user_id=7,
            memory_id=None,
            action=action,
            actor_type=MemoryActorType.USER,
            actor_id="account:7",
            metadata=metadata,
            created_at=NOW,
        )

    assert database.executions == []


@pytest.mark.parametrize(
    "actor_type,actor_id",
    [
        (MemoryActorType.USER, "account:13800138000"),
        (MemoryActorType.USER, "account:user@example.com"),
        (MemoryActorType.USER, "account:7:secret"),
        (MemoryActorType.USER, "user:7"),
        (MemoryActorType.AGENT, "agent:13800138000"),
        (MemoryActorType.AGENT, "agent:this is private"),
        (MemoryActorType.AGENT, "agent:" + "a" * 65),
        (MemoryActorType.AGENT, "service:worker"),
    ],
)
def test_audit_event_rejects_non_stable_or_sensitive_actor_ids(
    actor_type, actor_id
):
    repository, database = repository_with()

    with pytest.raises(ValueError, match="actor_id"):
        repository.append_audit_event(
            event_id="d" * 32,
            user_id=7,
            memory_id=None,
            action=MemoryAuditAction.MEMORY_DELETED,
            actor_type=actor_type,
            actor_id=actor_id,
            metadata={},
            created_at=NOW,
        )

    assert database.executions == []


def test_deleted_audit_event_does_not_query_the_deleted_memory():
    repository, database = repository_with([{"rowcount": 1}])

    repository.append_audit_event(
        event_id="d" * 32,
        user_id=7,
        memory_id="b" * 32,
        action=MemoryAuditAction.MEMORY_DELETED,
        actor_type=MemoryActorType.USER,
        actor_id="account:7",
        metadata={},
        created_at=NOW,
    )

    assert len(database.executions) == 1
    assert database.executions[0][0].startswith("INSERT INTO memory_audit_events")


@pytest.mark.parametrize(
    "action,metadata",
    [
        (
            MemoryAuditAction.MEMORY_CREATED,
            {
                "memory_type": MemoryType.USER,
                "category": MemoryCategory.RULE,
                "project_scoped": False,
                "session_scoped": False,
            },
        ),
        (MemoryAuditAction.MEMORY_UPDATED, {"changed_fields": ["importance"]}),
        (MemoryAuditAction.MEMORY_DELETED, {}),
        (MemoryAuditAction.MEMORY_KEYWORDS_REPLACED, {"keyword_count": 2}),
        (
            MemoryAuditAction.MEMORY_LEARNING_POLICY_CHANGED,
            {"enabled": False, "project_scoped": False, "memory_type": None},
        ),
        (
            MemoryAuditAction.MEMORY_PROJECT_SCOPE_CREATED,
            {"external_project_linked": False},
        ),
        (MemoryAuditAction.MEMORY_PROJECT_SCOPE_LINKED, {}),
    ],
)
def test_each_approved_audit_action_accepts_only_typed_metadata(action, metadata):
    repository, database = repository_with([{"rowcount": 1}])

    repository.append_audit_event(
        event_id="d" * 32,
        user_id=7,
        memory_id=None,
        action=action,
        actor_type=MemoryActorType.USER,
        actor_id="account:7",
        metadata=metadata,
        created_at=NOW,
    )

    assert json.loads(database.executions[0][1][6]) == metadata


@pytest.mark.parametrize(
    "actor_type,actor_id",
    [
        (MemoryActorType.USER, "account:7"),
        (MemoryActorType.SERVICE, "service:memory"),
        (MemoryActorType.AGENT, "agent:worker-1"),
        (MemoryActorType.DIRECTOR, "director:planner-1"),
        (MemoryActorType.SYSTEM, "system:maintenance"),
    ],
)
def test_audit_event_accepts_typed_stable_actor_ids(actor_type, actor_id):
    repository, database = repository_with([{"rowcount": 1}])

    repository.append_audit_event(
        event_id="d" * 32,
        user_id=7,
        memory_id=None,
        action=MemoryAuditAction.MEMORY_DELETED,
        actor_type=actor_type,
        actor_id=actor_id,
        metadata={},
        created_at=NOW,
    )

    assert database.executions[0][1][4:6] == (actor_type.value, actor_id)


@pytest.mark.parametrize(
    "forbidden",
    [
        "content",
        "content_text",
        "prompt",
        "transcript",
        "password",
        "token",
        "api_key",
        "phone",
        "email",
        "payment",
    ],
)
def test_audit_event_rejects_content_and_sensitive_metadata(forbidden):
    repository, database = repository_with()

    with pytest.raises(ValueError, match="audit metadata"):
        repository.append_audit_event(
            event_id="d" * 32,
            user_id=7,
            memory_id=None,
            action=MemoryAuditAction.MEMORY_DELETED,
            actor_type=MemoryActorType.USER,
            actor_id="account:7",
            metadata={"safe": {forbidden: "private"}},
            created_at=NOW,
        )

    assert database.executions == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_audit_event_rejects_non_finite_json_numbers(value):
    repository, database = repository_with()

    with pytest.raises(ValueError, match="audit metadata"):
        repository.append_audit_event(
            event_id="d" * 32,
            user_id=7,
            memory_id=None,
            action=MemoryAuditAction.MEMORY_DELETED,
            actor_type=MemoryActorType.USER,
            actor_id="account:7",
            metadata={"score": value},
            created_at=NOW,
        )

    assert database.executions == []
