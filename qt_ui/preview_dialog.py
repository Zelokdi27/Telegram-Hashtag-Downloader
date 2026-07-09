"""Preview dialog · Диалог предпросмотра"""

from __future__ import annotations

import queue
import threading
import time
import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImageReader, QIntValidator, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.i18n import kind_labels, preview_filter_labels, preview_sort_labels, tr
from app.message_links import build_message_link
from app.preview_core import (
    PREVIEW_FILTER_MODES,
    PREVIEW_SORT_MODES,
    PREVIEW_THUMB_SIZE,
    PreviewDuplicateTracker,
    PreviewItem,
    apply_preview_view,
    content_duplicate_badge,
    count_content_duplicates,
    disk_status_badge,
    format_message_local_datetime,
    is_content_duplicate,
    preview_channels,
    selection_summary,
    set_items_selection,
)
from app.preview_index import PreviewIndexSummary, format_sequential_index_status
from .preview_image_viewer import FullPreviewLoader, show_preview_image_viewer
from .win_chrome import apply_window_theme, center_dialog_on_parent

logger = logging.getLogger(__name__)

GRID_COLUMNS = 3
PAGE_ROWS = 12
PAGE_SIZE = GRID_COLUMNS * PAGE_ROWS
_STREAM_APPEND_COALESCE_MS = 48
_HEADER_UPDATE_COALESCE_MS = 120
_UI_SYNC_MS = 100
_THUMB_CACHE_MAX = 256
_THUMB_PIXMAP_CACHE: OrderedDict[str, QPixmap] = OrderedDict()


