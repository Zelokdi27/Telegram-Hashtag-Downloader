"""Parallel counting tests · Счётчик stats.files при параллельном скачивании"""

from __future__ import annotations

from dataclasses import replace

from app.telethon_loop import AsyncMethodFacade
from app.tg_hashtag_dl import HashtagDownloader
from tests.conftest import build_config
from tests.test_scenarios import _search_channel


def test_parallel_download_counts_unique_files(
    channel_feed,
    sim_client,
    tmp_path,
    download_root,
):
    for index in range(1, 9):
        channel_feed.add_single(800 + index, caption=f"parallel-{index}")

    config = replace(build_config(tmp_path, download_root, max_posts=0), download_parallel_workers=2)
    worker = AsyncMethodFacade(HashtagDownloader(sim_client, config))

    stats = worker.process_messages(_search_channel(worker, channel_feed))

    assert stats.files == 8
    assert len(list(download_root.rglob("*.jpg"))) == 8