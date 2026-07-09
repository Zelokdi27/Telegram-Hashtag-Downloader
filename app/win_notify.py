"""Win notify · Toast Windows"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys
import xml.sax.saxutils
from typing import TYPE_CHECKING

from .i18n import tr

if TYPE_CHECKING:
    from PySide6.QtWidgets import QSystemTrayIcon

logger = logging.getLogger(__name__)

APP_USER_MODEL_ID = "Telegram Hashtag Downloader"

_configured = False
_tray_fallback: QSystemTrayIcon | None = None


def configure_win_notifications() -> None:
    global _configured
    if _configured or not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        logger.debug(tr("log.notify.app_model_failed"), exc_info=True)
    _configured = True


def set_tray_fallback(icon: QSystemTrayIcon | None) -> None:
    global _tray_fallback
    _tray_fallback = icon


def notifications_available() -> bool:
    return sys.platform.startswith("win")


def show_win_notification(
    title: str,
    message: str,
    *,
    error: bool = False,
) -> bool:
    """Show toast · Toast Windows; True при успехе"""
    if not notifications_available():
        return False
    configure_win_notifications()
    if _show_powershell_toast(title, message):
        return True
    return _show_tray_fallback(title, message, error=error)


def _toast_app_short_name() -> str:
    return tr("notify.app_short_name")


def _show_powershell_toast(title: str, message: str) -> bool:
    safe_title = xml.sax.saxutils.escape(title.strip() or _toast_app_short_name())
    safe_body = xml.sax.saxutils.escape(message.strip() or " ")
    app_id = xml.sax.saxutils.escape(APP_USER_MODEL_ID)
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = @"
<toast>
  <visual>
    <binding template="ToastText02">
      <text id="1">{safe_title}</text>
      <text id="2">{safe_body}</text>
    </binding>
  </visual>
</toast>
"@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}").Show($toast)
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=8,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        logger.debug(tr("log.notify.toast_failed"), exc_info=True)
        return False


def _show_tray_fallback(title: str, message: str, *, error: bool) -> bool:
    if _tray_fallback is None:
        return False
    try:
        from PySide6.QtWidgets import QSystemTrayIcon

        icon = (
            QSystemTrayIcon.MessageIcon.Critical
            if error
            else QSystemTrayIcon.MessageIcon.Information
        )
        _tray_fallback.showMessage(title, message, icon, 8000)
        return True
    except Exception:
        logger.debug(tr("log.notify.tray_fallback_failed"), exc_info=True)
        return False
