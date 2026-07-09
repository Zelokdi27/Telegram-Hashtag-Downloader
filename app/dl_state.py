"""Download state · Состояние скачивания"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config_store import DISK_INDEX_CACHE_DIR, STATE_DIR
from .disk_index import shared_disk_index_store
from .dl_utils import normalize_hashtag, safe_name
from .state_sqlite import (
    StateDirty,
    load_sqlite,
    migrate_json_to_sqlite,
    sqlite_has_processed,
    sqlite_path_for,
    save_sqlite,
)

STATE_SAVE_INTERVAL_SEC = 5.0
STATE_SAVE_EVERY_N = 5
STATE_FULL_SNAPSHOT_INTERVAL_SEC = 600.0
_DISK_MESSAGE_ID_PATTERN = re.compile(
    r"(?<!\d)_(?P<id>\d+)(?:_\d+)?\.[^.]+$",
    re.IGNORECASE,
)


def load_state(path: Path) -> dict[str, Any]:
    sqlite = sqlite_path_for(path)
    if sqlite.exists():
        return load_sqlite(sqlite)
    if path.exists():
        return migrate_json_to_sqlite(path, sqlite)
    return {"processed": {}, "grouped": {}, "hashes": {}}


def save_state(
    path: Path,
    state: dict[str, Any],
    *,
    dirty: StateDirty | None = None,
    full: bool = False,
) -> None:
    save_sqlite(sqlite_path_for(path), state, dirty=dirty, full=full)


def reset_all_download_states(state_dir: Path, session_name: str) -> list[str]:
    """Reset download states · Удалить журналы сессии"""
    base = session_name.strip() or "hashtag_session"
    removed: list[str] = []
    patterns = (
        f"{base}_state.json",
        f"{base}_*_state.json",
        f"{base}_state.sqlite",
        f"{base}_*_state.sqlite",
        f"{base}_state.json.bak",
        f"{base}_*_state.json.bak",
    )
    for pattern in patterns:
        for path in state_dir.glob(pattern):
            path.unlink()
            removed.append(path.name)
    shared_disk_index_store(DISK_INDEX_CACHE_DIR).clear_all()
    return sorted(set(removed))


def has_download_journal(state_dir: Path, session_name: str, hashtag: str) -> bool:
    """Download journal check · Есть ли журнал по хештегу"""
    raw = hashtag.strip()
    if not raw:
        return False
    tag = normalize_hashtag(raw)
    base = session_name.strip() or "hashtag_session"
    primary_json = state_dir / f"{base}_{safe_name(tag)}_state.json"
    primary_sqlite = sqlite_path_for(primary_json)
    if sqlite_has_processed(primary_sqlite):
        return True
    if primary_json.exists() and load_state(primary_json).get("processed"):
        return True
    legacy_json = state_dir / f"{base}_state.json"
    legacy_sqlite = sqlite_path_for(legacy_json)
    if legacy_sqlite.exists() and sqlite_has_processed(legacy_sqlite):
        return True
    if legacy_json.exists() and _legacy_state_matches_hashtag(legacy_json, tag):
        return bool(load_state(legacy_json).get("processed"))
    return False


def _legacy_state_matches_hashtag(legacy_path: Path, hashtag: str) -> bool:
    if not legacy_path.exists():
        return False
    data = load_state(legacy_path)
    processed = data.get("processed", {})
    if not processed:
        return True
    tag_folder = safe_name(hashtag)
    for entry in processed.values():
        for file_path in entry.get("files", []):
            normalized = str(file_path).replace("\\", "/")
            if f"/{tag_folder}/" in normalized or normalized.endswith(f"/{tag_folder}"):
                return True
    return False