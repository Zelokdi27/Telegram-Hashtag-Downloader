"""Hashtag history · Локальная история хештегов"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from .config_store import STATE_DIR
from .i18n import tr
from .tg_hashtag_dl import normalize_hashtag

logger = logging.getLogger(__name__)

HASHTAG_SUGGEST_LIMIT = 4
HISTORY_PATH = STATE_DIR / "hashtag_history.json"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load_hashtag_history(*, path: Path | None = None, limit: int | None = None) -> list[str]:
    target = path or HISTORY_PATH
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug(tr("log.history.read_failed", target=target, exc=exc))
        return []
    raw = data.get("tags") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        tags.append(text)
    if limit is not None and limit > 0:
        return tags[:limit]
    return tags


def suggest_hashtag_history(*, path: Path | None = None) -> list[str]:
    """Hashtag suggest · Последние теги для списка"""
    return load_hashtag_history(path=path, limit=HASHTAG_SUGGEST_LIMIT)


def save_hashtag_history(tags: list[str], *, path: Path | None = None) -> None:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in tags:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    target = path or HISTORY_PATH
    _atomic_write_json(target, {"tags": cleaned})


def record_hashtags_used(raw_tags: Iterable[str], *, path: Path | None = None) -> list[str]:
    """Record hashtags · Добавить теги в MRU"""
    target = path or HISTORY_PATH
    history = load_hashtag_history(path=target)
    for raw in raw_tags:
        try:
            tag = normalize_hashtag(raw)
        except ValueError:
            continue
        if tag in history:
            history.remove(tag)
        history.insert(0, tag)
    save_hashtag_history(history, path=target)
    return history