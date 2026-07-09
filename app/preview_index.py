"""Preview index · Индекс публикаций для пошагового превью"""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from dataclasses import dataclass

from telethon import types
from telethon.errors import RPCError
from telethon.tl.custom.message import Message

from .i18n import tr
from .preview_core import (
    PreviewDiskStatusResolver,
    PreviewDuplicateTracker,
    PreviewItem,
    PreviewThumbPipeline,
    _album_index_from_search,
    _pause_between_album_fetches,
    _preview_aborted,
    _unwrap_worker,
    _wait_if_paused,
    build_preview_item,
    emit_preview_album,
    estimate_media_total,
)
from .tg_hashtag_dl import HashtagDownloader, media_kind

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class PreviewIndexEntry:
    message_id: int
    hashtag: str
    channel_filter: str
    channel_id: int
    grouped_id: int | None
    media_kind: str


@dataclass
class PreviewIndexSummary:
    publications_total: int
    album_groups: int
    media_estimate: int

    def estimated_batches(self, batch_size: int, *, media_limit: int = 0) -> int:
        return sequential_batch_total(self, batch_size=batch_size, media_limit=media_limit)


def sequential_media_cap(summary: PreviewIndexSummary, media_limit: int = 0) -> int:
    """Sequential media cap · Верхняя граница медиа в превью"""
    if media_limit > 0:
        return media_limit
    return max(0, summary.media_estimate)


