"""RAG retrieval module - embedding and vector search.

This module handles:
- Query embedding
- Vector search against ChromaDB
- Filtering by included chats
"""

import re
from datetime import datetime
from typing import Optional

from config import Settings
from embedding.ollama_embedder import embed_single
from utils.logger import get_logger
from vector_store.chroma import RetrievedChunk, query

logger = get_logger(__name__)


async def get_included_chat_ids() -> set[str]:
    """Get the set of chat IDs that are included in the index."""
    from db.database import fetch_all

    rows = await fetch_all("SELECT chat_id FROM chats WHERE included = 1")
    return {row["chat_id"] for row in rows if row["chat_id"]}


async def embed_query(query_text: str) -> list[float]:
    """Embed a query string.

    Args:
        query_text: The query to embed

    Returns:
        Embedding vector
    """
    return await embed_single(query_text)


async def retrieve_chunks(
    query_embedding: list[float],
    top_k: int,
    included_chat_ids: Optional[set[str]] = None,
    where: Optional[dict] = None,
) -> list[RetrievedChunk]:
    """Retrieve relevant chunks from vector store.

    Args:
        query_embedding: The embedded query vector
        top_k: Number of results to return
        included_chat_ids: Optional set of chat IDs to filter by.
        where: Optional metadata filter

    Returns:
        List of RetrievedChunk objects, sorted by similarity (best first)
    """
    if included_chat_ids is None:
        included_chat_ids = await get_included_chat_ids()

    logger.debug(f"Retrieving top {top_k} chunks from {len(included_chat_ids)} chats")
    results = await query(query_embedding, top_k, included_chat_ids, where=where)

    if not results:
        logger.warning("No relevant chunks found for query")

    return results


def parse_date_range(query: str):
    """Extremely basic month/year extractor for metadata filtering.
    Detects patterns like 'November' or 'Nov 2024'.
    """
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }

    query = query.lower()
    found_month = None
    found_year = None

    for m_name, m_num in months.items():
        if re.search(rf"\b{m_name}\b", query):
            found_month = m_num
            break

    year_match = re.search(r"\b(20\d{2})\b", query)
    if year_match:
        found_year = int(year_match.group(1))
    else:
        if found_month:
            now = datetime.now()
            # If month already passed this year, assume this year, else last year
            if found_month > now.month:
                found_year = now.year - 1
            else:
                found_year = now.year

    if found_year:
        try:
            if found_month:
                start_date = datetime(found_year, found_month, 1)
                if found_month == 12:
                    end_date = datetime(found_year + 1, 1, 1)
                else:
                    end_date = datetime(found_year, found_month + 1, 1)
            else:
                # Year only - filter for the whole year
                start_date = datetime(found_year, 1, 1)
                end_date = datetime(found_year + 1, 1, 1)
                
            return int(start_date.timestamp()), int(end_date.timestamp())
        except ValueError:
            return None, None

    return None, None


async def retrieve(
    query_text: str,
    settings: Settings,
) -> tuple[list[RetrievedChunk], set[str]]:
    """Main retrieval entry point - embed query and retrieve chunks.

    This is a convenience function that combines embedding and retrieval
    in a single call for common use cases.

    Args:
        query_text: The user's question
        settings: Settings containing top_k and other retrieval config

    Returns:
        Tuple of (retrieved_chunks, included_chat_ids)
    """
    # Get included chat IDs
    included_chat_ids = await get_included_chat_ids()

    # Detect date filters in query
    start_ts, end_ts = parse_date_range(query_text)
    where = None
    if start_ts and end_ts:
        where = {
            "$and": [
                {"timestamp_start": {"$gte": start_ts}},
                {"timestamp_end": {"$lte": end_ts}},
            ]
        }

    # Embed query
    query_embedding = await embed_query(query_text)

    # Retrieve chunks
    chunks = await retrieve_chunks(
        query_embedding, settings.top_k * 3, included_chat_ids, where=where
    )

    return chunks, included_chat_ids
