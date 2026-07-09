"""Task spec · Спецификация задачи"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.config_store import SettingsData
from app.tg_hashtag_dl import MissingPostRef

TaskMode = Literal["once", "preview", "verify", "integrity_download"]


@dataclass
class TaskSpec:
    mode: TaskMode
    settings: SettingsData
    integrity_refs: list[MissingPostRef] = field(default_factory=list)
    queue_hashtags: list[str] = field(default_factory=list)
