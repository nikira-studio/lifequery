"""Embedding pipeline for LifeQuery - incremental embedding with Ollama and ChromaDB."""

import hashlib
import json
import time
from typing import AsyncGenerator, Optional

from config import settings
from db.database import get_connection
from db.models import Chunk
from utils.logger import get_logger

from .ollama_embedder import check_model_exists, check_ollama_connection, embed_batch

logger = get_logger(__name__)

BATCH_SIZE = 32  # Embed 32 chunks at a time


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


async def get_sqlite_chunks() -> dict[str, tuple[str, str]]:
    """Get all chunk_id -> (content_hash, content) mapping from SQLite."""
    from db.database import fetch_all

    rows = await fetch_all("SELECT chunk_id, content_hash, content FROM chunks")
    return {row["chunk_id"]: (row["content_hash"], row["content"]) for row in rows}


async def get_embedded_chunk_ids() -> set[str]:
    """Get all chunk_ids currently in ChromaDB."""
    from vector_store.chroma import get_all_chunk_ids

    return await get_all_chunk_ids()


async def get_embedded_versions() -> dict[str, str]:
    """Get chunk_id -> embedding_version mapping from ChromaDB metadata."""
    from vector_store.chroma import _get_collection

    collection = _get_collection()
    try:
        # Get count and then fetch all metadata
        count = collection.count()
        result = collection.get(limit=count)
        versions = {}
        if result["metadatas"]:
            for i, chunk_id in enumerate(result["ids"]):
                versions[chunk_id] = result["metadatas"][i].get("embedding_version", "")
        return versions
    except Exception as e:
        logger.warning(f"Could not get embedded versions: {e}")
        return {}


async def check_embedding_version_mismatch() -> Optional[str]:
    """Check if embedding model has changed since last embed.

    Returns:
        Error message if mismatch, None if OK
    """
    current_version = settings.embedding_model

    try:
        embedded_versions = await get_embedded_versions()
        if not embedded_versions:
            return None  # No existing embeddings, OK

        stored_versions = set(embedded_versions.values())
        if len(stored_versions) > 1:
            logger.warning(f"Multiple embedding versions found: {stored_versions}")

        # Check if any chunk has a different version
        for chunk_id, stored_version in embedded_versions.items():
            if stored_version and stored_version != current_version:
                logger.error(
                    f"Embedding version mismatch: stored={stored_version}, "
                    f"current={current_version}. "
                    f"Embedding model has changed from '{stored_version}' to "
                    f"'{current_version}'. A full reindex is required."
                )
                return (
                    f"Embedding model has changed from '{stored_version}' to "
                    f"'{current_version}'. A full reindex is required."
                )
    except Exception as e:
        logger.warning(f"Could not check embedding version: {e}", exc_info=True)

    return None


