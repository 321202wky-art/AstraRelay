import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from memory.migrations import MemoryDatabase


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.factory = connection.factory
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, parameters=None):
        normalized = " ".join(statement.split())
        self.factory.statements.append((normalized, parameters))
        if normalized.startswith("SELECT GET_LOCK"):
            if self.factory.serialize_migrations and self.factory.lock_result == 1:
                self.factory.migration_lock.acquire()
                self.connection.holds_lock = True
            self._rows = [(self.factory.lock_result,)]
            return
        if normalized.startswith("SELECT RELEASE_LOCK"):
            if self.connection.holds_lock:
                self.connection.holds_lock = False
                self.factory.migration_lock.release()
            self._rows = [(self.factory.release_result,)]
            return
        if (
            self.factory.fail_on_contains
            and self.factory.fail_on_contains in normalized
        ):
            raise RuntimeError("migration failed")
        if normalized.startswith("SELECT version FROM memory_schema_migrations"):
            observed_versions = sorted(self.factory.applied_versions)
            if self.factory.version_barrier and not self.connection.holds_lock:
                self.factory.version_barrier.wait(timeout=5)
            self._rows = [(version,) for version in observed_versions]
        elif normalized.startswith("INSERT INTO memory_schema_migrations"):
            self.factory.applied_versions.add(int(parameters[0]))
            self._rows = []
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return None if not self._rows else self._rows[0]


class RecordingConnection:
    def __init__(self, factory):
        self.factory = factory
        self.holds_lock = False

    def cursor(self):
        return RecordingCursor(self)

    def commit(self):
        self.factory.commit_count += 1

    def rollback(self):
        self.factory.rollback_count += 1

    def close(self):
        self.factory.close_count += 1


class RecordingFactory:
    def __init__(
        self,
        fail_on_contains=None,
        *,
        lock_result=1,
        serialize_migrations=False,
        version_barrier=None,
    ):
        self.fail_on_contains = fail_on_contains
        self.lock_result = lock_result
        self.release_result = 1
        self.serialize_migrations = serialize_migrations
        self.version_barrier = version_barrier
        self.migration_lock = threading.Lock()
        self.statements = []
        self.applied_versions = set()
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.options = []

    def __call__(self, **options):
        self.options.append(options)
        return RecordingConnection(self)


def sql_text(factory):
    return "\n".join(statement for statement, _ in factory.statements)


def test_memory_database_defaults_to_an_independent_safe_database():
    database = MemoryDatabase(
        {"MYSQL_PASSWORD": ""}, connection_factory=RecordingFactory()
    )

    assert database.database_name == "memory_db"


def test_memory_database_rejects_an_unsafe_identifier():
    with pytest.raises(ValueError, match="MEMORY_DATABASE"):
        MemoryDatabase(
            {"MEMORY_DATABASE": "memory_db; DROP DATABASE user_db"},
            connection_factory=RecordingFactory(),
        )


