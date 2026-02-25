"""Chunking engine for messages."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator

from config import settings
from db.database import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)

# Time thresholds (in seconds)
GAP_HARD_SECONDS = 4 * 60 * 60  # 4 hours
GAP_SOFT_SECONDS = 20 * 60  # 20 minutes
CHUNK_MIN_TOKENS = 300  # Minimum tokens for soft break


@dataclass
class Chunk:
    chunk_id: str
    chat_id: str
    chat_name: str
    participants: list[str]
    timestamp_start: int
    timestamp_end: int
    message_count: int
    content: str
    content_hash: str


def estimate_tokens(text: str) -> int:
    """Estimate token count using a conservative word-based approximation."""
    if not text:
        return 0
    return int(len(text.split()) * 1.35)


def format_message(timestamp: int, sender_name: str, text: str) -> str:
    """Format a single message as [timestamp] Sender: message."""
    dt = datetime.utcfromtimestamp(timestamp)
    date_str = dt.strftime("%Y-%m-%d %H:%M")
    return f"[{date_str}] {sender_name}: {text}"


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def get_unembedded_messages() -> dict[str, list[dict]]:
    """Fetch all messages that haven't been embedded yet, grouped by chat_id.
    Only includes messages from chats where included=1.
    """
    from db.database import fetch_all
    
    rows = await fetch_all(
        """
        SELECT m.id, m.message_id, m.chat_id, m.chat_name, m.sender_id, m.sender_name, m.text, m.timestamp
        FROM messages m
        JOIN chats c ON m.chat_id = c.chat_id
        WHERE (c.included = 1)
        AND m.timestamp > IFNULL(c.last_chunked_at, 0)
        ORDER BY m.chat_id, m.timestamp ASC
        """
    )

    # Group by chat_id
    chats: dict[str, list[dict]] = {}
    for row in rows:
        chat_id = str(row["chat_id"])
        if chat_id not in chats:
            chats[chat_id] = []

        chats[chat_id].append(
            {
                "message_id": str(row["message_id"]),
                "chat_id": chat_id,
                "chat_name": row["chat_name"],
                "sender_id": str(row["sender_id"]) if row["sender_id"] else "",
                "sender_name": row["sender_name"] or "Unknown",
                "text": row["text"],
                "timestamp": row["timestamp"],
            }
        )

    return chats


def chunk_chat(messages: list[dict]) -> list[Chunk]:
    """Chunk a single chat's messages according to the algorithm."""
    if not messages:
        return []

    chunks: list[Chunk] = []
    current_chunk_messages: list[dict] = []
    current_chunk_start_timestamp: int = 0

    def finalize_chunk(msgs: list[dict]) -> Chunk | None:
        """Create a Chunk from a list of messages."""
        if not msgs:
            return None

        # Get unique participants
        participants = list(
            set(msg["sender_name"] for msg in msgs if msg["sender_name"])
        )

        # Build content
        content_parts = []
        for msg in msgs:
            content_parts.append(
                format_message(msg["timestamp"], msg["sender_name"], msg["text"])
            )
        content = "\n".join(content_parts)

        # Compute stable hash
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        # Compute stable chunk ID from content and chat
        chunk_id = hashlib.sha256(f"{msgs[0]['chat_id']}:{content_hash}".encode()).hexdigest()[:20]

        return Chunk(
            chunk_id=chunk_id,
            chat_id=msgs[0]["chat_id"],
            chat_name=msgs[0]["chat_name"],
            participants=participants,
            timestamp_start=msgs[0]["timestamp"],
            timestamp_end=msgs[-1]["timestamp"],
            message_count=len(msgs),
            content=content,
            content_hash=content_hash,
        )

    def get_current_token_count() -> int:
        """Get token count of current chunk content."""
        if not current_chunk_messages:
            return 0
        content = "\n".join(
            format_message(m["timestamp"], m["sender_name"], m["text"])
            for m in current_chunk_messages
        )
        return estimate_tokens(content)

    # Process noise keywords
    noise_keywords = [
        k.strip().lower() for k in settings.noise_filter_keywords.split(",") if k.strip()
    ]

    for i, msg in enumerate(messages):
        # Noise filter
        if noise_keywords:
            msg_text_lower = (msg["text"] or "").lower()
            if any(keyword in msg_text_lower for keyword in noise_keywords):
                logger.debug(f"Skipping noisy message in {msg['chat_name']}")
                continue

        if not current_chunk_messages:
            # Start new chunk
            current_chunk_messages.append(msg)
            current_chunk_start_timestamp = msg["timestamp"]
            continue

        last_msg = current_chunk_messages[-1]
        delta = msg["timestamp"] - last_msg["timestamp"]

        # Check for hard break (4+ hours gap)
        if delta > GAP_HARD_SECONDS:
            # Finalize current chunk
            chunk = finalize_chunk(current_chunk_messages)
            if chunk:
                chunks.append(chunk)

            # Start new chunk
            current_chunk_messages = [msg]
            current_chunk_start_timestamp = msg["timestamp"]
            continue

        # Check for soft break (20+ minutes gap)
        if delta > GAP_SOFT_SECONDS:
            current_tokens = get_current_token_count()
            if current_tokens >= CHUNK_MIN_TOKENS:
                # Finalize current chunk and start new
                chunk = finalize_chunk(current_chunk_messages)
                if chunk:
                    chunks.append(chunk)

                current_chunk_messages = [msg]
                current_chunk_start_timestamp = msg["timestamp"]
                continue

        # No break - append to current chunk
        current_chunk_messages.append(msg)

        # Check for hard max token limit
        current_tokens = get_current_token_count()
        if current_tokens >= settings.chunk_max:
            # Need to split - finalize with overlap
            # Find a good split point (aim for roughly half)
            split_point = len(current_chunk_messages) // 2

            # Finalize first half
            chunk = finalize_chunk(current_chunk_messages[:split_point])
            if chunk:
                chunks.append(chunk)

            # Start new chunk with overlap (last chunk_overlap tokens from previous)
            overlap_content = chunk.content if chunk else ""
            overlap_lines = overlap_content.split("\n")[-settings.chunk_overlap :]

            # Create overlap messages from the last part of the previous chunk
            overlap_messages = current_chunk_messages[split_point:]

            # Rebuild with overlap
            current_chunk_messages = overlap_messages
            current_chunk_start_timestamp = (
                current_chunk_messages[0]["timestamp"]
                if current_chunk_messages
                else msg["timestamp"]
            )

    # Finalize last chunk
    if current_chunk_messages:
        chunk = finalize_chunk(current_chunk_messages)
        if chunk:
            chunks.append(chunk)

    return chunks


