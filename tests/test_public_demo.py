"""The public preview is empty, loopback-only, and integration-disabled."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_demo_import_startup_and_routes_never_use_integrations():
    script = r'''
import builtins
import os
from pathlib import Path
import socket
import threading
import urllib.request
import dotenv
import pymysql

def forbidden(*args, **kwargs):
    raise AssertionError("Public demo attempted integration I/O")

original_open = builtins.open
original_path_open = Path.open
def guard(path):
    candidate = Path(path).resolve()
    root = Path.cwd()
    value = candidate.relative_to(root).as_posix().lower() if candidate.is_relative_to(root) else ""
    if value.startswith(("data/", ".codex/")) or candidate.name.startswith(".env"):
        forbidden()
def safe_open(path, *args, **kwargs):
    guard(path)
    return original_open(path, *args, **kwargs)
def safe_path_open(path, *args, **kwargs):
    guard(path)
    return original_path_open(path, *args, **kwargs)
builtins.open = safe_open
Path.open = safe_path_open
threading.Thread.start = forbidden
socket.create_connection = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
urllib.request.urlopen = forbidden
pymysql.connect = forbidden
dotenv.load_dotenv = forbidden
os.environ["PYTHON_DOTENV_DISABLED"] = "0"
os.environ["MODEL_GATEWAY_BASE_URL"] = "https://synthetic.invalid/v1"
os.environ["MODEL_GATEWAY_API_KEY"] = "<synthetic-inherited-credential>"
os.environ["ADMIN_USERNAME"] = "synthetic-admin"
os.environ["ADMIN_PASSWORD"] = "<synthetic-inherited-password>"
os.environ["SMTP_HOST"] = "synthetic.invalid"
os.environ["ENABLE_MOCK_PAYMENTS"] = "1"
import public_demo
assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
app = public_demo.create_demo_app()
app.testing = True
assert not app.debug
assert not app.config["ENABLE_MOCK_PAYMENTS"]
assert app.config["ADMIN_USERNAME"] == ""
assert app.config["ADMIN_PASSWORD"] == ""
assert app.extensions["user_database"]._testing_store["users"] == {}
assert app.extensions["user_database"]._testing_store["admin_principals"] == {}
assert app.extensions["model_catalog"].load()["providers"] == []
assert app.extensions["model_health"] == {}
client = app.test_client()
for path in ("/", "/login", "/model-plaza"):
    assert client.get(path).status_code == 200, path
assert client.get("/workspace").status_code == 302
assert client.get("/api/conversations").status_code == 401
with client.session_transaction() as session:
    session["username"] = "synthetic-session"
    session["is_admin"] = True
assert client.get("/workspace").status_code == 200
assert client.post("/api/conversations", json={}).status_code == 400
assert public_demo.create_demo_app().secret_key != app.secret_key
calls = []
app.run = lambda **kwargs: calls.append(kwargs)
public_demo.main(app)
assert calls == [{"host": "127.0.0.1", "port": 5000, "debug": False, "use_reloader": False, "load_dotenv": False}]
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_disabled_adapter_never_fabricates_success():
    from public_demo import DisabledAdapter

    adapter = DisabledAdapter()
    with pytest.raises(RuntimeError, match="disabled"):
        adapter.reply_with_history("synthetic prompt", [], model="deepseek")
    with pytest.raises(RuntimeError, match="disabled"):
        list(adapter.stream_reply_with_history("synthetic prompt", []))
    assert adapter.health_check("deepseek") is False


def test_factory_respects_injected_catalog_without_workers(monkeypatch):
    import app as app_module
    from database import Database
    from public_demo import DisabledAdapter, DisabledCatalog
    from user_database import UserDatabase

    def forbidden(*args, **kwargs):
        pytest.fail("Factory ignored disabled background tasks or injected catalog")

    monkeypatch.setattr(app_module.Thread, "start", forbidden)
    monkeypatch.setattr(app_module, "ModelCatalogService", forbidden)
    monkeypatch.setattr(app_module.config, "FLASK_SECRET_KEY", "<synthetic-session-signing-secret-32-plus>")
    monkeypatch.setattr(app_module.config, "ADMIN_USERNAME", "")
    monkeypatch.setattr(app_module.config, "ADMIN_PASSWORD", "")
    catalog = DisabledCatalog()
    application = app_module.create_app(database=Database.for_testing(), user_database=UserDatabase.for_testing(), adapter=DisabledAdapter(), catalog_service=catalog, start_background_tasks=False)
    assert application.extensions["model_catalog"] is catalog
