"""SQLite state journal tests · SQLite-журнал: snapshot и upsert"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.dl_state import load_state, save_state
from app.state_sqlite import (
    StateDirty,
    load_sqlite,
    save_sqlite,
    sqlite_add_message_files,
    sqlite_lookup_message_files,
    sqlite_path_for,
    sqlite_remove_missing_files,
)


def _sample_state(count: int = 3) -> dict:
    return {
        "processed": {
            f"peer:1:{index}": {
                "downloaded_at": f"2026-01-01T00:00:{index:02d}+00:00",
                "files": [f"channel/post_{index}.jpg"],
            }
            for index in range(1, count + 1)
        },
        "grouped": {
            "1001": {
                "downloaded_at": "2026-01-01T00:00:00+00:00",
                "files": ["channel/album_1.jpg", "channel/album_2.jpg"],
            }
        },
        "hashes": {
            "abc123": "channel/post_1.jpg",
            "def456": "channel/post_2.jpg",
        },
    }


def _row_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])
    finally:
        conn.close()


def test_full_snapshot_roundtrip(tmp_path: Path) -> None:
    state_path = tmp_path / "session_tag_state.json"
    state = _sample_state()

    save_state(state_path, state, full=True)
    loaded = load_state(state_path)

    assert loaded == state


def test_incremental_updates_single_processed_row(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "session_tag_state.sqlite"
    state = _sample_state(count=5)
    save_sqlite(sqlite_path, state, full=True)

    state["processed"]["peer:1:3"] = {
        "downloaded_at": "2026-07-03T12:00:00+00:00",
        "files": ["channel/post_3_updated.jpg"],
    }
    dirty = StateDirty(processed={"peer:1:3"})
    save_sqlite(sqlite_path, state, dirty=dirty)

    loaded = load_sqlite(sqlite_path)
    assert loaded["processed"]["peer:1:3"]["files"] == ["channel/post_3_updated.jpg"]
    assert len(loaded["processed"]) == 5
    assert _row_count(sqlite_path, "processed") == 5


def test_incremental_preserves_untouched_rows(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "session_tag_state.sqlite"
    state = _sample_state(count=4)
    save_sqlite(sqlite_path, state, full=True)

    state["processed"]["peer:1:99"] = {
        "downloaded_at": "2026-07-03T12:00:00+00:00",
        "files": ["channel/post_99.jpg"],
    }
    dirty = StateDirty(processed={"peer:1:99"})
    save_sqlite(sqlite_path, state, dirty=dirty)

    loaded = load_sqlite(sqlite_path)
    assert loaded["processed"]["peer:1:1"]["files"] == ["channel/post_1.jpg"]
    assert loaded["processed"]["peer:1:99"]["files"] == ["channel/post_99.jpg"]
    assert _row_count(sqlite_path, "processed") == 5


def test_incremental_hash_delete(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "session_tag_state.sqlite"
    state = _sample_state()
    save_sqlite(sqlite_path, state, full=True)

    state["hashes"].pop("abc123")
    dirty = StateDirty(deleted_hashes={"abc123"})
    save_sqlite(sqlite_path, state, dirty=dirty)

    loaded = load_sqlite(sqlite_path)
    assert "abc123" not in loaded["hashes"]
    assert loaded["hashes"]["def456"] == "channel/post_2.jpg"
    assert _row_count(sqlite_path, "hashes") == 1


def test_load_state_uses_sqlite_after_incremental(tmp_path: Path) -> None:
    state_path = tmp_path / "session_tag_state.json"
    state = _sample_state(count=2)
    save_state(state_path, state, full=True)

    state["grouped"]["2002"] = {
        "downloaded_at": "2026-07-03T12:00:00+00:00",
        "files": ["channel/new_album.jpg"],
    }
    dirty = StateDirty(grouped={"2002"})
    save_state(state_path, state, dirty=dirty)

    loaded = load_state(state_path)
    assert loaded["grouped"]["2002"]["files"] == ["channel/new_album.jpg"]
    assert sqlite_path_for(state_path).is_file()


def test_sqlite_files_roundtrip_and_delete(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "session_tag_state.sqlite"
    root = str((tmp_path / "downloads" / "tag" / "channel").resolve())
    first = tmp_path / "downloads" / "tag" / "channel" / "a_1.jpg"
    second = tmp_path / "downloads" / "tag" / "channel" / "a_1_2.jpg"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    sqlite_add_message_files(
        sqlite_path,
        root=root,
        message_id=1,
        paths=[str(first.resolve()), str(second.resolve())],
    )

    assert sqlite_lookup_message_files(sqlite_path, root=root, message_id=1) == [
        str(first.resolve()),
        str(second.resolve()),
    ]

    sqlite_remove_missing_files(sqlite_path, paths=[str(first.resolve())])

    assert sqlite_lookup_message_files(sqlite_path, root=root, message_id=1) == [
        str(second.resolve()),
    ]