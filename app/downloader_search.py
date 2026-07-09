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
    STATE_SAVE_EVERY_N,
    STATE_SAVE_INTERVAL_SEC,
    _DISK_MESSAGE_ID_PATTERN,
    _legacy_state_matches_hashtag,
    load_state,
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

logger = logging.getLogger(__name__)

class DownloaderSearchMixin:
    """Search mixin · Mixin поиска"""

    def _begin_search_media_tracking(self) -> None:
        self._search_media_used = 0
        self._search_seen_grouped: set[int] = set()
        self._partial_album_included.clear()
        self._album_slot_caps.clear()
        self._album_take_cache.clear()
        self._search_input_peer_cache: dict[int, types.TypeInputPeer] = {}

    async def _input_peer_for(self, peer: Any) -> types.TypeInputPeer | None:
        try:
            peer_id = utils.get_peer_id(peer)
        except (TypeError, ValueError):
            peer_id = None
        if peer_id is not None:
            cached = self._search_input_peer_cache.get(peer_id)
            if cached is not None:
                incr("search.input_peer_cache_hits")
                return cached
            entity = self._peer_entity_cache.get(peer_id)
            if entity is not None:
                try:
                    cached = utils.get_input_peer(entity)
                except TypeError:
                    cached = None
                if cached is not None:
                    self._search_input_peer_cache[peer_id] = cached
                    incr("search.input_peer_entity_hits")
                    return cached
        started = time.perf_counter()
        resolved = await self._call_with_flood_wait(lambda: self.client.get_input_entity(peer))
        if resolved is not None and peer_id is not None:
            self._search_input_peer_cache[peer_id] = resolved
        incr("search.input_peer_rpc")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf search.resolve_input_peer: %.1fms (peer_id=%s, cached=%s)",
                (time.perf_counter() - started) * 1000.0,
                peer_id,
                False,
            )
        return resolved

    def _report_search_progress(self, *, raw_messages: int, channel: bool = False) -> None:
        limit = self.config.max_posts
        if limit > 0:
            scope = tr("progress.detail.scope_channel") if channel else tr("progress.detail.scope_global")
            self._report_progress(
                phase="search",
                found=self._search_media_used,
                total=limit,
                processed=raw_messages,
                current=tr(
                    "progress.detail.search_scope_media",
                    scope=scope,
                    used=self._search_media_used,
                    limit=limit,
                ),
            )
            return
        scope = tr("progress.detail.scope_channel") if channel else tr("progress.detail.scope_global")
        self._report_progress(
            phase="search",
            found=raw_messages,
            total=0,
            current=tr("progress.detail.search_scope_messages", scope=scope, messages=raw_messages),
        )

    async def _try_collect_search_message(
        self,
        messages: list[Message],
        message: Message,
        *,
        skip_channel: bool = False,
    ) -> bool:
        """Search collect · Добавить сообщение; False — лимит исчерпан"""
        if not self._message_in_date_range(message):
            return True
        if not skip_channel and self.config.channel_filter and not await self._message_matches_channel(message):
            return True

        limit = self.config.max_posts
        if message.grouped_id and message.grouped_id in self._search_seen_grouped:
            included = self._partial_album_included.get(message.grouped_id, set())
            if not included or message.id in included:
                messages.append(message)
            if limit > 0 and self._search_media_used >= limit:
                return False
            return True

        if message.media and self._media_allowed(message):
            take_count, take_msgs = await self._take_messages_for_limit(
                message,
                self._search_media_used,
                limit,
            )
            if take_count > 0:
                if message.grouped_id:
                    self._search_seen_grouped.add(message.grouped_id)
                    self._partial_album_included[message.grouped_id] = {m.id for m in take_msgs}
                self._search_media_used += take_count
                if message.id in {m.id for m in take_msgs}:
                    messages.append(message)
                self._report_search_progress(
                    raw_messages=len(messages),
                    channel=skip_channel or bool(self.config.channel_filter),
                )
                if limit > 0 and self._search_media_used >= limit:
                    return False
                return True

        messages.append(message)
        self._report_search_progress(
            raw_messages=len(messages),
            channel=skip_channel or bool(self.config.channel_filter),
        )
        if limit > 0 and self._search_media_used >= limit:
            return False
        return True

    async def _search_messages(self) -> list[Message]:
        if self.config.channel_filter:
            entity = await self._resolve_channel_entity()
            return await self.search_in_channel(entity)
        return await self.search_all()

    async def _resolve_channel_entity(self):
        filt = self.config.channel_filter
        for candidate in (filt, f"@{filt}", f"https://t.me/{filt}"):
            try:
                entity = await self.client.get_entity(candidate)
                self._cache_peer_entity(entity)
                return entity
            except RPCError:
                continue
        raise ValueError(tr("errors.channel_resolve", name=filt))

    async def search_in_channel(self, entity) -> list[Message]:
        """Channel search · Поиск хештега в канале"""
        self._cache_peer_entity(entity)
        query = f"#{self.config.hashtag}"
        messages: list[Message] = []

        self._begin_search_media_tracking()
        self._report_progress(
            phase="search", found=0, total=self.config.max_posts, current=tr("progress.detail.search_in_channel"),
        )
        logger.info(tr("log.search.in_channel", tag=self.config.hashtag))

        try:
            async for message in self.client.iter_messages(entity, search=query, limit=None):
                if await self._wait_if_paused():
                    break
                if self._stopped():
                    break
                if not await self._try_collect_search_message(messages, message, skip_channel=True):
                    break
        except FloodWaitError as exc:
            logger.warning(tr("log.search.channel_flood", sec=exc.seconds))
            await self._wait_flood(exc.seconds + 1)

        logger.info(
            tr(
                "log.search.channel_done",
                messages=len(messages),
                media=self._search_media_used,
            ),
        )
        self._report_search_progress(raw_messages=len(messages), channel=True)
        return messages

    async def search_all(self) -> list[Message]:
        messages: list[Message] = []
        offset_rate = 0
        offset_peer: types.TypeInputPeer = types.InputPeerEmpty()
        offset_id = 0

        self._begin_search_media_tracking()
        self._report_progress(
            phase="search",
            found=0,
            total=self.config.max_posts,
            processed=0,
            current=tr("progress.detail.search_posts"),
        )

        while True:
            if await self._wait_if_paused():
                break
            if self._stopped():
                break

            page_started = time.perf_counter()
            batch, result = await self._search_page(offset_rate, offset_peer, offset_id)
            if not batch:
                break
            incr("search.page_fetches")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "perf search.page: %.1fms (offset_id=%s, batch=%s)",
                    (time.perf_counter() - page_started) * 1000.0,
                    offset_id,
                    len(batch),
                )

            if self.config.date_from:
                batch = [msg for msg in batch if msg.date.date() >= self.config.date_from]
                if not batch:
                    logger.info(tr("log.search.date_boundary"))
                    break

            stop = False
            for message in batch:
                if not await self._try_collect_search_message(messages, message):
                    stop = True
                    break
            logger.info(
                tr(
                    "log.search.global_found",
                    messages=len(messages),
                    media=self._search_media_used,
                ),
            )
            if stop:
                break

            if len(batch) < self.config.page_limit:
                break

            last = batch[-1]
            if self.config.date_from and last.date.date() < self.config.date_from:
                break
            if isinstance(result, types.messages.MessagesSlice) and result.next_rate:
                offset_rate = result.next_rate
            else:
                offset_rate = int(last.date.timestamp())
            peer = await self._input_peer_for(last.peer_id)
            if peer is None:
                break
            offset_peer = peer
            offset_id = last.id

        return messages

    async def _search_page(
        self,
        offset_rate: int,
        offset_peer: types.TypeInputPeer,
        offset_id: int,
    ) -> tuple[list[Message], types.TypeMessagesMessages | None]:
        while True:
            try:
                result = await self.client(
                    functions.channels.SearchPostsRequest(
                        hashtag=self.config.hashtag,
                        offset_rate=offset_rate,
                        offset_peer=offset_peer,
                        offset_id=offset_id,
                        limit=self.config.page_limit,
                    )
                )
                break
            except FloodWaitError as exc:
                logger.warning(tr("log.search.flood_wait", sec=exc.seconds))
                await self._wait_flood(exc.seconds + 1)
                if self._stopped():
                    return [], None

        if isinstance(result, types.messages.MessagesNotModified):
            return [], result

        entities = {
            utils.get_peer_id(entity): entity
            for entity in itertools.chain(result.chats, result.users)
        }
        self._cache_peer_entities(entities)

        parsed: list[Message] = []
        for raw in result.messages:
            if isinstance(raw, types.MessageEmpty):
                continue
            if isinstance(raw, types.Message):
                raw._finish_init(self.client, entities, None)
                parsed.append(raw)
        return parsed, result

