"""Crash dump diagnostics · Диагностические crash-дампы"""

from __future__ import annotations

import json

import pytest

from app.crash_dump import (
    CrashRecorder,
    format_crash_banner,
    format_dump_datetime,
    promote_stale_heartbeat,
    snapshot_to_mapping,
    startup_crash_info,
)
from app.i18n import tr
from app.search_form import SearchFormSnapshot, snapshot_from_mapping
from app.tg_hashtag_dl import ProgressState


@pytest.fixture
def crash_paths(tmp_path, monkeypatch):
    import app.crash_dump as crash_dump

    last_run = tmp_path / "last_run.json"
    crashes = tmp_path / "crashes"
    monkeypatch.setattr(crash_dump, "LAST_RUN_PATH", last_run)
    monkeypatch.setattr(crash_dump, "CRASHES_DIR", crashes)
    return last_run, crashes


def test_begin_and_finish_ok(crash_paths):
    last_run, _ = crash_paths
    recorder = CrashRecorder(entry="test")
    form = SearchFormSnapshot(hashtag="orphie", channel_filter="ch1", max_posts=5)

    recorder.begin_session(worker_mode="once", form_snapshot=form)
    assert last_run.is_file()
    payload = json.loads(last_run.read_text(encoding="utf-8"))
    assert payload["status"] == "running"
    assert payload["session"]["worker_mode"] == "once"
    assert payload["session"]["form_snapshot"]["hashtag"] == "orphie"

    recorder.finish_ok()
    assert not last_run.exists()


def test_note_progress_deferred_to_heartbeat(crash_paths):
    last_run, _ = crash_paths
    recorder = CrashRecorder(entry="gui")
    recorder.begin_session(
        worker_mode="once",
        form_snapshot=SearchFormSnapshot(hashtag="tag"),
    )
    writes: list[int] = []
    original = recorder._write_heartbeat_locked

    def tracked_write(*args, **kwargs):
        writes.append(1)
        return original(*args, **kwargs)

    recorder._write_heartbeat_locked = tracked_write  # type: ignore[method-assign]
    writes.clear()

    recorder.note_progress(ProgressState(phase="download", processed=7, total=20, current="x"))
    assert writes == []

    recorder.heartbeat(force=True)
    assert len(writes) == 1

    payload = json.loads(last_run.read_text(encoding="utf-8"))
    assert payload["progress"]["phase"] == "download"
    assert payload["progress"]["processed"] == 7


def test_write_crash_creates_file_and_clears_heartbeat(crash_paths):
    last_run, crashes = crash_paths
    recorder = CrashRecorder(entry="gui")
    recorder.begin_session(
        worker_mode="once",
        form_snapshot=SearchFormSnapshot(hashtag="tag"),
    )
    recorder.heartbeat(ProgressState(phase="download", processed=3, total=10, current="x"))

    path = recorder.write_crash(RuntimeError("boom"), code="WORKER_EXCEPTION")
    assert path is not None
    assert path.parent == crashes
    assert path.is_file()
    assert not last_run.exists()

    dump = json.loads(path.read_text(encoding="utf-8"))
    assert dump["bugcheck"]["code"] == "WORKER_EXCEPTION"
    assert "boom" in dump["bugcheck"]["summary"]
    assert dump["progress"]["phase"] == "download"


def test_promote_stale_heartbeat(crash_paths):
    last_run, crashes = crash_paths
    recorder = CrashRecorder(entry="gui")
    recorder.begin_session(worker_mode="watch", form_snapshot=SearchFormSnapshot(hashtag="w"))
    assert last_run.is_file()

    promoted = promote_stale_heartbeat()
    assert promoted is not None
    assert promoted.parent == crashes
    assert not last_run.exists()

    dump = json.loads(promoted.read_text(encoding="utf-8"))
    assert dump["bugcheck"]["code"] == "ABRUPT_EXIT"


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_startup_crash_info_from_stale_heartbeat(locale, crash_paths):
    recorder = CrashRecorder(entry="gui")
    recorder.begin_session(worker_mode="preview", form_snapshot=SearchFormSnapshot(hashtag="p"))

    info = startup_crash_info()
    assert info is not None
    assert info.form_snapshot is not None
    assert info.form_snapshot["hashtag"] == "p"
    assert info.summary.startswith(tr("crash.title_plain"))


def test_format_dump_datetime():
    assert format_dump_datetime("2026-07-01T16:52:07+03:00") == "01.07.2026 16:52:07"


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_crash_banner(locale):
    dump = {
        "created_at": "2026-07-01T12:00:00+03:00",
        "bugcheck": {"code": "WORKER_EXCEPTION", "summary": "RuntimeError: boom"},
        "session": {
            "worker_mode": "once",
            "form_snapshot": {"hashtag": "orphie", "channel_filter": "ch"},
        },
        "progress": {"phase": "download", "current": "file 2/5"},
    }
    title, detail = format_crash_banner(dump)
    assert title == tr("crash.title", date="01.07.2026 12:00:00")
    assert "WORKER_EXCEPTION" in detail
    assert "#orphie" in detail


def test_snapshot_roundtrip():
    original = SearchFormSnapshot(
        hashtag="a",
        extra_hashtags="b,c",
        required_hashtags="need",
        max_posts=7,
        media_audio=False,
    )
    restored = snapshot_from_mapping(snapshot_to_mapping(original))
    assert restored == original


def test_crash_rotation(crash_paths, monkeypatch):
    import app.crash_dump as crash_dump

    monkeypatch.setattr(crash_dump, "MAX_CRASH_FILES", 2)
    _, crashes = crash_paths
    recorder = CrashRecorder(entry="test")

    for idx in range(3):
        recorder.begin_session(worker_mode="once", form_snapshot=SearchFormSnapshot(hashtag=str(idx)))
        recorder.write_crash(RuntimeError(str(idx)), code="WORKER_EXCEPTION")

    files = sorted(crashes.glob("crash_*.json"))
    assert len(files) == 2