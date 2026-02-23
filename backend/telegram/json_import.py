"""Telegram JSON export import."""

import json
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

import ijson
from db.database import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB


def flatten_text(text_field) -> str:
    """Flatten the text field which can be a string or a list of entity objects."""
    if text_field is None:
        return ""
    if isinstance(text_field, str):
        return text_field
    if isinstance(text_field, list):
        return "".join(
            part if isinstance(part, str) else part.get("text", "")
            for part in text_field
        )
    return str(text_field)


async def import_json_file(
    file_path: str,
    progress_callback: Optional[AsyncGenerator[dict, None]] = None,
    username: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Import messages from a Telegram JSON export file.

    Args:
        file_path: Path to the JSON file
        progress_callback: Optional callback for progress updates
        username: Optional username to use for message attribution
              (useful when importing from a deleted account)
    """
    path = Path(file_path)

    # Validate file exists
    if not path.exists():
        raise ValueError("File not found")

    # Validate file size
    file_size = path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        raise ValueError(
            f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB"
        )

    # Validate it's valid JSON by checking structure
    yield {
        "type": "progress",
        "stage": "import",
        "message": "Validating JSON structure...",
    }

    try:
        with open(path, "rb") as f:
            # Peek at the first bytes to check if it's a list or object
            first_char = f.read(1)
            f.seek(0)

            if first_char == b"[":
                # Top-level is a list of chats
                yield {
                    "type": "progress",
                    "stage": "import",
                    "message": "Importing chat list...",
                }
                async for result in _import_chat_list(f, progress_callback, username=username):
                    yield result
            else:
                # Single chat object
                yield {
                    "type": "progress",
                    "stage": "import",
                    "message": "Importing single chat...",
                }
                async for result in _import_single_chat(f, progress_callback, username=username):
                    yield result
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")
    except Exception as e:
        logger.error(f"Import error: {e}")
        raise ValueError(f"Import failed: {e}")


async def _import_chat_list(
    file_obj,
    progress_callback: Optional[AsyncGenerator[dict, None]] = None,
    username: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Import from a JSON file containing a list of chat objects."""
    total_imported = 0
    total_skipped_duplicate = 0
    total_skipped_empty = 0
    chat_count = 0

    # Use ijson to stream-parse the list
    parser = ijson.items(file_obj, "item")

    for chat_obj in parser:
        chat_count += 1
        chat_id = str(chat_obj.get("id", ""))
        chat_name = chat_obj.get("name", "Unknown")

        yield {
            "type": "progress",
            "stage": "import",
            "message": f"Processing chat {chat_count}: {chat_name}",
        }

        async for result in _import_chat_messages(
            chat_id, chat_name, chat_obj.get("messages", []), username=username
        ):
            if result.get("type") == "progress":
                yield result
            elif "inserted" in result:
                total_imported += result["inserted"]
                total_skipped_duplicate += result["skipped_duplicate"]
                total_skipped_empty += result["skipped_empty"]

    yield {
        "type": "done",
        "inserted": total_imported,
        "skipped_duplicate": total_skipped_duplicate,
        "skipped_empty": total_skipped_empty,
        "chats_imported": chat_count,
    }


async def _import_single_chat(
    file_obj,
    progress_callback: Optional[AsyncGenerator[dict, None]] = None,
    username: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Import from a JSON file containing a single chat object."""
    # Parse the full object since it's a single chat
    chat_obj = json.load(file_obj)

    chat_id = str(chat_obj.get("id", ""))
    chat_name = chat_obj.get("name", "Unknown")

    yield {
        "type": "progress",
        "stage": "import",
        "message": f"Processing: {chat_name}",
    }

    imported, skipped_duplicate, skipped_empty = 0, 0, 0
    async for result in _import_chat_messages(
        chat_id, chat_name, chat_obj.get("messages", []), username=username
    ):
        if result.get("type") == "progress":
            yield result
        elif "inserted" in result:
            imported = result["inserted"]
            skipped_duplicate = result["skipped_duplicate"]
            skipped_empty = result["skipped_empty"]

    yield {
        "type": "done",
        "inserted": imported,
        "skipped_duplicate": skipped_duplicate,
        "skipped_empty": skipped_empty,
        "chats_imported": 1,
    }


async def _import_chat_messages(
    chat_id: str,
    chat_name: str,
    messages: list,
    username: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Import messages from a chat's message list.

    Yields:
        Progress dicts or final summary dict
    """
    imported = 0
    skipped_duplicate = 0
    skipped_empty = 0
    imported_at = int(datetime.now().timestamp())
    last_timestamp = 0

    # Determine chat type (default to private for JSON imports)
    chat_type = "private"

    # Process in batches
    batch_size = 500
    batch = []

    for msg in messages:
        # Only import actual messages, skip service events
        if msg.get("type") != "message":
            skipped_empty += 1
            continue

        text = flatten_text(msg.get("text"))
        if not text or not text.strip():
            skipped_empty += 1
            continue

        # Parse timestamp
        date_str = msg.get("date", "")
        try:
            timestamp = int(
                datetime.fromisoformat(date_str.replace("Z", "+00:00")).timestamp()
            )
        except (ValueError, TypeError):
            timestamp = imported_at

        # Track last message timestamp
        if timestamp > last_timestamp:
            last_timestamp = timestamp

        # Extract sender info
        from_id = str(msg.get("from_id", ""))
        from_name = msg.get("from", "Unknown")

        # Use provided username for self-messages if applicable
        if username and (not from_name or from_name == "Unknown" or from_id.startswith("user")):
             if not msg.get("from") or msg.get("from") == username:
                 from_name = username

        batch.append(
            (
                str(msg.get("id", "")),
                chat_id,
                chat_name,
                from_id,
                from_name,
                text,
                timestamp,
                "json_import",
                imported_at,
            )
        )

        if len(batch) >= batch_size:
            result = await _insert_message_batch(batch)
            imported += result["imported"]
            skipped_duplicate += result["skipped"]
            batch = []
            
            # Yield progress to the generator
            yield {
                "type": "progress",
                "stage": "import",
                "message": f"Chat {chat_name}: Imported {imported} messages...",
            }

    # Insert remaining batch
    if batch:
        result = await _insert_message_batch(batch)
        imported += result["imported"]
        skipped_duplicate += result["skipped"]

    # Update or create chat entry in chats table
    await _update_chat_entry(
        chat_id=chat_id,
        chat_name=chat_name,
        chat_type=chat_type,
        message_count=imported, # Approximate
        last_message_at=last_timestamp,
    )

    yield {
        "inserted": imported,
        "skipped_duplicate": skipped_duplicate,
        "skipped_empty": skipped_empty,
    }


async def _insert_message_batch(messages: list) -> dict:
    """Insert a batch of messages into the database."""
    from db.database import get_connection, _write_lock
    async with _write_lock:
        db = await get_connection()
        try:
            imported = 0
            skipped = 0

            await db.execute("BEGIN")

            for msg in messages:
                try:
                    await db.execute(
                        """INSERT OR IGNORE INTO messages
                           (message_id, chat_id, chat_name, sender_id, sender_name, text, timestamp, source, imported_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        msg,
                    )
                    cursor = await db.execute("SELECT changes()")
                    row = await cursor.fetchone()
                    if row and row[0] > 0:
                        imported += 1
                    else:
                        skipped += 1
                except Exception as e:
                    logger.debug(f"Error inserting message: {e}")
                    skipped += 1

            await db.commit()
            return {"imported": imported, "skipped": skipped}
        finally:
            await db.close()


async def _update_chat_entry(
    chat_id: str,
    chat_name: str,
    chat_type: str,
    message_count: int,
    last_message_at: int,
) -> None:
    """Update or create a chat entry in the chats table."""
    from db.database import get_connection, _write_lock, fetch_one
    import time

    # 1. Check if chat exists (read-only)
    existing = await fetch_one("SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,))

    async with _write_lock:
        db = await get_connection()
        try:
            if existing:
                await db.execute(
                    """
                    UPDATE chats SET
                        chat_name = ?,
                        chat_type = ?,
                        message_count = message_count + ?,
                        last_message_at = CASE WHEN last_message_at < ? THEN ? ELSE last_message_at END
                    WHERE chat_id = ?
                    """,
                    (chat_name, chat_type, message_count, last_message_at, last_message_at, chat_id),
                )
            else:
                created_at = int(time.time())
                await db.execute(
                    """
                    INSERT INTO chats
                    (chat_id, chat_name, chat_type, included, message_count, last_message_at, created_at)
                    VALUES (?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        chat_name,
                        chat_type,
                        message_count,
                        last_message_at,
                        created_at,
                    ),
                )
            await db.commit()
        finally:
            await db.close()
