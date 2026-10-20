import json
import io
import logging
import re
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from flask import Flask
from jinja2 import DictLoader

import app as app_module
import config
import email_auth as email_auth_module
from app import create_app
from database import Database
from email_auth import EmailAuthService, EmailCodeRateLimitError
from user_database import UserDatabase

# Explicit synthetic fixtures; slicing preserves legacy length/whitespace cases.
SYNTHETIC_CREDENTIAL_01 = '<synthetic- pass word >'[11:-1]
SYNTHETIC_CREDENTIAL_02 = '<synthetic-123456789>'[11:-1]
SYNTHETIC_CREDENTIAL_03 = '<synthetic-987654321>'[11:-1]
SYNTHETIC_CREDENTIAL_04 = '<synthetic-password-secret>'[11:-1]
SYNTHETIC_CREDENTIAL_05 = '<synthetic-safe-password>'[11:-1]
SYNTHETIC_CREDENTIAL_06 = '<synthetic-test-session-signing-key->'[11:-1]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET_KEY = SYNTHETIC_CREDENTIAL_06 + ("x" * 32)


class PublicBoundaryCatalog:
    def __init__(self, cache_path):
        self.cache_path = cache_path

    def load(self):
        return {
            "providers": [
                {
                    "key": "gateway_main",
                    "name": "GatewayMain",
                    "status": "fresh",
                    "capabilities": {
                        "chat": [
                            {
                                "platform_model_id": "gateway_main:deepseek-v3",
                                "model_id": "deepseek-v3",
                                "display_name": "DeepSeek V3",
                                "capability": "chat",
                                "callable": True,
                            },
                            {
                                "platform_model_id": "gateway_main:kimi-k3",
                                "model_id": "kimi-k3",
                                "display_name": "Kimi K3",
                                "capability": "chat",
                                "callable": True,
                            },
                            {
                                "platform_model_id": "gateway_main:gpt-5.4-mini",
                                "model_id": "gpt-5.4-mini",
                                "display_name": "GPT 5.4 Mini",
                                "capability": "chat",
                                "callable": True,
                            },
                        ]
                    },
                }
            ],
            "updated_at": "2026-07-28T00:00:00Z",
        }

    def refresh(self):
        return self.load()


def test_build_model_catalog_groups_configured_models_by_provider():
    catalog = app_module.build_model_catalog(
        {
            "deepseek": {"provider": "DeepSeek", "display_name": "V4 Flash"},
            "deepseek_r1": {"provider": "DeepSeek", "display_name": "R1"},
            "kimi": {"provider": "Kimi", "display_name": "K2.6"},
        }
    )

    assert catalog == {
        "DeepSeek": [
            {"id": "deepseek", "name": "V4 Flash"},
            {"id": "deepseek_r1", "name": "R1"},
        ],
        "Kimi": [{"id": "kimi", "name": "K2.6"}],
    }


def test_model_catalog_requires_login():
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = application.test_client().get("/api/models/catalog")

    assert response.status_code in {302, 401}


def test_desktop_session_endpoint_requires_authentication():
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = application.test_client().get("/api/desktop/session")

    assert response.status_code == 401
    assert response.get_json() == {"error": "authentication required"}


def test_desktop_session_endpoint_returns_only_safe_session_fields():
    database = FakeDatabase()
    database.list_conversations = lambda _username: []
    application = create_app(database=database, adapter=FakeAdapter("answer"))
    client = authenticated_client(application)
    with client.session_transaction() as current_session:
        current_session["private_marker"] = "must-not-leak"

    response = client.get("/api/desktop/session")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["authenticated"] is True
    assert payload["username"] == "authenticated-user"
    assert isinstance(payload["csrf_token"], str) and payload["csrf_token"]
    assert set(payload) == {"authenticated", "username", "csrf_token"}
    assert response.headers["Cache-Control"] == "private, no-store"

    conversations = client.get("/api/conversations")
    assert conversations.status_code == 200
    assert conversations.headers["Cache-Control"] == "private, no-store"


def test_model_plaza_replaces_the_token_relay_entry_and_is_publicly_browsable():
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    assert application.test_client().get("/model-plaza").status_code == 200

    client = authenticated_client(application)
    plaza = client.get("/model-plaza")
    retired_entry = client.get("/token-hub")

    assert plaza.status_code == 200
    assert "模型广场".encode() in plaza.data
    assert b"Token" not in plaza.data
    assert retired_entry.status_code == 302
    assert retired_entry.headers["Location"].endswith("/model-plaza")


def test_public_model_catalog_is_available_without_login_but_private_catalog_stays_protected():
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    public_response = application.test_client().get("/api/public/models/catalog")
    private_response = application.test_client().get("/api/models/catalog")

    assert public_response.status_code == 200
    assert public_response.is_json
    serialized = public_response.get_data(as_text=True).lower()
    for forbidden in ("api_key", "authorization", "base_url", "sk-"):
        assert forbidden not in serialized
    assert private_response.status_code in {302, 401}


def test_media_routes_require_login_and_csrf():
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    assert application.test_client().post("/api/media/images/generate", json={}).status_code == 401
    assert authenticated_client(application).post(
        "/api/media/images/generate", json={}
    ).status_code == 400


def test_media_generation_uses_unconfigured_service_response_without_a_media_key(monkeypatch):
    monkeypatch.setattr(app_module.config, "GATEWAY_MAIN_API_KEY", "")
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))
    client = authenticated_client(application)

    response = client.post(
        "/api/media/images/generate",
        json={"model_id": "gpt-image-2", "prompt": "A lighthouse"},
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )

    assert application.extensions["media_service"] is None
    assert response.status_code == 503
    assert response.get_json() == {"error": "图像服务尚未配置"}


def test_authenticated_image_generation_returns_safe_urls_only():
    class FakeMediaService:
        def generate_image(self, model_id, prompt, size=None):
            assert (model_id, prompt, size) == ("gpt-image-2", "A lighthouse", None)
            return ["https://example.test/image.png"]

    application = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        media_service=FakeMediaService(),
    )
    client = authenticated_client(application)
    token = csrf(client, "/workspace")

    response = client.post(
        "/api/media/images/generate",
        json={"model_id": "gpt-image-2", "prompt": "A lighthouse"},
        headers={"X-CSRF-Token": token},
    )

    assert response.get_json() == {"images": ["https://example.test/image.png"]}


def test_model_catalog_returns_cached_safe_tree_for_logged_in_user(monkeypatch):
    class CachedCatalog:
        def __init__(self, cache_path):
            self.cache_path = cache_path

        def load(self):
            return {
                "providers": [
                    {
                        "key": "gateway_compat",
                        "name": "GatewayCompat",
                        "status": "fresh",
                        "capabilities": {
                            "chat": [
                                {
                                    "platform_model_id": "gateway_compat:deepseek-ai/DeepSeek-V4-Flash",
                                    "model_id": "deepseek-ai/DeepSeek-V4-Flash",
                                    "display_name": "DeepSeek-V4-Flash",
                                    "capability": "chat",
                                    "callable": True,
                                }
                            ]
                        },
                    }
                ],
                "updated_at": "2026-07-18T00:00:00Z",
            }

        def refresh(self):
            return self.load()

    monkeypatch.setattr(app_module, "ModelCatalogService", CachedCatalog, raising=False)
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = authenticated_client(application).get("/api/models/catalog")

    assert response.status_code == 200
    catalog = response.get_json()
    assert catalog["providers"][0]["name"] == "DeepSeek"
    model = catalog["providers"][0]["capabilities"]["chat"][0]
    assert model["platform_model_id"].startswith("deepseek/")
    serialized = response.get_data(as_text=True).lower()
    for forbidden in ("gateway_compat", "gateway_main", "api_key", "base_url"):
        assert forbidden not in serialized


def test_catalog_response_contains_no_vendor_connection_data(monkeypatch):
    class CachedCatalog:
        def __init__(self, cache_path):
            self.cache_path = cache_path

        def load(self):
            return {
                "providers": [{
                    "key": "gateway_compat",
                    "name": "GatewayCompat",
                    "status": "fresh",
                    "capabilities": {"chat": [{
                        "platform_model_id": "gateway_compat:deepseek-ai/DeepSeek-V4-Flash",
                        "model_id": "deepseek-ai/DeepSeek-V4-Flash",
                        "display_name": "DeepSeek-V4-Flash",
                        "capability": "chat",
                        "callable": True,
                    }]},
                }],
                "updated_at": "2026-07-19T00:00:00Z",
            }

        def refresh(self):
            return self.load()

    monkeypatch.setattr(app_module, "ModelCatalogService", CachedCatalog, raising=False)
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    payload = authenticated_client(application).get("/api/models/catalog").get_json()
    serialized = json.dumps(payload, ensure_ascii=False).lower()

    for forbidden in (
        "api_key",
        "authorization",
        "base_url",
        "sk-",
        "gateway_compat",
        "gateway_main",
        "gateway_secondary",
    ):
        assert forbidden not in serialized
    assert payload["providers"][0]["key"] == "deepseek"


def test_model_catalog_refresh_requires_administrator_and_csrf(monkeypatch):
    class CachedCatalog:
        def __init__(self, cache_path):
            self.refresh_calls = 0
            self.updated_at = None

        def load(self):
            return {"providers": [], "updated_at": self.updated_at}

        def refresh(self):
            self.refresh_calls += 1
            self.updated_at = "2026-07-18T00:00:00Z"
            return self.load()

    monkeypatch.setattr(app_module, "ModelCatalogService", CachedCatalog, raising=False)
    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    assert authenticated_client(application).post("/api/admin/models/catalog/refresh").status_code == 401

    client = administrator_client(application)
    assert client.post("/api/admin/models/catalog/refresh").status_code == 400

    response = client.post(
        "/api/admin/models/catalog/refresh",
        data={"_csrf_token": csrf(client)},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "availability": "unavailable",
        "last_attempt_at": "2026-07-18T00:00:00Z",
        "last_success_at": None,
        "failure_categories": [],
        "retryable": False,
        "message": "模型目录暂不可用，请稍后重试",
        "providers": [],
        "updated_at": "2026-07-18T00:00:00Z",
    }


def test_catalog_background_and_manual_refresh_share_one_coordinator(monkeypatch):
    background_started = threading.Event()

    class StaticCatalog:
        def __init__(self, cache_path):
            self.refresh_calls = 0

        def load(self):
            return {
                "availability": "unavailable",
                "last_attempt_at": None,
                "last_success_at": None,
                "failure_categories": [],
                "retryable": False,
                "message": "safe",
                "providers": [],
                "updated_at": None,
            }

        def refresh(self):
            self.refresh_calls += 1
            return self.load()

    class RecordingCoordinator:
        instances = []

        def __init__(self, service, logger):
            self.service = service
            self.logger = logger
            self.manual_calls = 0
            self.background_calls = 0
            self.instances.append(self)

        def run_forever(self):
            self.background_calls += 1
            background_started.set()

        def refresh_once(self, *, manual=False):
            assert manual is True
            self.manual_calls += 1
            return self.service.refresh()

    monkeypatch.setattr(app_module, "ModelCatalogService", StaticCatalog)
    monkeypatch.setattr(
        app_module, "CatalogRefreshCoordinator", RecordingCoordinator, raising=False
    )

    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    assert background_started.wait(0.5)
    coordinator = application.extensions["model_catalog_refresh"]
    assert coordinator is RecordingCoordinator.instances[0]

    client = administrator_client(application)
    response = client.post(
        "/api/admin/models/catalog/refresh",
        data={"_csrf_token": csrf(client)},
    )

    assert response.status_code == 200
    assert coordinator.background_calls == 1
    assert coordinator.manual_calls == 1
    assert coordinator.service.refresh_calls == 1


class FakeDatabase:
    def __init__(self, messages=None):
        self.messages = messages or []
        self.saved = []
        self.initialize_calls = 0
        self.users = {}

    def initialize(self):
        self.initialize_calls += 1

    def ensure_user(self, username, password):
        self.users[username] = password

    def create_user_if_missing(self, username, password):
        self.users.setdefault(username, password)

    def verify_user(self, username, password):
        return self.users.get(username) == password

    def add_message(self, role, content, model="deepseek"):
        self.saved.append((role, content, model))

    def get_all_messages(self, limit=None):
        self.history_limit = limit
        return self.messages

    def get_recent_messages(self, limit=8, model="deepseek"):
        assert limit == 8
        return self.messages[-8:]


