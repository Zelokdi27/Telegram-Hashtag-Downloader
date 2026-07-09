from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .download_options import MediaFilterSettings, parse_media_filter
from .dl_utils import safe_name
from .i18n import tr


@dataclass(frozen=True)
class HashDedupResult:
    digest: str | None
    duplicate_path: str | None
    registered: bool


@dataclass
class ProgressState:
    phase: str = "idle"
    found: int = 0
    total: int = 0
    processed: int = 0
    files: int = 0
    media_total: int = 0
    skipped: int = 0
    bytes_downloaded: int = 0
    speed_bps: float = 0.0
    flood_wait_deadline: float = 0.0
    current: str = ""
    alert: str = ""
    batch_index: int = 0
    batch_total: int = 0
    batch_hashtag: str = ""
    batch_channel: str = ""


@dataclass
class DownloadStats:
    found: int = 0
    total: int = 0
    with_media: int = 0
    queue_messages: int = 0
    publications: int = 0
    media_expected: int = 0
    album_publications: int = 0
    single_publications: int = 0
    posts: int = 0
    files: int = 0
    files_already: int = 0
    media_accounted: int = 0
    files_reused: int = 0
    skipped: int = 0
    skipped_on_disk: int = 0
    skipped_no_media: int = 0
    skipped_album: int = 0
    skipped_media_filter: int = 0
    skipped_excluded_hashtag: int = 0
    skipped_required_hashtag: int = 0
    skipped_duplicates: int = 0
    redownloaded: int = 0
    errors: int = 0
    failed_posts: list[int] = field(default_factory=list)
    stopped: bool = False
    hashtag: str = ""
    channel_label: str = ""
    download_dir: str = ""
    batches: int = 0
    from_preview: bool = False
    publications_accounted: int = 0
    publications_on_disk: int = 0
    media_found: int = 0
    media_shortfall: int = 0


@dataclass
class MissingPostRef:
    message_id: int
    channel: str
    hashtag: str = ""
    channel_filter: str = ""


@dataclass
class IntegrityStats:
    found: int = 0
    with_media: int = 0
    media_files: int = 0
    files_on_disk: int = 0
    skipped: int = 0
    skipped_dedup: int = 0
    skipped_album: int = 0
    files_missing: int = 0
    missing_post_ids: list[int] = field(default_factory=list)
    missing_refs: list[MissingPostRef] = field(default_factory=list)
    extra_on_disk: int = 0
    hashtag: str = ""
    channel_label: str = ""
    download_dir: str = ""


def merge_integrity_stats(target: IntegrityStats, part: IntegrityStats) -> None:
    target.found += part.found
    target.with_media += part.with_media
    target.media_files += part.media_files
    target.files_on_disk += part.files_on_disk
    target.skipped += part.skipped
    target.skipped_dedup += part.skipped_dedup
    target.skipped_album += part.skipped_album
    target.files_missing += part.files_missing
    target.missing_post_ids.extend(part.missing_post_ids)
    target.missing_refs.extend(part.missing_refs)
    target.extra_on_disk += part.extra_on_disk


def resolve_summary_open_dir(stats: DownloadStats) -> Path:
    """Summary open dir · Папка для «Открыть папку»"""
    root = Path(stats.download_dir)
    if stats.hashtag and stats.batches <= 1:
        return root / safe_name(stats.hashtag)
    return root


def resolve_integrity_open_dir(stats: IntegrityStats) -> Path:
    """Integrity open dir · Папка отчёта целостности"""
    root = Path(stats.download_dir)
    if stats.hashtag:
        return root / safe_name(stats.hashtag)
    return root


def _publication_type_hint(stats: DownloadStats) -> str:
    parts: list[str] = []
    if stats.album_publications:
        parts.append(tr("summary.albums", n=stats.album_publications))
    if stats.single_publications:
        parts.append(tr("summary.singles", n=stats.single_publications))
    return f" ({', '.join(parts)})" if parts else ""


