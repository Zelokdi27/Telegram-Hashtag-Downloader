"""Download accounting tests · Сверка счётчиков медиа в отчёте скачивания"""

from __future__ import annotations

from tests.test_scenarios import _search_channel


def test_mixed_run_media_accounted_matches_limit(channel_feed, worker_factory):
    """Rerun: media_accounted = limit, files + files_reused match · Повторный прогон"""
    for index in range(1, 6):
        channel_feed.add_single(600 + index, caption=f"post-{index}")
    channel_feed.add_album(grouped_id=9600, count=5, start_id=700)

    worker = worker_factory(max_posts=10)
    first = worker.process_messages(_search_channel(worker, channel_feed))
    assert first.files == 10
    assert first.media_accounted == 10
    assert first.files_reused == 0

    worker2 = worker_factory(max_posts=10)
    second = worker2.process_messages(_search_channel(worker2, channel_feed))

    assert second.files == 0
    assert second.media_accounted == 10
    assert second.files_reused == 10
    assert second.files + second.files_reused == second.media_found == 10