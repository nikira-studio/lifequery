"""Settings system for LifeQuery."""

import time
from dataclasses import dataclass, fields
from typing import Any

from db.database import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULTS = {
    "telegram_api_id": "",
    "telegram_api_hash": "",
    "telegram_fetch_batch": "2000",
    "telegram_fetch_wait": "5",
    "ollama_url": "http://ollama:11434",
    "embedding_model": "qwen3-Embedding-0.6B:Q8_0",
    "chat_provider": "ollama",
    "chat_model": "qwen3:8b",
    "chat_url": "http://ollama:11434",
    "chat_api_key": "",
    "openrouter_api_key": "",
    "custom_chat_url": "",
    "temperature": "0.2",
    "max_tokens": "4096",
    "top_k": "15",
    "context_cap": "10000",
    "chunk_target": "1000",
    "chunk_max": "1500",
    "chunk_overlap": "250",
    "api_key": "",
    "auto_sync_interval": "30",
    "enable_thinking": "False",
    "enable_rag": "True",
    "system_prompt": """You are LifeQuery, a personal memory assistant for {user_name}. Today's date is {current_date}.

Answer the user's question using ONLY the provided Telegram history context. 

### REASONING STEPS:
1. **Target Identification**: Based on today's date ({current_date}), identify the specific time period or event being questioned.
2. **Context Filtering**: Focus strictly on messages relevant to the query. Ignore extraneous information.
3. **Literal Accuracy**: Use the exact names and terms found in the logs. Do not interpret or expand acronyms unless the context defines them.

### OUTPUT FORMAT:
If the information is found:
1. A brief direct answer.
2. Supporting log entries in this format:
   - [YYYY-MM-DD] Summary of relevant fact [Chat Name]

If the information is NOT found:
"I couldn't find any specific information about that in my current memory index."

### CONTEXT DATA:
{context_text}""",
    "user_first_name": "",
    "user_last_name": "",
    "user_username": "",
    "debug_logs": "False",
    "noise_filter_keywords": "",
}

SENSITIVE_FIELDS = {
    "telegram_api_hash",
    "openrouter_api_key",
    "chat_api_key",
    "api_key",
}


CONFIG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Settings:
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_fetch_batch: int = 2000
    telegram_fetch_wait: int = 5
    ollama_url: str = "http://ollama:11434"
    embedding_model: str = "qwen3-Embedding-0.6B:Q8_0"
    chat_provider: str = "ollama"
    chat_model: str = "qwen3:8b"
    chat_url: str = "http://ollama:11434"
    chat_api_key: str = ""
    openrouter_api_key: str = ""
    custom_chat_url: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    top_k: int = 15
    context_cap: int = 10000
    chunk_target: int = 1000
    chunk_max: int = 1500
    chunk_overlap: int = 250
    api_key: str = ""
    auto_sync_interval: int = 30
    enable_thinking: bool = False
    enable_rag: bool = True
    system_prompt: str = DEFAULTS["system_prompt"]
    user_first_name: str = ""
    user_last_name: str = ""
    user_username: str = ""
    debug_logs: bool = False
    noise_filter_keywords: str = ""


settings = Settings()


def _get_field_type(key: str) -> type:  # type: ignore[return-value]
    field_types = {f.name: f.type for f in fields(Settings)}
    return field_types.get(key, str)  # type: ignore[return-value]


def _convert_value(key: str, value: str) -> Any:
    if value is None or value == "":
        return None
    field_type = _get_field_type(key)
    if field_type == bool:
        return value.lower() in ("true", "1", "yes")
    elif field_type == int:
        return int(value)
    elif field_type == float:
        return float(value)
    else:
        if key == "system_prompt" and value:
            # Handle literal \n from old escapes or DB storage
            return value.replace("\\n", "\n")
        return value


