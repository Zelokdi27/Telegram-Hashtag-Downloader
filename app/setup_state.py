"""Setup state · Первый запуск и окружение"""

from __future__ import annotations

import os
import shutil
from typing import Literal

from dotenv import dotenv_values

from .config_store import (
    ENV_EXAMPLE_PATH,
    ENV_PATH,
    SettingsData,
    load_settings,
    resolve_download_dir,
    session_path_for,
)
from .i18n import tr
from .telegram_auth import AuthResult

WizardStartStep = Literal["welcome", "language", "api", "proxy", "login", "download", "finish"]


def api_is_configured(settings: SettingsData) -> bool:
    """API configured · Реальные ключи API"""
    api_id = settings.api_id.strip()
    api_hash = settings.api_hash.strip()
    if not api_id or not api_hash:
        return False
    if api_hash.lower() == "your_api_hash_here":
        return False
    try:
        if int(api_id) <= 0:
            return False
    except ValueError:
        return False
    return True


def ensure_env_file() -> bool:
    """Ensure env file · Создание .env из примера"""
    if ENV_PATH.exists():
        return False
    if not ENV_EXAMPLE_PATH.exists():
        return False
    shutil.copy2(ENV_EXAMPLE_PATH, ENV_PATH)
    text = ENV_PATH.read_text(encoding="utf-8")
    text = text.replace("API_ID=12345678", "API_ID=")
    text = text.replace("API_HASH=your_api_hash_here", "API_HASH=")
    ENV_PATH.write_text(text, encoding="utf-8")
    return True


def session_file_exists(session_name: str) -> bool:
    base = session_path_for(session_name)
    return base.with_suffix(".session").is_file()


def _setup_wizard_flag_in_env() -> bool | None:
    """Wizard flag · None если ключ мастера отсутствует"""
    if not ENV_PATH.exists():
        return None
    env = dotenv_values(ENV_PATH)
    if "SETUP_WIZARD_COMPLETED" not in env:
        return None
    raw = str(env.get("SETUP_WIZARD_COMPLETED", "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def setup_wizard_is_completed(settings: SettingsData | None = None) -> bool:
    """Wizard completed · Мастер завершён"""
    settings = settings or load_settings(include_session=False)
    if settings.setup_wizard_completed:
        return True
    flag = _setup_wizard_flag_in_env()
    if flag is None:
        return api_is_configured(settings) and session_file_exists(settings.session_name)
    return False


def resolve_wizard_start_step(settings: SettingsData | None = None) -> WizardStartStep:
    """Wizard start step · Первый незавершённый шаг"""
    settings = settings or load_settings(include_session=False)
    if not (settings.ui_language or "").strip():
        return "language"
    if not api_is_configured(settings):
        return "api"
    if not session_file_exists(settings.session_name):
        return "login"
    if validate_download_dir(settings.download_dir) is not None:
        return "download"
    return "finish"


def resolve_wizard_dialog_start_step(
    settings: SettingsData,
    *,
    first_run: bool = False,
    skip_welcome: bool = False,
    start_at_login: bool = False,
) -> WizardStartStep:
    """Wizard dialog start · С какой страницы открыть мастер в UI"""
    if start_at_login:
        return "login"
    if skip_welcome:
        return "language"
    if first_run:
        if settings.setup_wizard_completed:
            return resolve_wizard_start_step(settings)
        return "language"
    return "welcome"


def wizard_required(settings: SettingsData | None = None) -> bool:
    """Wizard required · Нужен ли мастер при запуске"""
    settings = settings or load_settings(include_session=False)
    if not ENV_PATH.exists():
        return True
    return not setup_wizard_is_completed(settings)


def validate_download_dir(value: str) -> str | None:
    """Download dir validate · Проверка папки загрузок"""
    raw = value.strip() or "data/downloads"
    path = resolve_download_dir(raw)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return tr("setup.dir.create_failed", path=path, exc=exc)
    if not path.is_dir():
        return tr("setup.dir.not_folder")
    if not os.access(path, os.W_OK):
        return tr("setup.dir.not_writable", path=path)
    return None


def session_login_should_be_verified(settings: SettingsData | None = None) -> bool:
    """Session verify · Проверка живости сессии"""
    settings = settings or load_settings(include_session=False)
    return api_is_configured(settings) and session_file_exists(settings.session_name)


def auth_result_needs_login_prompt(result: AuthResult) -> bool:
    """Login prompt needed · Сессия недействительна"""
    if result.ok:
        return False
    if result.needs_phone:
        return True
    if result.connection_failed:
        return False
    return bool((result.error or "").strip())
