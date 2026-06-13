"""Authentication helpers for external API surfaces."""

from config import settings
from fastapi import HTTPException, Request
from utils.logger import get_logger

logger = get_logger(__name__)


def verify_api_key(raw_request: Request, api_key: str | None = None) -> None:
    """Enforce optional Bearer API key auth using the configured API key.

    If no API key is configured, access is allowed. This matches the existing
    OpenAI-compatible endpoint behavior for local/self-hosted deployments.
    """
    expected_key = settings.api_key if api_key is None else api_key
    if not expected_key:
        return

    auth_header = raw_request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("API request rejected: Missing or invalid Authorization header")
        raise HTTPException(
            status_code=401, detail="Unauthorized: Missing or invalid API Key"
        )

    provided_key = auth_header.split(" ", 1)[1]
    if provided_key != expected_key:
        logger.warning("API request rejected: Invalid API Key")
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
