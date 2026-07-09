"""Download options · Параметры скачивания"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .i18n import plural_word, tr

@dataclass
class MediaFilterSettings:
    photo: bool = True
    video: bool = True
    animation: bool = True
    sticker: bool = False
    audio: bool = True
    voice: bool = False
    document: bool = True
    video_note: bool = False

    def allows(self, kind: str | None) -> bool:
        if not kind:
            return False
        return getattr(self, kind, False)


def parse_media_filter(
    *,
    photo: bool = True,
    video: bool = True,
    animation: bool = True,
    sticker: bool = False,
    audio: bool = True,
    voice: bool = False,
    document: bool = True,
    video_note: bool = False,
) -> MediaFilterSettings:
    return MediaFilterSettings(
        photo=photo,
        video=video,
        animation=animation,
        sticker=sticker,
        audio=audio,
        voice=voice,
        document=document,
        video_note=video_note,
    )


_SPLIT_RE = re.compile(r"[,;\n]+")


def parse_csv_list(value: str) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw in _SPLIT_RE.split(value.strip()):
        item = raw.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def _normalize_hashtag(value: str) -> str:
    tag = value.strip().lstrip("#")
    if not tag:
        raise ValueError(tr("errors.validation.hashtag_empty"))
    return tag


_HASHTAG_IN_TEXT_RE = re.compile(r"#([^\s#]+)", re.UNICODE)


def extract_hashtags_from_text(text: str) -> set[str]:
    tags: set[str] = set()
    for match in _HASHTAG_IN_TEXT_RE.finditer(text):
        raw = match.group(1).rstrip(".,!?;:)»\"'")
        if not raw:
            continue
        try:
            tags.add(_normalize_hashtag(raw).casefold())
        except ValueError:
            continue
    return tags


def parse_exclude_hashtag_list(value: str) -> list[str]:
    return parse_hashtag_list("", value)


def parse_required_hashtag_list(value: str) -> list[str]:
    return parse_hashtag_list("", value)


def parse_hashtag_list(primary: str, extra: str = "") -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for raw in [primary, *parse_csv_list(extra)]:
        try:
            normalized = _normalize_hashtag(raw)
        except ValueError:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        tags.append(normalized)
    return tags


def parse_channel_list(primary: str, extra: str = "") -> list[str]:
    channels: list[str] = []
    seen: set[str] = set()
    for raw in [primary, *parse_csv_list(extra)]:
        item = raw.strip().lstrip("@")
        if not item:
            continue
        lowered = item.lower()
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if prefix in lowered:
                item = item[lowered.index(prefix) + len(prefix) :]
                break
        item = item.split("/")[0].split("?")[0].strip().lower()
        if not item or item in seen:
            continue
        seen.add(item)
        channels.append(item)
    return channels


def batch_search_count(
    primary_tag: str,
    extra_tags: str = "",
    primary_channel: str = "",
    extra_channels: str = "",
) -> tuple[int, int, int]:
    hashtags = parse_hashtag_list(primary_tag, extra_tags)
    channels = parse_channel_list(primary_channel, extra_channels) or [""]
    tag_n = len(hashtags)
    ch_n = len(channels)
    return tag_n, ch_n, tag_n * ch_n


def format_batch_search_hint(
    primary_tag: str,
    extra_tags: str = "",
    primary_channel: str = "",
    extra_channels: str = "",
) -> str:
    tag_n, ch_n, total = batch_search_count(
        primary_tag,
        extra_tags,
        primary_channel,
        extra_channels,
    )
    if total <= 1 or tag_n == 0:
        return ""

    search_word = plural_word("search", total)
    tag_word = plural_word("hashtag", tag_n)
    ch_word = plural_word("channel", ch_n)
    if tag_n > 1 and ch_n > 1:
        return tr(
            "batch.hint.tags_channels",
            tags=tag_n,
            tag_word=tag_word,
            channels=ch_n,
            ch_word=ch_word,
            total=total,
            search_word=search_word,
        )
    if tag_n > 1:
        return tr(
            "batch.hint.tags_only",
            tags=tag_n,
            tag_word=tag_word,
            total=total,
            search_word=search_word,
        )
    return tr(
        "batch.hint.channels_only",
        channels=ch_n,
        ch_word=ch_word,
        total=total,
        search_word=search_word,
    )