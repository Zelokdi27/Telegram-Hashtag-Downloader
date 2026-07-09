"""Sequential preview index · Индекс пошагового превью"""

from __future__ import annotations

import queue

import pytest

from app.i18n import tr
from app.telethon_loop import run_async
from app.preview_index import (
    PreviewIndexSummary,
    build_preview_index,
    collect_sequential_preview_batch,
    format_sequential_index_status,
    merge_preview_summaries,
    sequential_batch_total,
    sequential_media_cap,
)


def test_build_preview_index_counts_publications(channel_feed, worker_factory):
    channel_feed.add_single(10, caption="one")
    channel_feed.add_album(grouped_id=100, count=4, start_id=20)
    channel_feed.add_text_post(99, caption="text only")

    worker = worker_factory()
    candidates = worker.collect_candidates()
    entries, summary = build_preview_index(
        candidates,
        hashtag="orphie",
        channel_filter="orphie_channel",
    )

    assert summary.publications_total == 2
    assert summary.album_groups == 1
    assert summary.media_estimate == 2
    assert len(entries) == 2
    assert entries[0].message_id == 10
    assert entries[1].grouped_id == 100


def test_merge_preview_summaries():
    merged = merge_preview_summaries(
        [
            PreviewIndexSummary(publications_total=10, album_groups=1, media_estimate=12),
            PreviewIndexSummary(publications_total=5, album_groups=0, media_estimate=5),
        ],
    )
    assert merged.publications_total == 15
    assert merged.album_groups == 1
    assert merged.media_estimate == 17
    assert merged.estimated_batches(200) == 1


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_sequential_index_status_without_limit(locale):
    summary = PreviewIndexSummary(publications_total=7600, album_groups=400, media_estimate=7600)
    batch_total = sequential_batch_total(summary, batch_size=200, batch_number=3)
    batch_label = tr("sequential.batch_approx", n=3, total=batch_total)
    text = format_sequential_index_status(
        summary,
        batch_number=3,
        publication_cursor=400,
        batch_size=200,
        files_downloaded=47,
        media_shown=412,
    )
    expected = " · ".join(
        [
            tr("sequential.title", batch=batch_label),
            tr("sequential.media_at_least", shown=412, estimate=7600),
            tr("sequential.publications", cursor=400, total=7600),
            tr("sequential.downloaded", n=47),
        ],
    )
    assert text == expected


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_sequential_index_status_with_media_limit(locale):
    summary = PreviewIndexSummary(publications_total=35, album_groups=0, media_estimate=35)
    batch_total = sequential_batch_total(summary, batch_size=20, media_limit=50)
    batch_label = tr("sequential.batch_exact", n=2, total=batch_total)
    text = format_sequential_index_status(
        summary,
        batch_number=2,
        publication_cursor=20,
        batch_size=20,
        media_shown=25,
        media_limit=50,
    )
    expected = " · ".join(
        [
            tr("sequential.title", batch=batch_label),
            tr("sequential.media_limit", shown=25, limit=50),
            tr("sequential.publications", cursor=20, total=35),
        ],
    )
    assert text == expected
    assert "≥" not in text


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_sequential_index_status_limit_exceeds_estimate(locale):
    summary = PreviewIndexSummary(publications_total=52, album_groups=2, media_estimate=52)
    batch_total = sequential_batch_total(summary, batch_size=34, media_limit=75, batch_number=3)
    batch_label = tr("sequential.batch_exact", n=3, total=batch_total)
    text = format_sequential_index_status(
        summary,
        batch_number=3,
        publication_cursor=52,
        batch_size=34,
        media_shown=75,
        media_limit=75,
    )
    expected = " · ".join(
        [
            tr("sequential.title", batch=batch_label),
            tr("sequential.media_limit", shown=75, limit=75),
            tr("sequential.publications", cursor=52, total=52),
        ],
    )
    assert text == expected


def test_sequential_media_cap_and_batches():
    summary = PreviewIndexSummary(publications_total=500, album_groups=10, media_estimate=500)
    assert sequential_media_cap(summary, media_limit=100) == 100
    assert sequential_media_cap(summary, media_limit=0) == 500
    assert sequential_batch_total(summary, batch_size=200, media_limit=100) == 1
    assert sequential_batch_total(summary, batch_size=200, media_limit=0) == 3
    assert sequential_batch_total(summary, batch_size=34, media_limit=75, batch_number=3) == 3


def test_collect_sequential_preview_batch_respects_limit(channel_feed, worker_factory):
    for msg_id in range(300, 310):
        channel_feed.add_single(msg_id, caption=f"post {msg_id}")

    worker = worker_factory()
    candidates = worker.collect_candidates()
    entries, _ = build_preview_index(
        candidates,
        hashtag="orphie",
        channel_filter="orphie_channel",
    )

    items, cursor = run_async(
        collect_sequential_preview_batch(
            worker,
            entries,
            0,
            batch_media_size=3,
        ),
    )

    assert len(items) == 3
    assert cursor == 3
    assert {item.message.id for item in items} == {300, 301, 302}


def test_collect_sequential_preview_batch_second_slice(channel_feed, worker_factory):
    for msg_id in range(400, 408):
        channel_feed.add_single(msg_id, caption=f"p {msg_id}")

    worker = worker_factory()
    entries, _ = build_preview_index(
        worker.collect_candidates(),
        hashtag="orphie",
        channel_filter="orphie_channel",
    )

    _, cursor1 = run_async(
        collect_sequential_preview_batch(worker, entries, 0, batch_media_size=3),
    )
    items2, cursor2 = run_async(
        collect_sequential_preview_batch(worker, entries, cursor1, batch_media_size=3),
    )

    assert len(items2) == 3
    assert cursor1 == 3
    assert cursor2 == 6
    assert items2[0].message.id == 403


def test_collect_sequential_preview_batch_streams_to_queue(channel_feed, worker_factory):
    for msg_id in range(500, 505):
        channel_feed.add_single(msg_id, caption=f"stream {msg_id}")

    worker = worker_factory()
    entries, _ = build_preview_index(
        worker.collect_candidates(),
        hashtag="orphie",
        channel_filter="orphie_channel",
    )
    item_queue: queue.Queue = queue.Queue()

    items, cursor = run_async(
        collect_sequential_preview_batch(
            worker,
            entries,
            0,
            batch_media_size=3,
            item_queue=item_queue,
        ),
    )

    streamed = []
    while True:
        entry = item_queue.get_nowait()
        if entry is None:
            break
        streamed.append(entry)

    assert len(items) == 3
    assert len(streamed) == 3
    assert streamed == items
    assert cursor == 3


def test_sequential_batches_respect_global_media_limit(channel_feed, worker_factory):
    for msg_id in range(600, 610):
        channel_feed.add_single(msg_id, caption=f"lim {msg_id}")

    worker = worker_factory()
    entries, _ = build_preview_index(
        worker.collect_candidates(),
        hashtag="orphie",
        channel_filter="orphie_channel",
    )

    media_limit = 7
    batch_size = 3
    cursor = 0
    total = 0
    while cursor < len(entries):
        if total >= media_limit:
            break
        effective = min(batch_size, media_limit - total)
        items, cursor = run_async(
            collect_sequential_preview_batch(
                worker,
                entries,
                cursor,
                batch_media_size=effective,
            ),
        )
        total += len(items)
        if not items:
            break

    assert total == media_limit
