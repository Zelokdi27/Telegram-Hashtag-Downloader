"""State SQLite · Журнал скачивания в SQLite"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import local
from typing import Any

from .i18n import tr
from .perf_metrics import incr

logger = logging.getLogger(__name__)
_THREAD_LOCAL = local()


@dataclass
class StateDirty:
    """State dirty · Изменённые ключи с последнего flush"""

    processed: set[str] = field(default_factory=set)
    grouped: set[str] = field(default_factory=set)
    hashes: set[str] = field(default_factory=set)
    deleted_processed: set[str] = field(default_factory=set)
    deleted_grouped: set[str] = field(default_factory=set)
    deleted_hashes: set[str] = field(default_factory=set)

    def has_changes(self) -> bool:
        return bool(
            self.processed
            or self.grouped
            or self.hashes
            or self.deleted_processed
            or self.deleted_grouped
            or self.deleted_hashes
        )

    def clear(self) -> None:
        self.processed.clear()
        self.grouped.clear()
        self.hashes.clear()
        self.deleted_processed.clear()
        self.deleted_grouped.clear()
        self.deleted_hashes.clear()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (
    key TEXT PRIMARY KEY,
    downloaded_at TEXT NOT NULL,
    files_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grouped (
    gid TEXT PRIMARY KEY,
    downloaded_at TEXT NOT NULL,
    files_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hashes (
    digest TEXT PRIMARY KEY,
    path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    root TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_root_message ON files(root, message_id);
"""


def sqlite_path_for(json_path: Path) -> Path:
    return json_path.with_suffix(".sqlite")


def _empty_state() -> dict[str, Any]:
    return {"processed": {}, "grouped": {}, "hashes": {}}


def _connect(path: Path) -> sqlite3.Connection:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = getattr(_THREAD_LOCAL, "sqlite_connections", None)
    if cache is None:
        cache = {}
        _THREAD_LOCAL.sqlite_connections = cache
    cached = cache.get(path)
    if cached is not None:
        return cached
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    cache[path] = conn
    incr("sqlite.connection_opens")
    return conn


def close_thread_connections() -> None:
    cache = getattr(_THREAD_LOCAL, "sqlite_connections", None)
    if not cache:
        return
    for conn in list(cache.values()):
        try:
            conn.close()
        except sqlite3.Error:
            pass
    cache.clear()


def load_sqlite(path: Path) -> dict[str, Any]:
    conn = _connect(path)
    state = _empty_state()
    for key, downloaded_at, files_json in conn.execute(
        "SELECT key, downloaded_at, files_json FROM processed",
    ):
        state["processed"][key] = {
            "downloaded_at": downloaded_at,
            "files": json.loads(files_json),
        }
    for gid, downloaded_at, files_json in conn.execute(
        "SELECT gid, downloaded_at, files_json FROM grouped",
    ):
        state["grouped"][gid] = {
            "downloaded_at": downloaded_at,
            "files": json.loads(files_json),
        }
    for digest, stored_path in conn.execute("SELECT digest, path FROM hashes"):
        state["hashes"][digest] = stored_path
    return state


