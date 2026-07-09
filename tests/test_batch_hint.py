"""Batch hint · Подпись пакетного режима"""

from __future__ import annotations

import pytest

from app.download_options import batch_search_count, format_batch_search_hint
from app.i18n import plural_word, tr


def test_batch_search_count_single_search():
    assert batch_search_count("orphie", "", "", "") == (1, 1, 1)
    assert batch_search_count("orphie", "other", "ch1", "") == (2, 1, 2)


def test_batch_search_count_cartesian_product():
    assert batch_search_count("a", "b,c", "ch1", "ch2,ch3") == (3, 3, 9)


def test_format_batch_search_hint_hidden_for_single():
    assert format_batch_search_hint("orphie") == ""


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_batch_search_hint_extra_hashtags(locale):
    assert format_batch_search_hint("a", "b,c") == tr(
        "batch.hint.tags_only",
        tags=3,
        tag_word=plural_word("hashtag", 3),
        total=3,
        search_word=plural_word("search", 3),
    )


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_batch_search_hint_extra_channels(locale):
    assert format_batch_search_hint("tag", "", "ch1", "ch2,ch3") == tr(
        "batch.hint.channels_only",
        channels=3,
        ch_word=plural_word("channel", 3),
        total=3,
        search_word=plural_word("search", 3),
    )


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_format_batch_search_hint_full_matrix(locale):
    assert format_batch_search_hint("a", "b", "c1", "c2") == tr(
        "batch.hint.tags_channels",
        tags=2,
        tag_word=plural_word("hashtag", 2),
        channels=2,
        ch_word=plural_word("channel", 2),
        total=4,
        search_word=plural_word("search", 4),
    )
