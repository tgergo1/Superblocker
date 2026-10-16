"""Authentication dependencies for privileged operational APIs."""

import hmac

from fastapi import Header, HTTPException

from app.core.config import get_settings

settings = get_settings()


async def require_admin_key(x_admin_key: str | None = Header(default=None)) -> str:
    """Require the configured administrator secret and return its audit actor."""
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=503,
            detail="Administrator operations are disabled until ADMIN_API_KEY is configured.",
        )
    if x_admin_key is None or not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=403, detail="Invalid administrator credentials")
    return "admin-api-key"
