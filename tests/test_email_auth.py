from datetime import datetime, timedelta

import pytest

from email_auth import (
    EmailAuthService,
    EmailCodeRateLimitError,
    SMTPEmailSender,
    normalize_email,
    validate_password,
)
from user_database import UserDatabase


class RecordingSender:
    def __init__(self):
        self.sent = []

    def send_verification_code(self, email, code, purpose):
        self.sent.append((email, code, purpose))


class FailingOnceSender(RecordingSender):
    def __init__(self):
        super().__init__()
        self.fail_next = True

    def send_verification_code(self, email, code, purpose):
        if self.fail_next:
            self.fail_next = False
            raise OSError("simulated smtp outage")
        super().send_verification_code(email, code, purpose)


def test_normalize_email_and_password_validation_enforce_public_rules():
    assert normalize_email(" Alice@Example.COM ") == "alice@example.com"
    assert validate_password("123456789") == "123456789"
    assert validate_password("123456789012") == "123456789012"

    with pytest.raises(ValueError, match="有效邮箱"):
        normalize_email("not-an-email")
    with pytest.raises(ValueError, match="密码长度"):
        validate_password("12345678")
    with pytest.raises(ValueError, match="密码长度"):
        validate_password("1234567890123")


def test_registration_code_is_six_digits_hashed_and_single_use():
    database = UserDatabase.for_testing()
    database.initialize()
    sender = RecordingSender()
    service = EmailAuthService(database, sender, pepper="test-pepper")

    attempt_id = service.request_registration("alice@example.com", "123456789")

    email, code, purpose = sender.sent[0]
    assert email == "alice@example.com"
    assert code.isdigit() and len(code) == 6
    assert purpose == "register"
    assert code not in repr(database._testing_store["email_codes"])

    identity = service.verify_registration(email, attempt_id, code)
    assert identity["email"] == email
    with pytest.raises(ValueError, match="验证码无效或已过期"):
        service.verify_registration(email, attempt_id, code)


def test_rate_limited_registration_retry_cannot_rebind_an_earlier_code_to_a_new_password():
    database = UserDatabase.for_testing()
    database.initialize()
    sender = RecordingSender()
    service = EmailAuthService(database, sender, pepper="test-pepper")

    attempt_id = service.request_registration("alice@example.com", "123456789")
    _, first_code, _ = sender.sent[0]

    with pytest.raises(ValueError, match="请稍后再试"):
        service.request_registration("alice@example.com", "987654321")

    service.verify_registration("alice@example.com", attempt_id, first_code)
    assert database.verify_email_password("alice@example.com", "123456789") is not None
    assert database.verify_email_password("alice@example.com", "987654321") is None


def test_verified_registration_is_rejected_before_smtp_and_keeps_delivered_records(monkeypatch):
    database = UserDatabase.for_testing()
    database.initialize()
    sender = RecordingSender()
    service = EmailAuthService(database, sender, pepper="test-pepper")
    start = datetime(2026, 7, 22, 12, 0, 0)
    times = iter((start, start + timedelta(seconds=1), start + timedelta(seconds=61)))
    monkeypatch.setattr("email_auth._utc_now", lambda: next(times))

    attempt_id = service.request_registration("alice@example.com", "123456789")
    _, code, _ = sender.sent[0]
    service.verify_registration("alice@example.com", attempt_id, code)
    delivered_records = dict(database._testing_store["email_codes"])

    with pytest.raises(ValueError):
        service.request_registration("alice@example.com", "987654321")

    assert len(sender.sent) == 1
    assert database._testing_store["email_codes"] == delivered_records


def test_registration_store_failure_does_not_send_or_leave_a_code(monkeypatch):
    database = UserDatabase.for_testing()
    database.initialize()
    sender = RecordingSender()
    service = EmailAuthService(database, sender, pepper="test-pepper")

    def fail_account_step(*args):
        raise RuntimeError("simulated account failure")

    monkeypatch.setattr(database, "store_registration_attempt", fail_account_step)

    with pytest.raises(RuntimeError, match="simulated account failure"):
        service.request_registration("alice@example.com", "123456789")

    assert sender.sent == []
    assert database._testing_store["email_codes"] == {}


