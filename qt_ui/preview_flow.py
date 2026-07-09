"""Preview flow · Поток предпросмотра"""

from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config_store import PREVIEW_CACHE_DIR, SettingsData, build_app_config, resolve_download_dir
from app.i18n import tr
from app.preview_core import (
    PREVIEWABLE_KINDS,
    PreviewDuplicateTracker,
    PreviewItem,
    PreviewPrepContext,
    PreviewThumbPipeline,
    cleanup_preview_cache,
    collect_preview_items,
    download_preview_full_async,
    release_sequential_batch_memory,
    format_preview_prep_stats,
    format_preview_prep_status,
    preview_target_total,
    stream_collect_preview_items,
)

from app.preview_index import (
    build_preview_index,
    collect_sequential_preview_batch,
    format_sequential_index_status,
    merge_preview_summaries,
)
from app.tg_hashtag_dl import (
    DownloadStats,
    HashtagDownloader,
    ProgressState,
    merge_download_stats,
)

from .preview_dialog import PreviewDialogResult, SequentialBatchInfo, show_preview_dialog


@dataclass
class _PreviewSession:
    cache_dir: Path
    preview_stop: threading.Event
    preview_pause: threading.Event
    thumb_queue: queue.Queue[PreviewItem]
    dialog_ready: threading.Event


