from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Superblocker API"
    debug: bool = False

    # API settings
    api_v1_prefix: str = "/api/v1"

    # CORS settings
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )

    # Nominatim settings
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "Superblocker/1.0 (https://github.com/tgergo1/Superblocker)"
    nominatim_min_interval_seconds: float = 1.0

    # OSM settings
    osm_timeout: int = 180  # seconds
    osm_memory_limit: int = 1073741824  # 1GB
    max_bbox_span_degrees: float = 0.5
    max_bbox_area_km2: float = 2500.0

    # Workload protection
    analysis_max_workers: int = 2
    analysis_max_concurrent_requests: int = 2
    analysis_rate_limit_per_minute: int = 6
    partition_cache_max_entries: int = 8
    partition_cache_ttl_seconds: int = 3600
    admin_api_key: str | None = None

    # Cache settings
    cache_enabled: bool = True
    cache_dir: str = "cache"
    cache_ttl_seconds: int = 86400  # 24 hours default TTL
    cache_network_ttl_seconds: int = 604800  # 7 days for network data
    cache_analysis_ttl_seconds: int = 86400  # 24 hours for analysis results
    cache_search_ttl_seconds: int = 3600  # 1 hour for search results


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