async def embed_chunks_incremental() -> AsyncGenerator[dict, None]:
    """Perform incremental embedding - only embed new or changed chunks.

    Yields:
        Progress dicts or final summary dict
    """
    # Check Ollama connection first
    if not await check_ollama_connection():
        yield {
            "type": "error",
            "message": "Ollama is not reachable. Please check your settings.",
        }
        return

    # Check for embedding version mismatch
    version_error = await check_embedding_version_mismatch()
    if version_error:
        from vector_store.chroma import wipe

        logger.warning(f"Embedding version mismatch detected: {version_error}")
        logger.warning("Wiping vector store for a full re-index...")
        wipe()
        # Continue - it will now treat everything as new

    # Check if embedding model is pulled
    if not await check_model_exists(settings.embedding_model):
        yield {
            "type": "error",
            "message": f"Embedding model '{settings.embedding_model}' is not pulled in Ollama.",
        }
        return

    # Get current chunks from SQLite
    sqlite_chunks = await get_sqlite_chunks()
    sqlite_chunk_ids = set(sqlite_chunks.keys())

    # Get chunks currently in ChromaDB
    chroma_chunk_ids = await get_embedded_chunk_ids()

    # Determine what to do
    new_chunk_ids = sqlite_chunk_ids - chroma_chunk_ids
    deleted_chunk_ids = chroma_chunk_ids - sqlite_chunk_ids

    # Find full chunk data from SQLite for analysis and embedding
    from db.database import fetch_all

    all_rows = await fetch_all(
        "SELECT chunk_id, content, content_hash, chat_id, chat_name, "
        "participants, timestamp_start, timestamp_end, message_count "
        "FROM chunks"
    )

    chunk_data = {}
    for row in all_rows:
        chunk_id = row["chunk_id"]
        chunk_data[chunk_id] = {
            "content": row["content"],
            "content_hash": row["content_hash"],
            "chat_id": row["chat_id"],
            "chat_name": row["chat_name"],
            "participants": row["participants"],
            "timestamp_start": row["timestamp_start"],
            "timestamp_end": row["timestamp_end"],
            "message_count": row["message_count"],
        }

    # Find changed chunks by comparing hashes
    from vector_store.chroma import _get_collection

    collection = _get_collection()

    changed_chunk_ids = set()
    try:
        chroma_count = collection.count()
        if chroma_count > 0:
            result = collection.get(ids=list(chroma_chunk_ids), limit=chroma_count)
            if result["metadatas"]:
                for i, chunk_id in enumerate(result["ids"]):
                    stored_hash = result["metadatas"][i].get("content_hash", "")
                    if chunk_id in chunk_data:
                        current_hash = chunk_data[chunk_id]["content_hash"]
                        if stored_hash != current_hash:
                            changed_chunk_ids.add(chunk_id)
    except Exception as e:
        logger.warning(f"Could not check changed chunks: {e}", exc_info=True)

    # Determine final sets
    chunks_to_embed = new_chunk_ids | changed_chunk_ids

    counts = {
        "embedded": 0,
        "skipped": len(chroma_chunk_ids) - len(changed_chunk_ids),
        "deleted": len(deleted_chunk_ids),
        "errors": 0,
    }

    if not chunks_to_embed:
        logger.info("No chunks to embed - everything is up to date")
        yield {"type": "done", **counts}
        return

    logger.info(
        f"Embedding {len(chunks_to_embed)} chunks ({len(new_chunk_ids)} new, {len(changed_chunk_ids)} changed)"
    )

    # Process in batches
    chunk_ids_list = list(chunks_to_embed)
    total_chunks = len(chunk_ids_list)
    total_batches = (total_chunks + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        start_idx = batch_idx * BATCH_SIZE
        end_idx = min(start_idx + BATCH_SIZE, total_chunks)
        batch_ids = chunk_ids_list[start_idx:end_idx]

        # Build batch data
        batch_chunks = []
        batch_contents = []

        for chunk_id in batch_ids:
            data = chunk_data[chunk_id]
            chunk = Chunk(
                chunk_id=chunk_id,
                chat_id=data["chat_id"],
                chat_name=data["chat_name"],
                participants=data["participants"],
                timestamp_start=data["timestamp_start"],
                timestamp_end=data["timestamp_end"],
                message_count=data["message_count"],
                content=data["content"],
                content_hash=data["content_hash"],
                embedding_version=settings.embedding_model,
            )
            batch_chunks.append(chunk)
            batch_contents.append(data["content"])

        # Embed the batch
        try:
            embeddings = await embed_batch(batch_contents)
            from vector_store.chroma import upsert as chroma_upsert

            chroma_upsert(batch_chunks, embeddings)

            counts["embedded"] += len(batch_chunks)

            # Yield progress
            yield {
                "type": "progress",
                "processed_chunks": counts["embedded"],
                "total_chunks": total_chunks,
                "message": f"Embedded {counts['embedded']}/{total_chunks} chunks...",
            }

            # Update embedded_at in SQLite using a batch lock
            from db.database import _write_lock, get_connection

            async with _write_lock:
                db = await get_connection()
                try:
                    timestamp = int(time.time())
                    placeholders = ",".join(["?"] * len(batch_ids))
                    await db.execute(
                        f"UPDATE chunks SET embedded_at = ?, embedding_version = ? "
                        f"WHERE chunk_id IN ({placeholders})",
                        [timestamp, settings.embedding_model] + batch_ids,
                    )
                    await db.commit()
                finally:
                    await db.close()

        except Exception as e:
            error_msg = f"Error embedding batch {batch_idx}: {e}"
            logger.error(error_msg)
            counts["errors"] += len(batch_ids)
            if "last_error" not in counts:
                counts["last_error"] = str(e)

    yield {"type": "done", **counts}


async def reindex_all() -> AsyncGenerator[dict, None]:
    """Full reindex - re-embed all chunks to a temporary collection and swap on success.

    This is rollback-safe: if any embedding fails, the old collection is preserved.

    Yields:
        Dict with progress: current, total
        Final yield: Dict with counts: embedded, skipped, errors
    """
    # Check Ollama connection first
    if not await check_ollama_connection():
        raise RuntimeError("Ollama is not reachable. Please check your settings.")

    # Check if embedding model is pulled
    if not await check_model_exists(settings.embedding_model):
        raise RuntimeError(
            f"Embedding model '{settings.embedding_model}' is not pulled in Ollama. "
            f"Please run 'ollama pull {settings.embedding_model}' or change the model in settings."
        )

    # Create temporary collection for rollback-safe reindex
    from vector_store.chroma import (
        create_temp_collection,
        delete_temp_collection,
        swap_collection,
    )
    from vector_store.chroma import (
        upsert as chroma_upsert,
    )

    temp_collection = create_temp_collection()

    logger.info("Temporary collection created - starting full reindex")

    # Get all chunks from SQLite
    from db.database import fetch_all

    all_rows = await fetch_all(
        "SELECT chunk_id, content, content_hash, chat_id, chat_name, "
        "participants, timestamp_start, timestamp_end, message_count "
        "FROM chunks"
    )

    if not all_rows:
        logger.info("No chunks to embed - database is empty")
        # Clean up temp collection
        delete_temp_collection()
        yield {"type": "done", "embedded": 0, "skipped": 0, "errors": 0}
        return

    # Process in batches
    total_chunks = len(all_rows)
    total_batches = (total_chunks + BATCH_SIZE - 1) // BATCH_SIZE

    counts = {"embedded": 0, "skipped": 0, "errors": 0}
    timestamp = int(time.time())

    try:
        for batch_idx in range(total_batches):
            start_idx = batch_idx * BATCH_SIZE
            end_idx = min(start_idx + BATCH_SIZE, total_chunks)
            batch_rows = all_rows[start_idx:end_idx]

            batch_chunks = []
            batch_contents = []

            for row in batch_rows:
                chunk = Chunk(
                    chunk_id=row["chunk_id"],
                    content=row["content"],
                    content_hash=row["content_hash"],
                    chat_id=row["chat_id"],
                    chat_name=row["chat_name"],
                    participants=row["participants"],
                    timestamp_start=row["timestamp_start"],
                    timestamp_end=row["timestamp_end"],
                    message_count=row["message_count"],
                    embedding_version=settings.embedding_model,
                )
                batch_chunks.append(chunk)
                batch_contents.append(row["content"])

            # Embed the batch
            try:
                embeddings = await embed_batch(batch_contents)

                # Upsert to temporary collection
                chroma_upsert(batch_chunks, embeddings, collection=temp_collection)

                counts["embedded"] += len(batch_chunks)

                # Yield progress
                yield {
                    "type": "progress",
                    "current": counts["embedded"],
                    "total": total_chunks,
                }

                # Update embedded_at in SQLite using a batch lock
                from db.database import _write_lock, get_connection

                async with _write_lock:
                    db = await get_connection()
                    try:
                        placeholders = ",".join(["?"] * len(batch_chunks))
                        await db.execute(
                            f"UPDATE chunks SET embedded_at = ?, embedding_version = ? "
                            f"WHERE chunk_id IN ({placeholders})",
                            [timestamp, settings.embedding_model]
                            + [c.chunk_id for c in batch_chunks],
                        )
                        await db.commit()
                    finally:
                        await db.close()

            except Exception as e:
                logger.error(f"Error embedding batch {batch_idx}: {e}")
                counts["errors"] += len(batch_chunks)
                # Rollback: delete temp collection and raise
                delete_temp_collection()
                raise RuntimeError(f"Reindex failed at batch {batch_idx}: {e}") from e

        # All batches succeeded - swap collections
        logger.info(
            f"All {counts['embedded']} chunks embedded successfully - swapping collections"
        )
        swap_collection()

        logger.info(
            f"Reindex complete: {counts['embedded']} embedded, {counts['errors']} errors"
        )

        yield {
            "type": "done",
            "embedded": counts["embedded"],
            "skipped": counts["skipped"],
            "errors": counts["errors"],
        }

    except Exception as e:
        # Rollback: delete temp collection
        logger.error(f"Reindex failed, rolling back: {e}")
        delete_temp_collection()
        raise