def sequential_batch_total(
    summary: PreviewIndexSummary,
    *,
    batch_size: int,
    media_limit: int = 0,
    batch_number: int = 0,
) -> int:
    size = max(1, batch_size)
    target = sequential_media_cap(summary, media_limit)
    planned = max(1, (target + size - 1) // size)
    if batch_number > 0:
        return max(planned, batch_number)
    return planned


def _peer_channel_id(message: Message) -> int:
    peer = message.peer_id
    if isinstance(peer, types.PeerChannel):
        return peer.channel_id
    channel_id = getattr(peer, "channel_id", None)
    if isinstance(channel_id, int):
        return channel_id
    raise ValueError(tr("errors.preview_channel_unknown", id=message.id))


def build_preview_index(
    messages: list[Message],
    *,
    hashtag: str,
    channel_filter: str,
) -> tuple[list[PreviewIndexEntry], PreviewIndexSummary]:
    entries: list[PreviewIndexEntry] = []
    for message in messages:
        if not message.media:
            continue
        entries.append(
            PreviewIndexEntry(
                message_id=message.id,
                hashtag=hashtag,
                channel_filter=channel_filter,
                channel_id=_peer_channel_id(message),
                grouped_id=message.grouped_id or None,
                media_kind=media_kind(message) or "media",
            ),
        )
    search_albums = _album_index_from_search(messages)
    summary = PreviewIndexSummary(
        publications_total=len(entries),
        album_groups=sum(1 for group in search_albums.values() if group),
        media_estimate=estimate_media_total(messages, search_albums),
    )
    return entries, summary


def merge_preview_summaries(parts: list[PreviewIndexSummary]) -> PreviewIndexSummary:
    return PreviewIndexSummary(
        publications_total=sum(part.publications_total for part in parts),
        album_groups=sum(part.album_groups for part in parts),
        media_estimate=sum(part.media_estimate for part in parts),
    )


def format_sequential_index_status(
    summary: PreviewIndexSummary,
    *,
    batch_number: int,
    publication_cursor: int,
    batch_size: int,
    files_downloaded: int = 0,
    media_shown: int = 0,
    media_limit: int = 0,
) -> str:
    batches = sequential_batch_total(
        summary,
        batch_size=batch_size,
        media_limit=media_limit,
        batch_number=batch_number,
    )
    batch_label = (
        tr("sequential.batch_approx", n=batch_number, total=batches)
        if summary.album_groups > 0 and media_limit <= 0
        else tr("sequential.batch_exact", n=batch_number, total=batches)
    )

    if media_limit > 0:
        media_part = tr("sequential.media_limit", shown=media_shown, limit=media_limit)
    elif summary.album_groups > 0:
        media_part = tr(
            "sequential.media_at_least",
            shown=media_shown,
            estimate=summary.media_estimate,
        )
    else:
        media_part = tr(
            "sequential.media_estimate",
            shown=media_shown,
            estimate=summary.media_estimate,
        )

    parts = [
        tr("sequential.title", batch=batch_label),
        media_part,
        tr(
            "sequential.publications",
            cursor=publication_cursor,
            total=summary.publications_total,
        ),
    ]
    if files_downloaded:
        parts.append(tr("sequential.downloaded", n=files_downloaded))
    return " · ".join(parts)


async def fetch_index_entry_messages(
    worker: HashtagDownloader,
    entries: list[PreviewIndexEntry],
) -> dict[int, Message]:
    if not entries:
        return {}
    try:
        entity = await worker.client.get_entity(types.PeerChannel(channel_id=entries[0].channel_id))
        batch = await worker.client.get_messages(entity, ids=[entry.message_id for entry in entries])
    except RPCError as exc:
        for entry in entries:
            logger.warning(
                tr(
                    "log.preview_index.fetch_failed",
                    id=entry.message_id,
                    tag=entry.hashtag,
                    exc=exc,
                ),
            )
        return {}
    if not isinstance(batch, list):
        batch = [batch]
    return {
        message.id: message
        for message in batch
        if message is not None and getattr(message, "media", None)
    }


async def collect_sequential_preview_batch(
    worker: HashtagDownloader,
    index: list[PreviewIndexEntry],
    start: int,
    *,
    batch_media_size: int,
    hashtag: str = "",
    channel_filter: str = "",
    item_queue: queue.Queue[PreviewItem | None] | None = None,
    thumb_pipeline: PreviewThumbPipeline | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    worker_resolver: Callable[[PreviewIndexEntry], HashtagDownloader] | None = None,
    duplicate_tracker: PreviewDuplicateTracker | None = None,
) -> tuple[list[PreviewItem], int]:
    """Sequential batch · Партия карточек превью с индекса"""
    if start >= len(index):
        if item_queue is not None:
            item_queue.put(None)
        return [], start

    probe = _unwrap_worker(worker)
    disk_resolver = PreviewDiskStatusResolver(probe)
    limit = max(1, batch_media_size)
    items: list[PreviewItem] = []
    seen_grouped: set[int] = set()
    pub_idx = start

    def entry_worker(entry: PreviewIndexEntry) -> HashtagDownloader:
        if worker_resolver is not None:
            return _unwrap_worker(worker_resolver(entry))
        return probe

    try:
        while pub_idx < len(index) and len(items) < limit:
            if _wait_if_paused(should_pause, should_stop):
                break
            if _preview_aborted(probe, should_stop):
                break

            batch_end = min(len(index), pub_idx + max(4, limit))
            entry_batch = index[pub_idx:batch_end]
            grouped_entries: dict[tuple[int, str, str], list[PreviewIndexEntry]] = {}
            for entry in entry_batch:
                active = entry_worker(entry)
                grouped_entries.setdefault(
                    (entry.channel_id, entry.hashtag, entry.channel_filter),
                    [],
                ).append(entry)
            resolved: dict[tuple[int, int], tuple[PreviewIndexEntry, HashtagDownloader, Message]] = {}
            for entries in grouped_entries.values():
                active = entry_worker(entries[0])
                fetched = await fetch_index_entry_messages(active, entries)
                await disk_resolver.prewarm(list(fetched.values()))
                for entry in entries:
                    message = fetched.get(entry.message_id)
                    if message is not None:
                        resolved[(entry.channel_id, entry.message_id)] = (entry, active, message)

            while pub_idx < batch_end and len(items) < limit:
                entry = index[pub_idx]
                pub_idx += 1
                resolved_item = resolved.get((entry.channel_id, entry.message_id))
                if resolved_item is None:
                    continue
                entry, active, message = resolved_item
                if not message or not message.media:
                    continue
                if message.grouped_id:
                    if message.grouped_id in seen_grouped:
                        continue
                    seen_grouped.add(message.grouped_id)
                    album_messages = await active.fetch_album_messages(message)
                    if len(album_messages) <= 1:
                        await _pause_between_album_fetches(
                            active,
                            thumb_pipeline=thumb_pipeline,
                            should_stop=should_stop,
                            should_pause=should_pause,
                        )
                    if _preview_aborted(active, should_stop):
                        break
                    await emit_preview_album(
                        active,
                        album_messages or [message],
                        items=items,
                        disk_resolver=disk_resolver,
                        duplicate_tracker=duplicate_tracker,
                        item_queue=item_queue,
                        thumb_pipeline=thumb_pipeline,
                        should_stop=should_stop,
                        hashtag=entry.hashtag or hashtag,
                        channel_filter=entry.channel_filter if entry.channel_filter is not None else channel_filter,
                        media_limit=limit,
                        media_ready=len(items),
                    )
                else:
                    await emit_preview_album(
                        active,
                        [message],
                        items=items,
                        disk_resolver=disk_resolver,
                        duplicate_tracker=duplicate_tracker,
                        item_queue=item_queue,
                        thumb_pipeline=thumb_pipeline,
                        should_stop=should_stop,
                        hashtag=entry.hashtag or hashtag,
                        channel_filter=entry.channel_filter if entry.channel_filter is not None else channel_filter,
                        media_limit=limit,
                        media_ready=len(items),
                    )
    finally:
        if item_queue is not None:
            item_queue.put(None)

    return items, pub_idx