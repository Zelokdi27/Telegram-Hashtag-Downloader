"""List field utils · Слияние списков в полях формы"""

from __future__ import annotations

import re

_SPLIT_RE = re.compile(r"[\n\r,;]+")


def split_list_field(text: str) -> list[str]:
    return [part.strip() for part in _SPLIT_RE.split(text or "") if part.strip()]


def merge_comma_field(current: str, additions: list[str]) -> str:
    """Merge comma field · Объединение без дубликатов"""
    merged: list[str] = []
    seen: set[str] = set()
    for token in split_list_field(current):
        key = token.lower()
        if key not in seen:
            seen.add(key)
            merged.append(token)
    for token in additions:
        clean = token.strip()
        if not clean:
            continue
        key = clean.lower()
        if key not in seen:
            seen.add(key)
            merged.append(clean)
    return ", ".join(merged)
