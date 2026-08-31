import asyncio
import hashlib
import json
import logging
import math
import queue
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from shapely.geometry import Point, shape

from app.core.config import get_settings
from app.core.workload import guard_expensive_request
from app.models.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    BoundingBox,
    CityPartition,
    Coordinates,
    PartitionProgress,
    PartitionRequest,
    PartitionResponse,
    PartitionReviewRequest,
    RouteRequest,
    RouteResult,
    StreetNetworkRequest,
    StreetNetworkResponse,
)
from app.services.detection.superblock_analyzer import SuperblockAnalyzer
from app.services.osm_service import (
    get_street_network,
    get_street_network_graph,
    graph_to_street_network,
)
from app.services.partitioning.city_partitioner import (
    CityPartitioner,
    assess_plan_readiness,
    compute_plan_id,
)
from app.services.routing.superblock_router import SuperblockRouter
from app.services.sizing.size_optimizer import calculate_optimal_superblock_size
from app.services.traffic import (
    apply_traffic_observations_to_graph,
    apply_traffic_observations_to_network,
    estimate_traffic,
)

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

# Thread pool for CPU-bound analysis
analysis_executor = ThreadPoolExecutor(
    max_workers=settings.analysis_max_workers,
    thread_name_prefix="superblock-work",
)


class WorkCancelled(RuntimeError):
    """Raised inside a worker when its streaming client disconnects."""


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise WorkCancelled("Analysis cancelled after client disconnect")


