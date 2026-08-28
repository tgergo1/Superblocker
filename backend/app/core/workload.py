"""In-process protection for expensive analysis workloads."""

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from fastapi import HTTPException, Request

from app.core.config import get_settings


class WorkloadGuard:
    """Bound concurrent work and apply a small per-client request budget."""

    def __init__(self, max_concurrent: int, requests_per_minute: int) -> None:
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._requests_per_minute = max(1, requests_per_minute)
        self._history: dict[str, deque[float]] = defaultdict(deque)
        self._history_lock = asyncio.Lock()

    async def acquire(self, client_id: str) -> None:
        await self._check_rate_limit(client_id, record=False)

        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=0.05)
        except TimeoutError as exc:
            raise HTTPException(
                status_code=429,
                detail="Analysis capacity is currently full. Please retry shortly.",
            ) from exc

        try:
            await self._check_rate_limit(client_id, record=True)
        except Exception:
            self._semaphore.release()
            raise

    async def _check_rate_limit(self, client_id: str, *, record: bool) -> None:
        now = time.monotonic()
        async with self._history_lock:
            history = self._history[client_id]
            while history and history[0] <= now - 60:
                history.popleft()
            if len(history) >= self._requests_per_minute:
                raise HTTPException(
                    status_code=429,
                    detail="Too many expensive requests. Please retry in a minute.",
                )
            if record:
                history.append(now)

    def release(self) -> None:
        self._semaphore.release()


settings = get_settings()
workload_guard = WorkloadGuard(
    settings.analysis_max_concurrent_requests,
    settings.analysis_rate_limit_per_minute,
)


async def guard_expensive_request(request: Request) -> AsyncIterator[None]:
    """FastAPI dependency that holds capacity until the response is complete."""
    client_id = request.client.host if request.client else "unknown"
    await workload_guard.acquire(client_id)
    try:
        yield
    finally:
        workload_guard.release()