class FakeUserDatabase:
    def __init__(self, config=None, email_identity=None, email_password=SYNTHETIC_CREDENTIAL_02):
        self.config = config
        self.users = {}
        self.active = {}
        self.roles = {}
        self.principals = {}
        self.initialize_calls = 0
        self.verify_calls = []
        self.email_identity = email_identity
        self.email_password = email_password
        self.email_verify_calls = []

    def initialize(self):
        self.initialize_calls += 1

    def ensure_owner(self, username, password):
        if username not in self.users:
            self.create_user(username, password)
        self.roles[username] = "admin"
        self.principals[username] = {
            "username": username,
            "access_tier": "owner",
            "policy_version": 1,
        }

    def get_admin_principal(self, username):
        principal = self.principals.get(username)
        return None if principal is None else dict(principal)

    def create_user_if_missing(self, username, password):
        self.users.setdefault(username, password)
        self.active.setdefault(username, True)
        self.roles.setdefault(username, "user")

    def create_user(self, username, password):
        if username in self.users:
            return False
        self.users[username] = password
        self.active[username] = True
        self.roles[username] = "user"
        return True

    def verify_user(self, username, password, require_admin=False):
        self.verify_calls.append((username, password))
        return (
            self.users.get(username) == password
            and self.active.get(username, False)
            and (not require_admin or self.roles.get(username) == "admin")
        )

    def authenticate_administrator(self, username, password):
        if not self.verify_user(username, password, require_admin=True):
            return None
        identity = {"username": username}
        versioned = self.get_session_identity_by_username(username)
        if versioned is not None:
            identity.update(versioned)
        return identity

    def verify_email_password(self, email, password):
        self.email_verify_calls.append((email, password))
        if (
            self.email_identity is not None
            and email.strip().lower() == self.email_identity["email"]
            and password == self.email_password
        ):
            return dict(self.email_identity)
        return None

    def get_session_identity(self, email):
        if (
            self.email_identity is not None
            and email.strip().lower() == self.email_identity["email"]
        ):
            return dict(self.email_identity)
        return None

    def get_session_identity_by_username(self, username):
        if self.email_identity is not None and username == self.email_identity["username"]:
            return {
                "email": self.email_identity["email"],
                "session_version": self.email_identity["session_version"],
            }
        return None

    def get_phone_binding_challenge(self, email):
        return None

    def list_users(self):
        return [
            {
                "username": username,
                "role": self.roles[username],
                "is_active": self.active[username],
            }
            for username in self.users
        ]

    def set_active(self, username, is_active):
        if username not in self.users:
            return False
        self.active[username] = is_active
        return True

    def set_role(self, username, role):
        if username not in self.users:
            return False
        self.roles[username] = role
        return True

    def reset_password(self, username, password):
        if username not in self.users:
            return False
        self.users[username] = password
        return True


class FakeEmailAuthService:
    def __init__(
        self,
        identity=None,
        registration_error=None,
        verification_error=None,
        password_reset_request_error=None,
        password_reset_error=None,
    ):
        self.identity = identity or {
            "username": "user-1",
            "email": "alice@example.com",
            "session_version": 0,
        }
        self.registration_error = registration_error
        self.verification_error = verification_error
        self.password_reset_request_error = password_reset_request_error
        self.password_reset_error = password_reset_error
        self.registration_requests = []
        self.verification_requests = []
        self.password_reset_requests = []
        self.password_reset_calls = []

    def request_registration(self, email, password):
        self.registration_requests.append((email, password))
        if self.registration_error:
            raise self.registration_error
        return "attempt-1"

    def verify_registration(self, email, attempt_id, code):
        self.verification_requests.append((email, attempt_id, code))
        if self.verification_error:
            raise self.verification_error
        return self.identity

    def request_password_reset(self, email):
        self.password_reset_requests.append(email)
        if self.password_reset_request_error:
            raise self.password_reset_request_error

    def reset_password(self, email, code, password):
        self.password_reset_calls.append((email, code, password))
        if self.password_reset_error:
            raise self.password_reset_error


class FakeSmsAuthService:
    def __init__(self, verified=True, send_error=None, verify_error=None):
        self.sent = []
        self.verified = verified
        self.verify_requests = []
        self.send_error = send_error
        self.verify_error = verify_error
        self.code = f"{uuid4().int % 1_000_000:06d}"

    def send_binding_code(self, phone_number):
        if self.send_error:
            raise self.send_error
        self.sent.append(phone_number)

    def verify_binding_code(self, phone_number, code):
        if self.verify_error:
            raise self.verify_error
        self.verify_requests.append((phone_number, code))
        return self.verified and code == self.code


