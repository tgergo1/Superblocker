"""Policy-conscious shared Nominatim HTTP client."""

import asyncio
import time
from typing import Any

import httpx

from app.core.config import get_settings

settings = get_settings()
_client: httpx.AsyncClient | None = None
_request_lock = asyncio.Lock()
_last_request_started = 0.0


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.nominatim_url,
            headers={"User-Agent": settings.nominatim_user_agent},
            timeout=10.0,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _client


async def nominatim_get(path: str, params: dict[str, Any]) -> httpx.Response:
    """Start aggregate outbound requests no faster than the configured interval."""
    global _last_request_started
    async with _request_lock:
        remaining = settings.nominatim_min_interval_seconds - (
            time.monotonic() - _last_request_started
        )
        if remaining > 0:
            await asyncio.sleep(remaining)
        _last_request_started = time.monotonic()
    return await _get_client().get(path, params=params)


async def close_nominatim_client() -> None:
    """Close pooled connections during application shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
