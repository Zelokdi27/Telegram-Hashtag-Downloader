from __future__ import annotations

from telethon import TelegramClient

from .downloader_control import DownloaderControlMixin
from .downloader_download import DownloaderDownloadMixin
from .downloader_filters import DownloaderFiltersMixin
from .downloader_integrity import DownloaderIntegrityMixin
from .downloader_search import DownloaderSearchMixin
from .downloader_state import DownloaderStateMixin


class HashtagDownloader(
    DownloaderControlMixin,
    DownloaderStateMixin,
    DownloaderFiltersMixin,
    DownloaderSearchMixin,
    DownloaderDownloadMixin,
    DownloaderIntegrityMixin,
):
    """Hashtag downloader · Фасад скачивания из mixins"""

    client: TelegramClient
