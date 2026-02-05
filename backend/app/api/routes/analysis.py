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
)
from app.services.osm_service import get_street_network
from app.services.traffic import estimate_traffic
from app.services.detection.superblock_analyzer import SuperblockAnalyzer

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
