"""
Cache management endpoints.

Provides API endpoints for viewing cache statistics and managing cache entries.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from app.services.cache_service import get_cache_service

router = APIRouter()


@router.get("/cache/stats")
async def get_cache_stats():
    """
    Get cache statistics.

    Returns:
        Cache statistics including hit/miss rates, entry counts, and size.
    """
    cache_service = get_cache_service()
    stats = cache_service.get_stats()
    return {
        "enabled": cache_service.enabled,
        "cache_dir": str(cache_service.cache_dir),
        "default_ttl_seconds": cache_service.default_ttl,
        "stats": stats.to_dict(),
    }


@router.delete("/cache")
async def clear_cache(
    cache_type: Optional[str] = Query(
        default=None,
        description="Type of cache to clear ('network', 'analysis', 'search'). "
        "If not provided, clears all cache.",
    ),
):
    """
    Clear cache entries.

    Args:
        cache_type: Optional type of cache to clear. If not provided, clears all.

    Returns:
        Number of cache entries cleared.
    """
    cache_service = get_cache_service()

    if not cache_service.enabled:
        return {"message": "Cache is disabled", "cleared": 0}

    count = cache_service.invalidate(cache_type=cache_type)
    return {
        "message": f"Cleared {count} cache entries",
        "cleared": count,
        "cache_type": cache_type or "all",
    }


@router.post("/cache/cleanup")
async def cleanup_expired():
    """
    Remove expired cache entries.

    Returns:
        Number of expired entries removed.
    """
    cache_service = get_cache_service()

    if not cache_service.enabled:
        return {"message": "Cache is disabled", "removed": 0}

    count = cache_service.cleanup_expired()
    return {
        "message": f"Removed {count} expired cache entries",
        "removed": count,
    }
