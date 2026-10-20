"""Immutable domain records shared across Memory adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional


class MemoryValidationError(ValueError):
    """Raised when a Memory domain value violates its public contract."""


class MemoryType(str, Enum):
    SESSION = "session"
    PROJECT = "project"
    USER = "user"
    EXPERIENCE = "experience"


class MemoryCategory(str, Enum):
    FACT = "fact"
    DECISION = "decision"
    RULE = "rule"
    PREFERENCE = "preference"
    STATE = "state"
    OUTCOME = "outcome"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemoryAuditAction(str, Enum):
    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_DELETED = "memory.deleted"
    MEMORY_KEYWORDS_REPLACED = "memory.keywords_replaced"
    MEMORY_LEARNING_POLICY_CHANGED = "memory.learning_policy_changed"
    MEMORY_PROJECT_SCOPE_CREATED = "memory.project_scope_created"
    MEMORY_PROJECT_SCOPE_LINKED = "memory.project_scope_linked"


class MemoryActorType(str, Enum):
    USER = "user"
    SERVICE = "service"
    AGENT = "agent"
    DIRECTOR = "director"
    SYSTEM = "system"


@dataclass(frozen=True)
class MemoryProject:
    """Minimal project-scope mapping; it is not a project-management model."""

    id: str
    external_project_id: Optional[str]
    user_id: int
    project_name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class NewMemory:
    """Validated content accepted by a future MemoryManager."""

    user_id: int
    project_id: Optional[str]
    session_id: Optional[str]
    memory_type: MemoryType
    category: MemoryCategory
    content: Mapping[str, Any]
    content_text: str
    importance: float
    confidence: float
    source: Mapping[str, Any]
    expires_at: Optional[datetime]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.user_id, int)
            or isinstance(self.user_id, bool)
            or self.user_id <= 0
        ):
            raise MemoryValidationError("user_id must be a positive integer")
        if not isinstance(self.memory_type, MemoryType):
            raise MemoryValidationError("memory_type must be a MemoryType")
        if not isinstance(self.category, MemoryCategory):
            raise MemoryValidationError("category must be a MemoryCategory")
        if not isinstance(self.content, Mapping) or not self.content:
            raise MemoryValidationError("content must be a non-empty mapping")
        if not isinstance(self.content_text, str) or not self.content_text.strip():
            raise MemoryValidationError("content_text is required")
        if not isinstance(self.source, Mapping) or not self.source:
            raise MemoryValidationError("source must be a non-empty mapping")
        _validate_score("importance", self.importance)
        _validate_score("confidence", self.confidence)
        if self.project_id is not None and (
            not isinstance(self.project_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", self.project_id) is None
        ):
            raise MemoryValidationError(
                "project_id must be a 32-character lowercase hexadecimal ID"
            )
        if self.session_id is not None and (
            not isinstance(self.session_id, str)
            or not self.session_id.strip()
            or len(self.session_id) > 128
        ):
            raise MemoryValidationError(
                "session_id must be a non-empty string up to 128 characters"
            )
        if self.memory_type is MemoryType.PROJECT and not self.project_id:
            raise MemoryValidationError("project_id is required for project memory")
        if self.memory_type is MemoryType.SESSION:
            if not self.session_id:
                raise MemoryValidationError(
                    "session_id is required for session memory"
                )
            if self.expires_at is None:
                raise MemoryValidationError(
                    "expires_at is required for session memory"
                )


@dataclass(frozen=True)
class MemoryRecord:
    """Persisted Memory record returned by a repository."""

    id: str
    user_id: int
    project_id: Optional[str]
    session_id: Optional[str]
    memory_type: MemoryType
    category: MemoryCategory
    content: Mapping[str, Any]
    content_text: str
    importance: float
    confidence: float
    source: Mapping[str, Any]
    status: MemoryStatus
    version: int
    content_hash: str
    embedding_id: Optional[str]
    expires_at: Optional[datetime]
    last_accessed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


def _validate_score(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 1
    ):
        raise MemoryValidationError(f"{name} must be between 0 and 1")
