"""
Caching service for Superblocker.

Provides a robust, configurable caching system for:
- Street network data downloaded from OSM
- Computed analysis results (superblock detection)
- Search/geocoding results

Features:
- File-based cache storage with JSON serialization
- Configurable TTL (time-to-live) for cache entries
- Cache key generation based on request parameters
- Thread-safe operations
- Cache statistics and management
"""

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Represents a cached item with metadata."""

    data: Any
    created_at: float
    ttl_seconds: int
    cache_key: str
    cache_type: str

    def is_expired(self) -> bool:
        """Check if the cache entry has expired."""
        if self.ttl_seconds <= 0:
            return False  # TTL of 0 or negative means never expire
        return time.time() > (self.created_at + self.ttl_seconds)


@dataclass
class CacheStats:
    """Cache statistics."""

    hits: int = 0
    misses: int = 0
    entries_count: int = 0
    total_size_bytes: int = 0
    oldest_entry_age_seconds: float = 0.0
    cache_types: dict = None

    def __post_init__(self):
        if self.cache_types is None:
            self.cache_types = {}

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / (self.hits + self.misses), 3)
            if (self.hits + self.misses) > 0
            else 0.0,
            "entries_count": self.entries_count,
            "total_size_mb": round(self.total_size_bytes / (1024 * 1024), 2),
            "oldest_entry_age_hours": round(self.oldest_entry_age_seconds / 3600, 1),
            "cache_types": self.cache_types,
        }


class CacheService:
    """
    File-based caching service with TTL support.

    Thread-safe implementation suitable for async FastAPI applications.
    """

    def __init__(
        self,
        cache_dir: str = "cache",
        default_ttl_seconds: int = 86400,  # 24 hours
        enabled: bool = True,
    ):
        """
        Initialize the cache service.

        Args:
            cache_dir: Directory for cache files (relative to app root or absolute)
            default_ttl_seconds: Default time-to-live for cache entries
            enabled: Whether caching is enabled
        """
        self.cache_dir = Path(cache_dir)
        self.default_ttl = default_ttl_seconds
        self.enabled = enabled
        self._lock = threading.RLock()
        self._stats = CacheStats()

        if self.enabled:
            self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        """Create cache directory if it doesn't exist."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Cache directory ready: %s", self.cache_dir.absolute())
        except OSError as e:
            logger.error("Failed to create cache directory: %s", e)
            self.enabled = False

    def _generate_cache_key(self, cache_type: str, params: dict) -> str:
        """
        Generate a unique cache key from type and parameters.

        Args:
            cache_type: Type of cached data (e.g., 'network', 'analysis', 'search')
            params: Dictionary of parameters that uniquely identify the cached data

        Returns:
            SHA-1 hash string as cache key
        """
        # Sort params for consistent key generation
        sorted_params = json.dumps(params, sort_keys=True, default=str)
        key_string = f"{cache_type}:{sorted_params}"
        return hashlib.sha1(key_string.encode()).hexdigest()

    def _get_cache_path(self, cache_key: str) -> Path:
        """Get the file path for a cache key."""
        return self.cache_dir / f"{cache_key}.json"

    def get(
        self,
        cache_type: str,
        params: dict,
    ) -> Optional[Any]:
        """
        Retrieve data from cache.

        Args:
            cache_type: Type of cached data
            params: Parameters that identify the cached data

        Returns:
            Cached data if found and not expired, None otherwise
        """
        if not self.enabled:
            return None

        cache_key = self._generate_cache_key(cache_type, params)
        cache_path = self._get_cache_path(cache_key)

        with self._lock:
            if not cache_path.exists():
                self._stats.misses += 1
                logger.debug("Cache miss for %s (key=%s)", cache_type, cache_key[:12])
                return None

            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)

                entry = CacheEntry(
                    data=cache_data.get("data"),
                    created_at=cache_data.get("created_at", 0),
                    ttl_seconds=cache_data.get("ttl_seconds", self.default_ttl),
                    cache_key=cache_key,
                    cache_type=cache_type,
                )

                if entry.is_expired():
                    logger.debug(
                        "Cache expired for %s (key=%s)", cache_type, cache_key[:12]
                    )
                    self._stats.misses += 1
                    # Clean up expired entry
                    self._delete_entry(cache_path)
                    return None

                self._stats.hits += 1
                logger.info(
                    "Cache hit for %s (key=%s, age=%.0fs)",
                    cache_type,
                    cache_key[:12],
                    time.time() - entry.created_at,
                )
                return entry.data

            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Cache read error for %s: %s", cache_key[:12], e)
                self._stats.misses += 1
                return None

    def set(
        self,
        cache_type: str,
        params: dict,
        data: Any,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """
        Store data in cache.

        Args:
            cache_type: Type of cached data
            params: Parameters that identify the cached data
            data: Data to cache (must be JSON-serializable)
            ttl_seconds: Optional custom TTL for this entry

        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled:
            return False

        cache_key = self._generate_cache_key(cache_type, params)
        cache_path = self._get_cache_path(cache_key)
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl

        cache_data = {
            "cache_type": cache_type,
            "cache_key": cache_key,
            "params": params,
            "created_at": time.time(),
            "ttl_seconds": ttl,
            "data": data,
        }

        with self._lock:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f)

                logger.info(
                    "Cached %s (key=%s, ttl=%ds)",
                    cache_type,
                    cache_key[:12],
                    ttl,
                )
                return True

            except (TypeError, OSError) as e:
                logger.error("Cache write error for %s: %s", cache_key[:12], e)
                return False

    def _delete_entry(self, cache_path: Path) -> bool:
        """Delete a cache entry file."""
        try:
            cache_path.unlink(missing_ok=True)
            return True
        except OSError as e:
            logger.warning("Failed to delete cache entry: %s", e)
            return False

    def invalidate(
        self,
        cache_type: Optional[str] = None,
        params: Optional[dict] = None,
    ) -> int:
        """
        Invalidate cache entries.

        Args:
            cache_type: If provided with params, invalidate specific entry.
                       If provided alone, invalidate all entries of this type.
            params: Parameters for specific entry invalidation.

        Returns:
            Number of entries invalidated
        """
        if not self.enabled:
            return 0

        with self._lock:
            if cache_type and params:
                # Invalidate specific entry
                cache_key = self._generate_cache_key(cache_type, params)
                cache_path = self._get_cache_path(cache_key)
                if self._delete_entry(cache_path):
                    logger.info("Invalidated cache entry: %s", cache_key[:12])
                    return 1
                return 0

            # Invalidate all or by type
            count = 0
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    if cache_type:
                        # Check if entry matches type
                        with open(cache_file, "r", encoding="utf-8") as f:
                            entry_data = json.load(f)
                        if entry_data.get("cache_type") != cache_type:
                            continue

                    if self._delete_entry(cache_file):
                        count += 1
                except (json.JSONDecodeError, OSError):
                    # Delete corrupted entries
                    if self._delete_entry(cache_file):
                        count += 1

            logger.info(
                "Invalidated %d cache entries (type=%s)", count, cache_type or "all"
            )
            return count

    def cleanup_expired(self) -> int:
        """
        Remove all expired cache entries.

        Returns:
            Number of entries removed
        """
        if not self.enabled:
            return 0

        count = 0
        with self._lock:
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        entry_data = json.load(f)

                    created_at = entry_data.get("created_at", 0)
                    ttl = entry_data.get("ttl_seconds", self.default_ttl)

                    if ttl > 0 and time.time() > (created_at + ttl):
                        if self._delete_entry(cache_file):
                            count += 1

                except (json.JSONDecodeError, OSError):
                    # Delete corrupted entries
                    if self._delete_entry(cache_file):
                        count += 1

        if count > 0:
            logger.info("Cleaned up %d expired cache entries", count)
        return count

    def get_stats(self) -> CacheStats:
        """
        Get cache statistics.

        Returns:
            CacheStats object with current statistics
        """
        if not self.enabled:
            return CacheStats()

        stats = CacheStats(
            hits=self._stats.hits,
            misses=self._stats.misses,
        )

        oldest_time = time.time()
        cache_types: dict = {}

        with self._lock:
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    file_size = cache_file.stat().st_size
                    stats.total_size_bytes += file_size
                    stats.entries_count += 1

                    with open(cache_file, "r", encoding="utf-8") as f:
                        entry_data = json.load(f)

                    cache_type = entry_data.get("cache_type", "unknown")
                    cache_types[cache_type] = cache_types.get(cache_type, 0) + 1

                    created_at = entry_data.get("created_at", time.time())
                    if created_at < oldest_time:
                        oldest_time = created_at

                except (json.JSONDecodeError, OSError):
                    continue

        stats.cache_types = cache_types
        if stats.entries_count > 0:
            stats.oldest_entry_age_seconds = time.time() - oldest_time

        return stats


# Global cache service instance - initialized lazily
_cache_service: Optional[CacheService] = None
_cache_lock = threading.Lock()


def get_cache_service() -> CacheService:
    """Get the global cache service instance."""
    global _cache_service
    if _cache_service is None:
        with _cache_lock:
            if _cache_service is None:
                # Import here to avoid circular imports
                from app.core.config import get_settings

                settings = get_settings()
                _cache_service = CacheService(
                    cache_dir=settings.cache_dir,
                    default_ttl_seconds=settings.cache_ttl_seconds,
                    enabled=settings.cache_enabled,
                )
    return _cache_service


def reset_cache_service() -> None:
    """Reset the global cache service (useful for testing)."""
    global _cache_service
    with _cache_lock:
        _cache_service = None
