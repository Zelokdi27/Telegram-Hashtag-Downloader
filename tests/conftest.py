"""Pytest fixtures · Фикстуры pytest"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.download_options import parse_media_filter
from app.i18n import set_locale
from app.telethon_loop import AsyncMethodFacade
from app.tg_hashtag_dl import AppConfig, HashtagDownloader
from tests.sim_telegram import ChannelFeed, SimulatedTelegramClient, make_channel


@pytest.fixture(autouse=True)
def _apply_locale(request) -> None:
    if "locale" not in request.fixturenames:
        set_locale("ru")


@pytest.fixture(params=["ru", "en"])
def locale(request) -> str:
    set_locale(request.param)
    return request.param


@pytest.fixture
def download_root(tmp_path: Path) -> Path:
    root = tmp_path / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def sim_client() -> SimulatedTelegramClient:
    return SimulatedTelegramClient()


@pytest.fixture
def channel_feed(sim_client: SimulatedTelegramClient) -> ChannelFeed:
    channel = make_channel("orphie_channel", channel_id=4242)
    feed = ChannelFeed(channel, hashtag="orphie")
    sim_client.register_feed(feed)
    return feed


def build_config(
    tmp_path: Path,
    download_root: Path,
    *,
    hashtag: str = "orphie",
    max_posts: int = 0,
    channel_filter: str = "orphie_channel",
    exclude_hashtags: tuple[str, ...] = (),
    required_hashtags: tuple[str, ...] = (),
) -> AppConfig:
    return AppConfig(
        api_id=1,
        api_hash="test_hash",
        hashtag=hashtag,
        download_dir=download_root,
        page_limit=50,
        max_posts=max_posts,
        session_name="test_session",
        state_file=tmp_path / f"state_{hashtag}.json",
        channel_filter=channel_filter,
        media_filter=parse_media_filter(),
        exclude_hashtags=exclude_hashtags,
        required_hashtags=required_hashtags,
    )


@pytest.fixture
def worker_factory(sim_client: SimulatedTelegramClient, tmp_path: Path, download_root: Path):
    def _factory(**kwargs) -> AsyncMethodFacade:
        config = build_config(tmp_path, download_root, **kwargs)
        inner = HashtagDownloader(sim_client, config)
        return AsyncMethodFacade(inner)

    return _factory
