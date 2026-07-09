"""Win chrome · Оформление Windows"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

_titlebar_dark = False
_win_dark_app_configured = False
_titlebar_generation = 0

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_WM_NCACTIVATE = 0x0086
_RDW_INVALIDATE = 0x0001
_RDW_FRAME = 0x0400
_RDW_UPDATENOW = 0x0100
_GA_ROOT = 2


def _set_app_color_scheme(dark: bool) -> None:
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)


def configure_windows_app_dark_mode(*, dark: bool) -> None:
    """App dark mode · SetPreferredAppMode Win10+"""
    global _win_dark_app_configured
    if sys.platform != "win32":
        return
    try:
        import ctypes

        uxtheme = ctypes.windll.uxtheme
        try:
            allow_app = uxtheme.AllowDarkModeForApp
            allow_app.argtypes = [ctypes.c_int]
            allow_app.restype = ctypes.c_int
            allow_app(1 if dark else 0)
        except AttributeError:
            pass
        mode = 1 if dark else 3
        try:
            set_preferred = uxtheme.SetPreferredAppMode
            set_preferred.argtypes = [ctypes.c_int]
            set_preferred.restype = ctypes.c_int
            set_preferred(mode)
        except AttributeError:
            pass
        try:
            uxtheme.FlushMenuThemes()
        except Exception:
            pass
        _win_dark_app_configured = True
    except Exception:
        pass


def _titlebar_eligible(window: QWidget) -> bool:
    if not window.isWindow():
        return False
    flags = window.windowFlags()
    if flags & (Qt.WindowType.Popup | Qt.WindowType.Tool | Qt.WindowType.ToolTip | Qt.WindowType.SplashScreen):
        return False
    return bool(flags & Qt.WindowType.WindowTitleHint)


def _window_hwnd(window: QWidget) -> int:
    if not window.isVisible():
        return 0
    handle = window.windowHandle()
    if handle is not None:
        hwnd = int(handle.winId())
        if hwnd:
            return hwnd
    hwnd = int(window.winId())
    if hwnd == 0 or sys.platform != "win32":
        return hwnd
    try:
        import ctypes

        root = ctypes.windll.user32.GetAncestor(hwnd, _GA_ROOT)
        if root:
            return int(root)
    except Exception:
        pass
    return hwnd


def _apply_windows_dark_titlebar(hwnd: int, *, dark: bool) -> None:
    import ctypes
    from ctypes import wintypes

    if hwnd == 0:
        return

    value = ctypes.c_int(1 if dark else 0)
    dwmapi = ctypes.windll.dwmapi

    try:
        uxtheme = ctypes.windll.uxtheme
        allow_dark = uxtheme.AllowDarkModeForWindow
        allow_dark.argtypes = [wintypes.HWND, wintypes.BOOL]
        allow_dark.restype = wintypes.BOOL
        allow_dark(hwnd, dark)
    except (AttributeError, OSError):
        pass

    if not dark:
        try:
            uxtheme = ctypes.windll.uxtheme
            set_theme = uxtheme.SetWindowTheme
            set_theme.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
            set_theme.restype = wintypes.HRESULT
            set_theme(hwnd, "Explorer", None)
        except (AttributeError, OSError):
            pass

    for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        try:
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                attr,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass

    _repaint_caption(hwnd)


def _repaint_caption(hwnd: int) -> None:
    """Caption repaint · Перерисовка шапки без мерцания"""
    if sys.platform != "win32" or hwnd == 0:
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
        )
        user32.RedrawWindow(
            hwnd,
            None,
            None,
            _RDW_FRAME | _RDW_INVALIDATE | _RDW_UPDATENOW,
        )
        try:
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass
    except Exception:
        pass


def restore_top_level_window(window: QWidget) -> None:
    """Restore window · Развернуть и активировать окно"""
    if window.isMinimized() or not window.isVisible():
        window.setWindowState(
            (window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive,
        )
        window.showNormal()
    window.show()
    # Delayed activation · Отложенная активация из трея
    QTimer.singleShot(0, window.raise_)
    QTimer.singleShot(0, window.activateWindow)


def prepare_native_top_level_window(window: QWidget) -> None:
    window.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    window.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)


def apply_dark_titlebar(window: QWidget, *, dark: bool | None = None) -> bool:
    if not _titlebar_eligible(window):
        return False
    use_dark = _titlebar_dark if dark is None else dark
    if sys.platform != "win32":
        return True
    try:
        hwnd = _window_hwnd(window)
        if hwnd == 0:
            return False
        if use_dark and not _win_dark_app_configured:
            configure_windows_app_dark_mode(dark=True)
        _apply_windows_dark_titlebar(hwnd, dark=use_dark)
        return True
    except Exception:
        return False


def set_titlebar_dark(dark: bool) -> None:
    global _titlebar_dark, _titlebar_generation
    _titlebar_dark = dark
    _titlebar_generation += 1
    generation = _titlebar_generation
    _set_app_color_scheme(dark)
    configure_windows_app_dark_mode(dark=dark)

    def _apply_all() -> None:
        if generation != _titlebar_generation:
            return
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            if isinstance(widget, QWidget) and widget.isWindow() and _titlebar_eligible(widget):
                apply_dark_titlebar(widget)

    _apply_all()
    QTimer.singleShot(50, _apply_all)


def titlebar_dark_enabled() -> bool:
    return _titlebar_dark


def prepare_dialog_window(dialog: QWidget) -> None:
    flags = (
        Qt.WindowType.Window
        | Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowCloseButtonHint
        | Qt.WindowType.WindowMinimizeButtonHint
    )
    dialog.setWindowFlags(flags)
    if hasattr(dialog, "setModal"):
        dialog.setModal(True)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)


def apply_window_theme(window: QWidget, parent: QWidget | None = None) -> None:
    if parent is not None:
        sheet = parent.styleSheet()
        if sheet:
            window.setStyleSheet(sheet)
            return
    if _titlebar_dark:
        from .theme import build_stylesheet

        window.setStyleSheet(build_stylesheet(dark=True))


def prepare_qr_window(dialog: QWidget) -> None:
    flags = (
        Qt.WindowType.Window
        | Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowCloseButtonHint
        | Qt.WindowType.WindowStaysOnTopHint
    )
    dialog.setWindowFlags(flags)
    if hasattr(dialog, "setModal"):
        dialog.setModal(False)
    dialog.setWindowModality(Qt.WindowModality.NonModal)


def _center_on_anchor(window: QWidget, anchor: QWidget) -> None:
    if anchor.isMinimized():
        anchor.showNormal()
    window.adjustSize()
    anchor_geo = anchor.frameGeometry()
    size = window.size()
    x = anchor_geo.x() + (anchor_geo.width() - size.width()) // 2
    y = anchor_geo.y() + (anchor_geo.height() - size.height()) // 2
    window.move(max(anchor_geo.x(), x), max(anchor_geo.y(), y))


def present_qr_window(window: QWidget, anchor: QWidget | None = None) -> None:
    prepare_qr_window(window)
    apply_window_theme(window, anchor)
    if anchor is not None:
        _center_on_anchor(window, anchor)
    window.show()
    window.raise_()
    window.activateWindow()
    apply_dark_titlebar(window)


def center_dialog_on_parent(dialog: QWidget, parent: QWidget | None) -> None:
    if parent is None:
        return
    if parent.isMinimized():
        parent.showNormal()
    anchor = parent.frameGeometry()
    size = dialog.frameGeometry().size()
    dialog.move(
        anchor.x() + max(0, (anchor.width() - size.width()) // 2),
        anchor.y() + max(0, (anchor.height() - size.height()) // 2),
    )


def present_top_level_window(window: QWidget, parent: QWidget | None = None) -> None:
    prepare_dialog_window(window)
    apply_window_theme(window, parent)
    if parent is not None and parent.isMinimized():
        parent.showNormal()
    window.raise_()
    window.activateWindow()


def schedule_dark_titlebar(
    window: QWidget,
    *,
    generation: int | None = None,
    delays_ms: tuple[int, ...] = (50,),
) -> None:
    use_generation = _titlebar_generation if generation is None else generation

    def _deferred() -> None:
        if use_generation != _titlebar_generation:
            return
        apply_dark_titlebar(window)

    for delay in delays_ms:
        QTimer.singleShot(delay, _deferred)


class TitlebarThemeFilter(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if not isinstance(obj, QWidget) or not _titlebar_eligible(obj):
            return False
        if event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            apply_dark_titlebar(obj)
        return False


def install_titlebar_filter(app: QApplication) -> TitlebarThemeFilter:
    filt = TitlebarThemeFilter(app)
    app.installEventFilter(filt)
    return filt