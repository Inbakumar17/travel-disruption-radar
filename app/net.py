"""Shared HTTP layer: one client, a TTL cache, one retry, and a per-request trace.

Every outbound call goes through Trace.fetch so the UI can show exactly which
sources were consulted, how long they took, and which ones failed.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import httpx

from . import config
from .models import SourceCall

_cache: dict[str, tuple[float, Any, int, float]] = {}
_client: Optional[httpx.AsyncClient] = None


class SourceError(Exception):
    """Raised when an external source can't be reached or returns an error."""


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=config.HTTP_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _fetch(url: str, params: Optional[dict], kind: str, headers: Optional[dict]) -> Any:
    if config.DEMO_MODE:
        from . import fixtures

        await asyncio.sleep(0.02)
        return fixtures.respond(url, params or {})

    last_error: Exception | None = None
    for attempt in range(2):  # one retry for transient failures
        try:
            r = await _get_client().get(url, params=params, headers=headers)
            if r.status_code >= 500:
                raise SourceError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                # client errors won't fix themselves - don't retry
                raise SourceError(f"HTTP {r.status_code}: {r.text[:120]}")
            return r.json() if kind == "json" else r.text
        except SourceError as e:
            last_error = e
            if not str(e).startswith("HTTP 5"):
                break
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_error = SourceError(f"{type(e).__name__}")
        except ValueError as e:  # bad JSON
            last_error = SourceError(f"Invalid response: {e}")
            break
        if attempt == 0:
            await asyncio.sleep(0.5)
    raise last_error or SourceError("Unknown error")


class Trace:
    """Collects a record of every source call made while answering one request."""

    def __init__(self) -> None:
        self.calls: list[SourceCall] = []

    async def fetch(
        self,
        source: str,
        target: str,
        url: str,
        params: Optional[dict] = None,
        *,
        kind: str = "json",
        ttl: int = 300,
        headers: Optional[dict] = None,
    ) -> Any:
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        now = time.time()
        hit = _cache.get(key)
        if hit and hit[0] > now:
            _, data, orig_ms, fetched_at = hit
            self.calls.append(SourceCall(source=source, target=target, url=url, ok=True, ms=orig_ms, cached=True,
                                         age_s=int(now - fetched_at)))
            return data

        t0 = time.perf_counter()
        try:
            data = await _fetch(url, params, kind, headers)
        except Exception as e:  # record, then surface as SourceError
            ms = int((time.perf_counter() - t0) * 1000)
            self.calls.append(
                SourceCall(source=source, target=target, url=url, ok=False, ms=ms, error=str(e)[:160] or type(e).__name__)
            )
            raise SourceError(str(e)) from e

        ms = int((time.perf_counter() - t0) * 1000)
        _cache[key] = (now + ttl, data, ms, now)  # expiry, payload, original latency, fetch time
        self.calls.append(SourceCall(source=source, target=target, url=url, ok=True, ms=ms))
        return data

    def failed(self, source_prefix: str = "") -> int:
        return sum(1 for c in self.calls if not c.ok and c.source.startswith(source_prefix))


def clear_cache() -> None:
    _cache.clear()
