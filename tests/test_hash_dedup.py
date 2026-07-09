"""Hash dedup tests · Однопроходный hash-dedup"""

from __future__ import annotations

from app.tg_hashtag_dl import HashDedupResult


def test_resolve_hash_dedup_registers_then_detects_duplicate(worker_factory, tmp_path):
    worker = worker_factory()
    payload = b"identical-payload-for-dedup-test"

    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(payload)
    second_path.write_bytes(payload)

    first = worker._resolve_hash_dedup(first_path)
    assert isinstance(first, HashDedupResult)
    assert first.digest
    assert first.duplicate_path is None
    assert first.registered is True

    second = worker._resolve_hash_dedup(second_path)
    assert second.digest == first.digest
    assert second.registered is False
    assert second.duplicate_path == str(first_path.resolve())


def test_resolve_hash_dedup_registers_distinct_files(worker_factory, tmp_path):
    worker = worker_factory()
    path_a = tmp_path / "a.jpg"
    path_b = tmp_path / "b.jpg"
    path_a.write_bytes(b"content-a")
    path_b.write_bytes(b"content-b")

    result_a = worker._resolve_hash_dedup(path_a)
    result_b = worker._resolve_hash_dedup(path_b)

    assert result_a.registered and result_b.registered
    assert result_a.digest != result_b.digest
    assert result_a.duplicate_path is None
    assert result_b.duplicate_path is None
    assert len(worker._ensure_hash_index()) == 2