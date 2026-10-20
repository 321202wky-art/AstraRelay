"""Email verification-code delivery and account recovery service."""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import hmac
import re
import secrets
import ssl
import smtplib

from werkzeug.security import generate_password_hash
from user_database import EmailCodeRateLimitError


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _utc_now():
    """Return a naive UTC value compatible with the existing MySQL DATETIME API."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_email(value):
    """Return a canonical email address or raise a safe validation error."""
    if not isinstance(value, str):
        raise ValueError("请输入有效邮箱")
    email = value.strip().lower()
    if len(email) > 320 or not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("请输入有效邮箱")
    return email


def validate_password(value):
    """Enforce the ordinary-account password length policy."""
    if not isinstance(value, str) or not 9 <= len(value) <= 12:
        raise ValueError("密码长度必须为 9–12 位")
    return value


class SMTPEmailSender:
    """Send verification codes using the standard-library SMTP client."""

    def __init__(self, host, port, username, password, from_email, use_tls=True):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.use_tls = use_tls

    def send_verification_code(self, email, code, purpose):
        """Deliver one code without retaining it in the sender."""
        message = EmailMessage()
        message["From"] = self.from_email
        message["To"] = email
        message["Subject"] = "注册验证码" if purpose == "register" else "密码重设验证码"
        message.set_content(f"您的验证码是 {code}，10 分钟内有效。请勿转发。")
        with smtplib.SMTP(self.host, self.port, timeout=15) as client:
            if self.use_tls:
                client.starttls(context=ssl.create_default_context())
            if self.username:
                client.login(self.username, self.password)
            client.send_message(message)


class EmailAuthService:
    """Issue and consume database-backed registration and recovery codes."""

    def __init__(self, database, sender, pepper):
        self.database = database
        self.sender = sender
        self.pepper = pepper

    def _hash_code(self, email, purpose, code):
        payload = f"{purpose}:{email}:{code}".encode("utf-8")
        return hmac.new(self.pepper.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def _issue(self, email, purpose):
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = _utc_now()
        try:
            code_id = self.database.store_email_code(
                email,
                purpose,
                self._hash_code(email, purpose, code),
                now,
                now + timedelta(minutes=10),
            )
        except EmailCodeRateLimitError:
            raise
        except ValueError as exc:
            raise ValueError("请稍后再试") from exc
        delivery_failed = False
        cleanup_failed = False
        try:
            self.sender.send_verification_code(email, code, purpose)
        except Exception:
            delivery_failed = True
            try:
                self.database.cancel_email_code(code_id)
            except Exception:
                cleanup_failed = True
        if delivery_failed:
            if cleanup_failed:
                raise RuntimeError("验证码发送失败，验证码状态清理失败，请稍后重试") from None
            raise RuntimeError("验证码发送失败，请稍后重试") from None
        return code_id

    def request_registration(self, email, password):
        email = normalize_email(email)
        validate_password(password)
        attempt_id = secrets.token_urlsafe(32)
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = _utc_now()
        try:
            code_id = self.database.store_registration_attempt(
                email,
                attempt_id,
                generate_password_hash(password),
                self._hash_code(email, "register", code),
                now,
                now + timedelta(minutes=10),
            )
        except ValueError as exc:
            if str(exc) == "邮箱已注册":
                raise
            raise ValueError("请稍后再试") from exc
        delivery_failed = False
        cleanup_failed = False
        try:
            self.sender.send_verification_code(email, code, "register")
        except Exception:
            delivery_failed = True
            try:
                self.database.cancel_email_code(code_id)
            except Exception:
                cleanup_failed = True
        if delivery_failed:
            if cleanup_failed:
                raise RuntimeError("验证码发送失败，验证码状态清理失败，请稍后重试") from None
            raise RuntimeError("验证码发送失败，请稍后重试") from None
        return attempt_id

    def request_password_reset(self, email):
        email = normalize_email(email)
        if self.database.find_verified_user_by_email(email) is not None:
            self._issue(email, "reset_password")

    def verify_registration(self, email, attempt_id, code):
        email = normalize_email(email)
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ValueError("验证码无效或已过期")
        digest = self._hash_code(email, "register", code)
        identity = self.database.consume_registration_attempt(
            email, attempt_id, digest, _utc_now()
        )
        if identity is None:
            raise ValueError("验证码无效或已过期")
        return identity

    def reset_password(self, email, code, new_password):
        email = normalize_email(email)
        validate_password(new_password)
        digest = self._hash_code(email, "reset_password", code)
        if not self.database.consume_email_code(email, "reset_password", digest, _utc_now()):
            raise ValueError("验证码无效或已过期")
        if not self.database.reset_password_by_email(email, new_password):
            raise ValueError("验证码无效或已过期")
