"""I18n · Локализация UI через tr()"""

from __future__ import annotations

import json
import locale as syslocale
from pathlib import Path

from app.paths import BUNDLE_DIR

LOCALES_DIR = BUNDLE_DIR / "locales"
SUPPORTED_LOCALES = ("ru", "en")
DEFAULT_LOCALE = "ru"

_catalog: dict[str, str | list[str]] = {}
_current_locale = DEFAULT_LOCALE


def _flatten(obj: object, prefix: str = "") -> dict[str, str | list[str]]:
    if not isinstance(obj, dict):
        return {}
    out: dict[str, str | list[str]] = {}
    for key, value in obj.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, full))
        elif isinstance(value, list):
            out[full] = [str(item) for item in value]
        else:
            out[full] = str(value)
    return out


def resolve_system_locale() -> str:
    try:
        loc, _ = syslocale.getdefaultlocale()
        text = (loc or "").lower()
    except Exception:
        text = ""
    if text.startswith("ru"):
        return "ru"
    return "en"


def normalize_locale(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text in {"", "system", "auto"}:
        return resolve_system_locale()
    if text in SUPPORTED_LOCALES:
        return text
    return DEFAULT_LOCALE


def current_locale() -> str:
    return _current_locale


def set_locale(lang: str | None) -> str:
    global _catalog, _current_locale
    resolved = normalize_locale(lang)
    path = LOCALES_DIR / f"{resolved}.json"
    if not path.is_file():
        resolved = DEFAULT_LOCALE
        path = LOCALES_DIR / f"{resolved}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    _catalog = _flatten(data)
    _current_locale = resolved
    return resolved


def tr(key: str, default: str | None = None, **kwargs: object) -> str:
    raw = _catalog.get(key)
    if raw is None:
        text = default if default is not None else key
    elif isinstance(raw, list):
        text = raw[0] if raw else (default or key)
    else:
        text = raw
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def tr_list(key: str) -> list[str]:
    raw = _catalog.get(key)
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        return [raw]
    return []


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    mod100 = n % 100
    mod10 = n % 10
    if 11 <= mod100 <= 14:
        return many
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def plural_word(key: str, n: int) -> str:
    forms = tr_list(f"batch.plural.{key}")
    if not forms:
        return key
    if current_locale() == "en":
        return forms[1] if n != 1 and len(forms) > 1 else forms[0]
    if len(forms) >= 3:
        return _ru_plural(n, forms[0], forms[1], forms[2])
    return forms[0] if n == 1 else forms[-1]


def kind_labels() -> dict[str, str]:
    return {
        "photo": tr("preview.kind.photo"),
        "video": tr("preview.kind.video"),
        "animation": tr("preview.kind.gif"),
        "document": tr("preview.kind.file"),
        "audio": tr("preview.kind.audio"),
    }


def preview_filter_labels() -> list[str]:
    return [
        tr("preview.filter.all"),
        tr("preview.filter.photo"),
        tr("preview.filter.video"),
        tr("preview.filter.gif"),
        tr("preview.filter.audio"),
        tr("preview.filter.files"),
        tr("preview.filter.selected"),
        tr("preview.filter.unselected"),
        tr("preview.filter.new"),
        tr("preview.filter.partial"),
        tr("preview.filter.on_disk"),
        tr("preview.filter.hide_on_disk"),
        tr("preview.filter.duplicates"),
        tr("preview.filter.hide_duplicates"),
    ]


def preview_sort_labels() -> list[str]:
    return [
        tr("preview.sort.date_desc"),
        tr("preview.sort.date_asc"),
        tr("preview.sort.channel"),
        tr("preview.sort.kind"),
    ]
