import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pymysql
import pytest

import memory.migrations as migrations_module
from memory.migrations import MemoryDatabase
from memory.models import (
    MemoryActorType,
    MemoryAuditAction,
    MemoryCategory,
    MemoryType,
    NewMemory,
)
from memory.mysql_repository import MySQLMemoryRepository
from memory.repository import MemoryConflictError


_RUN_REAL_MYSQL = os.environ.get("ASTRARELAY_RUN_MEMORY_MYSQL_TESTS") == "1"
_TEST_DATABASE_PATTERN = re.compile(
    r"astrarelay_memory_phase1_test_[0-9a-f]{16,32}"
)
_PROTECTED_DATABASES = {
    "memory_db",
    "user_db",
    "group_chat_db",
    "chat_db",
}

pytestmark = pytest.mark.skipif(
    not _RUN_REAL_MYSQL,
    reason="set ASTRARELAY_RUN_MEMORY_MYSQL_TESTS=1 for isolated MySQL 8 tests",
)


def _test_port():
    value = os.environ.get("ASTRARELAY_MEMORY_MYSQL_TEST_PORT")
    if value is None or not value.isdigit() or not 49152 <= int(value) <= 65535:
        raise RuntimeError("isolated MySQL test port must be a high local port")
    return int(value)


def _database_name():
    return f"astrarelay_memory_phase1_test_{uuid4().hex[:20]}"


def _config(database_name):
    return {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": _test_port(),
        "MYSQL_USER": "root",
        "MYSQL_PASSWORD": "",
        "MEMORY_DATABASE": database_name,
        "MEMORY_MIGRATION_LOCK_TIMEOUT_SECONDS": 10,
    }


def _server_connection(database=None):
    options = {
        "host": "127.0.0.1",
        "port": _test_port(),
        "user": "root",
        "password": "",
        "charset": "utf8mb4",
        "autocommit": False,
    }
    if database is not None:
        options["database"] = database
    return pymysql.connect(**options)


