"""Retry with exponential backoff.

A lean, purpose-built decorator (async- and sync-aware). Kept intentionally small:
fixed jitterless exponential backoff over a caller-supplied set of retryable
exceptions. The last exception is re-raised on exhaustion.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class _Backoff:
    """Delay schedule for a retry loop."""

    max_attempts: int
    base_delay: float
    max_delay: float
    backoff_factor: float
    retryable: tuple[type[Exception], ...]

    def delay_for(self, attempt: int) -> float:
        """Sleep before the *attempt*-th retry (1-based over already-failed tries)."""
        return min(
            self.base_delay * (self.backoff_factor ** (attempt - 1)), self.max_delay
        )


def _wrap_sync[**P, T](func: Callable[P, T], cfg: _Backoff) -> Callable[P, T]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in range(1, cfg.max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except cfg.retryable:
                if attempt == cfg.max_attempts:
                    raise
                time.sleep(cfg.delay_for(attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    return wrapper


def _wrap_async[**P, T](
    func: Callable[P, Awaitable[T]], cfg: _Backoff
) -> Callable[P, T]:
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in range(1, cfg.max_attempts + 1):
            try:
                return await func(*args, **kwargs)
            except cfg.retryable:
                if attempt == cfg.max_attempts:
                    raise
                await asyncio.sleep(cfg.delay_for(attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    return cast("Callable[P, T]", wrapper)


def retry_with_backoff[**P, T](
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 2.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Retry a callable on *retryable_exceptions* with exponential backoff.

    Works on both sync and coroutine functions. On the final attempt the caught
    exception propagates unchanged.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    cfg = _Backoff(
        max_attempts, base_delay, max_delay, backoff_factor, retryable_exceptions
    )

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        if asyncio.iscoroutinefunction(func):
            return _wrap_async(cast("Callable[P, Awaitable[T]]", func), cfg)
        return _wrap_sync(func, cfg)

    return decorator
