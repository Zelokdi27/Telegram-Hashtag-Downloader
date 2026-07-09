"""Preview image viewer · Полноразмерный просмотр фото"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImageReader, QKeyEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.preview_core import PreviewItem, format_message_local_datetime

from .win_chrome import apply_window_theme, center_dialog_on_parent

FullPreviewCallback = Callable[[str | None, str | None], None]
FullPreviewLoader = Callable[[PreviewItem, FullPreviewCallback], None]


def _load_pixmap_from_path(path: str) -> QPixmap | None:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if not reader.canRead():
        return None
    image = reader.read()
    if image.isNull():
        return None
    return QPixmap.fromImage(image)


class PreviewImageViewerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        item: PreviewItem,
        *,
        loader: FullPreviewLoader | None = None,
        initial_path: str | None = None,
        placeholder_path: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setModal(True)
        apply_window_theme(self, parent)
        self._item = item
        self._loader = loader
        self._closed = False
        self._source_pixmap: QPixmap | None = None
        self._native_size = False
        self._awaiting_full = False
        self._hint_label: QLabel | None = None
        self._close_btn: QPushButton | None = None

        parts = [tr("viewer.title"), format_message_local_datetime(item.message), item.channel]
        if item.hashtag:
            parts.insert(1, f"#{item.hashtag}")
        self.setWindowTitle(" · ".join(parts))
        self.resize(960, 720)
        self.setMinimumSize(480, 360)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        self._status = QLabel()
        self._status.setObjectName("muted")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._image_label)
        root.addWidget(self._scroll, stretch=1)

        controls = QHBoxLayout()
        self._native_check = QCheckBox(tr("viewer.native_size"))
        self._native_check.toggled.connect(self._on_native_toggled)
        controls.addWidget(self._native_check)
        controls.addStretch()
        root.addLayout(controls)

        self._hint_label = QLabel(tr("viewer.hint"))
        self._hint_label.setObjectName("muted")
        root.addWidget(self._hint_label)

        row = QHBoxLayout()
        row.addStretch()
        self._close_btn = QPushButton(tr("viewer.close"))
        self._close_btn.clicked.connect(self.accept)
        row.addWidget(self._close_btn)
        root.addLayout(row)

        if initial_path:
            self._set_source_pixmap(_load_pixmap_from_path(initial_path), from_cache=True)
        elif placeholder_path:
            placeholder = _load_pixmap_from_path(placeholder_path)
            if placeholder is not None and not placeholder.isNull():
                self._set_source_pixmap(placeholder, placeholder=True)
                self._awaiting_full = loader is not None
                self._status.setText(tr("viewer.loading_with_thumb"))
            else:
                self._status.setText(tr("viewer.loading"))
                self._awaiting_full = loader is not None
        elif loader is not None:
            self._status.setText(tr("viewer.loading"))
            self._awaiting_full = True
        else:
            self._status.setText(tr("viewer.unavailable"))
            self._image_label.setText(tr("viewer.load_failed"))

        if loader is not None and not initial_path:
            loader(item, self._on_loaded)

    def _viewport_limit(self) -> tuple[int, int]:
        viewport = self._scroll.viewport().size()
        return max(120, viewport.width() - 8), max(120, viewport.height() - 8)

    def _scaled_pixmap(self, source: QPixmap) -> QPixmap:
        if self._native_size:
            return source
        max_w, max_h = self._viewport_limit()
        if source.width() <= max_w and source.height() <= max_h:
            return source
        return source.scaled(
            max_w,
            max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _apply_display_pixmap(self) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        shown = self._scaled_pixmap(self._source_pixmap)
        self._image_label.setPixmap(shown)
        self._image_label.setFixedSize(shown.size())
        native = f"{self._source_pixmap.width()}×{self._source_pixmap.height()} px"
        if self._native_size:
            mode = tr("viewer.mode.native")
        elif shown.size() != self._source_pixmap.size():
            mode = tr("viewer.mode.fitted", w=shown.width(), h=shown.height())
        else:
            mode = tr("viewer.mode.unscaled")
        suffix = tr("viewer.loading_suffix") if self._awaiting_full else ""
        self._status.setText(
            tr(
                "viewer.status",
                id=self._item.message.id,
                native=native,
                mode=mode,
                suffix=suffix,
            ),
        )

    def _set_source_pixmap(
        self,
        pixmap: QPixmap | None,
        *,
        from_cache: bool = False,
        placeholder: bool = False,
    ) -> None:
        if pixmap is None or pixmap.isNull():
            self._image_label.setText(tr("viewer.display_failed"))
            return
        self._source_pixmap = pixmap
        if from_cache:
            self._status.setText(
                tr(
                    "viewer.cached",
                    id=self._item.message.id,
                    w=pixmap.width(),
                    h=pixmap.height(),
                ),
            )
        elif placeholder:
            pass
        self._apply_display_pixmap()

    def _on_native_toggled(self, checked: bool) -> None:
        self._native_size = checked
        self._apply_display_pixmap()

    def _on_loaded(self, path: str | None, error: str | None) -> None:
        if self._closed:
            return

        def apply() -> None:
            self._awaiting_full = False
            if path:
                self._set_source_pixmap(_load_pixmap_from_path(path))
            elif self._source_pixmap is None:
                fallback = error or tr("viewer.load_failed")
                self._status.setText(fallback)
                self._image_label.setText(self._status.text())
            else:
                self._status.setText(
                    tr("viewer.error_fallback", error=error or tr("viewer.load_failed")),
                )

        QTimer.singleShot(0, self, apply)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._source_pixmap is not None and not self._native_size:
            self._apply_display_pixmap()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._source_pixmap is not None:
            self._apply_display_pixmap()

    def done(self, result: int) -> None:
        self._closed = True
        super().done(result)


def show_preview_image_viewer(
    parent: QWidget,
    item: PreviewItem,
    *,
    loader: FullPreviewLoader | None = None,
    initial_path: str | None = None,
    placeholder_path: str | None = None,
) -> None:
    dialog = PreviewImageViewerDialog(
        parent,
        item,
        loader=loader,
        initial_path=initial_path,
        placeholder_path=placeholder_path,
    )
    dialog.resize(960, 720)
    center_dialog_on_parent(dialog, parent)
    dialog.exec()
