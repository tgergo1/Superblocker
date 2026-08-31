import httpx
from fastapi import APIRouter, HTTPException, Query

from app.core.config import get_settings
from app.models.schemas import AdministrativeBoundary, BoundingBox, SearchResponse, SearchResult
from app.services.cache_service import get_cache_service
from app.services.nominatim_service import nominatim_get

router = APIRouter()
settings = get_settings()


@router.get("/search", response_model=SearchResponse)
async def search_places(
    q: str = Query(
        ...,
        min_length=2,
        max_length=200,
        description="Search query submitted explicitly by the user",
    ),
    limit: int = Query(default=5, ge=1, le=20, description="Maximum number of results"),
):
    """
    Search for cities and places using Nominatim geocoding.

    Uses caching to avoid re-fetching the same search results.

    Returns matching places with their exact administrative polygon when the
    upstream OSM object provides one. The bounding box is retained for viewport
    fitting and as a clearly labeled fallback only.
    """
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(
            status_code=422,
            detail="Search query must contain at least two non-whitespace characters",
        )

    # Check cache first
    cache_params = {"query": query.lower(), "limit": limit, "geometry_version": 1}
    cache_service = get_cache_service()
    cached_data = cache_service.get("search", cache_params)

    if cached_data is not None:
        return SearchResponse(results=[SearchResult(**r) for r in cached_data])
    try:
        response = await nominatim_get(
            "/search",
            params={
                "q": query,
                "format": "json",
                "limit": limit,
                "addressdetails": 1,
                "extratags": 1,
                "polygon_geojson": 1,
            },
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Place search timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Place search is temporarily unavailable"
        ) from exc

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
                south=max(-90, lat - 0.01),
                north=min(90, lat + 0.01),
                west=max(-180, lon - 0.01),
                east=min(180, lon + 0.01),
            )

        boundary = None
        boundary_source = "bounding_box_fallback"
        geojson = item.get("geojson")
        if isinstance(geojson, dict) and geojson.get("type") in {"Polygon", "MultiPolygon"}:
            try:
                boundary = AdministrativeBoundary.model_validate(geojson)
                boundary_source = "nominatim"
            except ValueError:
                # Search remains usable for malformed/non-area upstream objects;
                # downstream analysis will state that it used the bbox fallback.
                boundary = None

        results.append(
            SearchResult(
                place_id=item["place_id"],
                osm_type=item.get("osm_type", ""),
                osm_id=item.get("osm_id", 0),
                display_name=item["display_name"],
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                boundingbox=bounding_box,
                boundary=boundary,
                boundary_source=boundary_source,
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
    cache_params = {"lat": round(lat, 5), "lon": round(lon, 5)}
    cache_service = get_cache_service()
    cached_data = cache_service.get("reverse_search", cache_params)
    if cached_data is not None:
        return cached_data

    try:
        response = await nominatim_get(
            "/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Reverse geocoding timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Reverse geocoding is temporarily unavailable"
        ) from exc

    data = response.json()
    cache_service.set(
        "reverse_search",
        cache_params,
        data,
        ttl_seconds=settings.cache_search_ttl_seconds,
    )
    return data
