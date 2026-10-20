"""Local, secret-free administrator environment bootstrap command."""

import os
import secrets
from collections.abc import Callable, MutableMapping
from types import SimpleNamespace

try:
    import winreg
except ImportError:  # pragma: no cover - Windows provides winreg.
    winreg = None

import config
from auth import initialize_auth
from flask import Flask
from user_database import UserDatabase


def bootstrap_admin(
    environment: MutableMapping[str, str],
    set_user_variable: Callable[[str, str], None],
    initialize: Callable[..., None],
) -> None:
    """Create or promote the environment-configured administrator without logging secrets."""
    _require_nonblank(environment, "MYSQL_PASSWORD", "缺少 MYSQL_PASSWORD 环境变量，未初始化数据库。")
    _require_nonblank(environment, "ADMIN_USERNAME", "缺少 ADMIN_USERNAME 环境变量。")
    _require_nonblank(environment, "ADMIN_PASSWORD", "缺少 ADMIN_PASSWORD 环境变量。")

    secret_key = environment.get("FLASK_SECRET_KEY", "")
    if not isinstance(secret_key, str) or len(secret_key) < 32 or not secret_key.strip():
        secret_key = secrets.token_urlsafe(32)
        set_user_variable("FLASK_SECRET_KEY", secret_key)
        environment["FLASK_SECRET_KEY"] = secret_key

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key,
        ADMIN_USERNAME=environment["ADMIN_USERNAME"],
        ADMIN_PASSWORD=environment["ADMIN_PASSWORD"],
    )
    user_database = UserDatabase(_database_settings(environment))
    user_database.initialize()
    initialize(app, user_database)


def set_current_user_variable(name: str, value: str) -> None:
    """Persist one environment variable for the Windows current user only."""
    if winreg is None:
        raise OSError("当前操作系统不支持 Windows 用户环境变量。")
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as environment_key:
        winreg.SetValueEx(environment_key, name, 0, winreg.REG_SZ, value)


def main() -> int:
    """Run the local bootstrap command and emit no credential values."""
    try:
        bootstrap_admin(os.environ, set_current_user_variable, initialize_auth)
    except Exception:
        print("管理员引导失败，请检查运行环境配置。")
        return 1
    print("管理员引导完成。")
    return 0


def _require_nonblank(environment: MutableMapping[str, str], name: str, message: str) -> None:
    value = environment.get(name, "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)


def _database_settings(environment: MutableMapping[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        MYSQL_HOST=environment.get("MYSQL_HOST", config.MYSQL_HOST),
        MYSQL_PORT=environment.get("MYSQL_PORT", config.MYSQL_PORT),
        MYSQL_USER=environment.get("MYSQL_USER", config.MYSQL_USER),
        MYSQL_PASSWORD=environment["MYSQL_PASSWORD"],
        USER_DATABASE=environment.get("USER_DATABASE", config.USER_DATABASE),
    )


if __name__ == "__main__":
    raise SystemExit(main())
