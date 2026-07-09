"""Progress coalesce tests · Батчинг обновлений прогресса"""

from app.progress_coalesce import ProgressCoalescer
from app.tg_hashtag_dl import ProgressState


def test_coalescer_batches_same_phase_updates():
    emitted: list[ProgressState] = []
    coalescer = ProgressCoalescer(emitted.append, interval_sec=60.0)

    coalescer(ProgressState(phase="search", found=1, total=10))
    coalescer(ProgressState(phase="search", found=2, total=10))
    coalescer(ProgressState(phase="search", found=3, total=10))

    assert len(emitted) == 1
    assert emitted[0].found == 1

    coalescer.flush()
    assert len(emitted) == 2
    assert emitted[-1].found == 3


def test_coalescer_emits_phase_changes_immediately():
    emitted: list[ProgressState] = []
    coalescer = ProgressCoalescer(emitted.append, interval_sec=60.0)

    coalescer(ProgressState(phase="search", found=1))
    coalescer(ProgressState(phase="download", processed=0, total=5))

    assert len(emitted) == 2
    assert emitted[0].phase == "search"
    assert emitted[1].phase == "download"