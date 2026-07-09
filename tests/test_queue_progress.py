"""Queue progress · Прогресс очереди"""

import pytest

from app.dl_types import ProgressState
from app.i18n import tr
from app.queue_progress import format_batch_progress_label, queue_overall_percent


def test_format_batch_progress_label_empty_for_single():
    state = ProgressState(batch_index=1, batch_total=1, batch_hashtag="foo")
    assert format_batch_progress_label(state) == ""


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_batch_progress_label_with_channel(locale):
    state = ProgressState(
        batch_index=3,
        batch_total=33,
        batch_hashtag="Orphie",
        batch_channel="@news",
    )
    assert format_batch_progress_label(state) == tr(
        "queue.progress.label",
        i=3,
        total=33,
        tag="Orphie",
        channel=" · @news",
    )


def test_queue_overall_percent_first_item_search():
    state = ProgressState(
        phase="search",
        batch_index=1,
        batch_total=10,
        found=25,
        total=100,
    )
    assert queue_overall_percent(state) == 2


def test_queue_overall_percent_middle_download():
    state = ProgressState(
        phase="download",
        batch_index=5,
        batch_total=10,
        processed=50,
        total=100,
    )
    assert queue_overall_percent(state) == 45


def test_queue_overall_percent_inactive():
    assert queue_overall_percent(ProgressState()) == 0
