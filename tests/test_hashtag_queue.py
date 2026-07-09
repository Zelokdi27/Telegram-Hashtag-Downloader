"""Hashtag queue tests · Тесты очереди хештегов"""

from __future__ import annotations

from pathlib import Path

from app.hashtag_queue import (
    load_hashtag_queue,
    normalize_hashtag_queue,
    save_hashtag_queue,
)


def test_normalize_hashtag_queue_dedupes_and_preserves_order() -> None:
    tags = normalize_hashtag_queue(["Orphie", "orphie", " #Tag ", "tag", ""])
    assert tags == ["Orphie", "Tag"]


def test_hashtag_queue_roundtrip(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "hashtag_queue.json"
    monkeypatch.setattr("app.hashtag_queue.QUEUE_PATH", path)

    save_hashtag_queue(["Orphie", "Cat", "Orphie"])
    assert load_hashtag_queue() == ["Orphie", "Cat"]
