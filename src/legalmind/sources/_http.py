"""HTTP for the public legal sources: timeouts, retries with backoff, a TTL cache, and one error type."""

from __future__ import annotations

import time
from collections import OrderedDict
from functools import wraps
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

TIMEOUT = httpx.Timeout(20.0, connect=5.0)
USER_AGENT = "LegalMind-AI/0.1 (+https://github.com/munyaradzi-zhou/LegalMind-AI)"


class SourceUnavailableError(Exception):
    """The public source didn't answer (after retries): the agent should say so, not guess."""


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


async def get(url: str, params: list[tuple[str, str]] | None = None) -> bytes:
    """GET ``url`` as XML; three attempts with backoff on network errors, 429 and 5xx."""
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=4),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
                    # Accept: */* gets a 406 from zoekservice.overheid.nl
                    response = await client.get(url, params=tuple(params or ()), headers={"Accept": "application/xml"})
                    response.raise_for_status()
                    return response.content
    except httpx.HTTPError as exc:
        logger.warning("legal_source_unavailable", url=url, error=type(exc).__name__)
        raise SourceUnavailableError(f"{httpx.URL(url).host} did not answer") from exc
    raise AssertionError("unreachable")  # pragma: no cover - AsyncRetrying either returns or raises


def ttl_cache(seconds: float, maxsize: int = 64) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Cache an async function's results for ``seconds`` (keyed on its arguments). Errors aren't cached."""

    def decorate(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        entries: OrderedDict[Any, tuple[float, T]] = OrderedDict()

        @wraps(fn)
        async def cached(*args: P.args, **kwargs: P.kwargs) -> T:
            key = (args, tuple(sorted(kwargs.items())))
            hit = entries.get(key)
            if hit and hit[0] > time.monotonic():
                entries.move_to_end(key)
                return hit[1]
            value = await fn(*args, **kwargs)
            entries[key] = (time.monotonic() + seconds, value)
            if len(entries) > maxsize:
                entries.popitem(last=False)
            return value

        cached.cache_clear = entries.clear  # type: ignore[attr-defined]
        return cached

    return decorate
