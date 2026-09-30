"""Runtime configuration for the campus traffic monitor.

Values are read from environment variables or a local .env file.  Do not put
real passwords, webhook tokens, or Flask keys in source control.
"""

from __future__ import annotations

import os
from pathlib import Path

import pymysql


BASE_DIR = Path(__file__).resolve().parent


def load_local_env(path: Path = BASE_DIR / ".env") -> None:
    """Load a tiny KEY=VALUE .env file without adding a runtime dependency."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


load_local_env()


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "development-only-change-me")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = env_int("DB_PORT", 3306)
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "campus_traffic")

    DINGTALK_ACCESS_TOKEN = os.getenv("DINGTALK_ACCESS_TOKEN", "")
    DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")
    ALERT_NOTIFY_INTERVAL_MINUTES = env_int("ALERT_NOTIFY_INTERVAL_MINUTES", 15)
    ALERT_NOTIFY_MAX_COUNT = env_int("ALERT_NOTIFY_MAX_COUNT", 3)
    ENABLE_ALERT_CHECKER = env_bool("ENABLE_ALERT_CHECKER", False)

    FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
    FLASK_PORT = env_int("FLASK_PORT", 5000)
    FLASK_DEBUG = env_bool("FLASK_DEBUG", False)
    DEFAULT_USER_ROLE = 2


def get_db_connection():
    """Create a short-lived database connection for one application action."""
    return pymysql.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        charset="utf8mb4",
        database=Config.DB_NAME,
        autocommit=False,
    )
