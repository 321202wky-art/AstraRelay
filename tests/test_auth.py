import importlib
import secrets

import pytest
from flask import Flask

import auth
from auth import initialize_auth
from bootstrap_admin import (
    bootstrap_admin,
    main as bootstrap_main,
    set_current_user_variable,
)
from user_database import UserDatabase

# Explicit synthetic fixtures; slicing preserves legacy length/whitespace cases.
SYNTHETIC_CREDENTIAL_01 = '<synthetic-bootstrap-password>'[11:-1]
SYNTHETIC_CREDENTIAL_02 = '<synthetic-long-admin-password>'[11:-1]
SYNTHETIC_CREDENTIAL_03 = '<synthetic-safe-password>'[11:-1]
SYNTHETIC_CREDENTIAL_04 = '<synthetic-x>'[11:-1]


def test_config_reads_security_settings_from_environment_with_blank_fallbacks(monkeypatch):
    import config

    monkeypatch.setenv("FLASK_SECRET_KEY", "secure-test-key-" + (SYNTHETIC_CREDENTIAL_04 * 32))
    monkeypatch.setenv("ADMIN_USERNAME", "configured-admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "configured-password")
    configured = importlib.reload(config)

    assert configured.FLASK_SECRET_KEY == "secure-test-key-" + (SYNTHETIC_CREDENTIAL_04 * 32)
    assert configured.ADMIN_USERNAME == "configured-admin"
    assert configured.ADMIN_PASSWORD == "configured-password"

    monkeypatch.delenv("FLASK_SECRET_KEY")
    monkeypatch.delenv("ADMIN_USERNAME")
    monkeypatch.delenv("ADMIN_PASSWORD")
    unset = importlib.reload(config)

    assert unset.FLASK_SECRET_KEY == ""
    assert unset.ADMIN_USERNAME == ""
    assert unset.ADMIN_PASSWORD == ""


def test_config_reads_mysql_password_from_environment_with_a_blank_fallback(monkeypatch):
    import config

    monkeypatch.setenv("MYSQL_PASSWORD", "database-password-from-environment")
    configured = importlib.reload(config)

    assert configured.MYSQL_PASSWORD == "database-password-from-environment"

    # ``load_dotenv`` intentionally preserves an explicitly empty process value.
    # This keeps the fallback assertion valid even when a developer has a local .env.
    monkeypatch.setenv("MYSQL_PASSWORD", "")
    unset = importlib.reload(config)

    assert unset.MYSQL_PASSWORD == ""


def test_user_verifies_only_with_the_password_used_when_created():
    users = UserDatabase.for_testing()
    users.initialize()

    assert users.create_user("admin", SYNTHETIC_CREDENTIAL_03) is True

    assert users.verify_user("admin", SYNTHETIC_CREDENTIAL_03) is True
    assert users.verify_user("admin", "wrong-password") is False


def test_initialize_auth_does_not_reset_an_existing_administrator_password():
    users = UserDatabase.for_testing()
    users.initialize()
    assert users.create_user("admin", "existing-password") is True
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SYNTHETIC_CREDENTIAL_04 * 32,
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD=SYNTHETIC_CREDENTIAL_01,
    )

    initialize_auth(app, users)

    assert users.verify_user("admin", "existing-password") is True
    assert users.verify_user("admin", SYNTHETIC_CREDENTIAL_01) is False
    assert users.verify_user("admin", "existing-password", require_admin=True) is True


def test_initialize_auth_bootstraps_a_missing_owner_with_a_stripped_username():
    users = AuthDatabase()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SYNTHETIC_CREDENTIAL_04 * 32,
        ADMIN_USERNAME=" admin ",
        ADMIN_PASSWORD=SYNTHETIC_CREDENTIAL_01,
    )

    initialize_auth(app, users)

    assert users.ensured_owner == ("admin", SYNTHETIC_CREDENTIAL_01)
    assert not hasattr(users, "ensured_admin")


class AuthDatabase:
    def ensure_owner(self, username, password):
        self.ensured_owner = (username, password)


def test_initialize_auth_rejects_blank_bootstrap_credentials():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SYNTHETIC_CREDENTIAL_04 * 32,
        ADMIN_USERNAME="   ",
        ADMIN_PASSWORD=SYNTHETIC_CREDENTIAL_03,
    )

    with pytest.raises(ValueError, match="ADMIN_USERNAME"):
        initialize_auth(app, AuthDatabase())


