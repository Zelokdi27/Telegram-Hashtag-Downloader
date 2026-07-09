"""Config store · Хранилище настроек"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .download_options import (
    MediaFilterSettings,
    parse_exclude_hashtag_list,
    parse_media_filter,
    parse_required_hashtag_list,
)
from .dl_types import AppConfig
from .dl_utils import (
    normalize_channel_filter,
    normalize_hashtag,
    parse_bool,
    parse_date_filter,
    safe_name,
)
from .i18n import tr
from .paths import BUNDLE_DIR, PROJECT_DIR

DATA_DIR = PROJECT_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
SESSIONS_DIR = DATA_DIR / "sessions"
STATE_DIR = DATA_DIR / "state"
DISK_INDEX_CACHE_DIR = STATE_DIR / "disk_index"
PREVIEW_CACHE_DIR = DATA_DIR / "cache" / "preview"
AUTOTUNE_PROFILE_PATH = STATE_DIR / "autotune_profile.json"
DEFAULT_DOWNLOAD_DIR = "data/downloads"
LOG_FILE = LOGS_DIR / "app.log"

ENV_PATH = PROJECT_DIR / ".env"
ENV_EXAMPLE_PATH = BUNDLE_DIR / ".env.example"

_DATA_MIGRATED = False


@dataclass
class SettingsData:
    api_id: str = ""
    api_hash: str = ""
    hashtag: str = ""
    download_dir: str = DEFAULT_DOWNLOAD_DIR
    page_limit: int = 50
    max_posts: int = 0
    date_from: str = ""
    date_to: str = ""
    channel_filter: str = ""
    session_name: str = "hashtag_session"
    proxy_enabled: bool = False
    proxy_type: str = "socks5"
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 1080
    media_photo: bool = True
    media_video: bool = True
    media_animation: bool = True
    media_audio: bool = True
    media_document: bool = True
    folder_by_date: bool = False
    caption_in_filename: bool = False
    caption_max_len: int = 40
    dedup_by_hash: bool = True
    download_retries: int = 3
    extra_hashtags: str = ""
    exclude_hashtags: str = ""
    required_hashtags: str = ""
    extra_channels: str = ""
    dark_theme: bool = False
    remember_last_search: bool = False
    sequential_preview: bool = False
    preview_batch_size: int = 200
    preview_parallel_workers: int = 3
    download_parallel_workers: int = 1
    win_notify_enabled: bool = True
    win_notify_success: bool = True
    win_notify_errors: bool = True
    setup_wizard_completed: bool = False
    ui_language: str = "system"


def safe_int(
    value: str | int | None,
    default: int,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    try:
        text = str(value).strip() if value is not None else ""
        parsed = int(text if text else default)
    except (ValueError, TypeError):
        parsed = default
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def _move_path(source: Path, dest: Path) -> None:
    if not source.exists() or dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    source.rename(dest)


def _move_tree_contents(source: Path, dest: Path) -> None:
    if not source.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = dest / item.name
        if not target.exists():
            item.rename(target)
    try:
        source.rmdir()
    except OSError:
        pass


def _normalize_download_dir_value(value: str) -> str:
    raw = value.strip() or DEFAULT_DOWNLOAD_DIR
    if raw.replace("\\", "/") == "downloads":
        return DEFAULT_DOWNLOAD_DIR
    return raw


def migrate_data_layout() -> None:
    """Data layout migration · Перенос файлов в data/"""
    global _DATA_MIGRATED
    if _DATA_MIGRATED:
        return
    _DATA_MIGRATED = True

    for folder in (
        DATA_DIR,
        LOGS_DIR,
        SESSIONS_DIR,
        STATE_DIR,
        DISK_INDEX_CACHE_DIR,
        PREVIEW_CACHE_DIR,
        DATA_DIR / "downloads",
    ):
        folder.mkdir(parents=True, exist_ok=True)

    _move_path(PROJECT_DIR / "app.log", LOG_FILE)
    _move_tree_contents(PROJECT_DIR / "downloads", DATA_DIR / "downloads")
    _move_tree_contents(PROJECT_DIR / ".preview_cache", PREVIEW_CACHE_DIR)

    for pattern in ("*_state.json",):
        for path in PROJECT_DIR.glob(pattern):
            _move_path(path, STATE_DIR / path.name)

    for pattern in ("*.session", "*.session-journal"):
        for path in PROJECT_DIR.glob(pattern):
            _move_path(path, SESSIONS_DIR / path.name)

    legacy_session = PROJECT_DIR / "hashtag_session"
    if legacy_session.exists() and legacy_session.is_file():
        _move_path(legacy_session, SESSIONS_DIR / legacy_session.name)


def session_path_for(session_name: str) -> Path:
    return SESSIONS_DIR / (session_name.strip() or "hashtag_session")


def clear_session_fields(settings: SettingsData) -> SettingsData:
    """Session search fields · Поля поиска только на сессию"""
    settings.hashtag = ""
    settings.date_from = ""
    settings.date_to = ""
    settings.channel_filter = ""
    settings.extra_hashtags = ""
    settings.exclude_hashtags = ""
    settings.required_hashtags = ""
    settings.extra_channels = ""
    return settings


def load_settings(*, include_session: bool = True) -> SettingsData:
    migrate_data_layout()
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
        env = dotenv_values(ENV_PATH)
    else:
        env = {}

    settings = SettingsData(
        api_id=str(env.get("API_ID", "") or ""),
        api_hash=str(env.get("API_HASH", "") or ""),
        hashtag=str(env.get("HASHTAG", "") or ""),
        download_dir=_normalize_download_dir_value(str(env.get("DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR) or DEFAULT_DOWNLOAD_DIR)),
        page_limit=safe_int(env.get("PAGE_LIMIT"), 50, min_value=1),
        max_posts=safe_int(env.get("MAX_POSTS"), 0, min_value=0),
        date_from=str(env.get("DATE_FROM", "") or ""),
        date_to=str(env.get("DATE_TO", "") or ""),
        channel_filter=str(env.get("CHANNEL_FILTER", "") or ""),
        session_name=str(env.get("SESSION_NAME", "hashtag_session") or "hashtag_session"),
        proxy_enabled=parse_bool(env.get("PROXY_ENABLED", "false"), default=False),
        proxy_type=str(env.get("PROXY_TYPE", "socks5") or "socks5").strip().lower(),
        proxy_host=str(env.get("PROXY_HOST", "127.0.0.1") or "127.0.0.1"),
        proxy_port=safe_int(env.get("PROXY_PORT"), 1080, min_value=1, max_value=65535),
        media_photo=parse_bool(env.get("MEDIA_PHOTO", "true"), default=True),
        media_video=parse_bool(env.get("MEDIA_VIDEO", "true"), default=True),
        media_animation=parse_bool(env.get("MEDIA_ANIMATION", "true"), default=True),
        media_audio=parse_bool(env.get("MEDIA_AUDIO", "true"), default=True),
        media_document=parse_bool(env.get("MEDIA_DOCUMENT", "true"), default=True),
        folder_by_date=parse_bool(env.get("FOLDER_BY_DATE", "false"), default=False),
        caption_in_filename=parse_bool(env.get("CAPTION_IN_FILENAME", "false"), default=False),
        caption_max_len=safe_int(env.get("CAPTION_MAX_LEN"), 40, min_value=0, max_value=80),
        dedup_by_hash=parse_bool(env.get("DEDUP_BY_HASH", "true"), default=True),
        download_retries=safe_int(env.get("DOWNLOAD_RETRIES"), 3, min_value=0, max_value=10),
        extra_hashtags=str(env.get("EXTRA_HASHTAGS", "") or ""),
        exclude_hashtags=str(env.get("EXCLUDE_HASHTAGS", "") or ""),
        required_hashtags=str(env.get("REQUIRED_HASHTAGS", "") or ""),
        extra_channels=str(env.get("EXTRA_CHANNELS", "") or ""),
        dark_theme=parse_bool(env.get("DARK_THEME", "false"), default=False),
        remember_last_search=parse_bool(env.get("REMEMBER_LAST_SEARCH", "false"), default=False),
        sequential_preview=parse_bool(env.get("SEQUENTIAL_PREVIEW", "false"), default=False),
        preview_batch_size=safe_int(env.get("PREVIEW_BATCH_SIZE"), 200, min_value=20, max_value=1000),
        preview_parallel_workers=safe_int(env.get("PREVIEW_PARALLEL_WORKERS"), 3, min_value=1, max_value=6),
        download_parallel_workers=safe_int(
            env.get("DOWNLOAD_PARALLEL_WORKERS"),
            1,
            min_value=1,
            max_value=3,
        ),
        win_notify_enabled=parse_bool(env.get("WIN_NOTIFY_ENABLED", "true"), default=True),
        win_notify_success=parse_bool(env.get("WIN_NOTIFY_SUCCESS", "true"), default=True),
        win_notify_errors=parse_bool(env.get("WIN_NOTIFY_ERRORS", "true"), default=True),
        setup_wizard_completed=parse_bool(
            env.get("SETUP_WIZARD_COMPLETED", "false"),
            default=False,
        ),
        ui_language=str(env.get("UI_LANGUAGE", "system") or "system").strip().lower(),
    )
    if not include_session and not settings.remember_last_search:
        clear_session_fields(settings)
    return settings


def save_settings(settings: SettingsData) -> None:
    lines = [
        "# Получить на https://my.telegram.org/apps",
        f"API_ID={settings.api_id.strip()}",
        f"API_HASH={settings.api_hash.strip()}",
        "",
        f"HASHTAG={settings.hashtag.strip() if settings.remember_last_search else ''}",
        f"DOWNLOAD_DIR={_normalize_download_dir_value(settings.download_dir)}",
        f"PAGE_LIMIT={max(1, min(int(settings.page_limit), 100))}",
        f"MAX_POSTS={max(0, int(settings.max_posts))}",
        f"DATE_FROM={settings.date_from.strip() if settings.remember_last_search else ''}",
        f"DATE_TO={settings.date_to.strip() if settings.remember_last_search else ''}",
        f"CHANNEL_FILTER={settings.channel_filter.strip() if settings.remember_last_search else ''}",
        f"SESSION_NAME={settings.session_name.strip() or 'hashtag_session'}",
        "",
        f"PROXY_ENABLED={'true' if settings.proxy_enabled else 'false'}",
        f"PROXY_TYPE={settings.proxy_type or 'socks5'}",
        f"PROXY_HOST={settings.proxy_host or '127.0.0.1'}",
        f"PROXY_PORT={max(1, int(settings.proxy_port))}",
        "",
        f"MEDIA_PHOTO={'true' if settings.media_photo else 'false'}",
        f"MEDIA_VIDEO={'true' if settings.media_video else 'false'}",
        f"MEDIA_ANIMATION={'true' if settings.media_animation else 'false'}",
        f"MEDIA_AUDIO={'true' if settings.media_audio else 'false'}",
        f"MEDIA_DOCUMENT={'true' if settings.media_document else 'false'}",
        f"FOLDER_BY_DATE={'true' if settings.folder_by_date else 'false'}",
        f"CAPTION_IN_FILENAME={'true' if settings.caption_in_filename else 'false'}",
        f"CAPTION_MAX_LEN={max(0, min(int(settings.caption_max_len), 80))}",
        f"DEDUP_BY_HASH={'true' if settings.dedup_by_hash else 'false'}",
        f"DOWNLOAD_RETRIES={max(0, min(int(settings.download_retries), 10))}",
        f"EXTRA_HASHTAGS={settings.extra_hashtags.strip() if settings.remember_last_search else ''}",
        f"EXCLUDE_HASHTAGS={settings.exclude_hashtags.strip() if settings.remember_last_search else ''}",
        f"REQUIRED_HASHTAGS={settings.required_hashtags.strip() if settings.remember_last_search else ''}",
        f"EXTRA_CHANNELS={settings.extra_channels.strip() if settings.remember_last_search else ''}",
        f"DARK_THEME={'true' if settings.dark_theme else 'false'}",
        f"REMEMBER_LAST_SEARCH={'true' if settings.remember_last_search else 'false'}",
        f"SEQUENTIAL_PREVIEW={'true' if settings.sequential_preview else 'false'}",
        f"PREVIEW_BATCH_SIZE={max(20, min(int(settings.preview_batch_size), 1000))}",
        f"PREVIEW_PARALLEL_WORKERS={max(1, min(int(settings.preview_parallel_workers), 6))}",
        f"DOWNLOAD_PARALLEL_WORKERS={max(1, min(int(settings.download_parallel_workers), 3))}",
        f"WIN_NOTIFY_ENABLED={'true' if settings.win_notify_enabled else 'false'}",
        f"WIN_NOTIFY_SUCCESS={'true' if settings.win_notify_success else 'false'}",
        f"WIN_NOTIFY_ERRORS={'true' if settings.win_notify_errors else 'false'}",
        f"SETUP_WIZARD_COMPLETED={'true' if settings.setup_wizard_completed else 'false'}",
        f"UI_LANGUAGE={settings.ui_language or 'system'}",
        "",
    ]
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")


def resolve_download_dir(value: str) -> Path:
    path = Path(_normalize_download_dir_value(value))
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def state_file_for(session_name: str, hashtag: str) -> Path:
    base = session_name.strip() or "hashtag_session"
    tag = safe_name(hashtag) if hashtag.strip() else "default"
    return STATE_DIR / f"{base}_{tag}_state.json"


def build_media_filter(settings: SettingsData) -> MediaFilterSettings:
    return parse_media_filter(
        photo=settings.media_photo,
        video=settings.media_video,
        animation=settings.media_animation,
        audio=settings.media_audio,
        document=settings.media_document,
    )


def build_app_config(
    settings: SettingsData,
    *,
    hashtag: str | None = None,
    channel_filter: str | None = None,
) -> AppConfig:
    api_id = settings.api_id.strip() or os.getenv("API_ID", "")
    api_hash = settings.api_hash.strip() or os.getenv("API_HASH", "")
    if not api_id or not api_hash:
        raise ValueError(tr("errors.validation.api_missing"))

    tag = normalize_hashtag(hashtag or settings.hashtag)

    page_limit = max(1, min(int(settings.page_limit), 100))
    session_name = settings.session_name.strip() or "hashtag_session"
    date_from = parse_date_filter(settings.date_from) if settings.date_from.strip() else None
    date_to = parse_date_filter(settings.date_to) if settings.date_to.strip() else None
    if date_from and date_to and date_from > date_to:
        raise ValueError(tr("errors.validation.date_range"))

    channel = normalize_channel_filter(
        channel_filter if channel_filter is not None else settings.channel_filter,
    )

    return AppConfig(
        api_id=int(api_id),
        api_hash=str(api_hash),
        hashtag=tag,
        download_dir=resolve_download_dir(settings.download_dir),
        page_limit=page_limit,
        max_posts=max(0, int(settings.max_posts)),
        session_name=session_name,
        state_file=state_file_for(session_name, tag),
        date_from=date_from,
        date_to=date_to,
        channel_filter=channel,
        media_filter=build_media_filter(settings),
        folder_by_date=settings.folder_by_date,
        caption_in_filename=settings.caption_in_filename,
        caption_max_len=max(0, min(int(settings.caption_max_len), 80)),
        dedup_by_hash=settings.dedup_by_hash,
        download_retries=max(0, min(int(settings.download_retries), 10)),
        download_parallel_workers=max(1, min(int(settings.download_parallel_workers), 3)),
        exclude_hashtags=tuple(parse_exclude_hashtag_list(settings.exclude_hashtags)),
        required_hashtags=tuple(parse_required_hashtag_list(settings.required_hashtags)),
    )