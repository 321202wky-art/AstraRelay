from datetime import datetime, timedelta
from threading import Event, Thread

import pytest

from user_database import UserDatabase


class _EmailCodeConnection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return _EmailCodeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _EmailCodeCursor:
    lastrowid = 7

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, statement, params=None):
        self.connection.statements.append((statement, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return (1,)


def test_email_account_requires_verification_before_login_and_uses_unique_normalized_email():
    database = UserDatabase.for_testing()
    database.initialize()

    account = database.prepare_email_registration(" Alice@Example.COM ", "123456789")

    assert account["email"] == "alice@example.com"
    assert database.verify_email_password("alice@example.com", "123456789") is None
    database.mark_email_verified("alice@example.com")
    identity = database.verify_email_password("ALICE@example.com", "123456789")
    assert identity["email"] == "alice@example.com"
    with pytest.raises(ValueError, match="邮箱已注册"):
        database.prepare_email_registration("alice@example.com", "987654321")


def test_unverified_registration_can_be_retried_but_verified_email_cannot():
    database = UserDatabase.for_testing()
    database.initialize()

    database.prepare_email_registration("alice@example.com", "123456789")
    database.prepare_email_registration("alice@example.com", "987654321")
    database.mark_email_verified("alice@example.com")

    assert database.verify_email_password("alice@example.com", "987654321") is not None
    with pytest.raises(ValueError, match="邮箱已注册"):
        database.prepare_email_registration("alice@example.com", "123456789")


def test_verified_phone_binding_is_unique_and_does_not_rebind_account():
    database = UserDatabase.for_testing()
    database.initialize()
    for email in ("alice@example.com", "bob@example.com"):
        database.prepare_email_registration(email, "123456789")
        database.mark_email_verified(email)

    assert database.get_phone_binding_state("alice@example.com", "13800138000") == "ready"
    assert database.bind_verified_phone("alice@example.com", "13800138000") == "bound"
    assert database.bind_verified_phone("alice@example.com", "13900139000") == "account_already_bound"
    assert database.get_phone_binding_state("bob@example.com", "13800138000") == "phone_already_bound"
    assert database.bind_verified_phone("bob@example.com", "13800138000") == "phone_already_bound"


def test_phone_binding_challenge_is_account_scoped_and_enforces_server_side_limits():
    database = UserDatabase.for_testing()
    database.initialize()
    database.prepare_email_registration("alice@example.com", "123456789")
    database.mark_email_verified("alice@example.com")
    now = datetime(2026, 7, 22, 12, 0, 0)

    assert database.start_phone_binding_challenge(
        "alice@example.com", "13800138000", now
    ) == "ready"
    assert database.get_phone_binding_challenge("alice@example.com", now) == {
        "phone": "13800138000", "sent_at": now, "failed_attempts": 0
    }
    assert database.start_phone_binding_challenge(
        "alice@example.com", "13800138000", now + timedelta(seconds=1)
    ) == "cooldown"
    for _ in range(5):
        assert database.claim_phone_binding_verification("alice@example.com", now) == {
            "phone": "13800138000", "sent_at": now
        }
    assert database.get_phone_binding_challenge("alice@example.com", now)["failed_attempts"] == 5
    assert database.consume_verified_phone_binding(
        "alice@example.com", "13800138000", now, now
    ) == "bound"
    assert database.get_phone_binding_challenge("alice@example.com", now) is None


def test_in_memory_phone_binding_operations_share_the_store_lock():
    database = UserDatabase.for_testing()
    database.initialize()
    database.prepare_email_registration("alice@example.com", "123456789")
    database.mark_email_verified("alice@example.com")
    lock = database._testing_store["phone_binding_lock"]

    assert database._connect(database="user_db").phone_binding_lock is lock

    def assert_operation_waits_for_lock(operation, expected):
        started = Event()
        finished = Event()
        result = {}

        def run():
            started.set()
            result["value"] = operation()
            finished.set()

        with lock:
            worker = Thread(target=run)
            worker.start()
            assert started.wait(timeout=1)
            assert not finished.wait(timeout=0.1)
        worker.join(timeout=1)

        assert not worker.is_alive()
        assert result["value"] == expected

    assert_operation_waits_for_lock(
        lambda: database.get_phone_binding_state("alice@example.com", "13800138000"),
        "ready",
    )
    assert_operation_waits_for_lock(
        lambda: database.bind_verified_phone("alice@example.com", "13800138000"),
        "bound",
    )


def test_verified_identity_records_include_phone_number():
    database = UserDatabase.for_testing()
    database.initialize()
    database.prepare_email_registration("alice@example.com", "123456789")
    database.mark_email_verified("alice@example.com")

    assert database.bind_verified_phone("alice@example.com", "13800138000") == "bound"
    assert database.find_verified_user_by_email("alice@example.com")["phone_number"] == "13800138000"
    assert database.get_session_identity("alice@example.com")["phone_number"] == "13800138000"


def test_verified_identity_records_treat_missing_legacy_phone_number_as_unbound():
    database = UserDatabase.for_testing()
    database.initialize()
    database.prepare_email_registration("alice@example.com", "123456789")
    database.mark_email_verified("alice@example.com")
    database._testing_account_by_email("alice@example.com").pop("phone_number")

    assert database.find_verified_user_by_email("alice@example.com")["phone_number"] is None
    assert database.get_session_identity("alice@example.com")["phone_number"] is None


def test_password_reset_increments_session_version_and_invalidates_prior_identity():
    database = UserDatabase.for_testing()
    database.initialize()
    database.prepare_email_registration("alice@example.com", "123456789")
    database.mark_email_verified("alice@example.com")

    before = database.get_session_identity("alice@example.com")

    assert database.reset_password_by_email("alice@example.com", "987654321") is True
    after = database.get_session_identity("alice@example.com")
    assert after["session_version"] == before["session_version"] + 1
    assert database.verify_email_password("alice@example.com", "123456789") is None
    assert database.verify_email_password("alice@example.com", "987654321") is not None


def test_administrator_authentication_uses_the_password_and_identity_from_one_snapshot():
    database = UserDatabase.for_testing()
    database.initialize()
    account = database.prepare_email_registration("admin@example.com", "123456789")
    database.mark_email_verified("admin@example.com")
    assert database.set_role(account["username"], "admin") is True

    assert database.reset_password_by_email("admin@example.com", "987654321") is True

    assert database.authenticate_administrator(account["username"], "123456789") is None
    identity = database.authenticate_administrator(account["username"], "987654321")
    assert identity == {
        "username": account["username"],
        "email": "admin@example.com",
        "session_version": 1,
    }


def test_administrator_password_update_enforces_policy_and_increments_session_version():
    database = UserDatabase.for_testing()
    database.initialize()
    account = database.prepare_email_registration("alice@example.com", "123456789")
    before = database.mark_email_verified("alice@example.com")

    for invalid_password in ("12345678", "1234567890123"):
        with pytest.raises(ValueError, match="密码长度"):
            database.reset_password(account["username"], invalid_password)

    unchanged = database.get_session_identity("alice@example.com")
    assert unchanged["session_version"] == before["session_version"]
    assert database.verify_email_password("alice@example.com", "123456789") is not None

    assert database.reset_password(account["username"], "new-pass9") is True

    changed = database.get_session_identity("alice@example.com")
    assert changed["session_version"] == before["session_version"] + 1
    assert database.verify_email_password("alice@example.com", "123456789") is None
    assert database.verify_email_password("alice@example.com", "new-pass9") is not None


def test_email_code_can_only_be_consumed_once_after_a_constant_time_hash_match():
    database = UserDatabase.for_testing()
    database.initialize()
    now = datetime(2026, 7, 19, 12, 0, 0)
    code_hash = "a" * 64

    database.store_email_code("alice@example.com", "register", code_hash, now, now + timedelta(minutes=10))

    assert database.consume_email_code("alice@example.com", "register", "b" * 64, now) is False
    assert database.consume_email_code("alice@example.com", "register", code_hash, now) is True
    assert database.consume_email_code("alice@example.com", "register", code_hash, now) is False


def test_cancelled_email_code_allows_an_immediate_retry_without_leaving_a_valid_code():
    database = UserDatabase.for_testing()
    database.initialize()
    now = datetime(2026, 7, 19, 12, 0, 0)

    code_id = database.store_email_code("alice@example.com", "register", "a" * 64, now, now + timedelta(minutes=10))
    database.cancel_email_code(code_id)

    replacement = database.store_email_code("alice@example.com", "register", "b" * 64, now, now + timedelta(minutes=10))
    assert database.consume_email_code("alice@example.com", "register", "a" * 64, now) is False
    assert database.consume_email_code("alice@example.com", "register", "b" * 64, now) is True


def test_store_email_code_serializes_first_send_with_a_named_advisory_lock():
    connection = _EmailCodeConnection()
    database = UserDatabase({}, connection_factory=lambda **options: connection)
    now = datetime(2026, 7, 19, 12, 0, 0)

    assert database.store_email_code("alice@example.com", "register", "a" * 64, now, now + timedelta(minutes=10)) == 7

    statements = [statement for statement, _ in connection.statements]
    assert statements[0].startswith("SELECT GET_LOCK")
    assert any(statement.startswith("SELECT sent_at FROM email_verification_codes") for statement in statements)
    assert statements[-1].startswith("SELECT RELEASE_LOCK")


def test_user_database_creates_and_verifies_an_account():
    users = UserDatabase.for_testing()
    users.initialize()

    assert users.create_user("new-user", "safe-password") is True
    assert users.verify_user("new-user", "safe-password") is True
    assert users.verify_user("new-user", "wrong-password") is False


def test_user_database_rejects_duplicate_username_without_replacing_password():
    users = UserDatabase.for_testing()
    users.initialize()

    assert users.create_user("new-user", "first-password") is True
    assert users.create_user("new-user", "second-password") is False
    assert users.verify_user("new-user", "first-password") is True


def test_bootstrap_user_is_created_only_when_missing():
    users = UserDatabase.for_testing()
    users.initialize()
    users.create_user("admin", "existing-password")

    users.create_user_if_missing("admin", "replacement-password")

    assert users.verify_user("admin", "existing-password") is True
    assert users.verify_user("admin", "replacement-password") is False


def test_user_database_lists_roles_and_enforces_active_admin_verification():
    users = UserDatabase.for_testing()
    users.initialize()
    assert users.create_user("member", "correct horse") is True
    users.ensure_admin("admin", "long-bootstrap-password")

    assert users.verify_user("member", "correct horse") is True
    assert users.verify_user("member", "correct horse", require_admin=True) is False
    assert users.verify_user("admin", "long-bootstrap-password", require_admin=True) is True
    assert users.set_active("member", False) is True
    assert users.verify_user("member", "correct horse") is False
    assert users.list_users() == [
        {"username": "admin", "role": "admin", "is_active": True, "created_at": None, "access_tier": "admin"},
        {"username": "member", "role": "user", "is_active": False, "created_at": None, "access_tier": None},
    ]


def test_user_database_can_change_role_and_reset_password_but_rejects_unknown_role():
    users = UserDatabase.for_testing()
    users.initialize()
    users.create_user("member", "old-password")

    assert users.set_role("member", "admin") is True
    assert users.reset_password("member", "new-password") is True
    assert users.verify_user("member", "old-password") is False
    assert users.verify_user("member", "new-password", require_admin=True) is True
    with pytest.raises(ValueError, match="role"):
        users.set_role("member", "owner")


def test_initialize_creates_admin_principals_with_restricted_default():
    connections = []

    def connect(**options):
        connection = _RecordingConnection()
        connections.append(connection)
        return connection

    UserDatabase({}, connection_factory=connect).initialize()

    statements = connections[1].statements
    assert any("CREATE TABLE IF NOT EXISTS admin_principals" in item for item in statements)
    assert any("owner_singleton" in item and "GENERATED ALWAYS" in item for item in statements)
    assert any("uq_admin_principals_single_owner" in item for item in statements)
    assert any(
        "INSERT INTO admin_principals" in item and "SELECT id, 'admin'" in item
        for item in statements
    )


def test_ensure_owner_uses_an_immutable_account_assignment_and_preserves_password():
    users = UserDatabase.for_testing()
    users.initialize()
    users.create_user("founder", "first-pass")
    account_id = users._testing_store["users"]["founder"]["id"]

    users.ensure_owner("founder", "replacement")

    assert users.verify_user("founder", "first-pass", require_admin=True) is True
    assert users.verify_user("founder", "replacement") is False
    assert users.get_admin_principal("founder") == {
        "username": "founder",
        "access_tier": "owner",
        "policy_version": 1,
    }
    assert users._testing_store["users"]["founder"]["id"] == account_id


def test_new_administrator_is_restricted_and_final_owner_is_protected():
    users = UserDatabase.for_testing()
    users.initialize()
    users.ensure_owner("founder", "owner-pass")
    users.create_user("operator", "operator-pass")

    assert users.set_role("operator", "admin") is True
    assert users.get_admin_principal("operator")["access_tier"] == "admin"
    with pytest.raises(ValueError, match="final owner"):
        users.set_active("founder", False)
    with pytest.raises(ValueError, match="final owner"):
        users.set_role("founder", "user")


def test_owner_assignment_cannot_move_to_another_account_or_promote_it():
    users = UserDatabase.for_testing()
    users.initialize()
    users.ensure_owner("founder", "owner-pass")
    users.create_user("other", "other-pass")

    with pytest.raises(ValueError, match="already assigned"):
        users.ensure_owner("other", "replacement")

    assert users.get_admin_principal("founder")["access_tier"] == "owner"
    assert users.get_admin_principal("other") is None
    assert users.verify_user("other", "other-pass", require_admin=True) is False


def test_initialize_normalizes_legacy_invalid_roles_to_user():
    users = UserDatabase.for_testing()
    users.initialize()
    assert users.create_user("legacy", "safe-password") is True
    users._testing_store["users"]["legacy"]["role"] = "owner"

    users.initialize()

    assert users.list_users()[0]["role"] == "user"


def test_initialize_constrains_persisted_roles_after_normalizing_legacy_values():
    connections = []

    def connect(**options):
        connection = _RecordingConnection()
        connections.append(connection)
        return connection

    users = UserDatabase({}, connection_factory=connect)
    users.initialize()

    schema_statements = connections[1].statements
    assert any("role NOT IN ('admin', 'user')" in statement for statement in schema_statements)
    assert any("MODIFY COLUMN role ENUM('admin', 'user')" in statement for statement in schema_statements)


def test_initialize_migrates_registration_attempt_binding_fields():
    connections = []

    def connect(**options):
        connection = _RecordingConnection()
        connections.append(connection)
        return connection

    UserDatabase({}, connection_factory=connect).initialize()

    schema_statements = connections[1].statements
    assert any("ADD COLUMN attempt_id" in statement for statement in schema_statements)
    assert any("ADD COLUMN pending_password_hash" in statement for statement in schema_statements)


def test_initialize_uses_mysql_compatible_role_add_column_statement():
    unsupported_statement = (
        "ALTER TABLE user_accounts "
        "ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'user'"
    )
    connections = []

    def connect(**options):
        connection = _MigrationFailureConnection(
            {unsupported_statement: _MySQLDatabaseError(1064, "You have an error in your SQL syntax")}
        )
        connections.append(connection)
        return connection

    users = UserDatabase({}, connection_factory=connect)

    users.initialize()

    assert (
        "ALTER TABLE user_accounts "
        "ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'"
    ) in connections[1].statements


def test_initialize_treats_duplicate_role_column_as_an_existing_migration():
    add_role_column = (
        "ALTER TABLE user_accounts "
        "ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'"
    )
    connections = []

    def connect(**options):
        connection = _MigrationFailureConnection(
            {add_role_column: _MySQLDatabaseError(1060, "Duplicate column name 'role'")}
        )
        connections.append(connection)
        return connection

    UserDatabase({}, connection_factory=connect).initialize()

    migration_connection = connections[1]
    assert migration_connection.committed is True
    assert migration_connection.rolled_back is False
    assert migration_connection.closed is True
    assert migration_connection.triggered_failures == [add_role_column]
    assert "UPDATE user_accounts SET role = 'user' " "WHERE role IS NULL OR role NOT IN ('admin', 'user')" in migration_connection.statements
    assert (
        "ALTER TABLE user_accounts "
        "MODIFY COLUMN role ENUM('admin', 'user') NOT NULL DEFAULT 'user'"
    ) in migration_connection.statements


def test_initialize_reraises_add_role_column_syntax_errors_and_rolls_back():
    add_role_column = (
        "ALTER TABLE user_accounts "
        "ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'"
    )
    syntax_error = _MySQLDatabaseError(1064, "You have an error in your SQL syntax")
    connections = []

    def connect(**options):
        connection = _MigrationFailureConnection({add_role_column: syntax_error})
        connections.append(connection)
        return connection

    with pytest.raises(_MySQLDatabaseError) as raised:
        UserDatabase({}, connection_factory=connect).initialize()

    migration_connection = connections[1]
    assert raised.value is syntax_error
    assert migration_connection.rolled_back is True
    assert migration_connection.closed is True


def test_initialize_does_not_mask_backfill_error_after_duplicate_role_column():
    add_role_column = (
        "ALTER TABLE user_accounts "
        "ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'"
    )
    backfill_roles = (
        "UPDATE user_accounts SET role = 'user' "
        "WHERE role IS NULL OR role NOT IN ('admin', 'user')"
    )
    backfill_error = _MySQLDatabaseError(1064, "unexpected backfill syntax error")
    connections = []

    def connect(**options):
        connection = _MigrationFailureConnection(
            {
                add_role_column: _MySQLDatabaseError(1060, "Duplicate column name 'role'"),
                backfill_roles: backfill_error,
            }
        )
        connections.append(connection)
        return connection

    with pytest.raises(_MySQLDatabaseError) as raised:
        UserDatabase({}, connection_factory=connect).initialize()

    migration_connection = connections[1]
    assert raised.value is backfill_error
    assert migration_connection.triggered_failures == [add_role_column, backfill_roles]
    assert migration_connection.rolled_back is True
    assert migration_connection.closed is True


def test_list_users_orders_by_created_at_before_username():
    users = UserDatabase.for_testing()
    users.initialize()
    for username in ("later", "beta", "alpha"):
        assert users.create_user(username, "safe-password") is True

    store = users._testing_store["users"]
    store["later"]["created_at"] = "2026-07-16 12:00:00"
    store["beta"]["created_at"] = "2026-07-16 08:00:00"
    store["alpha"]["created_at"] = "2026-07-16 08:00:00"

    assert [user["username"] for user in users.list_users()] == ["alpha", "beta", "later"]


def test_create_user_rolls_back_and_reraises_an_unexpected_database_error():
    error = _NoArgsDatabaseError()
    connection = _FailingConnection(error)
    users = UserDatabase({}, connection_factory=lambda **options: connection)

    with pytest.raises(_NoArgsDatabaseError) as raised:
        users.create_user("new-user", "safe-password")

    assert raised.value is error
    assert connection.rolled_back is True


def test_bootstrap_insert_rolls_back_and_reraises_an_unexpected_database_error():
    error = _NoArgsDatabaseError()
    connection = _FailingConnection(error)
    users = UserDatabase({}, connection_factory=lambda **options: connection)

    with pytest.raises(_NoArgsDatabaseError) as raised:
        users.create_user_if_missing("admin", "safe-password")

    assert raised.value is error
    assert connection.rolled_back is True
    assert connection.statement.startswith("INSERT INTO user_accounts")


@pytest.mark.parametrize(
    ("operation", "expected_statement"),
    [
        (lambda users: users.ensure_admin("admin", "safe-password"), "SELECT username"),
        (lambda users: users.set_active("member", False), "UPDATE user_accounts SET is_active"),
    ],
)
def test_admin_and_generic_mutations_roll_back_close_and_reraise_unexpected_errors(
    operation, expected_statement
):
    error = _NoArgsDatabaseError()
    connection = _FailingConnection(error)
    users = UserDatabase({}, connection_factory=lambda **options: connection)

    with pytest.raises(_NoArgsDatabaseError) as raised:
        operation(users)

    assert raised.value is error
    assert connection.rolled_back is True
    assert connection.closed is True
    assert connection.statement.startswith(expected_statement)


def test_initialize_rejects_an_unsafe_user_database_identifier_before_connecting():
    connect_calls = []
    users = UserDatabase(
        {"USER_DATABASE": "user_db; DROP DATABASE user_db"},
        connection_factory=lambda **options: connect_calls.append(options),
    )

    with pytest.raises(ValueError, match="USER_DATABASE"):
        users.initialize()

    assert connect_calls == []


@pytest.mark.parametrize(
    "failing_prefix",
    [
        "CREATE DATABASE",
        "CREATE TABLE",
        "ALTER TABLE",
        "UPDATE user_accounts SET role",
    ],
)
def test_initialize_rolls_back_reraises_and_closes_connections_on_database_and_migration_errors(
    failing_prefix,
):
    error = _NoArgsDatabaseError()
    connections = []

    def connect(**options):
        connection = _InitializeFailingConnection(error, failing_prefix)
        connections.append(connection)
        return connection

    users = UserDatabase({}, connection_factory=connect)

    with pytest.raises(_NoArgsDatabaseError) as raised:
        users.initialize()

    assert raised.value is error
    assert any(connection.rolled_back for connection in connections)
    assert all(connection.closed for connection in connections)


class _NoArgsDatabaseError(Exception):
    def __init__(self):
        Exception.__init__(self)


class _MySQLDatabaseError(Exception):
    pass


class _FailingConnection:
    def __init__(self, error):
        self.error = error
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return _FailingCursor(self, self.error)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FailingCursor:
    def __init__(self, connection, error):
        self.connection = connection
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params):
        self.connection.statement = sql
        raise self.error


class _InitializeFailingConnection:
    def __init__(self, error, failing_prefix):
        self.error = error
        self.failing_prefix = failing_prefix
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return _InitializeFailingCursor(self)

    def commit(self):
        pass

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _InitializeFailingCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params=None):
        if sql.startswith(self.connection.failing_prefix):
            raise self.connection.error


class _RecordingConnection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return _RecordingCursor(self.statements)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _RecordingCursor:
    def __init__(self, statements):
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params=None):
        self.statements.append(sql)


class _MigrationFailureConnection(_RecordingConnection):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures
        self.triggered_failures = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return _MigrationFailureCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _MigrationFailureCursor(_RecordingCursor):
    def __init__(self, connection):
        super().__init__(connection.statements)
        self.connection = connection

    def execute(self, sql, params=None):
        super().execute(sql, params)
        error = self.connection.failures.get(sql)
        if error is not None:
            self.connection.triggered_failures.append(sql)
            raise error