def _save_sqlite_full(path: Path, state: dict[str, Any]) -> None:
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM processed")
        conn.execute("DELETE FROM grouped")
        conn.execute("DELETE FROM hashes")
        for key, entry in (state.get("processed") or {}).items():
            conn.execute(
                "INSERT INTO processed(key, downloaded_at, files_json) VALUES (?, ?, ?)",
                (
                    str(key),
                    str(entry.get("downloaded_at", "")),
                    json.dumps(entry.get("files") or [], ensure_ascii=False),
                ),
            )
        for gid, entry in (state.get("grouped") or {}).items():
            conn.execute(
                "INSERT INTO grouped(gid, downloaded_at, files_json) VALUES (?, ?, ?)",
                (
                    str(gid),
                    str(entry.get("downloaded_at", "")),
                    json.dumps(entry.get("files") or [], ensure_ascii=False),
                ),
            )
        for digest, stored_path in (state.get("hashes") or {}).items():
            conn.execute(
                "INSERT INTO hashes(digest, path) VALUES (?, ?)",
                (str(digest), str(stored_path)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _save_sqlite_incremental(path: Path, state: dict[str, Any], dirty: StateDirty) -> None:
    processed = state.get("processed") or {}
    grouped = state.get("grouped") or {}
    hashes = state.get("hashes") or {}
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
        for key in dirty.deleted_processed:
            conn.execute("DELETE FROM processed WHERE key = ?", (str(key),))
        for key in dirty.processed:
            entry = processed.get(key)
            if entry is None:
                conn.execute("DELETE FROM processed WHERE key = ?", (str(key),))
                continue
            conn.execute(
                "INSERT OR REPLACE INTO processed(key, downloaded_at, files_json) VALUES (?, ?, ?)",
                (
                    str(key),
                    str(entry.get("downloaded_at", "")),
                    json.dumps(entry.get("files") or [], ensure_ascii=False),
                ),
            )
        for gid in dirty.deleted_grouped:
            conn.execute("DELETE FROM grouped WHERE gid = ?", (str(gid),))
        for gid in dirty.grouped:
            entry = grouped.get(gid)
            if entry is None:
                conn.execute("DELETE FROM grouped WHERE gid = ?", (str(gid),))
                continue
            conn.execute(
                "INSERT OR REPLACE INTO grouped(gid, downloaded_at, files_json) VALUES (?, ?, ?)",
                (
                    str(gid),
                    str(entry.get("downloaded_at", "")),
                    json.dumps(entry.get("files") or [], ensure_ascii=False),
                ),
            )
        for digest in dirty.deleted_hashes:
            conn.execute("DELETE FROM hashes WHERE digest = ?", (str(digest),))
        for digest in dirty.hashes:
            stored_path = hashes.get(digest)
            if stored_path is None:
                conn.execute("DELETE FROM hashes WHERE digest = ?", (str(digest),))
                continue
            conn.execute(
                "INSERT OR REPLACE INTO hashes(digest, path) VALUES (?, ?)",
                (str(digest), str(stored_path)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def save_sqlite(
    path: Path,
    state: dict[str, Any],
    *,
    dirty: StateDirty | None = None,
    full: bool = False,
) -> None:
    if full or dirty is None or not dirty.has_changes():
        _save_sqlite_full(path, state)
        return
    _save_sqlite_incremental(path, state, dirty)


def migrate_json_to_sqlite(json_path: Path, sqlite_path: Path) -> dict[str, Any]:
    with json_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("processed", {})
    data.setdefault("grouped", {})
    data.setdefault("hashes", {})
    save_sqlite(sqlite_path, data)
    backup = json_path.with_suffix(".json.bak")
    if not backup.exists():
        try:
            json_path.rename(backup)
            logger.info(tr("log.state.journal_migrated", name=sqlite_path.name, backup=backup.name))
        except OSError as exc:
            logger.warning(tr("log.state.journal_rename_failed", path=json_path, exc=exc))
    return data


def sqlite_has_processed(sqlite_path: Path) -> bool:
    if not sqlite_path.is_file():
        return False
    conn = _connect(sqlite_path)
    row = conn.execute("SELECT 1 FROM processed LIMIT 1").fetchone()
    return row is not None


def sqlite_lookup_message_files(
    sqlite_path: Path,
    *,
    root: str,
    message_id: int,
) -> list[str]:
    if not sqlite_path.is_file():
        return []
    started = time.perf_counter()
    conn = _connect(sqlite_path)
    try:
        rows = conn.execute(
            "SELECT path FROM files WHERE root = ? AND message_id = ? ORDER BY path",
            (str(root), int(message_id)),
        ).fetchall()
        incr("sqlite.file_lookup_calls")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf sqlite.lookup_message_files: %.1fms (root=%s, message_id=%s, rows=%s)",
                (time.perf_counter() - started) * 1000.0,
                root,
                message_id,
                len(rows),
            )
        return [str(row[0]) for row in rows]
    finally:
        pass


def sqlite_add_message_files(
    sqlite_path: Path,
    *,
    root: str,
    message_id: int,
    paths: list[str],
) -> None:
    if not paths:
        return
    started = time.perf_counter()
    conn = _connect(sqlite_path)
    try:
        conn.execute("BEGIN")
        for raw in paths:
            file_path = Path(raw)
            try:
                stat = file_path.stat()
            except OSError:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO files(root, message_id, path, size, mtime_ns)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(root), int(message_id), str(file_path), int(stat.st_size), int(stat.st_mtime_ns)),
            )
        conn.commit()
        incr("sqlite.file_add_calls")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf sqlite.add_message_files: %.1fms (root=%s, message_id=%s, paths=%s)",
                (time.perf_counter() - started) * 1000.0,
                root,
                message_id,
                len(paths),
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        pass


def sqlite_remove_missing_files(
    sqlite_path: Path,
    *,
    paths: list[str],
) -> None:
    if not paths or not sqlite_path.is_file():
        return
    started = time.perf_counter()
    conn = _connect(sqlite_path)
    try:
        conn.execute("BEGIN")
        for raw in paths:
            conn.execute("DELETE FROM files WHERE path = ?", (str(raw),))
        conn.commit()
        incr("sqlite.file_remove_calls")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf sqlite.remove_missing_files: %.1fms (paths=%s)",
                (time.perf_counter() - started) * 1000.0,
                len(paths),
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        pass