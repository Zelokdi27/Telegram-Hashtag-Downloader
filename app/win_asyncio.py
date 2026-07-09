"""Windows asyncio · Исправление asyncio на Windows"""

from __future__ import annotations

import asyncio
import sys

_applied = False


def fix_windows_asyncio() -> None:
    """Windows asyncio fix · Обход ProactorEventLoop + VPN"""
    global _applied
    if _applied or sys.platform != "win32":
        return

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _applied = True