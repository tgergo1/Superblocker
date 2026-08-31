import asyncio
import queue
import threading

import pytest
from fastapi import HTTPException

from app.api.routes.analysis import WorkCancelled, run_analysis_sync
from app.core.workload import WorkloadGuard
from app.models.schemas import BoundingBox


def test_workload_guard_limits_rate_and_concurrency():
    async def scenario():
        guard = WorkloadGuard(max_concurrent=1, requests_per_minute=1)
        await guard.acquire("client-a")

        with pytest.raises(HTTPException) as rate_error:
            await guard.acquire("client-a")
        assert rate_error.value.status_code == 429

        with pytest.raises(HTTPException) as capacity_error:
            await guard.acquire("client-b")
        assert capacity_error.value.status_code == 429

        guard.release()
        await guard.acquire("client-c")
        guard.release()

    asyncio.run(scenario())


def test_capacity_rejection_does_not_consume_rate_budget():
    async def scenario():
        guard = WorkloadGuard(max_concurrent=1, requests_per_minute=1)
        await guard.acquire("occupying-client")

        with pytest.raises(HTTPException) as capacity_error:
            await guard.acquire("waiting-client")
        assert capacity_error.value.status_code == 429

        guard.release()
        await guard.acquire("waiting-client")
        guard.release()

    asyncio.run(scenario())


def test_cancelled_worker_stops_before_expensive_work():
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(WorkCancelled):
        run_analysis_sync(
            BoundingBox(north=0.01, south=0, east=0.01, west=0),
            4,
            25,
            {"primary"},
            queue.Queue(),
            cancel_event,
        )
