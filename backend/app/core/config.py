from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings."""

    app_name: str = "Superblocker API"
    debug: bool = False

    # API settings
    api_v1_prefix: str = "/api/v1"

    # CORS settings
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Nominatim settings
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "Superblocker/1.0"

    # OSM settings
    osm_timeout: int = 180  # seconds
    osm_memory_limit: int = 1073741824  # 1GB

    # Cache settings
    cache_enabled: bool = True
    cache_dir: str = "cache"
    cache_ttl_seconds: int = 86400  # 24 hours default TTL
    cache_network_ttl_seconds: int = 604800  # 7 days for network data
    cache_analysis_ttl_seconds: int = 86400  # 24 hours for analysis results
    cache_search_ttl_seconds: int = 3600  # 1 hour for search results

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
