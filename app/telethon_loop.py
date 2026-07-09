"""Telethon loop · Async Telethon в worker и sync-обёртки"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .win_asyncio import fix_windows_asyncio

T = TypeVar("T")


async def maybe_await(value: Any) -> Any:
    """Maybe await · await или значение как есть"""
    if inspect.isawaitable(value):
        return await value
    return value


def run_async(coro: Awaitable[T]) -> T:
    """Run async · Корутина в новом event loop"""
    fix_windows_asyncio()
    if not inspect.isawaitable(coro):
        raise TypeError(f"a coroutine was expected, got {type(coro)!r}")
    return asyncio.run(coro)


def sync_await(awaitable: Awaitable[T]) -> T:
    """Sync await · Алиас для sync-кода"""
    return run_async(awaitable)


class AsyncMethodFacade:
    """Async facade · Sync-вызов async-методов"""

    def __init__(self, target: Any) -> None:
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str) -> Any:
        target = object.__getattribute__(self, "_target")
        value = getattr(target, name)
        if inspect.iscoroutinefunction(value):

            def _caller(*args: Any, **kwargs: Any) -> Any:
                return run_async(value(*args, **kwargs))

            return _caller
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_target"), name, value)