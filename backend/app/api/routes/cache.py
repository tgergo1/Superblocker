"""
Cache management endpoints.

Provides API endpoints for viewing cache statistics and managing cache entries.
"""

import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.core.config import get_settings
from app.services.cache_service import get_cache_service

router = APIRouter()
settings = get_settings()


async def require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    """Protect destructive maintenance operations."""
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=503,
            detail="Cache maintenance is disabled until ADMIN_API_KEY is configured.",
        )
    if x_admin_key is None or not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=403, detail="Invalid administrator credentials")


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
        "default_ttl_seconds": cache_service.default_ttl,
        "stats": stats.to_dict(),
    }


@router.delete("/cache")
async def clear_cache(
    cache_type: Literal["network", "analysis", "search", "reverse_search"] | None = Query(
        default=None,
        description="Type of cache to clear ('network', 'analysis', 'search'). "
        "If not provided, clears all cache.",
    ),
    _admin: None = Depends(require_admin_key),
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
async def cleanup_expired(_admin: None = Depends(require_admin_key)):
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
