"""Hashtag history UI tests · Ширина popup автодополнения хештегов"""

from types import SimpleNamespace

from qt_ui.hashtag_history_ui import measure_completer_popup_width


class _FakeFontMetrics:
    def __init__(self) -> None:
        self._widths = {
            "short": 48,
            "much_longer_hashtag": 156,
            "very_long_hashtag_name_here": 220,
        }
        self._avg_char = 8

    def boundingRect(self, text: str) -> SimpleNamespace:
        return SimpleNamespace(width=self._widths.get(text, len(text) * self._avg_char))

    def averageCharWidth(self) -> int:
        return self._avg_char


def test_measure_popup_width_from_text_and_entry_cap():
    fm = _FakeFontMetrics()
    tags = ["short", "much_longer_hashtag"]
    entry_width = fm.boundingRect("much_longer_hashtag").width + fm.averageCharWidth() * 4

    width = measure_completer_popup_width(
        tags=tags,
        font_metrics=fm,
        entry_width=entry_width,
    )
    assert width <= entry_width
    assert width > fm.boundingRect("short").width


def test_measure_popup_width_respects_narrow_entry():
    fm = _FakeFontMetrics()
    tags = ["very_long_hashtag_name_here"]
    narrow_entry = fm.averageCharWidth() * 12

    width = measure_completer_popup_width(
        tags=tags,
        font_metrics=fm,
        entry_width=narrow_entry,
    )
    assert width == narrow_entry