class PreviewFlowMixin:

    async def _process_preview_selection(
        self,
        client,
        settings: SettingsData,
        selected: list[PreviewItem],
    ) -> DownloadStats:
        from collections import defaultdict

        groups: dict[tuple[str, str], list] = defaultdict(list)
        for item in selected:
            tag = item.hashtag or settings.hashtag
            groups[(tag, item.channel_filter)].append(item.message)

        combined = DownloadStats(download_dir=str(resolve_download_dir(settings.download_dir)))
        for (tag, channel), messages in groups.items():
            if self.stop_event.is_set():
                combined.stopped = True
                break
            worker = self._batch_downloader(client, settings, tag, channel)
            batch_stats = await worker.process_messages(messages, preview_selection=True)
            if not combined.hashtag:
                combined.hashtag = batch_stats.hashtag
            if not combined.channel_label and batch_stats.channel_label:
                combined.channel_label = batch_stats.channel_label
            merge_download_stats(combined, batch_stats)
        combined.from_preview = True
        return combined

    def _on_preview_dialog_closed(self) -> None:
        self.progress_bar.set_indeterminate(False)
        self._update_progress(ProgressState(phase="idle", current=tr("preview_flow.finishing")))
        self.status_label.setText(tr("preview_flow.finishing"))

    def _make_preview_thumb_pipeline(
        self,
        client,
        cache_dir: Path,
        thumb_queue: queue.Queue[PreviewItem],
        preview_stop: threading.Event,
        *,
        preview_pause: threading.Event | None = None,
        items_counter: Callable[[], tuple[int, int]] | None = None,
        parallel_workers: int = 3,
    ) -> PreviewThumbPipeline:
        thumbs_loaded = 0

        def on_thumb_loaded(item: PreviewItem) -> None:
            nonlocal thumbs_loaded
            thumb_queue.put(item)
            if item.kind not in PREVIEWABLE_KINDS:
                return
            thumbs_loaded += 1
            media_ready, media_total = items_counter() if items_counter else (0, 0)
            n = thumbs_loaded
            self._progress_coalescer(
                ProgressState(
                    phase="preview",
                    found=media_ready,
                    total=media_total,
                    processed=n,
                    files=media_ready,
                    current=(
                        tr(
                            "preview_flow.thumbs_progress",
                            n=n,
                            total=media_total,
                            ready=media_ready,
                        )
                        if media_total
                        else tr("preview_flow.thumbs_only", n=n)
                    ),
                ),
            )

        pause = preview_pause

        def thumb_should_pause() -> bool:
            return self.task_paused.is_set() or bool(pause and pause.is_set())

        return PreviewThumbPipeline(
            client,
            cache_dir,
            should_stop=lambda: self.stop_event.is_set() or preview_stop.is_set(),
            should_pause=thumb_should_pause,
            on_item_loaded=on_thumb_loaded,
            parallel_workers=parallel_workers,
        )

    def _make_full_preview_loader(
        self,
        client,
        cache_dir: Path,
        preview_stop: threading.Event,
    ):
        def load_full(item: PreviewItem, on_ready) -> None:
            if preview_stop.is_set() or self.stop_event.is_set():
                self._invoker.run(lambda: on_ready(None, tr("preview_flow.error.cancelled")))
                return

            loop = getattr(self, "_worker_loop", None)
            if loop is None or not loop.is_running():
                self._invoker.run(
                    lambda: on_ready(None, tr("preview_flow.error.no_connection")),
                )
                return

            future = asyncio.run_coroutine_threadsafe(
                download_preview_full_async(client, item, cache_dir),
                loop,
            )

            def wait_done() -> None:
                try:
                    path, error = future.result(timeout=180)
                except concurrent.futures.TimeoutError:
                    path, error = None, tr("preview_flow.error.original_timeout")
                except Exception as exc:
                    path, error = None, str(exc)
                self._invoker.run(lambda p=path, e=error: on_ready(p, e))

            threading.Thread(target=wait_done, daemon=True).start()

        return load_full

    def _new_preview_session(self) -> _PreviewSession:
        return _PreviewSession(
            cache_dir=PREVIEW_CACHE_DIR / uuid.uuid4().hex,
            preview_stop=threading.Event(),
            preview_pause=threading.Event(),
            thumb_queue=queue.Queue(),
            dialog_ready=threading.Event(),
        )

    def _cleanup_preview_session(
        self,
        session: _PreviewSession,
        *,
        thumb_pipeline: PreviewThumbPipeline | None = None,
    ) -> None:
        session.preview_stop.set()
        session.preview_pause.clear()
        if thumb_pipeline is not None:
            thumb_pipeline.close()
        threading.Thread(target=cleanup_preview_cache, args=(session.cache_dir,), daemon=True).start()

    def _show_preview_dialog_async(
        self,
        *,
        selection_queue,
        session: _PreviewSession,
        items: list[PreviewItem],
        streaming: bool = False,
        item_queue=None,
        sequential_batch: SequentialBatchInfo | None = None,
        full_preview_loader=None,
    ) -> None:
        def show_preview() -> None:
            try:
                result = show_preview_dialog(
                    self,
                    items,
                    streaming=streaming,
                    item_queue=item_queue,
                    should_cancel=self.stop_event.is_set,
                    on_closing=session.preview_stop.set,
                    thumb_queue=session.thumb_queue,
                    on_ready=session.dialog_ready.set,
                    preview_pause=session.preview_pause,
                    sequential_batch=sequential_batch,
                    full_preview_loader=full_preview_loader,
                )
            except Exception:
                logging.exception(
                    tr("log.preview_flow.sequential_dialog_error")
                    if sequential_batch is not None
                    else tr("log.preview_flow.dialog_error"),
                )
                result = PreviewDialogResult(action="stop", items=[]) if sequential_batch is not None else None
            finally:
                session.preview_stop.set()
                self._invoker.run(self._on_preview_dialog_closed)
            if sequential_batch is not None and not isinstance(result, PreviewDialogResult):
                result = PreviewDialogResult(action="stop", items=result or [])
            selection_queue.put(result)

        self._invoker.run(show_preview)

    async def _wait_preview_selection(
        self,
        *,
        selection_queue: queue.Queue[list[PreviewItem] | None],
        preview_stop: threading.Event,
        thumb_pipeline: PreviewThumbPipeline | None = None,
        preview_pause: threading.Event | None = None,
    ) -> list[PreviewItem] | None:
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                preview_stop.set()
                return None
            paused = self.task_paused.is_set() or bool(preview_pause and preview_pause.is_set())
            if thumb_pipeline is not None and thumb_pipeline.has_pending() and not paused:
                await thumb_pipeline.pump()
            try:
                return selection_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
        preview_stop.set()
        return None

    async def _prompt_preview_for_items(
        self,
        client,
        preview_items: list[PreviewItem],
    ) -> list[PreviewItem] | None:
        if not preview_items:
            return []

        selection_queue: queue.Queue[list[PreviewItem] | None] = queue.Queue(maxsize=1)
        session = self._new_preview_session()
        previewable_count = sum(1 for item in preview_items if item.kind in PREVIEWABLE_KINDS)
        logging.info(tr("log.preview_flow.loading_media", n=previewable_count))
        thumb_pipeline = self._make_preview_thumb_pipeline(
            client,
            session.cache_dir,
            session.thumb_queue,
            session.preview_stop,
            preview_pause=session.preview_pause,
            items_counter=lambda: (len(preview_items), previewable_count),
            parallel_workers=self.settings.preview_parallel_workers,
        )
        for item in preview_items:
            thumb_pipeline.submit(item)
        full_preview_loader = self._make_full_preview_loader(client, session.cache_dir, session.preview_stop)

        try:
            self._show_preview_dialog_async(
                selection_queue=selection_queue,
                session=session,
                items=preview_items,
                full_preview_loader=full_preview_loader,
            )
            if not session.dialog_ready.wait(timeout=120):
                logging.warning(tr("log.preview_flow.dialog_timeout"))
                return None

            await thumb_pipeline.pump()
            return await self._wait_preview_selection(
                selection_queue=selection_queue,
                preview_stop=session.preview_stop,
                thumb_pipeline=thumb_pipeline,
                preview_pause=session.preview_pause,
            )
        finally:
            self._cleanup_preview_session(session, thumb_pipeline=thumb_pipeline)

    async def _run_sequential_preview(
        self,
        client,
        settings: SettingsData,
        hashtags: list[str],
        channels: list[str],
    ) -> DownloadStats | None:
        batch_size = max(20, min(int(settings.preview_batch_size), 1000))
        media_limit = max(0, int(settings.max_posts or 0))
        channel_list = channels or [""]
        full_index = []
        summary_parts = []

        self._invoker.run(
            lambda: self._update_progress(
                ProgressState(phase="search", current=tr("preview_flow.indexing")),
            ),
        )

        for tag in hashtags:
            for channel in channel_list:
                if self.stop_event.is_set():
                    return None
                self._crash_recorder.update_batch_active(tag, channel)
                worker = self._batch_downloader(client, settings, tag, channel)
                candidates = await worker.collect_candidates()
                if not candidates:
                    continue
                entries, part_summary = build_preview_index(
                    candidates,
                    hashtag=tag,
                    channel_filter=channel,
                )
                full_index.extend(entries)
                summary_parts.append(part_summary)
                logging.info(
                    tr(
                        "preview_flow.indexing_tag",
                        tag=tag,
                        channel=f" @{channel}" if channel else "",
                        n=part_summary.publications_total,
                    ),
                )
                del candidates

        if not full_index:
            return DownloadStats()

        index_summary = merge_preview_summaries(summary_parts)
        if media_limit > 0:
            media_log = tr("preview_flow.sequential_media_limit", n=media_limit)
        elif index_summary.album_groups > 0:
            media_log = tr("preview_flow.sequential_media_at_least", n=index_summary.media_estimate)
        else:
            media_log = tr("preview_flow.sequential_media_count", n=index_summary.media_estimate)
        logging.info(
            tr(
                "preview_flow.sequential_start",
                publications=index_summary.publications_total,
                media=media_log,
            ),
        )

        combined = DownloadStats(
            hashtag=settings.hashtag,
            download_dir=str(resolve_download_dir(settings.download_dir)),
            from_preview=True,
        )
        cursor = 0
        batch_number = 0
        media_collected = 0
        sequential_duplicate_tracker = PreviewDuplicateTracker()
        preview_workers: dict[tuple[str, str], HashtagDownloader] = {}
        probe_worker = self._batch_downloader(client, settings, hashtags[0], channel_list[0])

        def worker_resolver(entry):
            key = (entry.hashtag, entry.channel_filter)
            worker = preview_workers.get(key)
            if worker is None:
                worker = self._batch_downloader(client, settings, entry.hashtag, entry.channel_filter)
                preview_workers[key] = worker
            return worker

        while cursor < len(full_index):
            if self.stop_event.is_set():
                combined.stopped = True
                break
            if media_limit > 0 and media_collected >= media_limit:
                break

            effective_batch = batch_size
            if media_limit > 0:
                remaining_media = media_limit - media_collected
                effective_batch = min(batch_size, remaining_media)
                if effective_batch <= 0:
                    break

            batch_number += 1
            session = self._new_preview_session()
            item_queue: queue.Queue[PreviewItem | None] = queue.Queue()
            selection_queue: queue.Queue[PreviewDialogResult | None] = queue.Queue(maxsize=1)

            status = format_sequential_index_status(
                index_summary,
                batch_number=batch_number,
                publication_cursor=cursor,
                batch_size=batch_size,
                files_downloaded=combined.files,
                media_shown=media_collected,
                media_limit=media_limit,
            )
            self._invoker.run(
                lambda s=status: (
                    self.status_label.setText(s),
                    self._update_progress(ProgressState(phase="preview", current=s)),
                ),
            )

            sequential_info = SequentialBatchInfo(
                batch_number=batch_number,
                configured_batch_size=batch_size,
                effective_batch_size=effective_batch,
                publication_cursor=cursor,
                index_summary=index_summary,
                files_downloaded=combined.files,
                media_collected_before=media_collected,
                media_limit=media_limit,
            )

            full_preview_loader = self._make_full_preview_loader(client, session.cache_dir, session.preview_stop)
            self._show_preview_dialog_async(
                selection_queue=selection_queue,
                session=session,
                items=[],
                streaming=True,
                item_queue=item_queue,
                sequential_batch=sequential_info,
                full_preview_loader=full_preview_loader,
            )
            if not session.dialog_ready.wait(timeout=120):
                logging.warning(tr("log.preview_flow.sequential_dialog_timeout"))
                return combined if combined.files else None

            thumb_pipeline = self._make_preview_thumb_pipeline(
                client,
                session.cache_dir,
                session.thumb_queue,
                session.preview_stop,
                preview_pause=session.preview_pause,
                parallel_workers=settings.preview_parallel_workers,
            )
            batch_items: list[PreviewItem] = []
            dialog_result: PreviewDialogResult | None = None

            try:
                batch_items, cursor = await collect_sequential_preview_batch(
                    probe_worker,
                    full_index,
                    cursor,
                    batch_media_size=effective_batch,
                    item_queue=item_queue,
                    thumb_pipeline=thumb_pipeline,
                    should_stop=lambda: self.stop_event.is_set() or session.preview_stop.is_set(),
                    should_pause=lambda: self.task_paused.is_set() or session.preview_pause.is_set(),
                    worker_resolver=worker_resolver,
                    duplicate_tracker=sequential_duplicate_tracker,
                )
                await thumb_pipeline.pump()
                sequential_info.publication_cursor = cursor
                media_collected += len(batch_items)

                if self.stop_event.is_set():
                    break
                if not batch_items and cursor >= len(full_index):
                    break

                deadline = time.monotonic() + 3600
                while time.monotonic() < deadline:
                    if self.stop_event.is_set():
                        session.preview_stop.set()
                        combined.stopped = True
                        return combined if combined.files else None
                    paused = self.task_paused.is_set() or session.preview_pause.is_set()
                    if thumb_pipeline.has_pending() and not paused:
                        await thumb_pipeline.pump()
                    try:
                        dialog_result = selection_queue.get_nowait()
                        break
                    except queue.Empty:
                        await asyncio.sleep(0.05)
                        continue
                else:
                    session.preview_stop.set()
                    return combined if combined.files else None

                if dialog_result is None:
                    return combined if combined.files else None

                if dialog_result.action == "stop":
                    combined.stopped = self.stop_event.is_set()
                    return combined if combined.files else None

                if dialog_result.action == "skip_batch":
                    logging.info(tr("log.preview_flow.batch_skipped", n=batch_number))
                    continue

                if dialog_result.action == "download" and dialog_result.items:
                    batch_stats = await self._process_preview_selection(
                        client,
                        settings,
                        dialog_result.items,
                    )
                    merge_download_stats(combined, batch_stats)
                    logging.info(
                        tr(
                            "preview_flow.batch_downloaded",
                            n=batch_number,
                            files=batch_stats.files,
                            total=combined.files,
                        ),
                    )
            finally:
                self._cleanup_preview_session(session, thumb_pipeline=thumb_pipeline)
                release_sequential_batch_memory(
                    batch_items=batch_items,
                    item_queue=item_queue,
                    thumb_queue=session.thumb_queue,
                    workers=list(preview_workers.values()),
                )
                gc.collect()
                logging.debug(tr("log.preview_flow.batch_memory_freed", n=batch_number))

        preview_workers.clear()
        probe_worker.clear_preview_session_caches()
        del full_index
        gc.collect()
        return combined

    async def _prompt_preview_selection(
        self,
        client,
        settings: SettingsData,
        hashtags: list[str],
        channels: list[str],
    ) -> list[PreviewItem] | None:
        item_queue: queue.Queue[PreviewItem | None] = queue.Queue()
        preview_items: list[PreviewItem] = []
        dialog_opened = False

        media_limit = max(0, int(settings.max_posts or 0))

        def report_prep(ctx: PreviewPrepContext) -> None:
            nonlocal prep_ctx
            prep_ctx = ctx
            detail = format_preview_prep_status(ctx)
            stats = format_preview_prep_stats(ctx)
            target = preview_target_total(ctx)
            state = ProgressState(
                phase="preview",
                found=ctx.media_ready,
                total=target,
                processed=ctx.posts_total,
                files=ctx.media_ready,
                current=detail,
            )
            self._progress_coalescer(state)
            self._invoker.run(
                lambda d=detail, s=stats: (
                    self.progress_stats_label.setText(s),
                    self.status_label.setText(d),
                ),
            )

        selection_queue: queue.Queue[list[PreviewItem] | None] = queue.Queue(maxsize=1)
        session = self._new_preview_session()
        prep_ctx = PreviewPrepContext(
            posts_total=0,
            media_total=0,
            album_groups=0,
            albums_to_fetch=0,
            media_limit=media_limit,
            media_total_estimate=media_limit if media_limit > 0 else 0,
        )
        thumb_pipeline: PreviewThumbPipeline | None = None

        def items_counter() -> tuple[int, int]:
            return prep_ctx.media_ready, preview_target_total(prep_ctx)

        def preview_cancelled() -> bool:
            return self.stop_event.is_set() or session.preview_stop.is_set()

        def preview_paused() -> bool:
            return self.task_paused.is_set() or session.preview_pause.is_set()

        preview_media_collected = 0
        full_preview_loader = self._make_full_preview_loader(client, session.cache_dir, session.preview_stop)

        try:
            for tag in hashtags:
                if preview_cancelled():
                    break
                for channel in channels:
                    if preview_cancelled():
                        break
                    if media_limit > 0 and preview_media_collected >= media_limit:
                        break
                    self._crash_recorder.update_batch_active(tag, channel)
                    worker = self._batch_downloader(client, settings, tag, channel)
                    candidates = await worker.collect_candidates()
                    if not candidates:
                        continue
                    logging.info(
                        tr("preview_flow.opening_preview", n=len(candidates), tag=tag),
                    )

                    if not dialog_opened:
                        self._show_preview_dialog_async(
                            selection_queue=selection_queue,
                            session=session,
                            items=[],
                            streaming=True,
                            item_queue=item_queue,
                            full_preview_loader=full_preview_loader,
                        )
                        if not session.dialog_ready.wait(timeout=120):
                            logging.warning(tr("log.preview_flow.dialog_timeout"))
                            session.preview_stop.set()
                            return None
                        dialog_opened = True
                        thumb_pipeline = self._make_preview_thumb_pipeline(
                            client,
                            session.cache_dir,
                            session.thumb_queue,
                            session.preview_stop,
                            preview_pause=session.preview_pause,
                            items_counter=items_counter,
                            parallel_workers=settings.preview_parallel_workers,
                        )
                        logging.info(tr("log.preview_flow.parallel_thumbs"))

                    batch = await stream_collect_preview_items(
                        worker,
                        candidates,
                        hashtag=tag,
                        channel_filter=channel,
                        item_queue=item_queue,
                        on_progress=report_prep,
                        thumb_pipeline=thumb_pipeline,
                        should_stop=preview_cancelled,
                        should_pause=preview_paused,
                        media_limit=media_limit,
                        media_ready_offset=preview_media_collected,
                    )
                    preview_items.extend(batch)
                    preview_media_collected += len(batch)
                    if preview_cancelled():
                        break
                    if media_limit > 0 and preview_media_collected >= media_limit:
                        break
                if media_limit > 0 and preview_media_collected >= media_limit:
                    break

            if not dialog_opened:
                return []

            if not preview_cancelled():
                item_queue.put(None)
                logging.info(tr("log.preview_flow.list_ready", n=len(preview_items)))
            else:
                logging.info(tr("log.preview_flow.cancelled_by_user", n=len(preview_items)))

            return await self._wait_preview_selection(
                selection_queue=selection_queue,
                preview_stop=session.preview_stop,
                thumb_pipeline=thumb_pipeline,
                preview_pause=session.preview_pause,
            )
        finally:
            self._cleanup_preview_session(session, thumb_pipeline=thumb_pipeline)

