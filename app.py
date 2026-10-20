"""Flask routes for the multi-model collaboration workbench."""

import json
import hmac
import logging
from contextlib import nullcontext
from datetime import datetime, timezone
from queue import Full, Queue
from threading import Lock, Thread
from flask import Flask, Response, abort, flash, get_flashed_messages, has_request_context, jsonify, redirect, render_template, request, session, stream_with_context, url_for

import config
from ai_adapter import AIAdapter
from admin_authorization import (
    ACCOUNTS_MANAGE,
    ACCOUNTS_READ,
    MODEL_CATALOG_REFRESH,
    AdminAuthorizationService,
)
from attachment_service import AttachmentService
from auth import admin_capability_required, csrf_protected, csrf_token, initialize_auth, login_required, safe_login_destination, validate_username
from broadcast import BroadcastService, parse_targets
from billing_gateway import BillingGateway
from catalog_refresh import CatalogRefreshCoordinator
from collaboration_context import CollaborationContextService
from database import Database
from email_auth import (
    EmailAuthService,
    EmailCodeRateLimitError,
    SMTPEmailSender,
    normalize_email,
    validate_password,
)
from model_catalog import ModelCatalogService
from model_registry import ModelRegistry
from public_model_directory import PUBLIC_MODEL_UNAVAILABLE, PublicModelDirectory
from media_service import MediaService
from payment_service import PaymentService
from payments.providers import MockWechatPayProvider
from sms_auth import AlibabaSmsAuthService, mask_phone_number, normalize_mainland_phone
from user_database import UserDatabase
from workspace_stream_service import WorkspaceStreamService

GENERIC_ERROR = "\u670d\u52a1\u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5"
INVALID_LOGIN_ERROR = "\u7528\u6237\u540d\u6216\u5bc6\u7801\u9519\u8bef"
EMAIL_SERVICE_UNAVAILABLE_ERROR = "\u90ae\u7bb1\u670d\u52a1\u5c1a\u672a\u914d\u7f6e\uff0c\u8bf7\u8054\u7cfb\u7ba1\u7406\u5458"
SMS_SERVICE_UNAVAILABLE_ERROR = "\u77ed\u4fe1\u670d\u52a1\u5c1a\u672a\u914d\u7f6e\uff0c\u8bf7\u8054\u7cfb\u7ba1\u7406\u5458"
SMS_RETRY_LATER_ERROR = "\u8bf7\u7a0d\u540e\u518d\u8bd5"
PHONE_BINDING_CODE_INVALID_ERROR = "\u9a8c\u8bc1\u7801\u65e0\u6548\u6216\u5df2\u8fc7\u671f"
PHONE_ALREADY_BOUND_ERROR = "\u8be5\u624b\u673a\u53f7\u5df2\u7ed1\u5b9a\u5176\u4ed6\u8d26\u6237"
ACCOUNT_ALREADY_BOUND_ERROR = "\u5f53\u524d\u8d26\u6237\u5df2\u7ed1\u5b9a\u624b\u673a\u53f7"
EMAIL_CODE_PEPPER_PLACEHOLDERS = {
    "change-me",
    "change-this-development-secret",
    "replace-with-a-real-email-code-pepper",
    "replace-with-a-real-production-secret-before-deploying",
    "replace_with_a_long_random_secret",
    "your-email-code-pepper",
}
SMS_CONFIGURATION_PLACEHOLDERS = EMAIL_CODE_PEPPER_PLACEHOLDERS | {
    "your-alibaba-cloud-access-key-id",
    "your-alibaba-cloud-access-key-secret",
    "your-sms-sign-name",
    "your-sms-template-code",
}


PASSWORD_RESET_REQUEST_MESSAGE = "\u5982\u679c\u90ae\u7bb1\u5df2\u6ce8\u518c\uff0c\u6211\u4eec\u5df2\u53d1\u9001\u9a8c\u8bc1\u7801\uff0c\u8bf7\u68c0\u67e5\u90ae\u7bb1\u3002"
PASSWORD_RESET_SUCCESS_MESSAGE = "\u5bc6\u7801\u5df2\u91cd\u8bbe\uff0c\u8bf7\u4f7f\u7528\u65b0\u5bc6\u7801\u767b\u5f55\u3002"


class SensitiveDataFilter(logging.Filter):
    """Remove request data before log handlers format a record."""

    def filter(self, record):
        message = record.getMessage()
        secrets = [
            value
            for value in (
                config.GATEWAY_COMPAT_API_KEY,
                getattr(config, "GATEWAY_SECONDARY_API_KEY", ""),
                getattr(config, "GATEWAY_MAIN_API_KEY", ""),
            )
            if isinstance(value, str) and value
        ]
        prompts = []
        if has_request_context():
            payload = request.get_json(silent=True) or {}
            prompts.extend(value for value in (payload.get("message"), payload.get("prompt")) if isinstance(value, str))
            for field in request.form:
                prompts.extend(
                    value for value in request.form.getlist(field) if isinstance(value, str)
                )
        for sensitive_value in secrets + prompts:
            if sensitive_value:
                message = message.replace(sensitive_value, "***")
        record.msg = message
        record.args = ()
        if has_request_context():
            record.exc_info = None
            record.exc_text = None
        return True


def configure_logging_redaction(app):
    """Attach the filter once so Flask's existing handlers receive redacted records."""
    if not any(isinstance(item, SensitiveDataFilter) for item in app.logger.filters):
        app.logger.addFilter(SensitiveDataFilter())


def run_model_health_checks(adapter, logger):
    """Return in-memory availability results without preventing application startup."""
    results = {}
    for name in config.MODEL_METADATA:
        try:
            results[name] = bool(adapter.health_check(name))
        except Exception:
            results[name] = False
            logger.warning("[WARN] Model %s unreachable", name)
    return results


