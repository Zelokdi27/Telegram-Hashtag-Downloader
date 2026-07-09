"""Preview memory tests · Освобождение памяти между партиями превью"""

from __future__ import annotations

import queue

from app.preview_core import PreviewItem, release_preview_items, release_sequential_batch_memory
from app.tg_hashtag_dl import HashtagDownloader


def test_clear_preview_session_caches(worker_factory, channel_feed):
    channel_feed.add_album(grouped_id=77, count=3, start_id=1)
    worker: HashtagDownloader = worker_factory()
    messages = worker.collect_candidates()
    album = worker.fetch_album_messages(messages[0])
    assert worker._album_cache

    worker.clear_preview_session_caches()
    assert not worker._album_cache
    assert not worker._album_take_cache


def test_release_sequential_batch_memory_clears_items(channel_feed, worker_factory):
    channel_feed.add_single(10, caption="one")
    worker = worker_factory()
    message = worker.collect_candidates()[0]
    item = PreviewItem(
        message=message,
        channel="ch",
        kind="photo",
        summary="photo",
        preview_path="/tmp/x.jpg",
    )
    batch = [item]
    item_queue: queue.Queue = queue.Queue()
    item_queue.put(item)

    release_sequential_batch_memory(
        batch_items=batch,
        item_queue=item_queue,
        workers=[worker],
    )

    assert batch == []
    assert item.message is None
    assert item.preview_path is None
    assert item_queue.empty()


def test_release_preview_items_preserves_list_objects():
    items: list[PreviewItem] = []
    release_preview_items(items)