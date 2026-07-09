"""Download summary · Отчёт скачивания"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.i18n import tr
from app.tg_hashtag_dl import DownloadStats, format_download_summary, resolve_summary_open_dir, safe_name


def test_resolve_summary_open_dir_single_hashtag(download_root: Path):
    stats = DownloadStats(
        hashtag="Orphie",
        download_dir=str(download_root),
        batches=1,
    )

    assert resolve_summary_open_dir(stats) == download_root / safe_name("Orphie")


def test_resolve_summary_open_dir_batch_mode(download_root: Path):
    stats = DownloadStats(
        hashtag="Orphie",
        download_dir=str(download_root),
        batches=2,
    )

    assert resolve_summary_open_dir(stats) == download_root


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_download_summary_header_and_files(locale, download_root: Path):
    stats = DownloadStats(
        hashtag="test",
        channel_label="@channel",
        download_dir=str(download_root),
        publications=3,
        album_publications=1,
        single_publications=2,
        media_found=7,
        files=5,
        batches=1,
    )

    text = format_download_summary(stats)
    hint = f" ({tr('summary.albums', n=1)}, {tr('summary.singles', n=2)})"

    assert "#test · @channel" in text
    assert tr("summary.publications", n=3, hint=hint) in text
    assert tr("summary.media_count", n=7) in text
    assert tr("summary.new_files", n=5) in text
    assert str(resolve_summary_open_dir(stats)) in text
    assert tr("summary.no_errors") in text


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_download_summary_mixed_run(locale, download_root: Path):
    stats = DownloadStats(
        hashtag="Orphie",
        download_dir=str(download_root),
        publications=127,
        album_publications=15,
        single_publications=112,
        media_found=150,
        media_accounted=150,
        files=123,
        files_reused=25,
        files_already=67,
        publications_on_disk=40,
        skipped_duplicates=2,
        batches=1,
    )

    text = format_download_summary(stats)

    assert tr("summary.media_in_task", n=150) in text
    assert tr("summary.new_files", n=123) in text
    assert tr("summary.reused", n=25) in text
    assert tr("summary.pubs_skipped", n=40) in text
    assert tr(
        "summary.sum",
        total=150,
        parts=" + ".join(
            [
                tr("summary.sum_part.new", n=123),
                tr("summary.sum_part.reused", n=25),
                tr("summary.sum_part.duplicates", n=2),
            ],
        ),
    ) in text
    assert tr("summary.duplicates", n=2) in text
    assert tr("summary.reused", n=67) not in text


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_download_summary_from_preview(locale, download_root: Path):
    stats = DownloadStats(
        hashtag="preview",
        download_dir=str(download_root),
        from_preview=True,
        publications=2,
        media_found=4,
        files=4,
        batches=1,
    )

    text = format_download_summary(stats)

    assert tr("summary.scope.preview") in text
    assert tr("summary.filtered.label", n=0, reasons="") not in text