def refresh_model_health(app, adapter):
    """Run slow upstream probes after the Flask application is ready to serve."""
    app.extensions["model_health"] = run_model_health_checks(adapter, app.logger)


class PasswordResetDispatcher:
    """Dispatch password-reset work to a fixed worker set and bounded queue."""

    def __init__(self, worker_count=2, queue_capacity=32, logger=None):
        if worker_count < 1 or queue_capacity < 1:
            raise ValueError("worker_count and queue_capacity must be positive")
        self._queue = Queue(maxsize=queue_capacity)
        self._logger = logger or logging.getLogger(__name__)
        for index in range(worker_count):
            Thread(
                target=self._run_worker,
                name=f"password-reset-worker-{index + 1}",
                daemon=True,
            ).start()

    def __call__(self, task):
        try:
            self._queue.put_nowait(task)
        except Full:
            return False
        return True

    def _run_worker(self):
        while True:
            task = self._queue.get()
            try:
                task()
            except Exception:
                self._logger.warning("Password reset request was not delivered")
            finally:
                self._queue.task_done()


_PASSWORD_RESET_DISPATCHER = None
_PASSWORD_RESET_DISPATCHER_LOCK = Lock()


def dispatch_password_reset(task):
    """Queue password-reset delivery without creating per-request threads."""
    global _PASSWORD_RESET_DISPATCHER
    with _PASSWORD_RESET_DISPATCHER_LOCK:
        if _PASSWORD_RESET_DISPATCHER is None:
            _PASSWORD_RESET_DISPATCHER = PasswordResetDispatcher()
    return _PASSWORD_RESET_DISPATCHER(task)


def build_model_catalog(metadata):
    """Return configured models grouped by provider in declaration order."""
    catalog = {}
    for model_id, model in metadata.items():
        provider = model.get("provider", model.get("label", model_id))
        display_name = model.get("display_name", model.get("label", model_id))
        catalog.setdefault(provider, []).append({"id": model_id, "name": display_name})
    return catalog


