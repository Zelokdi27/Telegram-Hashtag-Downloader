"""Auth panel · Панель авторизации"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Literal

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from app.i18n import tr
from app.setup_state import auth_result_needs_login_prompt

from app.config_store import STATE_DIR, SettingsData
from app.telegram_auth import (
    AuthResult,
    check_session,
    disconnect_client_sync,
    login_interactive,
    login_qr_interactive,
    reset_session_files,
)
from app.tg_hashtag_dl import reset_all_download_states

from .dialogs import MainThreadPrompter, ask_yes_no, show_error, show_info, show_warning
from .login_prompt import show_session_login_prompt


AuthStatusMode = Literal["checking", "api_missing", "reset_done", "result"]


class AuthPanelMixin:

    _auth_status_mode: AuthStatusMode
    _last_auth_result: AuthResult | None

    def _set_startup_session_check(self, enabled: bool) -> None:
        self._startup_session_check = enabled

    def run_startup_session_check(self) -> None:
        QTimer.singleShot(0, self._run_startup_session_check)

    def _run_startup_session_check(self) -> None:
        settings = self._collect_settings()
        if not settings.api_id.strip() or not settings.api_hash.strip():
            self._auth_status_mode = "api_missing"
            self._last_auth_result = None
            self.auth_status_label.setText(tr("auth.error.api_missing"))
            self.auth_status_label.setObjectName("error")
            self._apply_theme()
            return
        if not self._persist_settings():
            return

        settings = self.settings
        self._auth_status_mode = "checking"
        self.auth_status_label.setText(tr("auth.status.checking"))
        self.auth_status_label.setObjectName("")

        def worker() -> None:
            result = check_session(
                self._session_path(settings),
                int(settings.api_id),
                settings.api_hash,
                settings,
            )
            self._invoker.run(lambda: self._finish_startup_session_check(result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_startup_session_check(self, result: AuthResult) -> None:
        self._set_auth_status(result)
        if not auth_result_needs_login_prompt(result):
            return
        from .setup_wizard import run_setup_wizard

        if run_setup_wizard(
            self,
            settings=self.settings,
            skip_welcome=True,
            start_at_login=True,
        ):
            self._refresh_auth_status()
            return
        action = show_session_login_prompt(
            self,
            message=result.error or tr("auth.error.not_logged_in"),
        )
        if action == "phone":
            self._start_login()
        elif action == "qr":
            self._start_qr_login()

    def _set_auth_status(self, result: AuthResult) -> None:
        self._auth_status_mode = "result"
        self._last_auth_result = result
        if result.ok:
            self.auth_username = result.username
            self.is_logged_in = True
            self.auth_status_label.setText(tr("auth.status.logged_in", user=result.username))
            self.auth_status_label.setObjectName("accent")
            self.auth_hint_label.setText(tr("auth.hint.done"))
        else:
            self.auth_username = ""
            self.is_logged_in = False
            self.auth_status_label.setText(result.error or tr("auth.status.unauthorized"))
            self.auth_status_label.setObjectName("error")
            if result.needs_phone:
                self.auth_hint_label.setText(tr("auth.hint.connected_not_logged"))
            elif result.connection_failed:
                self.auth_hint_label.setText(tr("auth.hint.no_connection"))
            else:
                self.auth_hint_label.setText(tr("auth.hint.need_login"))
        self._apply_theme()
        self._update_download_buttons()

    def _retranslate_auth_status(self) -> None:
        mode = getattr(self, "_auth_status_mode", "checking")
        if mode == "api_missing":
            settings = self._collect_settings()
            if not settings.api_id.strip() or not settings.api_hash.strip():
                self.auth_status_label.setText(tr("auth.error.api_missing"))
                self.auth_status_label.setObjectName("error")
                self.auth_hint_label.setText(tr("main.auth.hint"))
                self._apply_theme()
                return
            mode = "result" if self._last_auth_result is not None else "checking"

        if mode == "checking":
            self.auth_status_label.setText(tr("auth.status.checking"))
            self.auth_status_label.setObjectName("")
            self._apply_theme()
            return

        if mode == "reset_done":
            self.auth_status_label.setText(tr("auth.reset.done_status"))
            self.auth_status_label.setObjectName("error")
            self.auth_hint_label.setText(tr("auth.hint.need_login"))
            self._apply_theme()
            return

        if mode == "result" and self._last_auth_result is not None:
            self._set_auth_status(self._last_auth_result)
            return

        if self.is_logged_in and self.auth_username:
            self.auth_status_label.setText(tr("auth.status.logged_in", user=self.auth_username))
            self.auth_status_label.setObjectName("accent")
            self.auth_hint_label.setText(tr("auth.hint.done"))
            self._apply_theme()
            return

        self.auth_status_label.setText(tr("auth.status.unauthorized"))
        self.auth_status_label.setObjectName("error")
        self.auth_hint_label.setText(tr("main.auth.hint"))
        self._apply_theme()

    def _refresh_auth_status(self) -> None:
        settings = self._collect_settings()
        if not settings.api_id.strip() or not settings.api_hash.strip():
            self._auth_status_mode = "api_missing"
            self._last_auth_result = None
            self.auth_status_label.setText(tr("auth.error.api_missing"))
            self.auth_status_label.setObjectName("error")
            self._apply_theme()
            return

        if not self._persist_settings():
            return

        settings = self.settings
        self._auth_status_mode = "checking"
        self.auth_status_label.setText(tr("auth.status.checking"))
        self.auth_status_label.setObjectName("")

        def worker() -> None:
            result = check_session(
                self._session_path(settings),
                int(settings.api_id),
                settings.api_hash,
                settings,
            )
            self._invoker.run(lambda: self._set_auth_status(result))

        threading.Thread(target=worker, daemon=True).start()

    def _reset_session(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            show_warning(self, tr("main.busy.title"), tr("main.busy.stop_first"))
            return
        if not ask_yes_no(
            self,
            tr("auth.reset.title"),
            tr("auth.reset.body"),
        ):
            return
        if not self._persist_settings():
            return
        removed = reset_session_files(self._session_path(self.settings))
        self.is_logged_in = False
        self.auth_username = ""
        self._auth_status_mode = "reset_done"
        self._last_auth_result = None
        self.auth_status_label.setText(tr("auth.reset.done_status"))
        self.auth_status_label.setObjectName("error")
        self.auth_hint_label.setText(tr("auth.hint.need_login"))
        self._apply_theme()
        self._update_download_buttons()
        logging.info(
            tr(
                "log.auth.session_reset",
                files=", ".join(removed) or tr("log.auth.session_none"),
            ),
        )
        show_info(self, tr("auth.reset.title"), tr("auth.reset.done_info"))

    def _reset_download_journal(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            show_warning(self, tr("main.busy.title"), tr("main.busy.stop_first"))
            return
        if not ask_yes_no(
            self,
            tr("auth.journal.reset_title"),
            tr("auth.journal.reset_body"),
        ):
            return
        if not self._persist_settings():
            return
        session_name = self.settings.session_name.strip() or "hashtag_session"
        removed = reset_all_download_states(STATE_DIR, session_name)
        logging.info(
            tr(
                "log.auth.journal_reset",
                files=", ".join(removed) or tr("log.auth.journal_not_found"),
            ),
        )
        self._update_download_buttons()
        show_info(
            self,
            tr("auth.journal.reset_title"),
            tr("auth.journal.reset_info"),
        )

    def _update_download_buttons(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return
        logged_in = self.is_logged_in
        for btn in (self.once_btn, self.preview_btn, self.verify_btn):
            btn.setEnabled(logged_in)

    def _start_login(
        self,
        *,
        quiet: bool = False,
        on_finished: Callable[[AuthResult], None] | None = None,
        prompt_parent: QWidget | None = None,
    ) -> None:
        if self.login_thread and self.login_thread.is_alive():
            if not quiet:
                show_info(self, tr("main.auth.login"), tr("auth.login.already_running"))
            return
        if self.worker_thread and self.worker_thread.is_alive():
            if not quiet:
                show_warning(self, tr("main.busy.title"), tr("main.busy.stop_first"))
            return
        if not self._persist_settings():
            return
        settings = self.settings
        if not quiet:
            self.status_label.setText(tr("auth.status.waiting"))
        self._set_login_buttons_state(False)
        logging.info(tr("log.auth.login_start"))
        self._login_cancel.clear()
        self._active_login_client = None
        prompter = (
            MainThreadPrompter(prompt_parent, self._invoker)
            if prompt_parent is not None
            else self.prompter
        )
        self._login_prompter = prompter

        def worker() -> None:
            result = login_interactive(
                self._session_path(settings),
                int(settings.api_id),
                settings.api_hash,
                ask=lambda title, prompt: prompter.ask(title, prompt),
                ask_secret=lambda title, prompt: prompter.ask(title, prompt, secret=True),
                ask_code=prompter.ask_code,
                settings=settings,
                should_cancel=self._login_should_cancel,
                register_client=self._register_login_client,
            )
            self._invoker.run(
                lambda: self._on_login_finished(result, quiet=quiet, on_finished=on_finished),
            )

        self.login_thread = threading.Thread(target=worker, daemon=True)
        self.login_thread.start()

    def _start_qr_login(
        self,
        *,
        quiet: bool = False,
        on_finished: Callable[[AuthResult], None] | None = None,
        prompt_parent: QWidget | None = None,
    ) -> None:
        if self.login_thread and self.login_thread.is_alive():
            if not quiet:
                show_info(self, tr("main.auth.login"), tr("auth.login.already_running"))
            return
        if self.worker_thread and self.worker_thread.is_alive():
            if not quiet:
                show_warning(self, tr("main.busy.title"), tr("main.busy.stop_first"))
            return
        if not self._persist_settings():
            return
        settings = self.settings
        if not quiet:
            self.status_label.setText(tr("auth.status.waiting_qr"))
        self._set_login_buttons_state(False)
        logging.info(tr("log.auth.qr_start"))
        self._login_cancel.clear()
        self._active_login_client = None
        prompter = (
            MainThreadPrompter(prompt_parent, self._invoker)
            if prompt_parent is not None
            else self.prompter
        )
        self._login_prompter = prompter

        def worker() -> None:
            result = login_qr_interactive(
                self._session_path(settings),
                int(settings.api_id),
                settings.api_hash,
                show_qr=prompter.show_qr,
                hide_qr=prompter.hide_qr,
                ask_secret=lambda title, prompt: prompter.ask(title, prompt, secret=True),
                settings=settings,
                should_cancel=self._login_should_cancel,
                register_client=self._register_login_client,
            )
            self._invoker.run(
                lambda: self._on_login_finished(result, quiet=quiet, on_finished=on_finished),
            )

        self.login_thread = threading.Thread(target=worker, daemon=True)
        self.login_thread.start()

    def _set_login_buttons_state(self, enabled: bool) -> None:
        self.login_btn.setEnabled(enabled)
        self.qr_login_btn.setEnabled(enabled)
        self.auth_busy_label.setVisible(not enabled)
        self.auth_busy_label.setText(tr("auth.busy") if not enabled else "")

    def hide_login_qr(self) -> None:
        prompter = self._login_prompter or self.prompter
        prompter.hide_qr()

    def _login_should_cancel(self) -> bool:
        return self._login_cancel.is_set()

    def _register_login_client(self, client: object) -> None:
        self._active_login_client = client

    def cancel_login(self) -> None:
        self._login_cancel.set()
        self.hide_login_qr()
        client = self._active_login_client
        if client is not None:
            try:
                disconnect_client_sync(client)
            except Exception:
                pass

    def login_in_progress(self) -> bool:
        return bool(self.login_thread and self.login_thread.is_alive())

    def _on_login_finished(
        self,
        result: AuthResult,
        *,
        quiet: bool = False,
        on_finished: Callable[[AuthResult], None] | None = None,
    ) -> None:
        self._login_prompter = None
        self._active_login_client = None
        self._login_cancel.clear()
        self._set_login_buttons_state(True)
        self._set_auth_status(result)
        if result.ok:
            if not quiet:
                self.status_label.setText(tr("auth.login.success_status"))
                show_info(
                    self,
                    tr("auth.login.success_status"),
                    tr("auth.login.success_info", user=result.username),
                )
        else:
            if not quiet:
                self.status_label.setText(tr("auth.login.failed_status"))
                show_error(
                    self,
                    tr("auth.login.failed_status"),
                    result.error or tr("main.error.unknown"),
                )
        if on_finished is not None:
            on_finished(result)
