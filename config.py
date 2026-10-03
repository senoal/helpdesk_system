import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"


def sqlite_url(path):
    """Build a cross-platform absolute SQLite URL (including on Windows)."""
    return f"sqlite:///{Path(path).resolve().as_posix()}"


def env_value(name, default=None):
    """Return a non-empty environment value, or a portable default."""
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


class Config:
    SECRET_KEY = env_value("SECRET_KEY", "change-this-secret-in-production")
    ENVIRONMENT = env_value("APP_ENV", "development").lower()
    APP_TIMEZONE = env_value("APP_TIMEZONE", "Asia/Jakarta")
    SQLALCHEMY_DATABASE_URI = env_value(
        "DATABASE_URL", sqlite_url(INSTANCE_DIR / "servicehub.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ITEMS_PER_PAGE = 10
    MAX_CONTENT_LENGTH = 55 * 1024 * 1024
    MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
    MAX_ATTACHMENTS_PER_TICKET = 5
    UPLOAD_FOLDER = env_value("UPLOAD_FOLDER")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = ENVIRONMENT == "production"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = ENVIRONMENT == "production"
    PERMANENT_SESSION_LIFETIME = 8 * 60 * 60
    WTF_CSRF_TIME_LIMIT = 8 * 60 * 60
    AUTO_CREATE_DB = os.getenv("AUTO_CREATE_DB", "1").lower() in {"1", "true", "yes"}


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    UPLOAD_FOLDER = str(BASE_DIR / "instance" / "test_uploads")
    SECRET_KEY = "test-secret-key"
    AUTO_CREATE_DB = True
