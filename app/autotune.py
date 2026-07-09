"""Autotune · Локальная проверка производительности и рекомендации"""

from __future__ import annotations

import io
import json
import logging
import os
import platform
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .config_store import AUTOTUNE_PROFILE_PATH, SettingsData
from .dl_types import AppConfig
from .download_options import parse_media_filter
from .i18n import tr
from .perf_metrics import reset as perf_reset
from .perf_metrics import snapshot as perf_snapshot
from .preview_core import PREVIEW_THUMB_SIZE, PreviewThumbPipeline, stream_collect_preview_items
from .preview_index import build_preview_index, collect_sequential_preview_batch
from .state_sqlite import close_thread_connections
from .telethon_loop import run_async
from .tg_hashtag_dl import HashtagDownloader

logger = logging.getLogger(__name__)

try:
    from tests.sim_telegram import ChannelFeed, SimulatedTelegramClient, make_channel
except ImportError:  # pragma: no cover
    ChannelFeed = None
    SimulatedTelegramClient = None
    make_channel = None

AUTOTUNE_SCHEMA_VERSION = 1
AUTOTUNE_DEFAULT_BATCH_SIZE = 200
AUTOTUNE_PREVIEW_WORKERS = (1, 2, 4, 6)
AUTOTUNE_DOWNLOAD_WORKERS = (1, 2, 3)
AUTOTUNE_BATCH_SIZES = (80, 200, 400)
# Challenger must beat incumbent by this fraction to replace current/safe defaults.
AUTOTUNE_WIN_MARGIN = 0.10
# Sequential preview must beat regular preview by this fraction to be recommended.
AUTOTUNE_SEQUENTIAL_MARGIN = 0.12


def _pick_conservative_int(
    measurements: list[AutotuneMeasurement],
    scenario: str,
    metric_key: str,
    candidates: tuple[int, ...],
    *,
    incumbent: int | None = None,
    margin: float = AUTOTUNE_WIN_MARGIN,
) -> int:
    by_variant: dict[int, float] = {}
    for item in measurements:
        if item.scenario != scenario:
            continue
        raw = item.metrics.get(metric_key)
        if raw is None:
            continue
        value = int(raw)
        if value in candidates:
            by_variant[value] = item.elapsed_ms

    if not by_variant:
        if incumbent in candidates:
            return int(incumbent)
        return candidates[0]

    winner = incumbent if incumbent in candidates else candidates[0]
    winner_ms = by_variant.get(winner, float("inf"))
    for challenger in candidates:
        if challenger == winner:
            continue
        challenger_ms = by_variant.get(challenger)
        if challenger_ms is None:
            continue
        if challenger_ms <= winner_ms * (1.0 - margin):
            winner = challenger
            winner_ms = challenger_ms
    return winner


@dataclass
class AutotuneMeasurement:
    scenario: str
    variant: str
    elapsed_ms: float
    metrics: dict[str, float | int | bool | str] = field(default_factory=dict)
    counters: dict[str, dict[str, float | int]] = field(default_factory=dict)


@dataclass
class AutotuneRecommendation:
    preview_parallel_workers: int
    download_parallel_workers: int
    preview_batch_size: int
    sequential_preview: bool
    rationale: list[str] = field(default_factory=list)


@dataclass
class AutotuneProfile:
    schema_version: int
    created_at: str
    machine: dict[str, str | int]
    measurements: list[AutotuneMeasurement]
    recommendation: AutotuneRecommendation


def _as_measurement(data: dict[str, Any]) -> AutotuneMeasurement:
    return AutotuneMeasurement(
        scenario=str(data.get("scenario", "")),
        variant=str(data.get("variant", "")),
        elapsed_ms=float(data.get("elapsed_ms", 0.0)),
        metrics=dict(data.get("metrics", {}) or {}),
        counters=dict(data.get("counters", {}) or {}),
    )


def _as_recommendation(data: dict[str, Any]) -> AutotuneRecommendation:
    return AutotuneRecommendation(
        preview_parallel_workers=int(data.get("preview_parallel_workers", 3)),
        download_parallel_workers=int(data.get("download_parallel_workers", 1)),
        preview_batch_size=int(data.get("preview_batch_size", AUTOTUNE_DEFAULT_BATCH_SIZE)),
        sequential_preview=bool(data.get("sequential_preview", False)),
        rationale=[str(item) for item in (data.get("rationale") or [])],
    )


