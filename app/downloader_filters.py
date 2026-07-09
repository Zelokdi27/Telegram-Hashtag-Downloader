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
from .telegram_errors import format_telegram_error

logger = logging.getLogger(__name__)

class DownloaderFiltersMixin:
    """Filters mixin · Mixin фильтров"""

    def _message_in_date_range(self, message: Message) -> bool:
        msg_date = message.date.date()
        if self.config.date_from and msg_date < self.config.date_from:
            return False
        if self.config.date_to and msg_date > self.config.date_to:
            return False
        return True

    async def _message_matches_channel(self, message: Message) -> bool:
        filt = self.config.channel_filter
        if not filt:
            return True
        try:
            entity = await self._get_peer_entity(message.peer_id)
        except RPCError:
            return False

        username = (getattr(entity, "username", None) or "").lower()
        title = getattr(entity, "title", None) or ""
        entity_id = str(getattr(entity, "id", ""))

        if filt in {username, entity_id, safe_name(title).lower()}:
            return True
        if title and filt == title.lower():
            return True
        if title and filt in title.lower():
            return True
        return False

    async def _channel_dir(self, message: Message) -> Path:
        channel = safe_name(await self._channel_label(message))
        base = self.config.download_dir / safe_name(self.config.hashtag) / channel
        if self.config.folder_by_date:
            return base / message.date.strftime("%Y-%m")
        return base

    async def _channel_root_dir(self, message: Message) -> Path:
        channel = safe_name(await self._channel_label(message))
        return self.config.download_dir / safe_name(self.config.hashtag) / channel

    def _media_allowed(self, message: Message) -> bool:
        return self.config.media_filter.allows(media_kind(message))

    def _caption_part(self, message: Message) -> str:
        if not self.config.caption_in_filename or self.config.caption_max_len <= 0:
            return ""
        text = (getattr(message, "message", None) or getattr(message, "text", None) or "").strip()
        if not text:
            return ""
        return safe_name(text, max_len=self.config.caption_max_len)

    async def _build_filename(self, message: Message, suffix: str = "") -> str:
        kind = media_kind(message) or "bin"
        channel = safe_name(await self._channel_label(message))
        date_part = message.date.strftime("%Y-%m-%d_%H-%M-%S")
        caption = self._caption_part(message)
        caption_bit = f"_{caption}" if caption else ""
        ext = MEDIA_SUFFIXES.get(kind, "bin")
        stem = safe_name(
            f"{date_part}_{channel}_{message.id}{caption_bit}{suffix}",
            max_len=120,
        )
        return f"{stem}.{ext}"

    @staticmethod
    def _message_caption(message: Message) -> str:
        return (getattr(message, "message", None) or getattr(message, "text", None) or "").strip()

    async def _album_text_blob(self, message: Message) -> str:
        grouped_id = message.grouped_id
        if not grouped_id:
            return self._message_caption(message)
        if grouped_id in self._exclude_album_text_cache:
            return self._exclude_album_text_cache[grouped_id]

        own = self._message_caption(message)
        if own:
            self._exclude_album_text_cache[grouped_id] = own
            return own

        if self._stopped():
            return ""

        parts: list[str] = []
        for item in await self.fetch_album_messages(message):
            if self._stopped():
                break
            text = self._message_caption(item)
            if text:
                parts.append(text)
        blob = "\n".join(parts)
        self._exclude_album_text_cache[grouped_id] = blob
        return blob

    def _text_has_excluded_hashtag(self, text: str) -> bool:
        if not self._exclude_tags or not text:
            return False
        found = extract_hashtags_from_text(text)
        return bool(found & self._exclude_tags)

    async def _message_has_excluded_hashtag(self, message: Message) -> bool:
        if not self._exclude_tags:
            return False
        caption = self._message_caption(message)
        if caption and self._text_has_excluded_hashtag(caption):
            return True
        if message.grouped_id:
            return self._text_has_excluded_hashtag(await self._album_text_blob(message))
        return False

    def _text_has_required_hashtag(self, text: str) -> bool:
        if not self._required_tags:
            return True
        if not text:
            return False
        found = extract_hashtags_from_text(text)
        return bool(found & self._required_tags)

    async def _message_has_required_hashtag(self, message: Message) -> bool:
        if not self._required_tags:
            return True
        caption = self._message_caption(message)
        if caption and self._text_has_required_hashtag(caption):
            return True
        if message.grouped_id:
            return self._text_has_required_hashtag(await self._album_text_blob(message))
        return False

    async def _apply_hashtag_filters(
        self,
        messages: list[Message],
    ) -> tuple[list[Message], int, int]:
        if not self._exclude_tags and not self._required_tags:
            return messages, 0, 0

        excluded_groups: set[int] = set()
        required_missing_groups: set[int] = set()
        included_groups: set[int] = set()
        kept: list[Message] = []
        excluded_skips = 0
        required_skips = 0

        for message in messages:
            if self._stopped():
                break
            grouped_id = message.grouped_id
            if grouped_id:
                if grouped_id in excluded_groups or grouped_id in required_missing_groups:
                    continue
                if grouped_id in included_groups:
                    kept.append(message)
                    continue
                if await self._message_has_excluded_hashtag(message):
                    excluded_groups.add(grouped_id)
                    excluded_skips += 1
                    continue
                if not await self._message_has_required_hashtag(message):
                    required_missing_groups.add(grouped_id)
                    required_skips += 1
                    continue
                included_groups.add(grouped_id)
                kept.append(message)
                continue

            if await self._message_has_excluded_hashtag(message):
                excluded_skips += 1
                continue
            if not await self._message_has_required_hashtag(message):
                required_skips += 1
                continue
            kept.append(message)

        return kept, excluded_skips, required_skips

    async def _count_media_files(self, messages: list[Message]) -> int:
        total = 0
        seen_grouped: set[int] = set()
        for msg in messages:
            if not msg.media:
                continue
            if msg.grouped_id:
                if msg.grouped_id in seen_grouped:
                    continue
                seen_grouped.add(msg.grouped_id)
            total += await self._media_slots_for_unit(msg)
        return total

    async def _limit_by_media_files(self, messages: list[Message], limit: int) -> list[Message]:
        if limit <= 0:
            return messages
        self._album_slot_caps.clear()
        # Album cache reuse · Кэш альбомов с поиска
        self._album_take_cache.clear()
        result: list[Message] = []
        media_used = 0
        seen_grouped: set[int] = set()
        partial_included: dict[int, set[int]] = {}
        for msg in messages:
            if msg.grouped_id:
                if msg.grouped_id in seen_grouped:
                    if msg.id in partial_included.get(msg.grouped_id, set()):
                        result.append(msg)
                    continue
                take_count, take_msgs = await self._take_messages_for_limit(msg, media_used, limit)
                seen_grouped.add(msg.grouped_id)
                if take_count <= 0:
                    continue
                partial_included[msg.grouped_id] = {m.id for m in take_msgs}
                media_used += take_count
                if msg.id in partial_included[msg.grouped_id]:
                    result.append(msg)
                continue
            take_count, take_msgs = await self._take_messages_for_limit(msg, media_used, limit)
            if take_count <= 0:
                if not msg.media:
                    result.append(msg)
                continue
            media_used += take_count
            result.append(msg)
        return result

    async def _apply_album_caps_from_messages(self, messages: list[Message]) -> None:
        """Album caps · Частичное скачивание альбома в превью"""
        by_group: dict[int, list[Message]] = {}
        for msg in messages:
            if msg.grouped_id and msg.media:
                by_group.setdefault(msg.grouped_id, []).append(msg)
        for gid, msgs in by_group.items():
            rep = msgs[0]
            allowed = await self._album_allowed_messages(rep)
            if len(msgs) >= len(allowed):
                continue
            selected_ids = {m.id for m in msgs}
            prefix_len = 0
            for item in allowed:
                if item.id in selected_ids:
                    prefix_len += 1
                else:
                    break
            if prefix_len > 0:
                self._album_slot_caps[gid] = prefix_len

    async def _filter_messages(
        self,
        messages: list[Message],
        *,
        skip_channel: bool = False,
        apply_media_filter: bool = True,
        apply_max_media: bool = True,
    ) -> list[Message]:
        filtered = [msg for msg in messages if self._message_in_date_range(msg)]
        if not skip_channel and self.config.channel_filter:
            channel_filtered: list[Message] = []
            for msg in filtered:
                if await self._message_matches_channel(msg):
                    channel_filtered.append(msg)
            filtered = channel_filtered
        if apply_media_filter:
            filtered = [
                msg
                for msg in filtered
                if not msg.media or self._media_allowed(msg)
            ]
        if apply_max_media and apply_media_filter and self.config.max_posts > 0:
            filtered = await self._limit_by_media_files(filtered, self.config.max_posts)
        return filtered

    async def _album_allowed_messages(self, message: Message) -> list[Message]:
        if not message.media:
            return []
        album = await self.resolve_album_messages(message)
        allowed = [item for item in album if self._media_allowed(item)]
        if len(allowed) <= 1:
            return [message] if self._media_allowed(message) else []
        return allowed

    async def _take_messages_for_limit(
        self,
        message: Message,
        media_used: int,
        limit: int,
    ) -> tuple[int, list[Message]]:
        """Message limit · Лимит медиа из публикации"""
        if limit <= 0:
            if message.grouped_id:
                gid = message.grouped_id
                if gid in self._album_take_cache:
                    take = self._album_take_cache[gid]
                    return len(take), take
                allowed = await self._album_allowed_messages(message)
                if allowed:
                    self._album_take_cache[gid] = allowed
                return len(allowed), allowed
            if message.media and self._media_allowed(message):
                return 1, [message]
            return 0, []

        remaining = limit - media_used
        if remaining <= 0:
            return 0, []

        if message.grouped_id:
            gid = message.grouped_id
            if gid in self._album_take_cache:
                take = self._album_take_cache[gid]
                return len(take), take
            allowed = await self._album_allowed_messages(message)
            if not allowed:
                return 0, []
            take = allowed[:remaining]
            self._album_take_cache[gid] = take
            if len(take) < len(allowed):
                self._album_slot_caps[gid] = len(take)
            return len(take), take

        if not message.media or not self._media_allowed(message):
            return 0, []
        return 1, [message]

    async def _integrity_album_slots(self, message: Message) -> list[tuple[Message, str]]:
        allowed = await self._album_allowed_messages(message)
        if not allowed:
            return []
        if len(allowed) <= 1:
            return [(allowed[0], "")]
        cap = self._album_slot_caps.get(message.grouped_id) if message.grouped_id else None
        if cap is not None:
            allowed = allowed[:cap]
        return [(item, f"_{index}") for index, item in enumerate(allowed, start=1)]

    @staticmethod
    def _integrity_units(with_media: list[Message]) -> list[Message]:
        seen_grouped: set[int] = set()
        units: list[Message] = []
        for message in with_media:
            if message.grouped_id:
                if message.grouped_id in seen_grouped:
                    continue
                seen_grouped.add(message.grouped_id)
            units.append(message)
        return units

    @staticmethod
    def _publication_key(message: Message) -> str:
        if message.grouped_id:
            return f"g:{message.grouped_id}"
        return f"m:{message_key(message)}"

    def _cached_media_slots_for_unit(self, message: Message) -> int | None:
        if not message.media or not self._media_allowed(message):
            return 0
        if message.grouped_id:
            cap = self._album_slot_caps.get(message.grouped_id)
            if cap is not None:
                return cap
            take = self._album_take_cache.get(message.grouped_id)
            if take:
                return len(take)
            cached_album = self._album_cache.get(message.grouped_id)
            if cached_album:
                allowed = [item for item in cached_album if self._media_allowed(item)]
                cap = self._album_slot_caps.get(message.grouped_id)
                if cap is not None:
                    return min(cap, len(allowed))
                return len(allowed) or None
        return 1

    async def _media_slots_for_unit(self, message: Message) -> int:
        cached = self._cached_media_slots_for_unit(message)
        if cached is not None:
            return cached
        return len(await self._integrity_album_slots(message))

    async def _publication_stats_from_search(
        self,
        messages: list[Message],
    ) -> tuple[int, int, int, int]:
        """Publication stats · Публикации и медиа с альбомами"""
        with_media = [msg for msg in messages if msg.media]
        units = self._integrity_units(with_media)
        media_visible = 0
        albums = 0
        singles = 0
        for message in units:
            slots = self._cached_media_slots_for_unit(message)
            if slots is None:
                slots = await self._media_slots_for_unit(message)
            if message.grouped_id:
                albums += 1
            else:
                singles += 1
            media_visible += slots
        return len(units), media_visible, albums, singles

    async def _message_files_on_disk(self, message: Message) -> list[str]:
        """Disk file lookup · Поиск файла как при скачивании"""
        recorded = self._files_from_state(message)
        if recorded and self._all_files_exist(recorded):
            return recorded
        return await self._scan_disk_for_message(message)

