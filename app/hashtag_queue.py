"""Hashtag queue · Очередь хештегов"""

from __future__ import annotations

import json

from .config_store import STATE_DIR
from .tg_hashtag_dl import normalize_hashtag

QUEUE_PATH = STATE_DIR / "hashtag_queue.json"
MAX_QUEUE_SIZE = 50


def normalize_hashtag_queue(tags: list[str]) -> list[str]:
    """Queue normalize · Уникальные хештеги, порядок сохранён"""
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            tag = normalize_hashtag(text)
        except ValueError:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return result[:MAX_QUEUE_SIZE]


def load_hashtag_queue() -> list[str]:
    if not QUEUE_PATH.exists():
        return []
    try:
        raw = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return normalize_hashtag_queue([str(item) for item in raw])


def save_hashtag_queue(tags: list[str]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = normalize_hashtag_queue(tags)
    QUEUE_PATH.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
