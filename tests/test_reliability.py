"""Reliability · Надёжность скачивания"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.i18n import tr
from app.tg_hashtag_dl import DownloadStats, format_download_summary
from tests.test_scenarios import _photo_files, _search_channel


def test_partial_album_on_disk_downloads_remaining(
    channel_feed,
    worker_factory,
    download_root: Path,
):
    channel_feed.add_album(grouped_id=9700, count=5, start_id=900)
    first = worker_factory(max_posts=2)
    stats1 = first.process_messages(_search_channel(first, channel_feed))
    assert stats1.files == 2
    assert len(_photo_files(download_root)) == 2

    second = worker_factory(max_posts=0)
    stats2 = second.process_messages(_search_channel(second, channel_feed))

    assert stats2.files == 3
    assert len(_photo_files(download_root)) == 5


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_download_summary_media_shortfall(locale, download_root: Path):
    stats = DownloadStats(
        hashtag="Orphie",
        download_dir=str(download_root),
        publications=100,
        media_found=350,
        media_accounted=323,
        media_shortfall=27,
        files=300,
        files_reused=23,
        batches=1,
    )

    text = format_download_summary(stats)

    assert tr("summary.media_in_task", n=350) in text
    assert tr("summary.shortfall", n=27) in text