def load_autotune_profile(path: Path = AUTOTUNE_PROFILE_PATH) -> AutotuneProfile | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AutotuneProfile(
            schema_version=int(payload.get("schema_version", 0)),
            created_at=str(payload.get("created_at", "")),
            machine=dict(payload.get("machine", {}) or {}),
            measurements=[_as_measurement(item) for item in (payload.get("measurements") or [])],
            recommendation=_as_recommendation(payload.get("recommendation") or {}),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_autotune_profile(profile: AutotuneProfile, path: Path = AUTOTUNE_PROFILE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")


def format_autotune_summary(profile: AutotuneProfile | None, *, current: SettingsData | None = None) -> str:
    if profile is None:
        return tr("autotune.summary.empty")
    rec = profile.recommendation
    bits = [
        tr("autotune.summary.preview_workers", n=rec.preview_parallel_workers),
        tr("autotune.summary.download_workers", n=rec.download_parallel_workers),
        tr("autotune.summary.batch_size", n=rec.preview_batch_size),
        tr(
            "autotune.summary.mode",
            mode=tr("autotune.result.mode.sequential") if rec.sequential_preview else tr("autotune.result.mode.regular"),
        ),
    ]
    if current is not None:
        current_bits = [
            current.preview_parallel_workers == rec.preview_parallel_workers,
            current.download_parallel_workers == rec.download_parallel_workers,
            current.preview_batch_size == rec.preview_batch_size,
            current.sequential_preview == rec.sequential_preview,
        ]
        if all(current_bits):
            bits.append(tr("autotune.summary.applied"))
    stamp = profile.created_at.replace("T", " ").replace("+00:00", " UTC")
    return tr("autotune.summary.full", summary="; ".join(bits), date=stamp)


def profile_matches_settings(profile: AutotuneProfile | None, settings: SettingsData) -> bool:
    if profile is None:
        return False
    rec = profile.recommendation
    return (
        settings.preview_parallel_workers == rec.preview_parallel_workers
        and settings.download_parallel_workers == rec.download_parallel_workers
        and settings.preview_batch_size == rec.preview_batch_size
        and settings.sequential_preview == rec.sequential_preview
    )


def run_autotune_sync(
    settings: SettingsData,
    *,
    progress: Callable[[str, int, int], None] | None = None,
) -> AutotuneProfile:
    return AutotuneRunner(settings, progress=progress).run()


class AutotuneRunner:
    def __init__(
        self,
        settings: SettingsData,
        *,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self._settings = settings
        self._progress = progress
        self._measurements: list[AutotuneMeasurement] = []

    def run(self) -> AutotuneProfile:
        self._require_simulator()
        with tempfile.TemporaryDirectory(prefix="tghd_autotune_") as raw_tmp:
            try:
                tmp = Path(raw_tmp)
                preview_winner = self._bench_preview_stream(tmp)
                batch_winner = self._bench_sequential_preview(tmp, preview_winner)
                download_winner = self._bench_download(tmp)
                rerun = self._bench_rerun(tmp, download_winner)
            finally:
                close_thread_connections()

        rec = self._recommend(preview_winner, batch_winner, download_winner, rerun)
        return AutotuneProfile(
            schema_version=AUTOTUNE_SCHEMA_VERSION,
            created_at=datetime.now(timezone.utc).isoformat(),
            machine={
                "os": platform.platform(),
                "python": platform.python_version(),
                "cpu_count": os.cpu_count() or 1,
            },
            measurements=self._measurements,
            recommendation=rec,
        )

    def _report(self, text: str, step: int, total: int) -> None:
        if self._progress is not None:
            self._progress(text, step, total)

    @staticmethod
    def _require_simulator() -> None:
        if SimulatedTelegramClient is None or ChannelFeed is None or make_channel is None:
            raise RuntimeError("Autotune simulator is unavailable.")

    @staticmethod
    def _image_payload(seed: int) -> bytes:
        image = Image.new("RGB", (PREVIEW_THUMB_SIZE * 2, PREVIEW_THUMB_SIZE * 2), (seed % 255, 80, 160))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()

    def _make_dataset(self, root: Path, *, preview_workers: int, download_workers: int, batch_size: int) -> HashtagDownloader:
        client = SimulatedTelegramClient()
        channel = make_channel("autotune_channel", channel_id=6001)
        feed = ChannelFeed(channel, hashtag="autotune")
        client.register_feed(feed)
        for idx in range(48):
            feed.add_single(1000 + idx, caption=f"single {idx}", payload=self._image_payload(idx))
        for group_idx in range(8):
            feed.add_album(
                grouped_id=7000 + group_idx,
                count=5,
                start_id=2000 + group_idx * 10,
                caption=f"album {group_idx}",
            )
        config = AppConfig(
            api_id=1,
            api_hash="test_hash",
            hashtag="autotune",
            download_dir=root / "downloads",
            page_limit=50,
            max_posts=0,
            session_name="autotune_session",
            state_file=root / "state" / f"state_{preview_workers}_{download_workers}_{batch_size}.json",
            channel_filter="autotune_channel",
            media_filter=parse_media_filter(),
            download_parallel_workers=download_workers,
            download_retries=self._settings.download_retries,
            folder_by_date=self._settings.folder_by_date,
            caption_in_filename=self._settings.caption_in_filename,
            caption_max_len=self._settings.caption_max_len,
            dedup_by_hash=self._settings.dedup_by_hash,
        )
        return HashtagDownloader(client, config)

    def _collect_messages(self, worker: HashtagDownloader):
        return run_async(worker.collect_candidates())

    def _bench_preview_stream(self, tmp: Path) -> int:
        preview_workers = tuple(sorted({w for w in AUTOTUNE_PREVIEW_WORKERS if 1 <= w <= 6}))
        total_steps = len(preview_workers)
        for step, workers in enumerate(preview_workers, start=1):
            self._report(f"Preview check: workers {workers}", step, total_steps)
            worker = self._make_dataset(tmp / f"preview_{workers}", preview_workers=workers, download_workers=1, batch_size=AUTOTUNE_DEFAULT_BATCH_SIZE)
            messages = self._collect_messages(worker)
            perf_reset()
            thumb_pipeline = PreviewThumbPipeline(
                worker.client,
                worker.config.download_dir.parent / "preview_cache",
                parallel_workers=workers,
            )
            started = time.perf_counter()
            items = run_async(
                stream_collect_preview_items(
                    worker,
                    messages,
                    hashtag=worker.config.hashtag,
                    channel_filter=worker.config.channel_filter,
                    thumb_pipeline=thumb_pipeline,
                ),
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            thumb_pipeline.close()
            self._measurements.append(
                AutotuneMeasurement(
                    scenario="preview_stream",
                    variant=f"preview_parallel_workers={workers}",
                    elapsed_ms=elapsed_ms,
                    metrics={"items": len(items), "workers": workers},
                    counters=perf_snapshot(),
                ),
            )
        return _pick_conservative_int(
            self._measurements,
            "preview_stream",
            "workers",
            preview_workers,
            incumbent=self._settings.preview_parallel_workers,
        )

    def _bench_sequential_preview(self, tmp: Path, preview_workers: int) -> int:
        candidates = tuple(sorted({size for size in AUTOTUNE_BATCH_SIZES if 20 <= size <= 1000}))
        for step, batch_size in enumerate(candidates, start=1):
            self._report(f"Sequential preview: batch {batch_size}", step, len(candidates))
            worker = self._make_dataset(tmp / f"seq_{batch_size}", preview_workers=preview_workers, download_workers=1, batch_size=batch_size)
            messages = self._collect_messages(worker)
            entries, summary = build_preview_index(
                messages,
                hashtag=worker.config.hashtag,
                channel_filter=worker.config.channel_filter,
            )
            thumb_pipeline = PreviewThumbPipeline(
                worker.client,
                worker.config.download_dir.parent / "preview_cache",
                parallel_workers=preview_workers,
                batch_size=min(batch_size, preview_workers),
            )
            perf_reset()
            started = time.perf_counter()
            items, cursor = run_async(
                collect_sequential_preview_batch(
                    worker,
                    entries,
                    0,
                    batch_media_size=min(batch_size, max(20, summary.media_estimate or batch_size)),
                    thumb_pipeline=thumb_pipeline,
                ),
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            thumb_pipeline.close()
            self._measurements.append(
                AutotuneMeasurement(
                    scenario="preview_sequential",
                    variant=f"preview_batch_size={batch_size}",
                    elapsed_ms=elapsed_ms,
                    metrics={"items": len(items), "cursor": cursor, "workers": preview_workers, "batch_size": batch_size},
                    counters=perf_snapshot(),
                ),
            )
        return _pick_conservative_int(
            self._measurements,
            "preview_sequential",
            "batch_size",
            candidates,
            incumbent=self._settings.preview_batch_size,
        )

    def _bench_download(self, tmp: Path) -> int:
        candidates = tuple(sorted({n for n in AUTOTUNE_DOWNLOAD_WORKERS if 1 <= n <= 3}))
        for step, workers in enumerate(candidates, start=1):
            self._report(f"Download check: workers {workers}", step, len(candidates))
            worker = self._make_dataset(tmp / f"download_{workers}", preview_workers=1, download_workers=workers, batch_size=AUTOTUNE_DEFAULT_BATCH_SIZE)
            messages = self._collect_messages(worker)
            perf_reset()
            started = time.perf_counter()
            stats = run_async(worker.process_messages(messages))
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self._measurements.append(
                AutotuneMeasurement(
                    scenario="download",
                    variant=f"download_parallel_workers={workers}",
                    elapsed_ms=elapsed_ms,
                    metrics={"files": stats.files, "posts": stats.posts, "workers": workers},
                    counters=perf_snapshot(),
                ),
            )
        return _pick_conservative_int(
            self._measurements,
            "download",
            "workers",
            candidates,
            incumbent=self._settings.download_parallel_workers,
        )

    def _bench_rerun(self, tmp: Path, download_workers: int) -> float:
        self._report("Re-run check", 1, 1)
        root = tmp / "rerun"
        worker = self._make_dataset(root, preview_workers=1, download_workers=download_workers, batch_size=AUTOTUNE_DEFAULT_BATCH_SIZE)
        messages = self._collect_messages(worker)
        run_async(worker.process_messages(messages))
        worker2 = self._make_dataset(root, preview_workers=1, download_workers=download_workers, batch_size=AUTOTUNE_DEFAULT_BATCH_SIZE)
        messages2 = self._collect_messages(worker2)
        perf_reset()
        started = time.perf_counter()
        stats = run_async(worker2.process_messages(messages2))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._measurements.append(
            AutotuneMeasurement(
                scenario="rerun",
                variant=f"download_parallel_workers={download_workers}",
                elapsed_ms=elapsed_ms,
                metrics={"files_reused": stats.files_reused, "skipped_on_disk": stats.skipped_on_disk},
                counters=perf_snapshot(),
            ),
        )
        return elapsed_ms

    def _recommend(
        self,
        preview_workers: int,
        preview_batch_size: int,
        download_workers: int,
        rerun_elapsed_ms: float,
    ) -> AutotuneRecommendation:
        preview_stream_best = self._best_measurement("preview_stream")
        preview_sequential_best = self._best_measurement("preview_sequential")
        sequential_preview = False
        rationale: list[str] = []
        if preview_sequential_best is not None and preview_stream_best is not None:
            threshold = preview_stream_best.elapsed_ms * (1.0 - AUTOTUNE_SEQUENTIAL_MARGIN)
            sequential_preview = preview_sequential_best.elapsed_ms < threshold
            if self._settings.sequential_preview and not sequential_preview:
                close_enough = preview_sequential_best.elapsed_ms <= preview_stream_best.elapsed_ms * (1.0 + AUTOTUNE_WIN_MARGIN)
                if close_enough:
                    sequential_preview = True
            if sequential_preview:
                rationale.append(tr("autotune.result.reason.sequential_faster"))
            else:
                rationale.append(tr("autotune.result.reason.regular_competitive"))
        if download_workers > 1:
            rationale.append(
                tr("autotune.result.reason.download_workers_best", n=download_workers),
            )
        else:
            rationale.append(tr("autotune.result.reason.download_single_best"))
        rationale.append(
            tr("autotune.result.reason.batch_size_best", n=preview_batch_size),
        )
        rationale.append(
            tr("autotune.result.reason.rerun_ms", ms=f"{rerun_elapsed_ms:.0f}"),
        )
        return AutotuneRecommendation(
            preview_parallel_workers=preview_workers,
            download_parallel_workers=download_workers,
            preview_batch_size=preview_batch_size,
            sequential_preview=sequential_preview,
            rationale=rationale,
        )

    def _best_measurement(self, scenario: str) -> AutotuneMeasurement | None:
        matches = [item for item in self._measurements if item.scenario == scenario]
        if not matches:
            return None
        return min(matches, key=lambda item: item.elapsed_ms)
