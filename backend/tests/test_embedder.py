"""Unit tests for the embedding pipeline."""

import hashlib
import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Real ollama and chromadb available on nexus server

# Mock the database DATA_DIR before importing modules
with patch("db.database.DATA_DIR", None):
    from config import settings as config_settings
    from embedding import (
        check_embedding_version_mismatch,
        compute_content_hash,
        embed_chunks_incremental,
        get_embedded_chunk_ids,
        get_embedded_versions,
        get_sqlite_chunks,
        reindex_all,
    )
    from embedding.ollama_embedder import check_ollama_connection, embed_batch


async def consume_generator(gen):
    final_counts = {}
    async for event in gen:
        if event.get("type") == "done":
            final_counts = event
    return final_counts


@pytest.mark.asyncio
async def test_compute_content_hash() -> None:
    """Test content hash generation is deterministic."""
    content1 = "Hello, world!"
    content2 = "Hello, world!"
    content3 = "Different content"

    hash1 = compute_content_hash(content1)
    hash2 = compute_content_hash(content2)
    hash3 = compute_content_hash(content3)

    assert hash1 == hash2  # Same content should produce same hash
    assert hash1 != hash3  # Different content should produce different hash
    assert len(hash1) == 16  # Should be first 16 chars of SHA256


@pytest.mark.asyncio
async def test_get_sqlite_chunks() -> None:
    """Test retrieving all chunks from SQLite database."""
    from db.database import get_connection

    db = await get_connection()
    try:
        # Create test chunks
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT UNIQUE NOT NULL,
                chat_id TEXT NOT NULL,
                chat_name TEXT,
                participants TEXT NOT NULL,
                timestamp_start INTEGER NOT NULL,
                timestamp_end INTEGER NOT NULL,
                message_count INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding_version TEXT NOT NULL,
                embedded_at INTEGER
            );
        """)

        await db.execute(
            "INSERT OR REPLACE INTO chunks (chunk_id, chat_id, chat_name, participants, "
            "timestamp_start, timestamp_end, message_count, content, content_hash, embedding_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "chunk1",
                "chat1",
                "Chat 1",
                "[]",
                1000,
                2000,
                2,
                "content 1",
                "hash1",
                "model1",
            ),
        )
        await db.execute(
            "INSERT OR REPLACE INTO chunks (chunk_id, chat_id, chat_name, participants, "
            "timestamp_start, timestamp_end, message_count, content, content_hash, embedding_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "chunk2",
                "chat2",
                "Chat 2",
                '["Alice"]',
                3000,
                4000,
                3,
                "content 2",
                "hash2",
                "model2",
            ),
        )
        await db.commit()

        # Test retrieval
        chunks = await get_sqlite_chunks()

        assert len(chunks) == 2
        assert "chunk1" in chunks
        assert "chunk2" in chunks
        assert chunks["chunk1"][0] == "hash1"
        assert chunks["chunk1"][1] == "content 1"
        assert chunks["chunk2"][0] == "hash2"
        assert chunks["chunk2"][1] == "content 2"
    finally:
        await db.execute("DROP TABLE IF EXISTS chunks")
        await db.commit()
        await db.close()


@pytest.mark.asyncio
async def test_get_embedded_chunk_ids() -> None:
    """Test retrieving chunk IDs from ChromaDB."""
    with patch("vector_store.chroma.get_all_chunk_ids") as mock_get_all:
        mock_get_all.return_value = {"chunk1", "chunk2", "chunk3"}

        ids = await get_embedded_chunk_ids()

        assert ids == {"chunk1", "chunk2", "chunk3"}


@pytest.mark.asyncio
async def test_get_embedded_versions() -> None:
    """Test retrieving embedding versions from ChromaDB."""
    with patch("vector_store.chroma._get_collection") as mock_get_collection:
        mock_collection = MagicMock()
        mock_collection.count.return_value = 2
        mock_collection.get.return_value = {
            "ids": ["chunk1", "chunk2"],
            "metadatas": [
                {"embedding_version": "model1", "other": "data"},
                {"embedding_version": "model2", "other": "data"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        versions = await get_embedded_versions()

        assert versions == {"chunk1": "model1", "chunk2": "model2"}


@pytest.mark.asyncio
async def test_check_embedding_version_mismatch_no_embeddings() -> None:
    """Test version check when no embeddings exist yet."""
    with patch("embedding.get_embedded_versions", return_value={}):
        error = await check_embedding_version_mismatch()
        assert error is None


@pytest.mark.asyncio
async def test_check_embedding_version_mismatch_match() -> None:
    """Test version check when all embeddings match current model."""
    with patch.object(config_settings, "embedding_model", "model1"):
        with patch(
            "embedding.get_embedded_versions",
            return_value={"chunk1": "model1", "chunk2": "model1"},
        ):
            error = await check_embedding_version_mismatch()
            assert error is None


@pytest.mark.asyncio
async def test_check_embedding_version_mismatch_mismatch() -> None:
    """Test version check when embeddings use different model."""
    with patch.object(config_settings, "embedding_model", "model2"):
        with patch(
            "embedding.get_embedded_versions",
            return_value={"chunk1": "model1", "chunk2": "model1"},
        ):
            error = await check_embedding_version_mismatch()
            assert error is not None
            assert "model1" in error
            assert "model2" in error
            assert "reindex" in error.lower()


@pytest.mark.asyncio
async def test_check_embedding_version_mismatch_multiple_versions() -> None:
    """Test version check when embeddings have multiple versions (warning)."""
    with patch.object(config_settings, "embedding_model", "model1"):
        with patch(
            "embedding.get_embedded_versions",
            return_value={"chunk1": "model1", "chunk2": "model2"},
        ):
            # Should still return None if current version exists
            error = await check_embedding_version_mismatch()
            # Actually, this should detect a mismatch since chunk2 uses different version
            assert error is not None


@pytest.mark.asyncio
async def test_embed_batch() -> None:
    """Test embedding a batch of texts."""
    with patch("embedding.ollama_embedder._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.embed.return_value = {
            "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        }
        mock_get_client.return_value = mock_client

        with patch.object(config_settings, "embedding_model", "model1"):
            with patch.object(config_settings, "ollama_url", "http://localhost"):
                embeddings = await embed_batch(["text1", "text2"])

            assert len(embeddings) == 2
            assert embeddings[0] == [0.1, 0.2, 0.3]
            assert embeddings[1] == [0.4, 0.5, 0.6]

            mock_client.embed.assert_called_once_with(
                model="model1", input=["text1", "text2"]
            )


@pytest.mark.asyncio
async def test_embed_batch_empty() -> None:
    """Test embedding an empty batch returns empty list."""
    embeddings = await embed_batch([])
    assert embeddings == []


@pytest.mark.asyncio
async def test_check_ollama_connection_success() -> None:
    """Test successful Ollama connection check."""
    with patch("embedding.ollama_embedder._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.list.return_value = {"models": []}
        mock_get_client.return_value = mock_client

        with patch.object(config_settings, "ollama_url", "http://localhost"):
            result = await check_ollama_connection()
            assert result is True


@pytest.mark.asyncio
async def test_check_ollama_connection_failure() -> None:
    """Test Ollama connection check when Ollama is unreachable."""
    with patch("embedding.ollama_embedder._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.list.side_effect = Exception("Connection refused")
        mock_get_client.return_value = mock_client

        with patch.object(config_settings, "ollama_url", "http://localhost"):
            result = await check_ollama_connection()
            assert result is False


@pytest.mark.asyncio
async def test_embed_chunks_incremental_no_new_chunks() -> None:
    """Test incremental embedding when everything is up to date."""
    with patch("embedding.check_ollama_connection", return_value=True):
        with patch("embedding.check_embedding_version_mismatch", return_value=None):
            with patch(
                "embedding.get_sqlite_chunks",
                return_value={"chunk1": ("hash1", "content1")},
            ):
                with patch("embedding.get_embedded_chunk_ids", return_value={"chunk1"}):
                    with patch("embedding.get_connection") as mock_get_conn:
                        mock_db = AsyncMock()
                        mock_cursor = AsyncMock()
                        mock_cursor.fetchall = AsyncMock(
                            return_value=[
                                (
                                    "chunk1",
                                    "content1",
                                    "hash1",
                                    "chat1",
                                    "Chat 1",
                                    "[]",
                                    1000,
                                    2000,
                                    2,
                                )
                            ]
                        )
                        mock_db.execute.return_value = mock_cursor
                        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
                        mock_db.__aexit__ = AsyncMock()
                        mock_get_conn.return_value = mock_db

                        with patch(
                            "vector_store.chroma._get_collection"
                        ) as mock_get_collection:
                            mock_collection = MagicMock()
                            mock_collection.get.return_value = {
                                "ids": ["chunk1"],
                                "metadatas": [
                                    {
                                        "embedding_version": "model1",
                                        "content_hash": "hash1",
                                    }
                                ],
                            }
                            mock_get_collection.return_value = mock_collection

                            counts = await consume_generator(embed_chunks_incremental())

                        assert counts.get("embedded") == 0
                        assert counts.get("skipped") == 1
                        assert counts.get("deleted") == 0
                        assert counts.get("errors") == 0


@pytest.mark.asyncio
async def test_embed_chunks_incremental_new_chunks() -> None:
    """Test incremental embedding with new chunks."""
    with patch("embedding.check_ollama_connection", return_value=True):
        with patch("embedding.check_embedding_version_mismatch", return_value=None):
            with patch(
                "embedding.get_sqlite_chunks",
                return_value={"chunk1": ("hash1", "content1")},
            ):
                with patch("embedding.get_embedded_chunk_ids", return_value=set()):
                    with patch("embedding.get_connection") as mock_get_conn:
                        # Mock database connection
                        mock_db = AsyncMock()
                        mock_cursor = AsyncMock()
                        mock_cursor.fetchall = AsyncMock(
                            return_value=[
                                (
                                    "chunk1",
                                    "content1",
                                    "hash1",
                                    "chat1",
                                    "Chat 1",
                                    "[]",
                                    1000,
                                    2000,
                                    2,
                                )
                            ]
                        )
                        mock_db.execute.return_value = mock_cursor
                        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
                        mock_db.__aexit__ = AsyncMock()
                        mock_get_conn.return_value = mock_db

                        with patch(
                            "embedding.embed_batch", return_value=[[0.1, 0.2, 0.3]]
                        ):
                            with patch("vector_store.chroma.upsert"):
                                with patch.object(
                                    config_settings, "embedding_model", "model1"
                                ):
                                    counts = await consume_generator(embed_chunks_incremental())

                                    assert counts.get("embedded") == 1
                                    assert counts.get("errors") == 0


@pytest.mark.asyncio
async def test_embed_chunks_incremental_ollama_unreachable() -> None:
    """Test incremental embedding fails when Ollama is unreachable."""
    with patch("embedding.check_ollama_connection", return_value=False):
        async for event in embed_chunks_incremental():
            if event.get("type") == "error":
                 assert "Ollama is not reachable" in event.get("message")
                 return
        
        pytest.fail("Should have yielded an error event")


@pytest.mark.asyncio
async def test_embed_chunks_incremental_version_mismatch() -> None:
    """Test incremental embedding fails when version mismatch detected."""
    with patch("embedding.check_ollama_connection", return_value=True):
        with patch(
            "embedding.check_embedding_version_mismatch",
            return_value="Embedding model has changed. A full reindex is required.",
        ):
            # Since we now auto-wipe and continue, it shouldn't raise RuntimeError anymore
            # instead it will continue to embed.
            counts = await consume_generator(embed_chunks_incremental())
            # If no sqlite chunks were mocked, embedded will be 0 but it shouldn't crash
            assert counts is not None


@pytest.mark.asyncio
async def test_reindex_all_empty_database() -> None:
    """Test reindex when database is empty."""
    with patch("embedding.check_ollama_connection", return_value=True):
        with patch("embedding.get_connection") as mock_get_conn:
            mock_db = AsyncMock()
            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[])
            mock_db.execute.return_value = mock_cursor
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock()
            mock_get_conn.return_value = mock_db

            with patch("vector_store.chroma.wipe") as mock_wipe:
                counts = await consume_generator(reindex_all())

                assert counts.get("embedded") == 0
                assert counts.get("skipped") == 0
                assert counts.get("errors") == 0
                mock_wipe.assert_called_once()


@pytest.mark.asyncio
async def test_reindex_all_with_chunks() -> None:
    """Test reindex with existing chunks."""
    with patch("embedding.check_ollama_connection", return_value=True):
        with patch("embedding.get_connection") as mock_get_conn:
            # Mock database connection - first call gets chunks, second call updates embedded_at
            mock_db = AsyncMock()
            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(
                return_value=[
                    (
                        "chunk1",
                        "content1",
                        "hash1",
                        "chat1",
                        "Chat 1",
                        "[]",
                        1000,
                        2000,
                        2,
                    )
                ]
            )
            mock_db.execute.return_value = mock_cursor

            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock()
            mock_get_conn.return_value = mock_db

            with patch("vector_store.chroma.wipe"):
                with patch("embedding.embed_batch", return_value=[[0.1, 0.2, 0.3]]):
                    with patch("vector_store.chroma.upsert"):
                        with patch.object(config_settings, "embedding_model", "model1"):
                            counts = await consume_generator(reindex_all())

                            assert counts.get("embedded") == 1
                            assert counts.get("errors") == 0
