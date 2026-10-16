"""Citywide traffic simulation: network capacity with and without the plan."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.routes.analysis import _sse_data, analysis_executor, partition_store
from app.core.workload import guard_expensive_request
from app.models.simulation import CapacitySimulationRequest, CapacitySimulationResult
from app.services.osm_service import get_street_network_graph
from app.services.simulation.capacity_simulator import CapacitySimulator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/simulation")


class _Cancelled(Exception):
    pass


async def _graph_for(request: CapacitySimulationRequest):
    partition = request.partition
    cached = partition_store.get_exact(partition.bbox, partition.boundary)
    if cached is not None:
        return cached.graph
    graph = await get_street_network_graph(partition.bbox, boundary=partition.boundary)
    partition_store.put(partition, graph, partition.bbox)
    return graph


def _run(graph, request: CapacitySimulationRequest, progress=None) -> CapacitySimulationResult:
    return CapacitySimulator(
        graph,
        partition=request.partition,
        settings=request.settings,
        progress=progress,
    ).run()


@router.post("/capacity", response_model=CapacitySimulationResult)
async def simulate_capacity(
    request: CapacitySimulationRequest,
    _permit: None = Depends(guard_expensive_request),
):
    """Compare travel times and network capacity with and without the plan."""
    try:
        graph = await _graph_for(request)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(analysis_executor, _run, graph, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Capacity simulation failed")
        raise HTTPException(status_code=500, detail="Unable to simulate this plan") from exc


@router.post("/capacity/stream")
async def simulate_capacity_stream(
    request: CapacitySimulationRequest,
    http_request: Request,
    _permit: None = Depends(guard_expensive_request),
):
    """The same simulation with Server-Sent Events progress updates."""
    progress_queue: queue.Queue = queue.Queue(maxsize=100)
    cancel_event = threading.Event()

    def progress(message: str, share: float) -> None:
        if cancel_event.is_set():
            raise _Cancelled()
        with contextlib.suppress(queue.Full):
            progress_queue.put_nowait(
                {"type": "progress", "message": message, "percent": round(share * 100)}
            )

    async def generate():
        loop = asyncio.get_running_loop()
        try:
            progress("Loading the street network", 0.0)
            graph = await _graph_for(request)
        except ValueError as exc:
            yield _sse_data({"type": "error", "message": str(exc)})
            return
        except Exception:
            logger.exception("Capacity simulation could not load the network")
            yield _sse_data({"type": "error", "message": "Unable to load the street network"})
            return
        future = loop.run_in_executor(analysis_executor, _run, graph, request, progress)
        while not future.done():
            if await http_request.is_disconnected():
                cancel_event.set()
                return
            try:
                yield _sse_data(progress_queue.get_nowait())
            except queue.Empty:
                await asyncio.sleep(0.1)
        while not progress_queue.empty():
            yield _sse_data(progress_queue.get_nowait())
        try:
            result = await future
            yield _sse_data({"type": "complete", "result": result.model_dump(mode="json")})
        except ValueError as exc:
            yield _sse_data({"type": "error", "message": str(exc)})
        except Exception:
            logger.exception("Capacity simulation stream failed")
            yield _sse_data({"type": "error", "message": "Unable to simulate this plan"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
