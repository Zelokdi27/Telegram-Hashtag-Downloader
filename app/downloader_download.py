from __future__ import annotations

import asyncio
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

class DownloaderDownloadMixin:
    """Download mixin · Mixin скачивания"""

    async def _process_download_candidate(
        self,
        index: int,
        message: Message,
        *,
        total: int,
        stats: DownloadStats,
        known_on_disk: set[str],
        media_counted: set[str],
        pubs_on_disk: set[str],
        media_goal: int,
        pubs: int,
    ) -> None:
        key = message_key(message)
        grouped_id = message.grouped_id
        claim_album = False

        skip, existing_files, reason = await self._check_existing_download(message)
        with self._state_lock:
            if not skip and grouped_id:
                if grouped_id in self._seen_grouped or grouped_id in self._album_in_progress:
                    skip, existing_files, reason = True, [], "album_member"
                else:
                    self._album_in_progress.add(grouped_id)
                    claim_album = True

        try:
            if skip:
                if existing_files:
                    await self._sync_message_state(message, existing_files)
                    if reason == "on_disk":
                        for raw_path in existing_files:
                            known_on_disk.add(str(Path(raw_path).resolve()))
                        pub_key = self._publication_key(message)
                        with self._state_lock:
                            if pub_key not in media_counted:
                                media_counted.add(pub_key)
                            pubs_on_disk.add(pub_key)
                elif reason == "album_member":
                    with self._state_lock:
                        self._mark_processed(key, files=[])
                with self._state_lock:
                    if reason == "album_member":
                        stats.skipped_album += 1
                        progress_label = tr("progress.detail.album_done", index=index, total=total)
                    else:
                        stats.skipped += 1
                        if reason == "on_disk":
                            stats.skipped_on_disk += 1
                            progress_label = tr("progress.detail.skip_on_disk", index=index, total=total)
                        elif reason == "no_media":
                            stats.skipped_no_media += 1
                            progress_label = tr("progress.detail.skip_no_media", index=index, total=total)
                        else:
                            progress_label = tr("progress.detail.skip", index=index, total=total)
                if reason == "on_disk":
                    slots = await self._media_slots_for_unit(message)
                    with self._state_lock:
                        stats.media_accounted += slots
                self._request_save_state()
                self._report_progress(
                    processed=stats.files,
                    skipped=stats.skipped,
                    current=progress_label,
                )
                return

            if reason == "re_download":
                with self._state_lock:
                    stats.redownloaded += 1

            if not message.media:
                with self._state_lock:
                    self._mark_processed(key, files=[])
                    stats.skipped += 1
                    stats.skipped_no_media += 1
                self._request_save_state()
                self._report_progress(processed=index, skipped=stats.skipped)
                return

            try:
                downloaded = await self._download_message_media(message)
                with self._state_lock:
                    stats.files = len(self._session_new_paths)
            except OSError as exc:
                with self._state_lock:
                    stats.errors += 1
                    stats.failed_posts.append(message.id)
                logger.warning(tr("log.download.file_error", id=message.id, exc=exc))
                self._report_progress(
                    processed=index,
                    current=tr("progress.detail.file_error", id=message.id),
                )
                return
            except RPCError as exc:
                with self._state_lock:
                    stats.errors += 1
                    stats.failed_posts.append(message.id)
                notice = format_telegram_error(exc)
                logger.warning(tr("log.download.telegram_error", id=message.id, exc=exc))
                self._report_progress(
                    processed=index,
                    current=tr("progress.detail.post_error", id=message.id, notice=notice),
                    alert=notice,
                )
                return
            except Exception as exc:
                with self._state_lock:
                    stats.errors += 1
                    stats.failed_posts.append(message.id)
                logger.warning(
                    tr(
                        "log.download.post_failed",
                        id=message.id,
                        type=type(exc).__name__,
                        exc=exc,
                    ),
                )
                self._report_progress(
                    processed=index,
                    current=tr("progress.detail.post_error_continue", id=message.id),
                )
                return

            if not downloaded and message.media:
                with self._state_lock:
                    stats.errors += 1
                    stats.failed_posts.append(message.id)
                return

            pub_key = self._publication_key(message)
            with self._state_lock:
                if pub_key not in media_counted:
                    media_counted.add(pub_key)
                self._mark_processed(key, files=downloaded)
                if grouped_id and downloaded:
                    self._seen_grouped.add(grouped_id)
                stats.posts += 1
                stats.media_accounted += len(downloaded)
            await self._disk_index_add_paths(message, downloaded)
            self._request_save_state()
            with self._state_lock:
                files_count = stats.files
                posts_count = stats.posts
                accounted = stats.media_accounted
            self._report_progress(
                processed=accounted,
                files=files_count,
                current=tr(
                    "progress.detail.download_progress",
                    accounted=accounted,
                    goal=media_goal,
                    files=files_count,
                    posts=posts_count,
                    pubs=pubs,
                ),
            )
        finally:
            if claim_album and grouped_id:
                with self._state_lock:
                    self._album_in_progress.discard(grouped_id)

    async def process_messages(
        self,
        messages: list[Message],
        *,
        preview_selection: bool = False,
    ) -> DownloadStats:
        self._duplicate_hits = 0
        self._session_new_paths = set()
        if preview_selection:
            await self._apply_album_caps_from_messages(messages)
        channel_label = ""
        if self.config.channel_filter:
            try:
                entity = await self._resolve_channel_entity()
                channel_label = (
                    getattr(entity, "title", None)
                    or getattr(entity, "username", None)
                    or self.config.channel_filter
                )
            except ValueError:
                channel_label = self.config.channel_filter
        stats = DownloadStats(
            hashtag=self.config.hashtag,
            channel_label=channel_label,
            download_dir=str(self.config.download_dir),
            from_preview=preview_selection,
        )
        skip_channel = bool(self.config.channel_filter) and not preview_selection
        apply_max_media = not preview_selection
        if preview_selection:
            filtered_messages = messages
            excluded_skips = 0
            required_skips = 0
        else:
            filtered_messages, excluded_skips, required_skips = await self._apply_hashtag_filters(
                messages,
            )
        stats.skipped_excluded_hashtag = excluded_skips
        stats.skipped_required_hashtag = required_skips
        stats.skipped = excluded_skips + required_skips
        await self._ensure_coordinators()
        self._report_progress(
            phase="download",
            current=tr("progress.detail.parse_albums"),
        )
        base_candidates = await self._filter_messages(
            filtered_messages,
            skip_channel=skip_channel,
            apply_media_filter=False,
            apply_max_media=False,
        )
        candidates = await self._filter_messages(
            filtered_messages,
            skip_channel=skip_channel,
            apply_media_filter=True,
            apply_max_media=apply_max_media,
        )
        total = len(candidates)
        stats.found = len(messages) if not preview_selection else len(base_candidates)
        stats.total = len(base_candidates)
        stats.with_media = sum(1 for msg in base_candidates if msg.media)
        stats.queue_messages = total
        stats.skipped_media_filter = sum(
            1 for msg in base_candidates if msg.media and not self._media_allowed(msg)
        )
        pubs, media_in_search, albums, singles = await self._publication_stats_from_search(
            candidates,
        )
        stats.publications = pubs
        stats.media_found = media_in_search
        stats.media_expected = 0
        stats.album_publications = albums
        stats.single_publications = singles

        self._bytes_downloaded = 0
        self._download_clock_start = time.monotonic()

        known_on_disk: set[str] = set()
        media_counted: set[str] = set()
        pubs_on_disk: set[str] = set()

        media_goal = media_in_search or total
        self._report_progress(
            phase="download",
            found=len(messages),
            total=media_goal,
            processed=0,
            files=0,
            skipped=excluded_skips,
            current=tr("progress.detail.prep_download"),
        )

        workers = max(1, min(int(self.config.download_parallel_workers), 3))
        if workers > 1:
            logger.info(tr("log.download.parallel_workers", n=workers))
        else:
            logger.info(tr("log.download.sequential"))

        async def _run_candidate(index: int, message: Message) -> None:
            if await self._wait_if_paused() or self._stopped():
                return
            await self._process_download_candidate(
                index,
                message,
                total=total,
                stats=stats,
                known_on_disk=known_on_disk,
                media_counted=media_counted,
                pubs_on_disk=pubs_on_disk,
                media_goal=media_goal,
                pubs=pubs,
            )

        try:
            if workers <= 1:
                for index, message in enumerate(candidates, start=1):
                    if await self._wait_if_paused() or self._stopped():
                        break
                    await self._process_download_candidate(
                        index,
                        message,
                        total=total,
                        stats=stats,
                        known_on_disk=known_on_disk,
                        media_counted=media_counted,
                        pubs_on_disk=pubs_on_disk,
                        media_goal=media_goal,
                        pubs=pubs,
                    )
            else:
                semaphore = asyncio.Semaphore(workers)

                async def _limited(index: int, message: Message) -> None:
                    async with semaphore:
                        await _run_candidate(index, message)

                await asyncio.gather(
                    *(_limited(index, message) for index, message in enumerate(candidates, start=1)),
                )
        finally:
            self._request_save_state(force=True)
            stats.stopped = self._stopped()
            stats.skipped_duplicates = self._duplicate_hits
            stats.skipped += stats.skipped_duplicates
            stats.files = len(self._session_new_paths)
            stats.files_already = len(known_on_disk)
            stats.publications_accounted = len(media_counted)
            stats.publications_on_disk = len(pubs_on_disk)
            stats.files_reused = max(
                0,
                stats.media_accounted - stats.files - stats.skipped_duplicates,
            )
            media_total = stats.media_found or stats.media_expected
            if media_total and stats.media_accounted < media_total:
                stats.media_shortfall = media_total - stats.media_accounted
            if not stats.media_found:
                stats.media_found = media_in_search or total

        self._report_progress(
            phase="done",
            flood_wait_deadline=0.0,
            processed=stats.media_accounted,
            files=stats.files,
            total=media_goal,
            skipped=stats.skipped,
            current=tr("progress.detail.done_new_files", n=stats.files),
        )
        return stats

    async def _download_message_media(self, message: Message) -> list[str]:
        if message.grouped_id:
            files = await self._download_album(message)
            with self._state_lock:
                self._set_grouped_state(
                    str(message.grouped_id),
                    {
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                        "files": files,
                    },
                )
            return files

        return await self._download_single(message)

    async def _messages_in_range(
        self,
        message: Message,
        *,
        span: int = 50,
    ) -> list[Message]:
        started = time.perf_counter()
        try:
            entity = await self._get_peer_entity(message.peer_id)
            peer = utils.get_input_peer(entity)
        except FloodWaitError as exc:
            logger.warning(tr("log.download.channel_flood", id=message.id, sec=exc.seconds))
            await self._register_flood(exc.seconds + 1)
            if not await self._await_telegram_quota():
                return []
            if self._stopped():
                return []
            try:
                peer = await self.client.get_input_entity(message.peer_id)
            except RPCError as retry_exc:
                logger.warning(tr("log.download.channel_retry_failed", id=message.id, exc=retry_exc))
                return []
        except RPCError as exc:
            logger.warning(tr("log.download.channel_failed", id=message.id, exc=exc))
            return []
        except (TypeError, ValueError):
            try:
                peer = await self.client.get_input_entity(message.peer_id)
            except RPCError as exc:
                logger.warning(tr("log.download.channel_failed", id=message.id, exc=exc))
                return []

        anchor = message.id
        try:
            batch = await self._call_with_flood_wait(
                lambda: self.client.get_messages(
                    peer,
                    min_id=max(0, anchor - span),
                    max_id=anchor + span,
                    limit=None,
                ),
            )
        except RPCError as exc:
            logger.warning(tr("log.download.neighbors_failed", id=message.id, exc=exc))
            return []
        if batch is None:
            return []

        if not batch:
            return []
        if not isinstance(batch, list):
            batch = [batch]
        incr("download.neighbor_fetch_calls")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf download.messages_in_range: %.1fms (message_id=%s, span=%s, items=%s)",
                (time.perf_counter() - started) * 1000.0,
                message.id,
                span,
                len(batch),
            )
        return [msg for msg in batch if msg]

    async def fetch_album_messages(self, message: Message, *, grouped_id: int | None = None) -> list[Message]:
        target_group = grouped_id or message.grouped_id
        if not target_group:
            return []
        if target_group in self._album_cache:
            incr("download.album_cache_hits")
            return self._album_cache[target_group]

        await self._ensure_coordinators()
        assert self._album_api_semaphore is not None
        async with self._album_api_semaphore:
            if target_group in self._album_cache:
                return self._album_cache[target_group]

            album: list[Message] = []
            seen_ids: set[int] = set()
            started = time.perf_counter()
            probe_count = 0

            def collect(batch) -> None:
                for msg in batch:
                    if not msg or msg.id in seen_ids:
                        continue
                    if msg.grouped_id == target_group and msg.media:
                        seen_ids.add(msg.id)
                        album.append(msg)

            expected = self._album_slot_caps.get(target_group)
            try:
                collect(await self._messages_in_range(message, span=50))
                probe_count += 1
                if len(album) <= 1:
                    collect(await self._messages_in_range(message, span=100))
                    probe_count += 1
                if expected and len(album) < expected:
                    before = len(album)
                    collect(await self._messages_in_range(message, span=150))
                    probe_count += 1
                    if len(album) < expected:
                        collect(await self._messages_in_range(message, span=250))
                        probe_count += 1
                    if len(album) > before:
                        logger.info(
                            tr(
                                "log.download.album_expanded",
                                id=message.id,
                                n=len(album),
                                expected=expected,
                            ),
                        )
                    elif len(album) < expected:
                        logger.warning(
                            tr(
                                "log.download.album_short",
                                id=message.id,
                                n=len(album),
                                expected=expected,
                            ),
                        )
            except RPCError as exc:
                logger.warning(tr("log.download.album_failed", id=message.id, exc=exc))
                return []

            album.sort(key=lambda item: item.id)
            if album:
                self._album_cache[target_group] = album
            incr("download.album_fetches")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "perf download.fetch_album_messages: %.1fms (message_id=%s, grouped_id=%s, probes=%s, items=%s, expected=%s)",
                    (time.perf_counter() - started) * 1000.0,
                    message.id,
                    target_group,
                    probe_count,
                    len(album),
                    expected or 0,
                )
            return album

    async def resolve_album_messages(self, message: Message) -> list[Message]:
        """Album resolve · Все медиа альбома"""
        if not message.media:
            return []

        if message.grouped_id:
            album = await self.fetch_album_messages(message)
            if len(album) > 1:
                return album

        neighbors = await self._messages_in_range(message, span=50)
        grouped: dict[int, list[Message]] = {}
        for msg in neighbors:
            if msg and msg.grouped_id and msg.media:
                grouped.setdefault(msg.grouped_id, []).append(msg)

        for group_id, group_msgs in grouped.items():
            ids = {item.id for item in group_msgs}
            if message.id in ids:
                album = await self.fetch_album_messages(message, grouped_id=group_id)
                if len(album) > 1:
                    return album
                group_msgs.sort(key=lambda item: item.id)
                allowed = [item for item in group_msgs if self._media_allowed(item)]
                if len(allowed) > 1:
                    return allowed

        if message.grouped_id:
            album = await self.fetch_album_messages(message)
            if album:
                return album

        return [message]

    async def _download_album(self, message: Message) -> list[str]:
        slots = await self._integrity_album_slots(message)
        if not slots:
            return []
        if len(slots) == 1 and slots[0][1] == "":
            return await self._download_single(slots[0][0])

        files: list[str] = []
        for item, suffix in slots:
            existing = await self._item_already_on_disk(item, suffix=suffix)
            if existing:
                files.extend(existing)
                continue
            saved = await self._download_single(item, suffix=suffix)
            files.extend(saved)

        return sorted({str(Path(path).resolve()) for path in files})

    async def _download_single(self, message: Message, suffix: str = "") -> list[str]:
        kind = media_kind(message)
        if not kind or not self._media_allowed(message):
            return []

        existing = await self._item_already_on_disk(message, suffix=suffix)
        if existing:
            return existing

        filename = await self._build_filename(message, suffix=suffix)
        target_dir = await self._channel_dir(message)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename

        self._report_progress(current=tr("progress.detail.downloading_file", filename=filename))

        saved = await self._download_with_retries(message, target)
        if not saved:
            return []

        saved_path = Path(saved)
        dedup = self._resolve_hash_dedup(saved_path)
        if dedup.duplicate_path:
            try:
                saved_path.unlink(missing_ok=True)
            except OSError:
                pass
            with self._state_lock:
                self._duplicate_hits += 1
            logger.info(tr("log.download.hash_duplicate", path=dedup.duplicate_path))
            return [dedup.duplicate_path]

        resolved = str(saved_path.resolve())
        with self._state_lock:
            self._session_new_paths.add(resolved)
        try:
            with self._state_lock:
                self._bytes_downloaded += saved_path.stat().st_size
        except OSError:
            pass
        logger.info(tr("log.download.saved", path=saved))
        await self._disk_index_add_paths(message, [resolved])
        return [resolved]

    async def _download_with_retries(self, message: Message, target: Path) -> str | None:
        attempts = max(1, self.config.download_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            if self._stopped():
                return None
            try:
                while True:
                    try:
                        saved = await self.client.download_media(message, file=str(target))
                        if saved:
                            return str(saved)
                        return None
                    except FloodWaitError as exc:
                        await self._register_flood(exc.seconds + 1)
                        if not await self._await_telegram_quota():
                            return None
                        if self._stopped():
                            return None
            except OSError:
                raise
            except RPCError as exc:
                last_error = exc
                logger.warning(
                    tr(
                        "log.download.retry",
                        attempt=attempt,
                        attempts=attempts,
                        id=message.id,
                        exc=exc,
                    ),
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    tr(
                        "log.download.retry_exc",
                        attempt=attempt,
                        attempts=attempts,
                        id=message.id,
                        type=type(exc).__name__,
                        exc=exc,
                    ),
                )
            if attempt < attempts:
                await self._sleep(min(5, attempt * 2))
        if last_error:
            raise last_error
        return None

    async def channel_label(self, message: Message) -> str:
        return await self._channel_label(message)

    async def _channel_label(self, message: Message) -> str:
        try:
            entity = await self._get_peer_entity(message.peer_id)
        except RPCError:
            return "channel"

        username = getattr(entity, "username", None)
        title = getattr(entity, "title", None)
        if username:
            return username
        if title:
            return title
        return str(getattr(entity, "id", "channel"))