async def load_from_db() -> Settings:
    """Load settings from database with central helpers."""
    logger.info("Loading settings from database")
    try:
        from db.database import execute_fetchall

        # Ensure config table exists
        await execute_fetchall(CONFIG_TABLE_SQL)

        rows = await execute_fetchall("SELECT key, value FROM config")
        config_dict = {}
        for key, value in rows:
            normalized_value = _convert_value(key, value)
            if normalized_value is not None:
                config_dict[key] = normalized_value

        # In-place update of the global settings object.
        # This is CRITICAL because other modules have already imported a reference
        # to the original object. Re-assigning 'settings = ...' would break those ties.
        for key, value in config_dict.items():
            if hasattr(settings, key):
                object.__setattr__(settings, key, value)

        return settings
    except Exception as e:
        logger.error(f"Could not load settings from DB: {e}", exc_info=True)
        return settings


async def save_to_db(updates: dict[str, Any]) -> Settings:
    """Save settings updates to database using a single batch lock."""
    # Ensure active_provider is identified for profile sync logic
    active_provider = updates.get("chat_provider") or settings.chat_provider

    from db.database import _write_lock, get_connection

    logger.info(
        f"Saving settings updates: {list(updates.keys())} for provider: {active_provider}"
    )

    # Clean sensitive placeholders
    for field in SENSITIVE_FIELDS:
        if updates.get(field) == "****":
            updates.pop(field, None)

    # Use a single connection/lock for the whole batch
    async with _write_lock:
        db = await get_connection()
        try:
            # Ensure table exists
            await db.execute(CONFIG_TABLE_SQL)

            timestamp = int(time.time())
            for key, value in updates.items():
                if value is None:
                    continue
                str_value = str(value) if value != "" else ""
                await db.execute(
                    "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, str_value, timestamp),
                )

            from db.database import fetch_one

            # 2. Coherence sync: If provider changed, pull its profile into config
            if "chat_provider" in updates:
                profile = await fetch_one(
                    "SELECT base_url, api_key, last_model FROM providers WHERE id = ?",
                    (active_provider,),
                )
                if profile:
                    # Sync keys that aren't explicitly provided in this update batch
                    for cfg_key, prof_key in [
                        ("chat_url", "base_url"),
                        ("chat_api_key", "api_key"),
                        ("chat_model", "last_model"),
                    ]:
                        if cfg_key not in updates and profile.get(prof_key):
                            await db.execute(
                                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                                (cfg_key, str(profile[prof_key]), timestamp),
                            )

            # 3. Sync active settings back to profiles table
            provider_updates = {}
            if "chat_url" in updates:
                provider_updates["base_url"] = updates["chat_url"]
            if "chat_api_key" in updates:
                provider_updates["api_key"] = updates["chat_api_key"]
            if "chat_model" in updates:
                provider_updates["last_model"] = updates["chat_model"]

            if provider_updates and active_provider:
                set_clause = ", ".join([f"{k} = ?" for k in provider_updates.keys()])
                params = list(provider_updates.values()) + [timestamp, active_provider]
                await db.execute(
                    f"UPDATE providers SET {set_clause}, updated_at = ? WHERE id = ?",
                    params,
                )

            await db.commit()
        finally:
            await db.close()

    # Reload global settings after save
    await load_from_db()
    return settings


def mask_sensitive(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    for field in SENSITIVE_FIELDS:
        if field in result and result[field]:
            result[field] = "****"
    return result


def get_user_name() -> str:
    """Get the user's display name from saved identity settings."""
    first = settings.user_first_name or ""
    last = settings.user_last_name or ""
    username = settings.user_username or ""

    # Prefer first_name + last_name, then username, then fallback
    if first and last:
        return f"{first} {last}".strip()
    elif first:
        return first
    elif username:
        return username
    else:
        return "the user"


def get_current_date() -> str:
    """Get the current date formatted for the system prompt."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def get_system_prompt() -> str:
    """Get the system prompt with {user_name} and {current_date} placeholders replaced."""
    prompt = settings.system_prompt
    user_name = get_user_name()
    current_date = get_current_date()
    logger.info(
        f"System prompt placeholders: user_name='{user_name}', current_date='{current_date}'"
    )
    prompt = prompt.replace("{user_name}", user_name)
    prompt = prompt.replace("{current_date}", current_date)
    return prompt
