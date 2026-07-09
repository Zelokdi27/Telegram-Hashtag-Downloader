"""Performance opts tests · Индекс диска и батч save_state"""

from __future__ import annotations

from app.state_sqlite import sqlite_add_message_files, sqlite_path_for

def test_disk_index_maps_message_files(channel_feed, worker_factory, download_root):
    channel_feed.add_single(42, caption="#orphie")
    worker = worker_factory()
    message = channel_feed.history[0]
    root = worker._channel_root_dir(message)
    root.mkdir(parents=True, exist_ok=True)
    first = root / "2026-01-01_12-00-00_orphie_channel_42.jpg"
    second = root / "2026-01-01_12-00-00_orphie_channel_42_2.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    index = worker._build_disk_index(root)
    assert index[42] == sorted([str(first.resolve()), str(second.resolve())])
    assert worker._scan_disk_for_message(message) == index[42]


def test_state_save_is_batched(monkeypatch, channel_feed, worker_factory):
    saves: list[int] = []

    def _track_save(_path, _state, **kwargs) -> None:
        saves.append(1)

    monkeypatch.setattr("app.downloader_state.save_state", _track_save)

    for msg_id in range(500, 512):
        channel_feed.add_single(msg_id, caption=f"post {msg_id}")

    worker = worker_factory(max_posts=0)
    entity = worker._resolve_channel_entity()
    messages = worker.search_in_channel(entity)
    worker.process_messages(messages)

    assert len(messages) >= 10
    assert len(saves) < len(messages)
    assert len(saves) >= 1


def test_scan_disk_for_message_uses_sqlite_file_index(channel_feed, worker_factory):
    channel_feed.add_single(77, caption="#orphie")
    worker = worker_factory()
    message = channel_feed.history[0]
    root = worker._channel_root_dir(message)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "2026-01-01_12-00-00_orphie_channel_77.jpg"
    target.write_bytes(b"x")

    sqlite_add_message_files(
        sqlite_path_for(worker.config.state_file),
        root=str(root.resolve()),
        message_id=77,
        paths=[str(target.resolve())],
    )

    assert worker._scan_disk_for_message(message) == [str(target.resolve())]