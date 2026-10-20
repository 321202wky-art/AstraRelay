from datetime import datetime, timedelta, timezone

import pytest

from memory.models import (
    MemoryActorType,
    MemoryAuditAction,
    MemoryCategory,
    MemoryProject,
    MemoryType,
    MemoryValidationError,
    NewMemory,
)


def new_memory(**overrides):
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


def test_user_memory_accepts_structured_private_content():
    assert new_memory().memory_type is MemoryType.USER


def test_audit_contract_exposes_only_approved_actions_and_actor_types():
    assert {item.value for item in MemoryAuditAction} == {
        "memory.created",
        "memory.updated",
        "memory.deleted",
        "memory.keywords_replaced",
        "memory.learning_policy_changed",
        "memory.project_scope_created",
        "memory.project_scope_linked",
    }
    assert {item.value for item in MemoryActorType} == {
        "user",
        "service",
        "agent",
        "director",
        "system",
    }


def test_new_memory_rejects_boolean_user_id():
    with pytest.raises(MemoryValidationError, match="user_id"):
        new_memory(user_id=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("importance", -0.1),
        ("importance", 1.1),
        ("confidence", -0.1),
        ("confidence", 1.1),
    ],
)
def test_memory_scores_must_be_between_zero_and_one(field, value):
    with pytest.raises(MemoryValidationError, match=field):
        new_memory(**{field: value})


def test_project_memory_requires_project_id():
    with pytest.raises(MemoryValidationError, match="project_id"):
        new_memory(memory_type=MemoryType.PROJECT)


@pytest.mark.parametrize(
    "project_id",
    [True, "not-a-memory-project-id", "A" * 32],
)
def test_new_memory_rejects_invalid_project_scope_ids(project_id):
    with pytest.raises(MemoryValidationError, match="project_id"):
        new_memory(project_id=project_id)


@pytest.mark.parametrize("session_id", [True, " ", "x" * 129])
def test_new_memory_rejects_invalid_session_scope_ids(session_id):
    with pytest.raises(MemoryValidationError, match="session_id"):
        new_memory(session_id=session_id)


def test_session_memory_requires_session_id_and_expiration():
    with pytest.raises(MemoryValidationError, match="session_id"):
        new_memory(memory_type=MemoryType.SESSION)

    with pytest.raises(MemoryValidationError, match="expires_at"):
        new_memory(memory_type=MemoryType.SESSION, session_id="task:123")

    item = new_memory(
        memory_type=MemoryType.SESSION,
        session_id="task:123",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    assert item.session_id == "task:123"


def test_memory_project_contains_only_the_approved_seven_fields():
    assert set(MemoryProject.__dataclass_fields__) == {
        "id",
        "external_project_id",
        "user_id",
        "project_name",
        "description",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize(
    "field,value",
    [("memory_type", "user"), ("category", "rule")],
)
def test_new_memory_requires_typed_enums(field, value):
    with pytest.raises(MemoryValidationError, match=field):
        new_memory(**{field: value})
