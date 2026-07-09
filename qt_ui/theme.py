"""Qt theme · Тема Qt"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QLabel

from app.theme import DARK, LIGHT


def palette_for(*, dark: bool) -> dict[str, str]:
    return dict(DARK if dark else LIGHT)


def style_link_label(label: QLabel, *, dark: bool) -> None:
    """Link label style · Accent ссылок в QLabel явно"""
    accent = QColor(palette_for(dark=dark)["accent"])
    pal = label.palette()
    pal.setColor(QPalette.ColorRole.Link, accent)
    pal.setColor(QPalette.ColorRole.LinkVisited, accent)
    label.setPalette(pal)


def build_hashtag_completer_popup_stylesheet(*, dark: bool) -> str:
    p = palette_for(dark=dark)
    selected_fg = "#1a1a1a" if dark else "#ffffff"
    return f"""
    QListView#hashtagCompleterPopup {{
        background-color: {p["text_bg"]};
        color: {p["text_fg"]};
        border: 1px solid {p["border"]};
        border-radius: 0.25em;
        padding: 0.1em 0;
        outline: none;
    }}
    QListView#hashtagCompleterPopup::item {{
        padding: 0.4em 0.85em;
    }}
    QListView#hashtagCompleterPopup::item:hover {{
        background-color: {p["panel"]};
    }}
    QListView#hashtagCompleterPopup::item:selected {{
        background-color: {p["accent"]};
        color: {selected_fg};
    }}
    """


def build_stylesheet(*, dark: bool) -> str:
    p = palette_for(dark=dark)
    return f"""
    QMainWindow, QDialog, QWidget {{
        background-color: {p["bg"]};
        color: {p["fg"]};
    }}
    QGroupBox {{
        font-weight: bold;
        border: 1px solid {p["border"]};
        border-radius: 6px;
        margin-top: 10px;
        padding-top: 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}
    QGroupBox#collapsibleGroup {{
        margin-top: 16px;
        padding-top: 12px;
    }}
    QGroupBox#collapsibleGroup::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 4px 8px;
        font-weight: 600;
    }}
    QGroupBox#collapsibleGroup::indicator {{
        width: 0;
        height: 0;
        border: none;
        margin: 0;
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
        background-color: {p["text_bg"]};
        color: {p["text_fg"]};
        border: 1px solid {p["border"]};
        border-radius: 4px;
        padding: 4px 6px;
    }}
    QPushButton {{
        background-color: {p["panel"]};
        color: {p["fg"]};
        border: 1px solid {p["border"]};
        border-radius: 4px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{
        border-color: {p["accent"]};
    }}
    QPushButton:disabled {{
        color: {p["muted"]};
    }}
    QPushButton#danger {{
        background-color: {p["error"]};
        color: white;
        border: none;
    }}
    QPushButton#primary {{
        background-color: {p["accent"]};
        color: white;
        border: none;
    }}
    QCheckBox, QRadioButton, QLabel {{
        color: {p["fg"]};
    }}
    QLabel#muted {{
        color: {p["muted"]};
    }}
    QLabel#accent {{
        color: {p["accent"]};
    }}
    QLabel#error {{
        color: {p["error"]};
    }}
    QLabel#flood_wait {{
        color: {"#9a6700" if not dark else "#e3a008"};
        font-weight: bold;
    }}
    QLabel#pagerInfo {{
        color: {p["muted"]};
        padding: 2px 0 6px 0;
    }}
    QProgressBar {{
        border: 1px solid {p["border"]};
        border-radius: 4px;
        text-align: center;
        background: {p["panel"]};
        color: {p["fg"] if dark else "#141414"};
    }}
    QProgressBar::chunk {{
        background-color: {p["accent"]};
        border-radius: 3px;
    }}
    QDateEdit {{
        background-color: {p["text_bg"]};
        color: {p["text_fg"]};
        border: 1px solid {p["border"]};
        border-radius: 4px;
        padding: 4px 6px;
    }}
    QCalendarWidget {{
        background-color: {p["calendar_bg"]};
    }}
    QCalendarWidget QWidget {{
        background-color: {p["calendar_bg"]};
        color: {p["calendar_fg"]};
    }}
    QCalendarWidget QToolButton {{
        background-color: {p["calendar_headers"]};
        color: {p["calendar_fg"]};
        border: none;
        border-radius: 3px;
        margin: 2px;
        padding: 4px;
    }}
    QCalendarWidget QToolButton:hover {{
        background-color: {p["border"]};
    }}
    QCalendarWidget QSpinBox {{
        background-color: {p["text_bg"]};
        color: {p["text_fg"]};
        selection-background-color: {p["calendar_select"]};
    }}
    QCalendarWidget QAbstractItemView {{
        background-color: {p["text_bg"]};
        color: {p["text_fg"]};
        selection-background-color: {p["calendar_select"]};
        selection-color: {p["text_fg"]};
    }}
    QCalendarWidget QTableView {{
        background-color: {p["text_bg"]};
        alternate-background-color: {p["calendar_bg"]};
        gridline-color: {p["border"]};
    }}
    QCalendarWidget QHeaderView::section {{
        background-color: {p["calendar_headers"]};
        color: {p["calendar_fg"]};
        padding: 5px 2px;
        border: none;
        font-weight: bold;
    }}
    QTabWidget::pane {{
        border: 1px solid {p["border"]};
        border-radius: 4px;
    }}
    QTabBar::tab {{
        background: {p["panel"]};
        border: 1px solid {p["border"]};
        padding: 6px 14px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        border-bottom-color: {p["bg"]};
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QFrame#previewCard {{
        background: {p["panel"]};
        border: 1px solid {p["border"]};
        border-radius: 8px;
    }}
    QFrame#previewCard[selected="true"] {{
        border: 2px solid {p["accent"]};
    }}
    QFrame#previewCard[diskStatus="complete"] {{
        border-color: {"#2da44e" if not dark else "#3dd68c"};
    }}
    QFrame#previewCard[diskStatus="partial"] {{
        border-color: {"#9a6700" if not dark else "#e3a008"};
    }}
    QLabel#diskBadge {{
        color: {"#9a6700" if not dark else "#e3a008"};
        font-size: 11px;
        font-weight: bold;
        padding: 0 4px;
    }}
    QFrame#previewCard[diskStatus="complete"] QLabel#diskBadge {{
        color: {"#2da44e" if not dark else "#3dd68c"};
    }}
    QLabel#duplicateBadge {{
        color: {"#6f42c1" if not dark else "#b197fc"};
        font-size: 11px;
        font-weight: bold;
        padding: 0 4px;
    }}
    QFrame#thumbBox {{
        background: {p["text_bg"]};
        border-radius: 6px;
    }}
    QFrame#crashBanner {{
        background: {p["panel"]};
        border: 1px solid {"#9a6700" if not dark else "#e3a008"};
        border-radius: 8px;
    }}
    """