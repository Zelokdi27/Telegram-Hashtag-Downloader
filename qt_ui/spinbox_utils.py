"""Spinbox utils · Спинбоксы без колёсика мыши"""

from __future__ import annotations

from PySide6.QtWidgets import QSpinBox


class NoWheelSpinBox(QSpinBox):
    """No wheel spinbox · QSpinBox без колёсика"""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()