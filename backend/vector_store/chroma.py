"""ChromaDB vector store interface for LifeQuery."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from db.database import DATA_DIR
from db.models import Chunk
from utils.logger import get_logger

logger = get_logger(__name__)

COLLECTION_NAME = "lifequery_chunks"
CHROMA_PATH = DATA_DIR / "chroma"

# Global client and collection - initialized lazily
_client: Optional[chromadb.PersistentClient] = None
_collection: Optional[chromadb.Collection] = None


def _get_client() -> chromadb.PersistentClient:
    """Get or create the ChromaDB persistent client."""
    global _client
    if _client is None:
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(CHROMA_PATH), settings=ChromaSettings(anonymized_telemetry=False)
        )
        logger.info(f"ChromaDB client initialized at {CHROMA_PATH}")
    return _client


def _get_collection() -> chromadb.Collection:
    """Get or create the LifeQuery chunks collection."""
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"description": "LifeQuery message chunks"},
            embedding_function=None,  # We supply our own embeddings via Ollama
        )
        logger.info(f"ChromaDB collection '{COLLECTION_NAME}' ready")
    return _collection


def upsert(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    collection: Optional[chromadb.Collection] = None,
) -> None:
    """Insert or update chunks with their embeddings.

    Args:
        chunks: List of Chunk objects to upsert
        embeddings: List of embedding vectors (one per chunk)
        collection: Optional collection to use. If None, uses the default collection.
    """
    if not chunks or not embeddings:
        logger.warning("upsert called with empty chunks or embeddings")
        return

    if len(chunks) != len(embeddings):
        raise ValueError(
            f"Chunk count ({len(chunks)}) != embedding count ({len(embeddings)})"
        )

    if collection is None:
        collection = _get_collection()

    ids = [chunk.chunk_id for chunk in chunks]
    documents = [chunk.content for chunk in chunks]

    metadatas = []
    for chunk in chunks:
        participants = chunk.get_participants_list()
        metadatas.append(
            {
                "chunk_id": chunk.chunk_id,
                "chat_id": chunk.chat_id,
                "chat_name": chunk.chat_name or "",
                "participants": json.dumps(participants),
                "content_hash": chunk.content_hash or "",
                "timestamp_start": chunk.timestamp_start,
                "timestamp_end": chunk.timestamp_end,
                "message_count": chunk.message_count,
                "embedding_version": chunk.embedding_version or "",
            }
        )

    collection.upsert(
        ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas
    )
    logger.info(f"Upserted {len(chunks)} chunks to ChromaDB")


@dataclass
class RetrievedChunk:
    """A chunk retrieved from vector search."""

    chunk_id: str
    chat_id: str
    chat_name: Optional[str]
    participants: list[str]
    timestamp_start: int
    timestamp_end: int
    message_count: int
    content: str
    distance: float


async def query(
    embedding: list[float],
    top_k: int,
    included_chat_ids: Optional[set[str]] = None,
    where: Optional[dict] = None,
) -> list[RetrievedChunk]:
    """Query the vector store for similar chunks."""
    collection = _get_collection()

    query_params = {
        "query_embeddings": [embedding],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"],
    }

    # If included_chat_ids is provided but empty, no chats are allowed to be searched
    if included_chat_ids is not None and not included_chat_ids:
        logger.debug("query: included_chat_ids is empty set - returning no results")
        return []

    filters = []
    if included_chat_ids is not None:
        filters.append({"chat_id": {"$in": list(included_chat_ids)}})

    if where:
        filters.append(where)

    if len(filters) > 1:
        query_params["where"] = {"$and": filters}
    elif len(filters) == 1:
        query_params["where"] = filters[0]

    try:
        # collection.query is blocking, so we run it in a thread.
        # We also add a timeout to prevent hanging the whole pipeline if the NAS is slow/locked.
        results = await asyncio.wait_for(
            asyncio.to_thread(collection.query, **query_params), timeout=15.0
        )
    except asyncio.TimeoutError:
        logger.error(
            "ChromaDB query timed out after 15s - likely NAS file locking issue"
        )
        return []
    except Exception as e:
        logger.error(f"ChromaDB query error: {e}")
        return []

    retrieved = []
    if results["ids"] and results["ids"][0]:
        for i, chunk_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][i]
            document = results["documents"][0][i]
            distance = results["distances"][0][i]

            try:
                participants = json.loads(metadata.get("participants", "[]"))
            except (json.JSONDecodeError, TypeError):
                participants = []

            retrieved.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    chat_id=metadata.get("chat_id", ""),
                    chat_name=metadata.get("chat_name") or None,
                    participants=participants,
                    timestamp_start=metadata.get("timestamp_start", 0),
                    timestamp_end=metadata.get("timestamp_end", 0),
                    message_count=metadata.get("message_count", 0),
                    content=document,
                    distance=distance,
                )
            )

    logger.debug(f"Query returned {len(retrieved)} chunks")
    return retrieved


def create_temp_collection() -> chromadb.Collection:
    """Create a temporary collection for rollback-safe reindex."""
    client = _get_client()
    temp_name = f"{COLLECTION_NAME}_temp"

    try:
        client.delete_collection(name=temp_name)
    except Exception as e:
        logger.debug(f"Temp collection '{temp_name}' did not exist (OK): {e}")

    temp_collection = client.get_or_create_collection(
        name=temp_name,
        metadata={"description": "Temporary LifeQuery chunks for reindex"},
        embedding_function=None,
    )
    logger.info(f"Created temporary collection '{temp_name}'")
    return temp_collection


def swap_collection() -> None:
    """Swap temporary collection with the main collection."""
    global _collection
    client = _get_client()
    temp_name = f"{COLLECTION_NAME}_temp"

    try:
        temp_collection = client.get_collection(name=temp_name)
    except Exception as e:
        logger.error(f"Temporary collection not found: {e}")
        raise RuntimeError("Reindex failed: temporary collection not found")

    try:
        client.delete_collection(name=COLLECTION_NAME)
    except Exception as e:
        # Collection may not exist yet on first reindex - that's OK
        logger.info(
            f"Main collection did not exist or could not be deleted (OK on first run): {e}"
        )

    new_main = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "LifeQuery message chunks"},
        embedding_function=None,
    )

    try:
        all_data = temp_collection.get(include=["embeddings", "documents", "metadatas"])
        if all_data["ids"]:
            new_main.add(
                ids=all_data["ids"],
                embeddings=all_data["embeddings"],
                documents=all_data["documents"],
                metadatas=all_data["metadatas"],
            )
            logger.info(
                f"Copied {len(all_data['ids'])} vectors from temp to main collection"
            )
    except Exception as e:
        logger.error(f"Failed to copy data from temp collection: {e}")
        _collection = None
        _get_collection()
        raise RuntimeError(f"Failed to copy data from temp collection: {e}")

    try:
        client.delete_collection(name=temp_name)
    except Exception as e:
        logger.warning(f"Failed to delete temporary collection: {e}")

    _collection = None
    _get_collection()
    logger.info("Collection swap completed successfully")


def delete_temp_collection() -> None:
    """Delete the temporary collection (called on rollback)."""
    client = _get_client()
    temp_name = f"{COLLECTION_NAME}_temp"

    try:
        client.delete_collection(name=temp_name)
    except Exception as e:
        logger.debug(f"Could not delete temp collection '{temp_name}': {e}")


def wipe() -> None:
    """Delete and recreate the collection (for full reindex)."""
    global _collection
    client = _get_client()

    try:
        client.delete_collection(name=COLLECTION_NAME)
    except Exception as e:
        logger.debug(f"Collection '{COLLECTION_NAME}' did not exist (OK): {e}")

    _collection = None
    _get_collection()
    logger.info(f"Recreated ChromaDB collection '{COLLECTION_NAME}'")


async def count() -> int:
    """Get the total number of vectors in the collection."""
    collection = _get_collection()
    return await asyncio.to_thread(collection.count)


async def exists(chunk_id: str) -> bool:
    """Check if a chunk exists in the vector store."""
    collection = _get_collection()
    try:
        result = await asyncio.to_thread(collection.get, ids=[chunk_id])
        return len(result["ids"]) > 0 and result["ids"][0] == chunk_id
    except Exception as e:
        logger.warning(f"Could not check if chunk '{chunk_id}' exists: {e}")
        return False


async def get_all_chunk_ids() -> set[str]:
    """Get all chunk IDs currently stored in ChromaDB."""
    collection = _get_collection()
    try:
        cnt = await asyncio.to_thread(collection.count)
        if cnt == 0:
            return set()
        result = await asyncio.to_thread(collection.get, limit=cnt)
        return set(result.get("ids", []))
    except Exception as e:
        logger.warning(f"Could not get all chunk IDs from ChromaDB: {e}")
        return set()
