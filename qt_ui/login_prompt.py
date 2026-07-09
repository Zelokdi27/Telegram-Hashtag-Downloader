"""Login prompt · Диалог повторного входа"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr

from .dialogs import _present_dialog


def show_session_login_prompt(
    parent: QWidget,
    *,
    message: str,
) -> str | None:
    """Session login prompt · 'phone'/'qr'/None"""
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("login.prompt.title"))
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)

    title = QLabel(tr("login.prompt.heading"))
    title.setStyleSheet("font-weight: bold;")
    title.setWordWrap(True)
    layout.addWidget(title)

    body = QLabel(
        message.strip() or tr("login.prompt.default_error"),
    )
    body.setWordWrap(True)
    layout.addWidget(body)

    hint = QLabel(tr("login.prompt.hint"))
    hint.setObjectName("muted")
    hint.setWordWrap(True)
    layout.addWidget(hint)

    choice: list[str | None] = [None]

    def pick(action: str | None) -> None:
        choice[0] = action
        dialog.accept()

    row = QHBoxLayout()
    row.addStretch()
    later_btn = QPushButton(tr("login.prompt.later"))
    later_btn.clicked.connect(lambda: pick(None))
    row.addWidget(later_btn)
    qr_btn = QPushButton(tr("login.prompt.qr"))
    qr_btn.clicked.connect(lambda: pick("qr"))
    row.addWidget(qr_btn)
    phone_btn = QPushButton(tr("login.prompt.phone"))
    phone_btn.setDefault(True)
    phone_btn.clicked.connect(lambda: pick("phone"))
    row.addWidget(phone_btn)
    layout.addLayout(row)

    _present_dialog(dialog, parent)
    dialog.exec()
    return choice[0]
