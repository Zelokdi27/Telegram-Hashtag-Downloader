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
from .telegram_errors import format_telegram_error

logger = logging.getLogger(__name__)

class DownloaderIntegrityMixin:
    """Integrity mixin · Mixin целостности"""

    def _register_integrity_paths(self, paths: list[str], known_paths: set[str]) -> int:
        dedup_hits = 0
        for raw in paths:
            path = str(Path(raw).resolve())
            if path in known_paths:
                dedup_hits += 1
            else:
                known_paths.add(path)
        return dedup_hits

    async def _verify_unit_integrity(
        self,
        message: Message,
        *,
        known_paths: set[str],
    ) -> tuple[bool, list[str], int, int, int]:
        """Unit integrity · Проверка поста; критерий как при скачивании"""
        slots = await self._integrity_album_slots(message)
        expected = len(slots)

        skip, existing_files, reason = await self._check_existing_download(message)
        if skip:
            paths: list[str] = []
            if existing_files and self._all_files_exist(existing_files):
                paths = sorted({str(Path(p).resolve()) for p in existing_files})
            elif reason == "on_disk":
                scanned = await self._scan_disk_for_message(message)
                if scanned:
                    paths = sorted({str(Path(p).resolve()) for p in scanned})
            dedup_hits = self._register_integrity_paths(paths, known_paths)
            # Count on-disk files · Счёт файлов на диске
            stat_expected = len(paths) if paths else 1
            return True, paths, stat_expected, len(paths), dedup_hits

        unit_paths: list[str] = []
        found_count = 0
        dedup_hits = 0
        for item, suffix in slots:
            item_paths = await self._item_already_on_disk(item, suffix=suffix)
            if not item_paths or not self._all_files_exist(item_paths):
                continue
            found_count += 1
            primary = str(Path(item_paths[0]).resolve())
            unit_paths.append(primary)
            if primary in known_paths:
                dedup_hits += 1
            else:
                known_paths.add(primary)

        unique_paths = sorted(set(unit_paths))
        return found_count == expected, unique_paths, expected, found_count, dedup_hits

    async def collect_candidates(self) -> list[Message]:
        messages, _, _ = await self._apply_hashtag_filters(await self._search_messages())
        return await self._filter_messages(
            messages,
            skip_channel=bool(self.config.channel_filter),
            apply_media_filter=True,
        )

    async def verify_integrity(self) -> IntegrityStats:
        channel_label = ""
        if self.config.channel_filter:
            entity = await self._resolve_channel_entity()
            channel_label = (
                getattr(entity, "title", None)
                or getattr(entity, "username", None)
                or self.config.channel_filter
            )

        messages, _, _ = await self._apply_hashtag_filters(await self._search_messages())
        candidates = await self._filter_messages(
            messages,
            skip_channel=bool(self.config.channel_filter),
            apply_media_filter=True,
        )
        with_media = [msg for msg in candidates if msg.media]

        stats = IntegrityStats(
            found=len(candidates),
            with_media=len(with_media),
            hashtag=self.config.hashtag,
            channel_label=channel_label,
            download_dir=str(self.config.download_dir),
        )

        units = self._integrity_units(with_media)
        stats.skipped_album = max(0, len(with_media) - len(units))

        self._report_progress(
            phase="verify",
            found=len(candidates),
            total=len(units),
            processed=0,
            files=0,
            media_total=0,
            skipped=0,
            current=tr("progress.detail.checking_disk"),
        )

        known_paths: set[str] = set()
        units_checked = 0
        for units_checked, message in enumerate(units, start=1):
            if await self._wait_if_paused():
                break
            if self._stopped():
                break
            complete, files, expected, _found, dedup_hits = await self._verify_unit_integrity(
                message,
                known_paths=known_paths,
            )
            stats.media_files += expected
            stats.skipped_dedup += dedup_hits
            if not complete:
                stats.files_missing += max(0, expected - _found)
                stats.missing_post_ids.append(message.id)
                stats.missing_refs.append(
                    MissingPostRef(
                        message_id=message.id,
                        channel=await self._channel_label(message),
                        hashtag=self.config.hashtag,
                        channel_filter=self.config.channel_filter,
                    ),
                )
            stats.files_on_disk = len(known_paths)
            stats.skipped = stats.skipped_dedup + stats.files_missing
            self._report_progress(
                phase="verify",
                found=len(candidates),
                total=len(units),
                processed=units_checked,
                files=stats.files_on_disk,
                media_total=stats.media_files,
                skipped=stats.skipped,
                current=tr("progress.detail.verified_posts", done=units_checked, total=len(units)),
            )

        channel_roots: set[Path] = set()
        for message in with_media:
            channel_roots.add(await self._channel_root_dir(message))

        extra = 0
        for root in channel_roots:
            for path_str in self._all_indexed_files_in_root(root):
                if path_str not in known_paths:
                    extra += 1
        stats.extra_on_disk = extra

        self._report_progress(
            phase="done",
            found=len(candidates),
            total=len(units),
            processed=units_checked if units else 0,
            files=stats.files_on_disk,
            media_total=stats.media_files,
            skipped=stats.skipped,
            current=tr("progress.detail.verify_done"),
        )
        return stats

    async def fetch_missing_messages(self, refs: list[MissingPostRef]) -> list[Message]:
        from collections import defaultdict

        grouped: dict[str, list[int]] = defaultdict(list)
        for ref in refs:
            key = ref.channel_filter.strip() if ref.channel_filter.strip() else ref.channel
            grouped[key].append(ref.message_id)

        messages: list[Message] = []
        for key, ids in grouped.items():
            if self._stopped():
                break
            entity = None
            if self.config.channel_filter:
                try:
                    entity = await self._resolve_channel_entity()
                except ValueError as exc:
                    logger.warning("%s", exc)
                    continue
            else:
                for candidate in (key, f"@{key}", f"https://t.me/{key}"):
                    try:
                        entity = await self.client.get_entity(candidate)
                        break
                    except RPCError:
                        continue
            if entity is None:
                logger.warning(tr("log.integrity.channel_not_found", key=key))
                continue
            unique_ids = list(dict.fromkeys(ids))
            batch = await self.client.get_messages(entity, ids=unique_ids)
            if not isinstance(batch, list):
                batch = [batch]
            messages.extend(msg for msg in batch if msg and msg.media)
        return messages

    async def run_batch(self, hashtags: list[str], channels: list[str]) -> DownloadStats:
        from .hashtag_downloader import HashtagDownloader

        combined = DownloadStats(download_dir=str(self.config.download_dir))
        channel_list = channels or [""]
        total_batches = len(hashtags) * len(channel_list)
        batch_no = 0
        base_progress = self._on_progress

        def _emit_batch_progress(
            *,
            tag: str,
            channel: str,
            index: int,
            phase: str = "search",
            current: str = "",
        ) -> None:
            if not base_progress:
                return
            base_progress(
                ProgressState(
                    phase=phase,
                    current=current or tr("progress.detail.batch_start", tag=tag),
                    batch_index=index,
                    batch_total=total_batches,
                    batch_hashtag=tag,
                    batch_channel=channel,
                ),
            )

        def _wrap_progress(tag: str, channel: str, index: int):
            def callback(state: ProgressState) -> None:
                state.batch_index = index
                state.batch_total = total_batches
                state.batch_hashtag = tag
                state.batch_channel = channel
                if base_progress:
                    base_progress(state)

            return callback

        for tag in hashtags:
            for channel in channel_list:
                batch_no += 1
                if await self._wait_if_paused():
                    combined.stopped = True
                    break
                if self._stopped():
                    combined.stopped = True
                    break
                _emit_batch_progress(tag=tag, channel=channel, index=batch_no)
                batch_config = replace(
                    self.config,
                    hashtag=tag,
                    channel_filter=channel,
                    state_file=self.config.state_file.parent
                    / f"{self.config.session_name}_{safe_name(tag)}_state.json",
                )
                worker = HashtagDownloader(
                    self.client,
                    batch_config,
                    should_stop=self._should_stop,
                    should_pause=self._should_pause,
                    on_progress=_wrap_progress(tag, channel, batch_no),
                )
                batch_stats = await worker.run_once()
                merge_download_stats(combined, batch_stats)
            if self._stopped():
                break
        if combined.batches == 0:
            combined.batches = len(hashtags) * len(channel_list)
        return combined

    async def run_once(self) -> DownloadStats:
        channel_label = ""
        if self.config.channel_filter:
            entity = await self._resolve_channel_entity()
            title = getattr(entity, "title", None) or getattr(entity, "username", "")
            channel_label = title or self.config.channel_filter
            logger.info(
                tr(
                    "log.integrity.channel_mode",
                    tag=self.config.hashtag,
                    title=title,
                    filter=self.config.channel_filter,
                ),
            )
            messages = await self.search_in_channel(entity)
            filtered = await self._filter_messages(messages, skip_channel=True)
        else:
            logger.info(tr("log.integrity.global_search", tag=self.config.hashtag))
            messages = await self.search_all()
            filtered = await self._filter_messages(messages)

        with_media = sum(1 for msg in filtered if msg.media)
        logger.info(
            tr(
                "log.integrity.totals",
                found=len(messages),
                filtered=len(filtered),
                media=with_media,
            ),
        )
        stats = await self.process_messages(messages)
        stats.channel_label = channel_label
        return stats