@pytest.fixture(autouse=True)
def configure_secure_test_auth(monkeypatch):
    monkeypatch.setattr(app_module.config, "FLASK_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setattr(app_module.config, "ADMIN_USERNAME", "")
    monkeypatch.setattr(app_module.config, "ADMIN_PASSWORD", "")
    monkeypatch.setattr(app_module, "UserDatabase", FakeUserDatabase, raising=False)


class FakeAdapter:
    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error
        self.messages = []

    def reply(self, message):
        self.messages.append(message)
        if self.error:
            raise self.error
        return self.answer


class HistoryAwareAdapter:
    def __init__(self, answer):
        self.answer = answer
        self.outgoing_messages = None

    def reply_with_history(self, message, history, model="deepseek"):
        self.model = model
        self.outgoing_messages = history + [{"role": "user", "content": message}]
        return self.answer


def authenticated_client(app):
    identity = {
        "username": "authenticated-user",
        "email": "authenticated@example.com",
        "session_version": 0,
    }
    users = app.extensions["user_database"]
    if hasattr(users, "email_identity"):
        users.email_identity = dict(identity)
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(identity)
    return client


class FakeWorkspaceStreamService:
    def __init__(self):
        self.calls = []

    def stream(self, message, conversation_id, username, model_settings, history_message=None):
        self.calls.append((message, conversation_id, username, model_settings, history_message))
        yield {"event": "start", "models": [item["model_id"] for item in model_settings]}
        yield {"event": "token", "model": model_settings[0]["model_id"], "token": "hello"}
        yield {"event": "done", "model": model_settings[0]["model_id"]}


class FakeAttachmentService:
    def __init__(self):
        self.calls = []

    def prepare_files(self, username, conversation_id, files):
        self.calls.append((username, conversation_id, files))
        return [
            {
                "token": "safe-token",
                "name": "notes.txt",
                "media_type": "text/plain",
                "size": 5,
                "preview": "hello",
            }
        ]


def test_create_conversation_requires_csrf_and_accepts_a_valid_token():
    database = Database.for_testing()
    database.initialize()
    application = create_app(database=database, adapter=FakeAdapter("unused"))
    client = authenticated_client(application)
    token = csrf(client)

    missing = client.post("/api/conversations", json={"title": "New"})
    mismatched = client.post(
        "/api/conversations",
        json={"title": "New"},
        headers={"X-CSRF-Token": token + "-invalid"},
    )
    assert missing.status_code == 400
    assert mismatched.status_code == 400
    assert database.list_conversations("authenticated-user") == []

    accepted = client.post(
        "/api/conversations",
        json={"title": "New"},
        headers={"X-CSRF-Token": token},
    )
    assert accepted.status_code == 201
    assert accepted.get_json()["title"] == "New"
    assert len(database.list_conversations("authenticated-user")) == 1


def test_workspace_stream_rejects_empty_models_without_upstream_call():
    database = Database.for_testing()
    database.initialize()
    stream_service = FakeWorkspaceStreamService()
    application = create_app(
        database=database,
        adapter=FakeAdapter("unused"),
        workspace_stream_service=stream_service,
    )
    client = authenticated_client(application)
    conversation = database.create_conversation("authenticated-user")

    response = client.post(
        "/api/workspace/stream",
        json={"message": "draft", "conversation_id": conversation["id"], "models": []},
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )

    assert response.status_code == 400
    assert stream_service.calls == []


def test_workspace_attachment_upload_requires_owned_conversation_and_csrf():
    database = Database.for_testing()
    database.initialize()
    attachment_service = FakeAttachmentService()
    application = create_app(
        database=database,
        adapter=FakeAdapter("unused"),
        attachment_service=attachment_service,
    )
    client = authenticated_client(application)
    owned = database.create_conversation("authenticated-user")
    other = database.create_conversation("another-user")

    missing_csrf = client.post(
        "/api/workspace/attachments",
        data={"conversation_id": owned["id"], "files": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
    )
    forbidden = client.post(
        "/api/workspace/attachments",
        data={"conversation_id": other["id"], "files": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )
    response = client.post(
        "/api/workspace/attachments",
        data={"conversation_id": owned["id"], "files": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )

    assert missing_csrf.status_code == 400
    assert forbidden.status_code == 404
    assert response.status_code == 201
    assert response.get_json() == {
        "attachments": [
            {
                "token": "safe-token",
                "name": "notes.txt",
                "media_type": "text/plain",
                "size": 5,
                "preview": "hello",
            }
        ]
    }
    assert attachment_service.calls[0][:2] == ("authenticated-user", owned["id"])


def test_workspace_stream_translates_public_models_both_directions(monkeypatch):
    monkeypatch.setattr(app_module, "ModelCatalogService", PublicBoundaryCatalog)
    database = Database.for_testing()
    database.initialize()
    stream_service = FakeWorkspaceStreamService()
    application = create_app(
        database=database,
        adapter=FakeAdapter("unused"),
        workspace_stream_service=stream_service,
    )
    client = authenticated_client(application)
    conversation = database.create_conversation("authenticated-user")

    response = client.post(
        "/api/workspace/stream",
        json={
            "message": "draft",
            "conversation_id": conversation["id"],
            "models": [
                {
                    "model_id": "deepseek/deepseek-v3",
                    "context_mode": "smart",
                    "message_ids": [],
                }
            ],
        },
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "deepseek/deepseek-v3" in body
    assert "gateway_main" not in body.lower()
    assert stream_service.calls[0][3][0]["model_id"] == "gateway_main:deepseek-v3"


def test_workspace_model_settings_are_owned_and_require_csrf(monkeypatch):
    monkeypatch.setattr(app_module, "ModelCatalogService", PublicBoundaryCatalog)
    database = Database.for_testing()
    database.initialize()
    application = create_app(database=database, adapter=FakeAdapter("unused"))
    client = authenticated_client(application)
    conversation = database.create_conversation("authenticated-user")
    payload = {
        "models": [
            {
                "model_id": "deepseek/deepseek-v3",
                "context_mode": "smart",
                "message_ids": [],
            }
        ]
    }

    assert client.put(
        f"/api/conversations/{conversation['id']}/models", json=payload
    ).status_code == 400
    response = client.put(
        f"/api/conversations/{conversation['id']}/models",
        json=payload,
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )

    assert response.status_code == 200
    assert response.get_json()["models"][0]["model_id"] == "deepseek/deepseek-v3"
    assert database.get_conversation_model_settings(
        conversation["id"], "authenticated-user"
    )[0]["model_id"] == "gateway_main:deepseek-v3"
    loaded = client.get(f"/api/conversations/{conversation['id']}/models")

    assert loaded.status_code == 200
    assert loaded.mimetype == "application/json"
    assert loaded.get_json()["models"][0]["model_id"] == "deepseek/deepseek-v3"


def test_database_deletes_only_an_owned_conversation_and_its_scoped_state():
    database = Database.for_testing()
    database.initialize()
    owned = database.create_conversation("alice", "Owned")
    other = database.create_conversation("bob", "Other")
    database.add_collaboration_message(
        "user", "owned message", conversation_id=owned["id"]
    )
    database.replace_conversation_model_settings(
        owned["id"],
        "alice",
        [{"model_id": "deepseek", "context_mode": "smart", "message_ids": []}],
    )
    database.mark_model_context_synced(owned["id"], "deepseek", 7)

    assert database.delete_conversation(owned["id"], "bob") is False
    assert database.conversation_exists(owned["id"], "alice") is True
    assert database.delete_conversation(owned["id"], "alice") is True
    assert database.conversation_exists(owned["id"], "alice") is False
    assert database.get_shared_messages(owned["id"]) == []
    assert database.get_conversation_model_settings(owned["id"], "alice") == []
    assert database.get_model_context_sync(owned["id"], "deepseek") is None
    assert database.conversation_exists(other["id"], "bob") is True


def test_delete_conversation_requires_login_csrf_and_ownership():
    database = Database.for_testing()
    database.initialize()
    owned = database.create_conversation("authenticated-user", "Owned")
    other = database.create_conversation("other-user", "Other")
    application = create_app(database=database, adapter=FakeAdapter("answer"))

    anonymous = application.test_client().delete(
        f"/api/conversations/{owned['id']}"
    )
    client = authenticated_client(application)
    missing_csrf = client.delete(f"/api/conversations/{owned['id']}")
    forbidden = client.delete(
        f"/api/conversations/{other['id']}",
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )
    deleted = client.delete(
        f"/api/conversations/{owned['id']}",
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )

    assert anonymous.status_code in {302, 401}
    assert missing_csrf.status_code == 400
    assert forbidden.status_code == 404
    assert deleted.status_code == 200
    assert deleted.get_json() == {
        "deleted": True,
        "conversation_id": owned["id"],
    }


def test_rename_conversation_requires_csrf_and_preserves_ownership():
    database = Database.for_testing()
    database.initialize()
    owned = database.create_conversation("authenticated-user", "Original")
    other = database.create_conversation("other-user", "Other")
    application = create_app(database=database, adapter=FakeAdapter("answer"))
    client = authenticated_client(application)

    missing_csrf = client.put(
        f"/api/conversations/{owned['id']}", json={"title": "Renamed"}
    )
    forbidden = client.put(
        f"/api/conversations/{other['id']}",
        json={"title": "Renamed"},
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )
    renamed = client.put(
        f"/api/conversations/{owned['id']}",
        json={"title": "  Renamed  "},
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )
    renamed_again = client.put(
        f"/api/conversations/{owned['id']}",
        json={"title": "Renamed"},
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )

    assert missing_csrf.status_code == 400
    assert forbidden.status_code == 404
    assert renamed.status_code == 200
    assert renamed.get_json()["id"] == owned["id"]
    assert renamed.get_json()["title"] == "Renamed"
    assert renamed_again.status_code == 200
    assert renamed_again.get_json()["title"] == "Renamed"
    assert database.list_conversations("authenticated-user")[0]["title"] == "Renamed"


def test_delete_conversation_discards_its_attachment_tokens():
    from attachment_service import AttachmentService, AttachmentStore

    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("authenticated-user", "Owned")
    store = AttachmentStore()
    token = store.put(
        "authenticated-user", conversation["id"], "notes.txt", "text/plain", "private"
    )
    application = create_app(
        database=database,
        adapter=FakeAdapter("answer"),
        attachment_service=AttachmentService(store=store),
    )
    client = authenticated_client(application)

    deleted = client.delete(
        f"/api/conversations/{conversation['id']}",
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )

    assert deleted.status_code == 200
    assert store.resolve("authenticated-user", conversation["id"], [token]) == []


def test_model_presets_are_csrf_protected_and_user_scoped(monkeypatch):
    monkeypatch.setattr(app_module, "ModelCatalogService", PublicBoundaryCatalog)
    database = Database.for_testing()
    database.initialize()
    application = create_app(database=database, adapter=FakeAdapter("unused"))
    client = authenticated_client(application)
    payload = {
        "name": "research",
        "models": [
            {
                "model_id": "deepseek/deepseek-v3",
                "context_mode": "smart",
                "message_ids": [],
            }
        ],
    }

    assert client.post("/api/model-presets", json=payload).status_code == 400
    created = client.post(
        "/api/model-presets",
        json=payload,
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    )
    assert created.status_code == 201
    preset_id = created.get_json()["id"]
    saved_preset = client.get("/api/model-presets").get_json()[0]
    assert saved_preset["id"] == preset_id
    assert saved_preset["items"][0]["model_id"] == "deepseek/deepseek-v3"
    assert database.list_model_presets("authenticated-user")[0]["items"][0][
        "model_id"
    ] == "gateway_main:deepseek-v3"
    assert client.delete(f"/api/model-presets/{preset_id}").status_code == 400
    assert client.delete(
        f"/api/model-presets/{preset_id}",
        headers={"X-CSRF-Token": csrf(client, "/workspace")},
    ).status_code == 204


def test_conversation_messages_never_expose_private_model_ids(monkeypatch):
    monkeypatch.setattr(app_module, "ModelCatalogService", PublicBoundaryCatalog)
    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("authenticated-user")
    database.add_collaboration_message(
        "assistant",
        "private reply",
        model="gateway_main:deepseek-v3",
        conversation_id=conversation["id"],
    )
    application = create_app(database=database, adapter=FakeAdapter("unused"))

    response = authenticated_client(application).get(
        f"/api/conversations/{conversation['id']}/messages"
    )

    assert response.status_code == 200
    assert response.get_json()[0]["model"] == "deepseek/deepseek-v3"
    assert "gateway_main" not in response.get_data(as_text=True).lower()


def authenticated_email_client(app, version=0):
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(
            username="user-1",
            email="alice@example.com",
            session_version=version,
        )
    return client


def response_body_without_csrf(response):
    return re.sub(
        r'name="_csrf_token" value="[^"]+"',
        'name="_csrf_token" value="<token>"',
        response.get_data(as_text=True),
    )


def csrf(client, path="/login"):
    response = client.get(path)
    assert response.status_code == 200
    with client.session_transaction() as current_session:
        return current_session["csrf_token"]


def administrator_client(app, username="admin-user"):
    users = app.extensions["user_database"]
    if isinstance(users, FakeUserDatabase):
        users.principals.setdefault(
            username,
            {"username": username, "access_tier": "owner", "policy_version": 1},
        )
    elif users.get_admin_principal(username) is None:
        users.ensure_owner(username, "test-owner-password")
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session["username"] = username
        current_session["is_admin"] = True
    return client


def install_admin_users_template(app):
    app.jinja_loader = DictLoader(
        {
            "admin_users.html": (
                "{{ current_username }}:{{ current_principal['access_tier'] }}|"
                "{% for user in users %}"
                "{{ user['username'] }}:{{ user['role'] }}:{{ user['is_active'] }};"
                "{% endfor %}"
            )
        }
    )


def test_login_rejects_a_post_without_a_csrf_token():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = app.test_client().post(
        "/login", data={"username": "named-user", "password": SYNTHETIC_CREDENTIAL_05}
    )

    assert response.status_code == 400


def test_login_uses_a_session_bound_csrf_token_and_clears_privileged_session_state():
    users = FakeUserDatabase(
        email_identity={
            "username": "named-user",
            "email": "named@example.com",
            "session_version": 3,
        },
        email_password=SYNTHETIC_CREDENTIAL_05,
    )
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = app.test_client()
    token = csrf(client)
    with client.session_transaction() as current_session:
        current_session["is_admin"] = True

    response = client.post(
        "/login",
        data={"email": "named@example.com", "password": SYNTHETIC_CREDENTIAL_05, "_csrf_token": token},
    )

    assert response.status_code == 302
    with client.session_transaction() as current_session:
        assert current_session["username"] == "named-user"
        assert current_session["email"] == "named@example.com"
        assert current_session["session_version"] == 3
        assert "is_admin" not in current_session
        assert "csrf_token" not in current_session


def test_admin_login_authenticates_an_active_administrator_and_sets_admin_session():
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = app.test_client()

    response = client.post(
        "/admin/login",
        data={
            "username": " admin-user ",
            "password": SYNTHETIC_CREDENTIAL_05,
            "_csrf_token": csrf(client, "/admin/login"),
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/users")
    with client.session_transaction() as current_session:
        assert current_session["username"] == "admin-user"
        assert current_session["is_admin"] is True
        assert "csrf_token" not in current_session


def test_restricted_administrator_can_login_but_cannot_use_owner_routes():
    users = FakeUserDatabase()
    users.create_user("operator", SYNTHETIC_CREDENTIAL_05)
    users.roles["operator"] = "admin"
    users.principals["operator"] = {
        "username": "operator",
        "access_tier": "admin",
        "policy_version": 1,
    }
    application = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = administrator_client(application, username="operator")

    assert client.get("/admin/users").status_code == 403
    assert client.post(
        "/api/admin/models/catalog/refresh",
        headers={"X-CSRF-Token": csrf(client, "/admin/login")},
    ).status_code == 403


def test_administrator_role_without_a_principal_cannot_use_owner_routes():
    users = FakeUserDatabase()
    users.create_user("legacy-admin", SYNTHETIC_CREDENTIAL_05)
    users.roles["legacy-admin"] = "admin"
    application = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = application.test_client()
    with client.session_transaction() as current_session:
        current_session["username"] = "legacy-admin"
        current_session["is_admin"] = True

    assert client.get("/admin/users").status_code == 403


def test_owner_can_use_every_existing_administrator_route():
    users = FakeUserDatabase()
    users.create_user("owner", SYNTHETIC_CREDENTIAL_05)
    users.roles["owner"] = "admin"
    users.principals["owner"] = {
        "username": "owner",
        "access_tier": "owner",
        "policy_version": 1,
    }
    application = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )

    assert administrator_client(application, username="owner").get(
        "/admin/users"
    ).status_code == 200


def test_email_backed_administrator_login_session_is_revoked_after_an_email_password_reset():
    users = UserDatabase.for_testing()
    users.initialize()
    registration = users.prepare_email_registration("admin@example.com", SYNTHETIC_CREDENTIAL_05)
    identity = users.mark_email_verified("admin@example.com")
    users.ensure_owner(registration["username"], "unused-password")
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = app.test_client()

    login_response = client.post(
        "/admin/login",
        data={
            "username": identity["username"],
            "password": SYNTHETIC_CREDENTIAL_05,
            "_csrf_token": csrf(client, "/admin/login"),
        },
    )

    assert login_response.status_code == 302
    assert client.get("/admin/users").status_code == 200
    assert users.reset_password_by_email("admin@example.com", "new-password") is True

    response = client.get("/admin/users")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")
    with client.session_transaction() as current_session:
        assert dict(current_session) == {}


def test_reset_after_administrator_authentication_snapshot_cannot_mint_a_current_session():
    class ResetAfterSnapshotDatabase(UserDatabase):
        def _reset_after_authentication(self, identity):
            if identity is not None:
                assert self.reset_password_by_email("admin@example.com", SYNTHETIC_CREDENTIAL_03) is True
            return identity

        def verify_user(self, username, password, require_admin=False):
            verified = super().verify_user(username, password, require_admin=require_admin)
            if verified:
                self._reset_after_authentication({"username": username})
            return verified

        def authenticate_administrator(self, username, password):
            return self._reset_after_authentication(
                super().authenticate_administrator(username, password)
            )

    users = ResetAfterSnapshotDatabase.for_testing()
    users.initialize()
    account = users.prepare_email_registration("admin@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("admin@example.com")
    assert users.set_role(account["username"], "admin") is True
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = app.test_client()

    login_response = client.post(
        "/admin/login",
        data={
            "username": account["username"],
            "password": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(client, "/admin/login"),
        },
    )

    assert login_response.status_code == 302
    protected_response = client.get("/admin/users")
    assert protected_response.status_code == 302
    assert protected_response.headers["Location"].endswith("/admin/login")
    with client.session_transaction() as current_session:
        assert dict(current_session) == {}


def test_admin_login_rejects_an_ordinary_active_user_with_the_generic_credential_error():
    users = FakeUserDatabase()
    assert users.create_user("ordinary-user", SYNTHETIC_CREDENTIAL_05) is True
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = app.test_client()

    response = client.post(
        "/admin/login",
        data={
            "username": "ordinary-user",
            "password": SYNTHETIC_CREDENTIAL_05,
            "_csrf_token": csrf(client, "/admin/login"),
        },
    )

    assert response.status_code == 401
    assert app_module.INVALID_LOGIN_ERROR.encode() in response.data
    with client.session_transaction() as current_session:
        assert "is_admin" not in current_session


def test_admin_login_hides_user_store_exceptions_behind_the_generic_credential_error():
    class FailingUserDatabase(FakeUserDatabase):
        def verify_user(self, username, password, require_admin=False):
            raise RuntimeError("distinctive administrator-store failure")

    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=FailingUserDatabase()
    )
    client = app.test_client()

    response = client.post(
        "/admin/login",
        data={
            "username": "admin-user",
            "password": SYNTHETIC_CREDENTIAL_05,
            "_csrf_token": csrf(client, "/admin/login"),
        },
    )

    assert response.status_code == 401
    assert app_module.INVALID_LOGIN_ERROR.encode() in response.data
    assert b"distinctive administrator-store failure" not in response.data


def test_admin_page_redirects_normal_users_and_normal_users_cannot_mutate_accounts():
    users = FakeUserDatabase()
    assert users.create_user("ordinary-user", SYNTHETIC_CREDENTIAL_05) is True
    assert users.create_user("target-user", "target-password") is True
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = authenticated_client(app)

    page_response = client.get("/admin/users")
    mutation_response = client.post(
        "/admin/users/target-user/active", data={"is_active": "false"}
    )

    assert page_response.status_code == 302
    assert page_response.headers["Location"].endswith("/admin/login")
    assert mutation_response.status_code == 302
    assert mutation_response.headers["Location"].endswith("/admin/login")
    assert users.active["target-user"] is True


def test_admin_users_lists_accounts_for_the_logged_in_administrator():
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    assert users.create_user("member-user", "member-password") is True
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    install_admin_users_template(app)

    response = administrator_client(app).get("/admin/users")

    assert response.status_code == 200
    assert response.data == b"admin-user:owner|admin-user:admin:True;member-user:user:True;"


def test_admin_users_hides_store_failures_behind_a_generic_error():
    class FailingUserDatabase(FakeUserDatabase):
        def list_users(self):
            raise RuntimeError("distinctive account-list failure")

    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FailingUserDatabase(),
    )
    install_admin_users_template(app)

    response = administrator_client(app).get("/admin/users")

    assert response.status_code == 500
    assert app_module.GENERIC_ERROR.encode() in response.data
    assert b"distinctive account-list failure" not in response.data


def test_admin_can_update_another_accounts_active_state_role_and_password():
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    assert users.create_user("target-user", "old-password") is True
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = administrator_client(app)
    token = csrf(client)

    disabled = client.post(
        "/admin/users/%20target-user%20/active",
        data={"is_active": "false", "_csrf_token": token},
    )
    promoted = client.post(
        "/admin/users/target-user/role",
        data={"role": "admin", "_csrf_token": token},
    )
    password_reset = client.post(
        "/admin/users/target-user/password",
        data={"password": "new-pass9", "_csrf_token": token},
    )

    assert disabled.status_code == 302
    assert promoted.status_code == 302
    assert password_reset.status_code == 302
    assert users.active["target-user"] is False
    assert users.roles["target-user"] == "admin"
    assert users.users["target-user"] == "new-pass9"


def test_admin_password_reset_rejects_invalid_lengths_and_revokes_a_versioned_session():
    users = UserDatabase.for_testing()
    users.initialize()
    account = users.prepare_email_registration("target@example.com", SYNTHETIC_CREDENTIAL_02)
    identity = users.mark_email_verified("target@example.com")
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    admin = administrator_client(app)
    user = app.test_client()
    with user.session_transaction() as current_session:
        current_session.update(identity)

    for invalid_password in ("12345678", "1234567890123"):
        response = admin.post(
            f"/admin/users/{account['username']}/password",
            data={"password": invalid_password, "_csrf_token": csrf(admin)},
        )
        assert response.status_code == 400

    assert user.get("/workspace").status_code == 200

    response = admin.post(
        f"/admin/users/{account['username']}/password",
        data={"password": "new-pass9", "_csrf_token": csrf(admin)},
    )

    assert response.status_code == 302
    stale_response = user.get("/workspace")
    assert stale_response.status_code == 302
    assert stale_response.headers["Location"].endswith("/login?next=/workspace")
    assert users.verify_email_password("target@example.com", SYNTHETIC_CREDENTIAL_02) is None
    assert users.verify_email_password("target@example.com", "new-pass9") is not None


@pytest.mark.parametrize(
    ("path", "form"),
    [
        ("active", {"is_active": "false"}),
        ("role", {"role": "user"}),
        ("password", {"password": "replacement"}),
    ],
)
def test_admin_cannot_modify_its_own_account(path, form):
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = administrator_client(app)

    response = client.post(
        f"/admin/users/admin-user/{path}", data={**form, "_csrf_token": csrf(client)}
    )

    assert response.status_code == 400
    assert users.active["admin-user"] is True
    assert users.roles["admin-user"] == "admin"
    assert users.users["admin-user"] == SYNTHETIC_CREDENTIAL_05


@pytest.mark.parametrize(
    ("path", "form"),
    [
        ("active", {"is_active": "invalid"}),
        ("role", {"role": "owner"}),
        ("password", {"password": "   "}),
    ],
)
def test_admin_mutations_reject_invalid_form_values(path, form):
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    assert users.create_user("target-user", "target-password") is True
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = administrator_client(app)

    response = client.post(
        f"/admin/users/target-user/{path}", data={**form, "_csrf_token": csrf(client)}
    )

    assert response.status_code == 400


@pytest.mark.parametrize("target", ["missing-user", "x" * 65, "%20%20%20"])
def test_admin_mutations_return_not_found_or_bad_request_for_invalid_targets(target):
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = administrator_client(app)

    response = client.post(
        f"/admin/users/{target}/active",
        data={"is_active": "false", "_csrf_token": csrf(client)},
    )

    assert response.status_code == (404 if target == "missing-user" else 400)


@pytest.mark.parametrize("token_kind", ["missing", "mismatched"])
def test_admin_mutations_reject_missing_and_nonempty_mismatched_csrf_tokens(token_kind):
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    assert users.create_user("target-user", "target-password") is True
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = administrator_client(app)
    token = csrf(client)
    form = {"is_active": "false"}
    if token_kind == "mismatched":
        form["_csrf_token"] = token + "mismatch"

    response = client.post("/admin/users/target-user/active", data=form)

    assert response.status_code == 400
    assert users.active["target-user"] is True


@pytest.mark.parametrize(
    ("path", "form", "method", "operation"),
    [
        ("active", {"is_active": "false"}, "set_active", "set account active state"),
        ("role", {"role": "admin"}, "set_role", "set account role"),
        ("password", {"password": "replacement"}, "reset_password", "reset account password"),
    ],
)
def test_admin_mutations_hide_store_failures_with_route_specific_logging(
    path, form, method, operation, caplog
):
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    assert users.create_user("target-user", "target-password") is True

    def raise_store_error(*args):
        raise RuntimeError("distinctive account-update failure")

    setattr(users, method, raise_store_error)
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = administrator_client(app)

    response = client.post(
        f"/admin/users/target-user/{path}",
        data={**form, "_csrf_token": csrf(client)},
    )

    assert response.status_code == 500
    assert app_module.GENERIC_ERROR.encode() in response.data
    assert b"distinctive account-update failure" not in response.data
    assert any(operation in record.getMessage() for record in caplog.records)


def test_login_hides_user_store_exceptions():
    class FailingUserDatabase(FakeUserDatabase):
        def verify_email_password(self, email, password):
            raise RuntimeError("distinctive user-store failure")

    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=FailingUserDatabase()
    )
    client = app.test_client()

    response = client.post(
        "/login",
        data={"email": "named@example.com", "password": SYNTHETIC_CREDENTIAL_05, "_csrf_token": csrf(client)},
    )

    assert response.status_code == 500
    assert app_module.GENERIC_ERROR.encode() in response.data
    assert b"distinctive user-store failure" not in response.data


def test_registration_sends_code_without_logging_in_until_email_is_verified():
    service = FakeEmailAuthService()
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )
    client = app.test_client()

    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": SYNTHETIC_CREDENTIAL_02,
            "password_confirm": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(client, "/register"),
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/verify-email")
    assert service.registration_requests == [("alice@example.com", SYNTHETIC_CREDENTIAL_02)]
    with client.session_transaction() as current_session:
        assert "username" not in current_session
        assert current_session["pending_email"] == "alice@example.com"
        assert current_session["pending_registration_attempt_id"] == "attempt-1"


def test_registration_verification_rejects_a_code_from_a_different_browser_attempt(monkeypatch):
    class RecordingSender:
        def __init__(self):
            self.sent = []

        def send_verification_code(self, email, code, purpose):
            self.sent.append((email, code, purpose))

    users = UserDatabase.for_testing()
    users.initialize()
    sender = RecordingSender()
    service = EmailAuthService(users, sender, pepper="test-pepper")
    start = datetime(2026, 7, 22, 12, 0, 0)
    times = iter(
        (
            start,
            start + timedelta(seconds=61),
            start + timedelta(seconds=62),
            start + timedelta(seconds=63),
        )
    )
    monkeypatch.setattr(email_auth_module, "_utc_now", lambda: next(times))
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=users,
        email_auth_service=service,
    )
    original_browser = app.test_client()
    attacker_browser = app.test_client()

    original_browser.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": SYNTHETIC_CREDENTIAL_02,
            "password_confirm": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(original_browser, "/register"),
        },
    )
    attacker_browser.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": SYNTHETIC_CREDENTIAL_03,
            "password_confirm": SYNTHETIC_CREDENTIAL_03,
            "_csrf_token": csrf(attacker_browser, "/register"),
        },
    )
    with original_browser.session_transaction() as current_session:
        original_attempt_id = current_session["pending_registration_attempt_id"]
        assert SYNTHETIC_CREDENTIAL_02 not in repr(dict(current_session))
    with attacker_browser.session_transaction() as current_session:
        attacker_attempt_id = current_session["pending_registration_attempt_id"]
        assert SYNTHETIC_CREDENTIAL_03 not in repr(dict(current_session))
    assert original_attempt_id != attacker_attempt_id
    latest_code = sender.sent[-1][1]

    response = original_browser.post(
        "/verify-email",
        data={
            "code": latest_code,
            "_csrf_token": csrf(original_browser, "/verify-email"),
        },
    )

    assert response.status_code == 400
    assert users.verify_email_password("alice@example.com", SYNTHETIC_CREDENTIAL_03) is None

    matching_response = attacker_browser.post(
        "/verify-email",
        data={
            "code": latest_code,
            "_csrf_token": csrf(attacker_browser, "/verify-email"),
        },
    )

    assert matching_response.status_code == 302
    assert users.verify_email_password("alice@example.com", SYNTHETIC_CREDENTIAL_02) is None
    assert users.verify_email_password("alice@example.com", SYNTHETIC_CREDENTIAL_03) is not None


