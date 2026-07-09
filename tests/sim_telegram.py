"""
Telegram simulator · Симуляция Telegram, близкая к Telethon.

- Hashtag search may omit album parts (like Telegram).
- Full album restored via get_messages (neighbors).
- download_media writes files to disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from telethon import functions, types
from telethon._updates import EntityCache as MbEntityCache
from telethon.errors import RPCError


@dataclass
class SimChannel:
    channel_id: int
    username: str
    title: str

    @property
    def id(self) -> int:
        return self.channel_id


class SimMessage:
    """Message with fields read by HashtagDownloader · Сообщение для downloader"""

    __slots__ = (
        "id",
        "grouped_id",
        "peer_id",
        "photo",
        "video",
        "animation",
        "document",
        "audio",
        "voice",
        "sticker",
        "video_note",
        "media",
        "message",
        "text",
        "date",
        "_sim_bytes",
    )

    def __init__(
        self,
        *,
        msg_id: int,
        channel_id: int,
        grouped_id: int | None = None,
        kind: str = "photo",
        caption: str = "",
        when: datetime | None = None,
        payload: bytes | None = None,
    ) -> None:
        self.id = msg_id
        self.grouped_id = grouped_id
        self.peer_id = types.PeerChannel(channel_id=channel_id)
        self.message = caption
        self.text = caption
        self.date = when or datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._sim_bytes = payload or f"sim-payload-{channel_id}-{msg_id}".encode()

        for attr in (
            "photo",
            "video",
            "animation",
            "document",
            "audio",
            "voice",
            "sticker",
            "video_note",
            "media",
        ):
            setattr(self, attr, None)
        if kind:
            setattr(self, kind, SimpleNamespace(id=msg_id, size=len(self._sim_bytes)))
            self.media = SimpleNamespace(document_id=msg_id)


class ChannelFeed:
    """Channel feed: history and hashtag search · Лента канала и поиск"""

    def __init__(self, channel: SimChannel, hashtag: str) -> None:
        self.channel = channel
        self.hashtag = hashtag.lstrip("#").casefold()
        self._history: list[SimMessage] = []
        self._search_hits: list[SimMessage] = []
        self._by_id: dict[int, SimMessage] = {}
        self._clock = datetime(2024, 6, 1, 18, 0, 0, tzinfo=timezone.utc)

    def _tag_caption(self, caption: str) -> str:
        tag = f"#{self.hashtag}"
        if tag.casefold() in caption.casefold():
            return caption
        return f"{caption} {tag}".strip()

    def _store(self, message: SimMessage, *, visible_in_search: bool) -> SimMessage:
        self._history.append(message)
        self._by_id[message.id] = message
        if visible_in_search and self.hashtag in (message.message or "").casefold():
            self._search_hits.append(message)
        return message

    def add_single(
        self,
        msg_id: int,
        *,
        caption: str = "",
        kind: str = "photo",
        visible_in_search: bool = True,
        payload: bytes | None = None,
    ) -> SimMessage:
        message = SimMessage(
            msg_id=msg_id,
            channel_id=self.channel.channel_id,
            kind=kind,
            caption=self._tag_caption(caption),
            when=self._clock,
            payload=payload,
        )
        self._clock -= timedelta(minutes=3)
        return self._store(message, visible_in_search=visible_in_search)

    def add_album(
        self,
        grouped_id: int,
        count: int,
        start_id: int,
        *,
        caption: str = "",
        search_returns: str = "leader_only",
    ) -> list[SimMessage]:
        """
        search_returns:
          leader_only — search shows first frame only (Telegram-like);
          all — every frame in search results (rare).
          leader_only — в поиске только первый кадр;
          all — все кадры в выдаче.
        """
        album: list[SimMessage] = []
        for index in range(count):
            visible = search_returns == "all" or index == 0
            message = SimMessage(
                msg_id=start_id + index,
                channel_id=self.channel.channel_id,
                grouped_id=grouped_id,
                kind="photo",
                caption=self._tag_caption(caption if index == 0 else ""),
                when=self._clock,
                payload=f"album-{grouped_id}-{index}".encode(),
            )
            album.append(self._store(message, visible_in_search=visible))
        self._clock -= timedelta(minutes=5)
        return album

    def add_text_post(self, msg_id: int, *, caption: str = "") -> SimMessage:
        message = SimMessage(
            msg_id=msg_id,
            channel_id=self.channel.channel_id,
            kind="",
            caption=self._tag_caption(caption),
            when=self._clock,
        )
        self._clock -= timedelta(minutes=2)
        return self._store(message, visible_in_search=True)

    @property
    def history(self) -> list[SimMessage]:
        return list(self._history)

    @property
    def search_results(self) -> list[SimMessage]:
        return list(self._search_hits)


class SimulatedTelegramClient:
    """Minimal Telethon client for scenario tests · Минимальный клиент для тестов"""

    flood_sleep_threshold: int = 0

    def __init__(self) -> None:
        self.channels: dict[str, SimChannel] = {}
        self.feeds: dict[int, ChannelFeed] = {}
        self.download_log: list[tuple[int, str]] = []
        self._self_id = 0
        self._mb_entity_cache = MbEntityCache()
        self._loop = SimpleNamespace(is_running=lambda: False)

    @property
    def loop(self):
        return self._loop

    def register_feed(self, feed: ChannelFeed) -> ChannelFeed:
        channel = feed.channel
        self.channels[channel.username.casefold()] = channel
        self.channels[str(channel.channel_id)] = channel
        self.channels[f"@{channel.username}".casefold()] = channel
        self.feeds[channel.channel_id] = feed
        return feed

    def _feed_for_peer(self, peer: Any) -> ChannelFeed | None:
        if isinstance(peer, types.PeerChannel):
            return self.feeds.get(peer.channel_id)
        if isinstance(peer, SimChannel):
            return self.feeds.get(peer.channel_id)
        if isinstance(peer, str):
            channel = self.channels.get(peer.casefold()) or self.channels.get(
                peer.lstrip("@").casefold(),
            )
            return self.feeds.get(channel.channel_id) if channel else None
        channel_id = getattr(peer, "channel_id", None)
        if channel_id is not None:
            return self.feeds.get(channel_id)
        return None

    def _get_entity_sync(self, entity: Any) -> SimChannel:
        if isinstance(entity, SimChannel):
            return entity
        if isinstance(entity, types.PeerChannel):
            feed = self.feeds.get(entity.channel_id)
            if feed:
                return feed.channel
        key = str(entity).lstrip("@").casefold()
        for candidate in (key, f"@{key}", key.replace("https://t.me/", "")):
            if candidate in self.channels:
                return self.channels[candidate]
        raise RPCError("CHAT_INVALID")

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    def is_connected(self) -> bool:
        return True

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self):
        return SimpleNamespace(id=1, username="tester")

    async def get_entity(self, entity: Any) -> SimChannel:
        return self._get_entity_sync(entity)

    async def get_input_entity(self, peer: Any) -> types.InputPeerChannel:
        channel = await self.get_entity(peer)
        return types.InputPeerChannel(channel_id=channel.channel_id, access_hash=0)

    def _get_messages_sync(
        self,
        peer: Any,
        *,
        min_id: int = 0,
        max_id: int = 0,
        limit: int | None = None,
        ids: list[int] | None = None,
    ) -> list[SimMessage]:
        feed = self._feed_for_peer(peer)
        if not feed:
            return []
        if ids:
            return [feed._by_id[i] for i in ids if i in feed._by_id]
        selected = [
            msg
            for msg in feed.history
            if (not min_id or msg.id >= min_id) and (not max_id or msg.id <= max_id)
        ]
        selected.sort(key=lambda item: item.id)
        if limit is not None:
            return selected[:limit]
        return selected

    async def get_messages(
        self,
        peer: Any,
        *,
        min_id: int = 0,
        max_id: int = 0,
        limit: int | None = None,
        ids: list[int] | None = None,
    ) -> list[SimMessage]:
        return self._get_messages_sync(
            peer,
            min_id=min_id,
            max_id=max_id,
            limit=limit,
            ids=ids,
        )

    async def iter_messages(
        self,
        entity: Any,
        *,
        search: str | None = None,
        limit: int | None = None,
    ):
        feed = self._feed_for_peer(entity)
        if not feed:
            return
        pool = feed.search_results if search else list(reversed(feed.history))
        if search:
            tag = search.lstrip("#").casefold()
            pool = [msg for msg in pool if tag in (msg.message or "").casefold()]
        else:
            pool = list(reversed(feed.history))
        if limit is not None:
            pool = pool[:limit]
        for msg in pool:
            yield msg

    async def download_media(self, message: SimMessage, file: str | Path | None = None, **_: Any) -> str:
        target = Path(file) if file else Path(f"download_{message.id}.bin")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(message._sim_bytes)
        self.download_log.append((message.id, str(target.resolve())))
        return str(target.resolve())

    async def __call__(self, request: Any) -> Any:
        if isinstance(request, functions.channels.SearchPostsRequest):
            return self._search_posts(request)
        raise NotImplementedError(type(request).__name__)

    def _search_posts(self, request: functions.channels.SearchPostsRequest) -> types.messages.Messages:
        tag = request.hashtag.casefold()
        collected: list[SimMessage] = []
        for feed in self.feeds.values():
            for message in feed.search_results:
                if tag in (message.message or "").casefold():
                    collected.append(message)
        collected.sort(key=lambda item: item.id, reverse=True)
        collected = collected[: request.limit]
        now = datetime.now(timezone.utc)
        return types.messages.Messages(
            messages=[self._as_tl_message(msg) for msg in collected],
            chats=[
                types.Channel(
                    id=feed.channel.channel_id,
                    title=feed.channel.title,
                    photo=types.ChatPhotoEmpty(),
                    date=now,
                    username=feed.channel.username,
                    megagroup=False,
                    broadcast=True,
                )
                for feed in self.feeds.values()
            ],
            users=[],
            topics=[],
        )

    @staticmethod
    def _as_tl_message(message: SimMessage) -> types.Message:
        return types.Message(
            id=message.id,
            peer_id=message.peer_id,
            date=message.date,
            message=message.message,
            grouped_id=message.grouped_id,
            media=types.MessageMediaPhoto(
                photo=types.Photo(
                    id=message.id,
                    access_hash=0,
                    file_reference=b"",
                    date=message.date,
                    sizes=[],
                    dc_id=2,
                ),
            )
            if message.photo
            else None,
        )


def make_channel(username: str = "testchannel", *, channel_id: int = 10001) -> SimChannel:
    return SimChannel(channel_id=channel_id, username=username, title=f"Title {username}")