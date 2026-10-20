"""PyMySQL persistence adapter for the independent Memory database."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Mapping, Optional
from uuid import uuid4

from .models import (
    MemoryActorType,
    MemoryAuditAction,
    MemoryCategory,
    MemoryProject,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    NewMemory,
)
from .repository import MemoryConflictError, MemoryNotFoundError


_MEMORY_SELECT = (
    "SELECT id, user_id, project_id, session_id, memory_type, category, "
    "content, content_text, importance, confidence, source, status, version, "
    "content_hash, embedding_id, expires_at, last_accessed_at, created_at, "
    "updated_at FROM memories"
)

_UPDATABLE_MEMORY_FIELDS = frozenset(
    {"content", "content_text", "importance", "confidence", "source", "expires_at"}
)

class MySQLMemoryRepository:
    """Persist Memory scopes without depending on chat or project modules."""

    def __init__(self, database, id_factory=None):
        self.database = database
        self._id_factory = id_factory or (lambda: uuid4().hex)

    def create_project_scope(
        self,
        user_id: int,
        project_name: str,
        description: Optional[str] = None,
        external_project_id: Optional[str] = None,
    ) -> MemoryProject:
        user_id = self._validate_user_id(user_id)
        project_name = self._required_text("project_name", project_name, 255)
        description = self._optional_text("description", description)
        external_project_id = self._optional_text(
            "external_project_id", external_project_id, 128
        )
        project_id = self._new_id()
        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO memory_projects "
                    "(id, external_project_id, user_id, project_name, description) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        project_id,
                        external_project_id,
                        user_id,
                        project_name,
                        description,
                    ),
                )
            connection.commit()
        except Exception as error:
            connection.rollback()
            if self._is_duplicate_error(error):
                raise MemoryConflictError("project scope already exists") from error
            raise
        finally:
            connection.close()
        project = self.get_project_scope(project_id, user_id)
        if project is None:
            raise RuntimeError("created project scope could not be loaded")
        return project

    def get_project_scope(
        self, project_id: str, user_id: int
    ) -> Optional[MemoryProject]:
        project_id = self._validate_id("project_id", project_id)
        user_id = self._validate_user_id(user_id)
        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, external_project_id, user_id, project_name, "
                    "description, created_at, updated_at FROM memory_projects "
                    "WHERE id = %s AND user_id = %s",
                    (project_id, user_id),
                )
                row = cursor.fetchone()
            return None if row is None else self._as_project(row)
        finally:
            connection.close()

    def resolve_project_scope(
        self, user_id: int, external_project_id: str
    ) -> Optional[MemoryProject]:
        user_id = self._validate_user_id(user_id)
        external_project_id = self._required_text(
            "external_project_id", external_project_id, 128
        )
        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, external_project_id, user_id, project_name, "
                    "description, created_at, updated_at FROM memory_projects "
                    "WHERE user_id = %s AND external_project_id = %s",
                    (user_id, external_project_id),
                )
                row = cursor.fetchone()
            return None if row is None else self._as_project(row)
        finally:
            connection.close()

    def link_external_project(
        self, project_id: str, user_id: int, external_project_id: str
    ) -> MemoryProject:
        project_id = self._validate_id("project_id", project_id)
        user_id = self._validate_user_id(user_id)
        external_project_id = self._required_text(
            "external_project_id", external_project_id, 128
        )
        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE memory_projects SET external_project_id = %s "
                    "WHERE id = %s AND user_id = %s",
                    (external_project_id, project_id, user_id),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise MemoryNotFoundError("project scope not found")
            connection.commit()
        except MemoryNotFoundError:
            raise
        except Exception as error:
            connection.rollback()
            if self._is_duplicate_error(error):
                raise MemoryConflictError("external project already linked") from error
            raise
        finally:
            connection.close()
        project = self.get_project_scope(project_id, user_id)
        if project is None:
            raise MemoryNotFoundError("project scope not found")
        return project

    def create_memory(self, memory: NewMemory) -> MemoryRecord:
        if not isinstance(memory, NewMemory):
            raise ValueError("memory must be a NewMemory instance")
        self._validate_user_id(memory.user_id)
        if memory.project_id is not None:
            if self.get_project_scope(memory.project_id, memory.user_id) is None:
                raise MemoryNotFoundError("project scope not found")

        memory_id = self._new_id()
        content_json = self._canonical_json(memory.content)
        source_json = self._canonical_json(memory.source)
        content_hash = self._content_hash(memory, content_json)

        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO memories "
                    "(id, user_id, project_id, session_id, project_scope_key, "
                    "session_scope_key, memory_type, category, content, "
                    "content_text, importance, confidence, source, content_hash, "
                    "expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s)",
                    (
                        memory_id,
                        memory.user_id,
                        memory.project_id,
                        memory.session_id,
                        memory.project_id or "",
                        memory.session_id or "",
                        memory.memory_type.value,
                        memory.category.value,
                        content_json,
                        memory.content_text.strip(),
                        float(memory.importance),
                        float(memory.confidence),
                        source_json,
                        content_hash,
                        memory.expires_at,
                    ),
                )
            connection.commit()
        except Exception as error:
            connection.rollback()
            if self._is_duplicate_error(error):
                raise MemoryConflictError("memory already exists") from error
            raise
        finally:
            connection.close()

        created = self.get_memory(memory_id, memory.user_id)
        if created is None:
            raise RuntimeError("created memory could not be loaded")
        return created

    def get_memory(
        self, memory_id: str, user_id: int
    ) -> Optional[MemoryRecord]:
        memory_id = self._validate_id("memory_id", memory_id)
        user_id = self._validate_user_id(user_id)
        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    _MEMORY_SELECT + " WHERE id = %s AND user_id = %s",
                    (memory_id, user_id),
                )
                row = cursor.fetchone()
            return None if row is None else self._as_memory(row)
        finally:
            connection.close()

    def list_memories(
        self,
        user_id: int,
        *,
        project_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 50,
        after_updated_at: Optional[datetime] = None,
        after_id: Optional[str] = None,
    ):
        user_id = self._validate_user_id(user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if project_id is not None:
            project_id = self._validate_id("project_id", project_id)
        if memory_type is not None and not isinstance(memory_type, MemoryType):
            raise ValueError("memory_type must be a MemoryType")
        if (after_updated_at is None) != (after_id is None):
            raise ValueError("cursor requires after_updated_at and after_id")
        if after_updated_at is not None and not isinstance(after_updated_at, datetime):
            raise ValueError("after_updated_at must be a datetime")
        if after_id is not None:
            after_id = self._validate_id("after_id", after_id)

        conditions = ["user_id = %s"]
        parameters = [user_id]
        if project_id is not None:
            conditions.append("project_id = %s")
            parameters.append(project_id)
        if memory_type is not None:
            conditions.append("memory_type = %s")
            parameters.append(memory_type.value)
        if after_updated_at is not None:
            conditions.append("(updated_at < %s OR (updated_at = %s AND id < %s))")
            parameters.extend((after_updated_at, after_updated_at, after_id))
        parameters.append(limit)

        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    _MEMORY_SELECT
                    + " WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY updated_at DESC, id DESC LIMIT %s",
                    tuple(parameters),
                )
                rows = cursor.fetchall()
            return [self._as_memory(row) for row in rows]
        finally:
            connection.close()

    def update_memory(
        self,
        memory_id: str,
        user_id: int,
        expected_version: int,
        changes: Mapping[str, Any],
    ) -> MemoryRecord:
        memory_id = self._validate_id("memory_id", memory_id)
        user_id = self._validate_user_id(user_id)
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version <= 0
        ):
            raise ValueError("expected_version must be a positive integer")
        if not isinstance(changes, Mapping) or not changes:
            raise ValueError("changes must be a non-empty mapping")
        unsupported = set(changes) - _UPDATABLE_MEMORY_FIELDS
        if unsupported:
            raise ValueError("unsupported memory changes")

        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    _MEMORY_SELECT
                    + " WHERE id = %s AND user_id = %s FOR UPDATE",
                    (memory_id, user_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise MemoryNotFoundError("memory not found")
                current = self._as_memory(row)
                if current.version != expected_version:
                    raise MemoryConflictError("memory version conflict")

                validated = NewMemory(
                    user_id=current.user_id,
                    project_id=current.project_id,
                    session_id=current.session_id,
                    memory_type=current.memory_type,
                    category=current.category,
                    content=changes.get("content", current.content),
                    content_text=changes.get("content_text", current.content_text),
                    importance=changes.get("importance", current.importance),
                    confidence=changes.get("confidence", current.confidence),
                    source=changes.get("source", current.source),
                    expires_at=changes.get("expires_at", current.expires_at),
                )
                content_json = self._canonical_json(validated.content)
                source_json = self._canonical_json(validated.source)
                content_hash = self._content_hash(validated, content_json)

                cursor.execute(
                    "INSERT INTO memory_revisions "
                    "(memory_id, version, content, content_text, importance, "
                    "confidence, source) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        current.id,
                        current.version,
                        self._canonical_json(current.content),
                        current.content_text,
                        current.importance,
                        current.confidence,
                        self._canonical_json(current.source),
                    ),
                )
                cursor.execute(
                    "UPDATE memories SET content = %s, content_text = %s, "
                    "importance = %s, confidence = %s, source = %s, "
                    "expires_at = %s, content_hash = %s, version = version + 1 "
                    "WHERE id = %s AND user_id = %s AND version = %s",
                    (
                        content_json,
                        validated.content_text.strip(),
                        float(validated.importance),
                        float(validated.confidence),
                        source_json,
                        validated.expires_at,
                        content_hash,
                        memory_id,
                        user_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise MemoryConflictError("memory version conflict")
            connection.commit()
        except (MemoryConflictError, MemoryNotFoundError):
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            if self._is_duplicate_error(error):
                raise MemoryConflictError("memory already exists") from error
            raise
        finally:
            connection.close()

        updated = self.get_memory(memory_id, user_id)
        if updated is None:
            raise MemoryNotFoundError("memory not found")
        return updated

    def delete_memory(self, memory_id: str, user_id: int) -> bool:
        memory_id = self._validate_id("memory_id", memory_id)
        user_id = self._validate_user_id(user_id)
        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM memories WHERE id = %s AND user_id = %s",
                    (memory_id, user_id),
                )
                deleted = cursor.rowcount == 1
            connection.commit()
            return deleted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace_keywords(
        self, memory_id: str, user_id: int, keywords: Mapping[str, float]
    ) -> None:
        memory_id = self._validate_id("memory_id", memory_id)
        user_id = self._validate_user_id(user_id)
        if not isinstance(keywords, Mapping):
            raise ValueError("keywords must be a mapping")
        if len(keywords) > 64:
            raise ValueError("keywords cannot contain more than 64 entries")

        normalized = []
        for keyword, weight in keywords.items():
            if not isinstance(keyword, str) or not keyword.strip():
                raise ValueError("keyword is required")
            keyword = keyword.strip()
            if len(keyword) > 128:
                raise ValueError("keyword is too long")
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not 0 <= float(weight) <= 1
            ):
                raise ValueError("keyword weight must be between 0 and 1")
            normalized.append((keyword, float(weight)))
        normalized.sort(key=lambda item: item[0])

        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM memories WHERE id = %s AND user_id = %s "
                    "FOR UPDATE",
                    (memory_id, user_id),
                )
                if cursor.fetchone() is None:
                    raise MemoryNotFoundError("memory not found")
                cursor.execute(
                    "DELETE FROM memory_keywords WHERE memory_id = %s",
                    (memory_id,),
                )
                for keyword, weight in normalized:
                    cursor.execute(
                        "INSERT INTO memory_keywords "
                        "(memory_id, keyword, weight) VALUES (%s, %s, %s)",
                        (memory_id, keyword, weight),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_learning_policy(
        self,
        user_id: int,
        *,
        project_id: Optional[str],
        memory_type: Optional[MemoryType],
        enabled: bool,
        updated_by: str,
    ) -> None:
        user_id = self._validate_user_id(user_id)
        if project_id is not None:
            project_id = self._validate_id("project_id", project_id)
            if self.get_project_scope(project_id, user_id) is None:
                raise MemoryNotFoundError("project scope not found")
        if memory_type is not None and not isinstance(memory_type, MemoryType):
            raise ValueError("memory_type must be a MemoryType")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        updated_by = self._required_text("updated_by", updated_by, 255)
        project_scope_key = project_id or "*"
        memory_type_scope_key = memory_type.value if memory_type else "*"

        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO memory_learning_policies "
                    "(user_id, project_id, memory_type, project_scope_key, "
                    "memory_type_scope_key, enabled, updated_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), "
                    "updated_by = VALUES(updated_by), "
                    "updated_at = CURRENT_TIMESTAMP",
                    (
                        user_id,
                        project_id,
                        None if memory_type is None else memory_type.value,
                        project_scope_key,
                        memory_type_scope_key,
                        enabled,
                        updated_by,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_learning_policy(
        self,
        user_id: int,
        *,
        project_id: Optional[str],
        memory_type: Optional[MemoryType],
    ) -> Optional[bool]:
        user_id = self._validate_user_id(user_id)
        if project_id is not None:
            project_id = self._validate_id("project_id", project_id)
        if memory_type is not None and not isinstance(memory_type, MemoryType):
            raise ValueError("memory_type must be a MemoryType")
        project_scope_key = project_id or "*"
        memory_type_scope_key = memory_type.value if memory_type else "*"

        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT enabled FROM memory_learning_policies "
                    "WHERE user_id = %s AND project_scope_key = %s "
                    "AND memory_type_scope_key = %s",
                    (user_id, project_scope_key, memory_type_scope_key),
                )
                row = cursor.fetchone()
            if row is None:
                return None
            value = row.get("enabled") if isinstance(row, dict) else row[0]
            return bool(value)
        finally:
            connection.close()

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
    ) -> None:
        event_id = self._validate_id("event_id", event_id)
        user_id = self._validate_user_id(user_id)
        if memory_id is not None:
            memory_id = self._validate_id("memory_id", memory_id)
        if not isinstance(action, MemoryAuditAction):
            raise ValueError("action must be a MemoryAuditAction")
        if not isinstance(actor_type, MemoryActorType):
            raise ValueError("actor_type must be a MemoryActorType")
        actor_id = self._validate_actor_id(actor_type, actor_id)
        if not isinstance(created_at, datetime):
            raise ValueError("created_at must be a datetime")
        self._validate_audit_metadata(action, metadata)
        metadata_json = self._canonical_json(metadata)
        if len(metadata_json.encode("utf-8")) > 8192:
            raise ValueError("audit metadata is too large")

        connection = self.database.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO memory_audit_events "
                    "(id, user_id, memory_id, action, actor_type, actor_id, "
                    "metadata, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        event_id,
                        user_id,
                        memory_id,
                        action.value,
                        actor_type.value,
                        actor_id,
                        metadata_json,
                        created_at,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @classmethod
    def _content_hash(cls, memory: NewMemory, content_json: str) -> str:
        payload = cls._canonical_json(
            {
                "memory_type": memory.memory_type.value,
                "project_id": memory.project_id,
                "session_id": memory.session_id,
                "content": json.loads(content_json),
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_json(value: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def _validate_audit_metadata(
        cls, action: MemoryAuditAction, metadata: Mapping[str, Any]
    ) -> None:
        if not isinstance(metadata, Mapping):
            raise ValueError("audit metadata must be a mapping")

        if action is MemoryAuditAction.MEMORY_CREATED:
            cls._require_audit_keys(
                metadata,
                {"memory_type", "category", "project_scoped", "session_scoped"},
            )
            if not isinstance(metadata["memory_type"], MemoryType):
                raise ValueError("audit metadata memory_type is invalid")
            if not isinstance(metadata["category"], MemoryCategory):
                raise ValueError("audit metadata category is invalid")
            cls._require_audit_bool(metadata, "project_scoped")
            cls._require_audit_bool(metadata, "session_scoped")
            return

        if action is MemoryAuditAction.MEMORY_UPDATED:
            cls._require_audit_keys(metadata, {"changed_fields"})
            fields = metadata["changed_fields"]
            if (
                not isinstance(fields, (list, tuple))
                or not fields
                or len(fields) > len(_UPDATABLE_MEMORY_FIELDS)
                or any(field not in _UPDATABLE_MEMORY_FIELDS for field in fields)
                or len(set(fields)) != len(fields)
            ):
                raise ValueError("audit metadata changed_fields is invalid")
            return

        if action in {
            MemoryAuditAction.MEMORY_DELETED,
            MemoryAuditAction.MEMORY_PROJECT_SCOPE_LINKED,
        }:
            cls._require_audit_keys(metadata, set())
            return

        if action is MemoryAuditAction.MEMORY_KEYWORDS_REPLACED:
            cls._require_audit_keys(metadata, {"keyword_count"})
            count = metadata["keyword_count"]
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 64:
                raise ValueError("audit metadata keyword_count is invalid")
            return

        if action is MemoryAuditAction.MEMORY_LEARNING_POLICY_CHANGED:
            cls._require_audit_keys(
                metadata, {"enabled", "project_scoped", "memory_type"}
            )
            cls._require_audit_bool(metadata, "enabled")
            cls._require_audit_bool(metadata, "project_scoped")
            memory_type = metadata["memory_type"]
            if memory_type is not None and not isinstance(memory_type, MemoryType):
                raise ValueError("audit metadata memory_type is invalid")
            return

        if action is MemoryAuditAction.MEMORY_PROJECT_SCOPE_CREATED:
            cls._require_audit_keys(metadata, {"external_project_linked"})
            cls._require_audit_bool(metadata, "external_project_linked")
            return

        raise ValueError("audit action is not supported")

    @staticmethod
    def _require_audit_keys(metadata: Mapping[str, Any], expected: set) -> None:
        if set(metadata) != expected:
            raise ValueError("audit metadata fields are invalid")

    @staticmethod
    def _require_audit_bool(metadata: Mapping[str, Any], key: str) -> None:
        if not isinstance(metadata[key], bool):
            raise ValueError(f"audit metadata {key} is invalid")

    @staticmethod
    def _validate_actor_id(actor_type: MemoryActorType, actor_id: str) -> str:
        patterns = {
            MemoryActorType.USER: r"account:[1-9][0-9]{0,9}",
            MemoryActorType.SERVICE: r"service:[a-z0-9][a-z0-9._-]{0,63}",
            MemoryActorType.AGENT: r"agent:[a-z0-9][a-z0-9._-]{0,63}",
            MemoryActorType.DIRECTOR: r"director:[a-z0-9][a-z0-9._-]{0,63}",
            MemoryActorType.SYSTEM: r"system:[a-z0-9][a-z0-9._-]{0,63}",
        }
        if not isinstance(actor_id, str) or not re.fullmatch(
            patterns[actor_type], actor_id
        ):
            raise ValueError("actor_id must be a stable non-sensitive identifier")
        if re.fullmatch(r"[0-9]{11}", actor_id.split(":", 1)[1]):
            raise ValueError("actor_id must be a stable non-sensitive identifier")
        return actor_id

    def _new_id(self) -> str:
        return self._validate_id("generated id", self._id_factory())

    @staticmethod
    def _validate_id(name: str, value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError(f"{name} must be a 32-character lowercase hex id")
        return value

    @staticmethod
    def _validate_user_id(user_id: int) -> int:
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        return user_id

    @staticmethod
    def _required_text(name: str, value: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
        normalized = value.strip()
        if len(normalized) > maximum:
            raise ValueError(f"{name} is too long")
        return normalized

    @classmethod
    def _optional_text(
        cls, name: str, value: Optional[str], maximum: Optional[int] = None
    ) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        normalized = value.strip()
        if not normalized:
            return None
        if maximum is not None and len(normalized) > maximum:
            raise ValueError(f"{name} is too long")
        return normalized

    @staticmethod
    def _as_project(row) -> MemoryProject:
        if isinstance(row, dict):
            values = (
                row["id"],
                row["external_project_id"],
                row["user_id"],
                row["project_name"],
                row["description"],
                row["created_at"],
                row["updated_at"],
            )
        else:
            values = row
        return MemoryProject(
            id=values[0],
            external_project_id=values[1],
            user_id=int(values[2]),
            project_name=values[3],
            description=values[4],
            created_at=values[5],
            updated_at=values[6],
        )

    @staticmethod
    def _as_memory(row) -> MemoryRecord:
        names = (
            "id",
            "user_id",
            "project_id",
            "session_id",
            "memory_type",
            "category",
            "content",
            "content_text",
            "importance",
            "confidence",
            "source",
            "status",
            "version",
            "content_hash",
            "embedding_id",
            "expires_at",
            "last_accessed_at",
            "created_at",
            "updated_at",
        )
        values = row if isinstance(row, dict) else dict(zip(names, row))

        def decoded(value):
            return json.loads(value) if isinstance(value, str) else value

        return MemoryRecord(
            id=values["id"],
            user_id=int(values["user_id"]),
            project_id=values["project_id"],
            session_id=values["session_id"],
            memory_type=MemoryType(values["memory_type"]),
            category=MemoryCategory(values["category"]),
            content=decoded(values["content"]),
            content_text=values["content_text"],
            importance=float(values["importance"]),
            confidence=float(values["confidence"]),
            source=decoded(values["source"]),
            status=MemoryStatus(values["status"]),
            version=int(values["version"]),
            content_hash=values["content_hash"],
            embedding_id=values["embedding_id"],
            expires_at=values["expires_at"],
            last_accessed_at=values["last_accessed_at"],
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

    @staticmethod
    def _is_duplicate_error(error: Exception) -> bool:
        args = getattr(error, "args", ())
        return bool(args and args[0] == 1062)
