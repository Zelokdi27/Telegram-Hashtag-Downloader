"""Queue progress · Форматирование прогресса очереди"""

from __future__ import annotations

from .dl_types import ProgressState
from .i18n import tr


def format_batch_progress_label(state: ProgressState) -> str:
    """Batch progress label · Текст «Очередь: N из M»"""
    if state.batch_total <= 1:
        return ""
    channel = (state.batch_channel or "").strip()
    channel_part = f" · @{channel.lstrip('@')}" if channel else ""
    tag = (state.batch_hashtag or "").strip() or "?"
    return tr(
        "queue.progress.label",
        i=state.batch_index,
        total=state.batch_total,
        tag=tag,
        channel=channel_part,
    )


def queue_overall_percent(state: ProgressState) -> int:
    """Queue percent · Общий прогресс очереди 0–100"""
    if state.batch_total <= 1 or state.batch_index <= 0:
        return 0
    inner = 0.0
    if state.total > 0:
        if state.phase in {"search", "preview"}:
            inner = max(0.0, min(1.0, state.found / state.total))
        elif state.phase in {"download", "verify"}:
            inner = max(0.0, min(1.0, state.processed / state.total))
    elif state.phase in {"search", "download", "preview", "verify"}:
        inner = 0.15
    completed = max(0, state.batch_index - 1)
    overall = (completed + inner) / state.batch_total
    return min(100, max(0, int(100 * overall)))
