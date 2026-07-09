"""Download API · Re-export публичного API"""

from __future__ import annotations

from .dl_state import (
    STATE_SAVE_EVERY_N,
    STATE_SAVE_INTERVAL_SEC,
    has_download_journal,
    load_state,
    reset_all_download_states,
    save_state,
)
from .dl_types import (
    AppConfig,
    DownloadStats,
    HashDedupResult,
    IntegrityStats,
    MissingPostRef,
    ProgressCallback,
    ProgressState,
    format_download_summary,
    format_integrity_summary,
    merge_download_stats,
    merge_integrity_stats,
    resolve_integrity_open_dir,
    resolve_summary_open_dir,
)
from .dl_utils import (
    MEDIA_SUFFIXES,
    file_content_sha256,
    media_kind,
    message_key,
    normalize_channel_filter,
    normalize_hashtag,
    parse_bool,
    parse_date_filter,
    safe_name,
)
from .hashtag_downloader import HashtagDownloader

__all__ = [
    "AppConfig",
    "DownloadStats",
    "HashDedupResult",
    "HashtagDownloader",
    "IntegrityStats",
    "MEDIA_SUFFIXES",
    "MissingPostRef",
    "ProgressCallback",
    "ProgressState",
    "STATE_SAVE_EVERY_N",
    "STATE_SAVE_INTERVAL_SEC",
    "file_content_sha256",
    "format_download_summary",
    "format_integrity_summary",
    "has_download_journal",
    "load_state",
    "media_kind",
    "merge_download_stats",
    "merge_integrity_stats",
    "message_key",
    "normalize_channel_filter",
    "normalize_hashtag",
    "parse_bool",
    "parse_date_filter",
    "reset_all_download_states",
    "resolve_integrity_open_dir",
    "resolve_summary_open_dir",
    "safe_name",
    "save_state",
]