def test_initialize_auth_rejects_an_overlong_trimmed_bootstrap_username_before_creating_it():
    users = AuthDatabase()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SYNTHETIC_CREDENTIAL_04 * 32,
        ADMIN_USERNAME=" " + ("a" * 65) + " ",
        ADMIN_PASSWORD=SYNTHETIC_CREDENTIAL_03,
    )

    with pytest.raises(ValueError, match="ADMIN_USERNAME"):
        initialize_auth(app, users)

    assert not hasattr(users, "ensured_owner")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SECRET_KEY", "change-this-development-secret"),
        ("SECRET_KEY", "replace-with-a-real-production-secret-before-deploying"),
        ("SECRET_KEY", "short"),
        ("ADMIN_PASSWORD", "change-me"),
        ("ADMIN_PASSWORD", "replace-with-a-real-production-secret-before-deploying"),
        ("ADMIN_PASSWORD", "short"),
    ],
)
def test_initialize_auth_rejects_insecure_bootstrap_settings(key, value):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SYNTHETIC_CREDENTIAL_04 * 32,
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD=SYNTHETIC_CREDENTIAL_02,
    )
    app.config[key] = value

    with pytest.raises(ValueError):
        initialize_auth(app, AuthDatabase())


def test_initialize_auth_allows_registration_when_administrator_bootstrap_is_unset():
    app = Flask(__name__)
    app.config.update(SECRET_KEY=SYNTHETIC_CREDENTIAL_04 * 32, ADMIN_USERNAME="", ADMIN_PASSWORD="")

    initialize_auth(app, AuthDatabase())

    assert app.secret_key == SYNTHETIC_CREDENTIAL_04 * 32


def test_login_required_rejects_an_email_session_invalidated_by_a_password_reset():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", "123456789")
    users.mark_email_verified("alice@example.com")
    app = Flask(__name__)
    app.config.update(SECRET_KEY=SYNTHETIC_CREDENTIAL_04 * 32, ADMIN_USERNAME="", ADMIN_PASSWORD="")
    initialize_auth(app, users)

    @app.get("/login")
    def login():
        return "login"

    @app.get("/protected")
    @auth.login_required
    def protected():
        return "protected"

    identity = users.get_session_identity("alice@example.com")
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(identity)
    assert client.get("/protected").status_code == 200

    users.reset_password_by_email("alice@example.com", "987654321")

    response = client.get("/protected")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    with client.session_transaction() as current_session:
        assert dict(current_session) == {}


def test_admin_required_rejects_an_email_backed_admin_session_invalidated_by_a_password_reset():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("admin@example.com", "123456789")
    identity = users.mark_email_verified("admin@example.com")
    assert users.set_role(identity["username"], "admin") is True
    app = Flask(__name__)
    app.config.update(SECRET_KEY=SYNTHETIC_CREDENTIAL_04 * 32, ADMIN_USERNAME="", ADMIN_PASSWORD="")
    initialize_auth(app, users)

    @app.get("/admin/login")
    def admin_login():
        return "admin login"

    @app.get("/admin/protected")
    @auth.admin_required
    def protected_admin_view():
        return "protected"

    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(identity)
        current_session["is_admin"] = True
    assert client.get("/admin/protected").status_code == 200

    users.reset_password_by_email("admin@example.com", "987654321")

    response = client.get("/admin/protected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")
    with client.session_transaction() as current_session:
        assert dict(current_session) == {}


def test_validate_username_strips_valid_values_and_rejects_blank_or_overlong_values():
    assert auth.validate_username(" named-user ") == "named-user"

    for invalid_username in ("   ", "u" * 65):
        with pytest.raises(ValueError):
            auth.validate_username(invalid_username)


def test_admin_required_requires_a_username_and_an_exact_true_administrator_flag():
    app = Flask(__name__)
    app.secret_key = SYNTHETIC_CREDENTIAL_04 * 32

    @app.get("/admin/login")
    def admin_login():
        return "admin login"

    @app.get("/admin/protected")
    @auth.admin_required
    def protected_admin_view():
        return "protected"

    client = app.test_client()
    redirected = client.get("/admin/protected")
    with client.session_transaction() as current_session:
        current_session["is_admin"] = True
    flag_only = client.get("/admin/protected")
    with client.session_transaction() as current_session:
        current_session["username"] = "administrator"
        current_session["is_admin"] = True
    allowed = client.get("/admin/protected")

    assert redirected.status_code == 302
    assert redirected.headers["Location"].endswith("/admin/login")
    assert flag_only.status_code == 302
    assert flag_only.headers["Location"].endswith("/admin/login")
    assert allowed.status_code == 200


