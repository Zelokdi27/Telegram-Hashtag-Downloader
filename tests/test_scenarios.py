"""
Integration scenarios · Интеграционные сценарии: поиск → альбом → скачивание → журнал.

No real Telegram — same HashtagDownloader methods and API simulator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.preview_core import stream_collect_preview_items
from app.telethon_loop import run_async
from tests.sim_telegram import ChannelFeed, make_channel


def _search_channel(worker, feed: ChannelFeed):
    entity = worker._resolve_channel_entity()
    return worker.search_in_channel(entity)


def _photo_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.jpg") if path.is_file())


def test_search_expands_album_via_get_messages_and_honors_media_limit(
    channel_feed: ChannelFeed,
    worker_factory,
):
    """Search returns 1/6 album frames; limit 4 · В поиске 1 кадр из 6, лимит 4"""
    channel_feed.add_album(grouped_id=9001, count=6, start_id=100, caption="set A")
    channel_feed.add_single(200, caption="extra single")
    worker = worker_factory(max_posts=4)

    found = _search_channel(worker, channel_feed)

    assert worker._search_media_used == 4
    # Search still shows one album leader; rest via get_messages · один «лидер» альбома в выдаче
    assert len(found) == 1
    assert found[0].id == 100
    assert worker._partial_album_included[9001] == {100, 101, 102, 103}
    assert worker._album_slot_caps[9001] == 4
    assert 200 not in {msg.id for msg in found}


def test_download_pipeline_partial_album_writes_files_and_journal(
    channel_feed: ChannelFeed,
    worker_factory,
    download_root: Path,
    tmp_path: Path,
):
    """Full path: search → process_messages → state · Полный путь до grouped в state"""
    channel_feed.add_album(grouped_id=9100, count=5, start_id=300)
    worker = worker_factory(max_posts=3)
    messages = _search_channel(worker, channel_feed)

    stats = worker.process_messages(messages)

    files = _photo_files(download_root)
    assert stats.files == 3
    assert stats.errors == 0
    assert len(files) == 3
    names = " ".join(path.name for path in files)
    assert "_1" in names and "_2" in names and "_3" in names

    from app.dl_state import load_state

    state_path = tmp_path / "state_orphie.json"
    state = load_state(state_path)
    assert "9100" in state["grouped"]
    assert len(state["grouped"]["9100"]["files"]) == 3
    assert len(worker.client.download_log) == 3


def test_second_run_skips_existing_files(
    channel_feed: ChannelFeed,
    worker_factory,
    download_root: Path,
):
    """Rerun skips existing files · Повторный прогон не качает заново"""
    channel_feed.add_single(401, caption="one")
    channel_feed.add_single(402, caption="two")
    worker = worker_factory(max_posts=0)

    first = worker.process_messages(_search_channel(worker, channel_feed))
    assert first.files == 2
    downloads_after_first = len(worker.client.download_log)

    worker2 = worker_factory(max_posts=0)
    second = worker2.process_messages(_search_channel(worker2, channel_feed))

    assert second.files == 0
    assert second.skipped_on_disk >= 2
    assert len(worker2.client.download_log) == downloads_after_first


def test_preview_resolves_album_from_single_search_hit(
    channel_feed: ChannelFeed,
    worker_factory,
):
    """Preview fetches album via fetch_album_messages · Превью догружает альбом"""
    channel_feed.add_album(grouped_id=9200, count=4, start_id=500, search_returns="leader_only")
    worker = worker_factory(max_posts=0)
    messages = _search_channel(worker, channel_feed)
    assert len(messages) == 1

    items = run_async(stream_collect_preview_items(worker, messages, hashtag="orphie"))

    assert len(items) == 4
    assert {item.message.id for item in items} == {500, 501, 502, 503}


def test_preview_respects_media_limit_with_partial_album(
    channel_feed: ChannelFeed,
    worker_factory,
):
    channel_feed.add_album(grouped_id=9300, count=5, start_id=600)
    worker = worker_factory(max_posts=2)
    messages = _search_channel(worker, channel_feed)

    items = run_async(stream_collect_preview_items(worker, messages, hashtag="orphie"))

    assert len(items) == 2
    assert worker._album_slot_caps.get(9300) == 2


def test_exclude_hashtag_drops_publication(
    channel_feed: ChannelFeed,
    worker_factory,
):
    channel_feed.add_single(701, caption="keep")
    channel_feed.add_single(702, caption="drop #spam please")
    worker = worker_factory(exclude_hashtags=("spam",))
    messages = _search_channel(worker, channel_feed)

    stats = worker.process_messages(messages)

    assert stats.files == 1
    assert stats.skipped_excluded_hashtag == 1
    assert 702 not in {call[0] for call in worker.client.download_log}


def test_required_hashtag_or_keeps_matching_publication(
    channel_feed: ChannelFeed,
    worker_factory,
):
    channel_feed.add_single(711, caption="only main tag")
    channel_feed.add_single(712, caption="also #bonus and #other tags")
    worker = worker_factory(required_hashtags=("bonus", "vip"))
    messages = _search_channel(worker, channel_feed)

    stats = worker.process_messages(messages)

    assert stats.files == 1
    assert stats.skipped_required_hashtag == 1
    assert 711 not in {call[0] for call in worker.client.download_log}
    assert 712 in {call[0] for call in worker.client.download_log}


def test_required_hashtag_or_applies_in_preview_and_verify(
    channel_feed: ChannelFeed,
    worker_factory,
):
    channel_feed.add_single(721, caption="no required")
    channel_feed.add_single(722, caption="has #need tag")
    worker = worker_factory(required_hashtags=("need",))

    candidates = worker.collect_candidates()
    assert {msg.id for msg in candidates} == {722}

    integrity = worker.verify_integrity()
    assert integrity.found == 1
    assert integrity.with_media == 1

    items = run_async(stream_collect_preview_items(worker, candidates, hashtag="orphie"))
    assert len(items) == 1
    assert items[0].message.id == 722


def test_verify_integrity_flags_missing_after_files_deleted(
    channel_feed: ChannelFeed,
    worker_factory,
    download_root: Path,
):
    """Partial delete detected by integrity · Удалённые файлы видит целостность"""
    channel_feed.add_album(grouped_id=9400, count=5, start_id=800)
    worker = worker_factory(max_posts=0)
    worker.process_messages(_search_channel(worker, channel_feed))

    files = _photo_files(download_root)
    assert len(files) == 5
    files[0].unlink()
    files[1].unlink()

    checker = worker_factory(max_posts=0)
    integrity = checker.verify_integrity()

    assert integrity.media_files == 5
    assert integrity.files_on_disk == 3
    assert integrity.files_missing == 2
    assert integrity.missing_post_ids == [800]


def test_re_download_when_journal_exists_but_files_removed(
    channel_feed: ChannelFeed,
    worker_factory,
    download_root: Path,
):
    channel_feed.add_single(901, caption="fragile")
    worker = worker_factory(max_posts=0)
    worker.process_messages(_search_channel(worker, channel_feed))
    assert len(_photo_files(download_root)) == 1

    for path in _photo_files(download_root):
        path.unlink()

    worker2 = worker_factory(max_posts=0)
    stats = worker2.process_messages(_search_channel(worker2, channel_feed))

    assert stats.redownloaded == 1
    assert stats.files == 1
    assert len(_photo_files(download_root)) == 1


def test_mixed_feed_album_singles_and_limit(
    channel_feed: ChannelFeed,
    worker_factory,
):
    """
    Mixed feed: album 3 + 2 singles + album 10; limit 5 ·
    Лента: альбом 3 + 2 одиночных + альбом 10, лимит 5.
    """
    channel_feed.add_album(grouped_id=9500, count=3, start_id=10)
    channel_feed.add_single(20)
    channel_feed.add_single(21)
    channel_feed.add_album(grouped_id=9600, count=10, start_id=30)
    worker = worker_factory(max_posts=5)

    messages = _search_channel(worker, channel_feed)

    assert worker._search_media_used == 5
    assert 30 not in {msg.id for msg in messages}
    assert {20, 21} <= {msg.id for msg in messages}
    pubs, media, albums, singles = worker._publication_stats_from_search(messages)
    assert media == 5
    assert albums == 1
    assert singles == 2