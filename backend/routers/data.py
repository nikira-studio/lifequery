"""Data management router - sync, import, reindex, stats."""

from datetime import datetime
from typing import AsyncGenerator, Union

from chunker.chunker import chunk_messages_streaming
from config import settings
from db.database import (
    DATA_DIR,
    DB_PATH,
    _write_lock,
    execute_write,
    fetch_all,
    fetch_one,
    get_connection,
    get_db,
)
from embedding import embed_chunks_incremental, reindex_all
from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from schemas import (
    ChatUpdateRequest,
    DoneEvent,
    ErrorEvent,
    ImportPathRequest,
    ProgressEvent,
    ReindexRequest,
    StatsResponse,
    SyncLogResponse,
)
from sse_starlette import EventSourceResponse
from sse_starlette.sse import ServerSentEvent
from telegram.json_import import import_json_file
from telegram.telethon_sync import sync_telegram_messages
from utils.logger import get_logger
from utils.sse import create_error_event, create_progress_event, create_sse_event

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["data"])


# ============================================================================
# Chat Management Endpoints (moved from routers/chats.py)
# ============================================================================


@router.get("/chats")
async def list_chats() -> dict:
    """List all chats with inclusion status."""
    try:
        # Auto-cleanup of 'ghost' groups when disconnected or on refresh.
        # This keeps the UI tidy even if the user doesn't hit 'Clean Up'.
        from telegram.telethon_sync import get_telegram_status
        status = await get_telegram_status()
        if status.get("state") != "connected":
            async with _write_lock:
                db = await get_connection()
                try:
                    await db.execute("""
                        DELETE FROM chats 
                        WHERE (message_count < 1 OR message_count IS NULL)
                        AND NOT EXISTS (SELECT 1 FROM messages WHERE messages.chat_id = chats.chat_id)
                    """)
                    await db.commit()
                except Exception as e:
                    logger.debug(f"Auto-cleanup failed (benign): {e}")
                finally:
                    await db.close()

        rows = await fetch_all(
            """
            SELECT chat_id, chat_name, chat_type, included, message_count, last_message_at, created_at
            FROM chats
            ORDER BY last_message_at DESC
            """
        )

        chats = []
        for row in rows:
            chats.append(
                {
                    "chat_id": row["chat_id"],
                    "chat_name": row["chat_name"],
                    "chat_type": row["chat_type"],
                    "included": bool(row["included"]),
                    "message_count": row["message_count"],
                    "last_message_at": row["last_message_at"],
                    "created_at": row["created_at"],
                }
            )

        return {"chats": chats}
    except Exception as e:
        logger.error(f"Error listing chats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/chats/{chat_id}")