def format_download_summary(stats: DownloadStats) -> str:
    lines = [tr("summary.download.title"), ""]

    if stats.hashtag:
        header = f"#{stats.hashtag}"
        if stats.channel_label:
            header += f" · {stats.channel_label}"
        elif stats.hashtag:
            header += tr("summary.download.all_channels")
        lines.append(header)
    if stats.download_dir:
        lines.append(tr("summary.folder", path=resolve_summary_open_dir(stats)))
    lines.append("")

    scope = tr("summary.scope.preview") if stats.from_preview else tr("summary.scope.search")
    lines.append(scope)

    if stats.publications:
        lines.append(
            tr("summary.publications", n=stats.publications, hint=_publication_type_hint(stats)),
        )
        media_count = stats.media_found or stats.media_expected
        if media_count:
            lines.append(tr("summary.media_count", n=media_count))
    elif stats.with_media:
        lines.append(tr("summary.with_media", n=stats.with_media))

    if not stats.from_preview:
        filtered_out = max(0, stats.found - stats.queue_messages)
        if filtered_out:
            reasons: list[str] = []
            if stats.skipped_excluded_hashtag:
                reasons.append(tr("summary.filtered.reason.excluded", n=stats.skipped_excluded_hashtag))
            if stats.skipped_required_hashtag:
                reasons.append(tr("summary.filtered.reason.required", n=stats.skipped_required_hashtag))
            if stats.skipped_media_filter:
                reasons.append(tr("summary.filtered.reason.media", n=stats.skipped_media_filter))
            other_filtered = (
                filtered_out
                - stats.skipped_excluded_hashtag
                - stats.skipped_required_hashtag
                - stats.skipped_media_filter
            )
            if other_filtered > 0:
                reasons.append(tr("summary.filtered.reason.date_channel", n=other_filtered))
            reason_text = f" ({', '.join(reasons)})" if reasons else ""
            lines.append(tr("summary.filtered.label", n=filtered_out, reasons=reason_text))
    lines.append("")

    lines.append(tr("summary.totals"))
    media_total = stats.media_found or stats.media_expected
    accounted = stats.media_accounted
    reused = stats.files_reused

    if media_total:
        lines.append(tr("summary.media_in_task", n=media_total))

    if stats.files:
        lines.append(tr("summary.new_files", n=stats.files))
    elif stats.posts and not stats.files:
        lines.append(tr("summary.downloaded_pubs", n=stats.posts))

    if stats.skipped_duplicates:
        lines.append(tr("summary.duplicates", n=stats.skipped_duplicates))

    if reused:
        lines.append(tr("summary.reused", n=reused))

    if accounted and media_total:
        lines.append(tr("summary.slots", done=accounted, total=media_total))

    if stats.publications_on_disk or stats.skipped_on_disk:
        pubs_skipped = stats.publications_on_disk or stats.skipped_on_disk
        lines.append(tr("summary.pubs_skipped", n=pubs_skipped))

    if stats.redownloaded:
        lines.append(tr("summary.redownloaded", n=stats.redownloaded))

    if stats.media_shortfall:
        lines.append(tr("summary.shortfall", n=stats.media_shortfall))

    if accounted > 0 and stats.files:
        parts: list[str] = [tr("summary.sum_part.new", n=stats.files)]
        if reused:
            parts.append(tr("summary.sum_part.reused", n=reused))
        if stats.skipped_duplicates:
            parts.append(tr("summary.sum_part.duplicates", n=stats.skipped_duplicates))
        if stats.media_shortfall:
            parts.append(tr("summary.sum_part.shortfall", n=stats.media_shortfall))
        accounted_parts = stats.files + reused + stats.skipped_duplicates + stats.media_shortfall
        if accounted_parts == accounted:
            lines.append(tr("summary.sum", total=accounted, parts=" + ".join(parts)))

    if stats.skipped_no_media:
        lines.append(tr("summary.no_media", n=stats.skipped_no_media))

    nothing_new = (
        not stats.files
        and not stats.redownloaded
        and stats.skipped_on_disk
        and not stats.errors
        and not stats.stopped
    )
    if nothing_new:
        lines.append("")
        lines.append(tr("summary.all_done"))
    elif not stats.files and not stats.skipped_on_disk and not stats.errors and not stats.stopped:
        lines.append(tr("summary.nothing"))

    if stats.errors:
        lines.append("")
        lines.append(tr("summary.errors", n=stats.errors))
        if stats.failed_posts:
            preview = ", ".join(str(item) for item in stats.failed_posts[:12])
            suffix = "…" if len(stats.failed_posts) > 12 else ""
            lines.append(tr("summary.failed_ids", ids=f"{preview}{suffix}"))
    elif stats.batches <= 1:
        lines.append("")
        lines.append(tr("summary.no_errors"))

    if stats.batches > 1:
        lines.append("")
        lines.append(tr("summary.batches", n=stats.batches))

    if stats.stopped:
        lines.append("")
        lines.append(tr("summary.stopped"))

    return "\n".join(lines)


