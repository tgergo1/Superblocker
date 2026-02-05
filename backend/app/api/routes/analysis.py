from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import queue
import logging
import time

from app.models.schemas import (
    StreetNetworkRequest,
    StreetNetworkResponse,
    AnalysisRequest,
    AnalysisResponse,
    BoundingBox,
    PartitionRequest,
    PartitionResponse,
    PartitionProgress,
    RouteRequest,
    RouteResult,
    ValidationRequest,
    ValidationResult,
    Coordinates,
)
from app.services.osm_service import get_street_network, get_street_network_graph
from app.services.traffic import estimate_traffic
from app.services.detection.superblock_analyzer import SuperblockAnalyzer
from app.services.partitioning.city_partitioner import CityPartitioner
from app.services.routing.superblock_router import SuperblockRouter
from app.services.sizing.size_optimizer import calculate_optimal_superblock_size

router = APIRouter()
logger = logging.getLogger(__name__)

# Thread pool for CPU-bound analysis
analysis_executor = ThreadPoolExecutor(max_workers=2)


@router.post("/network", response_model=StreetNetworkResponse)
async def fetch_street_network(request: StreetNetworkRequest):
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
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching network: {str(e)}")


def run_analysis_sync(bbox: BoundingBox, min_area: float, max_area: float, progress_queue: queue.Queue):
    """
    Run the analysis synchronously in a thread.
    Uses a thread-safe queue for progress updates.
    """
    def progress_callback(stage: str, percent: int, message: str):
        try:
            progress_queue.put_nowait({
                "type": "progress",
                "stage": stage,
                "percent": percent,
                "message": message,
            })
            logger.info("Progress update: %s %s%% - %s", stage, percent, message)
        except queue.Full:
            pass

    logger.info(
        "Analysis thread started (min_area=%.2f max_area=%.2f bbox=%s)",
        min_area,
        max_area,
        bbox.model_dump(),
    )
    analyzer = SuperblockAnalyzer(min_area=min_area, max_area=max_area)

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


@router.post("/analyze")
async def analyze_superblocks(request: AnalysisRequest):
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
        progress_queue = queue.Queue()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            analysis_executor,
            run_analysis_sync,
            request.bbox,
            request.min_area_hectares,
            request.max_area_hectares,
            progress_queue,
        )

        response = {
            "candidates": result.get("candidates", []),
            "total_found": len(result.get("candidates", [])),
            "bbox": request.bbox.model_dump(),
            "network_stats": result.get("network_stats", {}),
            "parameters": {
                "min_area_hectares": request.min_area_hectares,
                "max_area_hectares": request.max_area_hectares,
                "algorithms": request.algorithms,
            }
        }
        logger.info("Completed /analyze request (total_found=%s)", response["total_found"])
        return response
    except ValueError as e:
        logger.warning("Validation error in /analyze: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Unhandled error in /analyze")
        raise HTTPException(status_code=500, detail=f"Error analyzing area: {str(e)}")


@router.post("/analyze/stream")
async def analyze_superblocks_stream(request: AnalysisRequest):
    """
    Analyze an area for potential superblocks with streaming progress updates.

    Returns Server-Sent Events (SSE) with progress updates followed by final results.
    """
    # Thread-safe queue for cross-thread communication
    progress_queue = queue.Queue()
    result_holder = {"result": None, "error": None, "done": False}

    def run_in_thread():
        """Run the analysis in a separate thread."""
        try:
            logger.info(
                "Stream analysis thread starting (min_area=%.2f max_area=%.2f bbox=%s)",
                request.min_area_hectares,
                request.max_area_hectares,
                request.bbox.model_dump(),
            )
            result = run_analysis_sync(
                request.bbox,
                request.min_area_hectares,
                request.max_area_hectares,
                progress_queue,
            )
            result_holder["result"] = result
            logger.info("Stream analysis thread completed")
        except Exception as e:
            logger.exception("Stream analysis thread error")
            result_holder["error"] = str(e)
        finally:
            result_holder["done"] = True

    async def generate():
        # Start analysis in background thread
        thread = threading.Thread(target=run_in_thread)
        thread.start()
        logger.info("Streaming response started")

        # Stream progress updates
        last_heartbeat = time.time()
        while not result_holder["done"]:
            try:
                # Non-blocking check for progress
                progress = progress_queue.get_nowait()
                logger.info("Streaming progress event: %s %s%%", progress.get("stage"), progress.get("percent"))
                yield f"data: {json.dumps(progress)}\n\n"
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
                logger.info("Streaming final progress event: %s %s%%", progress.get("stage"), progress.get("percent"))
                yield f"data: {json.dumps(progress)}\n\n"
            except queue.Empty:
                break

        # Wait for thread to complete
        thread.join(timeout=1.0)

        # Send final result
        if result_holder["error"]:
            logger.error("Streaming analysis failed: %s", result_holder["error"])
            yield f"data: {json.dumps({'type': 'error', 'message': result_holder['error']})}\n\n"
        elif result_holder["result"]:
            result = result_holder["result"]
            final_data = {
                "type": "complete",
                "candidates": result.get("candidates", []),
                "total_found": len(result.get("candidates", [])),
                "network_stats": result.get("network_stats", {}),
            }
            logger.info("Streaming analysis complete (total_found=%s)", final_data["total_found"])
            yield f"data: {json.dumps(final_data)}\n\n"
        else:
            logger.error("Streaming analysis completed with no result")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Analysis completed with no result'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/network/bbox")