class _ThumbLabel(QLabel):
    """Thumb label · Клик — выбор; двойной — оригинал"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._single_click = QTimer(self)
        self._single_click.setSingleShot(True)
        self._single_click.setInterval(220)
        self._single_click.timeout.connect(self._emit_single_click)
        self.on_single_click: Callable[[], None] | None = None
        self.on_double_click: Callable[[], None] | None = None

    def _emit_single_click(self) -> None:
        if self.on_single_click is not None:
            self.on_single_click()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._single_click.start()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self._single_click.stop()
        if self.on_double_click is not None:
            self.on_double_click()
        event.accept()


def _load_thumb_pixmap(path: str, *, max_side: int = PREVIEW_THUMB_SIZE) -> QPixmap | None:
    """Thumb pixmap load · Загрузка миниатюры без PIL"""
    cached = _THUMB_PIXMAP_CACHE.get(path)
    if cached is not None:
        _THUMB_PIXMAP_CACHE.move_to_end(path)
        return cached
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if not reader.canRead():
        return None
    size = reader.size()
    if size.isValid() and size.width() > 0 and size.height() > 0:
        scale = min(max_side / size.width(), max_side / size.height(), 1.0)
        if scale < 1.0:
            reader.setScaledSize(
                QSize(
                    max(1, int(size.width() * scale)),
                    max(1, int(size.height() * scale)),
                ),
            )
    image = reader.read()
    if image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    _THUMB_PIXMAP_CACHE[path] = pixmap
    while len(_THUMB_PIXMAP_CACHE) > _THUMB_CACHE_MAX:
        _THUMB_PIXMAP_CACHE.popitem(last=False)
    return pixmap


@dataclass
class SequentialBatchInfo:
    batch_number: int
    configured_batch_size: int
    effective_batch_size: int
    publication_cursor: int
    index_summary: PreviewIndexSummary
    files_downloaded: int = 0
    media_collected_before: int = 0
    media_limit: int = 0


@dataclass
class PreviewDialogResult:
    action: Literal["download", "skip_batch", "stop"]
    items: list[PreviewItem]


@dataclass
class _Card:
    item: PreviewItem | None
    frame: QFrame
    checkbox: QCheckBox
    thumb_label: QLabel
    status_label: QLabel
    duplicate_label: QLabel
    meta_label: QLabel
    summary_label: QLabel
    telegram_btn: QPushButton | None = None


class _PreviewPageView:
    """Paged card pool · Переиспользуемые карточки без deleteLater при обновлении"""

    def __init__(
        self,
        grid_layout: QGridLayout,
        allocate_card: Callable[[], _Card],
        bind_card: Callable[[_Card, PreviewItem], None],
    ) -> None:
        self._layout = grid_layout
        self._allocate_card = allocate_card
        self._bind_card = bind_card
        self._pool: list[_Card] = []

    def show_items(self, items: list[PreviewItem]) -> list[_Card]:
        needed = len(items)
        while len(self._pool) < needed:
            card = self._allocate_card()
            index = len(self._pool)
            row, col = divmod(index, GRID_COLUMNS)
            self._layout.addWidget(card.frame, row, col)
            self._pool.append(card)
        shown: list[_Card] = []
        for index, card in enumerate(self._pool):
            if index < needed:
                self._bind_card(card, items[index])
                card.frame.setVisible(True)
                shown.append(card)
            else:
                card.frame.setVisible(False)
        return shown

    def hide_all(self) -> None:
        for card in self._pool:
            card.frame.setVisible(False)

    def dispose(self) -> None:
        for card in self._pool:
            card.frame.setParent(None)
            card.frame.deleteLater()
        self._pool.clear()


class PreviewDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        items: list[PreviewItem],
        *,
        streaming: bool = False,
        item_queue: queue.Queue | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_closing: Callable[[], None] | None = None,
        thumb_queue: queue.Queue[PreviewItem] | None = None,
        on_ready: Callable[[], None] | None = None,
        preview_pause: threading.Event | None = None,
        sequential_batch: SequentialBatchInfo | None = None,
        full_preview_loader: FullPreviewLoader | None = None,
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
        self._items = list(items)
        self._duplicate_tracker = PreviewDuplicateTracker()
        for item in self._items:
            self._duplicate_tracker.annotate(item)
        self._streaming = streaming
        self._item_queue = item_queue
        self._collection_finished = not streaming
        self._should_cancel = should_cancel
        self._on_closing = on_closing
        self._thumb_queue = thumb_queue
        self._on_ready = on_ready
        self._preview_pause = preview_pause or threading.Event()
        self._sequential_batch = sequential_batch
        self._full_preview_loader = full_preview_loader
        self._result: list[PreviewItem] | None = None
        self._result_action: Literal["download", "skip_batch", "stop"] = "stop"
        self._closed = False
        self._current_page = 0
        self._filter_mode = "all"
        self._sort_mode = "date_desc"
        self._channel_pick = ""
        self._page_by_channel: dict[str, int] = {"": 0}
        self._cards: list[_Card] = []
        self._card_by_item_id: dict[int, _Card] = {}
        self._known_channels = set(preview_channels(self._items))
        self._pending_stream_items: list[PreviewItem] = []
        self._channel_refresh_timer = QTimer(self)
        self._channel_refresh_timer.setSingleShot(True)
        self._channel_refresh_timer.setInterval(120)
        self._channel_refresh_timer.timeout.connect(self._refresh_channel_combo)
        self._view_cache_key: tuple[str, str, str] | None = None
        self._view_cache_items: list[PreviewItem] | None = None
        self._total_pages = max(1, (len(self._visible_items()) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._skip_batch_btn: QPushButton | None = None
        self._stop_all_btn: QPushButton | None = None
        self._cancel_btn: QPushButton | None = None
        self._legend_label: QLabel | None = None
        self._filter_show_label: QLabel | None = None
        self._filter_channel_label: QLabel | None = None
        self._filter_sort_label: QLabel | None = None
        self._jump_label: QLabel | None = None

        self.resize(900, 700)
        self.setMinimumSize(640, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        self._header = QLabel()
        self._header.setWordWrap(True)
        root.addWidget(self._header)
        self._legend_label = QLabel()
        self._legend_label.setObjectName("muted")
        self._legend_label.setWordWrap(True)
        root.addWidget(self._legend_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._grid_host = QWidget()
        self._grid_layout = QGridLayout(self._grid_host)
        self._grid_layout.setSpacing(8)
        for col in range(GRID_COLUMNS):
            self._grid_layout.setColumnStretch(col, 1)
        self._page_view = _PreviewPageView(
            self._grid_layout,
            self._allocate_card,
            self._bind_card,
        )
        self._scroll.setWidget(self._grid_host)
        root.addWidget(self._scroll, stretch=1)

        pager_host = QWidget()
        pager_root = QVBoxLayout(pager_host)
        pager_root.setContentsMargins(0, 4, 0, 4)
        pager_root.setSpacing(6)

        nav_row = QHBoxLayout()
        nav_row.addStretch()
        self._prev_btn = QPushButton()
        self._prev_btn.clicked.connect(lambda: self._change_page(-1))
        nav_row.addWidget(self._prev_btn)
        nav_row.addSpacing(16)
        self._page_label = QLabel()
        self._page_label.setObjectName("pagerInfo")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setMinimumWidth(110)
        nav_row.addWidget(self._page_label)
        nav_row.addSpacing(16)
        self._next_btn = QPushButton()
        self._next_btn.clicked.connect(lambda: self._change_page(1))
        nav_row.addWidget(self._next_btn)
        nav_row.addStretch()
        pager_root.addLayout(nav_row)

        jump_row = QHBoxLayout()
        jump_row.addStretch()
        self._jump_label = QLabel()
        jump_row.addWidget(self._jump_label)
        self._page_jump_entry = QLineEdit()
        self._page_jump_entry.setFixedWidth(52)
        self._page_jump_entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_jump_entry.setValidator(QIntValidator(1, 1, self))
        self._page_jump_entry.returnPressed.connect(self._go_to_page)
        jump_row.addWidget(self._page_jump_entry)
        self._page_go_btn = QPushButton()
        self._page_go_btn.clicked.connect(self._go_to_page)
        jump_row.addWidget(self._page_go_btn)
        jump_row.addStretch()
        pager_root.addLayout(jump_row)

        root.addWidget(pager_host)

        self._page_info_label = QLabel("")
        self._page_info_label.setObjectName("pagerInfo")
        self._page_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._page_info_label)

        toolbar = QHBoxLayout()
        self._select_all_btn = QPushButton()
        self._select_all_btn.clicked.connect(self._select_all_visible)
        self._select_none_btn = QPushButton()
        self._select_none_btn.clicked.connect(self._select_none_visible)
        self._select_channel_btn = QPushButton()
        self._select_channel_btn.clicked.connect(self._select_current_channel)
        self._deselect_on_disk_btn = QPushButton()
        self._deselect_on_disk_btn.clicked.connect(self._deselect_on_disk)
        toolbar.addWidget(self._select_all_btn)
        toolbar.addWidget(self._select_none_btn)
        toolbar.addWidget(self._select_channel_btn)
        toolbar.addWidget(self._deselect_on_disk_btn)
        self._pause_btn = QPushButton()
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        toolbar.addWidget(self._pause_btn)
        toolbar.addStretch()
        self._filter_show_label = QLabel()
        toolbar.addWidget(self._filter_show_label)
        self._filter_combo = QComboBox()
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_combo)
        root.addLayout(toolbar)

        filters_row = QHBoxLayout()
        self._filter_channel_label = QLabel()
        filters_row.addWidget(self._filter_channel_label)
        self._channel_combo = QComboBox()
        self._channel_combo.setMinimumWidth(160)
        self._channel_combo.currentIndexChanged.connect(self._on_channel_changed)
        filters_row.addWidget(self._channel_combo, stretch=1)
        self._filter_sort_label = QLabel()
        filters_row.addWidget(self._filter_sort_label)
        self._sort_combo = QComboBox()
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        filters_row.addWidget(self._sort_combo)
        self._selection_label = QLabel("")
        self._selection_label.setObjectName("muted")
        filters_row.addWidget(self._selection_label, stretch=1, alignment=Qt.AlignmentFlag.AlignRight)
        root.addLayout(filters_row)
        self._refresh_channel_combo()

        buttons = QHBoxLayout()
        buttons.addStretch()
        if sequential_batch is not None:
            self._skip_batch_btn = QPushButton()
            self._skip_batch_btn.clicked.connect(self._on_skip_batch)
            self._stop_all_btn = QPushButton()
            self._stop_all_btn.clicked.connect(self._on_stop_all)
            buttons.addWidget(self._skip_batch_btn)
            buttons.addWidget(self._stop_all_btn)
        else:
            self._cancel_btn = QPushButton()
            self._cancel_btn.clicked.connect(self._on_cancel)
            buttons.addWidget(self._cancel_btn)
        self._download_btn = QPushButton()
        self._download_btn.setObjectName("primary")
        self._download_btn.clicked.connect(self._on_download)
        buttons.addWidget(self._download_btn)
        root.addLayout(buttons)

        self._retranslate_ui()
        self._render_page(0)
        self._update_header()

        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.setInterval(_STREAM_APPEND_COALESCE_MS)
        self._stream_flush_timer.timeout.connect(self._flush_stream_appends)
        self._header_update_timer = QTimer(self)
        self._header_update_timer.setSingleShot(True)
        self._header_update_timer.setInterval(_HEADER_UPDATE_COALESCE_MS)
        self._header_update_timer.timeout.connect(self._update_header)

        self._ui_sync_timer = QTimer(self)
        self._ui_sync_timer.timeout.connect(self._sync_ui)
        self._ui_sync_timer.start(_UI_SYNC_MS)

        self._set_streaming_controls_locked(self._streaming and not self._collection_finished)

        if on_ready:
            QTimer.singleShot(0, on_ready)

    def _set_streaming_controls_locked(self, locked: bool) -> None:
        return

    def _sync_ui(self) -> None:
        if self._closed:
            return
        if self._should_cancel and self._should_cancel():
            self._on_cancel()
            return
        if self._preview_pause.is_set():
            return
        if self._item_queue is not None:
            self._drain_item_queue()
        if self._thumb_queue is not None:
            self._drain_thumb_queue()

    def _drain_item_queue(self) -> None:
        if self._item_queue is None:
            return
        batch: list[PreviewItem] = []
        for _ in range(16):
            try:
                item = self._item_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._collection_finished = True
                self._set_streaming_controls_locked(False)
                if self._stream_flush_timer.isActive():
                    self._stream_flush_timer.stop()
                if self._pending_stream_items:
                    self._flush_stream_appends()
                self._refresh_channel_combo()
                self._update_header()
                if batch:
                    self._append_items(batch)
                return
            batch.append(item)
        if batch:
            self._append_items(batch)

    def _drain_thumb_queue(self) -> None:
        if self._thumb_queue is None:
            return
        touched: list[_Card] = []
        for _ in range(12):
            try:
                loaded = self._thumb_queue.get_nowait()
            except queue.Empty:
                break
            card = self._card_by_item_id.get(id(loaded))
            if card is not None:
                touched.append(card)
        if not touched:
            return
        self._scroll.setUpdatesEnabled(False)
        try:
            for card in touched:
                self._update_thumb(card)
        finally:
            self._scroll.setUpdatesEnabled(True)

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(tr("preview.window.title"))
        self._prev_btn.setText(tr("preview.nav.prev"))
        self._next_btn.setText(tr("preview.nav.next"))
        if self._jump_label is not None:
            self._jump_label.setText(tr("preview.nav.jump_label"))
        self._page_jump_entry.setToolTip(tr("preview.nav.jump_tip"))
        self._page_go_btn.setText(tr("preview.nav.go"))
        self._select_all_btn.setText(tr("preview.select.shown"))
        self._select_all_btn.setToolTip(tr("preview.select.shown_tip"))
        self._select_none_btn.setText(tr("preview.select.none_shown"))
        self._select_none_btn.setToolTip(tr("preview.select.none_shown_tip"))
        self._deselect_on_disk_btn.setText(tr("preview.deselect.on_disk"))
        self._deselect_on_disk_btn.setToolTip(tr("preview.deselect.on_disk_tip"))
        self._pause_btn.setToolTip(tr("preview.pause.tip"))
        if self._legend_label is not None:
            self._legend_label.setText(tr("preview.legend.text"))
        if self._filter_show_label is not None:
            self._filter_show_label.setText(tr("preview.filter.show"))
        if self._filter_channel_label is not None:
            self._filter_channel_label.setText(tr("preview.filter.channel"))
        if self._filter_sort_label is not None:
            self._filter_sort_label.setText(tr("preview.filter.sort"))
        if self._skip_batch_btn is not None:
            self._skip_batch_btn.setText(tr("preview.batch.skip"))
        if self._stop_all_btn is not None:
            self._stop_all_btn.setText(tr("preview.batch.stop"))
        if self._cancel_btn is not None:
            self._cancel_btn.setText(tr("preview.cancel"))
        filter_idx = self._filter_combo.currentIndex()
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        self._filter_combo.addItems(preview_filter_labels())
        if 0 <= filter_idx < self._filter_combo.count():
            self._filter_combo.setCurrentIndex(filter_idx)
        self._filter_combo.blockSignals(False)
        sort_idx = self._sort_combo.currentIndex()
        self._sort_combo.blockSignals(True)
        self._sort_combo.clear()
        self._sort_combo.addItems(preview_sort_labels())
        if 0 <= sort_idx < self._sort_combo.count():
            self._sort_combo.setCurrentIndex(sort_idx)
        self._sort_combo.blockSignals(False)
        self._refresh_channel_combo()
        self._sync_pause_button_label()
        self._update_pager_labels()
        self._update_selection_ui()
        self._update_header()
        for card in self._cards:
            card.checkbox.setText(tr("preview.card.download"))
            if card.telegram_btn is not None:
                card.telegram_btn.setToolTip(tr("preview.card.telegram"))
            if card.item is not None and card.item.kind == "photo" and self._full_preview_loader is not None:
                card.thumb_label.setToolTip(tr("preview.card.thumb_tip"))
            if card.item is not None:
                self._refresh_card(card)
                self._update_thumb(card)

    def _sync_pause_button_label(self) -> None:
        if self._preview_pause.is_set():
            self._pause_btn.setText(tr("preview.pause.resume"))
        else:
            self._pause_btn.setText(tr("preview.pause.label"))

    def _update_header(self) -> None:
        if self._sequential_batch is not None:
            batch = self._sequential_batch
            status = format_sequential_index_status(
                batch.index_summary,
                batch_number=batch.batch_number,
                publication_cursor=batch.publication_cursor,
                batch_size=batch.configured_batch_size,
                files_downloaded=batch.files_downloaded,
                media_shown=batch.media_collected_before + len(self._items),
                media_limit=batch.media_limit,
            )
            visible = len(self._visible_items())
            if self._streaming and not self._collection_finished:
                batch_hint = tr("preview.hint.loading")
            else:
                batch_hint = tr("preview.hint.click")
            if self._full_preview_loader is not None:
                batch_hint += " " + tr("preview.hint.dblclick")
            filter_hint = (
                " " + tr("preview.hint.shown", n=visible) if self._filter_mode != "all" else ""
            )
            self._header.setText(f"{status}\n{batch_hint}{filter_hint}{self._selection_hint()}")
            self._update_selection_ui()
            return
        visible = len(self._visible_items())
        total = len(self._items)
        if self._preview_pause.is_set():
            hint = tr("preview.hint.paused", total=total)
        elif self._streaming and not self._collection_finished:
            hint = tr("preview.hint.streaming", total=total)
        else:
            hint = tr("preview.hint.found", total=total)
        dup_count = count_content_duplicates(self._items)
        if dup_count:
            hint += "  " + tr("preview.hint.duplicates", n=dup_count)
        if self._full_preview_loader is not None:
            hint += " " + tr("preview.hint.dblclick")
        if self._filter_mode != "all":
            hint += "  " + tr("preview.hint.visible", visible=visible, total=total)
        hint += self._selection_hint()
        self._header.setText(hint)
        self._update_selection_ui()

    def _selection_hint(self) -> str:
        visible = self._visible_items()
        selected, total, visible_selected = selection_summary(self._items, visible)
        if total <= 0:
            return ""
        parts = [tr("preview.selected.total", sel=selected, total=total)]
        if len(visible) != total or self._channel_pick or self._filter_mode != "all":
            parts.append(tr("preview.selected.visible", sel=visible_selected, n=len(visible)))
        return "".join(parts)

    def _update_selection_ui(self) -> None:
        if not hasattr(self, "_selection_label") or not hasattr(self, "_download_btn"):
            return
        visible = self._visible_items()
        selected, total, visible_selected = selection_summary(self._items, visible)
        page_items = self._page_items(self._current_page)
        selected_on_page = sum(1 for item in page_items if item.selected)
        if hasattr(self, "_page_info_label"):
            self._page_info_label.setText(
                tr("preview.selected.page", on_page=len(page_items), sel=selected_on_page),
            )
        self._selection_label.setText(
            tr(
                "preview.selected.full",
                sel=selected,
                total=total,
                vis_sel=visible_selected,
                vis=len(visible),
            ),
        )
        channel = self._active_channel_name()
        self._select_channel_btn.setEnabled(bool(channel))
        if channel:
            self._select_channel_btn.setText(tr("preview.select.channel_named", channel=channel))
        else:
            self._select_channel_btn.setText(tr("preview.select.channel"))
        self._download_btn.setText(
            tr("preview.download_count", n=selected) if selected else tr("preview.download"),
        )
        self._download_btn.setEnabled(selected > 0)

    def _active_channel_name(self) -> str:
        channel = self._channel_pick.strip()
        if channel:
            return channel
        idx = self._channel_combo.currentIndex()
        if idx > 0:
            return str(self._channel_combo.itemData(idx) or "").strip()
        return ""

    def _open_full_preview(self, card: _Card) -> None:
        item = card.item
        if item is None or item.kind != "photo" or self._full_preview_loader is None:
            return
        show_preview_image_viewer(
            self,
            item,
            loader=self._full_preview_loader,
            initial_path=item.full_preview_path,
            placeholder_path=item.preview_path,
        )

    def _set_preview_paused(self, paused: bool) -> None:
        if paused:
            self._preview_pause.set()
        else:
            self._preview_pause.clear()
        self._sync_pause_button_label()
        self._update_header()

    def _on_pause_clicked(self) -> None:
        self._set_preview_paused(not self._preview_pause.is_set())

    def _schedule_header_update(self) -> None:
        if not self._header_update_timer.isActive():
            self._header_update_timer.start()

    def _invalidate_view_cache(self) -> None:
        self._view_cache_key = None
        self._view_cache_items = None

    def _maybe_refresh_channels(self, items: list[PreviewItem]) -> None:
        new_channels = {item.channel.strip() for item in items if item.channel.strip()}
        if not new_channels - self._known_channels:
            return
        self._known_channels |= new_channels
        if self._streaming and not self._collection_finished:
            return
        self._channel_refresh_timer.start()

    def _visible_items(self) -> list[PreviewItem]:
        cache_key = (self._filter_mode, self._channel_pick, self._sort_mode)
        cached = self._view_cache_items
        if cached is not None and self._view_cache_key == cache_key:
            return cached
        started = time.perf_counter()
        visible = apply_preview_view(
            self._items,
            mode=self._filter_mode,
            channel=self._channel_pick,
            sort=self._sort_mode,
        )
        self._view_cache_key = cache_key
        self._view_cache_items = visible
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf preview.visible_items: %.1fms (mode=%s, channel=%s, sort=%s, total=%s, visible=%s)",
                (time.perf_counter() - started) * 1000.0,
                self._filter_mode,
                self._channel_pick or "*",
                self._sort_mode,
                len(self._items),
                len(visible),
            )
        return visible

    def _refresh_channel_combo(self) -> None:
        if self._closed:
            return
        self._channel_combo.hidePopup()
        channels = preview_channels(self._items)
        current = self._channel_pick
        existing: dict[str, int] = {}
        for index in range(self._channel_combo.count()):
            existing[str(self._channel_combo.itemData(index) or "")] = index

        self._channel_combo.blockSignals(True)
        if "" not in existing:
            self._channel_combo.insertItem(0, tr("preview.filter.all_channels"), "")
            existing = {str(self._channel_combo.itemData(i) or ""): i for i in range(self._channel_combo.count())}

        for channel in channels:
            if channel not in existing:
                self._channel_combo.addItem(channel, channel)

        stale = [
            data
            for data in existing
            if data and data not in channels
        ]
        for data in stale:
            index = existing.get(data)
            if index is not None:
                self._channel_combo.removeItem(index)
                existing = {str(self._channel_combo.itemData(i) or ""): i for i in range(self._channel_combo.count())}

        if current:
            idx = self._channel_combo.findData(current)
            if idx >= 0:
                self._channel_combo.setCurrentIndex(idx)
            else:
                self._channel_pick = ""
        self._channel_combo.blockSignals(False)
        self._update_selection_ui()

    def _remember_page_for_channel(self, page: int | None = None) -> None:
        self._page_by_channel[self._channel_pick] = (
            self._current_page if page is None else page
        )

    def _on_channel_changed(self, index: int) -> None:
        if index < 0:
            return
        self._remember_page_for_channel()
        new_channel = str(self._channel_combo.itemData(index) or "")
        self._channel_pick = new_channel
        self._invalidate_view_cache()
        self._recalc_pages()
        saved = self._page_by_channel.get(new_channel, 0)
        self._current_page = min(saved, max(0, self._total_pages - 1))
        self._update_header()
        self._render_page(self._current_page)

    def _on_sort_changed(self, index: int) -> None:
        if index < 0 or index >= len(PREVIEW_SORT_MODES):
            return
        self._sort_mode = PREVIEW_SORT_MODES[index]
        self._invalidate_view_cache()
        self._recalc_pages()
        self._current_page = min(self._current_page, self._total_pages - 1)
        self._update_header()
        self._render_page(self._current_page, scroll_to_top=True)

    def _after_selection_change(self) -> None:
        self._invalidate_view_cache()
        self._recalc_pages()
        self._update_header()
        self._render_page(min(self._current_page, self._total_pages - 1))

    def _select_current_channel(self) -> None:
        channel = self._active_channel_name()
        if not channel:
            return
        for item in self._items:
            if item.channel == channel:
                item.selected = True
        self._after_selection_change()

    def _deselect_on_disk(self) -> None:
        for item in self._items:
            if item.disk_status == "complete":
                item.selected = False
        self._after_selection_change()

    def _open_in_telegram(self, item: PreviewItem) -> None:
        url = build_message_link(item.message, item.channel)
        if not url:
            return
        QDesktopServices.openUrl(QUrl(url))

    def _recalc_pages(self) -> int:
        self._total_pages = max(1, (len(self._visible_items()) + PAGE_SIZE - 1) // PAGE_SIZE)
        return self._total_pages

    def _maybe_refresh_filter_view(self) -> None:
        if self._filter_mode not in {"selected", "unselected"}:
            return
        self._recalc_pages()
        self._current_page = min(self._current_page, self._total_pages - 1)
        self._update_header()
        self._render_page(self._current_page)

    def _on_filter_changed(self, index: int) -> None:
        if index < 0 or index >= len(PREVIEW_FILTER_MODES):
            return
        self._filter_mode = PREVIEW_FILTER_MODES[index]
        self._invalidate_view_cache()
        self._recalc_pages()
        self._current_page = min(self._current_page, self._total_pages - 1)
        self._update_header()
        self._render_page(self._current_page, scroll_to_top=True)

    def _page_items(self, page: int) -> list[PreviewItem]:
        items = self._visible_items()
        start = page * PAGE_SIZE
        return items[start : start + PAGE_SIZE]

    def _scroll_to_top(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.minimum())

    def _render_page(self, page: int, *, scroll_to_top: bool = False) -> None:
        self._current_page = page
        self._remember_page_for_channel(page)
        page_items = self._page_items(page)
        self._scroll.setUpdatesEnabled(False)
        try:
            if page_items:
                self._cards = self._page_view.show_items(page_items)
            else:
                self._page_view.hide_all()
                self._cards = []
        finally:
            self._scroll.setUpdatesEnabled(True)
        self._update_pager_labels()
        if scroll_to_top:
            QTimer.singleShot(0, self._scroll_to_top)

    def _update_pager_labels(self) -> None:
        self._prev_btn.setEnabled(self._current_page > 0)
        self._next_btn.setEnabled(self._current_page + 1 < self._total_pages)
        total_pages = max(1, self._total_pages)
        current_page = min(self._current_page + 1, total_pages)
        self._page_label.setText(
            tr("preview.nav.page", cur=current_page, total=total_pages),
        )
        validator = self._page_jump_entry.validator()
        if isinstance(validator, QIntValidator):
            validator.setRange(1, total_pages)
        self._page_jump_entry.setText(str(current_page))
        can_jump = total_pages > 1
        self._page_jump_entry.setEnabled(can_jump)
        self._page_go_btn.setEnabled(can_jump)
        self._update_selection_ui()

    def _go_to_page(self) -> None:
        raw = self._page_jump_entry.text().strip()
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError:
            return
        page = max(0, value - 1)
        if page == self._current_page:
            return
        if page >= self._total_pages:
            return
        self._render_page(page, scroll_to_top=True)

    def _restore_scroll_after_append(self, *, old_scroll: int, stick_to_bottom: bool) -> None:
        bar = self._scroll.verticalScrollBar()

        def restore() -> None:
            if stick_to_bottom:
                bar.setValue(bar.maximum())
            else:
                bar.setValue(min(old_scroll, bar.maximum()))

        QTimer.singleShot(0, restore)

    def append_item(self, item: PreviewItem) -> None:
        self._append_items([item])

    def _append_items(self, items: list[PreviewItem]) -> None:
        if self._closed or not items:
            return
        if self._streaming and not self._collection_finished:
            self._pending_stream_items.extend(items)
            if not self._stream_flush_timer.isActive():
                self._stream_flush_timer.start()
            return
        self._apply_appended_items(items)

    def _flush_stream_appends(self) -> None:
        if self._closed or not self._pending_stream_items:
            return
        batch = self._pending_stream_items
        self._pending_stream_items = []
        self._apply_appended_items(batch)

    def _can_incremental_append(self, old_visible: list[PreviewItem], new_visible: list[PreviewItem]) -> bool:
        if len(new_visible) < len(old_visible):
            return False
        return new_visible[: len(old_visible)] == old_visible

    def _incremental_page_extend(
        self,
        old_page_items: list[PreviewItem],
        new_page_items: list[PreviewItem],
    ) -> None:
        if len(new_page_items) <= len(old_page_items):
            self._update_pager_labels()
            return
        self._scroll.setUpdatesEnabled(False)
        try:
            self._cards = self._page_view.show_items(new_page_items)
        finally:
            self._scroll.setUpdatesEnabled(True)
        self._update_pager_labels()

    def _apply_appended_items(self, items: list[PreviewItem]) -> None:
        if self._closed or not items:
            return
        started = time.perf_counter()
        old_visible = self._visible_items()
        old_page_items = self._page_items(self._current_page)
        old_pages = self._total_pages
        on_tail_page = self._current_page >= old_pages - 1

        self._items.extend(items)
        for item in items:
            self._duplicate_tracker.annotate(item)
        self._maybe_refresh_channels(items)
        self._invalidate_view_cache()
        self._recalc_pages()

        if self._streaming and not self._collection_finished:
            self._schedule_header_update()
        else:
            self._update_header()

        new_visible = self._visible_items()
        new_page_items = self._page_items(self._current_page)

        if not on_tail_page:
            self._update_pager_labels()
            return

        bar = self._scroll.verticalScrollBar()
        old_scroll = bar.value()
        old_max = bar.maximum()
        stick_to_bottom = old_max <= 0 or old_scroll >= old_max - 4

        if self._can_incremental_append(old_visible, new_visible):
            self._incremental_page_extend(old_page_items, new_page_items)
        else:
            self._scroll.setUpdatesEnabled(False)
            try:
                self._render_page(self._current_page)
            finally:
                self._scroll.setUpdatesEnabled(True)

        self._restore_scroll_after_append(old_scroll=old_scroll, stick_to_bottom=stick_to_bottom)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "perf preview.apply_appended_items: %.1fms (batch=%s, total=%s, visible=%s, pages=%s)",
                (time.perf_counter() - started) * 1000.0,
                len(items),
                len(self._items),
                len(new_visible),
                self._total_pages,
            )

    def _allocate_card(self) -> _Card:
        frame = QFrame()
        frame.setObjectName("previewCard")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)

        thumb_box = QFrame()
        thumb_box.setObjectName("thumbBox")
        thumb_box.setFixedHeight(PREVIEW_THUMB_SIZE)
        thumb_layout = QVBoxLayout(thumb_box)
        thumb_layout.setContentsMargins(4, 4, 4, 4)
        thumb_label = _ThumbLabel()
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        thumb_layout.addWidget(thumb_label)
        layout.addWidget(thumb_box)

        row = QHBoxLayout()
        status_label = QLabel()
        status_label.setObjectName("diskBadge")
        row.addWidget(status_label)
        duplicate_label = QLabel()
        duplicate_label.setObjectName("duplicateBadge")
        row.addWidget(duplicate_label)
        checkbox = QCheckBox(tr("preview.card.download"))
        row.addWidget(checkbox)
        telegram_btn = QPushButton("TG")
        telegram_btn.setFixedWidth(40)
        telegram_btn.setToolTip(tr("preview.card.telegram"))
        row.addWidget(telegram_btn)
        layout.addLayout(row)

        meta_label = QLabel()
        meta_label.setObjectName("muted")
        meta_label.setWordWrap(True)
        layout.addWidget(meta_label)

        summary_label = QLabel()
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        card = _Card(
            item=None,
            frame=frame,
            checkbox=checkbox,
            thumb_label=thumb_label,
            status_label=status_label,
            duplicate_label=duplicate_label,
            meta_label=meta_label,
            summary_label=summary_label,
            telegram_btn=telegram_btn,
        )

        def toggle() -> None:
            if card.item is None:
                return
            card.item.selected = not card.item.selected
            self._refresh_card(card)
            self._maybe_refresh_filter_view()
            self._update_selection_ui()

        def on_check(_state: int) -> None:
            if card.item is None:
                return
            card.item.selected = checkbox.isChecked()
            self._refresh_card(card)
            self._maybe_refresh_filter_view()
            self._update_selection_ui()

        checkbox.stateChanged.connect(on_check)
        thumb_box.mousePressEvent = lambda _e: toggle()  # type: ignore[method-assign]
        thumb_label.on_single_click = toggle
        thumb_label.on_double_click = lambda: self._open_full_preview(card)
        meta_label.mousePressEvent = lambda _e: toggle()  # type: ignore[method-assign]
        summary_label.mousePressEvent = lambda _e: toggle()  # type: ignore[method-assign]
        telegram_btn.clicked.connect(
            lambda _checked=False, c=card: self._open_in_telegram(c.item) if c.item is not None else None,
        )
        return card

    def _bind_card(self, card: _Card, item: PreviewItem) -> None:
        card.item = item
        self._card_by_item_id[id(item)] = card
        if item.kind == "photo" and self._full_preview_loader is not None:
            card.thumb_label.setToolTip(tr("preview.card.thumb_tip"))
        else:
            card.thumb_label.setToolTip("")
        meta_parts = [format_message_local_datetime(item.message), item.channel]
        if item.hashtag:
            meta_parts.insert(0, f"#{item.hashtag}")
        card.meta_label.setText(" · ".join(meta_parts))
        card.summary_label.setText(item.summary)
        self._update_thumb(card)
        self._refresh_card(card)

    def _refresh_card(self, card: _Card) -> None:
        if card.item is None:
            return
        card.checkbox.blockSignals(True)
        card.checkbox.setChecked(card.item.selected)
        card.checkbox.blockSignals(False)
        badge = disk_status_badge(card.item)
        card.status_label.setText(badge)
        card.status_label.setVisible(bool(badge))
        dup_badge = content_duplicate_badge(card.item)
        card.duplicate_label.setText(dup_badge)
        card.duplicate_label.setVisible(bool(dup_badge))
        if is_content_duplicate(card.item):
            card.duplicate_label.setToolTip(
                tr("preview.card.duplicate", id=card.item.duplicate_of_message_id),
            )
        else:
            card.duplicate_label.setToolTip("")
        link = build_message_link(card.item.message, card.item.channel)
        if card.telegram_btn is not None:
            card.telegram_btn.setEnabled(bool(link))
        if (
            card.frame.property("selected") != card.item.selected
            or card.frame.property("diskStatus") != card.item.disk_status
        ):
            card.frame.setProperty("selected", card.item.selected)
            card.frame.setProperty("diskStatus", card.item.disk_status)
            card.frame.update()

    def _update_thumb(self, card: _Card) -> None:
        item = card.item
        if item is None:
            return
        if item.preview_path:
            pixmap = _load_thumb_pixmap(str(item.preview_path))
            if pixmap is not None and not pixmap.isNull():
                card.thumb_label.setText("")
                card.thumb_label.setPixmap(pixmap)
                return
        labels = kind_labels()
        kind_label = labels.get(item.kind, item.kind)
        card.thumb_label.setPixmap(QPixmap())
        card.thumb_label.setText(kind_label)

    def _change_page(self, delta: int) -> None:
        new_page = self._current_page + delta
        if 0 <= new_page < self._total_pages:
            self._render_page(new_page, scroll_to_top=True)

    def _select_all_visible(self) -> None:
        set_items_selection(self._visible_items(), selected=True)
        self._after_selection_change()

    def _select_none_visible(self) -> None:
        set_items_selection(self._visible_items(), selected=False)
        self._after_selection_change()

    def _release_ui_memory(self) -> None:
        self._page_view.dispose()
        self._cards.clear()
        self._card_by_item_id.clear()
        for item in self._items:
            item.preview_path = None
        self._items.clear()

    def _close_with(
        self,
        selected: list[PreviewItem] | None,
        *,
        action: Literal["download", "skip_batch", "stop"] = "stop",
    ) -> None:
        if self._closed:
            return
        self._closed = True
        if self._on_closing is not None:
            self._on_closing()
        self._preview_pause.clear()
        if hasattr(self, "_channel_refresh_timer"):
            self._channel_refresh_timer.stop()
        if hasattr(self, "_stream_flush_timer"):
            self._stream_flush_timer.stop()
        if hasattr(self, "_header_update_timer"):
            self._header_update_timer.stop()
        self._pending_stream_items.clear()
        if hasattr(self, "_pause_btn"):
            self._sync_pause_button_label()
        self._result = selected
        self._result_action = action
        self._ui_sync_timer.stop()
        self._release_ui_memory()
        self.accept()
        self.deleteLater()

    def _on_download(self) -> None:
        self._close_with(
            [item for item in self._items if item.selected],
            action="download",
        )

    def _on_cancel(self) -> None:
        self._close_with(None, action="stop")

    def _on_skip_batch(self) -> None:
        self._close_with([], action="skip_batch")

    def _on_stop_all(self) -> None:
        self._close_with(None, action="stop")

    def result_items(self) -> list[PreviewItem] | None:
        return self._result

    def result_action(self) -> Literal["download", "skip_batch", "stop"]:
        return self._result_action

    def closeEvent(self, event) -> None:
        if not self._closed:
            self._on_cancel()
        super().closeEvent(event)


def show_preview_dialog(
    parent: QWidget,
    items: list[PreviewItem],
    *,
    streaming: bool = False,
    item_queue: queue.Queue | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_closing: Callable[[], None] | None = None,
    thumb_queue: queue.Queue[PreviewItem] | None = None,
    on_ready: Callable[[], None] | None = None,
    preview_pause: threading.Event | None = None,
    sequential_batch: SequentialBatchInfo | None = None,
    full_preview_loader: FullPreviewLoader | None = None,
) -> list[PreviewItem] | None | PreviewDialogResult:
    if not items and not streaming:
        if sequential_batch is not None:
            return PreviewDialogResult(action="stop", items=[])
        return []
    dialog = PreviewDialog(
        parent,
        items,
        streaming=streaming,
        item_queue=item_queue,
        should_cancel=should_cancel,
        on_closing=on_closing,
        thumb_queue=thumb_queue,
        on_ready=on_ready,
        preview_pause=preview_pause,
        sequential_batch=sequential_batch,
        full_preview_loader=full_preview_loader,
    )
    previous_dialog = getattr(parent, "_active_preview_dialog", None)
    setattr(parent, "_active_preview_dialog", dialog)
    dialog.resize(900, 700)
    center_dialog_on_parent(dialog, parent)
    try:
        dialog.exec()
        if sequential_batch is not None:
            return PreviewDialogResult(
                action=dialog.result_action(),
                items=dialog.result_items() or [],
            )
        return dialog.result_items()
    finally:
        setattr(parent, "_active_preview_dialog", previous_dialog)