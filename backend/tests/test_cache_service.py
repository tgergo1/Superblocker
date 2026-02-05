"""
Tests for the caching service.
"""

import json
import os
import tempfile
import time
import pytest

from app.services.cache_service import CacheService, CacheStats


class TestCacheService:
    """Tests for CacheService class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.cache = CacheService(
            cache_dir=self.temp_dir,
            default_ttl_seconds=3600,
            enabled=True,
        )

    def teardown_method(self):
        """Clean up after tests."""
        # Clean up temp directory
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cache_set_and_get(self):
        """Test basic cache set and get operations."""
        cache_type = "test"
        params = {"key": "value", "number": 123}
        data = {"result": "test data", "items": [1, 2, 3]}

        # Set data
        result = self.cache.set(cache_type, params, data)
        assert result is True

        # Get data
        cached = self.cache.get(cache_type, params)
        assert cached is not None
        assert cached["result"] == "test data"
        assert cached["items"] == [1, 2, 3]

    def test_cache_miss(self):
        """Test cache miss returns None."""
        cached = self.cache.get("nonexistent", {"key": "value"})
        assert cached is None

    def test_cache_key_consistency(self):
        """Test that cache keys are consistent for same parameters."""
        cache_type = "test"
        params = {"a": 1, "b": 2, "c": 3}
        data = {"value": "test"}

        self.cache.set(cache_type, params, data)

        # Same params, different order should hit cache
        cached = self.cache.get(cache_type, {"c": 3, "a": 1, "b": 2})
        assert cached is not None
        assert cached["value"] == "test"

    def test_cache_different_params(self):
        """Test that different params create different cache entries."""
        cache_type = "test"
        data1 = {"value": "data1"}
        data2 = {"value": "data2"}

        self.cache.set(cache_type, {"param": 1}, data1)
        self.cache.set(cache_type, {"param": 2}, data2)

        cached1 = self.cache.get(cache_type, {"param": 1})
        cached2 = self.cache.get(cache_type, {"param": 2})

        assert cached1["value"] == "data1"
        assert cached2["value"] == "data2"

    def test_cache_expiration(self):
        """Test that expired cache entries return None."""
        cache_type = "test"
        params = {"key": "value"}
        data = {"result": "test"}

        # Set with very short TTL
        self.cache.set(cache_type, params, data, ttl_seconds=1)

        # Should be available immediately
        cached = self.cache.get(cache_type, params)
        assert cached is not None

        # Wait for expiration
        time.sleep(1.5)

        # Should be expired now
        cached = self.cache.get(cache_type, params)
        assert cached is None

    def test_cache_disabled(self):
        """Test that disabled cache doesn't store or retrieve data."""
        disabled_cache = CacheService(
            cache_dir=self.temp_dir,
            enabled=False,
        )

        result = disabled_cache.set("test", {"key": "value"}, {"data": "test"})
        assert result is False

        cached = disabled_cache.get("test", {"key": "value"})
        assert cached is None

    def test_cache_invalidate_specific(self):
        """Test invalidating a specific cache entry."""
        cache_type = "test"
        params = {"key": "value"}
        data = {"result": "test"}

        self.cache.set(cache_type, params, data)
        assert self.cache.get(cache_type, params) is not None

        count = self.cache.invalidate(cache_type, params)
        assert count == 1
        assert self.cache.get(cache_type, params) is None

    def test_cache_invalidate_by_type(self):
        """Test invalidating all entries of a specific type."""
        self.cache.set("type1", {"a": 1}, {"data": 1})
        self.cache.set("type1", {"a": 2}, {"data": 2})
        self.cache.set("type2", {"a": 1}, {"data": 3})

        count = self.cache.invalidate(cache_type="type1")
        assert count == 2

        # type1 entries should be gone
        assert self.cache.get("type1", {"a": 1}) is None
        assert self.cache.get("type1", {"a": 2}) is None

        # type2 entry should remain
        assert self.cache.get("type2", {"a": 1}) is not None

    def test_cache_invalidate_all(self):
        """Test invalidating all cache entries."""
        self.cache.set("type1", {"a": 1}, {"data": 1})
        self.cache.set("type2", {"a": 1}, {"data": 2})

        count = self.cache.invalidate()
        assert count == 2

        assert self.cache.get("type1", {"a": 1}) is None
        assert self.cache.get("type2", {"a": 1}) is None

    def test_cache_cleanup_expired(self):
        """Test cleaning up expired entries."""
        # Set one with short TTL and one with long TTL
        self.cache.set("test", {"a": 1}, {"data": 1}, ttl_seconds=1)
        self.cache.set("test", {"a": 2}, {"data": 2}, ttl_seconds=3600)

        # Wait for first to expire
        time.sleep(1.5)

        count = self.cache.cleanup_expired()
        assert count == 1

        # First should be gone, second should remain
        assert self.cache.get("test", {"a": 1}) is None
        assert self.cache.get("test", {"a": 2}) is not None

    def test_cache_stats(self):
        """Test cache statistics."""
        # Create some entries
        self.cache.set("network", {"a": 1}, {"data": "a"})
        self.cache.set("network", {"a": 2}, {"data": "b"})
        self.cache.set("analysis", {"a": 1}, {"data": "c"})

        # Generate some hits and misses
        self.cache.get("network", {"a": 1})  # hit
        self.cache.get("network", {"a": 2})  # hit
        self.cache.get("network", {"a": 3})  # miss

        stats = self.cache.get_stats()
        assert stats.entries_count == 3
        assert stats.hits == 2
        assert stats.misses == 1
        assert "network" in stats.cache_types
        assert stats.cache_types["network"] == 2
        assert stats.cache_types["analysis"] == 1

    def test_cache_stats_to_dict(self):
        """Test cache stats dictionary conversion."""
        self.cache.set("test", {"a": 1}, {"data": "a"})
        self.cache.get("test", {"a": 1})  # hit
        self.cache.get("test", {"a": 2})  # miss

        stats = self.cache.get_stats()
        stats_dict = stats.to_dict()

        assert "hits" in stats_dict
        assert "misses" in stats_dict
        assert "hit_rate" in stats_dict
        assert "entries_count" in stats_dict
        assert "total_size_mb" in stats_dict
        assert stats_dict["hit_rate"] == 0.5

    def test_cache_with_complex_data(self):
        """Test caching complex nested data structures."""
        complex_data = {
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                    "properties": {"name": "Test Road", "highway": "primary"},
                }
            ],
            "metadata": {
                "total_edges": 100,
                "road_types": {"primary": 10, "secondary": 20},
            },
        }

        self.cache.set("network", {"bbox": "test"}, complex_data)
        cached = self.cache.get("network", {"bbox": "test"})

        assert cached["features"][0]["properties"]["name"] == "Test Road"
        assert cached["metadata"]["total_edges"] == 100

    def test_cache_zero_ttl_never_expires(self):
        """Test that TTL of 0 means entry never expires."""
        self.cache.set("test", {"a": 1}, {"data": "test"}, ttl_seconds=0)

        # Even after cleanup, entry should remain
        count = self.cache.cleanup_expired()
        assert count == 0

        cached = self.cache.get("test", {"a": 1})
        assert cached is not None


class TestCacheEntry:
    """Tests for CacheEntry class."""

    def test_entry_expired(self):
        """Test is_expired method."""
        from app.services.cache_service import CacheEntry

        # Entry that should be expired
        expired = CacheEntry(
            data={},
            created_at=time.time() - 3700,  # Created over an hour ago
            ttl_seconds=3600,  # 1 hour TTL
            cache_key="test",
            cache_type="test",
        )
        assert expired.is_expired() is True

        # Entry that should not be expired
        fresh = CacheEntry(
            data={},
            created_at=time.time(),
            ttl_seconds=3600,
            cache_key="test",
            cache_type="test",
        )
        assert fresh.is_expired() is False

        # Entry with zero TTL never expires
        never_expires = CacheEntry(
            data={},
            created_at=time.time() - 999999,
            ttl_seconds=0,
            cache_key="test",
            cache_type="test",
        )
        assert never_expires.is_expired() is False
