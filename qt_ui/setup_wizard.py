"""Setup wizard · Мастер первого запуска"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

from app.autotune import (
    format_autotune_summary,
    profile_matches_settings,
    run_autotune_sync,
)
from app.i18n import set_locale, tr
from app.config_store import (
    PROJECT_DIR,
    SettingsData,
    load_settings,
    resolve_download_dir,
    save_settings,
)
from app.setup_state import (
    api_is_configured,
    ensure_env_file,
    resolve_wizard_dialog_start_step,
    session_file_exists,
    validate_download_dir,
)
from app.telegram_auth import AuthResult, test_telegram_connectivity
from app.win_notify import notifications_available

from .dialogs import ask_yes_no, show_error, show_info
from .theme import palette_for, style_link_label
from .win_chrome import apply_window_theme, present_top_level_window

TELEGRAM_APPS_URL = "https://my.telegram.org/apps"
WIZARD_STEP_COUNT = 7
WIZARD_STEP_KEYS = ("language", "welcome", "api", "proxy", "login", "download", "finish")


def _wizard_step_number(step_key: str) -> int:
    return WIZARD_STEP_KEYS.index(step_key) + 1


def _format_step_subtitle(step_key: str, step_number: int) -> str:
    hint = tr(f"wizard.step.{step_key}")
    return tr("wizard.step_counter", n=step_number, total=WIZARD_STEP_COUNT, hint=hint)


def _download_dir_quick_paths() -> list[tuple[str, str]]:
    home = Path.home()
    return [
        (tr("wizard.dir.default"), str(PROJECT_DIR / "data" / "downloads")),
        (tr("wizard.dir.pictures"), str(home / "Pictures")),
        (tr("wizard.dir.desktop"), str(home / "Desktop")),
    ]


def _set_page_error(label: QLabel, message: str) -> None:
    if message:
        label.setText(message)
        label.setVisible(True)
    else:
        label.clear()
        label.setVisible(False)


class _WizardStepPage(QWizardPage):
    step_key: str = ""

    def _apply_step_subtitle(self, wizard: SetupWizard, step_number: int) -> None:
        self.setSubTitle(_format_step_subtitle(self.step_key, step_number))

    def _retranslate_ui(self) -> None:
        pass


class WelcomePage(_WizardStepPage):
    step_key = "welcome"

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self._wizard = wizard
        layout = QVBoxLayout(self)
        self._body_label = QLabel()
        self._body_label.setWordWrap(True)
        layout.addWidget(self._body_label)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setTitle(tr("wizard.welcome.title"))
        self._body_label.setText(tr("wizard.welcome.body"))
        self._apply_step_subtitle(self._wizard, _wizard_step_number(self.step_key))

    def initializePage(self) -> None:
        self._apply_step_subtitle(self._wizard, _wizard_step_number(self.step_key))
        self._retranslate_ui()


class LanguagePage(_WizardStepPage):
    step_key = "language"

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self._wizard = wizard
        layout = QVBoxLayout(self)

        self._body_label = QLabel()
        self._body_label.setWordWrap(True)
        layout.addWidget(self._body_label)

        row = QHBoxLayout()
        self._language_caption = QLabel()
        row.addWidget(self._language_caption)
        self.language_combo = QComboBox()
        self.language_combo.addItem("", "system")
        self.language_combo.addItem("", "ru")
        self.language_combo.addItem("", "en")
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        row.addWidget(self.language_combo, stretch=1)
        layout.addLayout(row)

        self.dark_theme_check = QCheckBox()
        self.dark_theme_check.toggled.connect(self._on_theme_changed)
        layout.addWidget(self.dark_theme_check)

        self._hint_label = QLabel()
        self._hint_label.setObjectName("muted")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setTitle(tr("wizard.language.title"))
        self._body_label.setText(tr("wizard.language.body"))
        self._language_caption.setText(tr("main.settings.language"))
        self.dark_theme_check.setText(tr("main.theme.dark"))
        current = self.language_combo.currentData()
        self.language_combo.blockSignals(True)
        self.language_combo.setItemText(0, tr("main.settings.language.system"))
        self.language_combo.setItemText(1, tr("main.settings.language.ru"))
        self.language_combo.setItemText(2, tr("main.settings.language.en"))
        if current is not None:
            idx = self.language_combo.findData(current)
            if idx >= 0:
                self.language_combo.setCurrentIndex(idx)
        self.language_combo.blockSignals(False)
        self._hint_label.setText(tr("wizard.language.hint"))

    def initializePage(self) -> None:
        self._apply_step_subtitle(self._wizard, _wizard_step_number(self.step_key))
        lang = (self._wizard.ui_language or "system").strip().lower()
        idx = self.language_combo.findData(lang)
        if idx < 0:
            idx = self.language_combo.findData("system")
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)
        self.dark_theme_check.blockSignals(True)
        self.dark_theme_check.setChecked(self._wizard.dark_theme)
        self.dark_theme_check.blockSignals(False)
        self._retranslate_ui()

    def validatePage(self) -> bool:
        self._wizard.ui_language = str(self.language_combo.currentData() or "system")
        self._wizard.dark_theme = self.dark_theme_check.isChecked()
        set_locale(self._wizard.ui_language)
        if not self._wizard.sync_appearance_to_main_window():
            return False
        self._wizard._retranslate_ui()
        return True

    def _on_language_changed(self, index: int) -> None:
        if index < 0:
            return
        lang = self.language_combo.itemData(index)
        if lang is None:
            return
        self._wizard.ui_language = str(lang)
        set_locale(self._wizard.ui_language)
        self._wizard.sync_appearance_to_main_window()
        self._wizard._retranslate_ui()

    def _on_theme_changed(self, checked: bool) -> None:
        self._wizard.dark_theme = checked
        self._wizard.sync_appearance_to_main_window()


class ApiCredentialsPage(_WizardStepPage):
    step_key = "api"

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self._wizard = wizard
        layout = QVBoxLayout(self)

        dark = bool(getattr(wizard.main_window, "_dark_theme", False))
        accent = palette_for(dark=dark)["accent"]
        self._link_label = QLabel()
        self._link_label.setWordWrap(True)
        self._link_label.setTextFormat(Qt.TextFormat.RichText)
        self._link_label.setOpenExternalLinks(True)
        style_link_label(self._link_label, dark=dark)
        layout.addWidget(self._link_label)

        self._hint_label = QLabel()
        self._hint_label.setObjectName("muted")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        id_row = QHBoxLayout()
        self._api_id_caption = QLabel()
        id_row.addWidget(self._api_id_caption)
        self.api_id_entry = QLineEdit()
        id_row.addWidget(self.api_id_entry, stretch=1)
        layout.addLayout(id_row)

        hash_row = QHBoxLayout()
        self._api_hash_caption = QLabel()
        hash_row.addWidget(self._api_hash_caption)
        self.api_hash_entry = QLineEdit()
        hash_row.addWidget(self.api_hash_entry, stretch=1)
        layout.addLayout(hash_row)

        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.api_id_entry.textChanged.connect(self._on_field_changed)
        self.api_hash_entry.textChanged.connect(self._on_field_changed)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        dark = bool(getattr(self._wizard.main_window, "_dark_theme", False))
        accent = palette_for(dark=dark)["accent"]
        self.setTitle(tr("wizard.api.title"))
        link_html = tr("wizard.api.link").replace(
            "my.telegram.org",
            f'<a href="{TELEGRAM_APPS_URL}" '
            f'style="color: {accent}; text-decoration: underline;">my.telegram.org</a>',
        )
        self._link_label.setText(link_html)
        self._hint_label.setText(tr("wizard.api.hint"))
        self._api_id_caption.setText(tr("main.settings.api_id"))
        self._api_hash_caption.setText(tr("main.settings.api_hash"))

    def _on_field_changed(self) -> None:
        _set_page_error(self.error_label, "")
        self.completeChanged.emit()

    def initializePage(self) -> None:
        self._apply_step_subtitle(self._wizard, _wizard_step_number(self.step_key))
        self.api_id_entry.setText(self._wizard.api_id)
        self.api_hash_entry.setText(self._wizard.api_hash)
        _set_page_error(self.error_label, "")
        self._retranslate_ui()

    def isComplete(self) -> bool:
        return bool(self.api_id_entry.text().strip() and self.api_hash_entry.text().strip())

    def validatePage(self) -> bool:
        api_id = self.api_id_entry.text().strip()
        api_hash = self.api_hash_entry.text().strip()
        if not api_id:
            _set_page_error(self.error_label, tr("wizard.api.error.id_required"))
            return False
        if not api_hash:
            _set_page_error(self.error_label, tr("wizard.api.error.hash_required"))
            return False
        try:
            parsed_id = int(api_id)
        except ValueError:
            _set_page_error(self.error_label, tr("wizard.api.error.id_not_number"))
            return False
        if parsed_id <= 0:
            _set_page_error(self.error_label, tr("wizard.api.error.id_positive"))
            return False
        if len(api_hash) < 20:
            _set_page_error(
                self.error_label,
                tr("wizard.api.error.hash_short"),
            )
            return False
        _set_page_error(self.error_label, "")
        self._wizard.api_id = api_id
        self._wizard.api_hash = api_hash
        try:
            if not self._wizard.sync_api_to_main_window():
                _set_page_error(self.error_label, tr("wizard.api.error.save_failed"))
                return False
        except Exception as exc:
            _set_page_error(self.error_label, tr("wizard.api.error.save", exc=exc))
            return False
        return True


class ProxyPage(_WizardStepPage):
    step_key = "proxy"

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self._wizard = wizard
        self._proxy_test_thread: threading.Thread | None = None
        layout = QVBoxLayout(self)

        self._hint_label = QLabel()
        self._hint_label.setObjectName("muted")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        self.proxy_enabled_check = QCheckBox()
        self.proxy_enabled_check.toggled.connect(self._on_proxy_toggled)
        layout.addWidget(self.proxy_enabled_check)

        type_row = QHBoxLayout()
        self._type_caption = QLabel()
        type_row.addWidget(self._type_caption)
        self.proxy_type_combo = QComboBox()
        self.proxy_type_combo.addItems(["socks5", "http"])
        type_row.addWidget(self.proxy_type_combo, stretch=1)
        layout.addLayout(type_row)

        host_row = QHBoxLayout()
        self._host_caption = QLabel()
        host_row.addWidget(self._host_caption)
        self.proxy_host_entry = QLineEdit()
        host_row.addWidget(self.proxy_host_entry, stretch=1)
        layout.addLayout(host_row)

        port_row = QHBoxLayout()
        self._port_caption = QLabel()
        port_row.addWidget(self._port_caption)
        self.proxy_port_entry = QLineEdit()
        port_row.addWidget(self.proxy_port_entry, stretch=1)
        layout.addLayout(port_row)

        test_row = QHBoxLayout()
        self.test_proxy_btn = QPushButton()
        self.test_proxy_btn.clicked.connect(self._test_proxy)
        test_row.addWidget(self.test_proxy_btn)
        test_row.addStretch()
        layout.addLayout(test_row)

        self.proxy_status_label = QLabel()
        self.proxy_status_label.setWordWrap(True)
        self.proxy_status_label.setVisible(False)
        layout.addWidget(self.proxy_status_label)

        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        for entry in (self.proxy_host_entry, self.proxy_port_entry):
            entry.textChanged.connect(self._on_field_changed)
        self.proxy_type_combo.currentTextChanged.connect(self._on_field_changed)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setTitle(tr("wizard.proxy.title"))
        self._hint_label.setText(tr("wizard.proxy.body"))
        self.proxy_enabled_check.setText(tr("wizard.proxy.enable"))
        self._type_caption.setText(tr("wizard.proxy.type"))
        self._host_caption.setText(tr("wizard.proxy.host"))
        self._port_caption.setText(tr("wizard.proxy.port"))
        self.test_proxy_btn.setText(tr("wizard.proxy.test"))

    def _set_proxy_fields_enabled(self, enabled: bool) -> None:
        self.proxy_type_combo.setEnabled(enabled)
        self.proxy_host_entry.setEnabled(enabled)
        self.proxy_port_entry.setEnabled(enabled)
        self.test_proxy_btn.setEnabled(enabled)

    def _on_proxy_toggled(self, enabled: bool) -> None:
        self._set_proxy_fields_enabled(enabled)
        self._update_next_caption()
        _set_page_error(self.error_label, "")
        self.proxy_status_label.setVisible(False)
        self.completeChanged.emit()

    def _on_field_changed(self) -> None:
        _set_page_error(self.error_label, "")
        self.proxy_status_label.setVisible(False)
        self.completeChanged.emit()

    def _update_next_caption(self) -> None:
        wiz = self.wizard()
        if isinstance(wiz, SetupWizard):
            wiz._update_next_button_caption()

    def initializePage(self) -> None:
        self._apply_step_subtitle(self._wizard, _wizard_step_number(self.step_key))
        self.proxy_enabled_check.setChecked(self._wizard.proxy_enabled)
        proxy_type = (self._wizard.proxy_type or "socks5").strip().lower()
        index = self.proxy_type_combo.findText(proxy_type)
        self.proxy_type_combo.setCurrentIndex(index if index >= 0 else 0)
        self.proxy_host_entry.setText(self._wizard.proxy_host)
        self.proxy_port_entry.setText(str(self._wizard.proxy_port))
        self._set_proxy_fields_enabled(self.proxy_enabled_check.isChecked())
        _set_page_error(self.error_label, "")
        self.proxy_status_label.setVisible(False)
        self._update_next_caption()
        self._retranslate_ui()

    def isComplete(self) -> bool:
        return True

    def validatePage(self) -> bool:
        enabled = self.proxy_enabled_check.isChecked()
        self._wizard.proxy_enabled = enabled
        if not enabled:
            _set_page_error(self.error_label, "")
            self._wizard.sync_to_main_window()
            return True

        proxy_type = self.proxy_type_combo.currentText().strip().lower()
        proxy_host = self.proxy_host_entry.text().strip()
        proxy_port_raw = self.proxy_port_entry.text().strip()

        if not proxy_host:
            _set_page_error(self.error_label, tr("wizard.proxy.error.host"))
            return False
        if not proxy_port_raw:
            _set_page_error(self.error_label, tr("wizard.proxy.error.port"))
            return False
        try:
            proxy_port = int(proxy_port_raw)
        except ValueError:
            _set_page_error(self.error_label, tr("wizard.proxy.error.port_number"))
            return False
        if not 1 <= proxy_port <= 65535:
            _set_page_error(self.error_label, tr("wizard.proxy.error.port_range"))
            return False

        _set_page_error(self.error_label, "")
        self._wizard.proxy_type = proxy_type
        self._wizard.proxy_host = proxy_host
        self._wizard.proxy_port = proxy_port
        self._wizard.sync_to_main_window()
        return True

    def _test_proxy(self) -> None:
        if self._proxy_test_thread is not None and self._proxy_test_thread.is_alive():
            return
        if not self._wizard.api_id.strip() or not self._wizard.api_hash.strip():
            self.proxy_status_label.setText(tr("wizard.proxy.status.need_api"))
            self.proxy_status_label.setObjectName("error")
            self.proxy_status_label.setVisible(True)
            return

        enabled = self.proxy_enabled_check.isChecked()
        proxy_type = self.proxy_type_combo.currentText().strip().lower()
        proxy_host = self.proxy_host_entry.text().strip()
        proxy_port_raw = self.proxy_port_entry.text().strip()
        if enabled and (not proxy_host or not proxy_port_raw):
            self.proxy_status_label.setText(tr("wizard.proxy.status.need_host_port"))
            self.proxy_status_label.setObjectName("error")
            self.proxy_status_label.setVisible(True)
            return

        try:
            proxy_port = int(proxy_port_raw) if proxy_port_raw else self._wizard.proxy_port
        except ValueError:
            self.proxy_status_label.setText(tr("wizard.proxy.error.port_number"))
            self.proxy_status_label.setObjectName("error")
            self.proxy_status_label.setVisible(True)
            return

        probe_settings = SettingsData(
            api_id=self._wizard.api_id,
            api_hash=self._wizard.api_hash,
            proxy_enabled=enabled,
            proxy_type=proxy_type,
            proxy_host=proxy_host or self._wizard.proxy_host,
            proxy_port=proxy_port,
        )

        self.test_proxy_btn.setEnabled(False)
        self.proxy_status_label.setText(tr("wizard.proxy.status.testing"))
        self.proxy_status_label.setObjectName("")
        self.proxy_status_label.setVisible(True)

        def worker() -> None:
            result = test_telegram_connectivity(
                int(self._wizard.api_id),
                self._wizard.api_hash,
                probe_settings,
            )
            QTimer.singleShot(0, lambda: self._finish_proxy_test(result))

        self._proxy_test_thread = threading.Thread(target=worker, daemon=True)
        self._proxy_test_thread.start()

    def _finish_proxy_test(self, result: AuthResult) -> None:
        self.test_proxy_btn.setEnabled(self.proxy_enabled_check.isChecked())
        if result.ok:
            label = tr("wizard.proxy.status.ok")
            if self.proxy_enabled_check.isChecked():
                label = tr("wizard.proxy.status.ok_proxy")
            self.proxy_status_label.setText(label)
            self.proxy_status_label.setObjectName("accent")
        else:
            self.proxy_status_label.setText(result.error or tr("wizard.proxy.status.fail"))
            self.proxy_status_label.setObjectName("error")
        self.proxy_status_label.setVisible(True)


class TelegramLoginPage(_WizardStepPage):
    step_key = "login"

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self._wizard = wizard
        self._logged_in = False
        self._auth_poll_timer: QTimer | None = None
        self._login_poll_timer: QTimer | None = None
        layout = QVBoxLayout(self)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.qr_login_btn = QPushButton()
        self.qr_login_btn.setDefault(True)
        self.qr_login_btn.clicked.connect(self._start_qr_login)
        self.login_btn = QPushButton()
        self.login_btn.clicked.connect(self._start_phone_login)
        btn_row.addWidget(self.qr_login_btn)
        btn_row.addWidget(self.login_btn)
        self.cancel_login_btn = QPushButton()
        self.cancel_login_btn.clicked.connect(self._cancel_login)
        self.cancel_login_btn.setVisible(False)
        btn_row.addWidget(self.cancel_login_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._hint_label = QLabel()
        self._hint_label.setObjectName("muted")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setTitle(tr("wizard.login.title"))
        self.qr_login_btn.setText(tr("wizard.login.qr"))
        self.login_btn.setText(tr("wizard.login.phone"))
        self.cancel_login_btn.setText(tr("wizard.login.cancel"))
        self._hint_label.setText(tr("wizard.login.hint"))
        if not self._logged_in and not self.login_btn.isEnabled():
            pass
        elif not self._logged_in and self.status_label.text() in (
            tr("wizard.login.need_auth"),
            "",
        ):
            self.status_label.setText(tr("wizard.login.need_auth"))

    def initializePage(self) -> None:
        self._apply_step_subtitle(self._wizard, _wizard_step_number(self.step_key))
        self._retranslate_ui()
        self._stop_auth_poll()
        self._stop_login_poll()
        self._sync_api_to_main_window()
        main = self._wizard.main_window
        if main.is_logged_in:
            self._logged_in = True
            self._set_status_logged_in(main.auth_username)
            self._set_login_buttons_enabled(True)
            self.cancel_login_btn.setVisible(False)
        elif main.login_in_progress():
            self._logged_in = False
            self._set_login_buttons_enabled(False)
            self.cancel_login_btn.setVisible(True)
            if not self.status_label.text().startswith("✓"):
                self.status_label.setText(tr("wizard.login.in_progress"))
                self.status_label.setObjectName("")
            self._start_login_poll()
        elif session_file_exists(self._wizard.session_name):
            self._logged_in = False
            self.status_label.setText(tr("wizard.login.checking_session"))
            self.status_label.setObjectName("")
            self._set_login_buttons_enabled(True)
            self.cancel_login_btn.setVisible(False)
            main._refresh_auth_status()
            self._start_auth_poll()
        else:
            self._logged_in = False
            self.status_label.setText(tr("wizard.login.need_auth"))
            self.status_label.setObjectName("")
            self._set_login_buttons_enabled(True)
            self.cancel_login_btn.setVisible(False)
        self._update_next_button()
        self.completeChanged.emit()

    def cleanupPage(self) -> None:
        self._stop_auth_poll()
        self._stop_login_poll()
        from qt_ui.main_window import HashtagDownloaderWindow

        main = self._wizard.main_window
        if isinstance(main, HashtagDownloaderWindow) and main.login_in_progress():
            main.cancel_login()

    def _start_auth_poll(self) -> None:
        self._auth_poll_timer = QTimer(self)
        self._auth_poll_timer.timeout.connect(self._poll_auth_status)
        self._auth_poll_timer.start(300)

    def _stop_auth_poll(self) -> None:
        if self._auth_poll_timer is not None:
            self._auth_poll_timer.stop()
            self._auth_poll_timer.deleteLater()
            self._auth_poll_timer = None

    def _poll_auth_status(self) -> None:
        main = self._wizard.main_window
        if main.is_logged_in:
            self._logged_in = True
            self._set_status_logged_in(main.auth_username)
            self._update_next_button()
            self.completeChanged.emit()
            self._stop_auth_poll()
            return
        status = main.auth_status_label.text()
        if status and not status.endswith("…"):
            self._set_status_error(status)
            self._stop_auth_poll()

    def isComplete(self) -> bool:
        return self._logged_in

    def _sync_api_to_main_window(self) -> None:
        self._wizard.sync_to_main_window()

    def _set_login_buttons_enabled(self, enabled: bool) -> None:
        self.login_btn.setEnabled(enabled)
        self.qr_login_btn.setEnabled(enabled)

    def _set_status_logged_in(self, username: str) -> None:
        self.status_label.setText(tr("wizard.login.success", user=username))
        self.status_label.setObjectName("accent")

    def _set_status_error(self, message: str) -> None:
        self.status_label.setText(message or tr("wizard.login.failed"))
        self.status_label.setObjectName("error")

    def _on_login_finished(self, result: AuthResult) -> None:
        self._stop_login_poll()
        self.cancel_login_btn.setVisible(False)
        self._set_login_buttons_enabled(True)
        if result.ok:
            self._logged_in = True
            self._set_status_logged_in(result.username or self._wizard.main_window.auth_username)
        else:
            self._logged_in = False
            self._set_status_error(result.error or tr("wizard.login.failed"))
        self._update_next_button()
        self.completeChanged.emit()

    def _start_login_poll(self) -> None:
        self._login_poll_timer = QTimer(self)
        self._login_poll_timer.timeout.connect(self._poll_login_thread)
        self._login_poll_timer.start(300)

    def _stop_login_poll(self) -> None:
        if self._login_poll_timer is not None:
            self._login_poll_timer.stop()
            self._login_poll_timer.deleteLater()
            self._login_poll_timer = None

    def _poll_login_thread(self) -> None:
        main = self._wizard.main_window
        if main.login_in_progress():
            return
        self._stop_login_poll()
        if main.is_logged_in:
            self._on_login_finished(
                AuthResult(ok=True, username=main.auth_username),
            )
            return
        self._set_login_buttons_enabled(True)
        self.cancel_login_btn.setVisible(False)
        self._set_status_error(tr("wizard.login.incomplete"))

    def _cancel_login(self) -> None:
        from qt_ui.main_window import HashtagDownloaderWindow

        main = self._wizard.main_window
        if isinstance(main, HashtagDownloaderWindow):
            main.cancel_login()
        self._stop_login_poll()
        self.cancel_login_btn.setVisible(False)
        if main.login_in_progress():
            self._set_login_buttons_enabled(False)
            self.status_label.setText(tr("wizard.login.finishing"))
            self.status_label.setObjectName("")
            self._start_login_poll()
            return
        self._set_login_buttons_enabled(True)
        self.status_label.setText(tr("wizard.login.cancelled"))
        self.status_label.setObjectName("")

    def _update_next_button(self) -> None:
        wiz = self.wizard()
        if wiz is not None:
            wiz.button(QWizard.WizardButton.NextButton).setEnabled(self._logged_in)

    def _prompt_parent(self) -> QWidget:
        wiz = self.wizard()
        if wiz is not None:
            return wiz
        return self._wizard.main_window

    def _begin_login(self, *, qr: bool) -> None:
        self._sync_api_to_main_window()
        self._set_login_buttons_enabled(False)
        self.cancel_login_btn.setVisible(True)
        self.status_label.setText(
            tr("wizard.login.wait_qr") if qr else tr("wizard.login.wait"),
        )
        self.status_label.setObjectName("")
        self._start_login_poll()
        parent = self._prompt_parent()
        starter = (
            self._wizard.main_window._start_qr_login
            if qr
            else self._wizard.main_window._start_login
        )
        starter(
            quiet=True,
            on_finished=self._on_login_finished,
            prompt_parent=parent,
        )

    def _start_phone_login(self) -> None:
        self._begin_login(qr=False)

    def _start_qr_login(self) -> None:
        self._begin_login(qr=True)


class DownloadDirPage(_WizardStepPage):
    step_key = "download"

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self._wizard = wizard
        self._quick_btns: list[QPushButton] = []
        layout = QVBoxLayout(self)

        quick_row = QHBoxLayout()
        self._quick_label = QLabel()
        quick_row.addWidget(self._quick_label)
        for title, path in _download_dir_quick_paths():
            btn = QPushButton(title)
            btn.clicked.connect(lambda _checked=False, p=path: self.download_dir_entry.setText(p))
            self._quick_btns.append(btn)
            quick_row.addWidget(btn)
        quick_row.addStretch()
        layout.addLayout(quick_row)

        dir_row = QHBoxLayout()
        self._dir_caption = QLabel()
        dir_row.addWidget(self._dir_caption)
        self.download_dir_entry = QLineEdit()
        self._browse_btn = QPushButton()
        self._browse_btn.clicked.connect(self._browse_download_dir)
        dir_row.addWidget(self.download_dir_entry, stretch=1)
        dir_row.addWidget(self._browse_btn)
        layout.addLayout(dir_row)

        self._hint_label = QLabel()
        self._hint_label.setObjectName("muted")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.download_dir_entry.textChanged.connect(self._on_field_changed)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setTitle(tr("wizard.download.title"))
        self._quick_label.setText(tr("wizard.download.quick"))
        for btn, (title, _path) in zip(self._quick_btns, _download_dir_quick_paths(), strict=False):
            btn.setText(title)
        self._dir_caption.setText(tr("main.settings.download_dir"))
        self._browse_btn.setText(tr("main.settings.browse"))
        self._hint_label.setText(tr("wizard.download.hint"))

    def _on_field_changed(self) -> None:
        _set_page_error(self.error_label, "")
        self.completeChanged.emit()

    def initializePage(self) -> None:
        self._apply_step_subtitle(self._wizard, _wizard_step_number(self.step_key))
        self.download_dir_entry.setText(self._wizard.download_dir)
        _set_page_error(self.error_label, "")
        self._retranslate_ui()

    def isComplete(self) -> bool:
        return bool(self.download_dir_entry.text().strip())

    def validatePage(self) -> bool:
        value = self.download_dir_entry.text().strip() or "data/downloads"
        error = validate_download_dir(value)
        if error:
            _set_page_error(self.error_label, error)
            return False
        _set_page_error(self.error_label, "")
        self._wizard.download_dir = value
        return True

    def _browse_download_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self.wizard() or self,
            tr("wizard.download.browse_title"),
            str(resolve_download_dir(self.download_dir_entry.text())),
        )
        if selected:
            self.download_dir_entry.setText(selected)


class FinishPage(_WizardStepPage):
    step_key = "finish"

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self._wizard = wizard
        self._autotune_queue: queue.Queue[tuple] = queue.Queue()
        self._autotune_thread: threading.Thread | None = None
        self._autotune_running = False
        layout = QVBoxLayout(self)

        self.checklist_label = QLabel()
        self.checklist_label.setWordWrap(True)
        layout.addWidget(self.checklist_label)

        self._body_label = QLabel()
        self._body_label.setWordWrap(True)
        layout.addWidget(self._body_label)

        self.win_notify_check = QCheckBox()
        self.win_notify_check.setVisible(notifications_available())
        layout.addWidget(self.win_notify_check)
        self._autotune_intro_label = QLabel()
        self._autotune_intro_label.setWordWrap(True)
        layout.addWidget(self._autotune_intro_label)
        autotune_row = QHBoxLayout()
        self._run_perf_btn = QPushButton()
        self._run_perf_btn.clicked.connect(self._start_autotune)
        autotune_row.addWidget(self._run_perf_btn)
        self._apply_perf_btn = QPushButton()
        self._apply_perf_btn.clicked.connect(self._apply_autotune)
        autotune_row.addWidget(self._apply_perf_btn)
        autotune_row.addStretch()
        layout.addLayout(autotune_row)
        self._autotune_summary_label = QLabel()
        self._autotune_summary_label.setObjectName("muted")
        self._autotune_summary_label.setWordWrap(True)
        layout.addWidget(self._autotune_summary_label)
        self._autotune_timer = QTimer(self)
        self._autotune_timer.timeout.connect(self._poll_autotune_queue)
        self._autotune_timer.start(150)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setTitle(tr("wizard.finish.title"))
        self._body_label.setText(tr("wizard.finish.body"))
        self.win_notify_check.setText(tr("wizard.finish.notify"))
        self._autotune_intro_label.setText(tr("wizard.finish.autotune"))
        self._run_perf_btn.setText(tr("autotune.button.run"))
        self._apply_perf_btn.setText(tr("autotune.button.apply"))
        self._refresh_autotune_summary()

    def initializePage(self) -> None:
        self._apply_step_subtitle(self._wizard, _wizard_step_number(self.step_key))
        self._retranslate_ui()
        wizard = self._wizard
        main = wizard.main_window
        logged_in = bool(getattr(main, "is_logged_in", False)) or session_file_exists(
            wizard.session_name,
        )
        proxy_line = (
            tr("wizard.finish.proxy_on", type=wizard.proxy_type, host=wizard.proxy_host, port=wizard.proxy_port)
            if wizard.proxy_enabled
            else tr("wizard.finish.proxy_off")
        )
        download_ok = validate_download_dir(wizard.download_dir) is None
        lines = [
            tr("wizard.finish.api_ok") if api_is_configured(wizard._as_settings()) else tr("wizard.finish.api_fail"),
            proxy_line,
            tr("wizard.finish.login_ok") if logged_in else tr("wizard.finish.login_fail"),
            tr("wizard.finish.dir_ok", path=wizard.download_dir)
            if download_ok
            else tr("wizard.finish.dir_fail", path=wizard.download_dir),
        ]
        self.checklist_label.setText("\n".join(lines))
        if self.win_notify_check.isVisible():
            self.win_notify_check.setChecked(wizard.win_notify_enabled)
        self._refresh_autotune_summary()

    def _refresh_autotune_summary(self) -> None:
        profile = getattr(self._wizard.main_window, "_autotune_profile", None)
        self._autotune_summary_label.setText(
            format_autotune_summary(profile, current=self._wizard._as_settings()),
        )
        can_apply = profile is not None and not profile_matches_settings(profile, self._wizard._as_settings())
        self._run_perf_btn.setEnabled(not self._autotune_running)
        self._apply_perf_btn.setVisible(profile is not None)
        self._apply_perf_btn.setEnabled(can_apply and not self._autotune_running)

    def _start_autotune(self) -> None:
        if self._autotune_running:
            return
        self._autotune_running = True
        self._autotune_summary_label.setText(tr("autotune.progress.starting"))
        self._refresh_autotune_summary()

        def worker() -> None:
            try:
                profile = run_autotune_sync(self._wizard._as_settings())
                self._autotune_queue.put(("done", profile, None))
            except Exception as exc:
                self._autotune_queue.put(("done", None, str(exc)))

        self._autotune_thread = threading.Thread(target=worker, daemon=True)
        self._autotune_thread.start()

    def _poll_autotune_queue(self) -> None:
        while True:
            try:
                kind, profile, error = self._autotune_queue.get_nowait()
            except queue.Empty:
                break
            if kind != "done":
                continue
            self._autotune_running = False
            if error:
                self._autotune_summary_label.setText(tr("autotune.progress.failed"))
                show_error(self, tr("autotune.dialog.title"), str(error))
                self._refresh_autotune_summary()
                continue
            setattr(self._wizard.main_window, "_autotune_profile", profile)
            self._refresh_autotune_summary()

    def _apply_autotune(self) -> None:
        main = self._wizard.main_window
        if not hasattr(main, "_apply_autotune_recommendations"):
            return
        main._apply_autotune_recommendations()
        self._refresh_autotune_summary()


class SetupWizard(QWizard):
    def __init__(
        self,
        main_window: QWidget,
        *,
        settings: SettingsData | None = None,
        first_run: bool = False,
        skip_welcome: bool = False,
        start_at_login: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.main_window = main_window
        self._first_run = first_run
        data = settings or load_settings(include_session=False)
        self.api_id = data.api_id
        self.api_hash = data.api_hash
        self.download_dir = data.download_dir
        self.session_name = data.session_name
        self.ui_language = data.ui_language
        self.dark_theme = data.dark_theme
        self.proxy_enabled = data.proxy_enabled
        self.proxy_type = data.proxy_type
        self.proxy_host = data.proxy_host
        self.proxy_port = data.proxy_port
        self.win_notify_enabled = data.win_notify_enabled

        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.NoCancelButton, False)

        self._welcome_page = WelcomePage(self)
        self.language_page = LanguagePage(self)
        self._language_page_id = self.addPage(self.language_page)
        self._welcome_id = self.addPage(self._welcome_page)
        self._api_page_id = self.addPage(ApiCredentialsPage(self))
        self.proxy_page = ProxyPage(self)
        self._proxy_page_id = self.addPage(self.proxy_page)
        self.login_page = TelegramLoginPage(self)
        self._login_page_id = self.addPage(self.login_page)
        self._download_page_id = self.addPage(DownloadDirPage(self))
        self.finish_page = FinishPage(self)
        self._finish_page_id = self.addPage(self.finish_page)

        self._step_page_ids = {
            "welcome": self._welcome_id,
            "language": self._language_page_id,
            "api": self._api_page_id,
            "proxy": self._proxy_page_id,
            "login": self._login_page_id,
            "download": self._download_page_id,
            "finish": self._finish_page_id,
        }

        if start_at_login:
            self.setStartId(self._login_page_id)
        else:
            start_step = resolve_wizard_dialog_start_step(
                data,
                first_run=first_run,
                skip_welcome=skip_welcome,
            )
            if start_step != "welcome":
                self.setStartId(self._step_page_ids[start_step])

        self.currentIdChanged.connect(self._on_page_changed)
        self.setMinimumSize(520, 420)
        self._retranslate_ui()

    def _on_page_changed(self, _page_id: int) -> None:
        self._update_next_button_caption()
        page = self.currentPage()
        if isinstance(page, _WizardStepPage):
            page._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(tr("wizard.window.title"))
        self.setButtonText(QWizard.WizardButton.BackButton, tr("wizard.btn.back"))
        self.setButtonText(QWizard.WizardButton.FinishButton, tr("wizard.btn.finish"))
        self.setButtonText(QWizard.WizardButton.CancelButton, tr("wizard.btn.cancel"))
        self._update_next_button_caption()
        for page_id in self._step_page_ids.values():
            page = self.page(page_id)
            if isinstance(page, _WizardStepPage):
                page._retranslate_ui()

    def _as_settings(self) -> SettingsData:
        return SettingsData(
            api_id=self.api_id,
            api_hash=self.api_hash,
            download_dir=self.download_dir,
            session_name=self.session_name,
            ui_language=self.ui_language,
            dark_theme=self.dark_theme,
            proxy_enabled=self.proxy_enabled,
            proxy_type=self.proxy_type,
            proxy_host=self.proxy_host,
            proxy_port=self.proxy_port,
            win_notify_enabled=self.win_notify_enabled,
        )

    def _update_next_button_caption(self) -> None:
        on_proxy = self.currentPage() is self.proxy_page
        skip_proxy = on_proxy and not self.proxy_page.proxy_enabled_check.isChecked()
        self.setButtonText(
            QWizard.WizardButton.NextButton,
            tr("wizard.btn.skip") if skip_proxy else tr("wizard.btn.next"),
        )

    def reject(self) -> None:
        from qt_ui.main_window import HashtagDownloaderWindow

        main = self.main_window
        if (
            isinstance(main, HashtagDownloaderWindow)
            and main.login_in_progress()
            and not ask_yes_no(
                self,
                tr("wizard.close.title"),
                tr("wizard.close.login_body"),
            )
        ):
            return
        if isinstance(main, HashtagDownloaderWindow) and main.login_in_progress():
            main.cancel_login()
        super().reject()
        if self._first_run:
            show_info(
                self.main_window,
                tr("wizard.closed.title"),
                tr("wizard.closed.body"),
            )

    def _apply_wizard_fields_to_main(self, main) -> None:
        main.api_id_entry.setText(self.api_id)
        main.api_hash_entry.setText(self.api_hash)
        main.session_name_entry.setText(self.session_name)
        language_combo = getattr(main, "language_combo", None)
        if language_combo is not None:
            lang_idx = language_combo.findData(self.ui_language or "system")
            if lang_idx >= 0:
                language_combo.setCurrentIndex(lang_idx)
        theme_check = getattr(main, "theme_check", None)
        if theme_check is not None:
            theme_check.setChecked(self.dark_theme)
        main.proxy_enabled_check.setChecked(self.proxy_enabled)
        main.proxy_type_entry.setText(self.proxy_type)
        main.proxy_host_entry.setText(self.proxy_host)
        main.proxy_port_spin.setValue(max(1, min(int(self.proxy_port), 65535)))

    def sync_api_to_main_window(self) -> bool:
        """Sync API keys · Сохранение API-ключей из мастера"""
        from qt_ui.main_window import HashtagDownloaderWindow

        main = self.main_window
        if not isinstance(main, HashtagDownloaderWindow):
            return False

        main.api_id_entry.setText(self.api_id)
        main.api_hash_entry.setText(self.api_hash)
        return main._persist_settings()

    def sync_language_to_main_window(self) -> bool:
        return self.sync_appearance_to_main_window()

    def sync_appearance_to_main_window(self) -> bool:
        from qt_ui.main_window import HashtagDownloaderWindow

        main = self.main_window
        if not isinstance(main, HashtagDownloaderWindow):
            return False

        language_combo = getattr(main, "language_combo", None)
        if language_combo is not None:
            lang_idx = language_combo.findData(self.ui_language or "system")
            if lang_idx >= 0:
                language_combo.setCurrentIndex(lang_idx)

        if main._dark_theme != self.dark_theme:
            main._on_theme_toggle(self.dark_theme)
        elif not main._persist_settings():
            return False

        apply_window_theme(self, main)
        return True

    def sync_to_main_window(self) -> bool:
        from qt_ui.main_window import HashtagDownloaderWindow

        main = self.main_window
        if not isinstance(main, HashtagDownloaderWindow):
            return False

        self._apply_wizard_fields_to_main(main)
        return main._persist_settings()

    def accept(self) -> None:
        if self.finish_page.win_notify_check.isVisible():
            self.win_notify_enabled = self.finish_page.win_notify_check.isChecked()
        self._apply_to_main_window()
        super().accept()

    def _apply_to_main_window(self) -> None:
        from qt_ui.main_window import HashtagDownloaderWindow

        main = self.main_window
        if not isinstance(main, HashtagDownloaderWindow):
            return

        settings = main._collect_settings()
        settings.api_id = self.api_id
        settings.api_hash = self.api_hash
        settings.download_dir = self.download_dir
        settings.session_name = self.session_name
        settings.ui_language = self.ui_language
        settings.dark_theme = self.dark_theme
        settings.proxy_enabled = self.proxy_enabled
        settings.proxy_type = self.proxy_type
        settings.proxy_host = self.proxy_host
        settings.proxy_port = self.proxy_port
        settings.win_notify_enabled = self.win_notify_enabled
        settings.setup_wizard_completed = True
        save_settings(settings)
        main.settings = settings

        self._apply_wizard_fields_to_main(main)
        main.download_dir_entry.setText(self.download_dir)
        if hasattr(main, "win_notify_enabled_check"):
            main.win_notify_enabled_check.setChecked(self.win_notify_enabled)
        main._refresh_auth_status()

        main.tabs.setCurrentIndex(0)
        main.hashtag_entry.setFocus()


def run_setup_wizard(
    main_window: QWidget,
    *,
    settings: SettingsData | None = None,
    first_run: bool = False,
    skip_welcome: bool = False,
    start_at_login: bool = False,
) -> bool:
    """Run setup wizard · Модальный мастер; True если завершён"""
    ensure_env_file()
    wizard = SetupWizard(
        main_window,
        settings=settings,
        first_run=first_run,
        skip_welcome=skip_welcome,
        start_at_login=start_at_login,
        parent=main_window,
    )
    apply_window_theme(wizard, main_window)
    present_top_level_window(wizard, main_window)
    return wizard.exec() == QWizard.DialogCode.Accepted
