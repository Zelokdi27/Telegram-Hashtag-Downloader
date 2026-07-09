"""Application version · Версия приложения"""

from __future__ import annotations

__version__ = "1.0.0"
APP_NAME = "Telegram Hashtag Downloader"
APP_SLUG = "telegram-hashtag-downloader"

# Author metadata · Авторство
APP_AUTHOR = "Zelokdi"
APP_COPYRIGHT_HOLDER = "Zelokdi"
APP_CONTACT_TELEGRAM = "@Zelokdi"
APP_LICENSE = "MIT License"
# GitHub / сайт проекта (пусто = без ссылки в «О программе»)
APP_URL = ""


def copyright_line() -> str:
    year = "2026"
    return f"Copyright © {year} {APP_COPYRIGHT_HOLDER}. {APP_LICENSE}."
