from __future__ import annotations

import itertools
import logging
import shutil
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from telethon import functions, types, utils
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.custom.message import Message

from .dl_state import (
    STATE_FULL_SNAPSHOT_INTERVAL_SEC,
    STATE_SAVE_EVERY_N,
    STATE_SAVE_INTERVAL_SEC,
    _DISK_MESSAGE_ID_PATTERN,
    _legacy_state_matches_hashtag,
    load_state,
    save_state,
)
from .disk_index import build_disk_index_snapshot
from .dl_types import (
    AppConfig,
    DownloadStats,
    HashDedupResult,
    IntegrityStats,
    MissingPostRef,
    ProgressCallback,
    ProgressState,
    merge_download_stats,
    merge_integrity_stats,
)
from .dl_utils import (
    MEDIA_SUFFIXES,
    _verify_hash_digest_debug,
    file_content_sha256,
    media_kind,
    message_key,
    safe_name,
)
from .download_options import extract_hashtags_from_text
from .i18n import tr
from .perf_metrics import incr
from .telegram_errors import format_telegram_error
from .state_sqlite import sqlite_add_message_files, sqlite_lookup_message_files, sqlite_path_for, sqlite_remove_missing_files

logger = logging.getLogger(__name__)

class DownloaderStateMixin:
    """State mixin · Mixin состояния"""

    @staticmethod
    def _existing_paths(paths: list[str]) -> tuple[list[str], list[str]]:
        existing: list[str] = []
        missing: list[str] = []
        for path in paths:
            if Path(path).is_file():
                existing.append(path)
            else:
                missing.append(path)
        return existing, missing

    def _ensure_hash_index(self) -> dict[str, str]:
        hashes = self.state.setdefault("hashes", {})
        if not isinstance(hashes, dict):
            hashes = {}
            self.state["hashes"] = hashes
        download_root = self.config.download_dir.resolve()
        changed = False
        for digest, stored in list(hashes.items()):
            if not isinstance(stored, str) or not stored:
                self._remove_hash_entry(str(digest))
                changed = True
                continue
            path = Path(stored)
            if path.is_absolute():
                try:
                    relative = str(path.resolve().relative_to(download_root))
                    self._set_hash_entry(str(digest), relative)
                    changed = True
                except ValueError:
                    pass
        if changed:
            self._request_save_state(force=True)
        return hashes

    def _store_hash_path(self, file_path: Path) -> str:
        resolved = file_path.resolve()
        try:
            return str(resolved.relative_to(self.config.download_dir.resolve()))
        except ValueError:
            return str(resolved)

    def _resolve_hash_path(self, stored: str) -> Path:
        path = Path(stored)
        if path.is_absolute():
            return path
        return (self.config.download_dir / path).resolve()

    def _resolve_hash_dedup(self, file_path: Path) -> HashDedupResult:
        """Hash dedup · SHA-256 и регистрация в журнале"""
        if not self.config.dedup_by_hash or not file_path.is_file():
            return HashDedupResult(None, None, False)
        digest = file_content_sha256(file_path)
        if digest is None:
            return HashDedupResult(None, None, False)
        _verify_hash_digest_debug(file_path, digest)
        index = self._ensure_hash_index()
        existing = index.get(digest)
        if existing:
            resolved = self._resolve_hash_path(existing)
            if resolved.is_file() and resolved.resolve() != file_path.resolve():
                return HashDedupResult(digest, str(resolved), False)
        self._set_hash_entry(digest, self._store_hash_path(file_path))
        return HashDedupResult(digest, None, True)

    @staticmethod
    def _all_files_exist(paths: list[str]) -> bool:
        return bool(paths) and all(Path(path).is_file() for path in paths)

    def _files_from_state(self, message: Message) -> list[str]:
        if message.grouped_id:
            group = self.state.get("grouped", {}).get(str(message.grouped_id))
            if group:
                return list(group.get("files") or [])
        entry = self.state.get("processed", {}).get(message_key(message))
        if entry:
            return list(entry.get("files") or [])
        return []

    def _build_disk_index(self, root: Path) -> dict[int, list[str]]:
        return self._build_disk_index_snapshot(root).index

    def _build_disk_index_snapshot(self, root: Path):
        return build_disk_index_snapshot(root, pattern=_DISK_MESSAGE_ID_PATTERN)

    def _disk_index_for_root(self, root: Path) -> dict[int, list[str]]:
        resolved_root = root.resolve()
        return self._disk_index_store.get_index(
            resolved_root,
            pattern=_DISK_MESSAGE_ID_PATTERN,
            builder=lambda: self._build_disk_index_snapshot(resolved_root),
        )

    async def _disk_index_for_message(self, message: Message) -> dict[int, list[str]]:
        return self._disk_index_for_root(await self._channel_root_dir(message))

    async def _disk_index_add_paths(self, message: Message, paths: list[str]) -> None:
        if not paths:
            return
        root = (await self._channel_root_dir(message)).resolve()
        self._disk_index_store.add_paths(root, message.id, paths)
        sqlite_add_message_files(
            self._sqlite_state_path,
            root=str(root),
            message_id=message.id,
            paths=[str(Path(path).resolve()) for path in paths],
        )

    def _all_indexed_files_in_root(self, root: Path) -> set[str]:
        index = self._disk_index_for_root(root)
        files: set[str] = set()
        for paths in index.values():
            for path in paths:
                if Path(path).is_file():
                    files.add(path)
        return files

    async def _scan_disk_for_message(self, message: Message) -> list[str]:
        """Disk scan · Поиск файла по id в индексе канала"""
        root = (await self._channel_root_dir(message)).resolve()
        started = time.perf_counter()
        sqlite_hits = sqlite_lookup_message_files(
            self._sqlite_state_path,
            root=str(root),
            message_id=message.id,
        )
        existing_sqlite_hits, missing_sqlite_hits = self._existing_paths(sqlite_hits)
        if missing_sqlite_hits:
            sqlite_remove_missing_files(
                self._sqlite_state_path,
                paths=missing_sqlite_hits,
            )
        if existing_sqlite_hits:
            self._disk_index_store.add_paths(root, message.id, existing_sqlite_hits)
            incr("state.sqlite_scan_hits")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "perf state.scan_disk_for_message: %.1fms (source=sqlite, message_id=%s, hits=%s)",
                    (time.perf_counter() - started) * 1000.0,
                    message.id,
                    len(existing_sqlite_hits),
                )
            return sorted({str(Path(path).resolve()) for path in existing_sqlite_hits})
        indexed = list(self._disk_index_for_root(root).get(message.id, []))
        result, missing_indexed = self._existing_paths(indexed)
        if missing_indexed:
            sqlite_remove_missing_files(
                self._sqlite_state_path,
                paths=missing_indexed,
            )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf state.scan_disk_for_message: %.1fms (source=index, message_id=%s, hits=%s, missing=%s)",
                (time.perf_counter() - started) * 1000.0,
                message.id,
                len(result),
                len(missing_indexed),
            )
        return result

    async def _item_already_on_disk_with_root(
        self,
        message: Message,
        *,
        root: Path,
        suffix: str = "",
    ) -> list[str]:
        started = time.perf_counter()
        scanned = list(self._disk_index_for_root(root).get(message.id, []))
        if suffix:
            suffix_token = suffix.lstrip("_")
            scanned = [path for path in scanned if f"_{suffix_token}." in Path(path).name]
        existing, missing = self._existing_paths(scanned)
        if missing:
            sqlite_remove_missing_files(
                self._sqlite_state_path,
                paths=missing,
            )
        if existing:
            incr("state.disk_index_hits")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "perf state.item_already_on_disk: %.1fms (source=index, message_id=%s, hits=%s, suffix=%s)",
                    (time.perf_counter() - started) * 1000.0,
                    message.id,
                    len(existing),
                    suffix,
                )
            return existing
        sqlite_hits = sqlite_lookup_message_files(
            self._sqlite_state_path,
            root=str(root),
            message_id=message.id,
        )
        if suffix:
            suffix_token = suffix.lstrip("_")
            sqlite_hits = [path for path in sqlite_hits if f"_{suffix_token}." in Path(path).name]
        existing_sqlite, missing_sqlite = self._existing_paths(sqlite_hits)
        if missing_sqlite:
            sqlite_remove_missing_files(
                self._sqlite_state_path,
                paths=missing_sqlite,
            )
        if existing_sqlite:
            self._disk_index_store.add_paths(root, message.id, existing_sqlite)
            incr("state.sqlite_fallback_hits")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf state.item_already_on_disk: %.1fms (source=%s, message_id=%s, hits=%s, suffix=%s)",
                (time.perf_counter() - started) * 1000.0,
                "sqlite" if existing_sqlite else "miss",
                message.id,
                len(existing_sqlite),
                suffix,
            )
        return sorted({str(Path(path).resolve()) for path in existing_sqlite})

    def _set_hash_entry(self, digest: str, path: str) -> None:
        hashes = self.state.setdefault("hashes", {})
        hashes[str(digest)] = path
        self._state_dirty_keys.hashes.add(str(digest))
        self._state_dirty_keys.deleted_hashes.discard(str(digest))

    def _remove_hash_entry(self, digest: str) -> None:
        digest_key = str(digest)
        hashes = self.state.setdefault("hashes", {})
        hashes.pop(digest_key, None)
        self._state_dirty_keys.deleted_hashes.add(digest_key)
        self._state_dirty_keys.hashes.discard(digest_key)

    def _set_grouped_state(self, gid: str, entry: dict[str, Any]) -> None:
        grouped = self.state.setdefault("grouped", {})
        grouped[str(gid)] = entry
        gid_key = str(gid)
        self._state_dirty_keys.grouped.add(gid_key)
        self._state_dirty_keys.deleted_grouped.discard(gid_key)

    def _request_save_state(self, *, force: bool = False) -> None:
        with self._state_lock:
            self._state_dirty = True
            self._state_touch_count += 1
            if force:
                self._state_save_force_full = True
                self._flush_state_unlocked()
                return
            now = time.monotonic()
            if self._state_touch_count >= STATE_SAVE_EVERY_N:
                self._flush_state_unlocked()
                return
            if now - self._last_state_save_monotonic >= STATE_SAVE_INTERVAL_SEC:
                self._flush_state_unlocked()

    def _flush_state(self) -> None:
        with self._state_lock:
            self._flush_state_unlocked()

    def _flush_state_unlocked(self) -> None:
        if not self._state_dirty:
            return
        now = time.monotonic()
        use_full = (
            self._state_save_force_full
            or now - self._last_full_state_save_monotonic >= STATE_FULL_SNAPSHOT_INTERVAL_SEC
            or not self._state_dirty_keys.has_changes()
        )
        save_state(
            self.config.state_file,
            self.state,
            dirty=self._state_dirty_keys,
            full=use_full,
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf state.flush: full=%s dirty_processed=%s dirty_grouped=%s dirty_hashes=%s",
                use_full,
                len(self._state_dirty_keys.processed),
                len(self._state_dirty_keys.grouped),
                len(self._state_dirty_keys.hashes),
            )
        if use_full:
            self._last_full_state_save_monotonic = now
        self._state_dirty_keys.clear()
        self._state_dirty = False
        self._state_save_force_full = False
        self._state_touch_count = 0
        self._last_state_save_monotonic = now

    async def _item_already_on_disk(self, message: Message, suffix: str = "") -> list[str]:
        root = (await self._channel_root_dir(message)).resolve()
        return await self._item_already_on_disk_with_root(message, root=root, suffix=suffix)

    async def _sync_message_state(self, message: Message, files: list[str]) -> None:
        await self._disk_index_add_paths(message, files)
        with self._state_lock:
            self._mark_processed(message_key(message), files=files)
            if message.grouped_id:
                self._seen_grouped.add(message.grouped_id)
                gid = str(message.grouped_id)
                existing = list(self.state.get("grouped", {}).get(gid, {}).get("files") or [])
                merged = sorted({*existing, *files})
                self._set_grouped_state(
                    gid,
                    {
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                        "files": merged,
                    },
                )

    async def _publication_complete_on_disk(self, message: Message) -> tuple[bool, list[str]]:
        """Publication on disk · True если все слоты на диске"""
        slots = await self._integrity_album_slots(message)
        if not slots:
            return False, []
        paths: list[str] = []
        for item, suffix in slots:
            found = await self._item_already_on_disk(item, suffix=suffix)
            if not found:
                return False, []
            paths.extend(found)
        return True, sorted({str(Path(p).resolve()) for p in paths})

    async def _check_existing_download(self, message: Message) -> tuple[bool, list[str], str]:
        """Existing download · Пропуск поста; (skip, paths, reason)"""
        key = message_key(message)

        if message.grouped_id and (
            message.grouped_id in self._seen_grouped
            or message.grouped_id in self._album_in_progress
        ):
            return True, [], "album_member"

        if not message.media:
            if key in self.state["processed"]:
                return True, [], "no_media"
            return False, [], "new"

        slots = await self._integrity_album_slots(message)
        if len(slots) > 1:
            recorded = self._files_from_state(message)
            if recorded and len(recorded) >= len(slots) and self._all_files_exist(recorded):
                return True, recorded, "on_disk"
            complete, paths = await self._publication_complete_on_disk(message)
            if complete:
                return True, paths, "on_disk"
            if recorded and self._all_files_exist(recorded):
                logger.info(
                    tr(
                        "log.download.album_redownload",
                        on_disk=len(recorded),
                        total=len(slots),
                        id=message.id,
                    ),
                )
                return False, [], "re_download"
            return False, [], "new"

        recorded = self._files_from_state(message)
        if recorded and self._all_files_exist(recorded):
            return True, recorded, "on_disk"

        scanned = await self._scan_disk_for_message(message)
        if scanned:
            return True, scanned, "on_disk"

        if recorded or key in self.state["processed"] or (
            message.grouped_id and str(message.grouped_id) in self.state["grouped"]
        ):
            logger.info(tr("log.download.redownload", id=message.id))
            return False, [], "re_download"

        return False, [], "new"

    def _mark_processed(self, key: str, files: list[str]) -> None:
        key_text = str(key)
        self.state["processed"][key_text] = {
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }
        self._state_dirty_keys.processed.add(key_text)
        self._state_dirty_keys.deleted_processed.discard(key_text)