async def update_chat(chat_id: str, request: ChatUpdateRequest = Body(...)) -> dict:
    """Update chat inclusion status."""
    included = request.included
    try:
        existing = await fetch_one("SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,))

        if not existing:
            raise HTTPException(status_code=404, detail="Chat not found")

        await execute_write(
            "UPDATE chats SET included = ? WHERE chat_id = ?",
            (1 if included else 0, chat_id),
        )

        logger.info(f"Updated chat {chat_id}: included={included}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str) -> dict:
    """Delete chat messages and chunks from LifeQuery and marks as excluded."""
    import asyncio
    logger.info(f"Delete chat requested: {chat_id}")
    try:
        # 1. Check if chat exists and get name
        existing = await fetch_one("SELECT chat_name FROM chats WHERE chat_id = ?", (chat_id,))
        if not existing:
            raise HTTPException(status_code=404, detail="Chat not found")
        chat_name = existing["chat_name"]

        # 2. Get chunk IDs for Chroma cleanup (before deleting from SQLite)
        chunk_rows = await fetch_all("SELECT chunk_id FROM chunks WHERE chat_id = ?", (chat_id,))
        chunk_ids = [row["chunk_id"] for row in chunk_rows]

        # 3. Check source of messages to see if it's a manual import
        sources_rows = await fetch_all("SELECT DISTINCT source FROM messages WHERE chat_id = ?", (chat_id,))
        sources = [row["source"] for row in sources_rows]
        
        # Determine if we should delete the chat entry entirely:
        # 1. It only has messages from manual JSON imports
        # 2. OR it already has 0 messages (user is deleting a 'ghost' entry)
        is_manual_only = all(s == 'json_import' for s in sources)
        has_no_messages = len(sources) == 0
        
        should_delete_record = is_manual_only or has_no_messages

        # 4. Delete from SQLite with write lock
        messages_deleted = 0
        chunks_deleted = 0

        async with _write_lock:
            db = await get_connection()
            try:
                cursor = await db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
                messages_deleted = cursor.rowcount
                cursor = await db.execute("DELETE FROM chunks WHERE chat_id = ?", (chat_id,))
                chunks_deleted = cursor.rowcount
                
                if should_delete_record:
                    # If it was manual data or already empty, delete it entirely
                    await db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
                    logger.info(f"Deleted chat record {chat_id} entirely from database.")
                else:
                    # For Telegram chats, mark as excluded to prevent auto-re-sync
                    await db.execute(
                        "UPDATE chats SET included = 0, message_count = 0 WHERE chat_id = ?",
                        (chat_id,),
                    )
                
                await db.commit()
            finally:
                await db.close()

        # 5. Chroma cleanup
        if chunk_ids:
            from vector_store.chroma import _get_collection

            def _chroma_delete():
                try:
                    _get_collection().delete(ids=chunk_ids)
                except Exception as exc:
                    logger.warning(f"ChromaDB delete error for {chat_id}: {exc}")

            await asyncio.get_event_loop().run_in_executor(None, _chroma_delete)
            logger.info(f"Deleted {len(chunk_ids)} chunks from ChromaDB for {chat_id}")

        logger.info(
            f"Deleted chat {chat_id} ({chat_name}): "
            f"{messages_deleted} messages, {chunks_deleted} chunks"
        )
        return {
            "ok": True,
            "messages_deleted": messages_deleted,
            "chunks_deleted": chunks_deleted,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def sync_chats_generator() -> AsyncGenerator[ServerSentEvent, None]:
    """Generator for syncing chat list from Telegram.

    Yields:
        Progress and completion events
    """
    try:
        yield create_progress_event("sync_chats", "Checking for new chats...")

        import aiosqlite
        from config import settings
        from telegram.telethon_sync import (
            get_telegram_status,
            _load_session_string,
            ensure_chat_entry,
        )
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        status = await get_telegram_status()
        if status.get("state") != "connected":
            yield create_progress_event("sync_chats", "Cleaning up list...")
            removed_count = 0
            async with _write_lock:
                db = await get_connection()
                try:
                    # Comprehensive cleanup:
                    # 1. No messages in message table
                    # 2. metadata says 0 messages
                    cursor = await db.execute("""
                        DELETE FROM chats 
                        WHERE (message_count < 1 OR message_count IS NULL)
                        AND NOT EXISTS (SELECT 1 FROM messages WHERE messages.chat_id = chats.chat_id)
                    """)
                    removed_count = cursor.rowcount
                    await db.commit()
                    logger.info(f"Manual cleanup: removed {removed_count} stale/empty chats.")
                except Exception as e:
                    await db.rollback()
                    logger.error(f"Error in disconnected chat cleanup: {e}")
                    raise
                finally:
                    await db.close()

            yield create_sse_event(
                {"type": "done", "updated": 0, "new": 0, "removed": removed_count}
            )
            return

        session_string = await _load_session_string()
        client = TelegramClient(
            StringSession(session_string or ""),
            int(settings.telegram_api_id),
            settings.telegram_api_hash,
        )

        await client.connect()
        if not await client.is_user_authorized():
            yield create_error_event("Telegram session not authorized")
            await client.disconnect()
            return

        dialogs = await client.get_dialogs()

        yield create_progress_event("sync_chats", f"Checking {len(dialogs)} dialogs...")

        telegram_chat_ids = {str(d.entity.id) for d in dialogs}
        updated_count = 0
        new_count = 0
        removed_count = 0
        now_ts = int(datetime.now().timestamp())

        from telethon.tl.types import Channel, Chat as TgChat

        # Use a single write lock + transaction for the whole batch.
        # IMPORTANT: do NOT call execute_write() or ensure_chat_entry() inside this
        # block — those also acquire _write_lock, which would deadlock.
        async with _write_lock:
            db = await get_connection()
            try:
                await db.execute("BEGIN")

                for dialog in dialogs:
                    chat_id = str(dialog.entity.id)
                    chat_name = getattr(dialog.entity, "title", None) or getattr(
                        dialog.entity, "first_name", "Unknown"
                    )

                    chat_type = "private"
                    if isinstance(dialog.entity, TgChat):
                        chat_type = "group"
                    elif isinstance(dialog.entity, Channel):
                        chat_type = "channel"

                    cursor = await db.execute(
                        "SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,)
                    )
                    existing = await cursor.fetchone()

                    if existing:
                        updated_count += 1
                        await db.execute(
                            "UPDATE chats SET chat_name = ?, chat_type = ? WHERE chat_id = ?",
                            (chat_name, chat_type, chat_id),
                        )
                    else:
                        new_count += 1
                        await db.execute(
                            """INSERT INTO chats
                               (chat_id, chat_name, chat_type, included, message_count,
                                last_message_at, created_at)
                               VALUES (?, ?, ?, 1, 0, 0, ?)""",
                            (chat_id, chat_name, chat_type, now_ts),
                        )

                # Remove chats that no longer exist in Telegram AND have no messages.
                # Chats with messages are kept (could be manually imported or already synced).
                cursor = await db.execute("SELECT chat_id FROM chats")
                all_db_chat_ids = {row[0] for row in await cursor.fetchall()}
                stale_ids = all_db_chat_ids - telegram_chat_ids
                for stale_id in stale_ids:
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM messages WHERE chat_id = ?", (stale_id,)
                    )
                    row = await cursor.fetchone()
                    if row and row[0] == 0:
                        await db.execute("DELETE FROM chats WHERE chat_id = ?", (stale_id,))
                        removed_count += 1
                        logger.info(f"Removed empty stale chat {stale_id} (no longer in Telegram)")

                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(f"Error in chat sync loop: {e}")
                raise
            finally:
                await db.close()

        await client.disconnect()

        yield create_sse_event(
            {"type": "done", "updated": updated_count, "new": new_count, "removed": removed_count}
        )

    except Exception as e:
        logger.error(f"Error syncing chats: {e}")
        yield create_error_event(str(e))


@router.post("/chats/sync")
async def sync_chats() -> EventSourceResponse:
    """Sync chat list from Telegram (SSE).

    Returns:
        EventSourceResponse yielding progress and completion events
    """
    return EventSourceResponse(sync_chats_generator(), headers={"X-Accel-Buffering": "no"})


@router.get("/stats", response_model=StatsResponse)
async def get_stats() -> StatsResponse:
    """Get database statistics."""
    try:
        # Message count
        row = await fetch_one("SELECT COUNT(*) as count FROM messages")
        message_count = row["count"] if row else 0

        # Chunk count
        row = await fetch_one("SELECT COUNT(*) as count FROM chunks")
        chunk_count = row["count"] if row else 0

        # Embedded count
        row = await fetch_one("SELECT COUNT(*) as count FROM chunks WHERE embedded_at IS NOT NULL")
        embedded_count = row["count"] if row else 0

        # Chat count (unique chats in database)
        row = await fetch_one("SELECT COUNT(*) as count FROM chats")
        chat_count = row["count"] if row else 0

        # Included chat count
        row = await fetch_one("SELECT COUNT(*) as count FROM chats WHERE included = 1")
        included_chat_count = row["count"] if row else 0

        # Excluded chat count
        row = await fetch_one("SELECT COUNT(*) as count FROM chats WHERE included = 0")
        excluded_chat_count = row["count"] if row else 0

        # Last sync info
        row = await fetch_one(
            "SELECT finished_at, messages_added, chunks_created FROM sync_log "
            "ORDER BY id DESC LIMIT 1"
        )
        
        last_sync = None
        last_sync_added = 0
        if row:
            last_sync = row["finished_at"]
            last_sync_added = row["messages_added"] or 0

        return StatsResponse(
            message_count=message_count,
            chunk_count=chunk_count,
            chat_count=chat_count,
            included_chat_count=included_chat_count,
            excluded_chat_count=excluded_chat_count,
            embedded_count=embedded_count,
            last_sync=last_sync,
            last_sync_added=last_sync_added,
        )
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending-stats")
async def get_pending_stats() -> dict:
    """Get counts of messages needing chunking and chunks needing embedding."""
    try:
        # Messages needing chunking: Any message whose chat_id exists in 'messages' 
        # but for which we haven't recently calculated a chunk coverage.
        # Actually, our chunker just re-scans everything and skips existing.
        # So we'll approximate 'pending' as 'newly imported messages'.
        # Messages needing chunking: Any message whose chat_id has no entries in the chunks table yet.
        # This avoids massive joins on chat_id.
        # Optimize: Instead of checking every message, check if the chat has any messages but 0 chunks.
        # This is incredibly fast compared to NOT EXISTS on the messages table directly.
        row = await fetch_one(
            """
            SELECT SUM(m.count) as count FROM (
                SELECT COUNT(id) as count, chat_id 
                FROM messages 
                GROUP BY chat_id 
                HAVING (SELECT COUNT(*) FROM chunks WHERE chunks.chat_id = messages.chat_id) = 0
            ) m
            """
        )
        unchunked = row["count"] if row else 0

        # Chunks needing embedding: explicitly check NULL embedded_at
        row = await fetch_one("SELECT COUNT(*) as count FROM chunks WHERE embedded_at IS NULL")
        unembedded = row["count"] if row else 0

        return {
            "unchunked_messages": unchunked,
            "unembedded_chunks": unembedded,
            "has_pending": unchunked > 0 or unembedded > 0
        }
    except Exception as e:
        logger.error(f"Error getting pending stats: {e}")
        return {"unchunked_messages": 0, "unembedded_chunks": 0, "has_pending": False}


async def _log_operation(
    operation: str,
    started_at: int,
    status: str,
    messages_added: int = 0,
    chunks_created: int = 0,
    skipped_duplicate: int = 0,
    skipped_empty: int = 0,
    detail: str | None = None,
):
    """Log an operation to the sync_log table."""
    await execute_write(
        """INSERT INTO sync_log
           (operation, started_at, finished_at, status, messages_added, chunks_created,
            skipped_duplicate, skipped_empty, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            operation,
            started_at,
            int(datetime.now().timestamp()),
            status,
            messages_added,
            chunks_created,
            skipped_duplicate,
            skipped_empty,
            detail,
        ),
    )


async def sync_generator() -> AsyncGenerator[ServerSentEvent, None]:
    """SSE generator for Telegram sync operation.

    Yields:
        ServerSentEvent with progress, done, or error data
    """
    start_ts = int(datetime.now().timestamp())
    total_messages = 0
    total_chunks = 0
    total_embedded = 0
    skipped_duplicate = 0
    skipped_empty = 0
    sync_cancelled = False

    logger.info("Sync request received — starting full sync pipeline")
    try:
        # Step 1: Sync messages from Telegram
        async for event in sync_telegram_messages():
            if event.get("type") == "progress":
                yield create_progress_event(
                    "ingest", event.get("message", "Syncing...")
                )
            elif event.get("type") == "done":
                total_messages = event.get("inserted", 0)
                skipped_duplicate = event.get("skipped_duplicate", 0)
                skipped_empty = event.get("skipped_empty", 0)
                sync_cancelled = event.get("cancelled", False)
                logger.info(
                    f"Sync {'cancelled' if sync_cancelled else 'complete'}: "
                    f"{total_messages} inserted, "
                    f"{skipped_duplicate} duplicates, {skipped_empty} empty skipped"
                )
            elif event.get("type") == "error":
                msg = event.get("message", "Unknown sync error")
                yield create_error_event(msg)
                await _log_operation("sync", start_ts, "error", detail=msg)
                return
            else:
                yield create_progress_event(
                    "ingest", event.get("message", "Syncing...")
                )

        # Step 2: Chunk messages
        yield create_progress_event("chunk", "Chunking messages...")

        try:
            async for event in chunk_messages_streaming():
                if event.get("type") == "progress":
                    yield create_progress_event(
                        "chunk", event.get("message", "Chunking...")
                    )
                elif event.get("type") == "done":
                    total_chunks = event.get("chunks_created", 0)
                    logger.info(f"Chunking complete: {total_chunks} chunks created")
        except Exception as e:
            logger.error(f"Chunking error: {e}", exc_info=True)
            yield create_error_event(f"Chunking failed: {str(e)}")
            await _log_operation("sync", start_ts, "error", detail=f"Chunking failed: {e}", 
                               messages_added=total_messages)
            return

        # Step 3: Embed chunks
        yield create_progress_event("embed", "Stage 3: Embedding chunks (Final step)...")
        total_embedded = 0

        try:
            async for event in embed_chunks_incremental():
                if event.get("type") == "progress":
                    yield create_progress_event(
                        "embed", event.get("message", "Embedding...")
                    )
                elif event.get("type") == "done":
                    total_embedded = event.get('embedded', 0)
                    logger.info(
                        f"Embedding complete: {total_embedded} embedded, "
                        f"{event.get('skipped', 0)} skipped"
                    )
                    yield create_progress_event(
                        "embed", f"Embedding complete! Processed {total_embedded} chunks."
                    )
                elif event.get("type") == "error":
                    raise RuntimeError(event.get("message", "Embedding error"))
        except Exception as e:
            logger.error(f"Embedding error: {e}", exc_info=True)
            yield create_error_event(f"Embedding failed: {str(e)}")
            await _log_operation("sync", start_ts, "error", detail=f"Embedding failed: {e}",
                               messages_added=total_messages, chunks_created=total_chunks)
            return

        # Step 4: Log successful sync operation
        await _log_operation(
            "sync",
            start_ts,
            "success",
            messages_added=total_messages,
            chunks_created=total_chunks,
            skipped_duplicate=skipped_duplicate,
            skipped_empty=skipped_empty,
        )

        # Send completion event
        yield create_sse_event(
            {
                "type": "done",
                "messages_added": total_messages,
                "inserted": total_messages,
                "skipped_duplicate": skipped_duplicate,
                "skipped_empty": skipped_empty,
                "chunks_created": total_chunks,
                "chunks_embedded": total_embedded,
                "cancelled": sync_cancelled,
            }
        )

    except Exception as e:
        logger.error(f"Sync generator error: {e}", exc_info=True)
        yield create_error_event(f"Sync failed: {str(e)}")
        await _log_operation("sync", start_ts, "error", detail=str(e),
                           messages_added=total_messages, chunks_created=total_chunks)


@router.post("/process")
async def start_process():
    """Process pending data (Chunking & Embedding) without Telegram sync."""
    logger.info("Starting manual data processing (chunk/embed)")
    async def process_generator():
        start_ts = int(datetime.now().timestamp())
        total_chunks = 0
        total_embedded = 0
        
        try:
            # Step 1: Chunking
            yield create_progress_event("chunk", "Chunking new/imported messages...")
            async for event in chunk_messages_streaming():
                if event.get("type") == "progress":
                    yield create_progress_event("chunk", event.get("message"))
                elif event.get("type") == "done":
                    total_chunks = event.get("chunks_created", 0)
            
            # Step 2: Embedding
            yield create_progress_event("embed", "Embedding new chunks...")
            async for event in embed_chunks_incremental():
                if event.get("type") == "progress":
                    yield create_progress_event("embed", event.get("message"))
                elif event.get("type") == "done":
                    total_embedded = event.get("embedded", 0)
            
            await _log_operation("process", start_ts, "success", 
                               chunks_created=total_chunks, 
                               detail=f"Processed {total_chunks} chunks, {total_embedded} embedded.")
                               
            yield create_sse_event({
                "type": "done",
                "chunks_created": total_chunks,
                "chunks_embedded": total_embedded
            })
        except Exception as e:
            logger.error(f"Manual process error: {e}")
            yield create_error_event(str(e))
            await _log_operation("process", start_ts, "error", detail=str(e))

    return EventSourceResponse(process_generator(), headers={"X-Accel-Buffering": "no"})


@router.post("/sync")
async def start_sync():
    """Start Telegram sync - returns SSE stream.

    Performs:
    1. Sync messages from Telegram
    2. Chunk new messages
    3. Embed new/changed chunks

    Returns SSE stream with progress updates.
    """
    logger.info("Starting Telegram sync")
    return EventSourceResponse(sync_generator(), headers={"X-Accel-Buffering": "no"})


@router.post("/sync/cancel")
async def cancel_sync_endpoint() -> dict:
    """Signal the running sync to stop after the current chat finishes."""
    from telegram.telethon_sync import cancel_sync
    cancel_sync()
    logger.info("Sync cancel requested")
    return {"ok": True}


async def import_generator(
    file: Union[str, UploadFile], username: str | None = None
) -> AsyncGenerator[ServerSentEvent, None]:
    """SSE generator for JSON import operation.
    """
    import os
    import tempfile
    import shutil

    total_messages = 0
    skipped_duplicate = 0
    skipped_empty = 0
    file_path = ""

    start_ts = int(datetime.now().timestamp())
    try:
        # Step 0: Handle UploadFile if necessary
        if not isinstance(file, str):
            yield create_progress_event("import", f"Saving {file.filename} to server storage...")
            # Create a persistent temp file for processing
            with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".json") as tmp:
                file_path = tmp.name
                # Read from UploadFile (which is already buffered by FastAPI)
                while chunk := await file.read(1024 * 1024):
                    tmp.write(chunk)
            yield create_progress_event("import", "Upload complete. Validating structure...")
        else:
            file_path = file

        # Ingest messages from JSON file
        yield create_progress_event("import", "Ingesting messages into database...")
        async for event in import_json_file(file_path, username=username):
            if event.get("type") == "progress":
                yield create_progress_event(
                    "import", event.get("message", "Ingesting JSON...")
                )
            elif event.get("type") == "done":
                total_messages = event.get("inserted", 0)
                skipped_duplicate = event.get("skipped_duplicate", 0)
                skipped_empty = event.get("skipped_empty", 0)
                logger.info(
                    f"Ingestion complete: {total_messages} messages, "
                    f"{skipped_duplicate} duplicates, {skipped_empty} empty skipped"
                )
            elif event.get("type") == "error":
                msg = event.get("message", "Unknown ingestion error")
                yield create_error_event(msg)
                await _log_operation("import", start_ts, "error", detail=msg)
                return
            else:
                yield create_progress_event(
                    "import", event.get("message", "Ingesting...")
                )

        # Log successful import
        await _log_operation(
            "import",
            start_ts,
            "success",
            messages_added=total_messages,
            skipped_duplicate=skipped_duplicate,
            skipped_empty=skipped_empty,
        )

        # Send completion event
        yield create_sse_event(
            {
                "type": "done",
                "messages_added": total_messages,
                "inserted": total_messages,
                "skipped_duplicate": skipped_duplicate,
                "skipped_empty": skipped_empty,
                "duration": int(datetime.now().timestamp()) - start_ts,
            }
        )

    except Exception as e:
        logger.error(f"Import generator error: {e}", exc_info=True)
        yield create_error_event(f"Import failed: {str(e)}")
        await _log_operation("import", start_ts, "error", detail=str(e),
                           messages_added=total_messages)
    finally:
        # Always clean up the temp file
        try:
            os.unlink(file_path)
        except OSError:
            pass


@router.post("/import")
async def start_import(
    file: UploadFile = File(...),
    username: str | None = Form(None),
):
    """Import messages from a Telegram JSON export file.
    
    Returns an SSE stream immediately while handling the file transfer.
    """
    # Quick extension check
    if not file.filename or not file.filename.lower().endswith((".json",)):
        raise HTTPException(status_code=400, detail="Only JSON files are supported.")
        
    logger.info(f"Initiating import SSE stream for: {file.filename}")
    
    return EventSourceResponse(
        import_generator(file, username), 
        headers={"X-Accel-Buffering": "no"}
    )


@router.post("/import/path")
async def start_import_path(
    request: ImportPathRequest,
):
    """Import messages from a Telegram JSON export file via local path.

    This bypasses HTTP upload limits by reading directly from the server's filesystem.
    """
    import os
    import shutil
    import tempfile

    target_path = request.path
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"File not found: {target_path}")

    if not target_path.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Only JSON files are supported")

    logger.info(f"Starting import from local path: {target_path}")

    # Create a temporary copy because import_generator unlinks the file at the end
    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".json") as tmp:
        with open(target_path, "rb") as f:
            shutil.copyfileobj(f, tmp)
        tmp_path = tmp.name

    return EventSourceResponse(
        import_generator(tmp_path, request.username), headers={"X-Accel-Buffering": "no"}
    )


@router.get("/import/scanned")
async def list_scanned_imports():
    """List JSON files in the server's imports directory."""
    import os

    im_dir = DATA_DIR / "imports"
    im_dir.mkdir(parents=True, exist_ok=True)
    
    files = []
    for f in os.listdir(im_dir):
        if f.lower().endswith(".json"):
            full_path = str(im_dir / f)
            stats = os.stat(full_path)
            files.append({
                "name": f,
                "path": full_path,
                "size_mb": round(stats.st_size / (1024 * 1024), 2),
                "modified": int(stats.st_mtime)
            })
            
    # Sort by modified time (newest first)
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files, "directory": str(im_dir)}


async def reindex_generator() -> AsyncGenerator[ServerSentEvent, None]:
    """SSE generator for reindex operation.

    Yields:
        ServerSentEvent with progress, done, or error data
    """
    start_ts = int(datetime.now().timestamp())
    total_chunks = 0
    try:
        # Step 1: Re-chunk messages
        yield create_progress_event("reindex", "Re-chunking all messages...")

        try:
            # Step 1: Clear existing chunks to ensure a clean slate
            # This is important if chunking settings (size/overlap) have changed.
            yield create_progress_event("reindex", "Clearing old chunks...")
            await execute_write("DELETE FROM chunks")

            async for event in chunk_messages_streaming():
                if event.get("type") == "progress":
                    yield create_progress_event(
                        "reindex", event.get("message", "Re-chunking...")
                    )
                elif event.get("type") == "done":
                    total_chunks = event.get("chunks_created", 0)

            logger.info(f"Re-chunking complete: {total_chunks} chunks")
        except Exception as e:
            logger.error(f"Re-chunking error: {e}", exc_info=True)
            yield create_error_event(f"Re-chunking failed: {str(e)}")
            await _log_operation("reindex", start_ts, "error", detail=f"Re-chunking failed: {e}")
            return

        # Step 2: Re-embed all chunks (full reindex)
        yield create_progress_event("reindex", "Re-embedding all chunks...")

        try:
            final_counts = {"embedded": 0, "errors": 0}
            async for event in reindex_all():
                if event.get("type") == "progress":
                    current = event.get("current", 0)
                    total = event.get("total", 0)
                    yield create_progress_event(
                        "reindex", f"Re-embedding: {current}/{total} chunks..."
                    )
                elif event.get("type") == "done":
                    final_counts["embedded"] = event.get("embedded", 0)
                    final_counts["errors"] = event.get("errors", 0)

            logger.info(
                f"Reindex complete: {final_counts['embedded']} chunks embedded, "
                f"{final_counts['errors']} errors"
            )
            
            # Log successful reindex
            await _log_operation(
                "reindex",
                start_ts,
                "success",
                chunks_created=total_chunks,
                detail=f"Re-embedded {final_counts['embedded']} chunks ({final_counts['errors']} errors)"
            )

            # Send completion event
            yield create_sse_event(
                {"type": "done", "chunks_embedded": final_counts["embedded"]}
            )

        except Exception as e:
            logger.error(f"Reindex error: {e}", exc_info=True)
            yield create_error_event(f"Reindex failed: {str(e)}")
            await _log_operation("reindex", start_ts, "error", detail=f"Re-embedding failed: {e}", 
                               chunks_created=total_chunks)

    except Exception as e:
        logger.error(f"Reindex generator error: {e}", exc_info=True)
        yield create_error_event(f"Reindex failed: {str(e)}")
        await _log_operation("reindex", start_ts, "error", detail=str(e))


@router.post("/reindex")
async def start_reindex(request: ReindexRequest):
    """Re-index all messages - re-chunk and re-embed.

    This is a destructive operation that:
    1. Re-chunks all messages from the database
    2. Wipes the vector store
    3. Re-embeds all chunks with the current embedding model

    Requires explicit confirmation to prevent accidental data loss.

    Args:
        request: ReindexRequest with confirm field set to true

    Raises:
        HTTPException: If confirm is not true

    Returns:
        SSE stream with progress updates
    """
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Set 'confirm': true in request body.",
        )

    logger.info("Starting full reindex")
    return EventSourceResponse(reindex_generator(), headers={"X-Accel-Buffering": "no"})


@router.get("/sync/logs", response_model=SyncLogResponse)
async def get_sync_logs(limit: int = 50) -> SyncLogResponse:
    """Get history of sync/import operations.

    Args:
        limit: Maximum number of logs to return (default 50)

    Returns:
        SyncLogResponse with list of log entries
    """
    rows = await fetch_all(
        """
        SELECT id, operation, started_at, finished_at, status,
               messages_added, chunks_created, skipped_duplicate, skipped_empty, detail
        FROM sync_log
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    )

    logs = [
        {
            "id": row["id"],
            "operation": row["operation"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "status": row["status"],
            "messages_added": row["messages_added"] or 0,
            "chunks_created": row["chunks_created"] or 0,
            "skipped_duplicate": row["skipped_duplicate"] or 0,
            "skipped_empty": row["skipped_empty"] or 0,
            "detail": row["detail"],
        }
        for row in rows
    ]

    return SyncLogResponse(logs=logs)
