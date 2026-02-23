"""Settings router - GET and POST /api/settings."""

from config import mask_sensitive, save_to_db, settings
from fastapi import APIRouter, HTTPException
from schemas import (
    SettingsResponse,
    SettingsUpdate,
    SettingsUpdateResponse,
)
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    """Get all settings with sensitive fields masked."""
    values = {
        "telegram_api_id": settings.telegram_api_id,
        "telegram_api_hash": settings.telegram_api_hash,
        "telegram_fetch_batch": settings.telegram_fetch_batch,
        "telegram_fetch_wait": settings.telegram_fetch_wait,
        "ollama_url": settings.ollama_url,
        "embedding_model": settings.embedding_model,
        "chat_provider": settings.chat_provider,
        "chat_model": settings.chat_model,
        "chat_url": settings.chat_url,
        "chat_api_key": settings.chat_api_key,
        "openrouter_api_key": settings.openrouter_api_key,
        "custom_chat_url": settings.custom_chat_url,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "top_k": settings.top_k,
        "context_cap": settings.context_cap,
        "chunk_target": settings.chunk_target,
        "chunk_max": settings.chunk_max,
        "chunk_overlap": settings.chunk_overlap,
        "api_key": settings.api_key,
        "auto_sync_interval": settings.auto_sync_interval,
        "enable_thinking": settings.enable_thinking,
        "enable_rag": settings.enable_rag,
        "system_prompt": settings.system_prompt,
        "user_first_name": settings.user_first_name,
        "user_last_name": settings.user_last_name,
        "user_username": settings.user_username,
        "noise_filter_keywords": settings.noise_filter_keywords,
    }
    return SettingsResponse(**mask_sensitive(values))


@router.get("/providers")
async def get_providers():
    """Get all persistent LLM provider profiles."""
    from config import mask_sensitive
    from db.database import fetch_all

    try:
        profiles = await fetch_all(
            "SELECT id, name, provider_type, base_url, api_key, last_model FROM providers ORDER BY name ASC"
        )
        # Mask keys for each profile
        return [mask_sensitive(p) for p in profiles]
    except Exception as e:
        logger.error(f"Error fetching providers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings", response_model=SettingsUpdateResponse)
async def update_settings(updates: SettingsUpdate) -> SettingsUpdateResponse:
    """Update settings. Masked values (****) are ignored (keep existing)."""
    update_dict = updates.model_dump(exclude_none=True)

    # Remove empty strings - don't update with empty values
    update_dict = {k: v for k, v in update_dict.items() if v != ""}

    if not update_dict:
        return SettingsUpdateResponse()

    try:
        await save_to_db(update_dict)
        # Reset cached embedding client if the Ollama URL or model changed
        if "ollama_url" in update_dict or "embedding_model" in update_dict:
            from embedding.ollama_embedder import reset_client

            reset_client()
        return SettingsUpdateResponse()
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))
