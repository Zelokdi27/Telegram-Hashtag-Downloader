"""List field utils tests · Слияние списков в полях формы"""

from __future__ import annotations

from app.list_field_utils import merge_comma_field, split_list_field


def test_split_list_field_multiline():
    assert split_list_field("a, b\nc;d") == ["a", "b", "c", "d"]


def test_merge_comma_field_dedupes_case_insensitive():
    merged = merge_comma_field("Tag1, tag2", ["TAG2", "tag3"])
    assert merged == "Tag1, tag2, tag3"