async def fetch_network_by_bbox(
    north: float,
    south: float,
    east: float,
    west: float,
    network_type: str = "drive",
):
    """
    Fetch street network using query parameters (GET alternative).
    """
    bbox = BoundingBox(north=north, south=south, east=east, west=west)
    request = StreetNetworkRequest(bbox=bbox, network_type=network_type)
    return await fetch_street_network(request)


# =============================================================================
# City Partitioning Endpoints
# =============================================================================

# Storage for partition results (in production, use proper storage)
partition_cache: dict[str, dict] = {}


def run_partition_sync(
    bbox: BoundingBox,
    target_size: float,
    min_area: float,
    max_area: float,
    num_sectors: int,
    enforce_constraints: bool,
    progress_queue: queue.Queue,
):
    """
    Run city partitioning synchronously in a thread.
    """
    import asyncio

    def progress_callback(progress: PartitionProgress):
        try:
            progress_queue.put_nowait({
                "type": "progress",
                "stage": progress.stage,
                "percent": progress.percent,
                "message": progress.message,
                "current_superblock": progress.current_superblock,
                "total_superblocks": progress.total_superblocks,
            })
            logger.info("Partition progress: %s %s%% - %s", progress.stage, progress.percent, progress.message)
        except queue.Full:
            pass

    logger.info(
        "Partition thread started (target_size=%.2f min_area=%.2f max_area=%.2f)",
        target_size, min_area, max_area,
    )

    loop = asyncio.new_event_loop()
    try:
        # Fetch graph
        graph = loop.run_until_complete(get_street_network_graph(bbox))

        # Create partitioner
        partitioner = CityPartitioner(
            graph=graph,
            bbox=bbox,
            target_size_ha=target_size,
            min_area_ha=min_area,
            max_area_ha=max_area,
            num_sectors=num_sectors,
            progress_callback=progress_callback if enforce_constraints else None,
        )

        # Run partitioning
        start_time = time.time()
        partition = partitioner.partition()
        elapsed = time.time() - start_time

        logger.info(
            "Partition completed in %.1fs (superblocks=%s coverage=%.1f%%)",
            elapsed, partition.total_superblocks, partition.coverage_percent,
        )

        return {
            "partition": partition,
            "graph": graph,
            "processing_time": elapsed,
        }

    finally:
        loop.close()


@router.post("/partition")
async def partition_city(request: PartitionRequest):
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

        progress_queue = queue.Queue()
        loop = asyncio.get_event_loop()

        result = await loop.run_in_executor(
            analysis_executor,
            run_partition_sync,
            request.bbox,
            request.target_size_hectares,
            request.min_area_hectares,
            request.max_area_hectares,
            request.num_sectors,
            request.enforce_constraints,
            progress_queue,
        )

        partition = result["partition"]

        # Cache the result for routing
        cache_key = f"{request.bbox.north}_{request.bbox.south}_{request.bbox.east}_{request.bbox.west}"
        partition_cache[cache_key] = {
            "partition": partition,
            "graph": result["graph"],
        }

        # Get street network for response
        network = await get_street_network(request.bbox)

        return {
            "partition": partition.model_dump(),
            "street_network": network.model_dump(),
            "processing_time_seconds": result["processing_time"],
        }

    except ValueError as e:
        logger.warning("Validation error in /partition: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Unhandled error in /partition")
        raise HTTPException(status_code=500, detail=f"Error partitioning city: {str(e)}")


