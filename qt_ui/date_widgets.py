"""Date widgets · Виджеты даты"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QDateEdit, QHBoxLayout, QPushButton, QWidget

from app.i18n import tr
from app.tg_hashtag_dl import parse_date_filter


class OptionalDatePicker(QWidget):
    """Optional date picker · Поле даты с календарём"""

    date_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._loading = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._placeholder_btn = QPushButton(tr("date.not_selected"))
        self._placeholder_btn.setFlat(True)
        self._placeholder_btn.setToolTip(tr("date.placeholder_tip"))
        self._placeholder_btn.clicked.connect(self._open_picker)
        layout.addWidget(self._placeholder_btn)

        self._picker = QDateEdit()
        self._picker.setCalendarPopup(True)
        self._picker.setDisplayFormat("dd.MM.yyyy")
        self._picker.dateChanged.connect(self._on_date_changed)
        self._picker.calendarWidget().clicked.connect(self._on_calendar_picked)
        self._picker.hide()
        layout.addWidget(self._picker)

        self._clear_btn = QPushButton("✕")
        self._clear_btn.setFixedWidth(36)
        self._clear_btn.clicked.connect(self.clear)
        layout.addWidget(self._clear_btn)

    def retranslate_ui(self) -> None:
        if not self._active:
            self._placeholder_btn.setText(tr("date.not_selected"))
        self._placeholder_btn.setToolTip(tr("date.placeholder_tip"))

    def _open_picker(self) -> None:
        """Open date picker · Календарь без включения фильтра"""
        self._placeholder_btn.hide()
        self._picker.show()
        self._set_picker_date(date.today())

    def _set_picker_date(self, value: date) -> None:
        from PySide6.QtCore import QDate

        self._loading = True
        self._picker.setDate(QDate(value.year, value.month, value.day))
        self._loading = False

    def _on_calendar_picked(self, qdate) -> None:
        self._active = True
        self._placeholder_btn.hide()
        self._picker.show()
        self._set_picker_date(qdate.toPython())
        self.date_changed.emit()

    def _on_date_changed(self) -> None:
        if self._loading:
            return
        self._active = True
        self._placeholder_btn.hide()
        self._picker.show()
        self.date_changed.emit()

    def activate(self, value: date | None = None) -> None:
        self._active = True
        self._placeholder_btn.hide()
        self._picker.show()
        self._set_picker_date(value or date.today())

    def is_active(self) -> bool:
        return self._active

    def display_text(self) -> str:
        if not self._active:
            return ""
        qd = self._picker.date()
        return qd.toString("dd.MM.yyyy")

    def clear(self) -> None:
        self._active = False
        self._picker.hide()
        self._placeholder_btn.show()
        self.date_changed.emit()

    def get(self) -> str:
        if not self._active:
            return ""
        qd = self._picker.date()
        return f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"

    def set_value(self, value: str) -> None:
        raw = value.strip()
        if not raw:
            self.clear()
            return
        try:
            parsed = parse_date_filter(raw)
        except ValueError:
            self.clear()
            return
        self.activate(parsed)

    def set_enabled(self, enabled: bool) -> None:
        self._placeholder_btn.setEnabled(enabled)
        self._picker.setEnabled(enabled)
        self._clear_btn.setEnabled(enabled)

    def apply_calendar_theme(self, palette: dict[str, str]) -> None:
        cal = self._picker.calendarWidget()
        p = palette
        cal.setStyleSheet(
            f"""
            QCalendarWidget {{
                background-color: {p["calendar_bg"]};
            }}
            QCalendarWidget QTableView {{
                background-color: {p["text_bg"]};
                alternate-background-color: {p["calendar_bg"]};
                color: {p["text_fg"]};
                gridline-color: {p["border"]};
            }}
            QCalendarWidget QHeaderView::section {{
                background-color: {p["calendar_headers"]};
                color: {p["calendar_fg"]};
                padding: 5px 2px;
                border: none;
                font-weight: bold;
            }}
            QCalendarWidget QToolButton {{
                background-color: {p["calendar_headers"]};
                color: {p["calendar_fg"]};
                border: none;
                border-radius: 3px;
                margin: 2px;
            }}
            QCalendarWidget QAbstractItemView:enabled {{
                color: {p["text_fg"]};
                background-color: {p["text_bg"]};
                selection-background-color: {p["calendar_select"]};
                selection-color: {p["text_fg"]};
            }}
            QCalendarWidget QSpinBox {{
                background-color: {p["text_bg"]};
                color: {p["text_fg"]};
            }}
            """,
        )
        pal = cal.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(p["calendar_bg"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(p["calendar_fg"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(p["text_bg"]))
        pal.setColor(QPalette.ColorRole.Text, QColor(p["text_fg"]))
        pal.setColor(QPalette.ColorRole.Button, QColor(p["calendar_headers"]))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(p["calendar_fg"]))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(p["calendar_select"]))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p["text_fg"]))
        cal.setPalette(pal)
        cal.setAutoFillBackground(True)