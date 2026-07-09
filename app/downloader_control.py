from __future__ import annotations

import asyncio
import itertools
import logging
import shutil
import threading
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient, functions, types, utils
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.custom.message import Message

from .config_store import DISK_INDEX_CACHE_DIR
from .disk_index import shared_disk_index_store
from .dl_state import (
    STATE_SAVE_EVERY_N,
    STATE_SAVE_INTERVAL_SEC,
    _DISK_MESSAGE_ID_PATTERN,
    _legacy_state_matches_hashtag,
    load_state,
    save_state,
)
from .state_sqlite import StateDirty
from .state_sqlite import sqlite_path_for
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
from .telegram_errors import format_telegram_error

logger = logging.getLogger(__name__)

class DownloaderControlMixin:
    """Control mixin · Mixin управления"""

    def __init__(
        self,
        client: TelegramClient,
        config: AppConfig,
        should_stop: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.client = client
        self.client.flood_sleep_threshold = 0
        self.config = config
        if not config.state_file.exists():
            legacy = config.state_file.parent / f"{config.session_name}_state.json"
            if _legacy_state_matches_hashtag(legacy, config.hashtag):
                shutil.copy2(legacy, config.state_file)
        self.state = load_state(config.state_file)
        self._seen_grouped: set[int] = set()
        self._should_stop = should_stop or (lambda: False)
        self._should_pause = should_pause or (lambda: False)
        self._on_progress = on_progress
        self._progress = ProgressState()
        self._duplicate_hits = 0
        self._session_new_paths: set[str] = set()
        self._bytes_downloaded = 0
        self._download_clock_start = time.monotonic()
        self._search_media_used = 0
        self._search_seen_grouped: set[int] = set()
        self._partial_album_included: dict[int, set[int]] = {}
        self._album_slot_caps: dict[int, int] = {}
        self._album_take_cache: dict[int, list[Message]] = {}
        self._exclude_album_text_cache: dict[int, str] = {}
        self._album_cache: dict[int, list[Message]] = {}
        self._exclude_tags = {tag.casefold() for tag in config.exclude_hashtags}
        self._required_tags = {tag.casefold() for tag in config.required_hashtags}
        self._disk_index_store = shared_disk_index_store(DISK_INDEX_CACHE_DIR)
        self._sqlite_state_path = sqlite_path_for(config.state_file)
        self._state_lock = threading.RLock()
        self._album_in_progress: set[int] = set()
        self._state_dirty = False
        self._state_dirty_keys = StateDirty()
        self._state_save_force_full = False
        self._state_touch_count = 0
        self._last_state_save_monotonic = 0.0
        self._last_full_state_save_monotonic = time.monotonic()
        self._peer_entity_cache: dict[int, Any] = {}
        self._flood_until: float = 0.0
        self._flood_lock: asyncio.Lock | None = None
        self._album_api_semaphore: asyncio.Semaphore | None = None

    async def _ensure_coordinators(self) -> None:
        if self._flood_lock is None:
            self._flood_lock = asyncio.Lock()
        if self._album_api_semaphore is None:
            self._album_api_semaphore = asyncio.Semaphore(1)

    def _cache_peer_entities(self, entities: dict[int, Any]) -> None:
        if entities:
            self._peer_entity_cache.update(entities)

    def _peer_id_from_entity(self, entity: Any) -> int | None:
        try:
            return utils.get_peer_id(entity)
        except (TypeError, ValueError):
            pass
        channel_id = getattr(entity, "channel_id", None)
        if channel_id is not None:
            return utils.get_peer_id(types.PeerChannel(channel_id=channel_id))
        entity_id = getattr(entity, "id", None)
        if isinstance(entity_id, int):
            return utils.get_peer_id(entity_id)
        return None

    def _cache_peer_entity(self, entity: Any) -> None:
        peer_id = self._peer_id_from_entity(entity)
        if peer_id is not None:
            self._peer_entity_cache[peer_id] = entity

    async def _get_peer_entity(self, peer: Any) -> Any:
        try:
            peer_id = utils.get_peer_id(peer)
        except (TypeError, ValueError):
            return await self.client.get_entity(peer)
        cached = self._peer_entity_cache.get(peer_id)
        if cached is not None:
            return cached
        entity = await self.client.get_entity(peer)
        self._peer_entity_cache[peer_id] = entity
        return entity

    def _stopped(self) -> bool:
        return self._should_stop()

    def _paused(self) -> bool:
        return self._should_pause()

    async def _wait_if_paused(self) -> bool:
        """Pause wait · Ждёт снятия паузы; True — остановка"""
        reported = False
        while self._paused():
            if self._stopped():
                return True
            if not reported:
                self._report_progress(
                    current=tr("progress.detail.paused"),
                )
                reported = True
            await asyncio.sleep(0.4)
        return self._stopped()

    def _report_progress(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self._progress, key, value)
        self._progress.bytes_downloaded = self._bytes_downloaded
        elapsed = time.monotonic() - self._download_clock_start
        if self._bytes_downloaded > 0 and elapsed >= 0.5:
            self._progress.speed_bps = self._bytes_downloaded / elapsed
        if self._on_progress:
            self._on_progress(ProgressState(**self._progress.__dict__))

    async def _register_flood(self, seconds: float) -> None:
        if seconds <= 0:
            return
        await self._ensure_coordinators()
        assert self._flood_lock is not None
        async with self._flood_lock:
            new_deadline = time.monotonic() + seconds
            if new_deadline > self._flood_until:
                self._flood_until = new_deadline
                logger.warning(tr("log.control.flood_wait", sec=int(seconds)))

    async def _await_telegram_quota(self) -> bool:
        """Telegram quota · FloodWait; False — остановка"""
        await self._ensure_coordinators()
        while time.monotonic() < self._flood_until:
            if await self._wait_if_paused() or self._stopped():
                self._report_progress(flood_wait_deadline=0.0)
                return False
            remaining = max(1, int(self._flood_until - time.monotonic()))
            phase = self._progress.phase or "idle"
            self._report_progress(
                phase=phase,
                flood_wait_deadline=self._flood_until,
                current=tr("progress.detail.flood_wait", sec=remaining),
            )
            await asyncio.sleep(min(1.0, max(0.05, self._flood_until - time.monotonic())))
        self._report_progress(flood_wait_deadline=0.0)
        return True

    async def _wait_flood(self, seconds: float) -> None:
        """Flood wait · FloodWait с отсчётом в прогрессе"""
        await self._register_flood(seconds)
        await self._await_telegram_quota()

    async def _call_with_flood_wait(self, action: Callable[[], Awaitable[Any]]) -> Any | None:
        while True:
            if not await self._await_telegram_quota():
                return None
            try:
                return await action()
            except FloodWaitError as exc:
                await self._register_flood(exc.seconds + 1)
                if self._stopped():
                    return None

    def clear_preview_session_caches(self) -> None:
        """Preview cache clear · Сброс кэшей между партиями превью"""
        self._album_cache.clear()
        self._album_take_cache.clear()
        self._exclude_album_text_cache.clear()
        self._album_slot_caps.clear()
        self._partial_album_included.clear()

    async def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if await self._wait_if_paused():
                return
            if self._stopped():
                return
            await asyncio.sleep(min(1.0, deadline - time.monotonic()))

