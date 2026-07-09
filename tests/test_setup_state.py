"""Setup state tests · Тесты первого запуска и проверки сессии"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config_store import SettingsData
from app.i18n import tr
from app.setup_state import (
    api_is_configured,
    auth_result_needs_login_prompt,
    resolve_wizard_dialog_start_step,
    resolve_wizard_start_step,
    session_file_exists,
    session_login_should_be_verified,
    setup_wizard_is_completed,
    validate_download_dir,
    wizard_required,
)
from app.telegram_auth import AuthResult


def test_api_is_configured_rejects_empty_and_placeholder() -> None:
    assert not api_is_configured(SettingsData())
    assert not api_is_configured(
        SettingsData(api_id="123", api_hash="your_api_hash_here"),
    )
    assert api_is_configured(SettingsData(api_id="12345", api_hash="a" * 32))


def test_api_is_configured_rejects_invalid_id() -> None:
    assert not api_is_configured(SettingsData(api_id="abc", api_hash="a" * 32))
    assert not api_is_configured(SettingsData(api_id="0", api_hash="a" * 32))


def test_wizard_required_without_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("app.setup_state.ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr("app.setup_state.ENV_EXAMPLE_PATH", tmp_path / ".env.example")

    assert wizard_required(SettingsData()) is True


def test_wizard_required_when_setup_not_completed(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "API_ID=1\nAPI_HASH=" + "x" * 32 + "\nSETUP_WIZARD_COMPLETED=false\n",
        encoding="utf-8",
    )
    session_base = tmp_path / "test"
    session_file = session_base.with_suffix(".session")
    session_file.write_bytes(b"session")
    monkeypatch.setattr("app.setup_state.ENV_PATH", env_path)
    monkeypatch.setattr("app.setup_state.session_path_for", lambda _name: session_base)

    settings = SettingsData(
        api_id="1",
        api_hash="x" * 32,
        session_name="test",
        setup_wizard_completed=False,
    )
    assert wizard_required(settings) is True


def test_wizard_not_required_when_setup_completed(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("API_ID=1\nAPI_HASH=" + "x" * 32 + "\n", encoding="utf-8")
    session_base = tmp_path / "test"
    session_file = session_base.with_suffix(".session")
    session_file.write_bytes(b"session")
    monkeypatch.setattr("app.setup_state.ENV_PATH", env_path)
    monkeypatch.setattr("app.setup_state.session_path_for", lambda _name: session_base)

    settings = SettingsData(
        api_id="1",
        api_hash="x" * 32,
        session_name="test",
        setup_wizard_completed=True,
    )
    assert wizard_required(settings) is False


def test_setup_wizard_is_completed_migrates_existing_install(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("API_ID=1\nAPI_HASH=" + "x" * 32 + "\n", encoding="utf-8")
    session_base = tmp_path / "test"
    session_base.with_suffix(".session").write_bytes(b"session")
    monkeypatch.setattr("app.setup_state.ENV_PATH", env_path)
    monkeypatch.setattr("app.setup_state.session_path_for", lambda _name: session_base)

    settings = SettingsData(
        api_id="1",
        api_hash="x" * 32,
        session_name="test",
        setup_wizard_completed=False,
    )
    assert setup_wizard_is_completed(settings) is True


def test_resolve_wizard_start_step_api_first() -> None:
    assert resolve_wizard_start_step(SettingsData()) == "api"


def test_resolve_wizard_dialog_start_step_fresh_first_run() -> None:
    assert (
        resolve_wizard_dialog_start_step(SettingsData(), first_run=True)
        == "language"
    )


def test_resolve_wizard_dialog_start_step_skip_welcome() -> None:
    assert (
        resolve_wizard_dialog_start_step(SettingsData(), skip_welcome=True)
        == "language"
    )


def test_resolve_wizard_dialog_start_step_resume_after_partial_setup() -> None:
    settings = SettingsData(setup_wizard_completed=True, api_id="", api_hash="")
    assert (
        resolve_wizard_dialog_start_step(settings, first_run=True)
        == "api"
    )


def test_resolve_wizard_start_step_login_without_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.setup_state.session_path_for",
        lambda _name: tmp_path / "missing",
    )
    settings = SettingsData(api_id="1", api_hash="x" * 32)
    assert resolve_wizard_start_step(settings) == "login"


def test_resolve_wizard_start_step_finish_when_ready(tmp_path: Path, monkeypatch) -> None:
    session_base = tmp_path / "test"
    session_base.with_suffix(".session").write_bytes(b"session")
    monkeypatch.setattr("app.setup_state.session_path_for", lambda _name: session_base)
    monkeypatch.chdir(tmp_path)

    settings = SettingsData(
        api_id="1",
        api_hash="x" * 32,
        session_name="test",
        download_dir="downloads",
    )
    assert resolve_wizard_start_step(settings) == "finish"


def test_session_login_should_be_verified_only_with_api_and_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_base = tmp_path / "test"
    session_file = session_base.with_suffix(".session")
    session_file.write_bytes(b"session")
    monkeypatch.setattr("app.setup_state.session_path_for", lambda _name: session_base)

    configured = SettingsData(api_id="1", api_hash="x" * 32, session_name="test")
    assert session_login_should_be_verified(configured) is True
    assert session_login_should_be_verified(SettingsData()) is False


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_auth_result_needs_login_prompt(locale) -> None:
    assert not auth_result_needs_login_prompt(AuthResult(ok=True, username="user"))
    assert auth_result_needs_login_prompt(
        AuthResult(ok=False, needs_phone=True, error=tr("auth.error.not_logged_in")),
    )
    assert not auth_result_needs_login_prompt(
        AuthResult(ok=False, connection_failed=True, error=tr("errors.no_connection")),
    )
    assert auth_result_needs_login_prompt(
        AuthResult(ok=False, error=tr("errors.session_expired")),
    )


def test_validate_download_dir_creates_and_accepts_writable_path(tmp_path: Path) -> None:
    target = tmp_path / "downloads"
    assert validate_download_dir(str(target)) is None
    assert target.is_dir()


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_validate_download_dir_rejects_file_path(locale, tmp_path: Path) -> None:
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("x", encoding="utf-8")
    error = validate_download_dir(str(file_path))
    assert error is not None
    create_failed_prefix = tr("setup.dir.create_failed", path=file_path, exc="").rsplit(":", 1)[0] + ":"
    assert error == tr("setup.dir.not_folder") or error.startswith(create_failed_prefix)


def test_session_file_exists(tmp_path: Path, monkeypatch) -> None:
    session_base = tmp_path / "orphie"
    monkeypatch.setattr("app.setup_state.session_path_for", lambda _name: session_base)
    assert not session_file_exists("orphie")
    session_base.with_suffix(".session").write_bytes(b"x")
    assert session_file_exists("orphie")
