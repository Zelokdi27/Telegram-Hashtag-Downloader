"""Peer cache tests · Кэш peer-сущностей из результатов поиска"""

from __future__ import annotations

from telethon import types, utils

from tests.sim_telegram import ChannelFeed, make_channel


def _count_get_entity(client) -> int:
    calls = {"n": 0}
    original = client.get_entity

    def _wrapped(entity):
        calls["n"] += 1
        return original(entity)

    client.get_entity = _wrapped
    return calls


def test_search_page_populates_peer_cache(channel_feed, worker_factory):
    for msg_id in range(10, 15):
        channel_feed.add_single(msg_id, caption=f"post {msg_id}")

    worker = worker_factory(channel_filter="")
    messages = worker.search_all()

    assert len(messages) >= 5
    peer_id = utils.get_peer_id(types.PeerChannel(channel_id=channel_feed.channel.channel_id))
    assert peer_id in worker._peer_entity_cache


def test_channel_label_uses_cache_after_global_search(
    channel_feed,
    worker_factory,
    sim_client,
):
    for msg_id in range(20, 28):
        channel_feed.add_single(msg_id, caption=f"item {msg_id}")

    worker = worker_factory(channel_filter="")
    messages = worker.search_all()
    assert messages

    calls = _count_get_entity(sim_client)
    for message in messages:
        label = worker._channel_label(message)
        assert label == channel_feed.channel.username

    assert calls["n"] == 0


def test_build_filename_uses_cache_after_global_search(
    channel_feed,
    worker_factory,
    sim_client,
):
    for msg_id in range(30, 35):
        channel_feed.add_single(msg_id, caption=f"dl {msg_id}")

    worker = worker_factory(channel_filter="", max_posts=0)
    messages = worker.search_all()

    calls = _count_get_entity(sim_client)
    names = [worker._build_filename(message) for message in messages]

    assert calls["n"] == 0
    assert len(names) == len(messages)
    for name in names:
        assert channel_feed.channel.username in name


def test_search_in_channel_caches_entity(channel_feed, worker_factory):
    channel_feed.add_single(40, caption="one")
    channel_feed.add_single(41, caption="two")

    worker = worker_factory()
    entity = worker._resolve_channel_entity()
    peer_id = worker._peer_id_from_entity(entity)
    assert peer_id in worker._peer_entity_cache

    worker.search_in_channel(entity)
    assert peer_id in worker._peer_entity_cache

    calls = _count_get_entity(worker.client)
    for message in channel_feed.search_results:
        assert worker._channel_label(message) == channel_feed.channel.username
    assert calls["n"] == 0


def test_get_peer_entity_fallback_on_cache_miss(channel_feed, worker_factory, sim_client):
    worker = worker_factory(channel_filter="")
    message = channel_feed.add_single(50, caption="solo")
    peer_id = utils.get_peer_id(message.peer_id)

    assert peer_id not in worker._peer_entity_cache

    calls = _count_get_entity(sim_client)
    entity = worker._get_peer_entity(message.peer_id)

    assert calls["n"] == 1
    assert entity is channel_feed.channel
    assert worker._peer_entity_cache[peer_id] is channel_feed.channel


def test_message_matches_channel_uses_cache(
    channel_feed,
    worker_factory,
    sim_client,
):
    channel_feed.add_single(60, caption="match me")
    worker = worker_factory()
    message = channel_feed.history[0]

    entity = worker._resolve_channel_entity()
    worker.search_in_channel(entity)

    calls = _count_get_entity(sim_client)
    assert worker._message_matches_channel(message) is True
    assert calls["n"] == 0


def test_global_search_two_channels_cached(
    sim_client,
    worker_factory,
    tmp_path,
    download_root,
):
    feed_a = ChannelFeed(make_channel("alpha_ch", channel_id=5001), hashtag="shared")
    feed_b = ChannelFeed(make_channel("beta_ch", channel_id=5002), hashtag="shared")
    sim_client.register_feed(feed_a)
    sim_client.register_feed(feed_b)
    feed_a.add_single(1, caption="a")
    feed_b.add_single(2, caption="b")

    worker = worker_factory(channel_filter="", hashtag="shared")
    messages = worker.search_all()
    assert len(messages) == 2

    peer_a = worker._peer_id_from_entity(feed_a.channel)
    peer_b = worker._peer_id_from_entity(feed_b.channel)
    assert peer_a in worker._peer_entity_cache
    assert peer_b in worker._peer_entity_cache

    labels = {worker._channel_label(msg) for msg in messages}
    assert labels == {"alpha_ch", "beta_ch"}