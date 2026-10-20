"""Versioned MySQL schema management for the independent Memory database."""

from __future__ import annotations

import hashlib
import re
import sys


_MIGRATION_ONE = (
    """
    CREATE TABLE IF NOT EXISTS memory_projects (
        id CHAR(32) PRIMARY KEY,
        external_project_id VARCHAR(128) NULL,
        user_id BIGINT NOT NULL,
        project_name VARCHAR(255) NOT NULL,
        description TEXT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_memory_projects_owner (id, user_id),
        UNIQUE KEY uq_memory_projects_external (user_id, external_project_id),
        INDEX idx_memory_projects_user_updated (user_id, updated_at)
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
        id CHAR(32) PRIMARY KEY,
        user_id BIGINT NOT NULL,
        project_id CHAR(32) NULL,
        session_id VARCHAR(128) NULL,
        memory_type VARCHAR(32) NOT NULL,
        category VARCHAR(32) NOT NULL,
        content JSON NOT NULL,
        content_text TEXT NOT NULL,
        importance DECIMAL(4,3) NOT NULL,
        confidence DECIMAL(4,3) NOT NULL,
        source JSON NOT NULL,
        status VARCHAR(24) NOT NULL DEFAULT 'active',
        version INT UNSIGNED NOT NULL DEFAULT 1,
        content_hash CHAR(64) NOT NULL,
        project_scope_key CHAR(32) NOT NULL DEFAULT '',
        session_scope_key VARCHAR(128) NOT NULL DEFAULT '',
        embedding_id VARCHAR(255) NULL,
        expires_at DATETIME NULL,
        last_accessed_at DATETIME NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        CONSTRAINT fk_memories_project FOREIGN KEY (project_id, user_id)
            REFERENCES memory_projects(id, user_id) ON DELETE CASCADE,
        CONSTRAINT chk_memories_importance
            CHECK (importance >= 0 AND importance <= 1),
        CONSTRAINT chk_memories_confidence
            CHECK (confidence >= 0 AND confidence <= 1),
        CONSTRAINT chk_memories_project_scope_key
            CHECK (project_scope_key = COALESCE(project_id, '')),
        CONSTRAINT chk_memories_session_scope_key
            CHECK (session_scope_key = COALESCE(session_id, '')),
        INDEX idx_memories_user_updated (user_id, updated_at, id),
        INDEX idx_memories_project_type
            (user_id, project_id, memory_type, status),
        INDEX idx_memories_session_type
            (user_id, session_id, memory_type, status),
        INDEX idx_memories_expiration (memory_type, expires_at),
        INDEX idx_memories_content_hash (user_id, memory_type, content_hash),
        UNIQUE KEY uq_memories_exact
            (user_id, memory_type, project_scope_key,
             session_scope_key, content_hash)
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_keywords (
        memory_id CHAR(32) NOT NULL,
        keyword VARCHAR(128) NOT NULL,
        weight DECIMAL(4,3) NOT NULL,
        PRIMARY KEY (memory_id, keyword),
        INDEX idx_memory_keywords_keyword (keyword, weight),
        CONSTRAINT fk_memory_keywords_memory FOREIGN KEY (memory_id)
            REFERENCES memories(id) ON DELETE CASCADE,
        CONSTRAINT chk_memory_keywords_weight
            CHECK (weight >= 0 AND weight <= 1)
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_revisions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        memory_id CHAR(32) NOT NULL,
        version INT UNSIGNED NOT NULL,
        content JSON NOT NULL,
        content_text TEXT NOT NULL,
        importance DECIMAL(4,3) NOT NULL,
        confidence DECIMAL(4,3) NOT NULL,
        source JSON NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_memory_revisions_version (memory_id, version),
        CONSTRAINT fk_memory_revisions_memory FOREIGN KEY (memory_id)
            REFERENCES memories(id) ON DELETE CASCADE
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_learning_policies (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        project_id CHAR(32) NULL,
        memory_type VARCHAR(32) NULL,
        project_scope_key VARCHAR(32) NOT NULL,
        memory_type_scope_key VARCHAR(32) NOT NULL,
        enabled BOOLEAN NOT NULL,
        updated_by VARCHAR(255) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_memory_learning_scope
            (user_id, project_scope_key, memory_type_scope_key),
        CONSTRAINT fk_memory_learning_project FOREIGN KEY (project_id, user_id)
            REFERENCES memory_projects(id, user_id) ON DELETE CASCADE
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_audit_events (
        id CHAR(32) PRIMARY KEY,
        user_id BIGINT NOT NULL,
        memory_id CHAR(32) NULL,
        action VARCHAR(64) NOT NULL,
        actor_type VARCHAR(32) NOT NULL,
        actor_id VARCHAR(255) NOT NULL,
        metadata JSON NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_memory_audit_user_created (user_id, created_at)
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_extraction_candidates (
        id CHAR(32) PRIMARY KEY,
        user_id BIGINT NOT NULL,
        project_id CHAR(32) NULL,
        session_id VARCHAR(128) NULL,
        proposed_memory_type VARCHAR(32) NOT NULL,
        proposed_category VARCHAR(32) NOT NULL,
        content JSON NOT NULL,
        content_text TEXT NOT NULL,
        importance DECIMAL(4,3) NOT NULL,
        confidence DECIMAL(4,3) NOT NULL,
        source JSON NOT NULL,
        decision VARCHAR(24) NOT NULL DEFAULT 'pending',
        expires_at DATETIME NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_memory_candidates_owner_expiry
            (user_id, decision, expires_at),
        CONSTRAINT fk_memory_candidates_project FOREIGN KEY (project_id, user_id)
            REFERENCES memory_projects(id, user_id) ON DELETE CASCADE,
        CONSTRAINT chk_memory_candidates_importance
            CHECK (importance >= 0 AND importance <= 1),
        CONSTRAINT chk_memory_candidates_confidence
            CHECK (confidence >= 0 AND confidence <= 1)
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
)


MIGRATIONS = {1: _MIGRATION_ONE}


class MemoryDatabase:
    """Own only the Memory schema and its versioned migrations."""

    def __init__(self, config, connection_factory=None):
        self.config = config
        self._connection_factory = connection_factory or self._pymysql_connect
        name = self._setting("MEMORY_DATABASE", "memory_db")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,63}", name
        ):
            raise ValueError("MEMORY_DATABASE must be a safe SQL identifier")
        self.database_name = name
        timeout = self._setting("MEMORY_MIGRATION_LOCK_TIMEOUT_SECONDS", 30)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 300
        ):
            raise ValueError(
                "MEMORY_MIGRATION_LOCK_TIMEOUT_SECONDS must be between 1 and 300"
            )
        self.migration_lock_timeout = timeout
        digest = hashlib.sha256(name.encode("ascii")).hexdigest()[:24]
        self.migration_lock_name = f"astrarelay-memory-{digest}"

    def initialize(self) -> None:
        """Create the dedicated database and apply unapplied migrations."""

        self._create_database()
        connection = self.connect()
        lock_acquired = False
        try:
            self._acquire_migration_lock(connection)
            lock_acquired = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS memory_schema_migrations ("
                    "version INT PRIMARY KEY, "
                    "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    ") ENGINE=InnoDB CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_unicode_ci"
                )
            connection.commit()

            with connection.cursor() as cursor:
                cursor.execute("SELECT version FROM memory_schema_migrations")
                applied = {
                    self._row_version(row) for row in cursor.fetchall()
                }

            for version in sorted(MIGRATIONS):
                if version in applied:
                    continue
                try:
                    with connection.cursor() as cursor:
                        for statement in MIGRATIONS[version]:
                            cursor.execute(statement)
                        cursor.execute(
                            "INSERT INTO memory_schema_migrations (version) "
                            "VALUES (%s)",
                            (version,),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            error_in_flight = sys.exc_info()[0] is not None
            try:
                if lock_acquired:
                    self._release_migration_lock(connection)
            except Exception:
                if not error_in_flight:
                    raise
            finally:
                connection.close()

    def connect(self):
        """Return a transaction-controlled connection to `memory_db`."""

        return self._connect(database=self.database_name)

    def _create_database(self) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _acquire_migration_lock(self, connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT GET_LOCK(%s, %s) AS acquired",
                (self.migration_lock_name, self.migration_lock_timeout),
            )
            acquired = self._row_scalar(cursor.fetchone(), "acquired")
        if acquired != 1:
            raise RuntimeError("memory migration lock could not be acquired")

    def _release_migration_lock(self, connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT RELEASE_LOCK(%s) AS released",
                (self.migration_lock_name,),
            )
            released = self._row_scalar(cursor.fetchone(), "released")
        if released != 1:
            raise RuntimeError("memory migration lock could not be released")

    def _connect(self, database=None):
        options = {
            "host": self._setting("MYSQL_HOST", "localhost"),
            "port": self._setting("MYSQL_PORT", 3306),
            "user": self._setting("MYSQL_USER", "root"),
            "password": self._setting("MYSQL_PASSWORD", ""),
            "charset": "utf8mb4",
            "autocommit": False,
        }
        if database:
            options["database"] = database
        return self._connection_factory(**options)

    def _setting(self, name, default):
        if isinstance(self.config, dict):
            return self.config.get(name, default)
        return getattr(self.config, name, default)

    @staticmethod
    def _row_version(row) -> int:
        if isinstance(row, dict):
            return int(row["version"])
        return int(row[0])

    @staticmethod
    def _row_scalar(row, key):
        if row is None:
            return None
        if isinstance(row, dict):
            return row.get(key)
        return row[0]

    @staticmethod
    def _pymysql_connect(**options):
        import pymysql

        return pymysql.connect(**options)
