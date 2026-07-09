"""Telegram auth · Авторизация Telegram"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyNotFound,
    FloodWaitError,
    NetworkMigrateError,
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    PhoneMigrateError,
    PhoneNumberInvalidError,
    RPCError,
    SessionPasswordNeededError,
    UserMigrateError,
)
from telethon.network import (
    ConnectionTcpAbridged,
    ConnectionTcpFull,
    ConnectionTcpIntermediate,
    ConnectionTcpObfuscated,
)

from .auth_constants import RESEND_CODE
from .config_store import SettingsData
from .i18n import tr
from .telethon_loop import sync_await as _run
from .win_asyncio import fix_windows_asyncio

logger = logging.getLogger(__name__)

# WARP DC workaround · Обход WARP DC
DC_TARGETS: dict[int, list[tuple[str, int, bool]]] = {
    1: [("149.154.175.50", 443, False)],
    2: [
        ("2001:67c:4e8:f002::a", 443, True),
        ("149.154.167.51", 443, False),
        ("149.154.167.41", 443, False),
    ],
    3: [("149.154.175.100", 443, False)],
    4: [("149.154.167.41", 443, False)],
    5: [("91.108.56.100", 443, False)],
}

CONNECTION_MODES = [
    ConnectionTcpIntermediate,
    ConnectionTcpObfuscated,
    ConnectionTcpAbridged,
    ConnectionTcpFull,
]


@dataclass
class AuthResult:
    ok: bool
    username: str = ""
    error: str = ""
    needs_phone: bool = False
    connection_failed: bool = False


def reset_session_files(session_path: str | Path) -> list[str]:
    base = Path(session_path)
    removed: list[str] = []
    for candidate in (base, Path(f"{base}.session"), Path(f"{base}-journal")):
        if candidate.exists():
            candidate.unlink()
            removed.append(candidate.name)
    return removed


def _build_proxy(settings: SettingsData | None):
    if not settings or not settings.proxy_enabled:
        return None
    proxy_type = settings.proxy_type or "socks5"
    host = settings.proxy_host or "127.0.0.1"
    port = int(settings.proxy_port or 1080)
    return (proxy_type, host, port)


def make_client(
    session_path: str | Path,
    api_id: int,
    api_hash: str,
    settings: SettingsData | None = None,
    connection: type | None = None,
) -> TelegramClient:
    proxy = _build_proxy(settings)
    client = TelegramClient(
        str(session_path),
        api_id,
        api_hash,
        proxy=proxy,
        connection=connection or ConnectionTcpIntermediate,
        connection_retries=1,
        retry_delay=1,
        timeout=15,
        request_retries=2,
        flood_sleep_threshold=0,
    )
    _patch_dc_switch(client)
    return client


def normalize_phone(phone: str) -> str:
    raw = phone.strip()
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if raw.startswith("+"):
        return "+" + digits
    if digits:
        return "+" + digits
    return raw


def guess_dc_from_phone(phone: str) -> int:
    digits = re.sub(r"\D", "", phone.strip())
    if not digits:
        return 2
    if digits.startswith("1"):
        return 1
    if digits.startswith("91") or digits.startswith("86"):
        return 5
    return 2


async def adisconnect_quietly(client: TelegramClient) -> None:
    from .telethon_loop import maybe_await

    try:
        if client.is_connected():
            await maybe_await(client.disconnect())
    except Exception:
        pass


def disconnect_client_sync(client: TelegramClient) -> None:
    """Sync disconnect · Отключение клиента из GUI"""
    _run(adisconnect_quietly(client))


def _clear_broken_session(client: TelegramClient) -> None:
    client.session.auth_key = None
    client.session.save()


async def _aprepare_migration(client: TelegramClient) -> None:
    await adisconnect_quietly(client)
    client.session.auth_key = None
    sender = getattr(client, "_sender", None)
    if sender is not None and getattr(sender, "auth_key", None) is not None:
        sender.auth_key.key = None
    client.session.save()


async def _aconnect_dc_robust(
    client: TelegramClient,
    dc_id: int,
    *,
    for_migration: bool = False,
) -> bool:
    if for_migration:
        await _aprepare_migration(client)

    targets = DC_TARGETS.get(dc_id, [])
    if not targets:
        return False

    mode_rounds = [CONNECTION_MODES[0], *CONNECTION_MODES[1:]]

    for conn_cls in mode_rounds:
        for ip, port, use_ipv6 in targets:
            try:
                await adisconnect_quietly(client)
                client._connection = conn_cls  # type: ignore[attr-defined]
                client._use_ipv6 = use_ipv6  # type: ignore[attr-defined]
                client.session.set_dc(dc_id, ip, port)
                logger.info(
                    tr(
                        "log.auth_telegram.dc_try",
                        dc=dc_id,
                        ip=ip,
                        mode=conn_cls.__name__,
                        ipv6=use_ipv6,
                    ),
                )
                await client.connect()
                if client.is_connected():
                    logger.info(
                        tr(
                            "log.auth_telegram.dc_connected",
                            dc=dc_id,
                            ip=ip,
                            mode=conn_cls.__name__,
                        ),
                    )
                    return True
            except AuthKeyNotFound:
                logger.warning(tr("log.auth_telegram.broken_session", dc=dc_id))
                _clear_broken_session(client)
            except (TimeoutError, OSError, ConnectionError) as exc:
                logger.warning(
                    tr(
                        "log.auth_telegram.dc_fail",
                        dc=dc_id,
                        ip=ip,
                        mode=conn_cls.__name__,
                        exc=exc,
                    ),
                )
            except Exception as exc:
                logger.warning(
                    tr(
                        "log.auth_telegram.dc_fail",
                        dc=dc_id,
                        ip=ip,
                        mode=conn_cls.__name__,
                        exc=exc,
                    ),
                )
            finally:
                if not client.is_connected():
                    await adisconnect_quietly(client)
    return False


async def aswitch_to_dc(client: TelegramClient, dc_id: int) -> bool:
    return await _aconnect_dc_robust(client, dc_id, for_migration=True)


def _patch_dc_switch(client: TelegramClient) -> None:
    if getattr(client, "_dc_switch_patched", False):
        return

    async def _patched_switch_dc(self: TelegramClient, new_dc: int):
        logger.info(tr("log.auth_telegram.dc_pick", dc=new_dc))
        ok = await _aconnect_dc_robust(self, new_dc, for_migration=True)
        if ok:
            return
        raise ConnectionError(tr("auth.login.dc_switch_failed", dc=new_dc))

    client._switch_dc = types.MethodType(_patched_switch_dc, client)  # type: ignore[method-assign]
    client._dc_switch_patched = True  # type: ignore[attr-defined]


def _migration_error(dc_id: int) -> AuthResult:
    return AuthResult(
        ok=False,
        error=tr("auth.migration", dc=dc_id),
        connection_failed=True,
    )


async def _ahandle_migration(client: TelegramClient, exc: BaseException) -> AuthResult | None:
    new_dc = getattr(exc, "new_dc", None)
    if new_dc is None:
        return None
    logger.info(tr("log.auth_telegram.dc_switch", dc=new_dc))
    if await aswitch_to_dc(client, new_dc):
        return None
    return _migration_error(new_dc)


def _describe_code_delivery(sent: Any) -> str:
    code_type = type(sent.type).__name__
    length = getattr(sent.type, "length", 5)
    timeout = sent.timeout or 0

    if code_type == "SentCodeTypeApp":
        text = tr("auth.code.app", length=length)
    elif code_type == "SentCodeTypeSms":
        text = tr("auth.code.sms", length=length)
    elif code_type in {"SentCodeTypeCall", "SentCodeTypeFlashCall"}:
        text = tr("auth.code.call", length=length)
    elif code_type == "SentCodeTypeMissedCall":
        prefix = getattr(sent.type, "prefix", "")
        pattern = f": {prefix}" if prefix else ""
        text = tr("auth.code.missed_call", pattern=pattern)
    else:
        text = tr("auth.code.other", type=code_type)

    if timeout:
        text += tr("auth.code.resend_timeout", timeout=timeout)
    return text


async def _arequest_login_code(
    client: TelegramClient,
    phone: str,
) -> tuple[AuthResult | None, Any | None]:
    target_dc = guess_dc_from_phone(phone)
    current_dc = client.session.dc_id
    if current_dc != target_dc:
        logger.info(
            tr(
                "log.auth_telegram.phone_dc_switch",
                phone=phone[:4] + "…",
                target=target_dc,
                current=current_dc,
            ),
        )
        if not await aswitch_to_dc(client, target_dc):
            return _migration_error(target_dc), None

    try:
        sent = await client.send_code_request(phone)
        logger.info(
            tr(
                "log.auth_telegram.code_requested_detail",
                type=type(sent.type).__name__,
                timeout=sent.timeout,
            ),
        )
        return None, sent
    except (PhoneMigrateError, NetworkMigrateError, UserMigrateError) as exc:
        migration = await _ahandle_migration(client, exc)
        if migration:
            return migration, None
        try:
            sent = await client.send_code_request(phone)
            logger.info(tr("log.auth_telegram.code_after_migration", type=type(sent.type).__name__))
            return None, sent
        except (PhoneMigrateError, NetworkMigrateError, UserMigrateError) as exc2:
            migration = await _ahandle_migration(client, exc2)
            if migration:
                return migration, None
            raise
    except ConnectionError as exc:
        return AuthResult(
            ok=False,
            error=tr("auth.login.code_send_failed", exc=exc),
            connection_failed=True,
        ), None


async def _atry_connect_client(client: TelegramClient) -> AuthResult | None:
    last_error = tr("log.unknown_error")
    preferred = client.session.dc_id or 2
    dc_order = [preferred] + [dc for dc in (2, 1, 3, 4, 5) if dc != preferred]

    for dc_id in dc_order:
        if await _aconnect_dc_robust(client, dc_id):
            return None
        last_error = tr("log.auth_telegram.dc_unreachable", dc=dc_id)

    return AuthResult(
        ok=False,
        error=tr("auth.login.connect_failed", error=last_error),
        connection_failed=True,
    )


async def aconnect_client(client: TelegramClient) -> AuthResult | None:
    fix_windows_asyncio()
    _patch_dc_switch(client)
    await adisconnect_quietly(client)
    return await _atry_connect_client(client)


async def acheck_session(
    session_path: str | Path,
    api_id: int,
    api_hash: str,
    settings: SettingsData | None = None,
) -> AuthResult:
    client = make_client(session_path, api_id, api_hash, settings)
    try:
        error = await aconnect_client(client)
        if error:
            return error
        if not await client.is_user_authorized():
            return AuthResult(
                ok=False,
                error=tr("auth.error.not_logged_in"),
                needs_phone=True,
            )
        me = await client.get_me()
        label = getattr(me, "username", None) or str(me.id)
        return AuthResult(ok=True, username=label)
    finally:
        await adisconnect_quietly(client)


def check_session(
    session_path: str | Path,
    api_id: int,
    api_hash: str,
    settings: SettingsData | None = None,
) -> AuthResult:
    return _run(acheck_session(session_path, api_id, api_hash, settings))


async def atest_telegram_connectivity(
    api_id: int,
    api_hash: str,
    settings: SettingsData | None = None,
) -> AuthResult:
    """Connectivity test · Проверка Telegram без входа"""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".session")
    os.close(fd)
    try:
        client = make_client(path, api_id, api_hash, settings)
        try:
            error = await aconnect_client(client)
            if error:
                return error
            return AuthResult(ok=True)
        finally:
            await adisconnect_quietly(client)
    finally:
        for candidate in (path, f"{path}-journal"):
            try:
                Path(candidate).unlink(missing_ok=True)
            except OSError:
                pass


def test_telegram_connectivity(
    api_id: int,
    api_hash: str,
    settings: SettingsData | None = None,
) -> AuthResult:
    return _run(atest_telegram_connectivity(api_id, api_hash, settings))


async def _asign_in_with_cloud_password(
    client: TelegramClient,
    ask_secret: Callable[[str, str], str | None],
    *,
    title: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> AuthResult | None:
    dialog_title = title or tr("auth.2fa.title")
    prompt = tr("auth.2fa.prompt")
    while True:
        if should_cancel and should_cancel():
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))
        password = ask_secret(dialog_title, prompt)
        if not password:
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))
        try:
            await client.sign_in(password=password)
            return None
        except PasswordHashInvalidError:
            prompt = tr("auth.2fa.invalid")


async def _acomplete_sign_in(
    client: TelegramClient,
    phone: str,
    code: str,
    ask_secret: Callable[[str, str], str | None],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> AuthResult | None:
    try:
        await client.sign_in(phone=phone, code=code)
        return None
    except (PhoneMigrateError, NetworkMigrateError, UserMigrateError) as exc:
        migration = await _ahandle_migration(client, exc)
        if migration:
            return migration
        await client.sign_in(phone=phone, code=code)
        return None
    except PhoneCodeInvalidError:
        return AuthResult(ok=False, error=tr("auth.login.invalid_code"))
    except SessionPasswordNeededError:
        return await _asign_in_with_cloud_password(
            client,
            ask_secret,
            title=tr("auth.step3.title"),
            should_cancel=should_cancel,
        )


async def alogin_interactive(
    session_path: str | Path,
    api_id: int,
    api_hash: str,
    ask: Callable[[str, str], str | None],
    ask_secret: Callable[[str, str], str | None],
    ask_code: Callable[[str, str], str | None] | None = None,
    settings: SettingsData | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    register_client: Callable[[TelegramClient], None] | None = None,
) -> AuthResult:
    client = make_client(session_path, api_id, api_hash, settings)
    if register_client:
        register_client(client)

    try:
        if should_cancel and should_cancel():
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))

        logger.info(tr("log.auth_telegram.connecting"))
        error = await aconnect_client(client)
        if error:
            return error

        if should_cancel and should_cancel():
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))

        if await client.is_user_authorized():
            me = await client.get_me()
            label = getattr(me, "username", None) or str(me.id)
            logger.info(tr("log.auth_telegram.already_authorized", user=label))
            return AuthResult(ok=True, username=label)

        phone = ask(
            tr("auth.step1.title"),
            tr("auth.step1.prompt"),
        )
        if should_cancel and should_cancel():
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))
        if not phone:
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))
        phone = normalize_phone(phone)
        logger.info(tr("log.auth_telegram.code_request", phone=phone[:4] + "…"))

        code_prompt = ask_code or ask
        try:
            code_error, sent = await _arequest_login_code(client, phone)
            if code_error:
                return code_error
        except PhoneNumberInvalidError:
            return AuthResult(ok=False, error=tr("auth.login.invalid_phone"))
        except FloodWaitError as exc:
            return AuthResult(ok=False, error=tr("auth.login.flood_wait", sec=exc.seconds))
        except ConnectionError as exc:
            return AuthResult(
                ok=False,
                error=tr("auth.login.connection_failed", exc=exc),
                connection_failed=True,
            )

        prompt = _describe_code_delivery(sent)
        code: str | None = None
        while True:
            if should_cancel and should_cancel():
                return AuthResult(ok=False, error=tr("auth.login.cancelled"))
            value = code_prompt(tr("auth.step2.title"), prompt)
            if value == RESEND_CODE:
                logger.info(tr("log.auth_telegram.code_resend"))
                try:
                    code_error, sent = await _arequest_login_code(client, phone)
                    if code_error:
                        return code_error
                    prompt = _describe_code_delivery(sent)
                except FloodWaitError as exc:
                    return AuthResult(
                        ok=False,
                        error=tr("auth.login.resend_wait", sec=exc.seconds),
                    )
                continue
            code = value
            break

        if not code:
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))

        sign_in_error = await _acomplete_sign_in(
            client,
            phone,
            code,
            ask_secret,
            should_cancel=should_cancel,
        )
        if sign_in_error:
            return sign_in_error

        me = await client.get_me()
        label = getattr(me, "username", None) or str(me.id)
        logger.info(tr("log.auth_telegram.login_success", user=label))
        return AuthResult(ok=True, username=label)

    except RPCError as exc:
        return AuthResult(ok=False, error=tr("auth.login.telegram_error", exc=exc))
    except Exception as exc:
        logger.exception(tr("log.auth_telegram.login_error"))
        return AuthResult(ok=False, error=str(exc))
    finally:
        await adisconnect_quietly(client)


def login_interactive(
    session_path: str | Path,
    api_id: int,
    api_hash: str,
    ask: Callable[[str, str], str | None],
    ask_secret: Callable[[str, str], str | None],
    ask_code: Callable[[str, str], str | None] | None = None,
    settings: SettingsData | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    register_client: Callable[[TelegramClient], None] | None = None,
) -> AuthResult:
    return _run(
        alogin_interactive(
            session_path,
            api_id,
            api_hash,
            ask,
            ask_secret,
            ask_code=ask_code,
            settings=settings,
            should_cancel=should_cancel,
            register_client=register_client,
        ),
    )


async def alogin_qr_interactive(
    session_path: str | Path,
    api_id: int,
    api_hash: str,
    show_qr: Callable[[str], None],
    hide_qr: Callable[[], None],
    ask_secret: Callable[[str, str], str | None],
    settings: SettingsData | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    register_client: Callable[[TelegramClient], None] | None = None,
) -> AuthResult:
    client = make_client(session_path, api_id, api_hash, settings)
    if register_client:
        register_client(client)

    try:
        if should_cancel and should_cancel():
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))

        logger.info(tr("log.auth_telegram.connecting_qr"))
        error = await aconnect_client(client)
        if error:
            return error

        if should_cancel and should_cancel():
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))

        if await client.is_user_authorized():
            me = await client.get_me()
            label = getattr(me, "username", None) or str(me.id)
            return AuthResult(ok=True, username=label)

        qr_login = await client.qr_login()
        show_qr(qr_login.url)
        logger.info(tr("log.auth_telegram.qr_waiting"))
        try:
            await qr_login.wait()
        except SessionPasswordNeededError:
            hide_qr()
            password_error = await _asign_in_with_cloud_password(
                client,
                ask_secret,
                should_cancel=should_cancel,
            )
            if password_error:
                return password_error
        except Exception:
            if should_cancel and should_cancel():
                return AuthResult(ok=False, error=tr("auth.login.cancelled"))
            raise
        finally:
            hide_qr()

        if should_cancel and should_cancel():
            return AuthResult(ok=False, error=tr("auth.login.cancelled"))

        me = await client.get_me()
        label = getattr(me, "username", None) or str(me.id)
        logger.info(tr("log.auth_telegram.qr_success", user=label))
        return AuthResult(ok=True, username=label)

    except RPCError as exc:
        return AuthResult(ok=False, error=tr("auth.login.telegram_error", exc=exc))
    except Exception as exc:
        logger.exception(tr("log.auth_telegram.qr_login_error"))
        message = str(exc).strip() or tr(
            "auth.login.qr_failed",
            type=type(exc).__name__,
        )
        return AuthResult(ok=False, error=message)
    finally:
        hide_qr()
        await adisconnect_quietly(client)


def login_qr_interactive(
    session_path: str | Path,
    api_id: int,
    api_hash: str,
    show_qr: Callable[[str], None],
    hide_qr: Callable[[], None],
    ask_secret: Callable[[str, str], str | None],
    settings: SettingsData | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    register_client: Callable[[TelegramClient], None] | None = None,
) -> AuthResult:
    return _run(
        alogin_qr_interactive(
            session_path,
            api_id,
            api_hash,
            show_qr,
            hide_qr,
            ask_secret,
            settings=settings,
            should_cancel=should_cancel,
            register_client=register_client,
        ),
    )