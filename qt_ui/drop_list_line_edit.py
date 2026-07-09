"""Drop list line edit · Поле со списком и DnD .txt"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLineEdit

from app.i18n import tr
from app.list_field_utils import merge_comma_field

_TOKEN_SPLIT = re.compile(r"[\n\r,;]+")


class DropListLineEdit(QLineEdit):
    def __init__(self, *args, for_hashtags: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._for_hashtags = for_hashtags
        self.setAcceptDrops(True)
        self._apply_placeholder()

    def _apply_placeholder(self) -> None:
        self.setPlaceholderText(tr("field.list_placeholder"))

    def retranslate_ui(self) -> None:
        self._apply_placeholder()

    def _normalize_token(self, token: str) -> str:
        text = token.strip()
        if not text:
            return ""
        if self._for_hashtags:
            from app.tg_hashtag_dl import normalize_hashtag

            return normalize_hashtag(text)
        return text.strip().lstrip("@")

    def _tokens_from_text(self, raw: str) -> list[str]:
        return [
            normalized
            for part in _TOKEN_SPLIT.split(raw or "")
            if (normalized := self._normalize_token(part))
        ]

    def _merge_tokens(self, tokens: list[str]) -> None:
        if not tokens:
            return
        self.setText(merge_comma_field(self.text(), tokens))

    def insertFromMimeData(self, source) -> None:
        if source.hasText():
            text = source.text()
            if _TOKEN_SPLIT.search(text):
                self._merge_tokens(self._tokens_from_text(text))
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        tokens: list[str] = []
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                try:
                    from pathlib import Path

                    text = Path(path).read_text(encoding="utf-8")
                except OSError:
                    continue
                tokens.extend(self._tokens_from_text(text))
        elif mime.hasText():
            tokens.extend(self._tokens_from_text(mime.text()))
        if tokens:
            self._merge_tokens(tokens)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
