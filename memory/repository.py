"""Storage port for the independent Memory layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Sequence

from .models import (
    MemoryActorType,
    MemoryAuditAction,
    MemoryProject,
    MemoryRecord,
    MemoryType,
    NewMemory,
)


class MemoryNotFoundError(LookupError):
    pass


class MemoryConflictError(RuntimeError):
    pass


class MemoryRepository(Protocol):
    def create_project_scope(
        self,
        user_id: int,
        project_name: str,
        description: Optional[str] = None,
        external_project_id: Optional[str] = None,
    ) -> MemoryProject: ...

    def get_project_scope(
        self, project_id: str, user_id: int
    ) -> Optional[MemoryProject]: ...

    def resolve_project_scope(
        self, user_id: int, external_project_id: str
    ) -> Optional[MemoryProject]: ...

    def link_external_project(
        self, project_id: str, user_id: int, external_project_id: str
    ) -> MemoryProject: ...

    def create_memory(self, memory: NewMemory) -> MemoryRecord: ...

    def get_memory(
        self, memory_id: str, user_id: int
    ) -> Optional[MemoryRecord]: ...

    def list_memories(
        self,
        user_id: int,
        *,
        project_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 50,
        after_updated_at: Optional[datetime] = None,
        after_id: Optional[str] = None,
    ) -> Sequence[MemoryRecord]: ...

    def update_memory(
        self,
        memory_id: str,
        user_id: int,
        expected_version: int,
        changes: Mapping[str, Any],
    ) -> MemoryRecord: ...

    def delete_memory(self, memory_id: str, user_id: int) -> bool: ...

    def replace_keywords(
        self, memory_id: str, user_id: int, keywords: Mapping[str, float]
    ) -> None: ...

    def set_learning_policy(
        self,
        user_id: int,
        *,
        project_id: Optional[str],
        memory_type: Optional[MemoryType],
        enabled: bool,
        updated_by: str,
    ) -> None: ...

    def get_learning_policy(
        self,
        user_id: int,
        *,
        project_id: Optional[str],
        memory_type: Optional[MemoryType],
    ) -> Optional[bool]: ...

    def append_audit_event(
        self,
        *,
        event_id: str,
        user_id: int,
        memory_id: Optional[str],
        action: MemoryAuditAction,
        actor_type: MemoryActorType,
        actor_id: str,
        metadata: Mapping[str, Any],
        created_at: datetime,
    ) -> None: ...
