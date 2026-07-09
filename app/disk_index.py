"""Disk index cache · Кэш индекса файлов на диске"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .i18n import tr
from .perf_metrics import incr

logger = logging.getLogger(__name__)

IndexDict = dict[int, list[str]]
BuildIndexFn = Callable[[], "CachedDiskIndex"]


@dataclass(frozen=True)
class FolderSignature:
    file_count: int
    total_size: int
    max_mtime_ns: int

    def matches(self, other: FolderSignature) -> bool:
        return (
            self.file_count == other.file_count
            and self.total_size == other.total_size
            and self.max_mtime_ns == other.max_mtime_ns
        )


@dataclass
class CachedDiskIndex:
    signature: FolderSignature
    index: IndexDict


def build_disk_index_snapshot(
    root: Path,
    *,
    pattern: re.Pattern[str],
) -> CachedDiskIndex:
    """Disk index snapshot · Один проход по дереву каталога"""
    started = time.perf_counter()
    index: IndexDict = {}
    file_count = 0
    total_size = 0
    max_mtime_ns = 0
    if not root.is_dir():
        return CachedDiskIndex(FolderSignature(0, 0, 0), {})
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if not pattern.search(path.name):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        file_count += 1
        total_size += stat.st_size
        max_mtime_ns = max(max_mtime_ns, stat.st_mtime_ns)
        match = pattern.search(path.name)
        if not match:
            continue
        message_id = int(match.group("id"))
        resolved = str(path.resolve())
        bucket = index.setdefault(message_id, [])
        if resolved not in bucket:
            bucket.append(resolved)
    for message_id in index:
        index[message_id] = sorted(index[message_id])
    snapshot = CachedDiskIndex(
        signature=FolderSignature(file_count, total_size, max_mtime_ns),
        index=index,
    )
    incr("disk_index.snapshot_builds")
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "perf disk_index.snapshot_build: %.1fms (root=%s, files=%s, ids=%s)",
            (time.perf_counter() - started) * 1000.0,
            root,
            file_count,
            len(index),
        )
    return snapshot


def compute_folder_signature(
    root: Path,
    *,
    pattern: re.Pattern[str],
) -> FolderSignature:
    file_count = 0
    total_size = 0
    max_mtime_ns = 0
    if not root.is_dir():
        return FolderSignature(0, 0, 0)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if not pattern.search(path.name):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        file_count += 1
        total_size += stat.st_size
        max_mtime_ns = max(max_mtime_ns, stat.st_mtime_ns)
    return FolderSignature(file_count, total_size, max_mtime_ns)


def _sidecar_path(cache_dir: Path, root: Path) -> Path:
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:20]
    return cache_dir / f"{digest}.json"


def _load_sidecar(path: Path) -> CachedDiskIndex | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        sig = raw.get("signature", {})
        signature = FolderSignature(
            int(sig.get("file_count", 0)),
            int(sig.get("total_size", 0)),
            int(sig.get("max_mtime_ns", 0)),
        )
        index_raw = raw.get("index", {})
        index: IndexDict = {
            int(key): list(value) for key, value in index_raw.items()
        }
        return CachedDiskIndex(signature=signature, index=index)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _save_sidecar(path: Path, root: Path, cached: CachedDiskIndex) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "root": str(root.resolve()),
        "signature": {
            "file_count": cached.signature.file_count,
            "total_size": cached.signature.total_size,
            "max_mtime_ns": cached.signature.max_mtime_ns,
        },
        "index": {str(k): v for k, v in cached.index.items()},
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class DiskIndexStore:
    """Disk index store · Кэш индекса канала в памяти"""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, CachedDiskIndex] = {}
        self._memory_dirty: set[str] = set()

    def get_index(
        self,
        root: Path,
        *,
        pattern: re.Pattern[str],
        builder: BuildIndexFn,
    ) -> IndexDict:
        resolved = root.resolve()
        key = str(resolved)
        cached = self._memory.get(key)
        if cached is not None:
            incr("disk_index.memory_hits")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("perf disk_index.memory_hit (root=%s, ids=%s)", resolved, len(cached.index))
            return cached.index

        sidecar = _load_sidecar(_sidecar_path(self._cache_dir, resolved))
        if sidecar is not None:
            self._memory[key] = sidecar
            self._memory_dirty.discard(key)
            logger.debug(tr("log.disk.index_sidecar", root=resolved, n=len(sidecar.index)))
            incr("disk_index.sidecar_hits")
            return sidecar.index

        started = time.perf_counter()
        built = builder()
        cached = CachedDiskIndex(signature=built.signature, index=built.index)
        self._memory[key] = cached
        self._memory_dirty.discard(key)
        _save_sidecar(_sidecar_path(self._cache_dir, resolved), resolved, cached)
        logger.debug(tr("log.disk.index_rebuilt", root=resolved, n=len(cached.index)))
        incr("disk_index.rebuilds")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf disk_index.rebuild: %.1fms (root=%s, files=%s, ids=%s)",
                (time.perf_counter() - started) * 1000.0,
                resolved,
                cached.signature.file_count,
                len(cached.index),
            )
        return cached.index

    def add_paths(self, root: Path, message_id: int, paths: list[str]) -> None:
        if not paths:
            return
        resolved = root.resolve()
        key = str(resolved)
        cached = self._memory.get(key)
        if cached is None:
            return
        bucket = cached.index.setdefault(message_id, [])
        added = False
        for raw in paths:
            try:
                path = Path(raw).resolve()
                stat = path.stat()
            except OSError:
                continue
            resolved_str = str(path)
            if resolved_str not in bucket:
                bucket.append(resolved_str)
                added = True
                cached.signature = FolderSignature(
                    cached.signature.file_count + 1,
                    cached.signature.total_size + stat.st_size,
                    max(cached.signature.max_mtime_ns, stat.st_mtime_ns),
                )
        if added:
            bucket.sort()
            self._memory_dirty.add(key)
            _save_sidecar(_sidecar_path(self._cache_dir, resolved), resolved, cached)
            incr("disk_index.sidecar_writes")

    def invalidate_root(self, root: Path) -> None:
        resolved = root.resolve()
        key = str(resolved)
        self._memory.pop(key, None)
        self._memory_dirty.discard(key)
        sidecar = _sidecar_path(self._cache_dir, resolved)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

    def clear_all(self) -> None:
        self._memory.clear()
        if not self._cache_dir.is_dir():
            return
        for path in self._cache_dir.glob("*.json"):
            try:
                path.unlink()
            except OSError:
                pass


_shared_store: DiskIndexStore | None = None


def shared_disk_index_store(cache_dir: Path) -> DiskIndexStore:
    global _shared_store
    if _shared_store is None or _shared_store._cache_dir != cache_dir:
        _shared_store = DiskIndexStore(cache_dir)
    return _shared_store