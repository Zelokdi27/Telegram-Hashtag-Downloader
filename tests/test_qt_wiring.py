"""GUI wiring tests · Связка GUI после рефакторинга (без запуска окна)"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.config_store import SettingsData
from qt_ui.task_spec import TaskSpec
from qt_ui.worker_controller import WorkerController


def test_task_spec_integrity_refs_default_empty():
    spec = TaskSpec(mode="preview", settings=SettingsData(hashtag="tag"))
    assert spec.integrity_refs == []


def test_worker_controller_batch_downloader_uses_window_events():
    win = MagicMock()
    win.stop_event.is_set.return_value = False
    win.task_paused.is_set.return_value = False
    coalescer = MagicMock()
    win._progress_coalescer = coalescer

    ctrl = WorkerController(win)
    settings = SettingsData(hashtag="tag", api_id="1", api_hash="x")
    client = object()

    with patch("qt_ui.worker_controller.HashtagDownloader") as downloader_cls:
        downloader_cls.return_value = MagicMock()
        with patch("qt_ui.worker_controller.build_app_config", return_value=MagicMock()):
            result = ctrl._batch_downloader(client, settings, "tag", "")

    downloader_cls.assert_called_once()
    _, kwargs = downloader_cls.call_args
    assert kwargs["should_stop"] is win.stop_event.is_set
    assert kwargs["should_pause"] is win.task_paused.is_set
    assert kwargs["on_progress"] is coalescer
    assert result is downloader_cls.return_value


def test_main_window_exposes_batch_downloader_delegate():
    from qt_ui.main_window import HashtagDownloaderWindow

    assert "_batch_downloader" in HashtagDownloaderWindow.__dict__
    assert "_start_autotune_check" in HashtagDownloaderWindow.__dict__