class BootstrapDatabase:
    def __init__(self, settings):
        self.settings = settings
        self.initialized = False

    def initialize(self):
        self.initialized = True


def _bootstrap_environment():
    return {
        "MYSQL_PASSWORD": secrets.token_urlsafe(18),
        "ADMIN_USERNAME": f"operator-{secrets.token_urlsafe(8)}",
        "ADMIN_PASSWORD": secrets.token_urlsafe(18),
    }


def test_bootstrap_admin_creates_a_secret_and_does_not_expose_runtime_credentials(
    monkeypatch, capsys
):
    import bootstrap_admin as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "UserDatabase", BootstrapDatabase)
    environment = _bootstrap_environment()
    supplied_password = environment["ADMIN_PASSWORD"]
    writes = []
    initialized = []

    bootstrap_admin(
        environment,
        lambda name, value: writes.append((name, value)),
        lambda app, database: initialized.append((app, database)),
    )

    captured_output = capsys.readouterr().out
    app, database = initialized[0]
    assert writes[0][0] == "FLASK_SECRET_KEY"
    assert len(writes[0][1]) >= 32
    assert environment["FLASK_SECRET_KEY"] == writes[0][1]
    assert database.initialized is True
    assert app.config["SECRET_KEY"] == environment["FLASK_SECRET_KEY"]
    assert app.config["ADMIN_USERNAME"] == environment["ADMIN_USERNAME"]
    assert app.config["ADMIN_PASSWORD"] == supplied_password
    assert supplied_password not in captured_output


def test_bootstrap_admin_rejects_missing_mysql_password_without_initializing(monkeypatch):
    import bootstrap_admin as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "UserDatabase", BootstrapDatabase)
    environment = _bootstrap_environment()
    environment["MYSQL_PASSWORD"] = ""
    initialized = []

    with pytest.raises(ValueError, match="MYSQL_PASSWORD") as error:
        bootstrap_admin(environment, lambda name, value: None, lambda *args: initialized.append(args))

    assert "缺少" in str(error.value)
    assert initialized == []


def test_bootstrap_admin_reuses_an_existing_secure_secret_without_writing_environment(
    monkeypatch,
):
    import bootstrap_admin as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "UserDatabase", BootstrapDatabase)
    environment = _bootstrap_environment()
    environment["FLASK_SECRET_KEY"] = secrets.token_urlsafe(32)
    writes = []
    initialized = []

    bootstrap_admin(
        environment,
        lambda name, value: writes.append((name, value)),
        lambda app, database: initialized.append((app, database)),
    )

    assert writes == []
    assert initialized[0][0].config["SECRET_KEY"] == environment["FLASK_SECRET_KEY"]


@pytest.mark.parametrize("missing_name", ("ADMIN_USERNAME", "ADMIN_PASSWORD"))
def test_bootstrap_admin_rejects_blank_administrator_runtime_variables_before_initializing(
    monkeypatch, missing_name
):
    import bootstrap_admin as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "UserDatabase", BootstrapDatabase)
    environment = _bootstrap_environment()
    environment[missing_name] = "   "
    initialized = []

    with pytest.raises(ValueError, match=missing_name):
        bootstrap_admin(environment, lambda name, value: None, lambda *args: initialized.append(args))

    assert initialized == []


def test_set_current_user_variable_writes_only_the_current_windows_user_registry_key(monkeypatch):
    import bootstrap_admin as bootstrap_module

    calls = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(
        bootstrap_module.winreg,
        "OpenKey",
        lambda *args: calls.append(("open", args)) or Key(),
    )
    monkeypatch.setattr(
        bootstrap_module.winreg,
        "SetValueEx",
        lambda *args: calls.append(("set", args)),
    )
    variable_name = "TEST_BOOTSTRAP_VARIABLE"
    value = secrets.token_urlsafe(12)

    set_current_user_variable(variable_name, value)

    assert calls[0][1][0] == bootstrap_module.winreg.HKEY_CURRENT_USER
    assert calls[1][1][1:] == (variable_name, 0, bootstrap_module.winreg.REG_SZ, value)


def test_bootstrap_cli_reports_a_secret_free_chinese_failure_status(monkeypatch, capsys):
    import bootstrap_admin as bootstrap_module

    protected_value = secrets.token_urlsafe(18)

    def fail(*args):
        raise ValueError(protected_value)

    monkeypatch.setattr(bootstrap_module, "bootstrap_admin", fail)

    assert bootstrap_main() == 1

    output = capsys.readouterr().out
    assert "失败" in output
    assert protected_value not in output