async def chunk_messages() -> int:
    """
    Main entry point - chunk all unembedded messages.
    Returns the number of chunks created.
    """
    logger.info("Starting message chunking...")

    # Get all messages grouped by chat
    chats = await get_unembedded_messages()

    if not chats:
        logger.info("No messages to chunk")
        return 0

    total_chunks = 0

    # Process each chat
    for chat_id, messages in chats.items():
        chat_name = messages[0]["chat_name"] if messages else "Unknown"
        logger.info(f"Chunking chat {chat_name} ({len(messages)} messages)")

        chunks = chunk_chat(messages)

        # Insert chunks into database using a single batch lock for this chat
        from db.database import get_connection, _write_lock
        async with _write_lock:
            db = await get_connection()
            try:
                for chunk in chunks:
                    # Check if chunk with this content hash already exists
                    cursor = await db.execute(
                        "SELECT id FROM chunks WHERE content_hash = ?",
                        (chunk.content_hash,),
                    )
                    existing = await cursor.fetchone()

                    if existing:
                        logger.debug(
                            f"Skipping duplicate chunk: {chunk.content_hash[:8]}..."
                        )
                        continue

                    await db.execute(
                        """
                        INSERT INTO chunks (
                            chunk_id, chat_id, chat_name, participants,
                            timestamp_start, timestamp_end, message_count,
                            content, content_hash, embedding_version, embedded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            chunk.chunk_id,
                            chunk.chat_id,
                            chunk.chat_name,
                            json.dumps(chunk.participants),
                            chunk.timestamp_start,
                            chunk.timestamp_end,
                            chunk.message_count,
                            chunk.content,
                            chunk.content_hash,
                            settings.embedding_model,
                        ),
                    )
                    total_chunks += 1

                # Update last_chunked_at for this chat so we don't process these messages again
                if messages:
                    last_ts = messages[-1]["timestamp"]
                    await db.execute(
                        "UPDATE chats SET last_chunked_at = ? WHERE chat_id = ?",
                        (last_ts, chat_id)
                    )

                await db.commit()
            finally:
                await db.close()

        logger.info(
            f"Created {len(chunks)} chunks from {len(messages)} messages in {chat_name}"
        )

    logger.info(f"Chunking complete. Total chunks created: {total_chunks}")
    return total_chunks


async def chunk_messages_streaming() -> AsyncGenerator[dict, None]:
    """
    Generator version of chunk_messages for SSE progress reporting.
    """
    logger.info("Starting message chunking (streaming)...")

    chats = await get_unembedded_messages()

    if not chats:
        yield {"type": "progress", "stage": "chunk", "message": "No messages to chunk"}
        yield {"type": "done", "chunks_created": 0}
        return

    total_chunks = 0

    for chat_id, messages in chats.items():
        chat_name = messages[0]["chat_name"] if messages else "Unknown"
        yield {
            "type": "progress",
            "stage": "chunk",
            "message": f"Processing {chat_name}...",
        }

        chunks = chunk_chat(messages)

        from db.database import get_connection, _write_lock
        async with _write_lock:
            db = await get_connection()
            try:
                for i, chunk in enumerate(chunks):
                    # Check if chunk with this content hash already exists
                    cursor = await db.execute(
                        "SELECT id FROM chunks WHERE content_hash = ?",
                        (chunk.content_hash,),
                    )
                    existing = await cursor.fetchone()

                    if existing:
                        continue

                    await db.execute(
                        """
                        INSERT INTO chunks (
                            chunk_id, chat_id, chat_name, participants,
                            timestamp_start, timestamp_end, message_count,
                            content, content_hash, embedding_version, embedded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            chunk.chunk_id,
                            chunk.chat_id,
                            chunk.chat_name,
                            json.dumps(chunk.participants),
                            chunk.timestamp_start,
                            chunk.timestamp_end,
                            chunk.message_count,
                            chunk.content,
                            chunk.content_hash,
                            settings.embedding_model,
                        ),
                    )
                    total_chunks += 1

                    # Periodic progress within large chats
                    if i % 50 == 0 and i > 0:
                        yield {
                            "type": "progress",
                            "stage": "chunk",
                            "message": f"Processing {chat_name}: Created {i} chunks...",
                        }

                # Update last_chunked_at for this chat so we don't process these messages again
                if messages:
                    last_ts = messages[-1]["timestamp"]
                    await db.execute(
                        "UPDATE chats SET last_chunked_at = ? WHERE chat_id = ?",
                        (last_ts, chat_id)
                    )

                await db.commit()
            finally:
                await db.close()

        yield {
            "type": "progress",
            "stage": "chunk",
            "message": f"Created {len(chunks)} chunks from {chat_name}",
        }

    yield {"type": "done", "chunks_created": total_chunks}