def has_valid_production_email_configuration(settings=config):
    """Return whether production registration can safely issue email codes."""
    host = settings.SMTP_HOST
    from_email = settings.SMTP_FROM_EMAIL
    pepper = settings.EMAIL_CODE_PEPPER
    if not isinstance(host, str) or not host.strip():
        return False
    if not isinstance(pepper, str):
        return False
    pepper = pepper.strip()
    if (
        len(pepper) < 32
        or pepper.lower() in EMAIL_CODE_PEPPER_PLACEHOLDERS
        or len(set(pepper)) < 12
        or any(
            len(pepper) % pattern_length == 0
            and pepper == pepper[:pattern_length] * (len(pepper) // pattern_length)
            for pattern_length in range(1, len(pepper) // 2 + 1)
        )
    ):
        return False
    try:
        normalize_email(from_email)
    except ValueError:
        return False
    return True


def has_valid_sms_auth_configuration(settings=config):
    """Return whether phone-binding SMS can be safely enabled."""
    def has_valid_credential(value):
        if not isinstance(value, str):
            return False
        value = value.strip()
        if len(value) < 16 or value.lower() in SMS_CONFIGURATION_PLACEHOLDERS or len(set(value)) < 8:
            return False
        return not any(
            len(value) % pattern_length == 0
            and value == value[:pattern_length] * (len(value) // pattern_length)
            for pattern_length in range(1, len(value) // 2 + 1)
        )

    def has_valid_setting(value):
        return (
            isinstance(value, str)
            and bool(value.strip())
            and value.strip().lower() not in SMS_CONFIGURATION_PLACEHOLDERS
        )

    return (
        bool(settings.SMS_AUTH_ENABLED)
        and has_valid_credential(settings.ALIBABA_CLOUD_ACCESS_KEY_ID)
        and has_valid_credential(settings.ALIBABA_CLOUD_ACCESS_KEY_SECRET)
        and has_valid_setting(settings.ALIYUN_SMS_SIGN_NAME)
        and has_valid_setting(settings.ALIYUN_SMS_BIND_TEMPLATE_CODE)
    )


def create_app(
    database=None,
    adapter=None,
    broadcast_service=None,
    user_database=None,
    payment_service=None,
    email_auth_service=None,
    password_reset_dispatcher=dispatch_password_reset,
    sms_auth_service=None,
    media_service=None,
    attachment_service=None,
    workspace_stream_service=None,
    catalog_service=None,
    start_background_tasks=True,
    runtime_settings=None,
):
    """Create the web app, accepting dependencies for isolated tests."""
    settings = runtime_settings if runtime_settings is not None else config
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=settings.FLASK_SECRET_KEY,
        ADMIN_USERNAME=settings.ADMIN_USERNAME,
        ADMIN_PASSWORD=settings.ADMIN_PASSWORD,
        MOCK_PAYMENT_CALLBACK_TOKEN=settings.MOCK_PAYMENT_CALLBACK_TOKEN,
        ENABLE_MOCK_PAYMENTS=settings.ENABLE_MOCK_PAYMENTS,
    )
    configure_logging_redaction(app)

    if database is None:
        database = Database(settings)
        database.initialize()
    if user_database is None:
        user_database = UserDatabase(settings)
        user_database.initialize()
    admin_authorization = AdminAuthorizationService(user_database)
    app.extensions["admin_authorization"] = admin_authorization
    if catalog_service is None:
        catalog_service = ModelCatalogService(settings.MODEL_CATALOG_CACHE_PATH)
    catalog_refresh = CatalogRefreshCoordinator(catalog_service, app.logger)
    model_registry = ModelRegistry(catalog_service, settings.MODEL_PROVIDER_CONNECTIONS)
    public_models = PublicModelDirectory(catalog_service)
    if adapter is None:
        gateway = None
        if settings.MODEL_PRICES_MICRO_POINTS and not settings.USE_LEGACY_API:
            gateway = BillingGateway(user_database)
        adapter = AIAdapter(
            database, billing_gateway=gateway, model_registry=model_registry
        )
    app.extensions["model_health"] = {}
    app.extensions["model_catalog"] = catalog_service
    app.extensions["model_catalog_refresh"] = catalog_refresh
    app.extensions["model_registry"] = model_registry
    app.extensions["public_model_directory"] = public_models
    collaboration_context = CollaborationContextService(database)
    app.extensions["collaboration_context"] = collaboration_context
    if workspace_stream_service is None:
        workspace_stream_service = WorkspaceStreamService(
            database, adapter, collaboration_context, model_registry
        )
    app.extensions["workspace_stream_service"] = workspace_stream_service
    if start_background_tasks:
        Thread(target=catalog_refresh.run_forever, daemon=True).start()
        Thread(target=refresh_model_health, args=(app, adapter), daemon=True).start()
    if broadcast_service is None:
        broadcast_service = BroadcastService(
            database, adapter, context_service=collaboration_context
        )
    initialize_auth(app, user_database)
    if email_auth_service is None:
        if has_valid_production_email_configuration(settings):
            email_sender = SMTPEmailSender(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                settings.SMTP_USERNAME,
                settings.SMTP_PASSWORD,
                settings.SMTP_FROM_EMAIL,
                settings.SMTP_USE_TLS,
            )
            email_auth_service = EmailAuthService(
                user_database, email_sender, settings.EMAIL_CODE_PEPPER
            )
        else:
            app.logger.warning("Email registration service is unavailable")
    if payment_service is None:
        payment_service = PaymentService(user_database, MockWechatPayProvider())
    app.extensions["payment_service"] = payment_service
    if sms_auth_service is None:
        if has_valid_sms_auth_configuration(settings):
            sms_auth_service = AlibabaSmsAuthService(
                settings.ALIYUN_SMS_SIGN_NAME.strip(),
                settings.ALIYUN_SMS_BIND_TEMPLATE_CODE.strip(),
            )
        else:
            app.logger.warning("SMS phone binding service is unavailable")
    app.extensions["sms_auth_service"] = sms_auth_service
    if media_service is None:
        if settings.GATEWAY_MAIN_API_KEY:
            media_service = MediaService()
    app.extensions["media_service"] = media_service
    if attachment_service is None:
        attachment_service = AttachmentService(media_service=media_service)
    app.extensions["attachment_service"] = attachment_service

    @app.get("/test")
    def test_status():
        """Expose a non-sensitive startup verification response."""
        return jsonify({"status": "ok", "models_loaded": list(settings.MODEL_METADATA)})

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/features")
    def features():
        return render_template("features.html")

    @app.route("/login", methods=["GET", "POST"])
    @csrf_protected
    def login():
        destination = safe_login_destination(
            request.form.get("next") if request.method == "POST" else request.args.get("next")
        )
        if request.method == "POST":
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            try:
                identity = user_database.verify_email_password(email, password)
            except Exception:
                app.logger.error("Unable to verify user credentials")
                return render_template("login.html", error=GENERIC_ERROR, next=destination), 500
            if identity is None:
                return render_template("login.html", error=INVALID_LOGIN_ERROR, next=destination), 401
            session.clear()
            session.update(
                username=identity["username"],
                email=identity["email"],
                session_version=identity["session_version"],
            )
            return redirect(destination)
        message = (
            PASSWORD_RESET_SUCCESS_MESSAGE
            if request.args.get("reset") == "success"
            else None
        )
        return render_template("login.html", message=message, next=destination)

    @app.post("/api/auth/password-reset-code")
    @csrf_protected(protect_api=True)
    def request_password_reset_code():
        """Issue a reset code without exposing account existence to the browser."""
        if email_auth_service is None:
            return jsonify(error=EMAIL_SERVICE_UNAVAILABLE_ERROR), 503
        payload = request.get_json(silent=True) or {}
        try:
            email_auth_service.request_password_reset(payload.get("email", ""))
        except EmailCodeRateLimitError as error:
            return jsonify(
                error="\u8bf7\u7a0d\u540e\u91cd\u65b0\u53d1\u9001\u9a8c\u8bc1\u7801",
                retry_after_seconds=error.retry_after_seconds,
            ), 429
        except Exception:
            app.logger.warning("Password reset code request was not delivered")
        return jsonify(
            message=PASSWORD_RESET_REQUEST_MESSAGE,
            retry_after_seconds=60,
        )

    @app.route("/forgot-password", methods=["GET", "POST"])
    @csrf_protected
    def forgot_password():
        if request.method == "GET":
            return render_template("forgot_password.html")

        email = request.form.get("email", "")

        def request_reset():
            try:
                if email_auth_service is None:
                    raise RuntimeError
                email_auth_service.request_password_reset(email)
            except Exception:
                app.logger.warning("Password reset request was not delivered")

        try:
            password_reset_dispatcher(request_reset)
        except Exception:
            app.logger.warning("Password reset request was not delivered")
        return render_template(
            "forgot_password.html", message=PASSWORD_RESET_REQUEST_MESSAGE
        )

    @app.route("/reset-password", methods=["GET", "POST"])
    @csrf_protected
    def reset_password():
        if request.method == "GET":
            return render_template("reset_password.html")
        password = request.form.get("password", "")
        if password != request.form.get("password_confirm", ""):
            return render_template("reset_password.html", error="\u4e24\u6b21\u5bc6\u7801\u4e0d\u4e00\u81f4"), 400
        if email_auth_service is None:
            return render_template(
                "reset_password.html", error=EMAIL_SERVICE_UNAVAILABLE_ERROR
            ), 503
        try:
            email_auth_service.reset_password(
                request.form.get("email", ""), request.form.get("code", ""), password
            )
        except ValueError as error:
            return render_template("reset_password.html", error=str(error)), 400
        except Exception:
            app.logger.error("Unable to reset password")
            return render_template("reset_password.html", error=GENERIC_ERROR), 500
        session.clear()
        return redirect(url_for("login", reset="success"))

    @app.route("/register", methods=["GET", "POST"])
    @csrf_protected
    def register():
        if request.method == "GET":
            return render_template("register.html")
        if email_auth_service is None:
            return render_template(
                "register.html", error=EMAIL_SERVICE_UNAVAILABLE_ERROR
            ), 503

        email = request.form.get("email", "")
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        if password != password_confirm:
            return render_template("register.html", error="两次密码不一致"), 400

        try:
            email = normalize_email(email)
            attempt_id = email_auth_service.request_registration(email, password)
        except ValueError as error:
            return render_template("register.html", error=str(error)), 400
        except Exception:
            app.logger.error("Unable to request registration email")
            return render_template("register.html", error=GENERIC_ERROR), 500

        session.clear()
        session["pending_email"] = email
        session["pending_registration_attempt_id"] = attempt_id
        return redirect(url_for("verify_email"))

    @app.route("/verify-email", methods=["GET", "POST"])
    @csrf_protected
    def verify_email():
        email = session.get("pending_email")
        attempt_id = session.get("pending_registration_attempt_id")
        if not email or not attempt_id:
            return redirect(url_for("register"))
        if request.method == "GET":
            return render_template("verify_email.html", email=email)
        if email_auth_service is None:
            return render_template(
                "verify_email.html", email=email, error=EMAIL_SERVICE_UNAVAILABLE_ERROR
            ), 503

        try:
            identity = email_auth_service.verify_registration(
                email, attempt_id, request.form.get("code", "")
            )
        except ValueError as error:
            return render_template("verify_email.html", email=email, error=str(error)), 400
        except Exception:
            app.logger.error("Unable to verify registration email")
            return render_template("verify_email.html", email=email, error=GENERIC_ERROR), 500

        session.clear()
        session["username"] = identity["username"]
        session["email"] = identity["email"]
        session["session_version"] = identity["session_version"]
        return redirect(url_for("workspace"))

    @app.route("/admin/login", methods=["GET", "POST"])
    @csrf_protected
    def admin_login():
        if request.method == "GET":
            return render_template("admin_login.html")

        try:
            username = validate_username(request.form.get("username", ""))
        except ValueError:
            return render_template("admin_login.html", error=INVALID_LOGIN_ERROR), 401
        password = request.form.get("password", "")
        if not password.strip():
            return render_template("admin_login.html", error=INVALID_LOGIN_ERROR), 401
        try:
            identity = user_database.authenticate_administrator(username, password)
        except Exception:
            app.logger.error("Unable to verify administrator credentials")
            identity = None
        if identity is None:
            return render_template("admin_login.html", error=INVALID_LOGIN_ERROR), 401

        session.clear()
        session["username"] = identity["username"]
        session["is_admin"] = True
        if "email" in identity:
            session["email"] = identity["email"]
            session["session_version"] = identity["session_version"]
        return redirect("/admin/users")

    def admin_target_username(username):
        """Validate an account mutation target and prevent self-modification."""
        try:
            normalized_username = validate_username(username)
        except ValueError:
            abort(400)
        if normalized_username == session.get("username"):
            abort(400)
        return normalized_username

    def admin_store_error(operation):
        """Log an administrator store failure without exposing its details."""
        app.logger.exception("Unable to %s", operation)
        return f"<p>{GENERIC_ERROR}</p>", 500

    @app.get("/admin/users")
    @admin_capability_required(ACCOUNTS_READ)
    def admin_users():
        try:
            users = user_database.list_users()
        except Exception:
            return admin_store_error("list administrator accounts")
        return render_template(
            "admin_users.html",
            users=users,
            current_username=session.get("username"),
            current_principal=admin_authorization.principal_for(session["username"]),
        )

    @app.post("/admin/users/<username>/active")
    @admin_capability_required(ACCOUNTS_MANAGE)
    @csrf_protected
    def admin_set_active(username):
        target_username = admin_target_username(username)
        value = request.form.get("is_active")
        if value not in {"true", "false"}:
            abort(400)
        try:
            updated = user_database.set_active(target_username, value == "true")
        except Exception:
            return admin_store_error("set account active state")
        if not updated:
            abort(404)
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<username>/role")
    @admin_capability_required(ACCOUNTS_MANAGE)
    @csrf_protected
    def admin_set_role(username):
        target_username = admin_target_username(username)
        role = request.form.get("role")
        if role not in {"admin", "user"}:
            abort(400)
        try:
            updated = user_database.set_role(target_username, role)
        except Exception:
            return admin_store_error("set account role")
        if not updated:
            abort(404)
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<username>/password")
    @admin_capability_required(ACCOUNTS_MANAGE)
    @csrf_protected
    def admin_reset_password(username):
        target_username = admin_target_username(username)
        password = request.form.get("password", "")
        try:
            validate_password(password)
        except ValueError:
            abort(400)
        try:
            updated = user_database.reset_password(target_username, password)
        except Exception:
            return admin_store_error("reset account password")
        if not updated:
            abort(404)
        return redirect(url_for("admin_users"))

    @app.post("/logout")
    @csrf_protected
    def logout():
        session.clear()
        return redirect(url_for("home"))

    @app.get("/workspace")
    @login_required
    def workspace():
        return render_template("chat/index.html")

    @app.get("/api/desktop/session")
    @login_required
    def desktop_session():
        response = jsonify(
            authenticated=True,
            username=session["username"],
            csrf_token=csrf_token(),
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/account/security")
    @login_required
    def account_security():
        """Show only the signed-in account's non-sensitive binding status."""
        email = session.get("email")
        if not isinstance(email, str) or not email:
            session.clear()
            return redirect(url_for("login"))
        identity = user_database.get_session_identity(email)
        if identity is None:
            session.clear()
            return redirect(url_for("login"))
        phone = identity.get("phone_number")
        phone_bound = bool(identity.get("phone_verified_at"))
        challenge = user_database.get_phone_binding_challenge(email)
        feedback = dict(get_flashed_messages(with_categories=True))
        return render_template(
            "account_security.html",
            email=identity["email"],
            phone_bound=phone_bound,
            phone_mask=mask_phone_number(phone) if phone_bound and isinstance(phone, str) else None,
            pending_phone_mask=(
                mask_phone_number(challenge["phone"])
                if not phone_bound and challenge is not None
                else None
            ),
            message=feedback.get("message"),
            error=feedback.get("error"),
            wechat_bound=bool(identity.get("wechat_bound_at")),
        )

    @app.post("/account/security/phone/send")
    @login_required
    @csrf_protected
    def send_phone_binding_code():
        email = session.get("email")
        if not isinstance(email, str) or not email:
            session.clear()
            return redirect(url_for("login"))
        sms_service = app.extensions["sms_auth_service"]
        if sms_service is None:
            return SMS_SERVICE_UNAVAILABLE_ERROR, 503
        try:
            phone = normalize_mainland_phone(request.form.get("phone", ""))
        except ValueError as error:
            return str(error), 400

        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        try:
            binding_state = user_database.start_phone_binding_challenge(email, phone, now)
        except Exception:
            app.logger.error("Unable to prepare phone binding challenge")
            return SMS_RETRY_LATER_ERROR, 503
        if binding_state == "phone_already_bound":
            return PHONE_ALREADY_BOUND_ERROR, 400
        if binding_state == "cooldown":
            return SMS_RETRY_LATER_ERROR, 429
        if binding_state != "ready":
            return ACCOUNT_ALREADY_BOUND_ERROR, 400

        try:
            sms_service.send_binding_code(phone)
        except Exception:
            app.logger.warning("Phone binding code delivery failed")
            try:
                user_database.clear_phone_binding_challenge(email, phone, now)
            except Exception:
                app.logger.error("Unable to clear failed phone binding challenge")
            return SMS_RETRY_LATER_ERROR, 503
        flash("验证码已发送，请输入短信验证码完成绑定", "message")
        return redirect(url_for("account_security"))

    @app.post("/account/security/phone/verify")
    @login_required
    @csrf_protected
    def verify_phone_binding_code():
        email = session.get("email")
        if not isinstance(email, str) or not email:
            session.clear()
            return redirect(url_for("login"))
        sms_service = app.extensions["sms_auth_service"]
        if sms_service is None:
            return SMS_SERVICE_UNAVAILABLE_ERROR, 503
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        try:
            challenge = user_database.claim_phone_binding_verification(email, now)
        except Exception:
            app.logger.error("Unable to claim phone binding verification")
            return SMS_RETRY_LATER_ERROR, 503
        if challenge is None:
            return PHONE_BINDING_CODE_INVALID_ERROR, 400
        phone = challenge["phone"]
        try:
            verified = sms_service.verify_binding_code(phone, request.form.get("code", ""))
        except Exception:
            app.logger.warning("Phone binding code verification failed")
            return SMS_RETRY_LATER_ERROR, 503
        if not verified:
            return PHONE_BINDING_CODE_INVALID_ERROR, 400

        try:
            binding_state = user_database.consume_verified_phone_binding(
                email, phone, challenge["sent_at"], now
            )
        except Exception:
            app.logger.error("Unable to persist verified phone binding")
            return SMS_RETRY_LATER_ERROR, 503
        if binding_state == "phone_already_bound":
            return PHONE_ALREADY_BOUND_ERROR, 400
        if binding_state != "bound":
            return PHONE_BINDING_CODE_INVALID_ERROR, 400
        flash("手机号已绑定", "message")
        return redirect(url_for("account_security"))

    @app.get("/chat")
    @login_required
    def chat_workspace():
        return render_template("chat/index.html")

    @app.get("/model-plaza")
    def model_plaza():
        """Show safe, server-curated model metadata without a relay capability."""
        return render_template("model_plaza.html")

    @app.get("/token-hub")
    def retired_token_hub():
        """Keep old bookmarks safe by redirecting to the non-relay model plaza."""
        return redirect(url_for("model_plaza"))

    @app.get("/api/models/catalog")
    @login_required
    def model_catalog():
        """Expose non-sensitive model choices for the authenticated workspace."""
        return jsonify(public_models.catalog())

    @app.get("/api/public/models/catalog")
    def public_model_catalog():
        """Expose cached, non-sensitive model metadata to model-plaza visitors."""
        return jsonify(public_models.catalog())

    @app.post("/api/media/vision")
    @login_required
    @csrf_protected(protect_api=True)
    def describe_image():
        service = app.extensions["media_service"]
        if service is None:
            return jsonify(error="图像服务尚未配置"), 503
        image = request.files.get("image")
        prompt = request.form.get("prompt", "")
        model_id = request.form.get("model_id", "")
        if image is None:
            return jsonify(error="请选择图片文件"), 400
        try:
            message = service.describe_image(
                model_id, prompt, image.read(), image.mimetype or ""
            )
            return jsonify(message=message)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except TimeoutError:
            return jsonify(error="图像理解请求超时，请稍后重试"), 504
        except RuntimeError:
            app.logger.exception("Image understanding request failed")
            return jsonify(error=GENERIC_ERROR), 503

    @app.post("/api/media/images/generate")
    @login_required
    @csrf_protected(protect_api=True)
    def generate_image():
        service = app.extensions["media_service"]
        if service is None:
            return jsonify(error="图像服务尚未配置"), 503
        payload = request.get_json(silent=True) or {}
        try:
            images = service.generate_image(
                payload.get("model_id", ""), payload.get("prompt", ""), payload.get("size")
            )
            return jsonify(images=images)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except TimeoutError:
            return jsonify(error="图像生成请求超时，请稍后重试"), 504
        except RuntimeError:
            app.logger.exception("Image generation request failed")
            return jsonify(error=GENERIC_ERROR), 503

    def current_user_id():
        """Resolve the signed-in account without trusting a client-supplied user id."""
        return user_database.get_user_id(session["username"])

    def owned_order_or_404(order_no):
        """Hide orders owned by another account behind the same 404 response."""
        try:
            order = payment_service.get_order(order_no)
            if order["user_id"] != current_user_id():
                abort(404)
            return order
        except KeyError:
            abort(404)

    @app.post("/api/recharge/orders")
    @login_required
    @csrf_protected(protect_api=True)
    def create_recharge_order():
        if not app.config.get("ENABLE_MOCK_PAYMENTS"):
            return jsonify(error="模拟支付未启用"), 503
        payload = request.get_json(silent=True) or {}
        try:
            result = payment_service.create_recharge(
                current_user_id(),
                payload.get("amount_minor"),
                payload.get("region", "CN"),
                payload.get("currency", "CNY"),
            )
            return jsonify(result), 201
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except KeyError:
            return jsonify(error="用户不存在"), 404
        except Exception:
            app.logger.exception("Unable to create recharge order")
            return jsonify(error=GENERIC_ERROR), 500

    @app.get("/api/recharge/orders/<order_no>")
    @login_required
    def recharge_order_status(order_no):
        return jsonify(owned_order_or_404(order_no))

    @app.post("/api/payments/wechat/mock/callback")
    def mock_wechat_callback():
        if not app.config.get("ENABLE_MOCK_PAYMENTS"):
            return jsonify(error="模拟支付未启用"), 503
        expected = app.config.get("MOCK_PAYMENT_CALLBACK_TOKEN", "")
        provided = request.headers.get("X-Mock-Payment-Token", "")
        if not isinstance(expected, str) or not expected:
            return jsonify(error="模拟支付回调未配置"), 503
        if not isinstance(provided, str) or not hmac.compare_digest(expected, provided):
            return jsonify(error="回调认证失败"), 403
        try:
            return jsonify(payment_service.handle_mock_callback(request.get_json(silent=True) or {}))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except KeyError:
            return jsonify(error="订单不存在"), 404
        except Exception:
            app.logger.exception("Unable to process mock payment callback")
            return jsonify(error=GENERIC_ERROR), 500

    @app.post("/api/recharge/orders/<order_no>/refund")
    @login_required
    @csrf_protected(protect_api=True)
    def refund_recharge_order(order_no):
        if not app.config.get("ENABLE_MOCK_PAYMENTS"):
            return jsonify(error="模拟支付未启用"), 503
        owned_order_or_404(order_no)
        try:
            return jsonify(payment_service.refund(order_no))
        except ValueError as error:
            return jsonify(error=str(error)), 409
        except KeyError:
            return jsonify(error="订单不存在"), 404
        except Exception:
            app.logger.exception("Unable to refund recharge order")
            return jsonify(error=GENERIC_ERROR), 500

    @app.post("/api/admin/models/catalog/refresh")
    @admin_capability_required(MODEL_CATALOG_REFRESH)
    @csrf_protected(protect_api=True)
    def refresh_model_catalog_route():
        """Refresh and return the non-sensitive provider directory for administrators."""
        catalog_refresh.refresh_once(manual=True)
        return jsonify(public_models.catalog())

    @app.get("/api/conversations")
    @login_required
    def conversations():
        response = jsonify(database.list_conversations(session["username"]))
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.post("/api/conversations")
    @login_required
    @csrf_protected(protect_api=True)
    def create_conversation():
        payload = request.get_json(silent=True) or {}
        title = payload.get("title", "新对话")
        if not isinstance(title, str) or not title.strip():
            title = "新对话"
        return jsonify(database.create_conversation(session["username"], title.strip()[:255])), 201

    @app.put("/api/conversations/<conversation_id>")
    @login_required
    @csrf_protected(protect_api=True)
    def rename_conversation(conversation_id):
        payload = request.get_json(silent=True) or {}
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            return jsonify(error="会话名称不能为空"), 400
        conversation = database.rename_conversation(
            conversation_id, session["username"], title.strip()[:255]
        )
        if conversation is None:
            return jsonify(error="会话不存在或无权访问"), 404
        return jsonify(conversation)

    @app.delete("/api/conversations/<conversation_id>")
    @login_required
    @csrf_protected(protect_api=True)
    def delete_conversation(conversation_id):
        attachment_store = getattr(app.extensions["attachment_service"], "store", None)
        lifecycle = (
            attachment_store.conversation_lifecycle(session["username"], conversation_id)
            if attachment_store is not None
            else nullcontext()
        )
        with lifecycle:
            if not database.delete_conversation(conversation_id, session["username"]):
                return jsonify(error="会话不存在或无权访问"), 404
            broadcast_service.cancel_conversation(conversation_id)
            if attachment_store is not None:
                attachment_store.discard_conversation(session["username"], conversation_id)
        return jsonify(deleted=True, conversation_id=conversation_id)

    @app.get("/api/conversations/<conversation_id>/messages")
    @login_required
    def conversation_messages(conversation_id):
        username = session["username"]
        if not database.conversation_exists(conversation_id, username):
            return jsonify(error="会话不存在或无权访问"), 404
        return jsonify(
            public_models.expose_messages(
                database.get_conversation_messages(conversation_id, username)
            )
        )

    @app.get("/api/conversations/<conversation_id>/models")
    @login_required
    def conversation_model_settings(conversation_id):
        username = session["username"]
        if not database.conversation_exists(conversation_id, username):
            return jsonify(error="会话不存在或无权访问"), 404
        return jsonify(
            models=public_models.to_public_settings(
                database.get_conversation_model_settings(conversation_id, username)
            )
        )

    @app.put("/api/conversations/<conversation_id>/models")
    @login_required
    @csrf_protected(protect_api=True)
    def replace_conversation_model_settings(conversation_id):
        username = session["username"]
        if not database.conversation_exists(conversation_id, username):
            return jsonify(error="会话不存在或无权访问"), 404
        payload = request.get_json(silent=True) or {}
        try:
            internal_settings = public_models.to_internal_settings(payload.get("models"))
            settings = database.replace_conversation_model_settings(
                conversation_id, username, internal_settings
            )
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return jsonify(models=public_models.to_public_settings(settings))

    @app.get("/api/model-presets")
    @login_required
    def model_presets():
        return jsonify(
            public_models.expose_presets(
                database.list_model_presets(session["username"])
            )
        )

    @app.post("/api/model-presets")
    @login_required
    @csrf_protected(protect_api=True)
    def create_model_preset():
        payload = request.get_json(silent=True) or {}
        try:
            internal_settings = public_models.to_internal_settings(payload.get("models"))
            preset = database.create_model_preset(
                session["username"], payload.get("name"), internal_settings
            )
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return jsonify(public_models.expose_presets([preset])[0]), 201

    @app.delete("/api/model-presets/<int:preset_id>")
    @login_required
    @csrf_protected(protect_api=True)
    def delete_model_preset(preset_id):
        if not database.delete_model_preset(preset_id, session["username"]):
            return jsonify(error="模型组合不存在或无权访问"), 404
        return "", 204

    @app.get("/api/history")
    @login_required
    def history():
        try:
            return jsonify(public_models.expose_messages(database.get_all_messages()))
        except Exception:
            app.logger.exception("Unable to load chat history")
            return jsonify(error=GENERIC_ERROR), 500

    @app.post("/api/chat")
    @login_required
    def chat():
        payload = request.get_json(silent=True)
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, str) or not message.strip():
            return jsonify(error="娑堟伅涓嶈兘涓虹┖"), 400

        selected_models = payload.get("models", payload.get("selected_models", []))
        conversation_id = payload.get("conversation_id")
        if conversation_id is not None and not database.conversation_exists(conversation_id, session["username"]):
            return jsonify(error="会话不存在或无权访问"), 404
        models, cleaned_message = parse_targets(message.strip(), selected_models)
        if not cleaned_message:
            return jsonify(error="\u8bf7\u8f93\u5165\u6d88\u606f\u5185\u5bb9"), 400
        try:
            task_id = broadcast_service.submit(
                cleaned_message,
                models,
                username=session["username"],
                **({"conversation_id": conversation_id} if conversation_id is not None else {}),
            )
            return jsonify(task_id=task_id, models=models), 202
        except Exception:
            app.logger.exception("Unable to process chat message")
            return jsonify(error=GENERIC_ERROR), 500

    @app.post("/api/chat/stream")
    @login_required
    def chat_stream():
        payload = request.get_json(silent=True)
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, str) or not message.strip():
            return jsonify(error="消息不能为空"), 400

        selected_models = payload.get("models", payload.get("selected_models", []))
        conversation_id = payload.get("conversation_id")
        if conversation_id is not None and not database.conversation_exists(conversation_id, session["username"]):
            return jsonify(error="会话不存在或无权访问"), 404
        models, cleaned_message = parse_targets(message.strip(), selected_models)
        if not cleaned_message:
            return jsonify(error="请输入消息内容"), 400

        @stream_with_context
        def generate():
            try:
                for event in broadcast_service.stream_events(
                    cleaned_message,
                    models,
                    username=session["username"],
                    **({"conversation_id": conversation_id} if conversation_id is not None else {}),
                ):
                    event_name = event.pop("event")
                    yield f"event: {event_name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception:
                app.logger.exception("Unable to stream chat message")
                payload = {"message": GENERIC_ERROR}
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/model-chat/stream")
    @login_required
    def dynamic_model_chat_stream():
        """Stream one verified dynamic model with the shared collaboration context."""
        payload = request.get_json(silent=True) or {}
        message = payload.get("message")
        model = payload.get("model")
        conversation_id = payload.get("conversation_id")
        if not isinstance(message, str) or not message.strip():
            return jsonify(error="消息不能为空"), 400
        if not isinstance(model, str) or not model.strip():
            return jsonify(error="请选择模型"), 400
        if not isinstance(conversation_id, str) or not database.conversation_exists(
            conversation_id, session["username"]
        ):
            return jsonify(error="会话不存在或无权访问"), 404
        try:
            internal_model = public_models.resolve(model)
            app.extensions["model_registry"].resolve(internal_model)
        except ValueError:
            return jsonify(error=PUBLIC_MODEL_UNAVAILABLE), 400
        prices = settings.MODEL_PRICES_MICRO_POINTS
        if internal_model.lower() not in prices:
            return jsonify(error="该模型尚未配置平台积分价格"), 400

        @stream_with_context
        def generate():
            try:
                context_service = app.extensions["collaboration_context"]
                history = context_service.build(conversation_id, internal_model)
                database.add_collaboration_message(
                    "user", message.strip(), conversation_id=conversation_id
                )
                yield f"event: start\ndata: {json.dumps({'models': [model]}, ensure_ascii=False)}\n\n"
                reply_parts = []
                billing_context = (
                    adapter.billing_user(session["username"])
                    if hasattr(adapter, "billing_user")
                    else nullcontext()
                )
                with billing_context:
                    for token in adapter.stream_reply_with_history(
                        message.strip(), history, model=internal_model
                    ):
                        reply_parts.append(token)
                        event = {"model": model, "token": token}
                        yield f"event: token\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                saved = database.add_collaboration_message(
                    "assistant",
                    "".join(reply_parts),
                    model=internal_model,
                    conversation_id=conversation_id,
                )
                context_service.mark_synced(conversation_id, internal_model, saved["id"])
                yield f"event: done\ndata: {json.dumps({'model': model}, ensure_ascii=False)}\n\n"
            except Exception:
                app.logger.exception("Unable to stream dynamic model message")
                yield f"event: error\ndata: {json.dumps({'message': GENERIC_ERROR}, ensure_ascii=False)}\n\n"

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/workspace/stream")
    @login_required
    @csrf_protected(protect_api=True)
    def workspace_stream():
        """Stream selected workbench models without legacy @-mention dispatch."""
        payload = request.get_json(silent=True) or {}
        message = payload.get("message")
        conversation_id = payload.get("conversation_id")
        model_settings = payload.get("models")
        attachment_tokens = payload.get("attachment_tokens", [])
        if not isinstance(message, str) or not message.strip():
            return jsonify(error="消息不能为空"), 400
        if not isinstance(model_settings, list) or not model_settings:
            return jsonify(error="请至少选择一个模型"), 400
        if not isinstance(conversation_id, str) or not database.conversation_exists(
            conversation_id, session["username"]
        ):
            return jsonify(error="会话不存在或无权访问"), 404
        if not isinstance(attachment_tokens, list) or not all(
            isinstance(token, str) and token for token in attachment_tokens
        ):
            return jsonify(error="附件参数无效"), 400
        try:
            internal_settings = public_models.to_internal_settings(model_settings)
        except ValueError:
            return jsonify(error=PUBLIC_MODEL_UNAVAILABLE), 400
        attachment_store = app.extensions["attachment_service"].store
        with attachment_store.conversation_lifecycle(session["username"], conversation_id):
            if not database.conversation_exists(conversation_id, session["username"]):
                return jsonify(error="会话不存在或无权访问"), 404
            attachments = attachment_store.resolve(
                session["username"], conversation_id, attachment_tokens
            )
            if not database.conversation_exists(conversation_id, session["username"]):
                return jsonify(error="会话不存在或无权访问"), 404
        if len(attachments) != len(attachment_tokens):
            return jsonify(error="附件已过期或无权使用，请重新添加"), 400
        attachment_context = "\n\n".join(item["context"] for item in attachments)
        model_message = message.strip()
        if attachment_context:
            model_message = f"{model_message}\n\n{attachment_context}"
        attachment_summary = "\n".join(
            f"[附件：{item['name']}]" for item in attachments
        )
        history_message = message.strip()
        if attachment_summary:
            history_message = f"{history_message}\n{attachment_summary}"
        reverse_models = {
            internal["model_id"]: public["model_id"]
            for public, internal in zip(model_settings, internal_settings)
        }

        @stream_with_context
        def generate():
            try:
                for event in app.extensions["workspace_stream_service"].stream(
                    model_message,
                    conversation_id,
                    session["username"],
                    internal_settings,
                    history_message=history_message,
                ):
                    serialized = dict(event)
                    event_name = serialized.pop("event")
                    if isinstance(serialized.get("model"), str):
                        serialized["model"] = reverse_models.get(
                            serialized["model"], "other/unavailable-model"
                        )
                    if isinstance(serialized.get("models"), list):
                        serialized["models"] = [
                            reverse_models.get(model, "other/unavailable-model")
                            for model in serialized["models"]
                            if isinstance(model, str)
                        ]
                    yield f"event: {event_name}\ndata: {json.dumps(serialized, ensure_ascii=False)}\n\n"
            except ValueError:
                yield f"event: error\ndata: {json.dumps({'message': PUBLIC_MODEL_UNAVAILABLE}, ensure_ascii=False)}\n\n"
            except Exception:
                app.logger.exception("Unable to stream workspace message")
                yield f"event: error\ndata: {json.dumps({'message': GENERIC_ERROR}, ensure_ascii=False)}\n\n"

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/workspace/attachments")
    @login_required
    @csrf_protected(protect_api=True)
    def workspace_attachments():
        """Preprocess files into short-lived task context without saving raw bytes."""
        conversation_id = request.form.get("conversation_id")
        if not isinstance(conversation_id, str) or not database.conversation_exists(
            conversation_id, session["username"]
        ):
            return jsonify(error="\u4f1a\u8bdd\u4e0d\u5b58\u5728\u6216\u65e0\u6743\u8bbf\u95ee"), 404
        attachment_service = app.extensions["attachment_service"]
        attachment_store = getattr(attachment_service, "store", None)
        lifecycle = (
            attachment_store.conversation_lifecycle(session["username"], conversation_id)
            if attachment_store is not None
            else nullcontext()
        )
        with lifecycle:
            if not database.conversation_exists(conversation_id, session["username"]):
                return jsonify(error="会话不存在或无权访问"), 404
            try:
                attachments = attachment_service.prepare_files(
                    session["username"], conversation_id, request.files.getlist("files")
                )
            except ValueError as error:
                return jsonify(error=str(error)), 400
            if not database.conversation_exists(conversation_id, session["username"]):
                if attachment_store is not None:
                    attachment_store.discard_conversation(
                        session["username"], conversation_id
                    )
                return jsonify(error="会话不存在或无权访问"), 404
        return jsonify(attachments=attachments), 201

    @app.get("/api/tasks/<task_id>")
    @login_required
    def task_status(task_id):
        task = broadcast_service.status(task_id)
        if task is None:
            return jsonify(error="task not found"), 404
        return jsonify(task)

    return app


if __name__ == "__main__":
    create_app().run(host=config.HOST, port=config.PORT, debug=config.FLASK_DEBUG)
