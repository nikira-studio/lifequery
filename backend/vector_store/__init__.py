"""Vector store module for LifeQuery - ChromaDB interface."""

from .chroma import (
    CHROMA_PATH,
    COLLECTION_NAME,
    RetrievedChunk,
    _get_client,
    _get_collection,
    count,
    exists,
    get_all_chunk_ids,
    query,
    upsert,
    wipe,
)

__all__ = [
    "COLLECTION_NAME",
    "CHROMA_PATH",
    "_get_client",
    "_get_collection",
    "count",
    "exists",
    "get_all_chunk_ids",
    "query",
    "RetrievedChunk",
    "upsert",
    "wipe",
]