@pytest.mark.parametrize(
    ("setting", "invalid_value"),
    [
        ("SMTP_HOST", ""),
        ("SMTP_FROM_EMAIL", "not-an-email"),
        ("EMAIL_CODE_PEPPER", "x" * 31),
        ("EMAIL_CODE_PEPPER", "x" * 32),
        ("EMAIL_CODE_PEPPER", "replace-with-a-real-email-code-pepper"),
        ("EMAIL_CODE_PEPPER", "REPLACE_WITH_A_LONG_RANDOM_SECRET"),
        ("EMAIL_CODE_PEPPER", "01234567" * 4),
    ],
)
def test_unconfigured_production_email_service_refuses_registration_before_account_creation(
    monkeypatch, caplog, setting, invalid_value
):
    class RegistrationStoreMustNotBeUsed(FakeUserDatabase):
        def __init__(self):
            super().__init__()
            self.registration_attempted = False

        def prepare_email_registration(self, email, password):
            self.registration_attempted = True
            raise AssertionError("unconfigured email service must not create an account")

    monkeypatch.setattr(app_module.config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(app_module.config, "SMTP_FROM_EMAIL", "no-reply@example.com")
    monkeypatch.setattr(
        app_module.config, "EMAIL_CODE_PEPPER", "f8Z!k2P#v7M@q4R$y9T%w3X^n6C&d1L*"
    )
    monkeypatch.setattr(app_module.config, setting, invalid_value)
    users = RegistrationStoreMustNotBeUsed()
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users)
    client = app.test_client()

    registration = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": SYNTHETIC_CREDENTIAL_02,
            "password_confirm": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(client, "/register"),
        },
    )

    assert client.get("/test").status_code == 200
    assert registration.status_code == 503
    assert "邮箱服务尚未配置，请联系管理员".encode() in registration.data
    assert users.registration_attempted is False
    assert any(
        record.getMessage() == "Email registration service is unavailable"
        for record in caplog.records
    )


def test_injected_email_service_bypasses_unavailable_production_email_configuration(monkeypatch):
    monkeypatch.setattr(app_module.config, "SMTP_HOST", "")
    monkeypatch.setattr(app_module.config, "SMTP_FROM_EMAIL", "")
    monkeypatch.setattr(app_module.config, "EMAIL_CODE_PEPPER", "")
    service = FakeEmailAuthService()
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )
    client = app.test_client()

    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": SYNTHETIC_CREDENTIAL_02,
            "password_confirm": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(client, "/register"),
        },
    )

    assert response.status_code == 302
    assert service.registration_requests == [("alice@example.com", SYNTHETIC_CREDENTIAL_02)]


def test_unconfigured_production_email_service_refuses_pending_verification(monkeypatch):
    monkeypatch.setattr(app_module.config, "SMTP_HOST", "")
    monkeypatch.setattr(app_module.config, "SMTP_FROM_EMAIL", "")
    monkeypatch.setattr(app_module.config, "EMAIL_CODE_PEPPER", "")
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=FakeUserDatabase()
    )
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session["pending_email"] = "alice@example.com"
        current_session["pending_registration_attempt_id"] = "attempt-1"

    response = client.post(
        "/verify-email",
        data={"code": "123456", "_csrf_token": csrf(client, "/verify-email")},
    )

    assert response.status_code == 503
    assert "邮箱服务尚未配置，请联系管理员".encode() in response.data
    with client.session_transaction() as current_session:
        assert current_session["pending_email"] == "alice@example.com"


def test_email_verification_activates_account_and_logs_user_in():
    service = FakeEmailAuthService(
        identity={"username": "user-1", "email": "alice@example.com", "session_version": 0}
    )
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session["pending_email"] = "alice@example.com"
        current_session["pending_registration_attempt_id"] = "attempt-1"

    response = client.post(
        "/verify-email",
        data={"code": "123456", "_csrf_token": csrf(client, "/verify-email")},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/workspace")
    assert service.verification_requests == [
        ("alice@example.com", "attempt-1", "123456")
    ]
    with client.session_transaction() as current_session:
        assert current_session["username"] == "user-1"
        assert current_session["email"] == "alice@example.com"
        assert current_session["session_version"] == 0


def test_failed_email_verification_preserves_pending_email():
    service = FakeEmailAuthService(verification_error=ValueError("验证码无效或已过期"))
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session["pending_email"] = "alice@example.com"
        current_session["pending_registration_attempt_id"] = "attempt-1"

    response = client.post(
        "/verify-email",
        data={"code": "123456", "_csrf_token": csrf(client, "/verify-email")},
    )

    assert response.status_code == 400
    with client.session_transaction() as current_session:
        assert current_session["pending_email"] == "alice@example.com"
        assert "username" not in current_session


def test_user_login_accepts_email_and_stores_versioned_identity():
    users = FakeUserDatabase(
        email_identity={
            "username": "user-1",
            "email": "alice@example.com",
            "session_version": 2,
        }
    )
    app = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("ok"),
        email_auth_service=FakeEmailAuthService(),
    )
    client = app.test_client()

    response = client.post(
        "/login",
        data={
            "email": "alice@example.com",
            "password": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(client),
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/workspace")
    assert users.email_verify_calls == [("alice@example.com", SYNTHETIC_CREDENTIAL_02)]
    with client.session_transaction() as current_session:
        assert current_session["username"] == "user-1"
        assert current_session["email"] == "alice@example.com"
        assert current_session["session_version"] == 2
        assert "is_admin" not in current_session


def test_admin_login_remains_username_based_after_ordinary_login_moves_to_email():
    users = FakeUserDatabase()
    assert users.create_user("admin-user", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["admin-user"] = "admin"
    app = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("ok"),
        email_auth_service=FakeEmailAuthService(),
    )
    client = app.test_client()

    response = client.post(
        "/admin/login",
        data={
            "username": "admin-user",
            "password": SYNTHETIC_CREDENTIAL_05,
            "_csrf_token": csrf(client, "/admin/login"),
        },
    )

    assert response.status_code == 302
    assert users.verify_calls == [("admin-user", SYNTHETIC_CREDENTIAL_05)]
    assert users.email_verify_calls == []


def test_reset_code_api_requires_csrf_and_returns_safe_response():
    service = FakeEmailAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(),
        adapter=FakeAdapter("ok"),
        email_auth_service=service,
    )
    client = application.test_client()

    denied = client.post(
        "/api/auth/password-reset-code", json={"email": "missing@example.com"}
    )
    allowed = client.post(
        "/api/auth/password-reset-code",
        json={"email": "missing@example.com"},
        headers={"X-CSRF-Token": csrf(client, "/reset-password")},
    )

    assert denied.status_code == 400
    assert allowed.status_code == 200
    assert allowed.get_json()["retry_after_seconds"] == 60
    assert service.password_reset_requests == ["missing@example.com"]


def test_reset_code_api_returns_retry_after_for_email_cooldown():
    service = FakeEmailAuthService(
        password_reset_request_error=EmailCodeRateLimitError(41)
    )
    application = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(),
        adapter=FakeAdapter("ok"),
        email_auth_service=service,
    )
    client = application.test_client()

    response = client.post(
        "/api/auth/password-reset-code",
        json={"email": "alice@example.com"},
        headers={"X-CSRF-Token": csrf(client, "/reset-password")},
    )

    assert response.status_code == 429
    assert response.get_json()["retry_after_seconds"] == 41


