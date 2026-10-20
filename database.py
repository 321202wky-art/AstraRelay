"""MySQL-backed storage for group and model-private chat histories."""
import json
import re

from datetime import datetime
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

from werkzeug.security import check_password_hash, generate_password_hash


class ConversationDeletedError(RuntimeError):
    """Raised when a concurrent deletion wins a conversation write race."""


class Database:
    """Store shared messages and per-model private histories using PyMySQL."""

    _DATABASE_SETTINGS = {
        "group": ("GROUP_CHAT_DATABASE", "group_chat_db"),
        "deepseek": ("DEEPSEEK_DATABASE", "deepseek_db"),
        "kimi": ("KIMI_DATABASE", "kimi_db"),
        "zhipu": ("ZHIPU_DATABASE", "zhipu_db"),
        "qwen": ("QWEN_DATABASE", "qwen_db"),
        "bytedance": ("BYTEDANCE_DATABASE", "bytedance_db"),
    }
    _CONTEXT_MODES = frozenset({"smart", "full", "from_now", "custom"})
    _MAX_CONVERSATION_MODELS = 16

    def __init__(self, config, connection_factory=None):
        self.config = config
        self._connection_factory = connection_factory or self._pymysql_connect

    @classmethod
    def for_testing(cls, legacy_messages=(), missing_legacy_database=False):
        """Build an isolated database backed by a small in-memory connection fake."""
        store = {"_migrations": set(), "_group_migrations": set(), "_tables": set()}
        if legacy_messages:
            store["chat_db"] = [
                (index, role, content, datetime.now())
                for index, (role, content) in enumerate(legacy_messages, start=1)
            ]
            store["_tables"].add("chat_db")
        config = SimpleNamespace(
            MYSQL_HOST="localhost",
            MYSQL_PORT=3306,
            MYSQL_USER="root",
            MYSQL_PASSWORD="",
            MYSQL_DATABASE=(
                "chat_db" if legacy_messages or missing_legacy_database else "deepseek_db"
            ),
        )

        def connect(**options):
            if missing_legacy_database and options.get("database") == "chat_db":
                raise _MissingDatabaseError()
            return _MemoryConnection(store, options.get("database"))

        database = cls(
            config,
            connection_factory=connect,
        )
        database._testing_store = store
        database._testing_lock = Lock()
        return database

    def initialize(self):
        for database_name in self._database_names():
            self._initialize_database(database_name)
        self._migrate_legacy_deepseek_history()

    def database_name_for(self, store):
        """Return the configured database name for a registry store."""
        try:
            setting, default = self._DATABASE_SETTINGS[store.lower()]
        except (AttributeError, KeyError) as error:
            raise ValueError(f"Unknown database store: {store}") from error
        return self._setting(setting, default)

    def add_message(self, role, content, model="deepseek", message_type="reply", conversation_id=None):
        """Write a model message to its private history and the shared history."""
        if conversation_id is not None and hasattr(self, "_testing_store"):
            with self._testing_lock:
                self._require_testing_conversation(conversation_id)
                timestamp = datetime.now()
                stores = self._testing_store.setdefault("_conversation_messages", {})
                for database_name, stored_model, stored_type in (
                    (self.database_name_for(model), None, None),
                    (self.database_name_for("group"), model, message_type),
                ):
                    stores.setdefault((database_name, conversation_id), []).append(
                        {
                            "role": role,
                            "content": content,
                            "timestamp": str(timestamp),
                            "model": stored_model,
                            "message_type": stored_type,
                        }
                    )
            return
        private_database = self.database_name_for(model)
        if model.lower() == "group":
            raise ValueError("Messages must target a private model store")
        group_database = self.database_name_for("group")
        connection = self._connect(database=group_database)
        try:
            with connection.cursor() as cursor:
                if conversation_id is None:
                    cursor.execute(f"INSERT INTO {self._qualified_history_table(private_database)} " "(role, content) VALUES (%s, %s)", (role, content))
                    cursor.execute(f"INSERT INTO {self._qualified_history_table(group_database)} " "(role, content, model, message_type) VALUES (%s, %s, %s, %s)", (role, content, model, message_type))
                else:
                    self._lock_existing_conversation(cursor, conversation_id)
                    cursor.execute(f"INSERT INTO {self._qualified_history_table(private_database)} " "(role, content, conversation_id) VALUES (%s, %s, %s)", (role, content, conversation_id))
                    cursor.execute(f"INSERT INTO {self._qualified_history_table(group_database)} " "(role, content, model, message_type, conversation_id) VALUES (%s, %s, %s, %s, %s)", (role, content, model, message_type, conversation_id))
                    cursor.execute("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = %s", (conversation_id,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _qualified_history_table(self, database_name):
        self._validate_database_name(database_name)
        return f"`{database_name}`.`chat_history`"

    def _insert_message(self, database_name, role, content):
        connection = self._connect(database=database_name)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO chat_history (role, content) VALUES (%s, %s)",
                    (role, content),
                )
            connection.commit()
        finally:
            connection.close()

    def get_recent_messages(self, limit=8, model="deepseek", conversation_id=None):
        return list(reversed(self._fetch_messages(limit, self.database_name_for(model), conversation_id)))

    def get_all_messages(self, limit=None, model="group", conversation_id=None):
        return list(reversed(self._fetch_messages(limit, self.database_name_for(model), conversation_id)))

    def add_collaboration_message(
        self, role, content, model=None, message_type="reply", conversation_id=None
    ):
        """Append one event to the shared collaboration ledger only."""
        if conversation_id is None:
            raise ValueError("A collaboration message requires a conversation id")
        if hasattr(self, "_testing_store"):
            with self._testing_lock:
                self._require_testing_conversation(conversation_id)
                messages = self._testing_store.setdefault("_conversation_messages", {}).setdefault(
                    (self.database_name_for("group"), conversation_id), []
                )
                message_id = len(messages) + 1
                message = {
                    "id": message_id,
                    "role": role,
                    "content": content,
                    "timestamp": str(datetime.now()),
                    "model": model,
                    "message_type": message_type,
                }
                messages.append(message)
            return dict(message)

        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                self._lock_existing_conversation(cursor, conversation_id)
                cursor.execute(
                    "INSERT INTO chat_history "
                    "(role, content, model, message_type, conversation_id) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (role, content, model, message_type, conversation_id),
                )
                message_id = cursor.lastrowid
                cursor.execute(
                    "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (conversation_id,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {"id": message_id, "role": role, "content": content, "model": model}

    def get_shared_messages(self, conversation_id, limit=24):
        """Return chronological collaboration events with their immutable ledger ids."""
        if hasattr(self, "_testing_store"):
            messages = self._testing_store.get("_conversation_messages", {}).get(
                (self.database_name_for("group"), conversation_id), []
            )
            selected = messages if limit is None else messages[-limit:]
            return [dict(message) for message in selected]

        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                query = (
                    "SELECT id, role, content, timestamp, model, message_type "
                    "FROM chat_history WHERE conversation_id = %s ORDER BY id DESC"
                )
                if limit is None:
                    cursor.execute(query, (conversation_id,))
                else:
                    cursor.execute(query + " LIMIT %s", (conversation_id, limit))
                rows = cursor.fetchall()
            return list(reversed([self._as_shared_message(row) for row in rows]))
        finally:
            connection.close()

    def mark_model_context_synced(self, conversation_id, model_id, message_id):
        """Store the last global event acknowledged by one model request."""
        if hasattr(self, "_testing_store"):
            with self._testing_lock:
                self._require_testing_conversation(conversation_id)
                self._testing_store.setdefault("_model_context_sync", {})[
                    (conversation_id, model_id)
                ] = message_id
            return
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                self._lock_existing_conversation(cursor, conversation_id)
                cursor.execute(
                    "INSERT INTO conversation_model_sync "
                    "(conversation_id, model_id, last_message_id) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE last_message_id = VALUES(last_message_id)",
                    (conversation_id, model_id, message_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_model_context_sync(self, conversation_id, model_id):
        """Return the latest shared event acknowledged by a model, if any."""
        if hasattr(self, "_testing_store"):
            return self._testing_store.get("_model_context_sync", {}).get(
                (conversation_id, model_id)
            )
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT last_message_id FROM conversation_model_sync "
                    "WHERE conversation_id = %s AND model_id = %s",
                    (conversation_id, model_id),
                )
                row = cursor.fetchone()
            if row is None:
                return None
            return row["last_message_id"] if isinstance(row, dict) else row[0]
        finally:
            connection.close()

    def create_conversation(self, username, title="新对话"):
        conversation_id = uuid4().hex
        if hasattr(self, "_testing_store"):
            items = self._testing_store.setdefault("_conversations", {})
            item = {"id": conversation_id, "username": username, "title": title, "created_at": str(datetime.now()), "updated_at": str(datetime.now())}
            items[conversation_id] = item
            return dict(item)
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO conversations (id, username, title) VALUES (%s, %s, %s)", (conversation_id, username, title))
            connection.commit()
        finally:
            connection.close()
        return {"id": conversation_id, "username": username, "title": title}

    def list_conversations(self, username):
        if hasattr(self, "_testing_store"):
            return [dict(item) for item in sorted(self._testing_store.get("_conversations", {}).values(), key=lambda item: item["updated_at"], reverse=True) if item["username"] == username]
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, title, created_at, updated_at FROM conversations WHERE username = %s ORDER BY updated_at DESC", (username,))
                return [self._as_conversation(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def rename_conversation(self, conversation_id, username, title):
        if hasattr(self, "_testing_store"):
            with self._testing_lock:
                item = self._testing_store.get("_conversations", {}).get(conversation_id)
                if item is None or item["username"] != username:
                    return None
                item["title"] = title
                item["updated_at"] = str(datetime.now())
                return dict(item)
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE conversations SET title = %s, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = %s AND username = %s",
                    (title, conversation_id, username),
                )
                if cursor.rowcount != 1:
                    cursor.execute(
                        "SELECT id FROM conversations WHERE id = %s AND username = %s",
                        (conversation_id, username),
                    )
                    if cursor.fetchone() is None:
                        connection.rollback()
                        return None
            connection.commit()
            return {"id": conversation_id, "title": title}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def conversation_exists(self, conversation_id, username):
        if hasattr(self, "_testing_store"):
            item = self._testing_store.get("_conversations", {}).get(conversation_id)
            return bool(item and item["username"] == username)
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM conversations WHERE id = %s AND username = %s", (conversation_id, username))
                return cursor.fetchone() is not None
        finally:
            connection.close()

    @staticmethod
    def _lock_existing_conversation(cursor, conversation_id):
        cursor.execute(
            "SELECT id FROM conversations WHERE id = %s FOR UPDATE", (conversation_id,)
        )
        if cursor.fetchone() is None:
            raise ConversationDeletedError("Conversation no longer exists")

    def _require_testing_conversation(self, conversation_id):
        if conversation_id not in self._testing_store.get("_conversations", {}):
            raise ConversationDeletedError("Conversation no longer exists")

    def get_conversation_messages(self, conversation_id, username):
        if not self.conversation_exists(conversation_id, username):
            return []
        if hasattr(self, "_testing_store"):
            return list(self._testing_store.get("_conversation_messages", {}).get((self.database_name_for("group"), conversation_id), []))
        return self.get_all_messages(model="group", conversation_id=conversation_id)

    def delete_conversation(self, conversation_id, username):
        """Delete one owned conversation and all of its conversation-scoped state."""
        if hasattr(self, "_testing_store"):
            with self._testing_lock:
                if not self.conversation_exists(conversation_id, username):
                    return False
                self._testing_store.setdefault("_conversations", {}).pop(
                    conversation_id, None
                )
                self._testing_store.setdefault("_conversation_model_settings", {}).pop(
                    conversation_id, None
                )
                self._testing_store.setdefault("_model_context_sync", {}).pop(
                    (conversation_id, "deepseek"), None
                )
                messages = self._testing_store.setdefault("_conversation_messages", {})
                for key in [key for key in messages if key[1] == conversation_id]:
                    messages.pop(key, None)
                sync = self._testing_store.setdefault("_model_context_sync", {})
                for key in [key for key in sync if key[0] == conversation_id]:
                    sync.pop(key, None)
                return True

        group_database = self.database_name_for("group")
        connection = self._connect(database=group_database)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM conversations "
                    "WHERE id = %s AND username = %s FOR UPDATE",
                    (conversation_id, username),
                )
                if cursor.fetchone() is None:
                    connection.rollback()
                    return False
                for database_name in self._database_names():
                    cursor.execute(
                        f"DELETE FROM {self._qualified_history_table(database_name)} "
                        "WHERE conversation_id = %s",
                        (conversation_id,),
                    )
                cursor.execute(
                    "DELETE FROM conversation_model_sync WHERE conversation_id = %s",
                    (conversation_id,),
                )
                cursor.execute(
                    "DELETE FROM conversation_model_settings WHERE conversation_id = %s",
                    (conversation_id,),
                )
                cursor.execute(
                    "DELETE FROM conversations WHERE id = %s AND username = %s",
                    (conversation_id, username),
                )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_conversation_model_settings(self, conversation_id, username):
        """Return one user's selected models for a conversation, in display order."""
        if not self.conversation_exists(conversation_id, username):
            return []
        if hasattr(self, "_testing_store"):
            settings = self._testing_store.get("_conversation_model_settings", {}).get(
                conversation_id, []
            )
            return [dict(item, message_ids=list(item["message_ids"])) for item in settings]

        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT model_id, context_mode, message_ids_json, position "
                    "FROM conversation_model_settings "
                    "WHERE conversation_id = %s ORDER BY position ASC",
                    (conversation_id,),
                )
                return [self._as_model_setting(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def replace_conversation_model_settings(self, conversation_id, username, settings):
        """Atomically replace a user's selected models for one conversation."""
        normalized = self._normalize_model_settings(settings)
        if hasattr(self, "_testing_store"):
            with self._testing_lock:
                if not self.conversation_exists(conversation_id, username):
                    return []
                self._testing_store.setdefault("_conversation_model_settings", {})[
                    conversation_id
                ] = [
                    dict(item, message_ids=list(item["message_ids"]))
                    for item in normalized
                ]
                return self.get_conversation_model_settings(conversation_id, username)

        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM conversations WHERE id = %s AND username = %s FOR UPDATE",
                    (conversation_id, username),
                )
                if cursor.fetchone() is None:
                    connection.rollback()
                    return []
                cursor.execute(
                    "DELETE FROM conversation_model_settings WHERE conversation_id = %s",
                    (conversation_id,),
                )
                for item in normalized:
                    cursor.execute(
                        "INSERT INTO conversation_model_settings "
                        "(conversation_id, model_id, context_mode, message_ids_json, position) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            conversation_id,
                            item["model_id"],
                            item["context_mode"],
                            json.dumps(item["message_ids"], ensure_ascii=False),
                            item["position"],
                        ),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_conversation_model_settings(conversation_id, username)

    def list_model_presets(self, username):
        """Return saved model combinations owned by a user."""
        if hasattr(self, "_testing_store"):
            presets = self._testing_store.get("_model_presets", {}).get(username, [])
            return [self._copy_model_preset(preset) for preset in presets]

        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name, created_at FROM model_presets "
                    "WHERE username = %s ORDER BY created_at DESC, id DESC",
                    (username,),
                )
                presets = [self._as_model_preset(row) for row in cursor.fetchall()]
                if not presets:
                    return []
                cursor.execute(
                    "SELECT preset_id, model_id, context_mode, message_ids_json, position "
                    "FROM model_preset_items WHERE preset_id IN ("
                    + ", ".join(["%s"] * len(presets))
                    + ") ORDER BY preset_id, position ASC",
                    tuple(preset["id"] for preset in presets),
                )
                items_by_preset = {preset["id"]: [] for preset in presets}
                for row in cursor.fetchall():
                    if isinstance(row, dict):
                        preset_id = row["preset_id"]
                        setting = self._as_model_setting(row)
                    else:
                        preset_id, *setting_row = row
                        setting = self._as_model_setting(tuple(setting_row))
                    items_by_preset[preset_id].append(setting)
                for preset in presets:
                    preset["items"] = items_by_preset[preset["id"]]
                return presets
        finally:
            connection.close()

    def create_model_preset(self, username, name, settings):
        """Persist a named model combination for later reuse."""
        normalized_name = self._normalize_preset_name(name)
        normalized = self._normalize_model_settings(settings)
        if hasattr(self, "_testing_store"):
            presets = self._testing_store.setdefault("_model_presets", {}).setdefault(
                username, []
            )
            if any(item["name"] == normalized_name for item in presets):
                raise ValueError("Preset name already exists")
            preset = {
                "id": self._testing_store.setdefault("_next_model_preset_id", 1),
                "name": normalized_name,
                "created_at": str(datetime.now()),
                "items": [dict(item, message_ids=list(item["message_ids"])) for item in normalized],
            }
            self._testing_store["_next_model_preset_id"] += 1
            presets.insert(0, preset)
            return self._copy_model_preset(preset)

        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO model_presets (username, name) VALUES (%s, %s)",
                    (username, normalized_name),
                )
                preset_id = cursor.lastrowid
                for item in normalized:
                    cursor.execute(
                        "INSERT INTO model_preset_items "
                        "(preset_id, model_id, context_mode, message_ids_json, position) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            preset_id,
                            item["model_id"],
                            item["context_mode"],
                            json.dumps(item["message_ids"], ensure_ascii=False),
                            item["position"],
                        ),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return next(
            preset
            for preset in self.list_model_presets(username)
            if preset["id"] == preset_id
        )

    def delete_model_preset(self, preset_id, username):
        """Delete a preset only when it belongs to the active user."""
        if hasattr(self, "_testing_store"):
            presets = self._testing_store.get("_model_presets", {}).get(username, [])
            for index, preset in enumerate(presets):
                if preset["id"] == preset_id:
                    del presets[index]
                    return True
            return False

        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM model_presets WHERE id = %s AND username = %s",
                    (preset_id, username),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
            return deleted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_user(self, username, password):
        """Create or update a user without storing its plaintext password."""
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash)",
                    (username, generate_password_hash(password)),
                )
            connection.commit()
        finally:
            connection.close()

    def create_user_if_missing(self, username, password):
        """Create a user without replacing an existing password hash."""
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT IGNORE INTO users (username, password_hash) VALUES (%s, %s)",
                    (username, generate_password_hash(password)),
                )
            connection.commit()
        finally:
            connection.close()

    def verify_user(self, username, password):
        """Return whether a username exists and its password hash matches."""
        connection = self._connect(database=self.database_name_for("group"))
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT password_hash FROM users WHERE username = %s", (username,)
                )
                row = cursor.fetchone()
            password_hash = row["password_hash"] if isinstance(row, dict) else row[0] if row else None
            return bool(password_hash and check_password_hash(password_hash, password))
        finally:
            connection.close()

    def _fetch_messages(self, limit, database_name, conversation_id=None):
        if conversation_id is not None and hasattr(self, "_testing_store"):
            rows = self._testing_store.get("_conversation_messages", {}).get((database_name, conversation_id), [])
            return list(rows[-limit:] if limit is not None else rows)
        connection = self._connect(database=database_name)
        try:
            with connection.cursor() as cursor:
                if database_name == self.database_name_for("group"):
                    query = "SELECT role, content, timestamp, model, message_type FROM chat_history"
                else:
                    query = "SELECT role, content, timestamp FROM chat_history"
                if conversation_id is not None:
                    query += " WHERE conversation_id = %s"
                    cursor.execute(query + " ORDER BY id DESC" if limit is None else query + " ORDER BY id DESC LIMIT %s", (conversation_id,) if limit is None else (conversation_id, limit))
                elif limit is None:
                    cursor.execute(query + " ORDER BY id DESC")
                else:
                    cursor.execute(query + " ORDER BY id DESC LIMIT %s", (limit,))
                rows = cursor.fetchall()
            return [self._as_message(row) for row in rows]
        finally:
            connection.close()

    def _database_names(self):
        return [self.database_name_for(store) for store in self._DATABASE_SETTINGS]

    def _initialize_database(self, database_name):
        self._validate_database_name(database_name)

        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            connection.commit()
        finally:
            connection.close()

        connection = self._connect(database=database_name)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS chat_history ("
                    "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
                    "role VARCHAR(32) NOT NULL, "
                    "content TEXT NOT NULL, "
                    "timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    ") ENGINE=InnoDB CHARACTER SET utf8mb4"
                )
                cursor.execute("SHOW COLUMNS FROM chat_history LIKE 'conversation_id'")
                if not cursor.fetchall():
                    cursor.execute("ALTER TABLE chat_history ADD COLUMN conversation_id CHAR(32) NULL")
                if database_name == self.database_name_for("group"):
                    self._initialize_group_tables(cursor)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _initialize_group_tables(cursor):
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            "id CHAR(32) PRIMARY KEY, username VARCHAR(255) NOT NULL, "
            "title VARCHAR(255) NOT NULL, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
            "INDEX idx_conversations_username_updated (username, updated_at)"
            ") ENGINE=InnoDB CHARACTER SET utf8mb4"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
            "username VARCHAR(255) NOT NULL UNIQUE, "
            "password_hash VARCHAR(255) NOT NULL, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB CHARACTER SET utf8mb4"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS conversation_model_sync ("
            "conversation_id CHAR(32) NOT NULL, "
            "model_id VARCHAR(255) NOT NULL, "
            "last_message_id BIGINT NOT NULL, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP "
            "ON UPDATE CURRENT_TIMESTAMP, "
            "PRIMARY KEY (conversation_id, model_id)"
            ") ENGINE=InnoDB CHARACTER SET utf8mb4"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS conversation_model_settings ("
            "conversation_id CHAR(32) NOT NULL, "
            "model_id VARCHAR(255) NOT NULL, "
            "context_mode VARCHAR(16) NOT NULL, "
            "message_ids_json JSON NOT NULL, "
            "position SMALLINT UNSIGNED NOT NULL, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP "
            "ON UPDATE CURRENT_TIMESTAMP, "
            "PRIMARY KEY (conversation_id, model_id), "
            "INDEX idx_conversation_model_settings_order (conversation_id, position)"
            ") ENGINE=InnoDB CHARACTER SET utf8mb4"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS model_presets ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
            "username VARCHAR(255) NOT NULL, "
            "name VARCHAR(80) NOT NULL, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "UNIQUE KEY uq_model_presets_username_name (username, name), "
            "INDEX idx_model_presets_username_created (username, created_at)"
            ") ENGINE=InnoDB CHARACTER SET utf8mb4"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS model_preset_items ("
            "preset_id BIGINT NOT NULL, "
            "model_id VARCHAR(255) NOT NULL, "
            "context_mode VARCHAR(16) NOT NULL, "
            "message_ids_json JSON NOT NULL, "
            "position SMALLINT UNSIGNED NOT NULL, "
            "PRIMARY KEY (preset_id, model_id), "
            "INDEX idx_model_preset_items_order (preset_id, position), "
            "CONSTRAINT fk_model_preset_items_preset "
            "FOREIGN KEY (preset_id) REFERENCES model_presets(id) ON DELETE CASCADE"
            ") ENGINE=InnoDB CHARACTER SET utf8mb4"
        )
        for column, definition in (
            ("model", "VARCHAR(255) NULL"),
            ("message_type", "VARCHAR(16) NOT NULL DEFAULT 'reply'"),
            ("conversation_id", "CHAR(32) NULL"),
        ):
            cursor.execute(f"SHOW COLUMNS FROM chat_history LIKE '{column}'")
            existing_columns = cursor.fetchall()
            if not existing_columns:
                cursor.execute(f"ALTER TABLE chat_history ADD COLUMN {column} {definition}")
            elif column == "model":
                existing = existing_columns[0]
                existing_type = (
                    existing.get("Type")
                    if isinstance(existing, dict)
                    else existing[1] if len(existing) > 1 else None
                )
                if isinstance(existing_type, str) and existing_type.lower() != "varchar(255)":
                    cursor.execute(
                        "ALTER TABLE chat_history "
                        "MODIFY COLUMN model VARCHAR(255) NULL"
                    )

    @staticmethod
    def _as_conversation(row):
        if isinstance(row, dict):
            return {"id": row["id"], "title": row["title"], "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"])}
        conversation_id, title, created_at, updated_at = row
        return {"id": conversation_id, "title": title, "created_at": str(created_at), "updated_at": str(updated_at)}

    def _migrate_legacy_deepseek_history(self):
        legacy_database = self._setting("MYSQL_DATABASE", "chat_db")
        deepseek_database = self.database_name_for("deepseek")
        group_database = self.database_name_for("group")
        if legacy_database == deepseek_database:
            return
        self._validate_database_name(legacy_database)

        try:
            source = self._connect(database=legacy_database)
        except Exception as error:
            if self._is_missing_database_error(error):
                return
            raise
        try:
            with source.cursor() as cursor:
                cursor.execute("SHOW TABLES LIKE 'chat_history'")
                if not cursor.fetchall():
                    return
                cursor.execute(
                    "SELECT id, role, content, timestamp FROM chat_history ORDER BY id"
                )
                rows = cursor.fetchall()
        finally:
            source.close()

        target = self._connect(database=deepseek_database)
        try:
            with target.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS legacy_chat_db_migrations ("
                    "source_database VARCHAR(64) NOT NULL, "
                    "source_id BIGINT NOT NULL, "
                    "PRIMARY KEY (source_database, source_id)"
                    ") CHARACTER SET utf8mb4"
                )
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS legacy_group_chat_db_migrations ("
                    "source_database VARCHAR(64) NOT NULL, "
                    "source_id BIGINT NOT NULL, "
                    "PRIMARY KEY (source_database, source_id)"
                    ") CHARACTER SET utf8mb4"
                )
                for source_id, role, content, timestamp in rows:
                    cursor.execute(
                        "INSERT IGNORE INTO legacy_chat_db_migrations "
                        "(source_database, source_id) VALUES (%s, %s)",
                        (legacy_database, source_id),
                    )
                    if cursor.rowcount:
                        cursor.execute(
                            "INSERT INTO chat_history (role, content, timestamp) "
                            "VALUES (%s, %s, %s)",
                            (role, content, timestamp),
                        )
                    cursor.execute(
                        "INSERT IGNORE INTO legacy_group_chat_db_migrations "
                        "(source_database, source_id) VALUES (%s, %s)",
                        (legacy_database, source_id),
                    )
                    if cursor.rowcount:
                        cursor.execute(
                            f"INSERT INTO {self._qualified_history_table(group_database)} "
                            "(role, content, timestamp, model, message_type) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (role, content, timestamp, "deepseek", "reply"),
                        )
            target.commit()
        finally:
            target.close()

    @staticmethod
    def _validate_database_name(database_name):
        if not database_name.replace("_", "").isalnum():
            raise ValueError("Database names must contain only letters, numbers, and underscores")

    @staticmethod
    def _is_missing_database_error(error):
        return bool(error.args) and error.args[0] == 1049

    def _connect(self, database=None):
        options = {
            "host": self._setting("MYSQL_HOST", "localhost"),
            "port": int(self._setting("MYSQL_PORT", 3306)),
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
    def _pymysql_connect(**options):
        import pymysql

        return pymysql.connect(**options)

    @staticmethod
    def _as_message(row):
        if isinstance(row, dict):
            role, content, timestamp = row["role"], row["content"], row["timestamp"]
            model, message_type = row.get("model"), row.get("message_type")
        else:
            role, content, timestamp, *metadata = row
            model, message_type = (metadata + [None, None])[:2]
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat(sep=" ")
        message = {"role": role, "content": content, "timestamp": str(timestamp)}
        if model is not None or message_type is not None:
            message.update(model=model, message_type=message_type)
        return message

    @staticmethod
    def _as_shared_message(row):
        if isinstance(row, dict):
            message_id = row["id"]
            message = Database._as_message(row)
        else:
            message_id, *message_row = row
            message = Database._as_message(tuple(message_row))
        return {"id": message_id, **message}

    @classmethod
    def _normalize_model_settings(cls, settings):
        if not isinstance(settings, list):
            raise ValueError("Model settings must be a list")
        if len(settings) > cls._MAX_CONVERSATION_MODELS:
            raise ValueError(f"At most {cls._MAX_CONVERSATION_MODELS} models can be selected")
        normalized, model_ids = [], set()
        for position, setting in enumerate(settings):
            if not isinstance(setting, dict):
                raise ValueError("Each model setting must be an object")
            model_id = setting.get("model_id")
            if not isinstance(model_id, str) or not (model_id := model_id.strip()) or len(model_id) > 255:
                raise ValueError("A valid model_id is required")
            if model_id in model_ids:
                raise ValueError("Duplicate model_id values are not allowed")
            context_mode = setting.get("context_mode", "smart")
            if context_mode not in cls._CONTEXT_MODES:
                raise ValueError("Unsupported context_mode")
            message_ids = setting.get("message_ids", [])
            if not isinstance(message_ids, list) or any(
                isinstance(message_id, bool)
                or not isinstance(message_id, int)
                or message_id <= 0
                for message_id in message_ids
            ):
                raise ValueError("message_ids must be a list of positive integers")
            model_ids.add(model_id)
            normalized.append(
                {
                    "model_id": model_id,
                    "context_mode": context_mode,
                    "message_ids": list(message_ids),
                    "position": position,
                }
            )
        return normalized

    @staticmethod
    def _normalize_preset_name(name):
        if not isinstance(name, str) or not (name := name.strip()) or len(name) > 80:
            raise ValueError("Preset name must contain between 1 and 80 characters")
        return name

    @classmethod
    def _as_model_setting(cls, row):
        if isinstance(row, dict):
            model_id = row["model_id"]
            context_mode = row["context_mode"]
            message_ids_json = row["message_ids_json"]
            position = row["position"]
        else:
            model_id, context_mode, message_ids_json, position = row
        if isinstance(message_ids_json, str):
            message_ids = json.loads(message_ids_json)
        else:
            message_ids = list(message_ids_json)
        return {
            "model_id": model_id,
            "context_mode": context_mode,
            "message_ids": message_ids,
            "position": position,
        }

    @staticmethod
    def _as_model_preset(row):
        if isinstance(row, dict):
            preset_id, name, created_at = row["id"], row["name"], row["created_at"]
        else:
            preset_id, name, created_at = row
        return {"id": preset_id, "name": name, "created_at": str(created_at), "items": []}

    @staticmethod
    def _copy_model_preset(preset):
        return {
            **preset,
            "items": [dict(item, message_ids=list(item["message_ids"])) for item in preset["items"]],
        }


class _MemoryConnection:
    def __init__(self, store, database):
        self.store = store
        self.database = database
        self._pending_messages = []
        self._pending_users = []

    def cursor(self):
        return _MemoryCursor(self)

    def commit(self):
        for database, role, content, timestamp, model, message_type in self._pending_messages:
            messages = self.store.setdefault(database, [])
            message = (len(messages) + 1, role, content, timestamp, model, message_type)
            messages.append(message)
        users = self.store.setdefault("_users", {})
        for username, password_hash, only_if_missing in self._pending_users:
            if not only_if_missing or username not in users:
                users[username] = password_hash
        self._pending_messages.clear()
        self._pending_users.clear()

    def rollback(self):
        self._pending_messages.clear()
        self._pending_users.clear()

    def close(self):
        pass


class _MissingDatabaseError(Exception):
    def __init__(self):
        super().__init__(1049, "Unknown database 'chat_db'")


class _MemoryCursor:
    def __init__(self, connection):
        self.connection = connection
        self.store = connection.store
        self.database = connection.database
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        statement = " ".join(sql.split()).upper()
        if statement.startswith("CREATE TABLE IF NOT EXISTS CHAT_HISTORY"):
            self.store["_tables"].add(self.database)
        elif statement.startswith("CREATE TABLE IF NOT EXISTS USERS"):
            self.store["_users"] = self.store.get("_users", {})
        elif statement.startswith("SHOW COLUMNS FROM CHAT_HISTORY LIKE"):
            column = sql.split("'")[1]
            columns = self.store.setdefault("_group_columns", set())
            self.rows = [(column,)] if column in columns else []
        elif statement.startswith("ALTER TABLE CHAT_HISTORY ADD COLUMN"):
            self.store.setdefault("_group_columns", set()).add(sql.split()[5])
        elif statement.startswith("SHOW TABLES LIKE 'CHAT_HISTORY'"):
            self.rows = [("chat_history",)] if self.database in self.store["_tables"] else []
        elif statement.startswith("INSERT IGNORE INTO LEGACY_CHAT_DB_MIGRATIONS"):
            migration = tuple(params)
            self.rowcount = 0 if migration in self.store["_migrations"] else 1
            self.store["_migrations"].add(migration)
        elif statement.startswith("INSERT IGNORE INTO LEGACY_GROUP_CHAT_DB_MIGRATIONS"):
            migration = tuple(params)
            self.rowcount = 0 if migration in self.store["_group_migrations"] else 1
            self.store["_group_migrations"].add(migration)
        elif statement.startswith("INSERT IGNORE INTO USERS"):
            username, password_hash = params
            self.connection._pending_users.append((username, password_hash, True))
        elif statement.startswith("INSERT INTO USERS"):
            username, password_hash = params
            self.connection._pending_users.append((username, password_hash, False))
        elif statement.startswith("SELECT PASSWORD_HASH FROM USERS"):
            password_hash = self.store.get("_users", {}).get(params[0])
            self.rows = [] if password_hash is None else [(password_hash,)]
        elif statement.startswith("INSERT INTO") and "CHAT_HISTORY" in statement:
            if len(params) == 2:
                role, content = params
                timestamp = datetime.now()
                model = message_type = None
            elif len(params) == 4:
                role, content, model, message_type = params
                timestamp = datetime.now()
            elif len(params) == 5:
                role, content, timestamp, model, message_type = params
            else:
                role, content, timestamp = params
                model = message_type = None
            match = re.search(r"INSERT INTO `([^`]+)`\.`chat_history`", sql, re.IGNORECASE)
            database = match.group(1) if match else self.database
            self.connection._pending_messages.append(
                (database, role, content, timestamp, model, message_type)
            )
        elif statement.startswith("SELECT ID, ROLE, CONTENT, TIMESTAMP"):
            self.rows = list(self.store.get(self.database, []))
        elif statement.startswith("SELECT ROLE, CONTENT, TIMESTAMP"):
            rows = list(reversed(self.store.get(self.database, [])))
            rows = [row[1:] for row in rows]
            self.rows = rows if params is None else rows[: params[0]]

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None
