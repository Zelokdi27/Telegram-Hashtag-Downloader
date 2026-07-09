"""Qt bridge · Мост UI-потока"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication


class MainThreadInvoker(QObject):
    """Main thread invoker · UI из фоновых потоков"""

    _invoke = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._invoke.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    def _dispatch(self, fn: object) -> None:
        if callable(fn):
            fn()

    def run(self, fn) -> None:
        app = QApplication.instance()
        if app is not None and QThread.currentThread() == app.thread():
            fn()
            return
        self._invoke.emit(fn)