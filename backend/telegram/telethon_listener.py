"""Real-time background Telegram listener for live syncing."""

import asyncio
from datetime import datetime
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from config import settings
from db.database import (
    execute_fetchone,
    execute_write,
    fetch_all,
    update_message_if_unchunked,
    delete_messages_if_unchunked,
)
from telegram.telethon_sync import (
    _derive_chat_type,
    _derive_sender_name,
    _extract_forward_info,
    _insert_message,
    _load_session_string,
    _update_chat_entry,
    ensure_chat_entry,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Global state
_listener_task: Optional[asyncio.Task] = None
_client: Optional[TelegramClient] = None
_stop_event = asyncio.Event()

async def _handle_new_message(event) -> None:
    """Handle incoming new messages and insert into DB."""
    message = event.message
    if not message or not message.text:
        return

    chat_id = str(message.chat_id)
    chat = await event.get_chat()
    chat_name = getattr(chat, "title", None) or getattr(chat, "first_name", "Unknown")
    chat_type = _derive_chat_type(chat)

    sender = await event.get_sender()
    sender_name = _derive_sender_name(sender)
    sender_id = str(getattr(sender, "id", "")) if sender else ""
    
    # Ensure chat entry exists before we start tracking messages
    # In a fully real-time scenario, this is necessary.
    await ensure_chat_entry(chat_id=chat_id, chat_name=chat_name, chat_type=chat_type)

    logger.debug(f"Listener [NewMessage]: {chat_name} - {message.text[:50]}...")
    
    timestamp = int(message.date.timestamp())
    forward_info = await _extract_forward_info(message, _client)

    try:
        # Use existing sync helper to insert the message gracefully
        inserted = await _insert_message(
            message_id=str(message.id),
            chat_id=chat_id,
            chat_name=chat_name,
            sender_id=sender_id,
            sender_name=sender_name,
            text=message.text,
            timestamp=timestamp,
            source="telegram_listener",
            **forward_info,
        )
        if inserted:
            # Keep the chat message count roughly in sync without recounting the
            # whole table on every message. This avoids a read-heavy hot path.
            row = await execute_fetchone(
                "SELECT message_count FROM chats WHERE chat_id = ?",
                (chat_id,),
            )
            current_count = int(row[0]) if row and row[0] is not None else 0
            await execute_write(
                "UPDATE chats SET message_count = ?, last_message_at = ? WHERE chat_id = ?",
                (current_count + 1, timestamp, chat_id),
            )
    except Exception as e:
        logger.error(f"Listener Error [NewMessage]: {e}")

async def _handle_edit_message(event) -> None:
    """Handle edited messages and update DB if unchunked."""
    message = event.message
    if not message or not message.text:
        return

    chat_id = str(message.chat_id)
    message_id = str(message.id)
    new_text = message.text
    timestamp = int(message.date.timestamp())

    try:
        # Call the RAG-aware db helper
        updated = await update_message_if_unchunked(
            message_id=message_id, 
            chat_id=chat_id, 
            new_text=new_text, 
            timestamp=timestamp
        )
        if updated:
            logger.debug(f"Listener [EditMessage]: Updated msg {message_id} in chat {chat_id}")
    except Exception as e:
        logger.error(f"Listener Error [EditMessage]: {e}")

async def _handle_delete_message(event) -> None:
    """Handle deleted messages and update DB if unchunked."""
    try:
        chat_id = str(event.chat_id)
        # Event might contain multiple deleted message IDs
        message_ids = [str(x) for x in event.deleted_ids]

        if not message_ids:
            return

        deleted_count = await delete_messages_if_unchunked(
            chat_id=chat_id, 
            message_ids=message_ids
        )
        if deleted_count > 0:
            logger.debug(f"Listener [DeleteMessage]: Removed {deleted_count} msgs in chat {chat_id}")
    except Exception as e:
        logger.error(f"Listener Error [DeleteMessage]: {e}")

async def _run_listener_loop():
    """Background task that runs the Telethon client and reconnects if necessary."""
    global _client
    
    logger.info("Starting Telegram background listener loop...")
    
    while not _stop_event.is_set():
        try:
            session_string = await _load_session_string()
            if not session_string:
                # No session, sleep and try again later
                await asyncio.sleep(60)
                continue

            if not settings.telegram_api_id or not settings.telegram_api_hash:
                await asyncio.sleep(60)
                continue

            logger.info("Initializing Telethon Live Listener client...")
            _client = TelegramClient(
                StringSession(session_string),
                int(settings.telegram_api_id),
                settings.telegram_api_hash,
            )

            # Register event handlers
            _client.add_event_handler(_handle_new_message, events.NewMessage())
            _client.add_event_handler(_handle_edit_message, events.MessageEdited())
            _client.add_event_handler(_handle_delete_message, events.MessageDeleted())
            
            await _client.connect()
            
            if not await _client.is_user_authorized():
                logger.warning("Listener: Session is not authorized. Disconnecting.")
                await _client.disconnect()
                await asyncio.sleep(60)
                continue
                
            logger.info("Telethon Live Listener is now connected and listening!")
            
            # Wait until client is disconnected or stop event is set
            while not _stop_event.is_set() and _client.is_connected():
                await asyncio.sleep(5)
                
            if _client.is_connected():
                logger.info("Listener: Disconnecting client due to stop event.")
                await _client.disconnect()
                
        except Exception as e:
            logger.error(f"Listener loop encountered an error: {e}")
            # Prevent rapid retry loops
            await asyncio.sleep(30)
        finally:
            if _client and not _client.is_connected():
                _client = None

    logger.info("Telegram background listener loop stopped.")

def start_listener():
    """Start the background listener task."""
    global _listener_task, _stop_event
    _stop_event.clear()
    
    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.create_task(_run_listener_loop())
        logger.info("Started Telegram background listener task.")
    else:
        logger.warning("Telegram background listener is already running.")

async def stop_listener():
    """Stop the background listener task gracefully."""
    global _listener_task, _stop_event, _client
    
    logger.info("Stopping Telegram background listener...")
    _stop_event.set()
    
    if _client and _client.is_connected():
        await _client.disconnect()
        
    if _listener_task and not _listener_task.done():
        try:
            await asyncio.wait_for(_listener_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Listener task did not shut down cleanly within timeout.")
            _listener_task.cancel()
            
    _listener_task = None
    logger.info("Telegram background listener stopped entirely.")