def test_reset_request_does_not_send_for_unknown_email_and_is_rate_limited_for_known_email():
    database = UserDatabase.for_testing()
    database.initialize()
    sender = RecordingSender()
    service = EmailAuthService(database, sender, pepper="test-pepper")

    service.request_password_reset("missing@example.com")
    assert sender.sent == []

    database.prepare_email_registration("alice@example.com", "123456789")
    database.mark_email_verified("alice@example.com")
    service.request_password_reset("alice@example.com")
    with pytest.raises(ValueError, match="请稍后再试"):
        service.request_password_reset("alice@example.com")


def test_reset_request_exposes_remaining_cooldown_for_a_second_request():
    database = UserDatabase.for_testing()
    database.initialize()
    database.prepare_email_registration("alice@example.com", "123456789")
    database.mark_email_verified("alice@example.com")
    service = EmailAuthService(database, RecordingSender(), pepper="test-pepper")

    service.request_password_reset("alice@example.com")

    with pytest.raises(EmailCodeRateLimitError) as raised:
        service.request_password_reset("alice@example.com")

    assert 1 <= raised.value.retry_after_seconds <= 60


def test_smtp_failure_cancels_unsent_code_and_allows_registration_retry():
    database = UserDatabase.for_testing()
    database.initialize()
    sender = FailingOnceSender()
    service = EmailAuthService(database, sender, pepper="test-pepper")

    with pytest.raises(RuntimeError, match="验证码发送失败"):
        service.request_registration("alice@example.com", "123456789")

    assert database._testing_store["email_codes"] == {}
    service.request_registration("alice@example.com", "123456789")
    assert len(sender.sent) == 1


def test_smtp_failure_exposes_only_a_safe_unlinked_exception():
    database = UserDatabase.for_testing()
    database.initialize()
    service = EmailAuthService(database, FailingOnceSender(), pepper="test-pepper")

    with pytest.raises(RuntimeError) as raised:
        service.request_registration("alice@example.com", "123456789")

    assert str(raised.value) == "验证码发送失败，请稍后重试"
    assert raised.value.__cause__ is None
    assert "simulated smtp outage" not in str(raised.value)


def test_smtp_failure_with_cleanup_error_raises_a_distinct_safe_exception(monkeypatch):
    database = UserDatabase.for_testing()
    database.initialize()
    service = EmailAuthService(database, FailingOnceSender(), pepper="test-pepper")

    def cleanup_fails(code_id):
        raise OSError("raw cleanup server detail")

    monkeypatch.setattr(database, "cancel_email_code", cleanup_fails)
    with pytest.raises(RuntimeError) as raised:
        service.request_registration("alice@example.com", "123456789")

    assert str(raised.value) == "验证码发送失败，验证码状态清理失败，请稍后重试"
    assert raised.value.__cause__ is None
    assert "raw cleanup server detail" not in str(raised.value)


def test_reset_password_consumes_code_and_updates_verified_user_password():
    database = UserDatabase.for_testing()
    database.initialize()
    database.prepare_email_registration("alice@example.com", "123456789")
    database.mark_email_verified("alice@example.com")
    sender = RecordingSender()
    service = EmailAuthService(database, sender, pepper="test-pepper")

    service.request_password_reset("alice@example.com")
    _, code, purpose = sender.sent[0]
    assert purpose == "reset_password"
    service.reset_password("alice@example.com", code, "987654321")

    assert database.verify_email_password("alice@example.com", "123456789") is None
    assert database.verify_email_password("alice@example.com", "987654321") is not None
    with pytest.raises(ValueError, match="验证码无效或已过期"):
        service.reset_password("alice@example.com", code, "123456789")


def test_smtp_sender_uses_tls_login_and_purpose_specific_message(monkeypatch):
    calls = []

    class FakeClient:
        def starttls(self, *, context=None):
            calls.append(("starttls", context))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(("message", message))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def fake_smtp(host, port, timeout):
        calls.append(("connect", host, port, timeout))
        return FakeClient()

    monkeypatch.setattr("email_auth.smtplib.SMTP", fake_smtp)
    tls_context = object()
    monkeypatch.setattr("email_auth.ssl.create_default_context", lambda: tls_context)
    sender = SMTPEmailSender("smtp.example.com", 587, "sender", "password", "no-reply@example.com")

    sender.send_verification_code("alice@example.com", "123456", "register")

    assert calls[0] == ("connect", "smtp.example.com", 587, 15)
    assert ("starttls", tls_context) in calls
    assert ("login", "sender", "password") in calls
    message = next(call[1] for call in calls if isinstance(call, tuple) and call[0] == "message")
    assert message["To"] == "alice@example.com"
    assert message["Subject"] == "注册验证码"
    assert "123456" in message.get_content()
