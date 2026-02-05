from fastapi import APIRouter, HTTPException
from typing import Optional

from app.models.schemas import (
    StreetNetworkRequest,
    StreetNetworkResponse,
    AnalysisRequest,
    AnalysisResponse,
    BoundingBox,
)
from app.services.osm_service import get_street_network
from app.services.traffic import estimate_traffic

router = APIRouter()


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


@router.post("/analyze")
async def analyze_superblocks(request: AnalysisRequest):
    """
    Analyze an area for potential superblocks.

    Runs the specified detection algorithms and returns candidate superblocks
    with their scores and properties.
    """
    # Placeholder - will be implemented with detection algorithms
    return {
        "message": "Analysis endpoint - implementation pending",
        "bbox": request.bbox.model_dump(),
        "algorithms": request.algorithms,
    }


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
