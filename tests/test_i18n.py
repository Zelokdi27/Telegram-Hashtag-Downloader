"""Locale parity · Согласованность локалей"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.i18n import LOCALES_DIR, set_locale, tr


def _flatten(obj: object, prefix: str = "") -> set[str]:
    if not isinstance(obj, dict):
        return set()
    keys: set[str] = set()
    for key, value in obj.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            keys.update(_flatten(value, full))
        else:
            keys.add(full)
    return keys


def test_locale_files_have_same_keys() -> None:
    ru = json.loads((LOCALES_DIR / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    ru_keys = _flatten(ru)
    en_keys = _flatten(en)
    assert ru_keys == en_keys


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_critical_keys_non_empty(lang: str) -> None:
    set_locale(lang)
    samples = [
        tr("summary.no_errors"),
        tr("preview.badge.duplicate"),
        tr("queue.progress.label", i=1, total=2, tag="t", channel=""),
        tr("errors.validation.hashtag_empty"),
        tr("auth.error.not_logged_in"),
    ]
    for text in samples:
        assert text.strip()
        assert not text.startswith("summary.")
        assert not text.startswith("preview.")