def test_forgot_password_response_is_exactly_enumeration_safe_for_every_outcome(caplog):
    generic_message = app_module.PASSWORD_RESET_REQUEST_MESSAGE
    outcomes = (
        ("known@example.com", FakeEmailAuthService()),
        ("missing@example.com", FakeEmailAuthService()),
        ("not an email", FakeEmailAuthService(password_reset_request_error=ValueError("invalid email"))),
        ("limited@example.com", FakeEmailAuthService(password_reset_request_error=ValueError("rate limited"))),
        ("smtp@example.com", FakeEmailAuthService(password_reset_request_error=RuntimeError("smtp delivery for smtp@example.com failed"))),
        ("unavailable@example.com", None),
    )
    responses = []
    recorded_messages = []
    caplog.set_level(logging.WARNING)
    for email, service in outcomes:
        app = create_app(
            database=FakeDatabase(),
            user_database=FakeUserDatabase(),
            adapter=FakeAdapter("ok"),
            email_auth_service=service,
        )
        client = app.test_client()
        caplog.clear()
        response = client.post(
            "/forgot-password",
            data={"email": email, "_csrf_token": csrf(client, "/forgot-password")},
        )
        assert response.status_code == 200
        assert generic_message in response.get_data(as_text=True)
        responses.append(response_body_without_csrf(response))
        recorded_messages.extend(record.getMessage() for record in caplog.records)

    assert responses.count(responses[0]) == len(responses)
    assert "smtp delivery for smtp@example.com failed" not in "\n".join(recorded_messages)
    assert "not an email" not in "\n".join(recorded_messages)
    recovery_messages = [
        message for message in recorded_messages if not message.startswith("[WARN] Model")
    ]
    assert all(
        message in {"Password reset request was not delivered", "Email registration service is unavailable"}
        for message in recovery_messages
    )


def test_forgot_password_queues_reset_work_without_calling_the_service_in_the_request():
    queued_tasks = []
    service = FakeEmailAuthService()
    app = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(),
        adapter=FakeAdapter("ok"),
        email_auth_service=service,
        password_reset_dispatcher=queued_tasks.append,
    )
    client = app.test_client()

    response = client.post(
        "/forgot-password",
        data={"email": "alice@example.com", "_csrf_token": csrf(client, "/forgot-password")},
    )

    assert response.status_code == 200
    assert service.password_reset_requests == []
    assert len(queued_tasks) == 1

    queued_tasks.pop()()

    assert service.password_reset_requests == ["alice@example.com"]


def test_forgot_password_dispatcher_bounds_workers_and_queue_while_response_stays_generic():
    class BlockingPasswordResetService(FakeEmailAuthService):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.peak_active = 0
            self.started_one = threading.Event()
            self.started_two = threading.Event()
            self.release = threading.Event()
            self.completed_three = threading.Event()
            self.lock = threading.Lock()

        def request_password_reset(self, email):
            with self.lock:
                self.password_reset_requests.append(email)
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
                if self.active == 1:
                    self.started_one.set()
                if self.active == 2:
                    self.started_two.set()
            self.release.wait(timeout=5)
            with self.lock:
                self.active -= 1
                if len(self.password_reset_requests) == 3 and self.active == 0:
                    self.completed_three.set()

    service = BlockingPasswordResetService()
    dispatcher = app_module.PasswordResetDispatcher(worker_count=2, queue_capacity=1)
    app = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(),
        adapter=FakeAdapter("ok"),
        email_auth_service=service,
        password_reset_dispatcher=dispatcher,
    )
    client = app.test_client()
    token = csrf(client, "/forgot-password")

    responses = [client.post(
        "/forgot-password",
        data={"email": "user-0@example.com", "_csrf_token": token},
    )]
    assert service.started_one.wait(timeout=2)
    responses.append(client.post(
        "/forgot-password",
        data={"email": "user-1@example.com", "_csrf_token": token},
    ))
    assert service.started_two.wait(timeout=2)
    responses.extend(
        client.post(
            "/forgot-password",
            data={"email": f"user-{index}@example.com", "_csrf_token": token},
        )
        for index in range(2, 4)
    )

    assert all(response.status_code == 200 for response in responses)
    bodies = [response_body_without_csrf(response) for response in responses]
    assert bodies.count(bodies[0]) == len(bodies)
    with service.lock:
        assert len(service.password_reset_requests) == 2
        assert service.peak_active == 2

    service.release.set()
    assert service.completed_three.wait(timeout=2)
    with service.lock:
        assert len(service.password_reset_requests) == 3
        assert service.peak_active == 2


def test_password_reset_dispatcher_logs_only_a_constant_message_for_worker_exceptions():
    class RecordingLogger:
        def __init__(self):
            self.messages = []
            self.logged = threading.Event()

        def warning(self, message):
            self.messages.append(message)
            self.logged.set()

    logger = RecordingLogger()
    dispatcher = app_module.PasswordResetDispatcher(
        worker_count=1, queue_capacity=1, logger=logger
    )

    def fail_with_sensitive_detail():
        raise RuntimeError("account@example.com smtp detail")

    assert dispatcher(fail_with_sensitive_detail) is True
    assert logger.logged.wait(timeout=2)
    assert logger.messages == ["Password reset request was not delivered"]


def test_reset_password_clears_current_session_after_a_valid_reset():
    service = FakeEmailAuthService()
    app = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(),
        adapter=FakeAdapter("ok"),
        email_auth_service=service,
    )
    client = authenticated_email_client(app, version=0)

    response = client.post(
        "/reset-password",
        data={
            "email": "alice@example.com",
            "code": "123456",
            "password": SYNTHETIC_CREDENTIAL_03,
            "password_confirm": SYNTHETIC_CREDENTIAL_03,
            "_csrf_token": csrf(client, "/reset-password"),
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?reset=success")
    assert service.password_reset_calls == [("alice@example.com", "123456", SYNTHETIC_CREDENTIAL_03)]
    with client.session_transaction() as current_session:
        assert "email" not in current_session
        assert "session_version" not in current_session


def test_reset_password_requires_matching_passwords_without_calling_the_service():
    service = FakeEmailAuthService()
    app = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(),
        adapter=FakeAdapter("ok"),
        email_auth_service=service,
    )
    client = app.test_client()

    response = client.post(
        "/reset-password",
        data={
            "email": "alice@example.com",
            "code": "123456",
            "password": SYNTHETIC_CREDENTIAL_03,
            "password_confirm": "different",
            "_csrf_token": csrf(client, "/reset-password"),
        },
    )

    assert response.status_code == 400
    assert service.password_reset_calls == []


def test_reset_password_hides_unexpected_service_details(caplog):
    service = FakeEmailAuthService(
        password_reset_error=RuntimeError("distinctive reset database failure")
    )
    app = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(),
        adapter=FakeAdapter("ok"),
        email_auth_service=service,
    )
    client = app.test_client()

    response = client.post(
        "/reset-password",
        data={
            "email": "alice@example.com",
            "code": "123456",
            "password": SYNTHETIC_CREDENTIAL_03,
            "password_confirm": SYNTHETIC_CREDENTIAL_03,
            "_csrf_token": csrf(client, "/reset-password"),
        },
    )

    assert response.status_code == 500
    assert app_module.GENERIC_ERROR.encode() in response.data
    assert b"distinctive reset database failure" not in response.data
    assert all("distinctive reset database failure" not in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("token_kind", ["missing", "mismatched"])
def test_email_verification_rejects_invalid_csrf_without_consuming_pending_email(token_kind):
    service = FakeEmailAuthService()
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session["pending_email"] = "alice@example.com"
        current_session["pending_registration_attempt_id"] = "attempt-1"
    form = {"code": "123456"}
    if token_kind == "mismatched":
        form["_csrf_token"] = csrf(client, "/verify-email") + "mismatch"

    response = client.post("/verify-email", data=form)

    assert response.status_code == 400
    assert service.verification_requests == []
    with client.session_transaction() as current_session:
        assert current_session["pending_email"] == "alice@example.com"


def test_unexpected_email_verification_failure_keeps_pending_flow_and_hides_details(caplog):
    service = FakeEmailAuthService(
        verification_error=RuntimeError("distinctive SMTP verification failure")
    )
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session["pending_email"] = "alice@example.com"
        current_session["pending_registration_attempt_id"] = "attempt-1"

    response = client.post(
        "/verify-email",
        data={"code": "123456", "_csrf_token": csrf(client, "/verify-email")},
    )

    assert response.status_code == 500
    assert app_module.GENERIC_ERROR.encode() in response.data
    assert b"distinctive SMTP verification failure" not in response.data
    assert not any(
        "distinctive SMTP verification failure" in record.getMessage() for record in caplog.records
    )
    with client.session_transaction() as current_session:
        assert current_session["pending_email"] == "alice@example.com"
        assert "username" not in current_session


def test_registration_normalizes_email_preserves_password_spaces_and_rejects_overlong_email():
    service = FakeEmailAuthService()
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )
    client = app.test_client()

    registered = client.post(
        "/register",
        data={
            "email": " Alice@Example.COM ",
            "password": SYNTHETIC_CREDENTIAL_01,
            "password_confirm": SYNTHETIC_CREDENTIAL_01,
            "_csrf_token": csrf(client, "/register"),
        },
    )
    overlong_email = client.post(
        "/register",
        data={
            "email": "a" * 315 + "@x.com",
            "password": SYNTHETIC_CREDENTIAL_02,
            "password_confirm": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(client, "/register"),
        },
    )

    assert registered.status_code == 302
    assert service.registration_requests == [("alice@example.com", SYNTHETIC_CREDENTIAL_01)]
    assert overlong_email.status_code == 400


def test_logout_requires_csrf_and_clears_the_session_with_a_valid_token():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))
    client = authenticated_client(app)

    rejected = client.post("/logout")
    accepted = client.post("/logout", data={"_csrf_token": csrf(client)})

    assert rejected.status_code == 400
    assert accepted.status_code == 302
    with client.session_transaction() as current_session:
        assert "username" not in current_session



@pytest.mark.parametrize(
    ("form", "message", "registration_error"),
    [
        (
            {"email": "", "password": SYNTHETIC_CREDENTIAL_02, "password_confirm": SYNTHETIC_CREDENTIAL_02},
            "请输入有效邮箱",
            None,
        ),
        (
            {"email": "alice@example.com", "password": "short", "password_confirm": "short"},
            "密码长度必须为 9–12 位",
            ValueError("密码长度必须为 9–12 位"),
        ),
        (
            {"email": "alice@example.com", "password": SYNTHETIC_CREDENTIAL_02, "password_confirm": SYNTHETIC_CREDENTIAL_03},
            "两次密码不一致",
            None,
        ),
    ],
)
def test_register_rejects_invalid_input(form, message, registration_error):
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=FakeEmailAuthService(registration_error=registration_error),
    )

    client = app.test_client()
    response = client.post("/register", data={**form, "_csrf_token": csrf(client, "/register")})

    assert response.status_code == 400
    assert message.encode() in response.data


def test_register_renders_safe_duplicate_email_error():
    service = FakeEmailAuthService(registration_error=ValueError("邮箱已被注册"))
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )

    client = app.test_client()
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": SYNTHETIC_CREDENTIAL_02,
            "password_confirm": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(client, "/register"),
        },
    )

    assert response.status_code == 400
    assert "邮箱已被注册".encode() in response.data
    assert service.registration_requests == [("alice@example.com", SYNTHETIC_CREDENTIAL_02)]


