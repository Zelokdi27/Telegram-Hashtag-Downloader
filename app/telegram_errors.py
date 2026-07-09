"""Telegram errors · Сообщения об ошибках для GUI"""

from __future__ import annotations

from telethon.errors import FloodWaitError, RPCError

from .i18n import tr


def format_telegram_error(exc: BaseException) -> str:
    if isinstance(exc, FloodWaitError):
        return tr("errors.flood_wait", sec=exc.seconds)
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, RPCError):
        name = type(exc).__name__
        text = str(exc).strip() or name
        upper = f"{name} {text}".upper()
        if "CHANNELPRIVATE" in upper or "CHANNEL_PRIVATE" in upper:
            return tr("errors.channel_private")
        if "USERNAMEINVALID" in upper or "USERNAME_INVALID" in upper:
            return tr("errors.channel_invalid")
        if "USERNAMENOTOCCUPIED" in upper or "USERNAME_NOT_OCCUPIED" in upper:
            return tr("errors.channel_not_found")
        if "TIMEOUT" in upper or "TIMED OUT" in upper:
            return tr("errors.timeout")
        if (
            ("SESSION" in upper and "REVOKED" in upper)
            or "AUTHKEYUNREGISTERED" in upper
            or "AUTH_KEY_UNREGISTERED" in upper
        ):
            return tr("errors.session_expired")
        if "NETWORK" in upper or "CONNECTION" in upper:
            return tr("errors.no_connection")
        return tr("errors.generic", text=text)
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return tr("errors.network")
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10060, 10061}:
        return tr("errors.connect_server")
    return str(exc) or type(exc).__name__
