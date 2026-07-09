"""Preview logic tests · Тесты логики предпросмотра (без GUI)"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.i18n import tr

from PIL import Image

from app.telethon_loop import run_async
from app.preview_core import (
    PREVIEW_THUMB_SIZE,
    PreviewItem,
    PreviewPrepContext,
    _optimize_preview_file,
    annotate_preview_duplicates,
    apply_preview_view,
    content_duplicate_badge,
    count_content_duplicates,
    disk_status_badge,
    download_preview_full_async,
    estimate_media_total,
    filter_preview_items,
    is_content_duplicate,
    media_content_key,
    preview_target_total,
    sort_preview_items,
    selection_summary,
    set_items_selection,
    stream_collect_preview_items,
)
from tests.sim_telegram import ChannelFeed, make_channel


def _album_index(messages) -> dict[int, list]:
    groups: dict[int, list] = {}
    for msg in messages:
        if msg.grouped_id and msg.media:
            groups.setdefault(msg.grouped_id, []).append(msg)
    return groups


def test_preview_target_total_uses_limit():
    ctx = PreviewPrepContext(
        posts_total=10,
        media_total=8,
        album_groups=2,
        albums_to_fetch=1,
        media_total_estimate=20,
        media_limit=15,
    )

    assert preview_target_total(ctx) == 15


def test_preview_target_total_falls_back_to_estimate():
    ctx = PreviewPrepContext(
        posts_total=10,
        media_total=8,
        album_groups=2,
        albums_to_fetch=1,
        media_total_estimate=12,
        media_limit=0,
    )

    assert preview_target_total(ctx) == 12


def test_estimate_media_total_with_resolved_album():
    channel = make_channel("preview_ch", channel_id=77)
    feed = ChannelFeed(channel, hashtag="tag")
    album = feed.add_album(grouped_id=11, count=4, start_id=1)
    feed.add_single(10)
    messages = feed.search_results
    search_albums = _album_index(feed.history)

    assert estimate_media_total(messages, search_albums) == 5
    assert len(album) == 4


def _preview_item(
    kind: str,
    msg_id: int,
    *,
    channel: str = "ch",
    disk_status: str = "new",
    grouped_id: int | None = None,
    album_index: int = 0,
    when: datetime | None = None,
) -> PreviewItem:
    from tests.sim_telegram import SimMessage

    message = SimMessage(msg_id=msg_id, channel_id=1, kind=kind, grouped_id=grouped_id, when=when)
    return PreviewItem(
        message=message,
        channel=channel,
        kind=kind,
        summary=f"post {msg_id}",
        disk_status=disk_status,  # type: ignore[arg-type]
        album_index=album_index,
        grouped_id=grouped_id or 0,
    )


def _preview_item_with_payload(
    msg_id: int,
    payload: bytes,
    *,
    channel: str = "ch",
) -> PreviewItem:
    from tests.sim_telegram import SimMessage

    message = SimMessage(msg_id=msg_id, channel_id=1, kind="photo", payload=payload)
    return PreviewItem(
        message=message,
        channel=channel,
        kind="photo",
        summary=f"post {msg_id}",
    )


def test_media_content_key_uses_payload_hash():
    from tests.sim_telegram import SimMessage

    payload = b"same-image-bytes"
    a = SimMessage(msg_id=1, channel_id=1, kind="photo", payload=payload)
    b = SimMessage(msg_id=2, channel_id=1, kind="photo", payload=payload)
    c = SimMessage(msg_id=3, channel_id=1, kind="photo", payload=b"other")

    assert media_content_key(a) == media_content_key(b)
    assert media_content_key(a) != media_content_key(c)


def test_annotate_preview_duplicates_marks_copies():
    payload = b"reposted-photo"
    items = [
        _preview_item_with_payload(10, payload),
        _preview_item_with_payload(20, payload),
        _preview_item_with_payload(30, b"unique"),
    ]
    annotate_preview_duplicates(items)

    assert not is_content_duplicate(items[0])
    assert is_content_duplicate(items[1])
    assert items[1].duplicate_of_message_id == 10
    assert not is_content_duplicate(items[2])
    assert count_content_duplicates(items) == 1


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_content_duplicate_badge(locale):
    item = _preview_item("photo", 1)
    assert content_duplicate_badge(item) == ""
    item.duplicate_of_message_id = 99
    assert content_duplicate_badge(item) == tr("preview.badge.duplicate")


def test_filter_preview_items_duplicate_modes():
    payload = b"dup"
    items = [
        _preview_item_with_payload(1, payload),
        _preview_item_with_payload(2, payload),
        _preview_item_with_payload(3, b"x"),
    ]
    annotate_preview_duplicates(items)

    assert [item.message.id for item in filter_preview_items(items, "duplicates_only")] == [2]
    assert [item.message.id for item in filter_preview_items(items, "hide_duplicates")] == [1, 3]


def test_filter_preview_items_disk_modes():
    items = [
        _preview_item("photo", 1, disk_status="new"),
        _preview_item("photo", 2, disk_status="complete"),
        _preview_item("photo", 3, disk_status="partial"),
    ]
    assert [item.message.id for item in filter_preview_items(items, "hide_on_disk")] == [1, 3]
    assert [item.message.id for item in filter_preview_items(items, "on_disk_only")] == [2]


def test_sort_preview_items_by_channel():
    items = [
        _preview_item("photo", 1, channel="beta"),
        _preview_item("photo", 2, channel="alpha"),
    ]
    sorted_items = sort_preview_items(items, "channel")
    assert [item.channel for item in sorted_items] == ["alpha", "beta"]


def test_sort_preview_items_keeps_album_order_for_date_desc():
    when = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    items = [
        _preview_item("photo", 101, grouped_id=77, album_index=1, when=when),
        _preview_item("photo", 102, grouped_id=77, album_index=2, when=when),
        _preview_item("photo", 103, grouped_id=77, album_index=3, when=when),
    ]

    sorted_items = sort_preview_items(items, "date_desc")

    assert [item.album_index for item in sorted_items] == [1, 2, 3]


def test_sort_preview_items_keeps_album_order_for_kind():
    when = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    items = [
        _preview_item("photo", 201, grouped_id=88, album_index=1, when=when),
        _preview_item("photo", 202, grouped_id=88, album_index=2, when=when),
        _preview_item("photo", 203, grouped_id=88, album_index=3, when=when),
    ]

    sorted_items = sort_preview_items(items, "kind")

    assert [item.album_index for item in sorted_items] == [1, 2, 3]


def test_apply_preview_view_channel_filter():
    items = [
        _preview_item("photo", 1, channel="a"),
        _preview_item("photo", 2, channel="b"),
    ]
    visible = apply_preview_view(items, mode="all", channel="b", sort="date_desc")
    assert len(visible) == 1
    assert visible[0].message.id == 2


def test_set_items_selection_only_targets_given_items():
    items = [
        _preview_item("photo", 1, channel="a"),
        _preview_item("photo", 2, channel="b"),
        _preview_item("photo", 3, channel="a"),
    ]
    visible = [items[0], items[2]]

    set_items_selection(visible, selected=False)

    assert items[0].selected is False
    assert items[1].selected is True
    assert items[2].selected is False


def test_selection_summary_counts_visible_subset():
    items = [
        _preview_item("photo", 1, disk_status="new"),
        _preview_item("photo", 2, disk_status="complete"),
    ]
    items[0].selected = True
    items[1].selected = False
    selected, total, visible_selected = selection_summary(items, items[:1])
    assert (selected, total, visible_selected) == (1, 2, 1)


def test_disk_status_badge_partial_album():
    item = _preview_item("photo", 1, disk_status="partial")
    item.album_on_disk = 2
    item.album_total = 5
    assert disk_status_badge(item) == "2/5"


def test_filter_preview_items_audio_and_document():
    items = [
        _preview_item("photo", 1),
        _preview_item("audio", 2),
        _preview_item("document", 3),
        _preview_item("video", 4),
    ]

    assert [item.message.id for item in filter_preview_items(items, "audio")] == [2]
    assert [item.message.id for item in filter_preview_items(items, "document")] == [3]


def test_optimize_preview_file_fits_thumb_size(tmp_path):
    source = tmp_path / "large.png"
    target = tmp_path / "thumb.jpg"
    Image.new("RGB", (800, 600), color=(40, 80, 120)).save(source)

    assert _optimize_preview_file(source, target, max_dimension=PREVIEW_THUMB_SIZE)

    with Image.open(target) as thumb:
        assert max(thumb.size) <= PREVIEW_THUMB_SIZE


def test_preview_collects_audio_and_document(
    channel_feed,
    worker_factory,
):
    channel_feed.add_single(801, kind="audio", caption="track")
    channel_feed.add_single(802, kind="document", caption="file")
    channel_feed.add_single(803, kind="photo", caption="pic")
    worker = worker_factory(max_posts=0)
    entity = worker._resolve_channel_entity()
    messages = worker.search_in_channel(entity)

    items = run_async(stream_collect_preview_items(worker, messages, hashtag="orphie"))

    assert {item.kind for item in items} == {"audio", "document", "photo"}


def test_download_preview_full_async_caches_photo(sim_client, tmp_path):
    from tests.sim_telegram import ChannelFeed, make_channel

    channel = make_channel("preview_full", channel_id=88)
    feed = ChannelFeed(channel, hashtag="tag")
    feed.add_single(501, kind="photo", caption="#tag full", payload=b"full-photo-bytes")
    sim_client.register_feed(feed)
    message = feed.history[0]
    item = PreviewItem(message=message, channel="preview_full", kind="photo", summary="pic")

    path, error = run_async(download_preview_full_async(sim_client, item, tmp_path))

    assert error is None
    assert path is not None
    assert item.full_preview_path == path
    assert Path(path).is_file()
    assert Path(path).read_bytes() == b"full-photo-bytes"
    cached, cached_error = run_async(download_preview_full_async(sim_client, item, tmp_path))
    assert cached_error is None
    assert cached == path


def test_download_preview_full_async_skips_non_photo(sim_client, tmp_path):
    item = _preview_item("video", 9)
    path, error = run_async(download_preview_full_async(sim_client, item, tmp_path))
    assert path is None
    assert error is not None