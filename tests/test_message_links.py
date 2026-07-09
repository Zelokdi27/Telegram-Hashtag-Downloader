"""Message link tests · Ссылки на посты Telegram"""

from __future__ import annotations

from telethon.tl.types import PeerChannel

from app.message_links import build_message_link
from tests.sim_telegram import SimMessage, make_channel


def test_build_message_link_public_channel():
    channel = make_channel("mychannel", channel_id=101)
    message = SimMessage(msg_id=42, channel_id=channel.id, kind="photo")

    assert build_message_link(message, "mychannel") == "https://t.me/mychannel/42"


def test_build_message_link_private_channel():
    message = SimMessage(msg_id=7, channel_id=555, kind="photo")
    message.peer_id = PeerChannel(channel_id=555)

    assert build_message_link(message, "Private Title") == "https://t.me/c/555/7"
