"""Sliding progress · Бегущий прогресс"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QProgressBar, QStyleOptionProgressBar


class SlidingProgressBar(QProgressBar):
    """Sliding progress bar · Детерминированный % или бегущая полоска"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._indeterminate = False
        self._slide_pos = -0.15
        self._track = QColor("#2a2a2a")
        self._accent = QColor("#1a7f37")
        self._border = QColor("#4a4a4a")
        self._text = QColor("#141414")
        self._text_on_chunk = QColor("#ffffff")
        self.setRange(0, 100)
        self.setFormat("%p%")
        self.setTextVisible(True)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick_slide)

    def set_theme_colors(
        self,
        *,
        track: str,
        accent: str,
        border: str,
        text: str,
        text_on_chunk: str | None = None,
    ) -> None:
        self._track = QColor(track)
        self._accent = QColor(accent)
        self._border = QColor(border)
        self._text = QColor(text)
        self._text_on_chunk = QColor(text_on_chunk or text)
        self.update()

    def set_indeterminate(self, active: bool) -> None:
        if self._indeterminate == active:
            return
        self._indeterminate = active
        if active:
            self._slide_pos = -0.15
            self.setTextVisible(False)
            self._timer.start()
        else:
            self._timer.stop()
            self.setTextVisible(self.value() > 0)
        self.update()

    def is_indeterminate(self) -> bool:
        return self._indeterminate

    def setValue(self, value: int) -> None:
        super().setValue(value)
        if not self._indeterminate:
            self.setTextVisible(value > 0)

    def _tick_slide(self) -> None:
        self._slide_pos += 0.009
        if self._slide_pos > 1.15:
            self._slide_pos = -0.15
        self.update()

    def _bar_text(self) -> str:
        opt = QStyleOptionProgressBar()
        self.initStyleOption(opt)
        return opt.text

    def paintEvent(self, _event) -> None:
        if self._indeterminate:
            self._paint_indeterminate()
            return
        self._paint_determinate()

    def _paint_determinate(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(1, 1, -2, -2)
        radius = 3

        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._track)
        painter.drawRoundedRect(rect, radius, radius)

        span = max(1, self.maximum() - self.minimum())
        done = max(0, self.value() - self.minimum())
        chunk_width = 0
        if done > 0:
            chunk_width = max(1, int(rect.width() * done / span))
            painter.save()
            painter.setClipRect(rect.left(), rect.top(), chunk_width, rect.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._accent)
            painter.drawRoundedRect(rect, radius, radius)
            painter.restore()

        if self.isTextVisible():
            label = self._bar_text()
            if label:
                if chunk_width < rect.width():
                    painter.save()
                    painter.setClipRect(
                        rect.left() + chunk_width,
                        rect.top(),
                        rect.width() - chunk_width,
                        rect.height(),
                    )
                    painter.setPen(self._text)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
                    painter.restore()
                if chunk_width > 0:
                    painter.save()
                    painter.setClipRect(rect.left(), rect.top(), chunk_width, rect.height())
                    painter.setPen(self._text_on_chunk)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
                    painter.restore()

        painter.end()

    def _paint_indeterminate(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(1, 1, -2, -2)
        radius = 3

        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._track)
        painter.drawRoundedRect(rect, radius, radius)

        width = rect.width()
        segment = max(48, int(width * 0.24))
        x = rect.left() + int(self._slide_pos * (width + segment)) - segment

        grad = QLinearGradient(x, rect.top(), x + segment, rect.top())
        accent = self._accent
        grad.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        grad.setColorAt(0.35, QColor(accent.red(), accent.green(), accent.blue(), 90))
        grad.setColorAt(0.5, accent)
        grad.setColorAt(0.65, QColor(accent.red(), accent.green(), accent.blue(), 90))
        grad.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        seg_top = rect.top() + 2
        seg_h = rect.height() - 4
        painter.drawRoundedRect(x, seg_top, segment, seg_h, 2, 2)