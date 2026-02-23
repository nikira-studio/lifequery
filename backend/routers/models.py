"""Models router - get available models from any OpenAI-compatible API."""

from config import settings
from fastapi import APIRouter, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["models"])


class ModelsResponse(BaseModel):
    """Response containing available models."""

    models: list[str]
    embedding_models: list[str]
    chat_models: list[str]


# Known embedding model name patterns
EMBEDDING_PATTERNS = [
    "embed",
    "bge",
    "e5",
    "gte",
    "nomic",
    "minilm",
    "instructor",
    "sentence",
    "all-minilm",
    "mxbai-embed",
]


def is_embedding_model(name: str) -> bool:
    """Check if a model name is an embedding model based on naming patterns."""
    lower = name.lower()
    return any(p in lower for p in EMBEDDING_PATTERNS)


def is_ollama_provider() -> bool:
    """Check if current provider is Ollama (needs native Ollama API)."""
    return settings.chat_provider == "ollama"


async def get_ollama_models(ollama_url: str) -> list[str]:
    """Get models from native Ollama API (uses /api/tags endpoint)."""
    import httpx

    # Ensure URL doesn't have /v1 suffix for native API
    base_url = ollama_url.rstrip("/").replace("/v1", "")

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base_url}/api/tags")
        response.raise_for_status()
        data = response.json()
        return [m["name"] for m in data.get("models", [])]

async def get_openai_compatible_models(base_url: str, api_key: str) -> list[str]:
    """Get models from OpenAI-compatible API (uses /v1/models endpoint)."""
    # Ensure URL has a version suffix (like /v1) if one isn't already present
    import re
    if not re.search(r'/v\d+([a-z0-9_-]*)?$', base_url.rstrip("/")):
        base_url = base_url.rstrip("/") + "/v1"

    # Use empty key if not provided
    if not api_key:
        api_key = "not-needed"

    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=30.0)

    response = await client.models.list()
    return [m.id for m in response.data]


@router.get("/models", response_model=ModelsResponse)
async def get_models(
    provider: str | None = None,
    url: str | None = None,
    api_key: str | None = None,
) -> ModelsResponse:
    """Get available models from a provider.
    
    If parameters are provided, it fetches from that specific configuration.
    Otherwise, it uses the globally configured chat provider.
    """
    active_provider = provider or settings.chat_provider
    active_url = url or settings.chat_url
    active_api_key = api_key or settings.chat_api_key or settings.openrouter_api_key

    # Handle masked key from frontend
    if active_api_key == "****":
        active_api_key = settings.chat_api_key or settings.openrouter_api_key

    # Profile lookup: if we are switching providers in the UI, try to get 
    # the persistent config for that target provider from the 'providers' table.
    if provider and provider != settings.chat_provider:
        try:
            from db.database import fetch_one
            profile = await fetch_one("SELECT base_url, api_key FROM providers WHERE id = ?", (provider,))
            if profile:
                active_url = url or profile["base_url"] or active_url
                active_api_key = api_key or profile["api_key"] or active_api_key
                if active_api_key == "****": # Security check for seeded values
                     active_api_key = settings.chat_api_key
        except Exception as e:
            logger.warning(f"Profile lookup failed for {provider}: {e}")

    try:
        if active_provider == "ollama":
            # Use native Ollama API (no /v1)
            all_models = await get_ollama_models(active_url)
        else:
            # Use OpenAI-compatible API (OpenRouter, Custom, etc.)
            
            # Smart URL resolution for preview - only if we don't have a specific profile URL
            if active_provider == "openrouter":
                if not active_url or "ollama" in active_url:
                    active_url = "https://openrouter.ai/api/v1"
            elif active_provider == "openai":
                if not active_url or any(x in active_url for x in ["ollama", "openrouter", "minimax", "glmai"]):
                    active_url = "https://api.openai.com/v1"
            elif active_provider == "minimax":
                if not active_url or "ollama" in active_url or "openrouter" in active_url:
                    active_url = "https://api.minimax.io/v1"
            elif active_provider == "glmai":
                if not active_url or "ollama" in active_url or "openrouter" in active_url:
                    active_url = "https://api.z.ai/api/coding/paas/v4"
            elif active_provider == "custom":
                if ("ollama" in active_url or not active_url) and settings.custom_chat_url:
                    active_url = settings.custom_chat_url

            try:
                all_models = await get_openai_compatible_models(active_url, active_api_key)
            except Exception as e:
                logger.warning(f"Could not fetch models from {active_provider}: {e}")
                # Fallback to hardcoded defaults if listing fails
                if active_provider == "openai":
                    all_models = ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"]
                elif active_provider == "minimax":
                    all_models = ["MiniMax-M2.5"]
                elif active_provider == "glmai":
                    all_models = ["glm-4.7"]
                else:
                    raise

        all_models = sorted(all_models)
        
        # Return all available models for both dropdowns to prevent aggressive filtering 
        # from hiding valid models that don't match standard naming conventions.
        return ModelsResponse(
            models=all_models,
            embedding_models=all_models,
            chat_models=all_models,
        )

    except Exception as e:
        logger.error(f"Failed to fetch models from {active_provider}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch models from {active_provider}: {str(e)}",
        )
