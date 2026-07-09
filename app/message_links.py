"""Message links · Ссылки на посты Telegram"""

from __future__ import annotations

import re

from telethon.tl.custom.message import Message
from telethon.tl.types import PeerChannel

_USERNAME_RE = re.compile(r"^[\w]{3,}$", re.ASCII)


def build_message_link(message: Message, channel: str = "") -> str | None:
    """Message link URL · t.me URL публичного/приватного канала"""
    msg_id = getattr(message, "id", None)
    if not msg_id:
        return None

    name = channel.strip().lstrip("@")
    if name and _USERNAME_RE.match(name):
        return f"https://t.me/{name}/{msg_id}"

    peer = getattr(message, "peer_id", None)
    if isinstance(peer, PeerChannel):
        return f"https://t.me/c/{peer.channel_id}/{msg_id}"

    return None
