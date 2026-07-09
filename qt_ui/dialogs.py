"""Dialogs · Диалоговые окна"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from app.auth_constants import RESEND_CODE
from app.i18n import tr

from .bridge import MainThreadInvoker
from .win_chrome import present_qr_window, present_top_level_window


class MainThreadPrompter:
    """Main thread prompter · Модальные окна из фонового потока"""

    def __init__(self, parent: QWidget, invoker: MainThreadInvoker) -> None:
        self._parent = parent
        self._invoker = invoker
        self._qr_dialog: QDialog | None = None

    def ask(self, title: str, prompt: str, *, secret: bool = False) -> str | None:
        response: queue.Queue[str | None] = queue.Queue(maxsize=1)
        done = threading.Event()

        def show() -> None:
            response.put(_input_dialog(self._parent, title, prompt, secret=secret))
            done.set()

        self._invoker.run(show)
        done.wait(timeout=600)
        try:
            return response.get_nowait()
        except queue.Empty:
            return None

    def ask_code(self, title: str, prompt: str) -> str | None:
        response: queue.Queue[str | None] = queue.Queue(maxsize=1)
        done = threading.Event()

        def show() -> None:
            response.put(_code_dialog(self._parent, title, prompt))
            done.set()

        self._invoker.run(show)
        done.wait(timeout=600)
        try:
            return response.get_nowait()
        except queue.Empty:
            return None

    def show_qr(self, url: str) -> None:
        done = threading.Event()
        error: list[BaseException | None] = [None]

        def show() -> None:
            try:
                self._qr_dialog = _qr_dialog(self._parent, url)
            except BaseException as exc:
                error[0] = exc
            finally:
                done.set()

        self._invoker.run(show)
        if not done.wait(timeout=60):
            raise TimeoutError(tr("errors.qr_timeout"))
        if error[0] is not None:
            raise error[0]

    def hide_qr(self) -> None:
        dialog = self._qr_dialog

        def close() -> None:
            if dialog is not None:
                dialog.close()
            self._qr_dialog = None

        self._invoker.run(close)


def _input_dialog(parent: QWidget, title: str, prompt: str, *, secret: bool = False) -> str | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(prompt))
    entry = QLineEdit()
    if secret:
        entry.setEchoMode(QLineEdit.EchoMode.Password)
    layout.addWidget(entry)
    row = QHBoxLayout()
    row.addStretch()
    ok_btn = QPushButton(tr("common.ok"))
    ok_btn.clicked.connect(dialog.accept)
    cancel_btn = QPushButton(tr("common.cancel"))
    cancel_btn.clicked.connect(dialog.reject)
    row.addWidget(ok_btn)
    row.addWidget(cancel_btn)
    layout.addLayout(row)
    entry.returnPressed.connect(dialog.accept)
    entry.setFocus()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    text = entry.text().strip()
    return text if text else None


def _code_dialog(parent: QWidget, title: str, prompt: str) -> str | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(prompt))
    entry = QLineEdit()
    layout.addWidget(entry)

    result: list[str | None] = [None]

    def accept_code() -> None:
        text = entry.text().strip()
        result[0] = text if text else None
        dialog.accept()

    def resend() -> None:
        result[0] = RESEND_CODE
        dialog.accept()

    row = QHBoxLayout()
    ok_btn = QPushButton(tr("common.ok"))
    ok_btn.clicked.connect(accept_code)
    resend_btn = QPushButton(tr("common.resend"))
    resend_btn.clicked.connect(resend)
    cancel_btn = QPushButton(tr("common.cancel"))
    cancel_btn.clicked.connect(dialog.reject)
    row.addWidget(ok_btn)
    row.addWidget(resend_btn)
    row.addWidget(cancel_btn)
    layout.addLayout(row)
    entry.returnPressed.connect(accept_code)
    entry.setFocus()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return result[0]


def _qr_pixmap_from_url(url: str) -> QPixmap:
    import io

    import qrcode
    from PIL import Image, ImageQt

    qr = qrcode.QRCode(border=2, box_size=6)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    if hasattr(img, "get_image"):
        pil_img = img.get_image()
    elif isinstance(img, Image.Image):
        pil_img = img
    else:
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        pixmap = QPixmap()
        if not pixmap.loadFromData(buffer.getvalue()):
            raise RuntimeError(tr("errors.qr_image"))
        return pixmap
    return QPixmap.fromImage(ImageQt.ImageQt(pil_img))


def _qr_dialog(parent: QWidget | None, url: str) -> QDialog:
    from PySide6.QtWidgets import QApplication

    dialog = QDialog(None)
    dialog.setWindowTitle(tr("login.qr.title"))
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(tr("login.qr.instructions")))
    pixmap = _qr_pixmap_from_url(url)
    img_label = QLabel()
    img_label.setPixmap(pixmap)
    img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(img_label)
    wait = QLabel(tr("login.qr.waiting"))
    wait.setObjectName("muted")
    layout.addWidget(wait)
    anchor = parent
    if anchor is None or not anchor.isVisible():
        active = QApplication.activeWindow()
        anchor = active if active is not None else parent
    present_qr_window(dialog, anchor)
    return dialog


def open_path_in_file_manager(path: str | Path) -> None:
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        import os

        os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(folder)], check=False)
    else:
        subprocess.run(["xdg-open", str(folder)], check=False)


def show_download_summary_dialog(
    parent: QWidget,
    title: str,
    summary: str,
    *,
    download_dir: str | Path | None = None,
) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(480, 360)
    layout = QVBoxLayout(dialog)
    text = QTextEdit()
    text.setReadOnly(True)
    text.setPlainText(summary)
    layout.addWidget(text)
    row = QHBoxLayout()
    row.addStretch()
    if download_dir:
        open_btn = QPushButton(tr("common.open_folder"))
        open_btn.clicked.connect(lambda: open_path_in_file_manager(download_dir))
        row.addWidget(open_btn)
    ok_btn = QPushButton(tr("common.ok"))
    ok_btn.clicked.connect(dialog.accept)
    row.addWidget(ok_btn)
    layout.addLayout(row)
    _present_dialog(dialog, parent)
    dialog.exec()


def show_integrity_summary_dialog(
    parent: QWidget,
    title: str,
    summary: str,
    *,
    can_download_missing: bool,
    download_dir: str | Path | None = None,
) -> str:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(480, 380)
    layout = QVBoxLayout(dialog)
    text = QTextEdit()
    text.setReadOnly(True)
    text.setPlainText(summary)
    layout.addWidget(text)
    result = ["ok"]
    row = QHBoxLayout()
    row.addStretch()

    def choose(action: str) -> None:
        result[0] = action
        dialog.accept()

    if download_dir:
        open_btn = QPushButton(tr("common.open_folder"))
        open_btn.clicked.connect(lambda: open_path_in_file_manager(download_dir))
        row.addWidget(open_btn)
    if can_download_missing:
        dl_btn = QPushButton(tr("common.download_missing"))
        dl_btn.clicked.connect(lambda: choose("download"))
        row.addWidget(dl_btn)
    ok_btn = QPushButton(tr("common.ok"))
    ok_btn.clicked.connect(lambda: choose("ok"))
    row.addWidget(ok_btn)
    layout.addLayout(row)
    _present_dialog(dialog, parent)
    dialog.exec()
    return result[0]


def show_autotune_result_dialog(
    parent: QWidget,
    title: str,
    summary: str,
    *,
    can_apply: bool,
    offer_open_settings: bool = False,
) -> str:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(520, 420)
    layout = QVBoxLayout(dialog)
    text = QTextEdit()
    text.setReadOnly(True)
    text.setPlainText(summary)
    layout.addWidget(text)
    result = ["keep"]
    row = QHBoxLayout()
    row.addStretch()

    def choose(action: str) -> None:
        result[0] = action
        dialog.accept()

    if offer_open_settings:
        open_btn = QPushButton(tr("autotune.dialog.open_settings"))
        open_btn.clicked.connect(lambda: choose("open"))
        row.addWidget(open_btn)
    keep_btn = QPushButton(tr("autotune.dialog.keep"))
    keep_btn.clicked.connect(lambda: choose("keep"))
    row.addWidget(keep_btn)
    if can_apply:
        apply_btn = QPushButton(tr("autotune.dialog.apply"))
        apply_btn.clicked.connect(lambda: choose("apply"))
        row.addWidget(apply_btn)
    layout.addLayout(row)
    _present_dialog(dialog, parent)
    dialog.exec()
    return result[0]


def _present_dialog(dialog: QDialog, parent: QWidget) -> None:
    present_top_level_window(dialog, parent)


def _message_box(parent: QWidget, title: str, text: str, icon: QMessageBox.Icon) -> QMessageBox:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    return box


def ask_yes_no(parent: QWidget, title: str, text: str) -> bool:
    box = _message_box(parent, title, text, QMessageBox.Icon.Question)
    yes_btn = box.addButton(tr("common.yes"), QMessageBox.ButtonRole.YesRole)
    no_btn = box.addButton(tr("common.no"), QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(no_btn)
    box.exec()
    return box.clickedButton() == yes_btn


def show_info(parent: QWidget, title: str, text: str) -> None:
    box = _message_box(parent, title, text, QMessageBox.Icon.Information)
    box.addButton(tr("common.ok"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def show_warning(parent: QWidget, title: str, text: str) -> None:
    box = _message_box(parent, title, text, QMessageBox.Icon.Warning)
    box.addButton(tr("common.ok"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def show_error(parent: QWidget, title: str, text: str) -> None:
    box = _message_box(parent, title, text, QMessageBox.Icon.Critical)
    box.addButton(tr("common.ok"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def show_about_dialog(parent: QWidget) -> None:
    from app.version import (
        APP_AUTHOR,
        APP_CONTACT_TELEGRAM,
        APP_NAME,
        APP_URL,
        __version__,
        copyright_line,
    )

    lines = [
        APP_NAME,
        tr("about.version", version=__version__),
        "",
        tr("about.author", author=APP_AUTHOR),
        tr("about.contact", contact=APP_CONTACT_TELEGRAM),
        copyright_line(),
        "",
        tr("about.disclaimer"),
        tr("about.signature_note"),
    ]
    url = (APP_URL or "").strip()
    if url:
        lines.extend(["", url])
    show_info(parent, tr("about.title"), "\n".join(lines))
