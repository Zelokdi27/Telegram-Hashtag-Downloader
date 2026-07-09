"""Qt GUI entry · Точка входа Qt GUI"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.config_store import load_settings
from app.i18n import set_locale
from app.setup_state import (
    ensure_env_file,
    session_login_should_be_verified,
    wizard_required,
)
from app.version import APP_NAME, __version__
from app.win_asyncio import fix_windows_asyncio
from qt_ui.main_window import HashtagDownloaderWindow
from qt_ui.setup_wizard import run_setup_wizard
from qt_ui.win_chrome import configure_windows_app_dark_mode, set_titlebar_dark


def run_gui() -> None:
    fix_windows_asyncio()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
    )
    ensure_env_file()
    settings = load_settings(include_session=False)
    set_locale(settings.ui_language)
    dark = settings.dark_theme
    if dark:
        # Win10 dark before QApp · Тёмная шапка до QApplication
        configure_windows_app_dark_mode(dark=True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    set_titlebar_dark(dark)

    settings = load_settings(include_session=False)
    window = HashtagDownloaderWindow(startup_session_check=True)

    wizard_completed = False
    if wizard_required(settings):
        wizard_completed = run_setup_wizard(window, settings=settings, first_run=True)

    window.show()

    settings = load_settings(include_session=False)
    if wizard_completed:
        window._refresh_auth_status()
    elif session_login_should_be_verified(settings):
        window.run_startup_session_check()
    else:
        window._refresh_auth_status()

    sys.exit(app.exec())