@router.post("/partition/stream")
async def partition_city_stream(request: PartitionRequest):
    """
    Partition a city with streaming progress updates.

    Returns Server-Sent Events (SSE) with progress updates followed by final results.
    """
    progress_queue = queue.Queue()
    result_holder = {"result": None, "error": None, "done": False}

    def run_in_thread():
        try:
            result = run_partition_sync(
                request.bbox,
                request.target_size_hectares,
                request.min_area_hectares,
                request.max_area_hectares,
                request.num_sectors,
                request.enforce_constraints,
                progress_queue,
            )
            result_holder["result"] = result
        except Exception as e:
            logger.exception("Partition stream thread error")
            result_holder["error"] = str(e)
        finally:
            result_holder["done"] = True

    async def generate():
        thread = threading.Thread(target=run_in_thread)
        thread.start()
        logger.info("Partition streaming started")

        while not result_holder["done"]:
            try:
                progress = progress_queue.get_nowait()
                yield f"data: {json.dumps(progress)}\n\n"
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue

        # Drain remaining progress
        while True:
            try:
                progress = progress_queue.get_nowait()
                yield f"data: {json.dumps(progress)}\n\n"
            except queue.Empty:
                break

        thread.join(timeout=1.0)

        if result_holder["error"]:
            yield f"data: {json.dumps({'type': 'error', 'message': result_holder['error']})}\n\n"
        elif result_holder["result"]:
            partition = result_holder["result"]["partition"]

            # Cache for routing
            cache_key = f"{request.bbox.north}_{request.bbox.south}_{request.bbox.east}_{request.bbox.west}"
            partition_cache[cache_key] = {
                "partition": partition,
                "graph": result_holder["result"]["graph"],
            }

            final_data = {
                "type": "complete",
                "partition": partition.model_dump(),
                "processing_time_seconds": result_holder["result"]["processing_time"],
            }
            yield f"data: {json.dumps(final_data)}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Partition completed with no result'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# =============================================================================
# Routing Endpoints
# =============================================================================


@router.post("/route", response_model=RouteResult)
async def compute_route(request: RouteRequest):
    """
    Compute a route that respects superblock constraints.

    The route will:
    - Use arterial roads for main travel
    - Only enter superblocks for origin/destination
    - Respect one-way conversions and modal filters

    Requires a partition to have been computed first for this area.
    """
    try:
        # Find cached partition that contains origin and destination
        origin_point = (request.origin.lon, request.origin.lat)
        dest_point = (request.destination.lon, request.destination.lat)

        matching_cache = None
        for cache_key, cached in partition_cache.items():
            bbox = cached["partition"].bbox
            if (bbox.west <= origin_point[0] <= bbox.east and
                bbox.south <= origin_point[1] <= bbox.north and
                bbox.west <= dest_point[0] <= bbox.east and
                bbox.south <= dest_point[1] <= bbox.north):
                matching_cache = cached
                break

        if matching_cache is None:
            return RouteResult(
                success=False,
                blocked_reason="No partition found for this area. Run /partition first.",
            )

        # Create router
        router_instance = SuperblockRouter(
            graph=matching_cache["graph"],
            partition=matching_cache["partition"],
        )

        # Compute route
        result = router_instance.route(request)
        return result

    except Exception as e:
        logger.exception("Error computing route")
        return RouteResult(
            success=False,
            blocked_reason=f"Error computing route: {str(e)}",
        )


@router.get("/route/test")
async def test_route_get(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    respect_superblocks: bool = True,
):
    """
    Test route computation (GET alternative for easy testing).
    """
    request = RouteRequest(
        origin=Coordinates(lat=origin_lat, lon=origin_lon),
        destination=Coordinates(lat=dest_lat, lon=dest_lon),
        respect_superblocks=respect_superblocks,
    )
    return await compute_route(request)


# =============================================================================
# Size Optimization Endpoint
# =============================================================================


@router.get("/optimize/size")
async def get_optimal_size(
    north: float,
    south: float,
    east: float,
    west: float,
    population_density: Optional[float] = None,
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

    except Exception as e:
        logger.exception("Error calculating optimal size")
        raise HTTPException(status_code=500, detail=str(e))