def format_integrity_summary(stats: IntegrityStats) -> str:
    lines = [tr("summary.integrity.title"), ""]
    if stats.hashtag:
        lines.append(tr("summary.integrity.hashtag", tag=stats.hashtag))
    if stats.channel_label:
        lines.append(tr("summary.integrity.channel", name=stats.channel_label))
    if stats.download_dir:
        lines.append(tr("summary.folder", path=stats.download_dir))
    lines.append("")
    lines.append(tr("summary.integrity.found", n=stats.found))
    lines.append(tr("summary.with_media", n=stats.with_media))
    if stats.skipped_album:
        lines.append(tr("summary.integrity.albums_collapsed", n=stats.skipped_album))
    if stats.media_files:
        lines.append(tr("summary.integrity.media_files", n=stats.media_files))
    lines.append(tr("summary.integrity.on_disk", n=stats.files_on_disk))
    lines.append("")
    lines.append(tr("summary.integrity.skipped", n=stats.skipped))
    if stats.skipped_dedup:
        lines.append(tr("summary.integrity.dedup", n=stats.skipped_dedup))
    if stats.files_missing:
        lines.append(tr("summary.integrity.missing", n=stats.files_missing))
    if stats.missing_post_ids:
        preview = ", ".join(str(item) for item in stats.missing_post_ids[:20])
        suffix = "…" if len(stats.missing_post_ids) > 20 else ""
        lines.append(tr("summary.integrity.missing_ids", ids=f"{preview}{suffix}"))
    if stats.extra_on_disk:
        lines.append(tr("summary.integrity.extra", n=stats.extra_on_disk))
    return "\n".join(lines)


def merge_download_stats(target: DownloadStats, source: DownloadStats) -> DownloadStats:
    target.found += source.found
    target.total += source.total
    target.with_media += source.with_media
    target.queue_messages += source.queue_messages
    target.publications += source.publications
    target.media_expected += source.media_expected
    target.album_publications += source.album_publications
    target.single_publications += source.single_publications
    target.posts += source.posts
    target.files += source.files
    target.files_already += source.files_already
    target.media_accounted += source.media_accounted
    target.files_reused += source.files_reused
    target.skipped += source.skipped
    target.skipped_on_disk += source.skipped_on_disk
    target.skipped_no_media += source.skipped_no_media
    target.skipped_album += source.skipped_album
    target.skipped_media_filter += source.skipped_media_filter
    target.skipped_excluded_hashtag += source.skipped_excluded_hashtag
    target.skipped_required_hashtag += source.skipped_required_hashtag
    target.skipped_duplicates += source.skipped_duplicates
    target.redownloaded += source.redownloaded
    target.errors += source.errors
    target.failed_posts.extend(source.failed_posts)
    target.publications_accounted += source.publications_accounted
    target.publications_on_disk += source.publications_on_disk
    target.media_found += source.media_found
    target.media_shortfall += source.media_shortfall
    target.stopped = target.stopped or source.stopped
    target.from_preview = target.from_preview or source.from_preview
    if not target.hashtag and source.hashtag:
        target.hashtag = source.hashtag
    if not target.channel_label and source.channel_label:
        target.channel_label = source.channel_label
    target.batches += 1
    return target


ProgressCallback = Callable[[ProgressState], None]


@dataclass
class AppConfig:
    api_id: int
    api_hash: str
    hashtag: str
    download_dir: Path
    page_limit: int
    max_posts: int
    session_name: str
    state_file: Path
    date_from: date | None = None
    date_to: date | None = None
    channel_filter: str = ""
    media_filter: MediaFilterSettings = field(default_factory=parse_media_filter)
    folder_by_date: bool = False
    caption_in_filename: bool = False
    caption_max_len: int = 40
    dedup_by_hash: bool = True
    download_retries: int = 3
    download_parallel_workers: int = 1
    exclude_hashtags: tuple[str, ...] = ()
    required_hashtags: tuple[str, ...] = ()