def test_initialize_creates_the_database_and_all_phase_one_tables_once():
    factory = RecordingFactory()
    database = MemoryDatabase(
        {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
    )

    database.initialize()
    database.initialize()

    sql = sql_text(factory)
    assert "CREATE DATABASE IF NOT EXISTS `memory_test`" in sql
    for table in (
        "memory_schema_migrations",
        "memory_projects",
        "memories",
        "memory_keywords",
        "memory_revisions",
        "memory_learning_policies",
        "memory_audit_events",
        "memory_extraction_candidates",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert factory.applied_versions == {1}
    assert sql.count("CREATE TABLE IF NOT EXISTS memories") == 1


def test_memory_projects_schema_has_only_the_approved_seven_columns():
    factory = RecordingFactory()
    MemoryDatabase(
        {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
    ).initialize()

    statement = next(
        sql
        for sql, _ in factory.statements
        if sql.startswith("CREATE TABLE IF NOT EXISTS memory_projects")
    )
    body = statement.split("(", 1)[1].rsplit(") ENGINE", 1)[0]
    column_names = re.findall(
        r"(?:^|,)\s*`?([a-z_]+)`?\s+"
        r"(?:CHAR|VARCHAR|BIGINT|TEXT|TIMESTAMP)\b",
        body,
        re.IGNORECASE,
    )
    assert set(column_names) == {
        "id",
        "external_project_id",
        "user_id",
        "project_name",
        "description",
        "created_at",
        "updated_at",
    }
    assert not re.search(
        r"local_path|root_path|git|repository|project_file", statement, re.IGNORECASE
    )


def test_project_scoped_tables_enforce_project_owner_with_composite_foreign_keys():
    factory = RecordingFactory()
    MemoryDatabase(
        {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
    ).initialize()

    sql = sql_text(factory)
    assert "UNIQUE KEY uq_memory_projects_owner (id, user_id)" in sql
    assert sql.count(
        "FOREIGN KEY (project_id, user_id) REFERENCES memory_projects(id, user_id)"
    ) == 3


def test_memories_schema_enforces_exact_uniqueness_with_checked_scope_keys():
    factory = RecordingFactory()
    MemoryDatabase(
        {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
    ).initialize()

    statement = next(
        sql
        for sql, _ in factory.statements
        if sql.startswith("CREATE TABLE IF NOT EXISTS memories")
    )
    assert "project_scope_key CHAR(32) NOT NULL DEFAULT ''" in statement
    assert "session_scope_key VARCHAR(128) NOT NULL DEFAULT ''" in statement
    assert (
        "CONSTRAINT chk_memories_project_scope_key "
        "CHECK (project_scope_key = COALESCE(project_id, ''))"
    ) in statement
    assert (
        "CONSTRAINT chk_memories_session_scope_key "
        "CHECK (session_scope_key = COALESCE(session_id, ''))"
    ) in statement
    assert (
        "UNIQUE KEY uq_memories_exact (user_id, memory_type, "
        "project_scope_key, session_scope_key, content_hash)"
    ) in statement


def test_failed_migration_rolls_back_without_recording_the_version():
    factory = RecordingFactory(fail_on_contains="CREATE TABLE IF NOT EXISTS memories")
    database = MemoryDatabase(
        {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
    )

    with pytest.raises(RuntimeError, match="migration failed"):
        database.initialize()

    assert factory.rollback_count == 1
    assert factory.applied_versions == set()


def test_migration_lock_wraps_version_reads_and_is_released():
    factory = RecordingFactory()
    MemoryDatabase(
        {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
    ).initialize()

    statements = [statement for statement, _ in factory.statements]
    acquire_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT GET_LOCK")
    )
    version_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT version")
    )
    release_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT RELEASE_LOCK")
    )
    assert acquire_index < version_index < release_index


@pytest.mark.parametrize("lock_result", [0, None])
def test_initialize_refuses_to_migrate_without_owning_the_lock(lock_result):
    factory = RecordingFactory(lock_result=lock_result)

    with pytest.raises(RuntimeError, match="migration lock"):
        MemoryDatabase(
            {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
        ).initialize()

    sql = sql_text(factory)
    assert "SELECT version FROM memory_schema_migrations" not in sql
    assert "CREATE TABLE IF NOT EXISTS memories" not in sql


def test_failed_partial_ddl_releases_lock_and_recovers_on_next_initialize():
    factory = RecordingFactory(
        fail_on_contains="CREATE TABLE IF NOT EXISTS memories"
    )
    database = MemoryDatabase(
        {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
    )

    with pytest.raises(RuntimeError, match="migration failed"):
        database.initialize()

    assert factory.applied_versions == set()
    assert any(
        statement.startswith("SELECT RELEASE_LOCK")
        for statement, _ in factory.statements
    )

    factory.fail_on_contains = None
    database.initialize()

    assert factory.applied_versions == {1}
    assert sum(
        statement.startswith("SELECT RELEASE_LOCK")
        for statement, _ in factory.statements
    ) == 2


def test_concurrent_initializers_apply_version_ddl_once():
    factory = RecordingFactory(
        serialize_migrations=True,
        version_barrier=threading.Barrier(2),
    )
    database = MemoryDatabase(
        {"MEMORY_DATABASE": "memory_test"}, connection_factory=factory
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(database.initialize) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)

    assert factory.applied_versions == {1}
    assert sum(
        statement.startswith("CREATE TABLE IF NOT EXISTS memories")
        for statement, _ in factory.statements
    ) == 1
