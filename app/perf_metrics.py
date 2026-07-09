"""Performance metrics · Лёгкие тайминги и счётчики"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

_LOG = logging.getLogger(__name__)
_ENABLED = os.getenv("TGHD_PERF_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
_COUNTS: dict[str, int] = defaultdict(int)
_TOTAL_MS: dict[str, float] = defaultdict(float)
_LOCK = Lock()


def perf_enabled() -> bool:
    return _ENABLED


def incr(name: str, amount: int = 1) -> None:
    if not _ENABLED:
        return
    with _LOCK:
        _COUNTS[name] += int(amount)


def add_ms(name: str, elapsed_ms: float) -> None:
    if not _ENABLED:
        return
    with _LOCK:
        _TOTAL_MS[name] += float(elapsed_ms)


@contextmanager
def measure(name: str, logger: logging.Logger | None = None, **fields: object) -> Iterator[None]:
    if not _ENABLED:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        add_ms(name, elapsed_ms)
        if logger is not None and logger.isEnabledFor(logging.DEBUG):
            payload = ", ".join(f"{key}={value}" for key, value in sorted(fields.items()))
            suffix = f" ({payload})" if payload else ""
            logger.debug("perf %s: %.1fms%s", name, elapsed_ms, suffix)


def snapshot() -> dict[str, dict[str, float | int]]:
    with _LOCK:
        names = sorted(set(_COUNTS) | set(_TOTAL_MS))
        return {
            name: {
                "count": _COUNTS.get(name, 0),
                "total_ms": round(_TOTAL_MS.get(name, 0.0), 3),
            }
            for name in names
        }


def reset() -> None:
    with _LOCK:
        _COUNTS.clear()
        _TOTAL_MS.clear()


def log_snapshot(logger: logging.Logger | None = None, *, prefix: str = "perf snapshot") -> None:
    if not _ENABLED:
        return
    target = logger or _LOG
    data = snapshot()
    if not data:
        return
    target.debug("%s: %s", prefix, data)
