"""Setup wizard sync tests · Синхронизация мастера настройки с главным окном"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.config_store import SettingsData
from qt_ui import setup_wizard as sw_mod


class _FakeEntry:
    def __init__(self) -> None:
        self.value = ""

    def setText(self, value: str) -> None:
        self.value = value

    def text(self) -> str:
        return self.value


class _FakeSpin:
    def __init__(self) -> None:
        self.value = 1

    def setValue(self, value: int) -> None:
        self.value = value


class _FakeCheck:
    def __init__(self) -> None:
        self.checked = False

    def setChecked(self, value: bool) -> None:
        self.checked = value

    def isChecked(self) -> bool:
        return self.checked


class FakeMainWindow:
    def __init__(self) -> None:
        self.api_id_entry = _FakeEntry()
        self.api_hash_entry = _FakeEntry()
        self.session_name_entry = _FakeEntry()
        self.proxy_enabled_check = _FakeCheck()
        self.proxy_type_entry = _FakeEntry()
        self.proxy_host_entry = _FakeEntry()
        self.proxy_port_spin = _FakeSpin()
        self.persist_calls = 0

    def _persist_settings(self) -> bool:
        self.persist_calls += 1
        return True


def _bare_wizard(main: FakeMainWindow) -> sw_mod.SetupWizard:
    wizard = sw_mod.SetupWizard.__new__(sw_mod.SetupWizard)
    wizard.main_window = main
    wizard.api_id = ""
    wizard.api_hash = ""
    wizard.session_name = "hashtag_session"
    wizard.proxy_enabled = False
    wizard.proxy_type = "socks5"
    wizard.proxy_host = "127.0.0.1"
    wizard.proxy_port = 1080
    return wizard


def test_setup_wizard_sync_to_main_window() -> None:
    main = FakeMainWindow()

    with patch("qt_ui.main_window.HashtagDownloaderWindow", FakeMainWindow):
        wizard = _bare_wizard(main)
        wizard.api_id = "99999"
        wizard.api_hash = "b" * 32
        wizard.session_name = "other"
        wizard.proxy_enabled = False
        wizard.proxy_type = "socks5"
        wizard.proxy_host = "127.0.0.1"
        wizard.proxy_port = 1080

        assert wizard.sync_to_main_window() is True

    assert main.persist_calls == 1
    assert main.api_id_entry.value == "99999"
    assert main.api_hash_entry.value == "b" * 32
    assert main.session_name_entry.value == "other"
    assert main.proxy_enabled_check.checked is False
    assert main.proxy_type_entry.value == "socks5"
    assert main.proxy_host_entry.value == "127.0.0.1"
    assert main.proxy_port_spin.value == 1080


def test_setup_wizard_sync_api_to_main_window() -> None:
    main = FakeMainWindow()

    with patch("qt_ui.main_window.HashtagDownloaderWindow", FakeMainWindow):
        wizard = _bare_wizard(main)
        wizard.api_id = "42"
        wizard.api_hash = "d" * 32

        assert wizard.sync_api_to_main_window() is True

    assert main.persist_calls == 1
    assert main.api_id_entry.value == "42"
    assert main.api_hash_entry.value == "d" * 32
    assert main.session_name_entry.value == ""


def test_setup_wizard_sync_returns_false_for_non_main_window() -> None:
    wizard = _bare_wizard(FakeMainWindow())
    wizard.main_window = MagicMock()
    assert wizard.sync_to_main_window() is False
