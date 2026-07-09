"""Install paths · Пути установки (исходники и PyInstaller)"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Writable app root · Рабочая папка (.env, data/)"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """Bundled read-only resources · Ресурсы внутри сборки"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_root()))
    return Path(__file__).resolve().parent.parent


PROJECT_DIR = app_root()
BUNDLE_DIR = bundle_root()
