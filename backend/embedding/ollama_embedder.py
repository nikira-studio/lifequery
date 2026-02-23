"""Embedding client using the OpenAI-compatible API.

Works with Ollama's /v1 endpoint, OpenRouter, or any OpenAI-compatible provider.
The embedding_model setting determines which model is used; the base URL is derived
from ollama_url so the same code supports local Ollama and cloud providers.
"""

from typing import Optional

from openai import AsyncOpenAI

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

# Cached client — reset to None if settings change
_client: Optional[AsyncOpenAI] = None
_client_base_url: str = ""


def _get_client() -> AsyncOpenAI:
    """Get or create the OpenAI-compatible embedding client.

    Derives the base URL from settings.ollama_url by appending /v1 if needed.
    The client is cached; call reset_client() if settings change.
    """
    global _client, _client_base_url

    base_url = settings.ollama_url.rstrip("/") + "/v1"

    # Re-create if the URL has changed (e.g., settings updated at runtime)
    if _client is None or _client_base_url != base_url:
        _client = AsyncOpenAI(base_url=base_url, api_key="ollama")
        _client_base_url = base_url
        logger.info(f"Embedding client initialized at {base_url}")

    return _client


def reset_client() -> None:
    """Force the next call to _get_client() to create a fresh client.

    Call this after changing ollama_url in settings.
    """
    global _client
    _client = None


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of text strings via the OpenAI embeddings endpoint.

    Args:
        texts: List of strings to embed.

    Returns:
        List of embedding vectors (one per input string).
    """
    if not texts:
        return []

    client = _get_client()
    model = settings.embedding_model

    logger.debug(f"Embedding {len(texts)} texts with model '{model}'")

    response = await client.embeddings.create(model=model, input=texts)
    embeddings = [item.embedding for item in response.data]

    logger.debug(f"Generated {len(embeddings)} embeddings (dim={len(embeddings[0]) if embeddings else 0})")
    return embeddings


async def embed_single(text: str) -> list[float]:
    """Embed a single text string."""
    embeddings = await embed_batch([text])
    return embeddings[0] if embeddings else []


async def check_ollama_connection() -> bool:
    """Check if the embedding service (Ollama /v1) is reachable."""
    try:
        client = _get_client()
        await client.models.list()
        logger.info("Embedding service connection successful")
        return True
    except Exception as e:
        logger.warning(f"Embedding service connection failed: {e}")
        return False


async def check_model_exists(model_name: str) -> bool:
    """Check if the given embedding model is available on the service.

    Handles namespace prefixes (e.g. 'ZimaBlueAI/Qwen3-Embedding-0.6B:Q8_0')
    and case differences so partial name matches still work.
    """
    try:
        client = _get_client()
        response = await client.models.list()

        # Normalise for comparison
        want_lower = model_name.lower()
        want_base = want_lower.split(":")[0]  # strip tag suffix

        for m in response.data:
            m_id = m.id.lower()
            m_id_short = m_id.split("/")[-1]   # strip namespace prefix
            m_id_base = m_id_short.split(":")[0]

            if (
                m_id == want_lower
                or m_id_short == want_lower
                or m_id_short == f"{want_lower}:latest"
                or m_id_base == want_base
            ):
                return True

        logger.warning(f"Embedding model '{model_name}' not found in available models")
        return False
    except Exception as e:
        logger.error(f"Error checking embedding model '{model_name}': {e}")
        return False
