"""Hashtag history UI · Выпадающий список хештегов"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QStringListModel, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QAbstractItemView, QCompleter, QLineEdit

from app.hashtag_history import HASHTAG_SUGGEST_LIMIT, suggest_hashtag_history

from .theme import build_hashtag_completer_popup_stylesheet


def _text_width(font_metrics: QFontMetrics, text: str) -> int:
    rect = font_metrics.boundingRect(text)
    width = rect.width
    return width() if callable(width) else int(width)


def measure_completer_popup_width(
    *,
    tags: list[str],
    font_metrics: QFontMetrics,
    entry_width: int,
    popup: QAbstractItemView | None = None,
    completion_model=None,
) -> int:
    """Completer popup width · Ширина popup по sizeHint/шрифту"""
    if not tags or entry_width <= 0:
        return 0

    content_width = 0
    if popup is not None and completion_model is not None:
        for row in range(completion_model.rowCount()):
            index = completion_model.index(row, 0)
            content_width = max(content_width, popup.sizeHintForIndex(index).width())

    if content_width <= 0:
        content_width = max(_text_width(font_metrics, tag) for tag in tags)

    frame = popup.frameWidth() * 2 if popup is not None else 0
    # Popup width margin · Запас по ширине символа шрифта
    inset = font_metrics.averageCharWidth() * 2
    width = content_width + frame + inset
    return min(width, entry_width)


class HashtagLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._on_empty_click: Callable[[], None] | None = None

    def set_empty_click_handler(self, handler: Callable[[], None] | None) -> None:
        self._on_empty_click = handler

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.text().strip()
            and self._on_empty_click is not None
        ):
            self._on_empty_click()


class HashtagHistoryCompleter(QObject):
    POPUP_OBJECT_NAME = "hashtagCompleterPopup"

    def __init__(self, entry: HashtagLineEdit, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.model = QStringListModel(parent)
        self.completer = QCompleter(self.model, entry)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setMaxVisibleItems(HASHTAG_SUGGEST_LIMIT)
        self.entry.setCompleter(self.completer)

        popup = self.completer.popup()
        popup.setObjectName(self.POPUP_OBJECT_NAME)
        popup.installEventFilter(self)

        entry.set_empty_click_handler(self.show_recent_if_empty)
        self.refresh()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        try:
            popup = self.completer.popup()
        except RuntimeError:
            return False
        if watched is popup and event.type() == QEvent.Type.Show:
            self._resize_popup()
        return super().eventFilter(watched, event)

    def apply_popup_theme(self, *, dark: bool) -> None:
        self.completer.popup().setStyleSheet(build_hashtag_completer_popup_stylesheet(dark=dark))

    def refresh(self) -> None:
        self.model.setStringList(suggest_hashtag_history())

    def show_recent_if_empty(self) -> None:
        if self.entry.text().strip():
            return
        self.refresh()
        if not self.model.stringList():
            return
        self.completer.setCompletionPrefix("")
        self._resize_popup()
        self.completer.complete()

    def _resize_popup(self) -> None:
        popup = self.completer.popup()
        tags = self._visible_completion_tags()
        width = measure_completer_popup_width(
            tags=tags,
            font_metrics=self.entry.fontMetrics(),
            entry_width=self.entry.width(),
            popup=popup,
            completion_model=self.completer.completionModel(),
        )
        if width > 0:
            popup.setFixedWidth(width)

    def _visible_completion_tags(self) -> list[str]:
        completion_model = self.completer.completionModel()
        tags: list[str] = []
        for row in range(completion_model.rowCount()):
            value = completion_model.data(completion_model.index(row, 0))
            if value:
                tags.append(str(value))
        if tags:
            return tags
        return self.model.stringList()