def test_register_hides_unexpected_email_service_errors(caplog):
    service = FakeEmailAuthService(registration_error=RuntimeError("distinctive SMTP failure"))
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
        email_auth_service=service,
    )

    client = app.test_client()
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": SYNTHETIC_CREDENTIAL_02,
            "password_confirm": SYNTHETIC_CREDENTIAL_02,
            "_csrf_token": csrf(client, "/register"),
        },
    )

    assert response.status_code == 500
    assert "服务暂时不可用，请稍后再试".encode() in response.data
    assert b"distinctive SMTP failure" not in response.data
    assert not any("distinctive SMTP failure" in record.getMessage() for record in caplog.records)


def test_login_uses_the_injected_user_database_instead_of_chat_memory_database():
    class ChatMemoryDatabase(FakeDatabase):
        def verify_user(self, username, password):
            raise AssertionError("login must not use the chat-memory database")

    users = FakeUserDatabase(
        email_identity={
            "username": "named-user",
            "email": "named@example.com",
            "session_version": 0,
        },
        email_password=SYNTHETIC_CREDENTIAL_05,
    )
    app = create_app(
        database=ChatMemoryDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )

    client = app.test_client()
    response = client.post(
        "/login",
        data={
            "email": "named@example.com",
            "password": SYNTHETIC_CREDENTIAL_05,
            "_csrf_token": csrf(client),
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/workspace")
    assert users.email_verify_calls == [("named@example.com", SYNTHETIC_CREDENTIAL_05)]


@pytest.mark.parametrize("path", ["/chat", "/workspace"])
def test_anonymous_workspace_redirects_to_login_with_the_requested_destination(path):
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = app.test_client().get(path)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/login?next={path}")


def test_login_form_keeps_only_a_safe_workspace_destination():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))
    client = app.test_client()

    safe_response = client.get("/login?next=/chat")
    unsafe_response = client.get("/login?next=https://example.test/steal")

    assert b'name="next" value="/chat"' in safe_response.data
    assert b'name="next" value="/workspace"' in unsafe_response.data


def test_login_page_uses_the_dark_console_ui_contract():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))
    response = app.test_client().get("/login")

    assert response.status_code == 200
    assert b"/static/login.css" in response.data
    assert b'class="login-card"' in response.data
    assert "欢迎回来".encode() in response.data

    client = app.test_client()
    error_response = client.post(
        "/login",
        data={"email": "wrong@example.com", "password": "wrong", "_csrf_token": csrf(client)},
    )
    assert error_response.status_code == 401
    assert b'role="alert"' in error_response.data


def test_login_and_register_pages_link_to_each_other_and_use_the_login_visual_contract():
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter("answer"),
        user_database=FakeUserDatabase(),
    )
    client = app.test_client()

    login = client.get("/login")
    register = client.get("/register")

    assert b'href="/register"' in login.data
    assert b'class="login-card"' in register.data
    assert b'name="password_confirm"' in register.data
    assert "已有账号？".encode() in register.data
    assert b'href="/login">' + "去登录".encode() + b"</a>" in register.data


def test_auth_templates_render_the_csrf_hidden_field():
    for template_name in ("login.html", "register.html"):
        template = (PROJECT_ROOT / "templates" / template_name).read_text(encoding="utf-8")
        assert 'name="_csrf_token" value="{{ csrf_token() }}"' in template


def test_logout_form_renders_the_csrf_hidden_field():
    template = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert 'name="_csrf_token" value="{{ csrf_token() }}"' in template


def test_login_styles_allow_vertical_scrolling_with_44px_controls():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = app.test_client().get("/static/login.css")

    assert response.status_code == 200
    assert b"overflow-x: hidden;" in response.data
    assert b"overflow-y: hidden;" not in response.data
    assert b"min-height: 44px;" in response.data


def test_features_page_is_public_and_describes_existing_capabilities():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = app.test_client().get("/features")

    assert response.status_code == 200
    assert "多模型协作" in response.text
    assert "实时流式响应" in response.text
    assert "独立记忆空间" in response.text
    assert 'href="/chat"' in response.text


def test_verified_user_can_log_in_and_view_workspace():
    users = FakeUserDatabase(
        email_identity={
            "username": "verified-user",
            "email": "verified@example.com",
            "session_version": 0,
        },
        email_password=SYNTHETIC_CREDENTIAL_05,
    )
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = app.test_client()

    login_response = client.post(
        "/login",
        data={
            "email": "verified@example.com",
            "password": SYNTHETIC_CREDENTIAL_05,
            "_csrf_token": csrf(client),
        },
    )
    workspace_response = client.get("/workspace")

    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/workspace")
    assert workspace_response.status_code == 200
    assert b'<section id="messages"' in workspace_response.data


@pytest.mark.parametrize("destination", ["/chat", "/workspace"])
def test_verified_login_returns_to_the_safe_requested_destination(destination):
    users = FakeUserDatabase(
        email_identity={
            "username": "verified-user",
            "email": "verified@example.com",
            "session_version": 0,
        },
        email_password=SYNTHETIC_CREDENTIAL_05,
    )
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = app.test_client()

    response = client.post(
        "/login",
        data={
            "email": "verified@example.com",
            "password": SYNTHETIC_CREDENTIAL_05,
            "next": destination,
            "_csrf_token": csrf(client, f"/login?next={destination}"),
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(destination)


@pytest.mark.parametrize(
    "destination",
    [
        "https://example.test/steal",
        "//example.test/steal",
        r"\\example.test\steal",
        "/chat?model=unsafe",
        "/workspace#fragment",
        "/account/security",
        "",
    ],
)
def test_verified_login_rejects_destinations_outside_the_exact_allowlist(destination):
    users = FakeUserDatabase(
        email_identity={
            "username": "verified-user",
            "email": "verified@example.com",
            "session_version": 0,
        },
        email_password=SYNTHETIC_CREDENTIAL_05,
    )
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("answer"), user_database=users
    )
    client = app.test_client()

    response = client.post(
        "/login",
        data={
            "email": "verified@example.com",
            "password": SYNTHETIC_CREDENTIAL_05,
            "next": destination,
            "_csrf_token": csrf(client),
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/workspace")


def test_login_next_does_not_change_csrf_enforcement():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = app.test_client().post(
        "/login",
        data={"email": "verified@example.com", "password": SYNTHETIC_CREDENTIAL_05, "next": "/chat"},
    )

    assert response.status_code == 400


def test_workspace_renders_the_immersive_shell_and_assets():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = authenticated_client(app).get("/workspace")

    assert response.status_code == 200
    for expected in (
        b"app-shell",
        b"session-sidebar",
        b"chat-panel",
        b"agent-sidebar",
        b"agent-status-list",
        b"task-progress",
        b"composer",
        b"mobile-agent-toggle",
        b"mobile-session-toggle",
            b"workspace-attachment-input",
        b"/static/workspace.css",
        b"/static/workspace.js",
    ):
        assert expected in response.data
    assert b"topbar" not in response.data


def test_workspace_exposes_text_model_selection_and_task_polling_contract():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = authenticated_client(app).get("/workspace")
    script = (PROJECT_ROOT / "static" / "workspace.js").read_bytes()

    assert response.status_code == 200
    assert b'id="model-picker"' not in response.data
    assert b'id="provider-select"' in response.data
    assert b'id="model-select"' in response.data
    assert b'id="add-model"' in response.data
    assert b'<label for="provider-select"' in response.data
    assert b'<label for="model-select"' in response.data
    assert b"/static/workspace.js" in response.data
    assert b"function createModelIdentity(presentation, className)" in script
    assert b"createModelAvatar(" not in script
    assert b"function setAgentThinking(model, isThinking)" in script
    assert b"async function pollTask(taskId)" in script
    assert b"/api/tasks/" in script
    assert b"aria-busy" in script


def test_workspace_submits_selected_models_as_an_async_task_and_polls_at_500ms():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = authenticated_client(app).get("/workspace")
    script = (PROJECT_ROOT / "static" / "workspace.js").read_bytes()

    assert b"/static/workspace.js" in response.data
    assert b"models: [...selectedModels]" in script
    assert b"selected_models: [...selectedModels]" in script
    assert b"await pollTask(data.task_id)" in script
    assert b"setTimeout(resolve, 500)" in script
    assert b"\xe5\x8d\x8f\xe4\xbd\x9c\xe6\x80\xbb\xe7\xbb\x93" in script


def test_workspace_uses_task_scoped_summaries_instead_of_history_polling():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = authenticated_client(app).get("/workspace")
    helper_response = app.test_client().get("/static/task-polling.js")
    script = (PROJECT_ROOT / "static" / "workspace.js").read_bytes()

    assert b"task-polling.js" in response.data
    assert b"workspace.js" in response.data
    assert b"TaskPolling.createTaskResultTracker()" in script
    assert b"waitForSummary" not in script
    assert b"loadHistory(true" not in script
    assert helper_response.status_code == 200
    assert b"task.summary" in helper_response.data


def test_anonymous_api_chat_is_rejected_before_any_data_is_processed():
    database = FakeDatabase()
    adapter = FakeAdapter("answer")
    app = create_app(database=database, adapter=adapter)

    response = app.test_client().post("/api/chat", json={"message": "hello"})

    assert response.status_code == 401
    assert response.get_json() == {"error": "authentication required"}
    assert adapter.messages == []
    assert database.saved == []


def test_authenticated_chat_starts_a_broadcast_task_and_exposes_its_status():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))
    client = authenticated_client(app)

    response = client.post(
        "/api/chat",
        json={"message": "ask @Kimi and @DeepSeek", "selected_models": ["qwen", "doubao"]},
    )

    assert response.status_code == 202
    body = response.get_json()
    assert isinstance(body["task_id"], str)
    assert body["models"] == ["deepseek", "kimi", "qwen"]
    status_response = client.get(f"/api/tasks/{body['task_id']}")
    assert status_response.status_code == 200
    assert status_response.get_json()["models"].keys() == {"deepseek", "kimi", "qwen"}


def test_authenticated_chat_honors_the_documented_models_payload_field():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = authenticated_client(app).post(
        "/api/chat", json={"message": "ask Kimi", "models": ["kimi"]}
    )

    assert response.status_code == 202
    assert response.get_json()["models"] == ["kimi"]


def test_logout_clears_the_session():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))
    client = authenticated_client(app)

    response = client.post("/logout", data={"_csrf_token": csrf(client)})
    workspace_response = client.get("/workspace")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert workspace_response.status_code == 302
    assert workspace_response.headers["Location"].endswith("/login?next=/workspace")


def test_chat_sends_current_message_once_with_prior_history():
    prior_history = [{"role": "assistant", "content": "earlier"}]
    database = FakeDatabase(prior_history)
    adapter = HistoryAwareAdapter("answer")
    app = create_app(database=database, adapter=adapter)

    response = authenticated_client(app).post("/api/chat", json={"message": "hello"})

    assert response.status_code == 202


def test_chat_ignores_an_unanswered_previous_user_message_in_context():
    prior_history = [
        {"role": "user", "content": "completed question"},
        {"role": "assistant", "content": "completed answer"},
        {"role": "user", "content": "timed out question"},
    ]
    database = FakeDatabase(prior_history)
    adapter = HistoryAwareAdapter("answer")
    app = create_app(database=database, adapter=adapter)

    response = authenticated_client(app).post("/api/chat", json={"message": "next question"})

    assert response.status_code == 202


def test_chat_routes_leading_kimi_mention_to_kimi_private_history_and_metadata():
    kimi_history = [{"role": "assistant", "content": "Kimi only"}]
    database = FakeDatabase(kimi_history)
    adapter = HistoryAwareAdapter("Kimi answer")
    app = create_app(database=database, adapter=adapter)

    response = authenticated_client(app).post("/api/chat", json={"message": "@Kimi hello"})

    assert response.status_code == 202
    assert response.get_json()["models"] == ["kimi"]


@pytest.mark.parametrize(
    ("mention", "model"),
    [
        ("@智谱", "zhipu"),
        ("@千问", "qwen"),
        ("@字节", "bytedance"),
    ],
)
def test_chat_routes_each_final_model_to_private_history_and_response_metadata(mention, model):
    history = [{"role": "assistant", "content": f"{model} only"}]
    database = FakeDatabase(history)
    adapter = HistoryAwareAdapter("answer")
    app = create_app(database=database, adapter=adapter)

    response = authenticated_client(app).post("/api/chat", json={"message": f"{mention} hello"})

    assert response.status_code == 202
    assert response.get_json()["models"] == [model]


def test_chat_defaults_to_deepseek_and_uses_only_its_private_history():
    database = FakeDatabase([{"role": "assistant", "content": "DeepSeek only"}])
    adapter = HistoryAwareAdapter("DeepSeek answer")
    app = create_app(database=database, adapter=adapter)

    response = authenticated_client(app).post("/api/chat", json={"message": "hello"})

    assert response.status_code == 202
    assert response.get_json()["models"] == ["deepseek"]


def test_chat_routes_leading_deepseek_mention_to_deepseek():
    database = FakeDatabase()
    adapter = HistoryAwareAdapter("DeepSeek answer")
    app = create_app(database=database, adapter=adapter)

    response = authenticated_client(app).post("/api/chat", json={"message": "@DeepSeek hello"})

    assert response.status_code == 202
    assert response.get_json()["models"] == ["deepseek"]


