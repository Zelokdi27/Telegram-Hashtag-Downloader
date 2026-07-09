"""Worker controller module · Модуль фоновых задач"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict

from app.config_store import SettingsData, build_app_config, resolve_download_dir
from app.i18n import tr
from app.search_form import snapshot_from_settings
from app.download_options import parse_channel_list, parse_hashtag_list
from app.preview_core import PreviewItem, collect_preview_items
from app.telethon_loop import run_async
from app.telegram_auth import aconnect_client, adisconnect_quietly, make_client
from app.telegram_errors import format_telegram_error
from app.tg_hashtag_dl import (
    DownloadStats,
    HashtagDownloader,
    IntegrityStats,
    ProgressState,
    format_download_summary,
    format_integrity_summary,
    merge_integrity_stats,
    resolve_integrity_open_dir,
    resolve_summary_open_dir,
)

from .dialogs import show_download_summary_dialog, show_integrity_summary_dialog, show_warning
from .task_spec import TaskSpec

if False:  # TYPE_CHECKING
    from .main_window import HashtagDownloaderWindow

class WorkerController:
    """Worker controller · Фоновые задачи скачивания/проверки"""

    def __init__(self, window: "HashtagDownloaderWindow") -> None:
        self._win = window

    @property
    def _window(self):
        return self._win

    def launch(self, spec: TaskSpec) -> None:
        win = self._win
        win.stop_event.clear()
        win.task_paused.clear()
        win._sync_pause_button()
        win._reset_progress()
        win._set_running_state(True)
        launch_labels = {
            "once": (tr("worker.status.once", tag=spec.settings.hashtag), tr("worker.log.once")),
            "verify": (tr("worker.status.verify"), tr("worker.log.verify")),
            "preview": (tr("worker.status.preview"), tr("worker.log.preview")),
            "integrity_download": (tr("worker.status.integrity"), tr("worker.log.integrity")),
        }
        if spec.mode == "once" and spec.queue_hashtags:
            count = len(spec.queue_hashtags)
            status_text = tr("worker.status.queue", n=count)
            log_template = tr("worker.log.queue")
        else:
            status_text, log_template = launch_labels.get(
                spec.mode,
                (tr("worker.status.task", tag=spec.settings.hashtag), tr("worker.log.task")),
            )
        win.status_label.setText(status_text)
        if spec.mode == "integrity_download":
            logging.info(tr("log.worker.integrity_refs", n=len(spec.integrity_refs)))
        elif spec.mode == "once" and spec.queue_hashtags:
            logging.info(tr("worker.log.queue", n=len(spec.queue_hashtags)))
            logging.info(
                tr("log.worker.queue_list", tags=", ".join(f"#{tag}" for tag in spec.queue_hashtags)),
            )
        elif spec.mode == "once":
            logging.info(tr("worker.log.once", tag=spec.settings.hashtag))
        elif spec.mode == "verify":
            logging.info(tr("worker.log.verify", tag=spec.settings.hashtag))
        elif spec.mode == "preview":
            logging.info(tr("worker.log.preview", tag=spec.settings.hashtag))
        else:
            logging.info(tr("worker.log.task", tag=spec.settings.hashtag))
        if spec.settings.date_from or spec.settings.date_to:
            logging.info(
                tr(
                    "log.worker.date_filter",
                    date_from=spec.settings.date_from or tr("log.none"),
                    date_to=spec.settings.date_to or tr("log.none"),
                ),
            )
        win._crash_recorder.begin_session(
            worker_mode=spec.mode,
            form_snapshot=snapshot_from_settings(spec.settings),
        )
        if spec.queue_hashtags:
            win._queue_download_active = True
            win._remember_hashtag_tags(spec.queue_hashtags)
        else:
            win._queue_download_active = False
            win._remember_hashtags_from_settings(spec.settings)
        win.worker_mode = spec.mode
        win.worker_thread = threading.Thread(target=self.run_worker, args=(spec,), daemon=True)
        win.worker_thread.start()
        win._heartbeat_timer.start()

    def _progress_callback(self):
        return self._win._progress_coalescer

    def _batch_downloader(self, client, settings: SettingsData, tag: str, channel: str) -> HashtagDownloader:
        batch_config = build_app_config(settings, hashtag=tag, channel_filter=channel)
        return HashtagDownloader(
            client,
            batch_config,
            should_stop=self._win.stop_event.is_set,
            should_pause=self._win.task_paused.is_set,
            on_progress=self._progress_callback(),
        )

    def run_worker(self, spec: TaskSpec) -> None:
        try:
            run_async(self._async_worker(spec))
        except Exception as exc:
            self._win._crash_recorder.write_crash(exc, code="WORKER_EXCEPTION")
            logging.exception(tr("log.worker.execution_error", exc=exc))
            notice = format_telegram_error(exc)
            self._win._invoker.run(lambda m=notice: self._win._show_worker_error(m))
        finally:
            self._win._invoker.run(self.on_finished)

    async def _async_worker(self, spec: TaskSpec) -> None:
        settings = spec.settings
        mode = spec.mode
        config = build_app_config(settings, hashtag=settings.hashtag)
        session_path = self._win._session_path(settings)
        client = make_client(session_path, config.api_id, config.api_hash, settings)
        self._win._worker_loop = asyncio.get_running_loop()

        try:
            error = await aconnect_client(client)
            if error:
                raise RuntimeError(error.error)
            if not await client.is_user_authorized():
                raise RuntimeError(tr("worker.error.need_login"))

            me = await client.get_me()
            logging.info(tr("log.worker.running_as", user=getattr(me, "username", None) or me.id))

            downloader = HashtagDownloader(
                client,
                config,
                should_stop=self._win.stop_event.is_set,
                should_pause=self._win.task_paused.is_set,
                on_progress=self._win._progress_coalescer,
            )

            hashtags = (
                list(spec.queue_hashtags)
                if spec.queue_hashtags
                else parse_hashtag_list(settings.hashtag, settings.extra_hashtags)
            )
            channels = parse_channel_list(settings.channel_filter, settings.extra_channels) or [""]
            multi_batch = len(hashtags) > 1 or len(channels) > 1

            if mode == "verify":
                if multi_batch:
                    combined = IntegrityStats(download_dir=str(config.download_dir))
                    for tag in hashtags:
                        for channel in channels:
                            self._win._crash_recorder.update_batch_active(tag, channel)
                            batch_config = build_app_config(
                                settings, hashtag=tag, channel_filter=channel,
                            )
                            worker = HashtagDownloader(
                                client,
                                batch_config,
                                should_stop=self._win.stop_event.is_set,
                                should_pause=self._win.task_paused.is_set,
                                on_progress=self._win._progress_coalescer,
                            )
                            merge_integrity_stats(combined, await worker.verify_integrity())
                    self._win._pending_integrity_stats = combined
                else:
                    self._win._pending_integrity_stats = await downloader.verify_integrity()
                self._win._pending_download_stats = None
                logging.info(tr("log.worker.integrity_finished"))
            elif mode == "integrity_download":
                from collections import defaultdict

                refs = list(spec.integrity_refs)
                self._win._integrity_download_refs = []
                grouped: dict[tuple[str, str], list] = defaultdict(list)
                for ref in refs:
                    grouped[(ref.hashtag or settings.hashtag, ref.channel_filter)].append(ref)

                preview_items: list[PreviewItem] = []
                for (tag, channel), group_refs in grouped.items():
                    if self._win.stop_event.is_set():
                        break
                    self._win._crash_recorder.update_batch_active(tag, channel)
                    worker = self._batch_downloader(client, settings, tag, channel)
                    preview_items.extend(
                        await collect_preview_items(
                            worker,
                            await worker.fetch_missing_messages(group_refs),
                            hashtag=tag,
                            channel_filter=channel,
                            should_stop=self._win.stop_event.is_set,
                            should_pause=self._win.task_paused.is_set,
                        ),
                    )

                self._win._invoker.run(
                    lambda: self._win._update_progress(
                        ProgressState(phase="idle", current=tr("worker.progress.prep_preview")),
                    ),
                )
                selected = await self._win._prompt_preview_for_items(client, preview_items)

                if self._win.stop_event.is_set() and selected is None:
                    self._win._pending_download_stats = None
                elif selected is None:
                    logging.info(tr("log.worker.topup_cancelled"))
                    self._win._pending_download_stats = None
                elif not selected:
                    logging.info(tr("log.worker.topup_nothing"))
                    self._win._pending_download_stats = DownloadStats()
                else:
                    stats = await self._win._process_preview_selection(client, settings, selected)
                    self._win._pending_download_stats = stats
                self._win._pending_integrity_stats = None
            elif mode == "preview":
                if settings.sequential_preview:
                    stats = await self._win._run_sequential_preview(client, settings, hashtags, channels)
                    if stats is None:
                        logging.info(tr("log.worker.sequential_cancelled"))
                        self._win._pending_download_stats = None
                    elif not stats.files and not stats.stopped:
                        logging.info(tr("log.worker.sequential_nothing"))
                        self._win._pending_download_stats = DownloadStats()
                    else:
                        self._win._pending_download_stats = stats
                else:
                    selected = await self._win._prompt_preview_selection(client, settings, hashtags, channels)
                    if selected == []:
                        logging.info(tr("log.worker.no_preview_posts"))
                        self._win._invoker.run(
                            lambda: show_warning(
                                self._win,
                                tr("worker.preview.empty_title"),
                                tr("worker.preview.empty_body"),
                            ),
                        )
                        self._win._pending_download_stats = None
                    elif selected is None:
                        logging.info(tr("log.worker.preview_cancelled"))
                        self._win._pending_download_stats = None
                    elif not selected:
                        logging.info(tr("log.worker.nothing_selected"))
                        self._win._pending_download_stats = DownloadStats()
                    else:
                        stats = await self._win._process_preview_selection(client, settings, selected)
                        self._win._pending_download_stats = stats
            elif mode == "once":
                stats = (
                    await downloader.run_batch(hashtags, channels)
                    if multi_batch
                    else await downloader.run_once()
                )
                self._win._pending_download_stats = stats
            else:
                raise RuntimeError(tr("worker.error.unknown_mode", mode=mode))
        finally:
            self._win._worker_loop = None
            if client.is_connected():
                await adisconnect_quietly(client)

    def on_finished(self) -> None:
        win = self._win
        win._heartbeat_timer.stop()
        win._progress_coalescer.flush()
        win._crash_recorder.finish_ok()
        pending_stats = win._pending_download_stats
        pending_integrity = win._pending_integrity_stats
        win._pending_download_stats = None
        win._pending_integrity_stats = None
        finished_mode = win.worker_mode
        win.worker_mode = ""
        win._set_running_state(False)
        win._queue_download_active = False
        win._clear_queue_highlight()
        if win.stop_event.is_set():
            win.status_label.setText(tr("worker.status.stopped"))
            win._update_progress(ProgressState(phase="stopped", current=tr("worker.status.stopped")))
        else:
            win.status_label.setText(tr("main.status.ready"))
            win._reset_progress()

        if pending_integrity is not None:
            win.notify_task_finished(mode=finished_mode, integrity=pending_integrity)
            win._bring_app_to_front()
            can_download = bool(pending_integrity.missing_refs) and not win.stop_event.is_set()
            action = show_integrity_summary_dialog(
                win,
                tr("worker.dialog.integrity"),
                format_integrity_summary(pending_integrity),
                can_download_missing=can_download,
                download_dir=resolve_integrity_open_dir(pending_integrity),
            )
            if action == "download" and can_download:
                win._start_integrity_download(pending_integrity)
        elif pending_stats is not None and finished_mode in {"once", "preview", "integrity_download"}:
            win.notify_task_finished(
                mode=finished_mode,
                stats=pending_stats,
                stopped=bool(win.stop_event.is_set() or pending_stats.stopped),
            )
            win._bring_app_to_front(notify=True)
            title = tr("worker.dialog.download_results")
            if pending_stats.stopped:
                title = tr("worker.dialog.download_stopped")
            show_download_summary_dialog(
                win,
                title,
                format_download_summary(pending_stats),
                download_dir=resolve_summary_open_dir(pending_stats),
            )

