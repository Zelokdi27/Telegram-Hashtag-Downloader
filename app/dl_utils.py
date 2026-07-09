"""Download utils · Утилиты скачивания"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path

from telethon import types
from telethon.tl.custom.message import Message

from .i18n import tr

logger = logging.getLogger(__name__)

_HASH_CHUNK_SIZE = 4 * 1024 * 1024
_HASH_MAX_FILE_BYTES = 100 * 1024 * 1024


def file_content_sha256(path: Path) -> str | None:
    """File SHA-256 · Хеш по частям; >100 МБ — None"""
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size > _HASH_MAX_FILE_BYTES:
        logger.debug(
            tr(
                "log.dl_utils.hash_skip_large",
                mb=f"{size / (1024 * 1024):.1f}",
                name=path.name,
            ),
        )
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_hash_digest_debug(file_path: Path, digest: str) -> None:
    """Hash dedup verify · VERIFY_HASH_DEDUP=1 самопроверка"""
    flag = os.getenv("VERIFY_HASH_DEDUP", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return
    second = file_content_sha256(file_path)
    if second is not None and second != digest:
        logger.warning(tr("log.dl_utils.verify_hash_mismatch", path=file_path))


MEDIA_SUFFIXES = {
    "photo": "jpg",
    "video": "mp4",
    "document": "bin",
    "audio": "mp3",
    "voice": "ogg",
    "animation": "mp4",
    "video_note": "mp4",
    "sticker": "webp",
}


def normalize_hashtag(value: str) -> str:
    tag = value.strip().lstrip("#")
    if not tag:
        raise ValueError(tr("errors.validation.hashtag_empty"))
    return tag


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да"}


def message_key(message: Message) -> str:
    peer_id = message.peer_id
    if isinstance(peer_id, types.PeerChannel):
        channel_id = peer_id.channel_id
    elif isinstance(peer_id, types.PeerChat):
        channel_id = peer_id.chat_id
    elif isinstance(peer_id, types.PeerUser):
        channel_id = peer_id.user_id
    else:
        channel_id = getattr(peer_id, "channel_id", 0)
    return f"{channel_id}:{message.id}"


def media_kind(message: Message) -> str | None:
    """Media kind · Тип медиа из Message"""
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "video_note", None):
        return "video_note"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "animation", None):
        return "animation"
    if getattr(message, "sticker", None):
        return "sticker"
    if getattr(message, "document", None):
        return "document"
    if getattr(message, "media", None):
        return "document"
    return None


_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(value: str, max_len: int = 80) -> str:
    cleaned = _WIN_INVALID.sub("_", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = re.sub(r"[^\w\-.]+", "_", cleaned, flags=re.UNICODE)
    cleaned = cleaned.strip("._")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("._")
    return cleaned or "unknown"


def normalize_channel_filter(value: str) -> str:
    raw = value.strip().lstrip("@")
    if not raw:
        return ""
    lowered = raw.lower()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if prefix in lowered:
            raw = raw[lowered.index(prefix) + len(prefix) :]
            break
    return raw.split("/")[0].split("?")[0].strip().lower()


def parse_date_filter(value: str) -> date | None:
    raw = value.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(tr("errors.validation.date_invalid", raw=raw))