"""Collapsible sections · Сворачиваемые секции"""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget


class CollapsibleGroupBox(QGroupBox):
    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        start_collapsed: bool = False,
        tool_tip: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("collapsibleGroup")
        self._base_title = title
        self.setCheckable(True)
        self.setFlat(False)
        if tool_tip:
            self.setToolTip(tool_tip)

        self._body = QWidget(self)
        self._content_layout = QVBoxLayout(self._body)
        self._content_layout.setContentsMargins(0, 0, 0, 0)

        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(12, 0, 12, 12)
        wrapper.addWidget(self._body)

        expanded = not start_collapsed
        self.blockSignals(True)
        self.setChecked(expanded)
        self.blockSignals(False)
        self._body.setVisible(expanded)
        self._refresh_title(expanded)
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, expanded: bool) -> None:
        self._refresh_title(expanded)
        self._body.setVisible(expanded)
        self.updateGeometry()

    def _refresh_title(self, expanded: bool) -> None:
        marker = "▼" if expanded else "▶"
        self.setTitle(f"{marker} {self._base_title}")

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout