"""Preview core · Логика предпросмотра без GUI"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import queue
import shutil
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Literal
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timezone
from functools import partial
from pathlib import Path

from PIL import Image
from telethon.errors import FloodWaitError
from telethon.tl.custom.message import Message

from .tg_hashtag_dl import media_kind, message_key, safe_name
from .i18n import kind_labels, preview_filter_labels, preview_sort_labels, tr

logger = logging.getLogger(__name__)


def _unwrap_worker(worker: Any) -> Any:
    """Worker unwrap · Async-превью без sync-фасада"""
    return getattr(worker, "_target", worker)

PREVIEWABLE_KINDS = frozenset({"photo", "video", "animation", "document"})
PREVIEW_FILTER_MODES = (
    "all",
    "photo",
    "video",
    "animation",
    "audio",
    "document",
    "selected",
    "unselected",
    "new_only",
    "partial_only",
    "on_disk_only",
    "hide_on_disk",
    "duplicates_only",
    "hide_duplicates",
)
PREVIEW_SORT_MODES = ("date_desc", "date_asc", "channel", "kind")
DiskStatus = Literal["new", "partial", "complete"]
PREVIEW_PARALLEL_WORKERS = 3
_THUMB_CANDIDATES = (-1, 0, 1, 2)
_PREVIEW_JPEG_QUALITY = 92
PREVIEW_THUMB_SIZE = 200


def __getattr__(name: str):
    """I18n back-compat · Совместимость с preview_dialog"""
    if name == "PREVIEW_FILTER_LABELS":
        return tuple(preview_filter_labels())
    if name == "PREVIEW_SORT_LABELS":
        return tuple(preview_sort_labels())
    if name == "KIND_LABELS":
        return kind_labels()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass
class PreviewPrepContext:
    """Preview prep context · Сводка для статус-бара"""

    posts_total: int
    media_total: int
    album_groups: int
    albums_to_fetch: int
    media_total_estimate: int = 0
    media_ready: int = 0
    media_limit: int = 0
    phase: str = "summary"
    cards_total: int = 0


def preview_target_total(ctx: PreviewPrepContext) -> int:
    if ctx.media_limit > 0:
        return ctx.media_limit
    return ctx.media_total_estimate or ctx.media_total or 0


def format_preview_prep_status(ctx: PreviewPrepContext) -> str:
    estimate = preview_target_total(ctx)
    if ctx.phase == "done":
        return tr(
            "preview.prep.done",
            posts=ctx.posts_total,
            albums=ctx.album_groups,
            cards=ctx.cards_total,
        )
    return tr(
        "preview.prep.progress",
        posts=ctx.posts_total,
        albums=ctx.album_groups,
        ready=ctx.media_ready,
        total=estimate,
    )


def format_preview_prep_stats(ctx: PreviewPrepContext) -> str:
    estimate = preview_target_total(ctx)
    return tr(
        "preview.prep.stats",
        ready=ctx.media_ready,
        total=estimate,
        posts=ctx.posts_total,
        albums=ctx.album_groups,
    )


def estimate_media_total(messages, search_albums: dict[int, list]) -> int:
    total = 0
    seen_groups: set[int] = set()
    for message in messages:
        if not message.media:
            continue
        if message.grouped_id:
            if message.grouped_id in seen_groups:
                continue
            seen_groups.add(message.grouped_id)
            total += len(search_albums.get(message.grouped_id, [message]))
        else:
            total += 1
    return total


@dataclass
class PreviewItem:
    message: Message
    channel: str
    kind: str
    summary: str
    hashtag: str = ""
    channel_filter: str = ""
    selected: bool = True
    preview_path: str | None = None
    full_preview_path: str | None = None
    disk_status: DiskStatus = "new"
    disk_paths: list[str] | None = None
    album_on_disk: int = 0
    album_total: int = 0
    album_index: int = 0
    grouped_id: int = 0
    content_key: str = ""
    duplicate_of_message_id: int = 0


def media_content_key(message: Message) -> str | None:
    """Content key · Ключ содержимого медиа"""
    file = getattr(message, "file", None)
    if file is not None:
        unique_id = getattr(file, "unique_id", None)
        if unique_id:
            return f"uid:{unique_id}"
        file_id = getattr(file, "id", None)
        if file_id is not None:
            return f"file:{file_id}"

    for attr in ("photo", "document", "video", "animation", "audio"):
        media = getattr(message, attr, None)
        if media is None:
            continue
        unique_id = getattr(media, "file_unique_id", None) or getattr(media, "unique_id", None)
        if unique_id:
            return f"uid:{unique_id}"

    sim_key = getattr(message, "_sim_content_key", None)
    if sim_key:
        return f"sim:{sim_key}"

    sim_bytes = getattr(message, "_sim_bytes", None)
    if isinstance(sim_bytes, bytes) and sim_bytes:
        return f"bytes:{hashlib.sha256(sim_bytes).hexdigest()}"

    for attr in ("photo", "document", "video", "animation", "audio"):
        media = getattr(message, attr, None)
        if media is None:
            continue
        media_id = getattr(media, "id", None)
        if media_id is not None:
            return f"{attr}:{media_id}"

    return None


def is_content_duplicate(item: PreviewItem) -> bool:
    return item.duplicate_of_message_id > 0


def content_duplicate_badge(item: PreviewItem) -> str:
    if not is_content_duplicate(item):
        return ""
    return tr("preview.badge.duplicate")


def count_content_duplicates(items: list[PreviewItem]) -> int:
    return sum(1 for item in items if is_content_duplicate(item))


class PreviewDuplicateTracker:
    """Duplicate tracker · Первый content_key — оригинал"""

    def __init__(self) -> None:
        self._first_by_key: dict[str, int] = {}

    def annotate(self, item: PreviewItem) -> None:
        key = media_content_key(item.message)
        item.content_key = key or ""
        if not key:
            item.duplicate_of_message_id = 0
            return
        first_id = self._first_by_key.get(key)
        if first_id is None:
            self._first_by_key[key] = item.message.id
            item.duplicate_of_message_id = 0
        else:
            item.duplicate_of_message_id = first_id


def annotate_preview_duplicates(items: list[PreviewItem]) -> None:
    tracker = PreviewDuplicateTracker()
    for item in items:
        tracker.annotate(item)


def disk_status_badge(item: PreviewItem) -> str:
    if item.disk_status == "complete":
        return tr("preview.badge.on_disk")
    if item.disk_status == "partial":
        if item.album_total > 1:
            return f"{item.album_on_disk}/{item.album_total}"
        return tr("preview.badge.partial")
    return ""


def preview_channels(items: list[PreviewItem]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        channel = item.channel.strip()
        if channel:
            seen[channel] = None
    return sorted(seen.keys())


def count_selected_items(items: list[PreviewItem]) -> int:
    return sum(1 for item in items if item.selected)


def set_items_selection(items: list[PreviewItem], *, selected: bool) -> None:
    for item in items:
        item.selected = selected


def selection_summary(
    items: list[PreviewItem],
    visible: list[PreviewItem] | None = None,
) -> tuple[int, int, int]:
    """Selection summary · (выбрано, всего, среди показанных)"""
    total = len(items)
    selected = count_selected_items(items)
    if visible is None:
        return selected, total, selected
    visible_selected = sum(1 for item in visible if item.selected)
    return selected, total, visible_selected


def filter_preview_items(items: list[PreviewItem], mode: str) -> list[PreviewItem]:
    if mode == "photo":
        return [item for item in items if item.kind == "photo"]
    if mode == "video":
        return [item for item in items if item.kind == "video"]
    if mode == "animation":
        return [item for item in items if item.kind == "animation"]
    if mode == "audio":
        return [item for item in items if item.kind == "audio"]
    if mode == "document":
        return [item for item in items if item.kind == "document"]
    if mode == "selected":
        return [item for item in items if item.selected]
    if mode == "unselected":
        return [item for item in items if not item.selected]
    if mode == "new_only":
        return [item for item in items if item.disk_status == "new"]
    if mode == "partial_only":
        return [item for item in items if item.disk_status == "partial"]
    if mode == "on_disk_only":
        return [item for item in items if item.disk_status == "complete"]
    if mode == "hide_on_disk":
        return [item for item in items if item.disk_status != "complete"]
    if mode == "duplicates_only":
        return [item for item in items if is_content_duplicate(item)]
    if mode == "hide_duplicates":
        return [item for item in items if not is_content_duplicate(item)]
    return items


def sort_preview_items(items: list[PreviewItem], mode: str) -> list[PreviewItem]:
    def ordering_key(item: PreviewItem, *, reverse_date: bool) -> tuple:
        ts = item.message.date.timestamp()
        primary = -ts if reverse_date else ts
        album_order = item.album_index if item.album_index > 0 else 1
        message_order = item.message.id if item.grouped_id and item.album_index > 0 else item.message.id
        if reverse_date and not (item.grouped_id and item.album_index > 0):
            message_order = -message_order
        return (
            primary,
            item.channel.lower(),
            -(item.grouped_id or 0) if reverse_date else (item.grouped_id or 0),
            album_order,
            message_order,
        )

    if mode == "date_asc":
        return sorted(items, key=lambda item: ordering_key(item, reverse_date=False))
    if mode == "channel":
        return sorted(
            items,
            key=lambda item: (
                item.channel.lower(),
                item.message.date.timestamp(),
                item.grouped_id or 0,
                item.album_index if item.album_index > 0 else 1,
                item.message.id,
            ),
        )
    if mode == "kind":
        labels = kind_labels()
        return sorted(
            items,
            key=lambda item: (
                labels.get(item.kind, item.kind),
                -item.message.date.timestamp(),
                -(item.grouped_id or 0),
                item.album_index if item.album_index > 0 else 1,
                item.message.id if item.grouped_id and item.album_index > 0 else -item.message.id,
            ),
        )
    return sorted(items, key=lambda item: ordering_key(item, reverse_date=True))


def apply_preview_view(
    items: list[PreviewItem],
    *,
    mode: str,
    channel: str = "",
    sort: str = "date_desc",
) -> list[PreviewItem]:
    filtered = filter_preview_items(items, mode)
    channel_pick = channel.strip()
    if channel_pick:
        filtered = [item for item in filtered if item.channel == channel_pick]
    return sort_preview_items(filtered, sort)


class PreviewDiskStatusResolver:
    """Disk status resolver · Пакетная проверка диска для превью"""

    def __init__(self, worker) -> None:
        self._worker = _unwrap_worker(worker)
        self._roots_by_key: dict[str, Path] = {}

    async def root_for(self, message: Message) -> Path:
        key = message_key(message)
        cached = self._roots_by_key.get(key)
        if cached is not None:
            return cached
        root = (await self._worker._channel_root_dir(message)).resolve()
        self._roots_by_key[key] = root
        return root

    async def prewarm(self, messages: list[Message]) -> None:
        unique_roots: dict[str, Path] = {}
        for message in messages:
            if not getattr(message, "media", None):
                continue
            root = await self.root_for(message)
            unique_roots[str(root)] = root
        for root in unique_roots.values():
            self._worker._disk_index_for_root(root)

    async def resolve(self, item: PreviewItem) -> None:
        root = await self.root_for(item.message)
        scanned = await self._worker._item_already_on_disk_with_root(item.message, root=root)
        if scanned:
            item.disk_status = "complete"
            item.disk_paths = list(scanned)
            return

        slots = await self._worker._integrity_album_slots(item.message)
        if len(slots) <= 1:
            item.disk_status = "new"
            item.disk_paths = []
            return

        on_disk = 0
        paths: list[str] = []
        for slot_msg, suffix in slots:
            slot_root = await self.root_for(slot_msg)
            found = await self._worker._item_already_on_disk_with_root(
                slot_msg,
                root=slot_root,
                suffix=suffix,
            )
            if found:
                on_disk += 1
                paths.extend(found)
        item.album_total = len(slots)
        item.album_on_disk = on_disk
        item.disk_paths = sorted({str(path) for path in paths})
        if on_disk >= len(slots):
            item.disk_status = "complete"
        elif on_disk > 0:
            item.disk_status = "partial"
        else:
            item.disk_status = "new"


async def build_preview_item(
    message: Message,
    channel_resolver,
    *,
    hashtag: str = "",
    channel_filter: str = "",
    album_index: int = 0,
    album_total: int = 0,
) -> PreviewItem | None:
    if not message.media:
        return None
    kind = media_kind(message) or "media"
    try:
        resolved = channel_resolver(message)
        channel = await resolved if inspect.isawaitable(resolved) else resolved
    except Exception:
        channel = tr("preview.channel_unknown")
    text = (getattr(message, "message", None) or getattr(message, "text", None) or "").strip()
    if len(text) > 80:
        text = text[:77] + "…"
    summary = text or tr("preview.post_fallback", id=message.id)
    if album_total > 1 and album_index > 0:
        summary = f"{summary} · {album_index}/{album_total}"
    return PreviewItem(
        message=message,
        channel=channel,
        kind=kind,
        summary=summary,
        hashtag=hashtag,
        channel_filter=channel_filter,
        album_index=album_index,
        grouped_id=message.grouped_id or 0,
    )


def _album_index_from_search(messages) -> dict[int, list]:
    groups: dict[int, list] = {}
    for msg in messages:
        if msg.grouped_id and msg.media:
            groups.setdefault(msg.grouped_id, []).append(msg)
    for group in groups.values():
        group.sort(key=lambda item: item.id)
    return groups


_ALBUM_FETCH_PAUSE_SEC = 0.25


def _wait_if_paused(
    should_pause: Callable[[], bool] | None,
    should_stop: Callable[[], bool] | None,
) -> bool:
    """Pause check · True — остановка; False — продолжать"""
    while should_pause and should_pause():
        if should_stop and should_stop():
            return True
        time.sleep(0.4)
    return bool(should_stop and should_stop())


async def _pause_between_album_fetches(
    worker,
    *,
    thumb_pipeline: PreviewThumbPipeline | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
) -> None:
    deadline = time.monotonic() + _ALBUM_FETCH_PAUSE_SEC
    while time.monotonic() < deadline:
        if _wait_if_paused(should_pause, should_stop):
            break
        if should_stop and should_stop():
            break
        if thumb_pipeline is not None:
            await thumb_pipeline.pump()
        if worker._stopped():
            break
        await asyncio.sleep(0.05)


def _preview_aborted(worker, should_stop: Callable[[], bool] | None) -> bool:
    return worker._stopped() or bool(should_stop and should_stop())


def _preview_prep_context(
    messages,
    search_albums: dict[int, list],
    *,
    media_limit: int = 0,
) -> PreviewPrepContext:
    media_total = sum(1 for msg in messages if msg.media)
    albums_to_fetch = sum(1 for group in search_albums.values() if len(group) == 1)
    estimate = estimate_media_total(messages, search_albums)
    if media_limit > 0:
        estimate = media_limit
    return PreviewPrepContext(
        posts_total=len(messages),
        media_total=media_total,
        album_groups=len(search_albums),
        albums_to_fetch=albums_to_fetch,
        media_total_estimate=estimate,
        media_limit=media_limit,
    )


def media_ready_count(items: list[PreviewItem], *, offset: int = 0) -> int:
    return offset + len(items)


def _stream_progress_tick(
    ctx: PreviewPrepContext,
    on_progress: Callable[[PreviewPrepContext], None] | None,
) -> None:
    ctx.media_ready += 1
    if on_progress:
        on_progress(ctx)


async def emit_preview_album(
    active,
    album_messages: list[Message],
    *,
    items: list[PreviewItem],
    disk_resolver: PreviewDiskStatusResolver,
    duplicate_tracker: PreviewDuplicateTracker | None = None,
    item_queue: queue.Queue[PreviewItem] | None = None,
    thumb_pipeline: PreviewThumbPipeline | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_item: Callable[[PreviewItem], None] | None = None,
    on_after_item: Callable[[], None] | None = None,
    hashtag: str = "",
    channel_filter: str = "",
    media_limit: int = 0,
    media_ready: int = 0,
) -> int:
    allowed = [msg for msg in album_messages if active._media_allowed(msg)]
    if not allowed:
        return 0
    album_total = len(allowed)
    remaining = media_limit - media_ready if media_limit > 0 else None
    if remaining is not None:
        if remaining <= 0:
            return 0
        take = allowed[:remaining]
        grouped_id = allowed[0].grouped_id
        if grouped_id and len(take) < len(allowed):
            active._album_slot_caps[grouped_id] = len(take)
    else:
        take = allowed
    emitted = 0
    for album_index, msg in enumerate(take, start=1):
        if should_stop and should_stop():
            break
        item = await build_preview_item(
            msg,
            active.channel_label,
            hashtag=hashtag,
            channel_filter=channel_filter,
            album_index=album_index,
            album_total=album_total,
        )
        if not item:
            continue
        await disk_resolver.resolve(item)
        if duplicate_tracker is not None:
            duplicate_tracker.annotate(item)
        items.append(item)
        emitted += 1
        if item_queue is not None:
            item_queue.put(item)
        if on_item is not None:
            on_item(item)
        if thumb_pipeline is not None:
            thumb_pipeline.submit(item)
            await thumb_pipeline.pump()
        if on_after_item is not None:
            on_after_item()
    return emitted


async def stream_collect_preview_items(
    worker,
    messages,
    *,
    hashtag: str = "",
    channel_filter: str = "",
    item_queue: queue.Queue | None = None,
    on_progress: Callable[[PreviewPrepContext], None] | None = None,
    thumb_pipeline: PreviewThumbPipeline | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    media_limit: int | None = None,
    media_ready_offset: int = 0,
    on_disk_status: Callable[[PreviewItem], None] | None = None,
) -> list[PreviewItem]:
    """Stream preview · Потоковый сбор карточек превью"""
    active = _unwrap_worker(worker)
    if media_limit is None:
        media_limit = max(0, int(getattr(active.config, "max_posts", 0) or 0))
    else:
        media_limit = max(0, int(media_limit))
    media_ready_offset = max(0, int(media_ready_offset))
    search_albums = _album_index_from_search(messages)
    disk_resolver = PreviewDiskStatusResolver(active)
    await disk_resolver.prewarm([msg for msg in messages if getattr(msg, "media", None)])
    ctx = _preview_prep_context(messages, search_albums, media_limit=media_limit)
    ctx.media_ready = media_ready_offset
    ctx.phase = "collect"
    if on_progress:
        on_progress(ctx)

    seen_grouped: set[int] = set()
    items: list[PreviewItem] = []
    album_fetches = 0
    duplicate_tracker = PreviewDuplicateTracker()

    for message in messages:
        if _wait_if_paused(should_pause, should_stop):
            break
        if _preview_aborted(active, should_stop):
            break
        if not message.media:
            continue
        if message.grouped_id:
            if message.grouped_id in seen_grouped:
                continue
            seen_grouped.add(message.grouped_id)
            group_id = message.grouped_id
            album_messages = search_albums.get(group_id, [message])
            if len(album_messages) == 1:
                before = len(album_messages)
                fetched = await active.fetch_album_messages(message)
                album_fetches += 1
                if len(fetched) > 1:
                    search_albums[group_id] = fetched
                    album_messages = fetched
                    if media_limit <= 0:
                        ctx.media_total_estimate += len(fetched) - before
                        if on_progress:
                            on_progress(ctx)
                if _preview_aborted(active, should_stop):
                    break
                await _pause_between_album_fetches(
                    active,
                    thumb_pipeline=thumb_pipeline,
                    should_stop=should_stop,
                    should_pause=should_pause,
                )
            if _preview_aborted(active, should_stop):
                break
            emitted = await emit_preview_album(
                active,
                album_messages,
                items=items,
                disk_resolver=disk_resolver,
                duplicate_tracker=duplicate_tracker,
                item_queue=item_queue,
                thumb_pipeline=thumb_pipeline,
                should_stop=should_stop,
                on_item=on_disk_status,
                on_after_item=lambda: _stream_progress_tick(ctx, on_progress),
                hashtag=hashtag,
                channel_filter=channel_filter,
                media_limit=media_limit,
                media_ready=ctx.media_ready,
            )
            if emitted:
                ctx.media_ready = media_ready_count(items, offset=media_ready_offset)
        else:
            emitted = await emit_preview_album(
                active,
                [message],
                items=items,
                disk_resolver=disk_resolver,
                duplicate_tracker=duplicate_tracker,
                item_queue=item_queue,
                thumb_pipeline=thumb_pipeline,
                should_stop=should_stop,
                on_item=on_disk_status,
                on_after_item=lambda: _stream_progress_tick(ctx, on_progress),
                hashtag=hashtag,
                channel_filter=channel_filter,
                media_limit=media_limit,
                media_ready=ctx.media_ready,
            )
            if emitted:
                ctx.media_ready = media_ready_count(items, offset=media_ready_offset)

    if album_fetches:
        logger.info(tr("log.preview.albums_fetched", n=album_fetches))

    ctx.cards_total = len(items)
    if media_limit <= 0:
        ctx.media_total_estimate = max(ctx.media_total_estimate, ctx.media_ready)
    ctx.phase = "done"
    if on_progress:
        on_progress(ctx)
    return items


async def collect_preview_items(
    worker,
    messages,
    *,
    hashtag: str = "",
    channel_filter: str = "",
    on_progress: Callable[[PreviewPrepContext], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
) -> list[PreviewItem]:
    """Collect preview · Сбор карточек превью"""
    return await stream_collect_preview_items(
        worker,
        messages,
        hashtag=hashtag,
        channel_filter=channel_filter,
        on_progress=on_progress,
        should_stop=should_stop,
        should_pause=should_pause,
    )


def format_message_local_datetime(message: Message) -> str:
    dt = message.date
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d.%m.%Y %H:%M")


def _optimize_preview_file(source: Path, target: Path, *, max_dimension: int) -> bool:
    try:
        with Image.open(source) as img:
            img = img.convert("RGB")
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            img.save(target, format="JPEG", quality=_PREVIEW_JPEG_QUALITY, optimize=True)
        return target.is_file() and target.stat().st_size > 0
    except Exception as exc:
        logger.debug(tr("log.preview.optimize_failed", source=source, exc=exc))
        return False


async def _refresh_preview_message(client, message: Message) -> Message:
    """Refresh message · Полная версия поста перед скачиванием"""
    try:
        entity = await client.get_input_entity(message.peer_id)
        fresh = await client.get_messages(entity, ids=message.id)
        if fresh is not None and getattr(fresh, "media", None):
            return fresh
    except Exception as exc:
        logger.debug(tr("log.preview.refresh_failed", id=message.id, exc=exc))
    return message


def _resolve_downloaded_path(
    cache_dir: Path,
    key: str,
    result: str | bytes | None,
    *,
    fallback_stem: str,
) -> Path | None:
    if result:
        candidate = Path(result)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    stem = Path(fallback_stem).name
    matches = sorted(
        cache_dir.glob(f"{stem}*"),
        key=lambda path: path.stat().st_size if path.is_file() else 0,
        reverse=True,
    )
    for candidate in matches:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


async def download_preview_full_async(
    client,
    item: PreviewItem,
    cache_dir: Path,
) -> tuple[str | None, str | None]:
    """Preview full download · Полноразмерное фото; (path, error)"""
    if item.kind != "photo":
        return None, tr("preview.original.photos_only")
    cached = item.full_preview_path
    if cached and Path(cached).is_file():
        return cached, None

    cache_dir.mkdir(parents=True, exist_ok=True)
    key = safe_name(message_key(item.message))
    message = await _refresh_preview_message(client, item.message)
    last_error = tr("preview.original.load_failed")

    for attempt, use_thumb in enumerate((None, -1)):
        target = cache_dir / f"{key}.full"
        for old in cache_dir.glob(f"{key}.full*"):
            try:
                old.unlink()
            except OSError:
                pass
        label = tr("preview.original.label_full") if use_thumb is None else tr("preview.original.label_thumb")
        try:
            kwargs: dict = {"file": str(target)}
            if use_thumb is not None:
                kwargs["thumb"] = use_thumb
            result = await client.download_media(message, **kwargs)
            downloaded = _resolve_downloaded_path(
                cache_dir,
                key,
                result,
                fallback_stem=str(target),
            )
            if downloaded is not None:
                item.full_preview_path = str(downloaded.resolve())
                if use_thumb is not None:
                    logger.info(
                        tr("log.preview.original_shown", id=message.id, label=label),
                    )
                return item.full_preview_path, None
            last_error = tr("preview.original.not_returned", label=label, id=message.id)
            logger.warning(last_error)
        except FloodWaitError as exc:
            logger.warning(
                tr("log.preview.preview_flood", id=message.id, sec=exc.seconds),
            )
            await asyncio.sleep(exc.seconds + 1)
            last_error = tr("preview.original.flood_wait", sec=exc.seconds)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(tr("log.preview.original_failed", id=message.id, error=last_error))

    return None, last_error


async def _download_preview_thumb_async(
    client,
    item: PreviewItem,
    cache_dir: Path,
    *,
    executor: ThreadPoolExecutor,
    should_stop: Callable[[], bool] | None = None,
) -> str | None:
    key = safe_name(message_key(item.message))
    raw_path = cache_dir / f"{key}.raw"
    target = cache_dir / f"{key}.jpg"
    loop = asyncio.get_running_loop()

    for thumb_idx in _THUMB_CANDIDATES:
        if should_stop and should_stop():
            return None
        try:
            if raw_path.exists():
                raw_path.unlink()
            result = await client.download_media(item.message, file=str(raw_path), thumb=thumb_idx)
            downloaded = Path(result) if result else raw_path
            if not downloaded.is_file() or downloaded.stat().st_size <= 0:
                continue
            optimized = await loop.run_in_executor(
                executor,
                partial(_optimize_preview_file, downloaded, target, max_dimension=PREVIEW_THUMB_SIZE),
            )
            if optimized:
                return str(target)
            if downloaded != target:
                await loop.run_in_executor(executor, shutil.copy2, downloaded, target)
                return str(target)
        except FloodWaitError as exc:
            logger.warning(tr("log.preview.preview_flood", id=item.message.id, sec=exc.seconds))
            await asyncio.sleep(exc.seconds + 1)
        except Exception as exc:
            logger.debug(
                tr("log.preview.preview_thumb_failed", id=item.message.id, thumb=thumb_idx, exc=exc),
            )
    return None


class PreviewThumbPipeline:
    """Thumb pipeline · Пакетная загрузка миниатюр"""

    def __init__(
        self,
        client,
        cache_dir: Path,
        *,
        should_stop: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
        on_item_loaded: Callable[[PreviewItem], None] | None = None,
        parallel_workers: int = PREVIEW_PARALLEL_WORKERS,
        batch_size: int | None = None,
    ) -> None:
        self._client = client
        self._cache_dir = cache_dir
        self._should_stop = should_stop
        self._should_pause = should_pause
        self._on_item_loaded = on_item_loaded
        self._parallel_workers = max(1, min(int(parallel_workers), 6))
        self._batch_size = max(1, batch_size if batch_size is not None else self._parallel_workers)
        self._pending: deque[PreviewItem] = deque()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=self._parallel_workers)
        self._executor_closed = False

    def close(self) -> None:
        if self._executor_closed:
            return
        self._executor_closed = True
        self._pending.clear()
        try:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._executor.shutdown(wait=False)
        except Exception as exc:
            logger.warning(tr("log.preview.pool_shutdown_failed", exc=exc))

    def submit(self, item: PreviewItem) -> None:
        self._pending.append(item)

    def has_pending(self) -> bool:
        return bool(self._pending)

    async def pump(self) -> None:
        if self._executor_closed:
            return
        if _wait_if_paused(self._should_pause, self._should_stop):
            return
        if not self._pending or (self._should_stop and self._should_stop()):
            return
        batch: list[PreviewItem] = []
        while self._pending and len(batch) < self._batch_size:
            batch.append(self._pending.popleft())
        if not batch:
            return
        await prepare_preview_thumbnails(
            self._client,
            batch,
            self._cache_dir,
            should_stop=self._should_stop,
            should_pause=self._should_pause,
            on_item_loaded=self._on_item_loaded,
            executor=self._executor,
            parallel_workers=self._parallel_workers,
        )

    async def flush(self) -> None:
        try:
            while self._pending and not (self._should_stop and self._should_stop()):
                if _wait_if_paused(self._should_pause, self._should_stop):
                    break
                await self.pump()
        finally:
            self.close()


async def _prepare_preview_thumbnails_async(
    client,
    items: list[PreviewItem],
    cache_dir: Path,
    *,
    should_stop: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None = None,
    on_item_loaded: Callable[[PreviewItem], None] | None = None,
    executor: ThreadPoolExecutor | None = None,
    parallel_workers: int = PREVIEW_PARALLEL_WORKERS,
) -> None:
    workers = max(1, min(int(parallel_workers), 6))
    semaphore = asyncio.Semaphore(workers)
    owns_executor = executor is None
    if owns_executor:
        executor = ThreadPoolExecutor(max_workers=workers)

    try:

        async def load_one(item: PreviewItem) -> None:
            while should_pause and should_pause():
                if should_stop and should_stop():
                    return
                await asyncio.sleep(0.4)
            if should_stop and should_stop():
                return
            if item.kind not in PREVIEWABLE_KINDS:
                if on_item_loaded:
                    on_item_loaded(item)
                return
            async with semaphore:
                if should_stop and should_stop():
                    return
                item.preview_path = await _download_preview_thumb_async(
                    client,
                    item,
                    cache_dir,
                    executor=executor,
                    should_stop=should_stop,
                )
            if on_item_loaded:
                on_item_loaded(item)

        tasks = [asyncio.create_task(load_one(item)) for item in items]
        try:
            await asyncio.gather(*tasks)
        finally:
            if should_stop and should_stop():
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if owns_executor and executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)


async def prepare_preview_thumbnails(
    client,
    items: list[PreviewItem],
    cache_dir: Path,
    *,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    on_item_loaded: Callable[[PreviewItem], None] | None = None,
    executor: ThreadPoolExecutor | None = None,
    parallel_workers: int = PREVIEW_PARALLEL_WORKERS,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not items:
        return

    await _prepare_preview_thumbnails_async(
        client,
        items,
        cache_dir,
        should_stop=should_stop,
        should_pause=should_pause,
        on_item_loaded=on_item_loaded,
        executor=executor,
        parallel_workers=parallel_workers,
    )


def cleanup_preview_cache(cache_dir: Path | None) -> None:
    if cache_dir and cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)


def drain_preview_queue(item_queue: queue.Queue | None) -> None:
    if item_queue is None:
        return
    while True:
        try:
            item_queue.get_nowait()
        except queue.Empty:
            break


def release_preview_items(items: list[PreviewItem] | None) -> None:
    if not items:
        return
    for item in items:
        item.preview_path = None
        item.message = None  # type: ignore[assignment]


def release_sequential_batch_memory(
    *,
    batch_items: list[PreviewItem] | None = None,
    item_queue: queue.Queue | None = None,
    thumb_queue: queue.Queue | None = None,
    workers: list | None = None,
) -> None:
    """Batch memory release · Освобождение памяти партии превью"""
    drain_preview_queue(item_queue)
    drain_preview_queue(thumb_queue)
    release_preview_items(batch_items)
    if batch_items is not None:
        batch_items.clear()
    if workers:
        for worker in workers:
            clear_fn = getattr(worker, "clear_preview_session_caches", None)
            if callable(clear_fn):
                clear_fn()