def _drop_test_database(database_name):
    if (
        database_name in _PROTECTED_DATABASES
        or _TEST_DATABASE_PATTERN.fullmatch(database_name) is None
    ):
        raise RuntimeError("refusing to drop a non-test database")
    connection = _server_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database_name}`")
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def real_memory_database():
    database_name = _database_name()
    database = MemoryDatabase(_config(database_name))
    try:
        yield database_name, database
    finally:
        _drop_test_database(database_name)


def _new_memory(content_text, **overrides):
    values = {
        "user_id": 7,
        "project_id": None,
        "session_id": None,
        "memory_type": MemoryType.USER,
        "category": MemoryCategory.RULE,
        "content": {"rule": content_text},
        "content_text": content_text,
        "importance": 0.9,
        "confidence": 1.0,
        "source": {"type": "integration_test", "ref": "test:memory"},
        "expires_at": None,
    }
    values.update(overrides)
    return NewMemory(**values)


def _scalar(database_name, statement, parameters=None):
    connection = _server_connection(database_name)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            row = cursor.fetchone()
        return None if row is None else row[0]
    finally:
        connection.close()


def test_real_mysql_migration_is_idempotent_and_has_exact_unique_schema(
    real_memory_database,
):
    database_name, database = real_memory_database

    database.initialize()
    database.initialize()

    assert _scalar(
        database_name, "SELECT COUNT(*) FROM memory_schema_migrations"
    ) == 1
    assert _scalar(
        database_name,
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name IN "
        "('memory_projects', 'memories', 'memory_keywords', "
        "'memory_revisions', 'memory_learning_policies', "
        "'memory_audit_events', 'memory_extraction_candidates')",
        (database_name,),
    ) == 7
    assert _scalar(
        database_name,
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'memories' "
        "AND column_name IN ('project_scope_key', 'session_scope_key') "
        "AND is_nullable = 'NO'",
        (database_name,),
    ) == 2
    assert _scalar(
        database_name,
        "SELECT COUNT(DISTINCT column_name) FROM information_schema.statistics "
        "WHERE table_schema = %s AND table_name = 'memories' "
        "AND index_name = 'uq_memories_exact' AND non_unique = 0",
        (database_name,),
    ) == 5


def test_real_mysql_serializes_two_concurrent_initializers(real_memory_database):
    database_name, _ = real_memory_database
    first = MemoryDatabase(_config(database_name))
    second = MemoryDatabase(_config(database_name))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(item.initialize) for item in (first, second)]
        for future in futures:
            future.result(timeout=20)

    assert _scalar(
        database_name, "SELECT COUNT(*) FROM memory_schema_migrations"
    ) == 1


def test_real_mysql_rejects_cross_owner_project_scope_links(real_memory_database):
    database_name, database = real_memory_database
    database.initialize()
    project_id = uuid4().hex

    connection = _server_connection(database_name)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO memory_projects (id, user_id, project_name) "
                "VALUES (%s, %s, %s)",
                (project_id, 7, "Owner-scoped project"),
            )
        connection.commit()

        with pytest.raises(pymysql.err.IntegrityError) as cross_owner:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO memories "
                    "(id, user_id, project_id, project_scope_key, memory_type, "
                    "category, content, content_text, importance, confidence, "
                    "source, content_hash) VALUES "
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        uuid4().hex,
                        8,
                        project_id,
                        project_id,
                        "project",
                        "rule",
                        json.dumps({"rule": "tenant isolation probe"}),
                        "tenant isolation probe",
                        0.9,
                        1.0,
                        json.dumps({"type": "integration_test"}),
                        "d" * 64,
                    ),
                )
        assert cross_owner.value.args[0] == 1452
        connection.rollback()
    finally:
        connection.close()


def test_real_mysql_recovers_from_partially_applied_ddl(
    real_memory_database, monkeypatch
):
    database_name, database = real_memory_database
    recovery_version = 99
    failing = dict(migrations_module.MIGRATIONS)
    failing[recovery_version] = (
        "CREATE TABLE IF NOT EXISTS memory_recovery_probe "
        "(id INT PRIMARY KEY) ENGINE=InnoDB",
        "CREATE TABL deliberately_invalid_sql",
    )
    monkeypatch.setattr(migrations_module, "MIGRATIONS", failing)

    with pytest.raises(Exception):
        database.initialize()

    assert _scalar(
        database_name,
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = 'memory_recovery_probe'",
        (database_name,),
    ) == 1
    assert _scalar(
        database_name,
        "SELECT COUNT(*) FROM memory_schema_migrations WHERE version = %s",
        (recovery_version,),
    ) == 0

    recovered = dict(failing)
    recovered[recovery_version] = (
        "CREATE TABLE IF NOT EXISTS memory_recovery_probe "
        "(id INT PRIMARY KEY) ENGINE=InnoDB",
    )
    monkeypatch.setattr(migrations_module, "MIGRATIONS", recovered)
    database.initialize()

    assert _scalar(
        database_name,
        "SELECT COUNT(*) FROM memory_schema_migrations WHERE version = %s",
        (recovery_version,),
    ) == 1


def test_real_mysql_unique_key_handles_null_scopes_and_duplicate_create(
    real_memory_database,
):
    database_name, database = real_memory_database
    database.initialize()
    repository = MySQLMemoryRepository(database)

    def create_identical():
        return repository.create_memory(_new_memory("same confirmed rule"))

    successes = []
    conflicts = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_identical) for _ in range(2)]
        for future in futures:
            try:
                successes.append(future.result(timeout=20))
            except MemoryConflictError as error:
                conflicts.append(error)

    assert len(successes) == 1
    assert len(conflicts) == 1

    connection = _server_connection(database_name)
    try:
        with connection.cursor() as cursor:
            project_id = uuid4().hex
            cursor.execute(
                "INSERT INTO memory_projects (id, user_id, project_name) "
                "VALUES (%s, %s, %s)",
                (project_id, 7, "Integration scope"),
            )
            common = (
                7,
                "user",
                "rule",
                json.dumps({"rule": "raw uniqueness"}),
                "raw uniqueness",
                0.9,
                1.0,
                json.dumps({"type": "integration_test"}),
                "f" * 64,
            )
            cursor.execute(
                "INSERT INTO memories "
                "(id, user_id, project_id, session_id, memory_type, category, "
                "content, content_text, importance, confidence, source, content_hash) "
                "VALUES (%s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid4().hex,) + common,
            )
            cursor.execute(
                "INSERT INTO memories "
                "(id, user_id, project_id, session_id, session_scope_key, "
                "memory_type, category, "
                "content, content_text, importance, confidence, source, content_hash) "
                "VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid4().hex, common[0], "session:one", "session:one")
                + common[1:],
            )
            cursor.execute(
                "INSERT INTO memories "
                "(id, user_id, project_id, session_id, project_scope_key, "
                "memory_type, category, content, content_text, importance, "
                "confidence, source, content_hash) VALUES "
                "(%s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid4().hex, common[0], project_id, project_id) + common[1:],
            )
        connection.commit()

        with pytest.raises(pymysql.err.OperationalError) as mismatched_scope:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO memories "
                    "(id, user_id, project_id, session_id, memory_type, category, "
                    "content, content_text, importance, confidence, source, "
                    "content_hash) VALUES "
                    "(%s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (uuid4().hex, common[0], project_id)
                    + common[1:-1]
                    + ("e" * 64,),
                )
        assert mismatched_scope.value.args[0] == 3819
        connection.rollback()

        with pytest.raises(pymysql.err.IntegrityError) as duplicate:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO memories "
                    "(id, user_id, project_id, session_id, memory_type, category, "
                    "content, content_text, importance, confidence, source, content_hash) "
                    "VALUES (%s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (uuid4().hex,) + common,
                )
        assert duplicate.value.args[0] == 1062
        connection.rollback()
    finally:
        connection.close()


def test_real_mysql_update_conflict_rolls_back_revision(real_memory_database):
    database_name, database = real_memory_database
    database.initialize()
    repository = MySQLMemoryRepository(database)
    first = repository.create_memory(_new_memory("first rule"))
    second = repository.create_memory(_new_memory("second rule"))

    with pytest.raises(MemoryConflictError, match="already exists"):
        repository.update_memory(
            second.id,
            7,
            expected_version=1,
            changes={
                "content": {"rule": "first rule"},
                "content_text": "first rule",
            },
        )

    unchanged = repository.get_memory(second.id, 7)
    assert unchanged.version == 1
    assert unchanged.content_text == "second rule"
    assert _scalar(
        database_name,
        "SELECT COUNT(*) FROM memory_revisions WHERE memory_id = %s",
        (second.id,),
    ) == 0
    assert repository.get_memory(first.id, 7).content_text == "first rule"


def test_real_mysql_audit_allowlist_and_actor_validation(real_memory_database):
    database_name, database = real_memory_database
    database.initialize()
    repository = MySQLMemoryRepository(database)
    now = datetime.now(timezone.utc)

    repository.append_audit_event(
        event_id=uuid4().hex,
        user_id=7,
        memory_id=None,
        action=MemoryAuditAction.MEMORY_PROJECT_SCOPE_CREATED,
        actor_type=MemoryActorType.AGENT,
        actor_id="agent:worker-1",
        metadata={"external_project_linked": False},
        created_at=now,
    )

    with pytest.raises(ValueError, match="audit metadata"):
        repository.append_audit_event(
            event_id=uuid4().hex,
            user_id=7,
            memory_id=None,
            action=MemoryAuditAction.MEMORY_PROJECT_SCOPE_CREATED,
            actor_type=MemoryActorType.AGENT,
            actor_id="agent:worker-1",
            metadata={"external_project_linked": "private prompt"},
            created_at=now,
        )
    with pytest.raises(ValueError, match="actor_id"):
        repository.append_audit_event(
            event_id=uuid4().hex,
            user_id=7,
            memory_id=None,
            action=MemoryAuditAction.MEMORY_DELETED,
            actor_type=MemoryActorType.AGENT,
            actor_id="agent:13800138000",
            metadata={},
            created_at=now,
        )

    assert _scalar(database_name, "SELECT COUNT(*) FROM memory_audit_events") == 1
