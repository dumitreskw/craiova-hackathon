"""Shared async HTTP client for connectors.

One client, sane defaults, a descriptive User-Agent. Connectors should use
:func:`get_client` rather than creating their own, so connection pooling and
timeouts are consistent.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Awaitable, Callable, TypeVar

import httpx

USER_AGENT = "osint-platform/0.1 (+hackathon; research use)"

T = TypeVar("T")

# External sources (crt.sh, search engines) are flaky and throttle bursts.
# Retry transient failures with jittered backoff so one hiccup doesn't produce
# an empty or failed finding.
_RETRIABLE_STATUS = {429, 500, 502, 503, 504}


async def retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.6,
) -> T:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in _RETRIABLE_STATUS:
                raise
            last = e
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = e
        if i < attempts - 1:
            await asyncio.sleep(base_delay * (2**i) + random.uniform(0, 0.3))
    assert last is not None
    raise last

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # OSINT_HTTP_PROXY routes collection through a chosen egress (VPN exit,
        # proxy in the target's country) instead of the server's own IP.
        _client = httpx.AsyncClient(
            proxy=os.environ.get("OSINT_HTTP_PROXY") or None,
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
