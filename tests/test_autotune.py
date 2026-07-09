"""Autotune tests · Тесты проверки производительности и рекомендаций"""

from __future__ import annotations

from pathlib import Path

from app.autotune import (
    AutotuneMeasurement,
    AutotuneProfile,
    AutotuneRecommendation,
    _pick_conservative_int,
    format_autotune_summary,
    load_autotune_profile,
    profile_matches_settings,
    run_autotune_sync,
    save_autotune_profile,
)
from app.config_store import SettingsData


def test_pick_conservative_int_keeps_incumbent_within_margin() -> None:
    measurements = [
        AutotuneMeasurement("download", "w=1", 100.0, metrics={"workers": 1}),
        AutotuneMeasurement("download", "w=2", 96.0, metrics={"workers": 2}),
        AutotuneMeasurement("download", "w=3", 94.0, metrics={"workers": 3}),
    ]

    assert _pick_conservative_int(measurements, "download", "workers", (1, 2, 3), incumbent=1) == 1
    assert _pick_conservative_int(measurements, "download", "workers", (1, 2, 3), incumbent=2) == 2


def test_pick_conservative_int_switches_on_clear_win() -> None:
    measurements = [
        AutotuneMeasurement("download", "w=1", 100.0, metrics={"workers": 1}),
        AutotuneMeasurement("download", "w=2", 80.0, metrics={"workers": 2}),
        AutotuneMeasurement("download", "w=3", 70.0, metrics={"workers": 3}),
    ]

    assert _pick_conservative_int(measurements, "download", "workers", (1, 2, 3), incumbent=1) == 3


def test_autotune_profile_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "autotune_profile.json"
    profile = AutotuneProfile(
        schema_version=1,
        created_at="2026-07-09T10:00:00+00:00",
        machine={"os": "test", "python": "3.12", "cpu_count": 8},
        measurements=[],
        recommendation=AutotuneRecommendation(
            preview_parallel_workers=2,
            download_parallel_workers=1,
            preview_batch_size=200,
            sequential_preview=True,
            rationale=["Synthetic preview was faster."],
        ),
    )

    save_autotune_profile(profile, path)
    loaded = load_autotune_profile(path)

    assert loaded == profile


def test_run_autotune_sync_returns_bounded_recommendations() -> None:
    profile = run_autotune_sync(SettingsData())

    rec = profile.recommendation
    assert profile.schema_version >= 1
    assert rec.preview_parallel_workers in {1, 2, 4, 6}
    assert 1 <= rec.download_parallel_workers <= 3
    assert 20 <= rec.preview_batch_size <= 1000
    assert isinstance(rec.sequential_preview, bool)
    assert profile.measurements


def test_profile_matches_settings_and_summary() -> None:
    profile = AutotuneProfile(
        schema_version=1,
        created_at="2026-07-09T10:00:00+00:00",
        machine={},
        measurements=[],
        recommendation=AutotuneRecommendation(
            preview_parallel_workers=3,
            download_parallel_workers=2,
            preview_batch_size=200,
            sequential_preview=False,
            rationale=[],
        ),
    )
    settings = SettingsData(
        preview_parallel_workers=3,
        download_parallel_workers=2,
        preview_batch_size=200,
        sequential_preview=False,
    )

    assert profile_matches_settings(profile, settings) is True
    summary = format_autotune_summary(profile, current=settings)
    assert summary
