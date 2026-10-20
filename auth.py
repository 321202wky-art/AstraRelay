"""Session authentication helpers for the chat application."""

import hmac
import secrets
from functools import wraps

from flask import abort, current_app, jsonify, redirect, request, session, url_for


_PLACEHOLDER_SECRETS = {
    "change-this-development-secret",
    "change-me",
    "replace-with-a-real-production-secret-before-deploying",
    "replace-with-your-secret-key",
    "your-secret-key",
    "your-admin-password",
}

_LOGIN_DESTINATIONS = frozenset({"/chat", "/workspace"})


def safe_login_destination(value):
    """Return an exact, local workspace destination for post-login navigation."""
    return value if isinstance(value, str) and value in _LOGIN_DESTINATIONS else "/workspace"


def login_required(view):
    """Require a signed user session before calling a protected view."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("username"):
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify(error="authentication required"), 401
        if request.method == "GET" and request.path in _LOGIN_DESTINATIONS:
            return redirect(url_for("login", next=request.path))
        return redirect(url_for("login"))

    return wrapped_view


def csrf_token() -> str:
    """Return the current session's CSRF token, creating it when needed."""
    token = session.get("csrf_token")
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def csrf_protected(view=None, *, protect_api=False):
    """Require a matching session-bound token for protected POST endpoints."""
    def decorator(protected_view):
        @wraps(protected_view)
        def wrapped_view(*args, **kwargs):
            requires_token = (
                protect_api
                and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            ) or (
                not request.path.startswith("/api/") and request.method == "POST"
            )
            if requires_token:
                expected_token = session.get("csrf_token")
                provided_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
                if (
                    not isinstance(expected_token, str)
                    or not isinstance(provided_token, str)
                    or not hmac.compare_digest(expected_token, provided_token)
                ):
                    abort(400)
            return protected_view(*args, **kwargs)

        return wrapped_view

    return decorator(view) if view is not None else decorator


def admin_required(view):
    """Require an administrator session for later HTML administration views."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("username") and session.get("is_admin") is True:
            return view(*args, **kwargs)
        return redirect(url_for("admin_login"))

    return wrapped_view


def admin_capability_required(capability):
    """Require a fresh administrator principal with one declared capability."""
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            username = session.get("username")
            if not username or session.get("is_admin") is not True:
                if request.path.startswith("/api/"):
                    return jsonify(error="authentication required"), 401
                return redirect(url_for("admin_login"))
            authorization = current_app.extensions["admin_authorization"]
            try:
                allowed = authorization.allows(username, capability)
            except Exception:
                current_app.logger.error("Unable to authorize administrator action")
                if request.path.startswith("/api/"):
                    return jsonify(error="authorization unavailable"), 503
                abort(503)
            if not allowed:
                if request.path.startswith("/api/"):
                    return jsonify(error="forbidden"), 403
                abort(403)
            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def validate_username(username: str) -> str:
    """Normalize a username and reject blank or overlong values."""
    if not isinstance(username, str):
        raise ValueError("用户名不能为空")
    normalized_username = username.strip()
    if not normalized_username:
        raise ValueError("用户名不能为空")
    if len(normalized_username) > 64:
        raise ValueError("用户名长度不能超过64个字符")
    return normalized_username


def initialize_auth(app, user_database):
    """Configure session signing and optionally create the configured administrator."""
    secret_key = app.config.get("SECRET_KEY")
    username = app.config.get("ADMIN_USERNAME")
    password = app.config.get("ADMIN_PASSWORD")

    if (
        not isinstance(secret_key, str)
        or not secret_key.strip()
        or len(secret_key) < 32
        or secret_key.strip().lower() in _PLACEHOLDER_SECRETS
    ):
        raise ValueError("FLASK_SECRET_KEY must be at least 32 characters and not a placeholder")

    app.secret_key = secret_key
    app.extensions["user_database"] = user_database
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def clear_stale_versioned_session():
        email = session.get("email")
        if not isinstance(email, str) or not email:
            if session.get("username") and session.get("is_admin") is not True:
                session.clear()
            return None
        try:
            identity = user_database.get_session_identity(email)
        except Exception:
            app.logger.error("Unable to validate signed-in account")
            session.clear()
            return None
        if identity is None or identity["session_version"] != session.get("session_version"):
            session.clear()
        return None

    username_is_blank = not isinstance(username, str) or not username.strip()
    password_is_blank = not isinstance(password, str) or not password.strip()
    if username_is_blank and password_is_blank:
        return
    if username_is_blank:
        raise ValueError("ADMIN_USERNAME must not be blank")
    if password_is_blank:
        raise ValueError("ADMIN_PASSWORD must not be blank")
    username = username.strip()
    if len(username) > 64:
        raise ValueError("ADMIN_USERNAME must not exceed 64 characters")
    if len(password) < 12 or password.strip().lower() in _PLACEHOLDER_SECRETS:
        raise ValueError("ADMIN_PASSWORD must be at least 12 characters and not a placeholder")

    app.config["ADMIN_USERNAME"] = username
    user_database.ensure_owner(username, password)