def test_chat_ignores_unsupported_doubao_and_falls_back_to_deepseek():
    database = FakeDatabase()
    app = create_app(database=database, adapter=FakeAdapter("answer"))

    for mention in ("@豆包 hello",):
        response = authenticated_client(app).post("/api/chat", json={"message": mention})
        assert response.status_code == 202
        assert response.get_json()["models"] == ["deepseek"]


def test_chat_rejects_blank_message():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = authenticated_client(app).post("/api/chat", json={"message": "   "})

    assert response.status_code == 400
    assert response.get_json()["error"] == "娑堟伅涓嶈兘涓虹┖"


def test_chat_accepts_a_default_deepseek_broadcast():
    database = FakeDatabase()
    app = create_app(database=database, adapter=FakeAdapter("answer"))

    response = authenticated_client(app).post("/api/chat", json={"message": "hello"})

    assert response.status_code == 202


def test_injected_database_is_not_initialized():
    database = FakeDatabase()

    create_app(database=database, adapter=FakeAdapter("answer"))

    assert database.initialize_calls == 0


def test_history_returns_all_stored_messages():
    messages = [{"role": "user", "content": "hello", "timestamp": "now"}]
    database = FakeDatabase(messages)
    app = create_app(database=database, adapter=FakeAdapter("answer"))

    response = authenticated_client(app).get("/api/history")

    assert response.status_code == 200
    assert response.get_json() == messages
    assert database.history_limit is None


def test_home_is_public_and_links_to_login():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b'href="/login"' in response.data


def test_chat_rejects_missing_and_non_string_messages():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    for payload in ({}, {"message": 1}):
        response = authenticated_client(app).post("/api/chat", json=payload)
        assert response.status_code == 400
        assert response.get_json() == {"error": "娑堟伅涓嶈兘涓虹┖"}


def test_chat_accepts_a_task_before_an_adapter_failure_is_known():
    database = FakeDatabase()
    app = create_app(database=database, adapter=FakeAdapter(error=RuntimeError("model failed")))

    response = authenticated_client(app).post("/api/chat", json={"message": "hello"})

    assert response.status_code == 202


def test_chat_accepts_a_task_before_an_adapter_timeout_is_known():
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter(error=TimeoutError("model response timed out")),
    )

    response = authenticated_client(app).post("/api/chat", json={"message": "hello"})

    assert response.status_code == 202


def test_debug_is_disabled_by_default():
    assert config.FLASK_DEBUG is False


def test_health_checks_run_in_background_without_delaying_app_creation(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class StaticCatalog:
        def __init__(self, cache_path):
            self.cache_path = cache_path

        def load(self):
            return {"providers": [], "updated_at": None}

        def refresh(self):
            return self.load()

    class BlockingHealthAdapter(FakeAdapter):
        def health_check(self, model):
            started.set()
            assert release.wait(1)
            return True

    monkeypatch.setattr(app_module, "ModelCatalogService", StaticCatalog)
    started_at = time.monotonic()
    app = create_app(
        database=FakeDatabase(),
        adapter=BlockingHealthAdapter(answer="ok"),
        media_service=object(),
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.25
    assert started.wait(0.5)
    assert app.extensions["model_health"] == {}

    release.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not app.extensions["model_health"]:
        time.sleep(0.01)
    assert app.extensions["model_health"]["deepseek"] is True


def test_catalog_refresh_runs_in_background_without_delaying_app_creation(
    monkeypatch, tmp_path
):
    started = threading.Event()
    release = threading.Event()

    class BlockingCatalog:
        def __init__(self, cache_path):
            self.cache_path = cache_path

        def load(self):
            return {"providers": [], "updated_at": None}

        def refresh(self):
            started.set()
            assert release.wait(1)
            return self.load()

    monkeypatch.setattr(
        config, "MODEL_CATALOG_CACHE_PATH", tmp_path / "background-catalog.json"
    )
    monkeypatch.setattr(app_module, "ModelCatalogService", BlockingCatalog)
    started_at = time.monotonic()
    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter(answer="ok"),
        media_service=object(),
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.25
    assert started.wait(0.5)
    assert app.extensions["model_catalog"].load() == {"providers": [], "updated_at": None}

    release.set()


def test_health_check_results_are_cached_and_failure_does_not_stop_startup(caplog):
    class HealthAdapter(FakeAdapter):
        def health_check(self, model):
            if model == "kimi":
                raise RuntimeError("offline")
            return True

    with caplog.at_level(logging.WARNING):
        app = create_app(database=FakeDatabase(), adapter=HealthAdapter(answer="ok"))

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not app.extensions["model_health"]:
        time.sleep(0.01)
    assert app.extensions["model_health"]["deepseek"] is True
    assert app.extensions["model_health"]["kimi"] is False
    assert "[WARN] Model kimi unreachable" in caplog.text


def test_log_filter_redacts_api_key_and_request_prompt(monkeypatch):
    monkeypatch.setattr(config, "GATEWAY_COMPAT_API_KEY", "test-secret-key")
    monkeypatch.setattr(config, "GATEWAY_MAIN_API_KEY", "gateway_main-test-key")
    app = Flask(__name__)
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1,
        "failed key=test-secret-key alternate=gateway_main-test-key prompt=private user prompt", (), None,
    )

    with app.test_request_context("/api/chat", json={"message": "private user prompt"}):
        assert app_module.SensitiveDataFilter().filter(record) is True

    assert "test-secret-key" not in record.getMessage()
    assert "gateway_main-test-key" not in record.getMessage()
    assert "private user prompt" not in record.getMessage()
    assert record.getMessage().count("***") == 3


def test_request_context_logs_redact_every_form_value_and_exception_tracebacks(caplog):
    app = Flask(__name__)
    app_module.configure_logging_redaction(app)
    form = {
        "email": "alice@example.com",
        "password": SYNTHETIC_CREDENTIAL_04,
        "password_confirm": "confirmation-secret",
        "code": "123456",
        "message": "private message",
        "prompt": "private prompt",
    }
    raw_values = " | ".join(form.values())

    with app.test_request_context("/reset-password", data=form), caplog.at_level(
        logging.ERROR, logger=app.logger.name
    ):
        try:
            raise RuntimeError(raw_values)
        except RuntimeError:
            app.logger.exception("Authentication request failed: %s", raw_values)

    assert "Authentication request failed: ***" in caplog.text
    assert "Traceback" not in caplog.text
    assert all(value not in caplog.text for value in form.values())


def test_test_route_reports_loaded_model_ids():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter(answer="ok"))

    response = app.test_client().get("/test")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "models_loaded": list(config.MODEL_METADATA),
    }


def test_chat_stream_returns_sse_events_for_authenticated_users():
    class StreamingBroadcast:
        def stream_events(self, message, selected_models, username=None):
            assert message == "hello"
            assert selected_models == ["deepseek"]
            assert username == "authenticated-user"
            yield {"event": "start", "models": ["deepseek"]}
            yield {"event": "token", "model": "deepseek", "token": "你"}
            yield {"event": "done", "model": "deepseek"}

    app = create_app(
        database=FakeDatabase(),
        adapter=FakeAdapter(answer="ok"),
        broadcast_service=StreamingBroadcast(),
    )

    response = authenticated_client(app).post(
        "/api/chat/stream", json={"message": "hello", "models": ["deepseek"]}
    )

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "event: token" in response.get_data(as_text=True)
    assert '"token": "你"' in response.get_data(as_text=True)


def test_dynamic_model_stream_receives_shared_context_and_records_one_global_reply(monkeypatch):
    class DynamicRegistry:
        def resolve(self, model):
            assert model == "gateway_main:deepseek-v3"
            return {
                "model_id": "deepseek-v3",
                "base_url": "https://example.test/v1",
                "api_key_setting": "GATEWAY_MAIN_API_KEY",
            }

    class StreamingAdapter:
        def billing_user(self, username):
            assert username == "authenticated-user"
            return nullcontext()

        def stream_reply_with_history(self, message, history, model="deepseek"):
            assert message == "现在加入协作"
            assert model == "gateway_main:deepseek-v3"
            assert history == [
                {
                    "role": "system",
                    "content": "当前协作上下文：\n用户: 先分析需求\nDeepSeek: 已列出风险",
                }
            ]
            yield "动态模型回复"

    monkeypatch.setattr(
        config, "MODEL_PRICES_MICRO_POINTS", {"gateway_main:deepseek-v3": 10}
    )
    monkeypatch.setattr(app_module, "ModelCatalogService", PublicBoundaryCatalog)
    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("authenticated-user")
    database.add_collaboration_message(
        "user", "先分析需求", conversation_id=conversation["id"]
    )
    database.add_collaboration_message(
        "assistant",
        "已列出风险",
        model="deepseek",
        conversation_id=conversation["id"],
    )
    application = create_app(database=database, adapter=StreamingAdapter())
    application.extensions["model_registry"] = DynamicRegistry()

    response = authenticated_client(application).post(
        "/api/model-chat/stream",
        json={
            "message": "现在加入协作",
            "model": "deepseek/deepseek-v3",
            "conversation_id": conversation["id"],
        },
    )

    assert response.status_code == 200
    assert "event: start" in response.get_data(as_text=True)
    assert '"model": "deepseek/deepseek-v3"' in response.get_data(as_text=True)
    assert "gateway_main" not in response.get_data(as_text=True).lower()
    assert [message["content"] for message in database.get_shared_messages(conversation["id"])] == [
        "先分析需求",
        "已列出风险",
        "现在加入协作",
        "动态模型回复",
    ]


def test_dynamic_model_stream_rejects_an_unpriced_model(monkeypatch):
    class DynamicRegistry:
        def resolve(self, model):
            return {"model_id": "deepseek-v3"}

    monkeypatch.setattr(config, "MODEL_PRICES_MICRO_POINTS", {})
    monkeypatch.setattr(app_module, "ModelCatalogService", PublicBoundaryCatalog)
    database = Database.for_testing()
    database.initialize()
    conversation = database.create_conversation("authenticated-user")
    application = create_app(database=database, adapter=FakeAdapter(answer="unused"))
    application.extensions["model_registry"] = DynamicRegistry()

    response = authenticated_client(application).post(
        "/api/model-chat/stream",
        json={
            "message": "加入协作",
            "model": "deepseek/deepseek-v3",
            "conversation_id": conversation["id"],
        },
    )

    assert response.status_code == 400
    assert "积分价格" in response.get_json()["error"]


def test_landing_page_is_public_and_chat_route_requires_a_session():
    app = create_app(database=FakeDatabase(), adapter=FakeAdapter(answer="ok"))

    landing = app.test_client().get("/")
    chat = app.test_client().get("/chat")

    assert landing.status_code == 200
    assert b"MULTI-AGENT COLLABORATION" in landing.data
    assert chat.status_code == 302
    assert chat.headers["Location"].endswith("/login?next=/chat")


def test_account_security_requires_login_and_renders_unbound_phone_binding_form():
    app = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(
            email_identity={
                "username": "user-1",
                "email": "alice@example.com",
                "session_version": 0,
            }
        ),
        adapter=FakeAdapter("ok"),
        email_auth_service=FakeEmailAuthService(),
    )

    assert app.test_client().get("/account/security").status_code == 302

    client = authenticated_email_client(app)
    response = client.get("/account/security")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "\u90ae\u7bb1\u5df2\u9a8c\u8bc1" in body
    assert 'action="/account/security/phone/send"' in body
    assert 'name="_csrf_token"' in body
    assert 'name="phone" autocomplete="tel"' in body
    assert "\u5fae\u4fe1\u7ed1\u5b9a\u6682\u672a\u5f00\u653e" in body


def test_account_security_invalidates_a_session_without_a_verified_identity():
    app = create_app(
        database=FakeDatabase(),
        user_database=FakeUserDatabase(),
        adapter=FakeAdapter("ok"),
        email_auth_service=FakeEmailAuthService(),
    )

    client = authenticated_email_client(app)
    response = client.get("/account/security")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    with client.session_transaction() as current_session:
        assert not current_session


def phone_binding_client(application, email="alice@example.com"):
    client = application.test_client()
    identity = application.extensions["user_database"].get_session_identity(email)
    with client.session_transaction() as current_session:
        current_session.update(identity, csrf_token="csrf")
    return client


def test_phone_binding_requires_login_csrf_then_binds_verified_number():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)

    assert application.test_client().post("/account/security/phone/send").status_code == 302
    assert client.post(
        "/account/security/phone/send", data={"phone": "13800138000"}
    ).status_code == 400
    assert client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    ).status_code == 302
    assert client.post(
        "/account/security/phone/verify",
        data={"code": service.code, "_csrf_token": "csrf"},
    ).status_code == 302
    assert users.get_session_identity("alice@example.com")["phone_verified_at"] is not None
    assert service.sent == ["13800138000"]


def test_account_security_renders_a_masked_pending_phone_challenge():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=FakeSmsAuthService(),
    )
    client = phone_binding_client(application)

    assert client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    ).status_code == 302

    response = client.get("/account/security")

    assert response.status_code == 200
    assert b"138****8000" in response.data
    assert b"13800138000" not in response.data
    assert b'action="/account/security/phone/verify"' in response.data


