from __future__ import annotations

import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = Path(os.getenv("BID_PLATFORM_INSTANCE_DIR", str(BASE_DIR / "instance"))).resolve()
STORAGE_DIR = Path(os.getenv("BID_PLATFORM_STORAGE_DIR", str(BASE_DIR / "storage"))).resolve()
TMP_DIR = STORAGE_DIR / "tmp"
PROJECT_STORAGE_DIR = STORAGE_DIR / "projects"
TEMPLATES_DIR = BASE_DIR / "platform_app" / "templates"
STATIC_DIR = BASE_DIR / "platform_app" / "static"
_DEVELOPMENT_SECRET = secrets.token_urlsafe(48)
_DEVELOPMENT_ADMIN_PASSWORD = secrets.token_urlsafe(18)


def is_production() -> bool:
    return os.getenv("BID_PLATFORM_ENV", "development").strip().lower() == "production"


def _required_in_production(name: str, development_value: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    if is_production():
        raise RuntimeError(f"生产环境必须设置 {name}。")
    return development_value


def get_database_url() -> str:
    return os.getenv("BID_PLATFORM_DATABASE_URL", f"sqlite:///{(INSTANCE_DIR / 'platform.db').as_posix()}")


def get_secret_key() -> str:
    return _required_in_production("BID_PLATFORM_SECRET_KEY", _DEVELOPMENT_SECRET)


def get_admin_username() -> str:
    return os.getenv("BID_PLATFORM_ADMIN_USERNAME", "admin")


def get_admin_password() -> str:
    return _required_in_production("BID_PLATFORM_ADMIN_PASSWORD", _DEVELOPMENT_ADMIN_PASSWORD)


def get_app_name() -> str:
    return os.getenv("BID_PLATFORM_APP_NAME", "bid-platform")


def get_public_base_url() -> str:
    return os.getenv("BID_PLATFORM_PUBLIC_BASE_URL", "http://127.0.0.1:8010").rstrip("/")


def legacy_ai_routes_enabled() -> bool:
    return os.getenv("BID_PLATFORM_ENABLE_LEGACY_AI", "0").strip().lower() in {"1", "true", "yes"}


def get_deepseek_base_url() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


def get_deepseek_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def get_system_setting_env_key() -> str:
    return "BID_PLATFORM_DEEPSEEK_API_KEY"


def get_paddleocr_api_url_env_key() -> str:
    return "PADDLEOCR_DOC_PARSING_API_URL"


def get_paddleocr_access_token_env_key() -> str:
    return "PADDLEOCR_ACCESS_TOKEN"


def get_paddleocr_timeout_env_key() -> str:
    return "PADDLEOCR_DOC_PARSING_TIMEOUT"
