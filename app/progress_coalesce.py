"""Progress coalesce · Сглаживание callback прогресса"""

from __future__ import annotations

import time

from .tg_hashtag_dl import ProgressCallback, ProgressState

PROGRESS_COALESCE_INTERVAL_SEC = 0.15
_IMMEDIATE_PHASES = frozenset({"done", "stopped"})


class ProgressCoalescer:
    def __init__(
        self,
        callback: ProgressCallback,
        *,
        interval_sec: float = PROGRESS_COALESCE_INTERVAL_SEC,
    ) -> None:
        self._callback = callback
        self._interval_sec = max(0.05, float(interval_sec))
        self._pending: ProgressState | None = None
        self._last_emit = 0.0
        self._last_phase = ""

    def __call__(self, state: ProgressState) -> None:
        if self._should_emit_immediately(state):
            self._pending = None
            self._emit(state)
            return
        self._pending = state
        if time.monotonic() - self._last_emit >= self._interval_sec:
            self._flush_pending()

    def flush(self) -> None:
        self._flush_pending()

    def _should_emit_immediately(self, state: ProgressState) -> bool:
        if state.phase in _IMMEDIATE_PHASES:
            return True
        if state.alert:
            return True
        if state.phase != self._last_phase:
            return True
        return False

    def _flush_pending(self) -> None:
        if self._pending is None:
            return
        self._emit(self._pending)
        self._pending = None

    def _emit(self, state: ProgressState) -> None:
        self._callback(state)
        self._last_emit = time.monotonic()
        self._last_phase = state.phase