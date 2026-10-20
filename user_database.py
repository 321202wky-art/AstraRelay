"""Standalone user-account storage backed by the configured MySQL database."""

import hmac
import hashlib
import re
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from werkzeug.security import check_password_hash, generate_password_hash


VALID_ROLES = {"admin", "user"}


class EmailCodeRateLimitError(ValueError):
    """Expose only the remaining cooldown for a verification-code resend."""

    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = max(1, min(60, int(retry_after_seconds)))
        super().__init__("\u8bf7\u7a0d\u540e\u518d\u8bd5")


class UserDatabase:
    """Manage account records without coupling them to chat-memory storage."""

    def __init__(self, config, connection_factory=None):
        self.config = config
        self._connection_factory = connection_factory or self._pymysql_connect

    @classmethod
    def for_testing(cls):
        """Build an isolated account database backed by a small in-memory fake."""
        phone_binding_lock = RLock()
        store = {
            "users": {},
            "admin_principals": {},
            "wallets": {},
            "wallet_transactions": {},
            "orders": {},
            "email_codes": {},
            "next_email_code_id": 1,
            "phone_binding_lock": phone_binding_lock,
        }

        def connect(**options):
            return _MemoryConnection(store, options.get("database"), phone_binding_lock)

        database = cls(
            {
                "MYSQL_HOST": "localhost",
                "MYSQL_PORT": 3306,
                "MYSQL_USER": "root",
                "MYSQL_PASSWORD": "",
                "USER_DATABASE": "user_db",
            },
            connection_factory=connect,
        )
        database._testing_store = store
        database._testing_phone_binding_lock = phone_binding_lock
        return database

    def initialize(self):
        database_name = self._database_name()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database_name}` CHARACTER SET utf8mb4"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        connection = self._connect(database=database_name)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS user_accounts ("
                    "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
                    "username VARCHAR(64) NOT NULL UNIQUE, "
                    "password_hash VARCHAR(255) NOT NULL, "
                    "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
                    "role VARCHAR(16) NOT NULL DEFAULT 'user', "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    ") CHARACTER SET utf8mb4"
                )
                for statement in (
                    "ALTER TABLE user_accounts ADD COLUMN email VARCHAR(320) NULL",
                    "ALTER TABLE user_accounts ADD COLUMN email_verified_at TIMESTAMP NULL",
                    "ALTER TABLE user_accounts ADD COLUMN session_version BIGINT NOT NULL DEFAULT 0",
                    "ALTER TABLE user_accounts ADD COLUMN phone_number VARCHAR(32) NULL",
                    "ALTER TABLE user_accounts ADD COLUMN phone_verified_at TIMESTAMP NULL",
                    "ALTER TABLE user_accounts ADD COLUMN phone_binding_pending_phone VARCHAR(32) NULL",
                    "ALTER TABLE user_accounts ADD COLUMN phone_binding_sent_at DATETIME NULL",
                    "ALTER TABLE user_accounts ADD COLUMN phone_binding_expires_at DATETIME NULL",
                    "ALTER TABLE user_accounts ADD COLUMN phone_binding_failed_attempts INT NOT NULL DEFAULT 0",
                    "ALTER TABLE user_accounts ADD COLUMN wechat_openid VARCHAR(128) NULL",
                    "ALTER TABLE user_accounts ADD COLUMN wechat_bound_at TIMESTAMP NULL",
                ):
                    self._execute_optional_migration(cursor, statement)
                self._execute_optional_migration(
                    cursor,
                    "CREATE UNIQUE INDEX uq_user_accounts_email ON user_accounts (email)",
                )
                self._execute_optional_migration(
                    cursor,
                    "CREATE UNIQUE INDEX uq_user_accounts_phone_number ON user_accounts (phone_number)",
                )
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS email_verification_codes ("
                    "id BIGINT AUTO_INCREMENT PRIMARY KEY, email VARCHAR(320) NOT NULL, "
                    "purpose ENUM('register','reset_password') NOT NULL, code_hash CHAR(64) NOT NULL, "
                    "attempt_id VARCHAR(128) NULL, pending_password_hash VARCHAR(255) NULL, "
                    "expires_at DATETIME NOT NULL, sent_at DATETIME NOT NULL, failed_attempts INT NOT NULL DEFAULT 0, "
                    "used_at DATETIME NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "INDEX idx_email_purpose_sent (email, purpose, sent_at)"
                    ") CHARACTER SET utf8mb4"
                )
                for statement in (
                    "ALTER TABLE email_verification_codes ADD COLUMN attempt_id VARCHAR(128) NULL",
                    "ALTER TABLE email_verification_codes ADD COLUMN pending_password_hash VARCHAR(255) NULL",
                ):
                    self._execute_optional_migration(cursor, statement)
                self._execute_optional_migration(
                    cursor,
                    "CREATE UNIQUE INDEX uq_email_verification_attempt "
                    "ON email_verification_codes (attempt_id)",
                )
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS user_wallets ("
                    "user_id BIGINT PRIMARY KEY, balance_points BIGINT NOT NULL DEFAULT 0, "
                    "frozen_points BIGINT NOT NULL DEFAULT 0, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
                    "FOREIGN KEY (user_id) REFERENCES user_accounts(id)"
                    ") CHARACTER SET utf8mb4"
                )
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS wallet_transactions ("
                    "id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, request_id VARCHAR(64) NULL, "
                    "transaction_type ENUM('grant','freeze','settle','release','recharge','refund') NOT NULL, points BIGINT NOT NULL, "
                    "balance_after BIGINT NOT NULL, frozen_after BIGINT NOT NULL, metadata TEXT NULL, "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "INDEX idx_wallet_request (user_id, request_id), "
                    "UNIQUE KEY uq_wallet_request_type (user_id, request_id, transaction_type)"
                    ") CHARACTER SET utf8mb4"
                )
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS orders ("
                    "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
                    "order_no VARCHAR(64) NOT NULL UNIQUE, user_id BIGINT NOT NULL, "
                    "provider VARCHAR(32) NOT NULL, status ENUM('pending','paid','refunded','failed') NOT NULL DEFAULT 'pending', "
                    "amount_minor BIGINT NOT NULL, currency ENUM('CNY','USD') NOT NULL, "
                    "region ENUM('CN','GLOBAL') NOT NULL, points BIGINT NOT NULL, "
                    "provider_transaction_id VARCHAR(128) NULL UNIQUE, "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, paid_at TIMESTAMP NULL, "
                    "refunded_at TIMESTAMP NULL, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
                    "INDEX idx_orders_user_created (user_id, created_at), "
                    "FOREIGN KEY (user_id) REFERENCES user_accounts(id)"
                    ") CHARACTER SET utf8mb4"
                )
                cursor.execute(
                    "ALTER TABLE wallet_transactions MODIFY COLUMN transaction_type "
                    "ENUM('grant','freeze','settle','release','recharge','refund') NOT NULL"
                )
                try:
                    cursor.execute(
                        "ALTER TABLE wallet_transactions ADD UNIQUE KEY "
                        "uq_wallet_request_type (user_id, request_id, transaction_type)"
                    )
                except Exception as error:
                    if not self._is_duplicate_index_error(error):
                        raise
                try:
                    cursor.execute(
                        "ALTER TABLE user_accounts "
                        "ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'"
                    )
                except Exception as error:
                    if not self._is_duplicate_column_error(error):
                        raise
                cursor.execute(
                    "UPDATE user_accounts SET role = 'user' "
                    "WHERE role IS NULL OR role NOT IN ('admin', 'user')"
                )
                cursor.execute(
                    "ALTER TABLE user_accounts "
                    "MODIFY COLUMN role ENUM('admin', 'user') NOT NULL DEFAULT 'user'"
                )
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS admin_principals ("
                    "user_id BIGINT PRIMARY KEY, "
                    "access_tier ENUM('owner','admin') NOT NULL DEFAULT 'admin', "
                    "policy_version BIGINT NOT NULL DEFAULT 1, "
                    "owner_singleton TINYINT GENERATED ALWAYS AS ("
                    "CASE WHEN access_tier='owner' THEN 1 ELSE NULL END) STORED, "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
                    "UNIQUE KEY uq_admin_principals_single_owner (owner_singleton), "
                    "FOREIGN KEY (user_id) REFERENCES user_accounts(id)"
                    ") CHARACTER SET utf8mb4"
                )
                self._execute_optional_migration(
                    cursor,
                    "ALTER TABLE admin_principals ADD COLUMN owner_singleton TINYINT "
                    "GENERATED ALWAYS AS (CASE WHEN access_tier='owner' THEN 1 ELSE NULL END) STORED",
                )
                self._execute_optional_migration(
                    cursor,
                    "CREATE UNIQUE INDEX uq_admin_principals_single_owner "
                    "ON admin_principals (owner_singleton)",
                )
                cursor.execute(
                    "INSERT INTO admin_principals (user_id,access_tier,policy_version) "
                    "SELECT id, 'admin', 1 FROM user_accounts WHERE role='admin' "
                    "ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_user(self, username: str, password: str) -> bool:
        """Create an account, returning False when its username already exists."""
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO user_accounts (username, password_hash) VALUES (%s, %s)",
                    (username, generate_password_hash(password)),
                )
                if not hasattr(self, "_testing_store"):
                    user_id = cursor.lastrowid
                    cursor.execute(
                        "INSERT INTO user_wallets (user_id,balance_points,frozen_points) "
                        "VALUES (%s,500,0)",
                        (user_id,),
                    )
                    cursor.execute(
                        "INSERT INTO wallet_transactions "
                        "(user_id,request_id,transaction_type,points,balance_after,frozen_after,metadata) "
                        "VALUES (%s,NULL,'grant',500,500,0,%s)",
                        (user_id, '{"source":"registration"}'),
                    )
            if hasattr(self, "_testing_store"):
                self.ensure_wallet(username)
            connection.commit()
            return True
        except Exception as error:
            connection.rollback()
            if self._is_duplicate_error(error):
                return False
            raise
        finally:
            connection.close()

    def prepare_email_registration(self, email: str, password: str) -> dict:
        """Create or replace an unverified email registration."""
        normalized = self._normalize_email(email)
        password_hash = generate_password_hash(password)
        if hasattr(self, "_testing_store"):
            account = self._testing_account_by_email(normalized)
            if account is not None:
                if account["email_verified_at"] is not None:
                    raise ValueError("邮箱已注册")
                account["password_hash"] = password_hash
                return self._email_account_summary(account)
            username = f"user-{uuid4().hex[:12]}"
            account = _MemoryCursor._account(password_hash)
            account.update(
                {
                    "id": len(self._testing_store["users"]) + 1,
                    "username": username,
                    "email": normalized,
                    "email_verified_at": None,
                    "session_version": 0,
                    "phone_number": None,
                    "phone_verified_at": None,
                    "wechat_openid": None,
                    "wechat_bound_at": None,
                }
            )
            self._testing_store["users"][username] = account
            return self._email_account_summary(account)

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT username,email_verified_at,session_version FROM user_accounts "
                    "WHERE email = %s FOR UPDATE",
                    (normalized,),
                )
                row = cursor.fetchone()
                if row is not None:
                    username, verified_at, session_version = self._row_values(
                        row, "username", "email_verified_at", "session_version"
                    )
                    if verified_at is not None:
                        raise ValueError("邮箱已注册")
                    cursor.execute(
                        "UPDATE user_accounts SET password_hash = %s WHERE email = %s",
                        (password_hash, normalized),
                    )
                    account = {
                        "username": username,
                        "email": normalized,
                        "session_version": int(session_version),
                    }
                else:
                    username = f"user-{uuid4().hex[:12]}"
                    cursor.execute(
                        "INSERT INTO user_accounts (username, password_hash, email) VALUES (%s, %s, %s)",
                        (username, password_hash, normalized),
                    )
                    account = {"username": username, "email": normalized, "session_version": 0}
            connection.commit()
            return account
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_email_verified(self, email: str) -> dict:
        """Mark a pending email account as verified and return its session identity."""
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            account = self._testing_account_by_email(normalized)
            if account is None:
                raise KeyError("email account not found")
            account["email_verified_at"] = True
            return self.get_session_identity(normalized)

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE user_accounts SET email_verified_at = CURRENT_TIMESTAMP "
                    "WHERE email = %s",
                    (normalized,),
                )
                if cursor.rowcount == 0:
                    raise KeyError("email account not found")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_session_identity(normalized)

    def get_phone_binding_state(self, email: str, phone: str) -> str:
        """Report whether a verified account may bind a phone number."""
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            with self._testing_phone_binding_lock:
                account = self._testing_account_by_email(normalized)
                if account is None or account["email_verified_at"] is None or account.get("phone_number") is not None:
                    return "account_already_bound"
                if self._testing_account_by_phone(phone) is not None:
                    return "phone_already_bound"
                return "ready"

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT phone_number FROM user_accounts "
                    "WHERE email = %s AND email_verified_at IS NOT NULL",
                    (normalized,),
                )
                row = cursor.fetchone()
                if row is None:
                    return "account_already_bound"
                (bound_phone,) = self._row_values(row, "phone_number")
                if bound_phone is not None:
                    return "account_already_bound"
                cursor.execute(
                    "SELECT id FROM user_accounts WHERE phone_number = %s",
                    (phone,),
                )
                return "phone_already_bound" if cursor.fetchone() is not None else "ready"
        finally:
            connection.close()

    def start_phone_binding_challenge(self, email: str, phone: str, now: datetime) -> str:
        """Persist an account-scoped phone-binding challenge before delivery."""
        normalized = self._normalize_email(email)
        now = self._phone_binding_timestamp(now)
        expires_at = now + timedelta(minutes=5)
        if hasattr(self, "_testing_store"):
            with self._testing_phone_binding_lock:
                account = self._testing_account_by_email(normalized)
                if account is None or account["email_verified_at"] is None or account.get("phone_number") is not None:
                    return "account_already_bound"
                if self._testing_account_by_phone(phone) is not None:
                    return "phone_already_bound"
                sent_at = account.get("phone_binding_sent_at")
                if isinstance(sent_at, datetime) and now - sent_at < self._one_minute():
                    return "cooldown"
                account.update(
                    phone_binding_pending_phone=phone,
                    phone_binding_sent_at=now,
                    phone_binding_expires_at=expires_at,
                    phone_binding_failed_attempts=0,
                )
                return "ready"

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,phone_number,phone_binding_sent_at FROM user_accounts "
                    "WHERE email = %s AND email_verified_at IS NOT NULL FOR UPDATE",
                    (normalized,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return "account_already_bound"
                account_id, bound_phone, sent_at = self._row_values(
                    row, "id", "phone_number", "phone_binding_sent_at"
                )
                if bound_phone is not None:
                    connection.commit()
                    return "account_already_bound"
                cursor.execute("SELECT id FROM user_accounts WHERE phone_number = %s", (phone,))
                if cursor.fetchone() is not None:
                    connection.commit()
                    return "phone_already_bound"
                if sent_at is not None and now - sent_at < self._one_minute():
                    connection.commit()
                    return "cooldown"
                cursor.execute(
                    "UPDATE user_accounts SET phone_binding_pending_phone = %s, "
                    "phone_binding_sent_at = %s, phone_binding_expires_at = %s, "
                    "phone_binding_failed_attempts = 0 WHERE id = %s",
                    (phone, now, expires_at, account_id),
                )
            connection.commit()
            return "ready"
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_phone_binding_challenge(self, email: str, now: datetime | None = None) -> dict | None:
        """Return the current server-side challenge without exposing it to a session cookie."""
        normalized = self._normalize_email(email)
        now = self._phone_binding_timestamp(
            now or datetime.now(timezone.utc).replace(tzinfo=None)
        )
        if hasattr(self, "_testing_store"):
            with self._testing_phone_binding_lock:
                account = self._testing_account_by_email(normalized)
                if account is None:
                    return None
                phone = account.get("phone_binding_pending_phone")
                sent_at = account.get("phone_binding_sent_at")
                expires_at = account.get("phone_binding_expires_at")
                if not phone or not isinstance(sent_at, datetime) or not isinstance(expires_at, datetime) or expires_at <= now:
                    return None
                return {
                    "phone": phone,
                    "sent_at": sent_at,
                    "failed_attempts": int(account.get("phone_binding_failed_attempts", 0)),
                }

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT phone_binding_pending_phone,phone_binding_sent_at,"
                    "phone_binding_expires_at,phone_binding_failed_attempts FROM user_accounts "
                    "WHERE email = %s AND email_verified_at IS NOT NULL",
                    (normalized,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        phone, sent_at, expires_at, failed_attempts = self._row_values(
            row,
            "phone_binding_pending_phone",
            "phone_binding_sent_at",
            "phone_binding_expires_at",
            "phone_binding_failed_attempts",
        )
        if not phone or sent_at is None or expires_at is None or expires_at <= now:
            return None
        return {"phone": phone, "sent_at": sent_at, "failed_attempts": int(failed_attempts)}

    def claim_phone_binding_verification(self, email: str, now: datetime) -> dict | None:
        """Atomically reserve one provider verification attempt for the account challenge."""
        normalized = self._normalize_email(email)
        now = self._phone_binding_timestamp(now)
        if hasattr(self, "_testing_store"):
            with self._testing_phone_binding_lock:
                challenge = self.get_phone_binding_challenge(normalized, now)
                if challenge is None or challenge["failed_attempts"] >= 5:
                    return None
                account = self._testing_account_by_email(normalized)
                account["phone_binding_failed_attempts"] += 1
                return {"phone": challenge["phone"], "sent_at": challenge["sent_at"]}

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,phone_binding_pending_phone,phone_binding_sent_at,"
                    "phone_binding_expires_at,phone_binding_failed_attempts FROM user_accounts "
                    "WHERE email = %s AND email_verified_at IS NOT NULL FOR UPDATE",
                    (normalized,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return None
                account_id, phone, sent_at, expires_at, failed_attempts = self._row_values(
                    row,
                    "id",
                    "phone_binding_pending_phone",
                    "phone_binding_sent_at",
                    "phone_binding_expires_at",
                    "phone_binding_failed_attempts",
                )
                if not phone or sent_at is None or expires_at is None or expires_at <= now or int(failed_attempts) >= 5:
                    connection.commit()
                    return None
                cursor.execute(
                    "UPDATE user_accounts SET phone_binding_failed_attempts = "
                    "phone_binding_failed_attempts + 1 WHERE id = %s",
                    (account_id,),
                )
            connection.commit()
            return {"phone": phone, "sent_at": sent_at}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def clear_phone_binding_challenge(self, email: str, phone: str, sent_at: datetime) -> None:
        """Clear one exact challenge after a delivery failure or terminal result."""
        normalized = self._normalize_email(email)
        sent_at = self._phone_binding_timestamp(sent_at)
        if hasattr(self, "_testing_store"):
            with self._testing_phone_binding_lock:
                account = self._testing_account_by_email(normalized)
                if account is not None and account.get("phone_binding_pending_phone") == phone and account.get("phone_binding_sent_at") == sent_at:
                    account.update(
                        phone_binding_pending_phone=None,
                        phone_binding_sent_at=None,
                        phone_binding_expires_at=None,
                        phone_binding_failed_attempts=0,
                    )
            return

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE user_accounts SET phone_binding_pending_phone = NULL, "
                    "phone_binding_sent_at = NULL, phone_binding_expires_at = NULL, "
                    "phone_binding_failed_attempts = 0 WHERE email = %s "
                    "AND phone_binding_pending_phone = %s AND phone_binding_sent_at = %s",
                    (normalized, phone, sent_at),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def consume_verified_phone_binding(
        self, email: str, phone: str, sent_at: datetime, now: datetime
    ) -> str:
        """Atomically require the active challenge and bind the verified phone number."""
        normalized = self._normalize_email(email)
        sent_at = self._phone_binding_timestamp(sent_at)
        now = self._phone_binding_timestamp(now)
        if hasattr(self, "_testing_store"):
            with self._testing_phone_binding_lock:
                challenge = self.get_phone_binding_challenge(normalized, now)
                if challenge is None or challenge["phone"] != phone or challenge["sent_at"] != sent_at:
                    return "invalid_challenge"
                account = self._testing_account_by_email(normalized)
                if account.get("phone_number") is not None:
                    return "account_already_bound"
                if self._testing_account_by_phone(phone) is not None:
                    return "phone_already_bound"
                account.update(
                    phone_number=phone,
                    phone_verified_at=True,
                    phone_binding_pending_phone=None,
                    phone_binding_sent_at=None,
                    phone_binding_expires_at=None,
                    phone_binding_failed_attempts=0,
                )
                return "bound"

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,phone_number,phone_binding_pending_phone,phone_binding_sent_at,"
                    "phone_binding_expires_at FROM user_accounts WHERE email = %s "
                    "AND email_verified_at IS NOT NULL FOR UPDATE",
                    (normalized,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return "invalid_challenge"
                account_id, bound_phone, pending_phone, stored_sent_at, expires_at = self._row_values(
                    row,
                    "id",
                    "phone_number",
                    "phone_binding_pending_phone",
                    "phone_binding_sent_at",
                    "phone_binding_expires_at",
                )
                if pending_phone != phone or stored_sent_at != sent_at or expires_at is None or expires_at <= now:
                    connection.commit()
                    return "invalid_challenge"
                if bound_phone is not None:
                    connection.commit()
                    return "account_already_bound"
                cursor.execute("SELECT id FROM user_accounts WHERE phone_number = %s", (phone,))
                if cursor.fetchone() is not None:
                    connection.commit()
                    return "phone_already_bound"
                cursor.execute(
                    "UPDATE user_accounts SET phone_number = %s, phone_verified_at = CURRENT_TIMESTAMP, "
                    "phone_binding_pending_phone = NULL, phone_binding_sent_at = NULL, "
                    "phone_binding_expires_at = NULL, phone_binding_failed_attempts = 0 "
                    "WHERE id = %s AND phone_number IS NULL",
                    (phone, account_id),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return "account_already_bound"
            connection.commit()
            return "bound"
        except Exception as error:
            connection.rollback()
            if self._is_duplicate_error(error):
                return "phone_already_bound"
            raise
        finally:
            connection.close()

    def bind_verified_phone(self, email: str, phone: str) -> str:
        """Atomically bind a previously verified phone to an email account."""
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            with self._testing_phone_binding_lock:
                account = self._testing_account_by_email(normalized)
                if account is None or account["email_verified_at"] is None or account.get("phone_number") is not None:
                    return "account_already_bound"
                if self._testing_account_by_phone(phone) is not None:
                    return "phone_already_bound"
                account["phone_number"] = phone
                account["phone_verified_at"] = True
                return "bound"

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,phone_number FROM user_accounts "
                    "WHERE email = %s AND email_verified_at IS NOT NULL FOR UPDATE",
                    (normalized,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return "account_already_bound"
                account_id, bound_phone = self._row_values(row, "id", "phone_number")
                if bound_phone is not None:
                    connection.commit()
                    return "account_already_bound"
                cursor.execute(
                    "UPDATE user_accounts SET phone_number = %s, phone_verified_at = CURRENT_TIMESTAMP "
                    "WHERE id = %s AND phone_number IS NULL",
                    (phone, account_id),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return "account_already_bound"
            connection.commit()
            return "bound"
        except Exception as error:
            connection.rollback()
            if self._is_duplicate_error(error):
                return "phone_already_bound"
            raise
        finally:
            connection.close()

    def find_verified_user_by_email(self, email: str) -> dict | None:
        """Return a verified email account, including its password hash for authentication."""
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            account = self._testing_account_by_email(normalized)
            if account is None or account["email_verified_at"] is None:
                return None
            identity = dict(account)
            identity.setdefault("phone_number", None)
            return identity

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT username,email,password_hash,is_active,session_version,phone_number,phone_verified_at,wechat_bound_at "
                    "FROM user_accounts WHERE email = %s AND email_verified_at IS NOT NULL",
                    (normalized,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        username, stored_email, password_hash, is_active, version, phone_number, phone_verified_at, wechat_bound_at = self._row_values(
            row,
            "username",
            "email",
            "password_hash",
            "is_active",
            "session_version",
            "phone_number",
            "phone_verified_at",
            "wechat_bound_at",
        )
        return {
            "username": username,
            "email": stored_email,
            "password_hash": password_hash,
            "is_active": bool(is_active),
            "session_version": int(version),
            "phone_number": phone_number,
            "phone_verified_at": phone_verified_at,
            "wechat_bound_at": wechat_bound_at,
        }

    def verify_email_password(self, email: str, password: str) -> dict | None:
        account = self.find_verified_user_by_email(email)
        if not account or not account["is_active"] or not check_password_hash(account["password_hash"], password):
            return None
        return {
            "username": account["username"],
            "email": account["email"],
            "session_version": int(account["session_version"]),
        }

    def get_session_identity(self, email: str) -> dict | None:
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            account = self._testing_account_by_email(normalized)
            if account is None or account["email_verified_at"] is None:
                return None
            return {
                "username": account["username"],
                "email": account["email"],
                "session_version": int(account["session_version"]),
                "phone_number": account.get("phone_number"),
                "phone_verified_at": account["phone_verified_at"],
                "wechat_bound_at": account["wechat_bound_at"],
            }

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT username,email,session_version,phone_number,phone_verified_at,wechat_bound_at "
                    "FROM user_accounts WHERE email = %s AND email_verified_at IS NOT NULL",
                    (normalized,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        username, stored_email, version, phone_number, phone_verified_at, wechat_bound_at = self._row_values(
            row, "username", "email", "session_version", "phone_number", "phone_verified_at", "wechat_bound_at"
        )
        return {
            "username": username,
            "email": stored_email,
            "session_version": int(version),
            "phone_number": phone_number,
            "phone_verified_at": phone_verified_at,
            "wechat_bound_at": wechat_bound_at,
        }

    def get_session_identity_by_username(self, username: str) -> dict | None:
        """Return the versioned identity for an active, verified email account."""
        if hasattr(self, "_testing_store"):
            account = self._testing_store["users"].get(username)
            if (
                account is None
                or not account["is_active"]
                or account.get("email_verified_at") is None
            ):
                return None
            return {
                "email": account["email"],
                "session_version": int(account["session_version"]),
            }

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT email,session_version FROM user_accounts "
                    "WHERE username = %s AND is_active = TRUE "
                    "AND email_verified_at IS NOT NULL",
                    (username,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        email, version = self._row_values(row, "email", "session_version")
        return {"email": email, "session_version": int(version)}

    def reset_password_by_email(self, email: str, password: str) -> bool:
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            account = self._testing_account_by_email(normalized)
            if account is None or account["email_verified_at"] is None:
                return False
            account["password_hash"] = generate_password_hash(password)
            account["session_version"] += 1
            return True
        return self._update(
            "UPDATE user_accounts SET password_hash = %s, session_version = session_version + 1 "
            "WHERE email = %s AND email_verified_at IS NOT NULL",
            (generate_password_hash(password), normalized),
        )

    def store_registration_attempt(
        self,
        email: str,
        attempt_id: str,
        pending_password_hash: str,
        code_hash: str,
        now,
        expires_at,
    ) -> int:
        """Atomically reject verified accounts and persist one attempt-bound code."""
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            account = self._testing_account_by_email(normalized)
            if account is not None and account["email_verified_at"] is not None:
                raise ValueError("\u90ae\u7bb1\u5df2\u6ce8\u518c")
            codes = self._testing_store["email_codes"]
            recent = [
                item
                for item in codes.values()
                if item["email"] == normalized
                and item["purpose"] == "register"
                and item["sent_at"] > now - self._one_hour()
            ]
            if any(now - item["sent_at"] < self._one_minute() for item in recent) or len(recent) >= 5:
                raise ValueError("email verification code send limit exceeded")
            for item in codes.values():
                if (
                    item["email"] == normalized
                    and item["purpose"] == "register"
                    and item["used_at"] is None
                ):
                    item["used_at"] = now
            code_id = self._testing_store["next_email_code_id"]
            self._testing_store["next_email_code_id"] += 1
            codes[code_id] = {
                "id": code_id,
                "email": normalized,
                "purpose": "register",
                "code_hash": code_hash,
                "attempt_id": attempt_id,
                "pending_password_hash": pending_password_hash,
                "expires_at": expires_at,
                "sent_at": now,
                "failed_attempts": 0,
                "used_at": None,
            }
            return code_id

        connection = self._connect(database=self._database_name())
        lock_acquired = False
        lock_name = hashlib.sha256(f"{normalized}\0register".encode()).hexdigest()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, 5))
                lock_row = cursor.fetchone()
                if lock_row is None or lock_row[0] != 1:
                    raise RuntimeError("unable to acquire email verification code lock")
                lock_acquired = True
                cursor.execute(
                    "SELECT email_verified_at FROM user_accounts WHERE email = %s FOR UPDATE",
                    (normalized,),
                )
                account_row = cursor.fetchone()
                if account_row is not None and self._row_values(account_row, "email_verified_at")[0] is not None:
                    raise ValueError("\u90ae\u7bb1\u5df2\u6ce8\u518c")
                cursor.execute(
                    "SELECT sent_at FROM email_verification_codes WHERE email = %s "
                    "AND purpose = 'register' AND sent_at > %s FOR UPDATE",
                    (normalized, now - self._one_hour()),
                )
                sent_at_values = [self._row_values(row, "sent_at")[0] for row in cursor.fetchall()]
                if any(now - sent_at < self._one_minute() for sent_at in sent_at_values) or len(sent_at_values) >= 5:
                    raise ValueError("email verification code send limit exceeded")
                cursor.execute(
                    "UPDATE email_verification_codes SET used_at = %s WHERE email = %s "
                    "AND purpose = 'register' AND used_at IS NULL",
                    (now, normalized),
                )
                cursor.execute(
                    "INSERT INTO email_verification_codes "
                    "(email,purpose,code_hash,attempt_id,pending_password_hash,expires_at,sent_at) "
                    "VALUES (%s,'register',%s,%s,%s,%s,%s)",
                    (
                        normalized,
                        code_hash,
                        attempt_id,
                        pending_password_hash,
                        expires_at,
                        now,
                    ),
                )
                code_id = cursor.lastrowid
            connection.commit()
            return int(code_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            if lock_acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception:
                    pass
            connection.close()

    def consume_registration_attempt(
        self, email: str, attempt_id: str, code_hash: str, now
    ) -> dict | None:
        """Consume and activate only the password bound to the matching attempt."""
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            code = next(
                (
                    item
                    for item in self._testing_store["email_codes"].values()
                    if item["email"] == normalized
                    and item["purpose"] == "register"
                    and item.get("attempt_id") == attempt_id
                    and item["used_at"] is None
                ),
                None,
            )
            if (
                code is None
                or code["expires_at"] <= now
                or code["failed_attempts"] >= 5
                or not code.get("pending_password_hash")
            ):
                return None
            if not hmac.compare_digest(code["code_hash"], code_hash):
                code["failed_attempts"] += 1
                return None
            account = self._testing_account_by_email(normalized)
            if account is not None and account["email_verified_at"] is not None:
                return None
            if account is None:
                username = f"user-{uuid4().hex[:12]}"
                account = _MemoryCursor._account(code["pending_password_hash"])
                account.update(
                    {
                        "id": len(self._testing_store["users"]) + 1,
                        "username": username,
                        "email": normalized,
                        "email_verified_at": True,
                        "session_version": 0,
                        "phone_number": None,
                        "phone_verified_at": None,
                        "wechat_openid": None,
                        "wechat_bound_at": None,
                    }
                )
                self._testing_store["users"][username] = account
            else:
                account["password_hash"] = code["pending_password_hash"]
                account["email_verified_at"] = True
            code["used_at"] = now
            return self._email_account_summary(account)

        connection = self._connect(database=self._database_name())
        lock_acquired = False
        lock_name = hashlib.sha256(f"{normalized}\0register".encode()).hexdigest()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, 5))
                lock_row = cursor.fetchone()
                if lock_row is None or lock_row[0] != 1:
                    raise RuntimeError("unable to acquire email verification code lock")
                lock_acquired = True
                cursor.execute(
                    "SELECT id,code_hash,expires_at,failed_attempts,pending_password_hash "
                    "FROM email_verification_codes WHERE email = %s AND purpose = 'register' "
                    "AND attempt_id = %s AND used_at IS NULL LIMIT 1 FOR UPDATE",
                    (normalized, attempt_id),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return None
                code_id, stored_hash, expires_at, failed_attempts, password_hash = self._row_values(
                    row,
                    "id",
                    "code_hash",
                    "expires_at",
                    "failed_attempts",
                    "pending_password_hash",
                )
                if expires_at <= now or int(failed_attempts) >= 5 or not password_hash:
                    connection.commit()
                    return None
                if not hmac.compare_digest(stored_hash, code_hash):
                    cursor.execute(
                        "UPDATE email_verification_codes SET failed_attempts = failed_attempts + 1 "
                        "WHERE id = %s",
                        (code_id,),
                    )
                    connection.commit()
                    return None
                cursor.execute(
                    "SELECT username,email_verified_at,session_version FROM user_accounts "
                    "WHERE email = %s FOR UPDATE",
                    (normalized,),
                )
                account_row = cursor.fetchone()
                if account_row is not None:
                    username, verified_at, session_version = self._row_values(
                        account_row, "username", "email_verified_at", "session_version"
                    )
                    if verified_at is not None:
                        connection.commit()
                        return None
                    cursor.execute(
                        "UPDATE user_accounts SET password_hash = %s, "
                        "email_verified_at = CURRENT_TIMESTAMP WHERE email = %s "
                        "AND email_verified_at IS NULL",
                        (password_hash, normalized),
                    )
                else:
                    username = f"user-{uuid4().hex[:12]}"
                    session_version = 0
                    cursor.execute(
                        "INSERT INTO user_accounts "
                        "(username,password_hash,email,email_verified_at) "
                        "VALUES (%s,%s,%s,CURRENT_TIMESTAMP)",
                        (username, password_hash, normalized),
                    )
                cursor.execute(
                    "UPDATE email_verification_codes SET used_at = %s "
                    "WHERE id = %s AND used_at IS NULL",
                    (now, code_id),
                )
            connection.commit()
            return {
                "username": username,
                "email": normalized,
                "session_version": int(session_version),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            if lock_acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception:
                    pass
            connection.close()

    def store_email_code(self, email: str, purpose: str, code_hash: str, now, expires_at) -> int:
        """Persist one verification code while enforcing send-rate limits."""
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            codes = self._testing_store["email_codes"]
            recent = [
                code for code in codes.values()
                if code["email"] == normalized and code["purpose"] == purpose and code["sent_at"] > now - self._one_hour()
            ]
            recent_minute = [
                code for code in recent if now - code["sent_at"] < self._one_minute()
            ]
            if recent_minute:
                newest_sent_at = max(code["sent_at"] for code in recent_minute)
                raise EmailCodeRateLimitError(
                    (newest_sent_at + self._one_minute() - now).total_seconds()
                )
            if len(recent) >= 5:
                raise ValueError("email verification code send limit exceeded")
            for code in codes.values():
                if code["email"] == normalized and code["purpose"] == purpose and code["used_at"] is None:
                    code["used_at"] = now
            code_id = self._testing_store["next_email_code_id"]
            self._testing_store["next_email_code_id"] += 1
            codes[code_id] = {
                "id": code_id,
                "email": normalized,
                "purpose": purpose,
                "code_hash": code_hash,
                "expires_at": expires_at,
                "sent_at": now,
                "failed_attempts": 0,
                "used_at": None,
            }
            return code_id

        connection = self._connect(database=self._database_name())
        lock_acquired = False
        lock_name = hashlib.sha256(f"{normalized}\0{purpose}".encode()).hexdigest()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, 5))
                lock_row = cursor.fetchone()
                if lock_row is None or lock_row[0] != 1:
                    raise RuntimeError("unable to acquire email verification code lock")
                lock_acquired = True
                cursor.execute(
                    "SELECT sent_at FROM email_verification_codes WHERE email = %s AND purpose = %s "
                    "AND sent_at > %s FOR UPDATE",
                    (normalized, purpose, now - self._one_hour()),
                )
                sent_at_values = [self._row_values(row, "sent_at")[0] for row in cursor.fetchall()]
                recent_minute = [
                    sent_at
                    for sent_at in sent_at_values
                    if now - sent_at < self._one_minute()
                ]
                if recent_minute:
                    newest_sent_at = max(recent_minute)
                    raise EmailCodeRateLimitError(
                        (newest_sent_at + self._one_minute() - now).total_seconds()
                    )
                if len(sent_at_values) >= 5:
                    raise ValueError("email verification code send limit exceeded")
                cursor.execute(
                    "UPDATE email_verification_codes SET used_at = %s "
                    "WHERE email = %s AND purpose = %s AND used_at IS NULL",
                    (now, normalized, purpose),
                )
                cursor.execute(
                    "INSERT INTO email_verification_codes "
                    "(email,purpose,code_hash,expires_at,sent_at) VALUES (%s,%s,%s,%s,%s)",
                    (normalized, purpose, code_hash, expires_at, now),
                )
                code_id = cursor.lastrowid
            connection.commit()
            return int(code_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            if lock_acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception:
                    pass
            connection.close()

    def consume_email_code(self, email: str, purpose: str, code_hash: str, now) -> bool:
        """Consume the newest valid code only when its hash matches."""
        normalized = self._normalize_email(email)
        if hasattr(self, "_testing_store"):
            candidates = [
                code for code in self._testing_store["email_codes"].values()
                if code["email"] == normalized and code["purpose"] == purpose and code["used_at"] is None
            ]
            if not candidates:
                return False
            code = max(candidates, key=lambda item: (item["sent_at"], item["id"]))
            if code["expires_at"] <= now or code["failed_attempts"] >= 5:
                return False
            if not hmac.compare_digest(code["code_hash"], code_hash):
                code["failed_attempts"] += 1
                return False
            code["used_at"] = now
            return True

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,code_hash,expires_at,failed_attempts FROM email_verification_codes "
                    "WHERE email = %s AND purpose = %s AND used_at IS NULL "
                    "ORDER BY sent_at DESC, id DESC LIMIT 1 FOR UPDATE",
                    (normalized, purpose),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return False
                code_id, stored_hash, expires_at, failed_attempts = self._row_values(
                    row, "id", "code_hash", "expires_at", "failed_attempts"
                )
                if expires_at <= now or int(failed_attempts) >= 5:
                    connection.commit()
                    return False
                if not hmac.compare_digest(stored_hash, code_hash):
                    cursor.execute(
                        "UPDATE email_verification_codes SET failed_attempts = failed_attempts + 1 WHERE id = %s",
                        (code_id,),
                    )
                    connection.commit()
                    return False
                cursor.execute(
                    "UPDATE email_verification_codes SET used_at = %s WHERE id = %s AND used_at IS NULL",
                    (now, code_id),
                )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def cancel_email_code(self, code_id: int) -> None:
        """Remove a code that could not be delivered by email."""
        if hasattr(self, "_testing_store"):
            code = self._testing_store["email_codes"].get(code_id)
            if code is not None and code["used_at"] is None:
                del self._testing_store["email_codes"][code_id]
            return
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM email_verification_codes WHERE id = %s AND used_at IS NULL",
                    (code_id,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_user_if_missing(self, username: str, password: str) -> None:
        """Create an account only if no account with that username exists."""
        self.create_user(username, password)

    def get_user_id(self, username: str) -> int:
        if hasattr(self, "_testing_store"):
            account = self._testing_store["users"].get(username)
            if account is None:
                raise KeyError("user not found")
            return account["id"]
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM user_accounts WHERE username = %s", (username,))
                row = cursor.fetchone()
            if row is None:
                raise KeyError("user not found")
            return int(row[0] if isinstance(row, tuple) else row["id"])
        finally:
            connection.close()

    def create(self, **order) -> dict:
        if hasattr(self, "_testing_store"):
            saved = {**order, "status": "pending", "provider_transaction_id": None}
            self._testing_store["orders"][order["order_no"]] = saved
            return dict(saved)
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO orders (order_no,user_id,provider,amount_minor,currency,region,points) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (order["order_no"], order["user_id"], order["provider"], order["amount_minor"], order["currency"], order["region"], order["points"]),
                )
            connection.commit()
            return self.get(order["order_no"])
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, order_no: str) -> dict:
        if hasattr(self, "_testing_store"):
            order = self._testing_store["orders"].get(order_no)
            if order is None:
                raise KeyError("order not found")
            return dict(order)
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT order_no,user_id,provider,status,amount_minor,currency,region,points,provider_transaction_id FROM orders WHERE order_no=%s", (order_no,))
                row = cursor.fetchone()
            if row is None:
                raise KeyError("order not found")
            keys = ("order_no","user_id","provider","status","amount_minor","currency","region","points","provider_transaction_id")
            return dict(zip(keys, row)) if isinstance(row, tuple) else dict(row)
        finally:
            connection.close()

    def complete_paid_order(self, order_no: str, provider_transaction_id: str) -> dict:
        if hasattr(self, "_testing_store"):
            order = self._testing_store["orders"][order_no]
            if order["status"] == "pending":
                order["status"] = "paid"
                order["provider_transaction_id"] = provider_transaction_id
                username = next(name for name, account in self._testing_store["users"].items() if account["id"] == order["user_id"])
                wallet = self.ensure_wallet(username)
                wallet["balance_points"] += order["points"]
                self._testing_store["wallets"][username] = wallet
                self._testing_store["wallet_transactions"][username].append(
                    {"transaction_type": "recharge", "points": order["points"],
                     "balance_after": wallet["balance_points"], "frozen_after": wallet["frozen_points"],
                     "request_id": order_no}
                )
            return dict(order)
        return self._transition_paid_mysql(order_no, provider_transaction_id)

    def refund_paid_order(self, order_no: str) -> dict:
        if hasattr(self, "_testing_store"):
            order = self._testing_store["orders"][order_no]
            if order["status"] == "paid":
                username = next(name for name, account in self._testing_store["users"].items() if account["id"] == order["user_id"])
                wallet = self.ensure_wallet(username)
                if wallet["balance_points"] - wallet["frozen_points"] < order["points"]:
                    raise ValueError("insufficient points for refund")
                wallet["balance_points"] -= order["points"]
                self._testing_store["wallets"][username] = wallet
                order["status"] = "refunded"
                self._testing_store["wallet_transactions"][username].append(
                    {"transaction_type": "refund", "points": -order["points"],
                     "balance_after": wallet["balance_points"], "frozen_after": wallet["frozen_points"],
                     "request_id": order_no}
                )
            return dict(order)
        return self._transition_refund_mysql(order_no)

    def _transition_paid_mysql(self, order_no, provider_transaction_id):
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT user_id,status,points FROM orders WHERE order_no=%s FOR UPDATE", (order_no,))
                row = cursor.fetchone()
                if row is None:
                    raise KeyError("order not found")
                user_id, status, points = row if isinstance(row, tuple) else (row["user_id"], row["status"], row["points"])
                if status == "pending":
                    cursor.execute("UPDATE orders SET status='paid',provider_transaction_id=%s,paid_at=CURRENT_TIMESTAMP WHERE order_no=%s AND status='pending'", (provider_transaction_id, order_no))
                    cursor.execute("INSERT IGNORE INTO user_wallets (user_id,balance_points,frozen_points) VALUES (%s,500,0)", (user_id,))
                    if cursor.rowcount == 1:
                        cursor.execute(
                            "INSERT INTO wallet_transactions (user_id,request_id,transaction_type,points,balance_after,frozen_after,metadata) "
                            "VALUES (%s,NULL,'grant',500,500,0,%s)",
                            (user_id, '{"source":"legacy-wallet-initialization"}'),
                        )
                    cursor.execute("UPDATE user_wallets SET balance_points=balance_points+%s WHERE user_id=%s", (points, user_id))
                    cursor.execute("SELECT balance_points,frozen_points FROM user_wallets WHERE user_id=%s", (user_id,))
                    balance, frozen = cursor.fetchone()
                    cursor.execute("INSERT INTO wallet_transactions (user_id,request_id,transaction_type,points,balance_after,frozen_after,metadata) VALUES (%s,%s,'recharge',%s,%s,%s,%s)", (user_id, order_no, points, balance, frozen, '{"source":"recharge"}'))
            connection.commit()
            return self.get(order_no)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _transition_refund_mysql(self, order_no):
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT user_id,status,points FROM orders WHERE order_no=%s FOR UPDATE", (order_no,))
                row = cursor.fetchone()
                if row is None:
                    raise KeyError("order not found")
                user_id, status, points = row if isinstance(row, tuple) else (row["user_id"], row["status"], row["points"])
                if status == "paid":
                    cursor.execute(
                        "SELECT balance_points,frozen_points FROM user_wallets "
                        "WHERE user_id=%s FOR UPDATE",
                        (user_id,),
                    )
                    wallet_row = cursor.fetchone()
                    if wallet_row is None:
                        raise ValueError("insufficient points for refund")
                    balance, frozen = (
                        wallet_row
                        if isinstance(wallet_row, tuple)
                        else (wallet_row["balance_points"], wallet_row["frozen_points"])
                    )
                    if balance - frozen < points:
                        raise ValueError("insufficient points for refund")
                    cursor.execute(
                        "UPDATE user_wallets SET balance_points=balance_points-%s WHERE user_id=%s",
                        (points, user_id),
                    )
                    cursor.execute("UPDATE orders SET status='refunded',refunded_at=CURRENT_TIMESTAMP WHERE order_no=%s AND status='paid'", (order_no,))
                    cursor.execute("SELECT balance_points,frozen_points FROM user_wallets WHERE user_id=%s", (user_id,))
                    balance, frozen = cursor.fetchone()
                    cursor.execute(
                        "INSERT INTO wallet_transactions (user_id,request_id,transaction_type,points,balance_after,frozen_after,metadata) "
                        "VALUES (%s,%s,'refund',%s,%s,%s,%s)",
                        (user_id, order_no, -points, balance, frozen, '{"source":"recharge-refund"}'),
                    )
            connection.commit()
            return self.get(order_no)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_wallet(self, username: str) -> dict:
        if hasattr(self, "_testing_store"):
            wallet = self._testing_store["wallets"].setdefault(username, {"balance_points": 500, "frozen_points": 0})
            transactions = self._testing_store["wallet_transactions"].setdefault(username, [])
            if not transactions:
                transactions.append({"transaction_type": "grant", "points": 500, "balance_after": 500, "frozen_after": 0})
            return dict(wallet)
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM user_accounts WHERE username=%s", (username,))
                row = cursor.fetchone()
                if row is None:
                    raise KeyError("user not found")
                user_id = row[0] if isinstance(row, tuple) else row["id"]
                cursor.execute(
                    "INSERT IGNORE INTO user_wallets (user_id,balance_points,frozen_points) "
                    "VALUES (%s,500,0)",
                    (user_id,),
                )
                if cursor.rowcount == 1:
                    cursor.execute(
                        "INSERT INTO wallet_transactions "
                        "(user_id,request_id,transaction_type,points,balance_after,frozen_after,metadata) "
                        "VALUES (%s,NULL,'grant',500,500,0,%s)",
                        (user_id, '{"source":"wallet-initialization"}'),
                    )
                cursor.execute(
                    "SELECT balance_points,frozen_points FROM user_wallets WHERE user_id=%s",
                    (user_id,),
                )
                balance, frozen = cursor.fetchone()
            connection.commit()
            return {"balance_points": int(balance), "frozen_points": int(frozen)}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def wallet_transactions(self, username: str) -> list[dict]:
        if hasattr(self, "_testing_store"):
            return list(self._testing_store["wallet_transactions"].get(username, []))
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wt.transaction_type,wt.points,wt.balance_after,wt.frozen_after,wt.request_id "
                    "FROM wallet_transactions wt JOIN user_accounts ua ON ua.id=wt.user_id "
                    "WHERE ua.username=%s ORDER BY wt.id",
                    (username,),
                )
                rows = cursor.fetchall()
            keys = ("transaction_type", "points", "balance_after", "frozen_after", "request_id")
            return [dict(zip(keys, row)) if isinstance(row, tuple) else dict(row) for row in rows]
        finally:
            connection.close()

    def freeze_points(self, username: str, request_id: str, points: int) -> bool:
        self._validate_wallet_request(request_id)
        if type(points) is not int or points <= 0:
            return False
        if hasattr(self, "_testing_store"):
            wallet = self.ensure_wallet(username)
            entries = [item for item in self._testing_store["wallet_transactions"][username] if item.get("request_id") == request_id]
            if any(item["transaction_type"] in {"settle", "release"} for item in entries):
                return False
            if any(item["transaction_type"] == "freeze" for item in entries):
                return True
            if wallet["balance_points"] - wallet["frozen_points"] < points:
                return False
            wallet["frozen_points"] += points
            self._testing_store["wallets"][username] = wallet
            self._testing_store["wallet_transactions"][username].append({"transaction_type": "freeze", "points": points, "balance_after": wallet["balance_points"], "frozen_after": wallet["frozen_points"], "request_id": request_id})
            return True
        self.ensure_wallet(username)
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                user_id, balance, frozen = self._locked_wallet(cursor, username)
                entries = self._request_entries(cursor, user_id, request_id)
                if any(kind in {"settle", "release"} for kind, _ in entries):
                    connection.commit()
                    return False
                if any(kind == "freeze" for kind, _ in entries):
                    connection.commit()
                    return True
                if balance - frozen < points:
                    connection.commit()
                    return False
                frozen += points
                cursor.execute("UPDATE user_wallets SET frozen_points=%s WHERE user_id=%s", (frozen, user_id))
                cursor.execute(
                    "INSERT INTO wallet_transactions (user_id,request_id,transaction_type,points,balance_after,frozen_after) "
                    "VALUES (%s,%s,'freeze',%s,%s,%s)",
                    (user_id, request_id, points, balance, frozen),
                )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def settle_points(self, username: str, request_id: str, points: int) -> dict:
        self._validate_wallet_request(request_id)
        if type(points) is not int or points < 0:
            raise ValueError("settlement points must be a non-negative integer")
        if hasattr(self, "_testing_store"):
            wallet = self.ensure_wallet(username)
            entries = [item for item in self._testing_store["wallet_transactions"][username] if item.get("request_id") == request_id]
            if any(item["transaction_type"] == "settle" for item in entries):
                return dict(wallet)
            if any(item["transaction_type"] == "release" for item in entries):
                raise ValueError("reservation was already released")
            frozen_entry = next((item for item in entries if item["transaction_type"] == "freeze"), None)
            if frozen_entry is None or points > frozen_entry["points"]:
                raise ValueError("settlement exceeds the request reservation")
            wallet["balance_points"] -= points
            wallet["frozen_points"] -= frozen_entry["points"]
            self._testing_store["wallets"][username] = wallet
            self._testing_store["wallet_transactions"][username].append({"transaction_type": "settle", "points": points, "balance_after": wallet["balance_points"], "frozen_after": wallet["frozen_points"], "request_id": request_id})
            return dict(wallet)
        self.ensure_wallet(username)
        return self._finish_reservation_mysql(username, request_id, "settle", points)

    def release_points(self, username: str, request_id: str) -> dict:
        self._validate_wallet_request(request_id)
        if hasattr(self, "_testing_store"):
            wallet = self.ensure_wallet(username)
            entries = [item for item in self._testing_store["wallet_transactions"][username] if item.get("request_id") == request_id]
            if any(item["transaction_type"] in {"settle", "release"} for item in entries):
                return dict(wallet)
            frozen_entry = next((item for item in entries if item["transaction_type"] == "freeze"), None)
            if frozen_entry is None:
                return dict(wallet)
            released = frozen_entry["points"]
            wallet["frozen_points"] -= released
            self._testing_store["wallets"][username] = wallet
            self._testing_store["wallet_transactions"][username].append({"transaction_type": "release", "points": released, "balance_after": wallet["balance_points"], "frozen_after": wallet["frozen_points"], "request_id": request_id})
            return dict(wallet)
        self.ensure_wallet(username)
        return self._finish_reservation_mysql(username, request_id, "release", 0)

    def _finish_reservation_mysql(self, username, request_id, action, points):
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                user_id, balance, frozen = self._locked_wallet(cursor, username)
                entries = self._request_entries(cursor, user_id, request_id)
                if any(kind == action for kind, _ in entries):
                    connection.commit()
                    return {"balance_points": balance, "frozen_points": frozen}
                if any(kind in {"settle", "release"} for kind, _ in entries):
                    if action == "release":
                        connection.commit()
                        return {"balance_points": balance, "frozen_points": frozen}
                    raise ValueError("reservation is already finalized")
                reservation = next((value for kind, value in entries if kind == "freeze"), None)
                if reservation is None:
                    if action == "release":
                        connection.commit()
                        return {"balance_points": balance, "frozen_points": frozen}
                    raise ValueError("reservation not found")
                if action == "settle" and points > reservation:
                    raise ValueError("settlement exceeds the request reservation")
                charged = points if action == "settle" else 0
                balance -= charged
                frozen -= reservation
                cursor.execute(
                    "UPDATE user_wallets SET balance_points=%s,frozen_points=%s WHERE user_id=%s",
                    (balance, frozen, user_id),
                )
                cursor.execute(
                    "INSERT INTO wallet_transactions (user_id,request_id,transaction_type,points,balance_after,frozen_after) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (user_id, request_id, action, charged if action == "settle" else reservation, balance, frozen),
                )
            connection.commit()
            return {"balance_points": int(balance), "frozen_points": int(frozen)}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_wallet_request(request_id):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 64:
            raise ValueError("request_id must be a non-empty string up to 64 characters")

    @staticmethod
    def _request_entries(cursor, user_id, request_id):
        cursor.execute(
            "SELECT transaction_type,points FROM wallet_transactions "
            "WHERE user_id=%s AND request_id=%s ORDER BY id FOR UPDATE",
            (user_id, request_id),
        )
        rows = cursor.fetchall()
        return [(row[0], int(row[1])) if isinstance(row, tuple) else (row["transaction_type"], int(row["points"])) for row in rows]

    @staticmethod
    def _locked_wallet(cursor, username):
        cursor.execute(
            "SELECT ua.id,uw.balance_points,uw.frozen_points FROM user_accounts ua "
            "JOIN user_wallets uw ON uw.user_id=ua.id WHERE ua.username=%s FOR UPDATE",
            (username,),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError("wallet not found")
        values = row if isinstance(row, tuple) else (row["id"], row["balance_points"], row["frozen_points"])
        return int(values[0]), int(values[1]), int(values[2])

    def verify_user(self, username: str, password: str, require_admin: bool = False) -> bool:
        """Return whether an active account has the requested credentials and role."""
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT password_hash, is_active, role FROM user_accounts WHERE username = %s",
                    (username,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return False
        if isinstance(row, tuple):
            password_hash, is_active, role = row
        else:
            password_hash = row["password_hash"]
            is_active = row["is_active"]
            role = row["role"]
        if not is_active or (require_admin and role != "admin"):
            return False
        return check_password_hash(password_hash, password)

    def authenticate_administrator(self, username: str, password: str) -> dict | None:
        """Authenticate an administrator and return identity from one account snapshot."""
        if hasattr(self, "_testing_store"):
            account = self._testing_store["users"].get(username)
            if account is None:
                return None
            snapshot = {
                "username": username,
                "password_hash": account["password_hash"],
                "is_active": account["is_active"],
                "role": account["role"],
                "email": account.get("email"),
                "email_verified_at": account.get("email_verified_at"),
                "session_version": int(account.get("session_version", 0)),
            }
        else:
            connection = self._connect(database=self._database_name())
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT username,password_hash,is_active,role,email,email_verified_at,session_version "
                        "FROM user_accounts WHERE username = %s",
                        (username,),
                    )
                    row = cursor.fetchone()
            finally:
                connection.close()
            if row is None:
                return None
            values = self._row_values(
                row,
                "username",
                "password_hash",
                "is_active",
                "role",
                "email",
                "email_verified_at",
                "session_version",
            )
            snapshot = dict(
                zip(
                    (
                        "username",
                        "password_hash",
                        "is_active",
                        "role",
                        "email",
                        "email_verified_at",
                        "session_version",
                    ),
                    values,
                )
            )

        if (
            not snapshot["is_active"]
            or snapshot["role"] != "admin"
            or not check_password_hash(snapshot["password_hash"], password)
        ):
            return None
        identity = {"username": snapshot["username"]}
        if snapshot["email"] and snapshot["email_verified_at"] is not None:
            identity.update(
                email=snapshot["email"],
                session_version=int(snapshot["session_version"]),
            )
        return identity

    def list_users(self) -> list[dict]:
        """Return non-sensitive account details in deterministic creation order."""
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT ua.username,ua.role,ua.is_active,ua.created_at,ap.access_tier "
                    "FROM user_accounts ua LEFT JOIN admin_principals ap ON ap.user_id=ua.id "
                    "ORDER BY ua.created_at,ua.username"
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        users = []
        for row in rows:
            if isinstance(row, tuple):
                username, role, is_active, created_at, access_tier = row
            else:
                username = row["username"]
                role = row["role"]
                is_active = row["is_active"]
                created_at = row["created_at"]
                access_tier = row["access_tier"]
            users.append(
                {
                    "username": username,
                    "role": role,
                    "is_active": bool(is_active),
                    "created_at": created_at,
                    "access_tier": access_tier,
                }
            )
        return users

    def set_active(self, username: str, is_active: bool) -> bool:
        """Set an account's active state."""
        if hasattr(self, "_testing_store"):
            if is_active is False and self._is_owner(username):
                raise ValueError("cannot modify the final owner")
            return self._update(
                "UPDATE user_accounts SET is_active = %s WHERE username = %s",
                (is_active, username),
            )
        updated = self._update(
            "UPDATE user_accounts SET is_active = %s WHERE username = %s "
            "AND NOT (%s = FALSE AND id IN ("
            "SELECT user_id FROM admin_principals WHERE access_tier='owner'))",
            (is_active, username, is_active),
        )
        if not updated and is_active is False and self._is_owner(username):
            raise ValueError("cannot modify the final owner")
        return updated

    def set_role(self, username: str, role: str) -> bool:
        """Set an account role, rejecting unsupported roles."""
        if role not in VALID_ROLES:
            raise ValueError("role must be 'admin' or 'user'")
        if role == "user" and self._is_owner(username):
            raise ValueError("cannot modify the final owner")
        if hasattr(self, "_testing_store"):
            account = self._testing_store["users"].get(username)
            if account is None:
                return False
            account["role"] = role
            if role == "admin":
                self._testing_store["admin_principals"].setdefault(
                    account["id"],
                    {"access_tier": "admin", "policy_version": 1},
                )
            return True

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM user_accounts WHERE username=%s FOR UPDATE",
                    (username,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return False
                user_id = self._row_values(row, "id")[0]
                cursor.execute(
                    "UPDATE user_accounts SET role=%s WHERE id=%s", (role, user_id)
                )
                if role == "admin":
                    cursor.execute(
                        "INSERT INTO admin_principals "
                        "(user_id,access_tier,policy_version) VALUES (%s,'admin',1) "
                        "ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)",
                        (user_id,),
                    )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reset_password(self, username: str, password: str) -> bool:
        """Replace a password and atomically revoke existing versioned sessions."""
        if not isinstance(password, str) or not 9 <= len(password) <= 12:
            raise ValueError("密码长度必须为 9–12 位")
        return self._update(
            "UPDATE user_accounts SET password_hash = %s, session_version = session_version + 1 "
            "WHERE username = %s",
            (generate_password_hash(password), username),
        )

    def ensure_admin(self, username: str, password: str) -> None:
        """Create a missing administrator or promote an existing account without resetting it."""
        if hasattr(self, "_testing_store"):
            account = self._testing_store["users"].get(username)
            if account is None:
                self.create_user(username, password)
                account = self._testing_store["users"][username]
            account["role"] = "admin"
            self._testing_store["admin_principals"].setdefault(
                account["id"], {"access_tier": "admin", "policy_version": 1}
            )
            return
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT username FROM user_accounts WHERE username = %s", (username,))
                if cursor.fetchone() is None:
                    cursor.execute(
                        "INSERT INTO user_accounts (username, password_hash, role) VALUES (%s, %s, %s)",
                        (username, generate_password_hash(password), "admin"),
                    )
                else:
                    cursor.execute(
                        "UPDATE user_accounts SET role = %s WHERE username = %s", ("admin", username)
                    )
                cursor.execute(
                    "INSERT INTO admin_principals (user_id,access_tier,policy_version) "
                    "SELECT id,'admin',1 FROM user_accounts WHERE username=%s "
                    "ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)",
                    (username,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_owner(self, username: str, password: str) -> None:
        """Assign the configured owner to one immutable administrator account."""
        if hasattr(self, "_testing_store"):
            account = self._testing_store["users"].get(username)
            existing_owner_ids = {
                user_id
                for user_id, principal in self._testing_store["admin_principals"].items()
                if principal["access_tier"] == "owner"
            }
            if existing_owner_ids and (
                account is None or account["id"] not in existing_owner_ids
            ):
                raise ValueError("an owner is already assigned")
            if account is None:
                self.create_user(username, password)
                account = self._testing_store["users"][username]
            account["role"] = "admin"
            principal = self._testing_store["admin_principals"].get(account["id"])
            if principal is None:
                self._testing_store["admin_principals"][account["id"]] = {
                    "access_tier": "owner",
                    "policy_version": 1,
                }
            elif principal["access_tier"] != "owner":
                principal.update(
                    access_tier="owner",
                    policy_version=int(principal["policy_version"]) + 1,
                )
            return

        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM user_accounts WHERE username=%s FOR UPDATE", (username,)
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        "INSERT INTO user_accounts (username,password_hash,role) "
                        "VALUES (%s,%s,'admin')",
                        (username, generate_password_hash(password)),
                    )
                    user_id = cursor.lastrowid
                else:
                    user_id = self._row_values(row, "id")[0]
                    cursor.execute(
                        "UPDATE user_accounts SET role='admin' WHERE id=%s", (user_id,)
                    )
                cursor.execute(
                    "SELECT user_id FROM admin_principals "
                    "WHERE access_tier='owner' AND user_id<>%s FOR UPDATE",
                    (user_id,),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("an owner is already assigned")
                cursor.execute(
                    "INSERT INTO admin_principals (user_id,access_tier,policy_version) "
                    "VALUES (%s,'owner',1) ON DUPLICATE KEY UPDATE "
                    "policy_version=policy_version+(access_tier<>'owner'),access_tier='owner'",
                    (user_id,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_admin_principal(self, username: str) -> dict | None:
        """Return the safe authorization principal for an administrator account."""
        if hasattr(self, "_testing_store"):
            account = self._testing_store["users"].get(username)
            if account is None or account["role"] != "admin" or not account["is_active"]:
                return None
            principal = self._testing_store["admin_principals"].get(account["id"])
            if principal is None:
                return None
            return {
                "username": username,
                "access_tier": principal["access_tier"],
                "policy_version": int(principal["policy_version"]),
            }
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT ua.username,ap.access_tier,ap.policy_version "
                    "FROM user_accounts ua JOIN admin_principals ap ON ap.user_id=ua.id "
                    "WHERE ua.username=%s AND ua.role='admin' AND ua.is_active=TRUE",
                    (username,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        values = self._row_values(row, "username", "access_tier", "policy_version")
        return {
            "username": values[0],
            "access_tier": values[1],
            "policy_version": int(values[2]),
        }

    def _is_owner(self, username: str) -> bool:
        principal = self.get_admin_principal(username)
        return principal is not None and principal["access_tier"] == "owner"

    def _update(self, sql, params) -> bool:
        connection = self._connect(database=self._database_name())
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                updated = cursor.rowcount > 0
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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

    def _database_name(self):
        database_name = self._setting("USER_DATABASE", "user_db")
        if not isinstance(database_name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,63}", database_name
        ):
            raise ValueError("USER_DATABASE must be a safe SQL identifier")
        return database_name

    def _setting(self, name, default):
        if isinstance(self.config, dict):
            return self.config.get(name, default)
        return getattr(self.config, name, default)

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def _phone_binding_timestamp(value: datetime) -> datetime:
        """Match the second precision used by MySQL DATETIME phone-binding columns."""
        return value.replace(microsecond=0)

    def _testing_account_by_email(self, email: str) -> dict | None:
        return next(
            (
                account
                for account in self._testing_store["users"].values()
                if account.get("email") == email
            ),
            None,
        )

    def _testing_account_by_phone(self, phone: str) -> dict | None:
        return next(
            (
                account
                for account in self._testing_store["users"].values()
                if account.get("phone_number") == phone
            ),
            None,
        )

    @staticmethod
    def _email_account_summary(account: dict) -> dict:
        return {
            "username": account["username"],
            "email": account["email"],
            "session_version": int(account["session_version"]),
        }

    @staticmethod
    def _row_values(row, *keys):
        if isinstance(row, tuple):
            return row
        return tuple(row[key] for key in keys)

    @staticmethod
    def _one_minute():
        return timedelta(minutes=1)

    @staticmethod
    def _one_hour():
        return timedelta(hours=1)

    def _execute_optional_migration(self, cursor, statement):
        try:
            cursor.execute(statement)
        except Exception as error:
            if statement.startswith("CREATE UNIQUE INDEX"):
                if not self._is_duplicate_index_error(error):
                    raise
            elif not self._is_duplicate_column_error(error):
                raise

    @staticmethod
    def _pymysql_connect(**options):
        import pymysql

        return pymysql.connect(**options)

    @staticmethod
    def _is_duplicate_error(error):
        error_args = getattr(error, "args", ())
        return bool(error_args) and error_args[0] == 1062

    @staticmethod
    def _is_duplicate_column_error(error):
        error_args = getattr(error, "args", ())
        return bool(error_args) and error_args[0] == 1060

    @staticmethod
    def _is_duplicate_index_error(error):
        error_args = getattr(error, "args", ())
        return bool(error_args) and error_args[0] == 1061


class _DuplicateUsernameError(Exception):
    def __init__(self):
        super().__init__(1062, "Duplicate entry")


class _MemoryConnection:
    """Test-only SQL subset for the standalone user database."""

    def __init__(self, store, database, phone_binding_lock):
        self._store = store
        self._database = database
        self.phone_binding_lock = phone_binding_lock

    def cursor(self):
        return _MemoryCursor(self._store)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _MemoryCursor:
    def __init__(self, store):
        self._store = store
        self._row = None
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params=None):
        statement = " ".join(sql.split()).upper()
        if (
            statement.startswith("CREATE DATABASE")
            or statement.startswith("CREATE TABLE")
            or statement.startswith("CREATE UNIQUE INDEX")
        ):
            return
        if (
            statement.startswith("ALTER TABLE USER_ACCOUNTS")
            or statement.startswith("ALTER TABLE ADMIN_PRINCIPALS")
            or statement.startswith("ALTER TABLE WALLET_TRANSACTIONS")
            or statement.startswith("ALTER TABLE EMAIL_VERIFICATION_CODES")
        ):
            return
        if statement.startswith("UPDATE USER_ACCOUNTS SET ROLE = 'USER' WHERE"):
            for account in self._store["users"].values():
                if not isinstance(account, dict):
                    continue
                if account.get("role") not in VALID_ROLES:
                    account["role"] = "user"
            return
        if statement.startswith("INSERT INTO ADMIN_PRINCIPALS") and params is None:
            for account in self._store["users"].values():
                if account.get("role") == "admin":
                    self._store["admin_principals"].setdefault(
                        account["id"],
                        {"access_tier": "admin", "policy_version": 1},
                    )
            return
        if statement.startswith("INSERT IGNORE INTO USER_ACCOUNTS"):
            username, password_hash = params
            self._store["users"].setdefault(username, self._account(password_hash))
            return
        if statement.startswith("INSERT INTO USER_ACCOUNTS"):
            username, password_hash, *role = params
            if username in self._store["users"]:
                raise _DuplicateUsernameError()
            self._store["users"][username] = self._account(
                password_hash, role[0] if role else "user"
            )
            self._store["users"][username]["id"] = len(self._store["users"])
            self.rowcount = 1
            return
        if statement.startswith("SELECT PASSWORD_HASH, IS_ACTIVE, ROLE FROM USER_ACCOUNTS"):
            account = self._store["users"].get(params[0])
            self._row = (
                None
                if account is None
                else (account["password_hash"], account["is_active"], account["role"])
            )
            return
        if statement.startswith(
            "SELECT EMAIL,SESSION_VERSION FROM USER_ACCOUNTS WHERE USERNAME = %S "
            "AND IS_ACTIVE = TRUE AND EMAIL_VERIFIED_AT IS NOT NULL"
        ):
            account = self._store["users"].get(params[0])
            self._row = (
                None
                if account is None
                or not account["is_active"]
                or account.get("email_verified_at") is None
                else (account["email"], account["session_version"])
            )
            return
        if statement.startswith("SELECT PASSWORD_HASH FROM USER_ACCOUNTS"):
            account = self._store["users"].get(params[0])
            self._row = None if account is None else (account["password_hash"],)
            return
        if statement.startswith("SELECT USERNAME FROM USER_ACCOUNTS"):
            self._row = (params[0],) if params[0] in self._store["users"] else None
            return
        if statement.startswith(
            "SELECT UA.USERNAME,UA.ROLE,UA.IS_ACTIVE,UA.CREATED_AT,AP.ACCESS_TIER"
        ):
            accounts = self._store["users"].items()
            self._rows = [
                (
                    username,
                    account["role"],
                    account["is_active"],
                    account["created_at"],
                    self._store["admin_principals"].get(account["id"], {}).get("access_tier"),
                )
                for username, account in sorted(
                    accounts,
                    key=lambda item: (
                        item[1]["created_at"] is not None,
                        item[1]["created_at"] or "",
                        item[0],
                    ),
                )
            ]
            return
        if statement.startswith("UPDATE USER_ACCOUNTS SET IS_ACTIVE = %S WHERE USERNAME = %S"):
            is_active, username = params
            account = self._store["users"].get(username)
            if account is not None:
                account["is_active"] = bool(is_active)
                self.rowcount = 1
            return
        if statement.startswith("UPDATE USER_ACCOUNTS SET ROLE = %S WHERE USERNAME = %S"):
            role, username = params
            account = self._store["users"].get(username)
            if account is not None:
                account["role"] = role
                self.rowcount = 1
            return
        if statement.startswith(
            "UPDATE USER_ACCOUNTS SET PASSWORD_HASH = %S, SESSION_VERSION = SESSION_VERSION + 1 "
            "WHERE USERNAME = %S"
        ):
            password_hash, username = params
            account = self._store["users"].get(username)
            if account is not None:
                account["password_hash"] = password_hash
                account["session_version"] = int(account.get("session_version", 0)) + 1
                self.rowcount = 1
            return
        raise AssertionError(f"Unsupported test SQL: {sql}")

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows

    @staticmethod
    def _account(password_hash, role="user"):
        return {
            "id": None,
            "password_hash": password_hash,
            "is_active": True,
            "role": role,
            "created_at": None,
        }