def _json_safe(value):
    """Recursively replace non-finite numeric values before public serialization."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(_json_safe(payload), allow_nan=False)}\n\n"


@router.post("/network", response_model=StreetNetworkResponse)
async def fetch_street_network(
    request: StreetNetworkRequest,
    _permit: None = Depends(guard_expensive_request),
):
    """
    Fetch street network for a bounding box.

    Returns GeoJSON FeatureCollection of road segments with traffic estimates.
    """
    try:
        network = await get_street_network(
            bbox=request.bbox,
            network_type=request.network_type,
        )

        # Add traffic estimates
        network = estimate_traffic(network)

        return network
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unhandled error fetching street network")
        raise HTTPException(status_code=500, detail="Unable to fetch the street network") from e


def run_analysis_sync(
    bbox: BoundingBox,
    min_area: float,
    max_area: float,
    boundary_road_types: set[str],
    progress_queue: queue.Queue,
    cancel_event: threading.Event | None = None,
):
    """
    Run the analysis synchronously in a thread.
    Uses a thread-safe queue for progress updates.
    """

    def progress_callback(stage: str, percent: int, message: str):
        _raise_if_cancelled(cancel_event)
        try:
            progress_queue.put_nowait(
                {
                    "type": "progress",
                    "stage": stage,
                    "percent": percent,
                    "message": message,
                }
            )
            logger.info("Progress update: %s %s%% - %s", stage, percent, message)
        except queue.Full:
            pass

    _raise_if_cancelled(cancel_event)
    logger.info(
        "Analysis thread started (min_area=%.2f max_area=%.2f bbox=%s)",
        min_area,
        max_area,
        bbox.model_dump(),
    )
    analyzer = SuperblockAnalyzer(
        min_area=min_area,
        max_area=max_area,
        boundary_road_types=boundary_road_types,
    )

    # Run synchronous version of analyze
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        start_time = time.time()
        result = loop.run_until_complete(analyzer.analyze(bbox, progress_callback))
        elapsed = time.time() - start_time
        logger.info(
            "Analysis thread finished in %.1fs (candidates=%s)",
            elapsed,
            len(result.get("candidates", [])) if isinstance(result, dict) else "n/a",
        )
        return result
    finally:
        loop.close()


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_superblocks(
    request: AnalysisRequest,
    _permit: None = Depends(guard_expensive_request),
):
    """
    Analyze an area for potential superblocks.

    Uses advanced centrality-based detection with multi-criteria scoring
    based on the Barcelona Superilles methodology.
    """
    try:
        logger.info(
            "Received /analyze request (min_area=%.2f max_area=%.2f bbox=%s)",
            request.min_area_hectares,
            request.max_area_hectares,
            request.bbox.model_dump(),
        )
        # Run in thread pool to not block event loop
        progress_queue = queue.Queue(maxsize=100)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            analysis_executor,
            run_analysis_sync,
            request.bbox,
            request.min_area_hectares,
            request.max_area_hectares,
            {road_type.value for road_type in request.boundary_road_types},
            progress_queue,
            None,
        )

        response = {
            "candidates": result.get("candidates", []),
            "total_found": len(result.get("candidates", [])),
            "bbox": request.bbox.model_dump(),
            "network_stats": result.get("network_stats", {}),
            "parameters": {
                "min_area_hectares": request.min_area_hectares,
                "max_area_hectares": request.max_area_hectares,
                "algorithms": [algorithm.value for algorithm in request.algorithms],
                "boundary_road_types": [
                    road_type.value for road_type in request.boundary_road_types
                ],
            },
        }
        logger.info("Completed /analyze request (total_found=%s)", response["total_found"])
        return response
    except ValueError as e:
        logger.warning("Validation error in /analyze: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unhandled error in /analyze")
        raise HTTPException(status_code=500, detail="Unable to analyze this area") from e


@router.post("/analyze/stream")
async def analyze_superblocks_stream(
    request: AnalysisRequest,
    http_request: Request,
    _permit: None = Depends(guard_expensive_request),
):
    """
    Analyze an area for potential superblocks with streaming progress updates.

    Returns Server-Sent Events (SSE) with progress updates followed by final results.
    """
    # Thread-safe queue for cross-thread communication
    progress_queue = queue.Queue(maxsize=100)

    async def generate():
        loop = asyncio.get_running_loop()
        cancel_event = threading.Event()
        stream_task = asyncio.current_task()
        if stream_task is not None:
            stream_task.add_done_callback(lambda _task: cancel_event.set())
        future = loop.run_in_executor(
            analysis_executor,
            run_analysis_sync,
            request.bbox,
            request.min_area_hectares,
            request.max_area_hectares,
            {road_type.value for road_type in request.boundary_road_types},
            progress_queue,
            cancel_event,
        )
        logger.info("Streaming response started")

        # Stream progress updates
        last_heartbeat = time.time()
        while not future.done():
            if await http_request.is_disconnected():
                cancel_event.set()
                future.cancel()
                logger.info("Analysis stream client disconnected")
                return
            try:
                # Non-blocking check for progress
                progress = progress_queue.get_nowait()
                logger.info(
                    "Streaming progress event: %s %s%%",
                    progress.get("stage"),
                    progress.get("percent"),
                )
                yield _sse_data(progress)
            except queue.Empty:
                # No progress yet, wait a bit
                now = time.time()
                if now - last_heartbeat >= 15:
                    logger.info("Streaming heartbeat: analysis still running")
                    last_heartbeat = now
                await asyncio.sleep(0.1)
                continue

        # Drain any remaining progress messages
        while True:
            try:
                progress = progress_queue.get_nowait()
                logger.info(
                    "Streaming final progress event: %s %s%%",
                    progress.get("stage"),
                    progress.get("percent"),
                )
                yield _sse_data(progress)
            except queue.Empty:
                break

        try:
            result = await future
            final_data = {
                "type": "complete",
                "candidates": result.get("candidates", []),
                "total_found": len(result.get("candidates", [])),
                "network_stats": result.get("network_stats", {}),
            }
            logger.info("Streaming analysis complete (total_found=%s)", final_data["total_found"])
            yield _sse_data(final_data)
        except Exception:
            logger.exception("Streaming analysis failed")
            yield _sse_data({"type": "error", "message": "Unable to analyze this area"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/network/bbox")
async def fetch_network_by_bbox(
    north: float,
    south: float,
    east: float,
    west: float,
    network_type: str = "drive",
    _permit: None = Depends(guard_expensive_request),
):
    """
    Fetch street network using query parameters (GET alternative).
    """
    try:
        bbox = BoundingBox(north=north, south=south, east=east, west=west)
        request = StreetNetworkRequest(bbox=bbox, network_type=network_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return await fetch_street_network(request, _permit)


# =============================================================================
# City Partitioning Endpoints
# =============================================================================


@dataclass
class CachedPartition:
    partition: object
    graph: object
    created_at: float


class PartitionStore:
    """Small TTL/LRU optimization cache; routing does not depend on it."""

    def __init__(self, max_entries: int, ttl_seconds: int) -> None:
        self._entries: OrderedDict[str, CachedPartition] = OrderedDict()
        self._max_entries = max(1, max_entries)
        self._ttl_seconds = max(1, ttl_seconds)
        self._lock = threading.RLock()

    @staticmethod
    def key(bbox: BoundingBox, boundary=None) -> str:
        bbox_key = "_".join(
            str(round(value, 6)) for value in (bbox.north, bbox.south, bbox.east, bbox.west)
        )
        if boundary is None:
            return bbox_key
        boundary_json = json.dumps(boundary.model_dump(), sort_keys=True, separators=(",", ":"))
        return f"{bbox_key}_{hashlib.sha256(boundary_json.encode()).hexdigest()[:16]}"

    def put(self, partition: object, graph: object, bbox: BoundingBox) -> None:
        with self._lock:
            key = self.key(bbox, getattr(partition, "boundary", None))
            self._entries[key] = CachedPartition(partition, graph, time.monotonic())
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def find(self, origin: Coordinates, destination: Coordinates) -> CachedPartition | None:
        now = time.monotonic()
        with self._lock:
            for key, cached in list(self._entries.items()):
                if now - cached.created_at > self._ttl_seconds:
                    del self._entries[key]
                    continue
                bbox = cached.partition.bbox
                inside_bounds = (
                    bbox.west <= origin.lon <= bbox.east
                    and bbox.south <= origin.lat <= bbox.north
                    and bbox.west <= destination.lon <= bbox.east
                    and bbox.south <= destination.lat <= bbox.north
                )
                boundary = cached.partition.boundary
                inside_boundary = boundary is None or (
                    shape(boundary.model_dump()).covers(Point(origin.lon, origin.lat))
                    and shape(boundary.model_dump()).covers(Point(destination.lon, destination.lat))
                )
                if inside_bounds and inside_boundary:
                    self._entries.move_to_end(key)
                    return cached
        return None

    def get_exact(self, bbox: BoundingBox, boundary=None) -> CachedPartition | None:
        with self._lock:
            cache_key = self.key(bbox, boundary)
            cached = self._entries.get(cache_key)
            if cached is None:
                return None
            if time.monotonic() - cached.created_at > self._ttl_seconds:
                del self._entries[cache_key]
                return None
            return cached


partition_store = PartitionStore(
    settings.partition_cache_max_entries,
    settings.partition_cache_ttl_seconds,
)


def run_partition_sync(
    bbox: BoundingBox,
    boundary,
    target_size: float,
    min_area: float,
    max_area: float,
    num_sectors: int,
    enforce_constraints: bool,
    arterial_road_types: set[str],
    traffic_observations,
    access_targets,
    access_dataset_source: str | None,
    access_dataset_complete: bool,
    progress_queue: queue.Queue,
    cancel_event: threading.Event | None = None,
):
    """
    Run city partitioning synchronously in a thread.
    """
    import asyncio

    def progress_callback(progress: PartitionProgress):
        _raise_if_cancelled(cancel_event)
        try:
            progress_queue.put_nowait(
                {
                    "type": "progress",
                    "stage": progress.stage,
                    "percent": progress.percent,
                    "message": progress.message,
                    "current_superblock": progress.current_superblock,
                    "total_superblocks": progress.total_superblocks,
                }
            )
            logger.info(
                "Partition progress: %s %s%% - %s",
                progress.stage,
                progress.percent,
                progress.message,
            )
        except queue.Full:
            pass

    _raise_if_cancelled(cancel_event)
    logger.info(
        "Partition thread started (target_size=%.2f min_area=%.2f max_area=%.2f)",
        target_size,
        min_area,
        max_area,
    )

    loop = asyncio.new_event_loop()
    try:
        # Fetch graph
        graph = loop.run_until_complete(get_street_network_graph(bbox, boundary=boundary))
        traffic_evidence = apply_traffic_observations_to_graph(graph, traffic_observations)

        # Create partitioner
        partitioner = CityPartitioner(
            graph=graph,
            bbox=bbox,
            boundary=boundary,
            target_size_ha=target_size,
            min_area_ha=min_area,
            max_area_ha=max_area,
            num_sectors=num_sectors,
            arterial_road_types=arterial_road_types,
            enforce_constraints=enforce_constraints,
            traffic_evidence=traffic_evidence,
            access_targets=access_targets,
            access_dataset_source=access_dataset_source,
            access_dataset_complete=access_dataset_complete,
            progress_callback=progress_callback,
        )

        # Run partitioning
        start_time = time.time()
        partition = partitioner.partition()
        elapsed = time.time() - start_time
        network = estimate_traffic(graph_to_street_network(graph, bbox, boundary=boundary))
        network = apply_traffic_observations_to_network(network, traffic_observations)
        network.metadata["measured_edge_coverage_percent"] = traffic_evidence[
            "measured_edge_coverage_percent"
        ]

        logger.info(
            "Partition completed in %.1fs (superblocks=%s coverage=%.1f%%)",
            elapsed,
            partition.total_superblocks,
            partition.coverage_percent,
        )

        return {
            "partition": partition,
            "graph": graph,
            "processing_time": elapsed,
            "network": network,
        }

    finally:
        loop.close()


@router.post("/partition", response_model=PartitionResponse)
async def partition_city(
    request: PartitionRequest,
    _permit: None = Depends(guard_expensive_request),
):
    """
    Partition a city area into superblocks with enforced enter-exit constraints.

    This is the main endpoint for the new superblock system. It:
    1. Identifies arterial roads as boundaries
    2. Creates superblock cells from enclosed areas
    3. Enforces the enter-exit same-side constraint using graph algorithms
    4. Returns a complete city partition with all modifications

    Returns:
        PartitionResponse with superblocks, arterial network, and statistics
    """
    try:
        logger.info(
            "Received /partition request (target=%.2f min=%.2f max=%.2f sectors=%d)",
            request.target_size_hectares,
            request.min_area_hectares,
            request.max_area_hectares,
            request.num_sectors,
        )

        progress_queue = queue.Queue(maxsize=100)
        loop = asyncio.get_running_loop()

        result = await loop.run_in_executor(
            analysis_executor,
            run_partition_sync,
            request.bbox,
            request.boundary,
            request.target_size_hectares,
            request.min_area_hectares,
            request.max_area_hectares,
            request.num_sectors,
            request.enforce_constraints,
            {road_type.value for road_type in request.arterial_road_types},
            request.traffic_observations,
            request.access_targets,
            request.access_dataset_source,
            request.access_dataset_complete,
            progress_queue,
            None,
        )

        partition = result["partition"]

        # Cache the result for routing
        partition_store.put(partition, result["graph"], request.bbox)

        return {
            "partition": partition.model_dump(),
            "street_network": result["network"].model_dump(),
            "processing_time_seconds": result["processing_time"],
        }

    except ValueError as e:
        logger.warning("Validation error in /partition: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unhandled error in /partition")
        raise HTTPException(status_code=500, detail="Unable to partition this area") from e


@router.post("/partition/stream")
async def partition_city_stream(
    request: PartitionRequest,
    http_request: Request,
    _permit: None = Depends(guard_expensive_request),
):
    """
    Partition a city with streaming progress updates.

    Returns Server-Sent Events (SSE) with progress updates followed by final results.
    """
    progress_queue = queue.Queue(maxsize=100)

    async def generate():
        loop = asyncio.get_running_loop()
        cancel_event = threading.Event()
        stream_task = asyncio.current_task()
        if stream_task is not None:
            stream_task.add_done_callback(lambda _task: cancel_event.set())
        future = loop.run_in_executor(
            analysis_executor,
            run_partition_sync,
            request.bbox,
            request.boundary,
            request.target_size_hectares,
            request.min_area_hectares,
            request.max_area_hectares,
            request.num_sectors,
            request.enforce_constraints,
            {road_type.value for road_type in request.arterial_road_types},
            request.traffic_observations,
            request.access_targets,
            request.access_dataset_source,
            request.access_dataset_complete,
            progress_queue,
            cancel_event,
        )
        logger.info("Partition streaming started")

        while not future.done():
            if await http_request.is_disconnected():
                cancel_event.set()
                future.cancel()
                logger.info("Partition stream client disconnected")
                return
            try:
                progress = progress_queue.get_nowait()
                yield _sse_data(progress)
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue

        # Drain remaining progress
        while True:
            try:
                progress = progress_queue.get_nowait()
                yield _sse_data(progress)
            except queue.Empty:
                break

        try:
            result = await future
            partition = result["partition"]
            partition_store.put(partition, result["graph"], request.bbox)

            final_data = {
                "type": "complete",
                "partition": partition.model_dump(),
                "street_network": result["network"].model_dump(),
                "processing_time_seconds": result["processing_time"],
            }
            yield _sse_data(final_data)
        except Exception:
            logger.exception("Partition stream failed")
            yield _sse_data({"type": "error", "message": "Unable to partition this area"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/partition/review", response_model=CityPartition)
async def review_partition(
    request: PartitionReviewRequest,
    _permit: None = Depends(guard_expensive_request),
):
    """Apply post-analysis professional attestations to this exact plan digest."""
    partition = request.partition.model_copy(deep=True)
    if not partition.plan_id or compute_plan_id(partition) != partition.plan_id:
        raise HTTPException(
            status_code=400,
            detail="The supplied plan ID does not match the partition contents",
        )

    validated_target_count = sum(
        superblock.access_target_count for superblock in partition.superblocks
    )
    partition.readiness = assess_plan_readiness(
        evidence=partition.evidence,
        has_boundary=partition.boundary is not None,
        modeled_directional_validation_passed=all(
            superblock.modeled_directional_validation_passed for superblock in partition.superblocks
        )
        and bool(partition.superblocks),
        validated_target_count=validated_target_count,
        total_unreachable_targets=partition.total_unreachable_access_targets,
        review_types={review.review_type for review in request.review_attestations},
    )
    return partition


# =============================================================================
# Routing Endpoints
# =============================================================================


@router.post("/route", response_model=RouteResult)
async def compute_route(
    request: RouteRequest,
    _permit: None = Depends(guard_expensive_request),
):
    """
    Compute a route that respects superblock constraints.

    The route will:
    - Use arterial roads for main travel
    - Only enter superblocks for origin/destination
    - Respect one-way conversions and modal filters

    Requires a partition to have been computed first for this area.
    """
    try:
        partition = request.partition
        cached = (
            partition_store.get_exact(partition.bbox, partition.boundary) if partition else None
        )
        if partition is None:
            cached = partition_store.find(request.origin, request.destination)
            partition = cached.partition if cached else None

        if partition is None:
            return RouteResult(
                success=False,
                blocked_reason=(
                    "No partition supplied for this area. Run /partition first and "
                    "include its partition in the route request."
                ),
            )

        bbox = partition.bbox
        if not (
            bbox.west <= request.origin.lon <= bbox.east
            and bbox.south <= request.origin.lat <= bbox.north
            and bbox.west <= request.destination.lon <= bbox.east
            and bbox.south <= request.destination.lat <= bbox.north
        ):
            return RouteResult(
                success=False,
                blocked_reason="Origin and destination must be inside the partition bounds.",
            )
        if partition.boundary is not None:
            boundary_geometry = shape(partition.boundary.model_dump())
            if not (
                boundary_geometry.covers(Point(request.origin.lon, request.origin.lat))
                and boundary_geometry.covers(
                    Point(request.destination.lon, request.destination.lat)
                )
            ):
                return RouteResult(
                    success=False,
                    blocked_reason=(
                        "Origin and destination must be inside the administrative boundary."
                    ),
                )

        graph = (
            cached.graph
            if cached
            else await get_street_network_graph(bbox, boundary=partition.boundary)
        )
        if cached is None:
            partition_store.put(partition, graph, bbox)

        # Create router
        router_instance = SuperblockRouter(
            graph=graph,
            partition=partition,
        )

        # Compute route
        result = router_instance.route(request)
        return result

    except Exception:
        logger.exception("Error computing route")
        return RouteResult(
            success=False,
            blocked_reason="Unable to compute a route for these points.",
        )


@router.get("/route/test")
async def test_route_get(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    respect_superblocks: bool = True,
    _permit: None = Depends(guard_expensive_request),
):
    """
    Test route computation (GET alternative for easy testing).
    """
    request = RouteRequest(
        origin=Coordinates(lat=origin_lat, lon=origin_lon),
        destination=Coordinates(lat=dest_lat, lon=dest_lon),
        respect_superblocks=respect_superblocks,
    )
    return await compute_route(request, _permit)


# =============================================================================
# Size Optimization Endpoint
# =============================================================================


@router.get("/optimize/size")
async def get_optimal_size(
    north: float,
    south: float,
    east: float,
    west: float,
    population_density: float | None = Query(default=None, ge=0),
    _permit: None = Depends(guard_expensive_request),
):
    """
    Calculate optimal superblock size for an area.

    Based on Barcelona Superilles research with adjustments for:
    - Population density
    - Street grid characteristics
    """
    try:
        bbox = BoundingBox(north=north, south=south, east=east, west=west)

        # Get graph for grid analysis
        graph = await get_street_network_graph(bbox)

        # Get latitude for solar considerations
        latitude = (north + south) / 2

        recommendation = calculate_optimal_superblock_size(
            graph=graph,
            population_density=population_density,
            latitude=latitude,
        )

        return {
            "min_side_m": recommendation.min_side_m,
            "max_side_m": recommendation.max_side_m,
            "optimal_side_m": recommendation.optimal_side_m,
            "min_area_ha": recommendation.min_area_ha,
            "max_area_ha": recommendation.max_area_ha,
            "optimal_area_ha": recommendation.optimal_area_ha,
            "grid_orientation_deg": recommendation.grid_orientation_deg,
            "rationale": recommendation.rationale,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Error calculating optimal size")
        raise HTTPException(status_code=500, detail="Unable to calculate an optimal size") from e
