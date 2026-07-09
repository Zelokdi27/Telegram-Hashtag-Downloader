"""Hashtag history tests · История использованных хештегов"""

from app.hashtag_history import (
    HASHTAG_SUGGEST_LIMIT,
    load_hashtag_history,
    record_hashtags_used,
    save_hashtag_history,
    suggest_hashtag_history,
)


def test_record_moves_existing_tag_to_front(tmp_path):
    path = tmp_path / "history.json"
    save_hashtag_history(["alpha", "beta"], path=path)
    updated = record_hashtags_used(["beta"], path=path)
    assert updated == ["beta", "alpha"]


def test_record_keeps_full_history(tmp_path):
    path = tmp_path / "history.json"
    tags = [f"tag{i}" for i in range(12)]
    record_hashtags_used(tags, path=path)
    assert load_hashtag_history(path=path) == [f"tag{i}" for i in range(11, -1, -1)]
    assert len(load_hashtag_history(path=path)) == 12


def test_suggest_limits_dropdown(tmp_path):
    path = tmp_path / "history.json"
    tags = [f"tag{i}" for i in range(12)]
    record_hashtags_used(tags, path=path)
    assert len(suggest_hashtag_history(path=path)) == HASHTAG_SUGGEST_LIMIT
    assert suggest_hashtag_history(path=path) == [f"tag{i}" for i in range(11, 7, -1)]


def test_record_skips_invalid_tags(tmp_path):
    path = tmp_path / "history.json"
    updated = record_hashtags_used(["valid", "   ", "#"], path=path)
    assert updated == ["valid"]


def test_load_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_hashtag_history(path=path) == []