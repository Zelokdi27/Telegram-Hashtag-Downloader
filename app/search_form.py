"""Search form · Шаблоны и снимок формы поиска"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .config_store import STATE_DIR, SettingsData

TEMPLATES_PATH = STATE_DIR / "search_templates.json"
MAX_NAMED_TEMPLATES = 20


@dataclass
class SearchFormSnapshot:
    hashtag: str = ""
    extra_hashtags: str = ""
    exclude_hashtags: str = ""
    required_hashtags: str = ""
    extra_channels: str = ""
    channel_filter: str = ""
    date_from: str = ""
    date_to: str = ""
    max_posts: int = 0
    media_photo: bool = True
    media_video: bool = True
    media_animation: bool = True
    media_audio: bool = True
    media_document: bool = True
    sequential_preview: bool = False
    preview_batch_size: int = 200


@dataclass
class NamedSearchTemplate:
    name: str
    form: SearchFormSnapshot = field(default_factory=SearchFormSnapshot)


def _date_to_str(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def snapshot_from_settings(settings: SettingsData) -> SearchFormSnapshot:
    return SearchFormSnapshot(
        hashtag=settings.hashtag.strip(),
        extra_hashtags=settings.extra_hashtags.strip(),
        exclude_hashtags=settings.exclude_hashtags.strip(),
        required_hashtags=settings.required_hashtags.strip(),
        extra_channels=settings.extra_channels.strip(),
        channel_filter=settings.channel_filter.strip(),
        date_from=settings.date_from.strip(),
        date_to=settings.date_to.strip(),
        max_posts=int(settings.max_posts),
        media_photo=settings.media_photo,
        media_video=settings.media_video,
        media_animation=settings.media_animation,
        media_audio=settings.media_audio,
        media_document=settings.media_document,
        sequential_preview=settings.sequential_preview,
        preview_batch_size=int(settings.preview_batch_size),
    )


def apply_snapshot_to_settings(
    settings: SettingsData,
    snapshot: SearchFormSnapshot,
) -> SettingsData:
    settings.hashtag = snapshot.hashtag
    settings.extra_hashtags = snapshot.extra_hashtags
    settings.exclude_hashtags = snapshot.exclude_hashtags
    settings.required_hashtags = snapshot.required_hashtags
    settings.extra_channels = snapshot.extra_channels
    settings.channel_filter = snapshot.channel_filter
    settings.date_from = snapshot.date_from
    settings.date_to = snapshot.date_to
    settings.max_posts = max(0, int(snapshot.max_posts))
    settings.media_photo = snapshot.media_photo
    settings.media_video = snapshot.media_video
    settings.media_animation = snapshot.media_animation
    settings.media_audio = snapshot.media_audio
    settings.media_document = snapshot.media_document
    settings.sequential_preview = snapshot.sequential_preview
    settings.preview_batch_size = max(20, min(int(snapshot.preview_batch_size), 1000))
    return settings


def load_named_templates() -> list[NamedSearchTemplate]:
    if not TEMPLATES_PATH.exists():
        return []
    try:
        raw = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items: list[NamedSearchTemplate] = []
    for entry in raw.get("templates", []):
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        form_data = entry.get("form") or {}
        defaults = asdict(SearchFormSnapshot())
        merged = {key: form_data.get(key, defaults[key]) for key in defaults}
        items.append(NamedSearchTemplate(name=name, form=SearchFormSnapshot(**merged)))
    return items


def save_named_templates(templates: list[NamedSearchTemplate]) -> None:
    TEMPLATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "templates": [
            {"name": item.name, "form": asdict(item.form)}
            for item in templates[:MAX_NAMED_TEMPLATES]
        ],
    }
    TEMPLATES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def upsert_named_template(name: str, form: SearchFormSnapshot) -> None:
    clean = name.strip()
    if not clean:
        return
    templates = [item for item in load_named_templates() if item.name != clean]
    templates.insert(0, NamedSearchTemplate(name=clean, form=form))
    save_named_templates(templates[:MAX_NAMED_TEMPLATES])


def delete_named_template(name: str) -> None:
    clean = name.strip()
    if not clean:
        return
    templates = [item for item in load_named_templates() if item.name != clean]
    save_named_templates(templates)


def rename_named_template(old_name: str, new_name: str) -> bool:
    """Rename template · Переименование; False если имя занято"""
    old_clean = old_name.strip()
    new_clean = new_name.strip()
    if not old_clean or not new_clean or old_clean == new_clean:
        return False
    templates = load_named_templates()
    if any(item.name == new_clean for item in templates):
        return False
    updated: list[NamedSearchTemplate] = []
    found = False
    for item in templates:
        if item.name == old_clean:
            updated.append(NamedSearchTemplate(name=new_clean, form=item.form))
            found = True
        else:
            updated.append(item)
    if not found:
        return False
    save_named_templates(updated)
    return True


def template_exists(name: str) -> bool:
    clean = name.strip()
    if not clean:
        return False
    return any(item.name == clean for item in load_named_templates())


def empty_snapshot() -> SearchFormSnapshot:
    return SearchFormSnapshot()


def snapshot_from_mapping(data: dict | None) -> SearchFormSnapshot | None:
    if not data:
        return None
    defaults = asdict(SearchFormSnapshot())
    merged = {key: data.get(key, defaults[key]) for key in defaults}
    return SearchFormSnapshot(**merged)