def test_account_security_renders_a_masked_bound_phone_and_flashed_feedback():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)
    assert client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    ).status_code == 302
    assert client.post(
        "/account/security/phone/verify",
        data={"code": service.code, "_csrf_token": "csrf"},
    ).status_code == 302

    response = client.get("/account/security")

    assert response.status_code == 200
    assert b"138****8000" in response.data
    assert b"13800138000" not in response.data
    assert b'role="status"' in response.data
    assert "手机号已绑定".encode() in response.data


def test_account_security_renders_flashed_error_in_the_accessible_alert_region():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    application = create_app(
        database=FakeDatabase(), user_database=users, adapter=FakeAdapter("answer")
    )
    client = phone_binding_client(application)
    with client.session_transaction() as current_session:
        current_session["_flashes"] = [("error", "安全提示")]

    response = client.get("/account/security")

    assert response.status_code == 200
    assert b'role="alert"' in response.data
    assert "安全提示".encode() in response.data


def test_phone_binding_rejects_malformed_number_without_sending_a_code():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )

    response = phone_binding_client(application).post(
        "/account/security/phone/send",
        data={"phone": "not-a-number", "_csrf_token": "csrf"},
    )

    assert response.status_code == 400
    assert service.sent == []


def test_phone_binding_refuses_requests_when_sms_service_is_unavailable(monkeypatch):
    monkeypatch.setattr(app_module.config, "SMS_AUTH_ENABLED", False)
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    application = create_app(
        database=FakeDatabase(), user_database=users, adapter=FakeAdapter("answer")
    )

    response = phone_binding_client(application).post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    )

    assert response.status_code == 503
    assert "短信服务尚未配置，请联系管理员".encode() in response.data


def test_phone_binding_rate_limits_a_second_send_within_one_minute():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)

    assert client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    ).status_code == 302
    response = client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    )

    assert response.status_code == 429
    assert service.sent == ["13800138000"]


def test_phone_binding_rejects_wrong_codes_without_binding_the_number():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)
    client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    )

    response = client.post(
        "/account/security/phone/verify",
        data={"code": "wrong", "_csrf_token": "csrf"},
    )

    assert response.status_code == 400
    assert users.get_session_identity("alice@example.com")["phone_verified_at"] is None
    assert service.verify_requests == [("13800138000", "wrong")]


def test_phone_binding_stops_calling_the_provider_after_five_wrong_codes():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)
    client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    )

    for _ in range(6):
        response = client.post(
            "/account/security/phone/verify",
            data={"code": "wrong", "_csrf_token": "csrf"},
        )
        assert response.status_code == 400

    assert len(service.verify_requests) == 5


def test_phone_binding_rejects_a_number_already_bound_to_another_account():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.prepare_email_registration("bob@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    users.mark_email_verified("bob@example.com")
    assert users.bind_verified_phone("bob@example.com", "13800138000") == "bound"
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )

    response = phone_binding_client(application).post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    )

    assert response.status_code == 400
    assert "该手机号已绑定其他账户".encode() in response.data
    assert service.sent == []


def test_phone_binding_constructs_the_adapter_only_from_complete_enabled_configuration(monkeypatch):
    class ConstructedSmsAuthService:
        def __init__(self, sign_name, template_code):
            self.sign_name = sign_name
            self.template_code = template_code

    credential = uuid4().hex
    setting = uuid4().hex
    monkeypatch.setattr(app_module.config, "SMS_AUTH_ENABLED", True)
    monkeypatch.setattr(app_module.config, "ALIBABA_CLOUD_ACCESS_KEY_ID", credential)
    monkeypatch.setattr(app_module.config, "ALIBABA_CLOUD_ACCESS_KEY_SECRET", credential)
    monkeypatch.setattr(app_module.config, "ALIYUN_SMS_SIGN_NAME", setting)
    monkeypatch.setattr(app_module.config, "ALIYUN_SMS_BIND_TEMPLATE_CODE", setting)
    monkeypatch.setattr(app_module, "AlibabaSmsAuthService", ConstructedSmsAuthService)

    application = create_app(database=FakeDatabase(), adapter=FakeAdapter("answer"))

    service = application.extensions["sms_auth_service"]
    assert isinstance(service, ConstructedSmsAuthService)
    assert (service.sign_name, service.template_code) == (setting, setting)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("SMS_AUTH_ENABLED", False),
        ("ALIBABA_CLOUD_ACCESS_KEY_ID", ""),
        ("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "   "),
        ("ALIYUN_SMS_SIGN_NAME", "change-me"),
        ("ALIYUN_SMS_BIND_TEMPLATE_CODE", "change-me"),
        ("ALIBABA_CLOUD_ACCESS_KEY_ID", "short"),
        ("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "x" * 32),
    ],
)
def test_phone_binding_configuration_rejects_disabled_incomplete_and_placeholder_values(
    monkeypatch, setting, value
):
    credential = uuid4().hex
    setting_value = uuid4().hex
    monkeypatch.setattr(app_module.config, "SMS_AUTH_ENABLED", True)
    monkeypatch.setattr(app_module.config, "ALIBABA_CLOUD_ACCESS_KEY_ID", credential)
    monkeypatch.setattr(app_module.config, "ALIBABA_CLOUD_ACCESS_KEY_SECRET", credential)
    monkeypatch.setattr(app_module.config, "ALIYUN_SMS_SIGN_NAME", setting_value)
    monkeypatch.setattr(app_module.config, "ALIYUN_SMS_BIND_TEMPLATE_CODE", setting_value)
    monkeypatch.setattr(app_module.config, setting, value)

    assert app_module.has_valid_sms_auth_configuration() is False


def test_phone_binding_cooldown_is_enforced_for_a_client_with_a_pre_send_cookie():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    current_client = phone_binding_client(application)
    stale_client = phone_binding_client(application)

    assert current_client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    ).status_code == 302
    response = stale_client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    )

    assert response.status_code == 429
    assert service.sent == ["13800138000"]
    with current_client.session_transaction() as current_session:
        assert "pending_phone_binding_number" not in current_session
        assert "phone_binding_sent_at" not in current_session
        assert "phone_binding_failed_attempts" not in current_session


def test_phone_binding_attempt_cap_is_enforced_for_a_client_with_a_pre_send_cookie():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    sending_client = phone_binding_client(application)
    stale_client = phone_binding_client(application)
    assert sending_client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    ).status_code == 302

    for _ in range(6):
        assert stale_client.post(
            "/account/security/phone/verify",
            data={"code": "wrong", "_csrf_token": "csrf"},
        ).status_code == 400

    assert len(service.verify_requests) == 5


def test_phone_binding_verify_requires_login_and_csrf_before_provider_calls():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)

    assert application.test_client().post("/account/security/phone/verify").status_code == 302
    assert client.post(
        "/account/security/phone/verify", data={"code": "wrong"}
    ).status_code == 400
    assert service.verify_requests == []


@pytest.mark.parametrize("operation", ("send", "verify"))
def test_phone_binding_adapter_errors_return_only_the_safe_retry_message(operation):
    detail = "provider-request-secret-13800138000"
    service = FakeSmsAuthService(
        send_error=RuntimeError(detail) if operation == "send" else None,
        verify_error=RuntimeError(detail) if operation == "verify" else None,
    )
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)
    if operation == "verify":
        assert client.post(
            "/account/security/phone/send",
            data={"phone": "13800138000", "_csrf_token": "csrf"},
        ).status_code == 302
    path = f"/account/security/phone/{operation}"
    form = {"_csrf_token": "csrf"}
    form["phone" if operation == "send" else "code"] = "13800138000" if operation == "send" else "wrong"

    response = client.post(path, data=form)

    assert response.status_code == 503
    assert "请稍后再试".encode() in response.data
    assert detail.encode() not in response.data
    assert "13800138000".encode() not in response.data


def test_phone_binding_failed_send_clears_a_mysql_second_precision_challenge(monkeypatch):
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    original_start = users.start_phone_binding_challenge

    def mysql_second_precision_start(email, phone, now):
        return original_start(email, phone, now.replace(microsecond=0))

    monkeypatch.setattr(users, "start_phone_binding_challenge", mysql_second_precision_start)
    service = FakeSmsAuthService(send_error=RuntimeError("delivery failure"))
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)

    assert client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    ).status_code == 503
    service.send_error = None
    response = client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    )

    assert response.status_code == 302
    assert service.sent == ["13800138000"]


@pytest.mark.parametrize("operation", ("prepare", "consume"))
def test_phone_binding_persistence_errors_return_only_the_safe_retry_message(
    monkeypatch, operation
):
    detail = "database-phone-binding-detail"
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    users.mark_email_verified("alice@example.com")
    service = FakeSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)

    if operation == "prepare":
        monkeypatch.setattr(
            users,
            "start_phone_binding_challenge",
            lambda *_: (_ for _ in ()).throw(RuntimeError(detail)),
        )
        response = client.post(
            "/account/security/phone/send",
            data={"phone": "13800138000", "_csrf_token": "csrf"},
        )
    else:
        assert client.post(
            "/account/security/phone/send",
            data={"phone": "13800138000", "_csrf_token": "csrf"},
        ).status_code == 302
        monkeypatch.setattr(
            users,
            "consume_verified_phone_binding",
            lambda *_: (_ for _ in ()).throw(RuntimeError(detail)),
        )
        response = client.post(
            "/account/security/phone/verify",
            data={"code": service.code, "_csrf_token": "csrf"},
        )

    assert response.status_code == 503
    assert "请稍后再试".encode() in response.data
    assert detail.encode() not in response.data


def test_phone_binding_rejects_a_bind_time_phone_conflict_after_provider_passes():
    users = UserDatabase.for_testing()
    users.initialize()
    for email in ("alice@example.com", "bob@example.com"):
        users.prepare_email_registration(email, SYNTHETIC_CREDENTIAL_02)
        users.mark_email_verified(email)

    class BindingRaceSmsAuthService(FakeSmsAuthService):
        def verify_binding_code(self, phone_number, code):
            assert users.bind_verified_phone("bob@example.com", phone_number) == "bound"
            return super().verify_binding_code(phone_number, code)

    service = BindingRaceSmsAuthService()
    application = create_app(
        database=FakeDatabase(),
        user_database=users,
        adapter=FakeAdapter("answer"),
        sms_auth_service=service,
    )
    client = phone_binding_client(application)
    assert client.post(
        "/account/security/phone/send",
        data={"phone": "13800138000", "_csrf_token": "csrf"},
    ).status_code == 302

    response = client.post(
        "/account/security/phone/verify",
        data={"code": service.code, "_csrf_token": "csrf"},
    )

    assert response.status_code == 400
    assert "该手机号已绑定其他账户".encode() in response.data
    assert users.get_session_identity("alice@example.com")["phone_verified_at"] is None


def test_stale_versioned_session_is_cleared_before_rendering_a_public_template():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    identity = users.mark_email_verified("alice@example.com")
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("ok"), user_database=users
    )
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(identity)
    assert users.reset_password_by_email("alice@example.com", SYNTHETIC_CREDENTIAL_03) is True

    response = client.get("/")

    assert response.status_code == 200
    assert identity["username"].encode() not in response.data
    assert "账户安全".encode() not in response.data
    with client.session_transaction() as current_session:
        assert "username" not in current_session
        assert "email" not in current_session
        assert "session_version" not in current_session


def test_stale_versioned_session_gets_the_established_json_401_on_an_api_route():
    users = UserDatabase.for_testing()
    users.initialize()
    users.prepare_email_registration("alice@example.com", SYNTHETIC_CREDENTIAL_02)
    identity = users.mark_email_verified("alice@example.com")
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("ok"), user_database=users
    )
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session.update(identity)
    assert users.reset_password_by_email("alice@example.com", SYNTHETIC_CREDENTIAL_03) is True

    response = client.get("/api/models/catalog")

    assert response.status_code == 401
    assert response.is_json
    assert response.get_json() == {"error": "authentication required"}
    with client.session_transaction() as current_session:
        assert dict(current_session) == {}


def test_public_session_validation_preserves_a_legacy_email_less_administrator():
    users = FakeUserDatabase()
    assert users.create_user("legacy-admin", SYNTHETIC_CREDENTIAL_05) is True
    users.roles["legacy-admin"] = "admin"
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("ok"), user_database=users
    )
    client = administrator_client(app, username="legacy-admin")

    response = client.get("/admin/users")

    assert response.status_code == 200
    with client.session_transaction() as current_session:
        assert current_session["username"] == "legacy-admin"
        assert current_session["is_admin"] is True
        assert "email" not in current_session


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [("/", 200), ("/workspace", 302), ("/api/models/catalog", 401)],
)
def test_email_less_ordinary_session_is_cleared_before_rendering_or_authorization(
    path, expected_status
):
    app = create_app(
        database=FakeDatabase(), adapter=FakeAdapter("ok"), user_database=FakeUserDatabase()
    )
    client = app.test_client()
    with client.session_transaction() as current_session:
        current_session["username"] = "ordinary-user"

    response = client.get(path)

    assert response.status_code == expected_status
    if path == "/":
        assert b"ordinary-user" not in response.data
    if path.startswith("/api/"):
        assert response.get_json() == {"error": "authentication required"}
    with client.session_transaction() as current_session:
        assert "username" not in current_session
