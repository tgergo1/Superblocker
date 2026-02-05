from fastapi import APIRouter, HTTPException, Query
import httpx

from app.core.config import get_settings
from app.models.schemas import SearchResponse, SearchResult, BoundingBox
from app.services.cache_service import get_cache_service

router = APIRouter()
settings = get_settings()


@router.get("/search", response_model=SearchResponse)
async def search_places(
    q: str = Query(..., min_length=2, description="Search query for city/place name"),
    limit: int = Query(default=5, ge=1, le=20, description="Maximum number of results"),
):
    """
    Search for cities and places using Nominatim geocoding.

    Uses caching to avoid re-fetching the same search results.

    Returns a list of matching places with their coordinates and bounding boxes.
    """
    # Check cache first
    cache_params = {"query": q.lower().strip(), "limit": limit}
    cache_service = get_cache_service()
    cached_data = cache_service.get("search", cache_params)

    if cached_data is not None:
        return SearchResponse(results=[SearchResult(**r) for r in cached_data])
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{settings.nominatim_url}/search",
                params={
                    "q": q,
                    "format": "json",
                    "limit": limit,
                    "addressdetails": 1,
                    "extratags": 1,
                },
                headers={"User-Agent": settings.nominatim_user_agent},
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Nominatim request timed out")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Nominatim error: {str(e)}")

    data = response.json()

    results = []
    for item in data:
        # Parse bounding box (Nominatim returns [south, north, west, east])
        bbox = item.get("boundingbox", [])
        if len(bbox) >= 4:
            bounding_box = BoundingBox(
                south=float(bbox[0]),
                north=float(bbox[1]),
                west=float(bbox[2]),
                east=float(bbox[3]),
            )
        else:
            # Fallback: create small bbox around point
            lat, lon = float(item["lat"]), float(item["lon"])
            bounding_box = BoundingBox(
                south=lat - 0.01,
                north=lat + 0.01,
                west=lon - 0.01,
                east=lon + 0.01,
            )

        results.append(
            SearchResult(
                place_id=item["place_id"],
                osm_type=item.get("osm_type", ""),
                osm_id=item.get("osm_id", 0),
                display_name=item["display_name"],
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                boundingbox=bounding_box,
                type=item.get("type", "unknown"),
                importance=item.get("importance", 0.0),
            )
        )

    # Cache the results
    cache_service.set(
        "search",
        cache_params,
        [r.model_dump() for r in results],
        ttl_seconds=settings.cache_search_ttl_seconds,
    )

    return SearchResponse(results=results)


@router.get("/search/reverse")
async def reverse_geocode(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    """
    Reverse geocode coordinates to get place information.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{settings.nominatim_url}/reverse",
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "json",
                },
                headers={"User-Agent": settings.nominatim_user_agent},
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Nominatim error: {str(e)}")

    return response.json()
