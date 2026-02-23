"""Live Telegram sync via Telethon."""

import asyncio
import logging
import time
from datetime import datetime
from typing import AsyncGenerator, Optional

import aiosqlite
from config import settings
from db.database import execute_fetchone, execute_write, get_connection
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat, User
from utils.logger import get_logger

logger = get_logger(__name__)

# Global lock to prevent multiple Telethon instances from colliding.
_telethon_lock = asyncio.Lock()

# Cancellation flag — set via cancel_sync() to stop after the current chat.
_sync_cancel = asyncio.Event()


def cancel_sync() -> None:
    """Signal the running sync to stop after the current chat finishes."""
    _sync_cancel.set()


# In-memory state for auth flow and status caching
_auth_clients: dict[str, TelegramClient] = {}
_auth_tokens: dict[str, str] = {}  # token -> phone
_status_cache: dict = {"state": None, "expires": 0}
CACHE_TTL = 30  # 30 seconds for NAS sanity


# ---------------------------------------------------------------------------
# Session string helpers — stored in the config table (NAS-safe)
# ---------------------------------------------------------------------------


async def _load_session_string() -> Optional[str]:
    """Load the Telegram session string from the config table."""
    row = await execute_fetchone(
        "SELECT value FROM config WHERE key = 'telegram_session'", ()
    )
    return row[0] if row and row[0] else None


async def _save_session_string(session_string: str) -> None:
    """Persist the Telegram session string to the config table."""
    now = int(datetime.now().timestamp())
    await execute_write(
        """INSERT INTO config (key, value, updated_at) VALUES ('telegram_session', ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (session_string, now),
    )


async def _save_user_identity(client: TelegramClient) -> None:
    """Fetch and save the authenticated user's identity to config."""
    try:
        me = await client.get_me()
        now = int(datetime.now().timestamp())

        user_first_name = me.first_name or ""
        user_last_name = me.last_name or ""
        user_username = me.username or ""

        # Save each field to config
        for key, value in [
            ("user_first_name", user_first_name),
            ("user_last_name", user_last_name),
            ("user_username", user_username),
        ]:
            await execute_write(
                """INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value, now),
            )

        # Reload settings so the singleton is updated immediately
        from config import load_from_db

        await load_from_db()

        logger.info(
            f"User identity saved: {user_first_name} {user_last_name} (@{user_username})"
        )
    except Exception as e:
        logger.warning(f"Failed to save user identity: {e}")


async def _clear_session_string() -> None:
    """Remove the Telegram session string from the config table."""
    await execute_write("DELETE FROM config WHERE key = 'telegram_session'", ())


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _derive_sender_name(sender) -> str:
    """Extract sender name from a Telethon sender object."""
    if sender is None:
        return "Channel"

    if isinstance(sender, User):
        name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
        if not name and sender.username:
            name = sender.username
        if not name:
            name = str(sender.id)
        return name

    if isinstance(sender, Channel):
        return sender.title

    return str(getattr(sender, "id", "Unknown"))


def _derive_chat_type(entity) -> str:
    """Determine the chat type from a Telethon entity."""
    if isinstance(entity, User):
        return "private"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        return "channel"
    return "unknown"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def get_telegram_status(force_refresh: bool = False) -> dict:
    """Get current Telegram connection status.

    Uses a local cache to avoid slow network handshakes with Telegram on every poll.
    """
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        return {"state": "uninitialized"}

    # Check if we have a pending auth flow (always checked live)
    for token, phone in _auth_tokens.items():
        if token in _auth_clients:
            return {"state": "phone_sent", "phone": phone, "token": token}

    # No session stored yet
    session_string = await _load_session_string()
    if not session_string:
        return {"state": "needs_auth"}

    # Check cache first
    now = time.time()
    if not force_refresh and _status_cache["expires"] > now:
        # If cache is valid, return it.
        # This is critical for performance as frontend polls this every 2-5s.
        return {"state": _status_cache["state"]}

    # Try to connect with existing session string
    async with _telethon_lock:
        try:
            client = TelegramClient(
                StringSession(session_string),
                int(settings.telegram_api_id),
                settings.telegram_api_hash,
            )
            # Reduced timeout for status check — 30s is too long for a UI poll.
            await asyncio.wait_for(client.connect(), timeout=5.0)
            is_authorized = await client.is_user_authorized()
            await client.disconnect()

            if is_authorized:
                _status_cache.update({"state": "connected", "expires": now + CACHE_TTL})
                return {"state": "connected"}
            else:
                _status_cache.update({"state": "needs_auth", "expires": now + 60})
                return {"state": "needs_auth"}
        except Exception as e:
            logger.debug(f"Simple check failed (likely disconnected/expired): {e}")
            # Cache the failure state too to avoid hammer
            _status_cache.update({"state": "needs_auth", "expires": now + 30})
            return {"state": "needs_auth"}


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------


def normalize_phone(phone: str) -> str:
    """Normalize phone number: remove spaces, dashes, parentheses and ensure + prefix."""
    phone = "".join(filter(str.isdigit, phone))

    if not phone.startswith("+"):
        if len(phone) == 10:
            # Assume US (+1) if 10 digits
            phone = "+1" + phone
        else:
            phone = "+" + phone
    return phone


async def start_auth(phone: str) -> dict:
    """Start Telegram auth flow — send code request."""
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise ValueError("Telegram API credentials not configured")

    # Normalize phone number
    phone = normalize_phone(phone)

    logger.info(f"Normalized phone number: {phone}")

    import secrets

    token = secrets.token_hex(8)

    async with _telethon_lock:
        # Start with a fresh (empty) StringSession — no file needed
        client = TelegramClient(
            StringSession(),
            int(settings.telegram_api_id),
            settings.telegram_api_hash,
        )

        try:
            await asyncio.wait_for(client.connect(), timeout=30)
        except Exception as e:
            logger.error(f"Connection failed while starting auth: {e}")
            raise ValueError(f"Could not connect to Telegram: {e}")

        try:
            await client.send_code_request(phone)
            _auth_clients[token] = client
            _auth_tokens[token] = phone
            return {"state": "phone_sent", "token": token}
        except Exception as e:
            await client.disconnect()
            logger.error(f"Error sending code request: {e}")
            raise ValueError(f"Failed to send code: {e}")


async def verify_auth(
    token: str, code: Optional[str] = None, password: Optional[str] = None
) -> dict:
    """Verify the auth code or password and complete login."""
    if token not in _auth_clients:
        raise ValueError("Invalid or expired auth session")

    client = _auth_clients[token]
    phone = _auth_tokens.get(token, "Unknown")

    async with _telethon_lock:
        try:
            if password:
                logger.info(f"verify_auth: checking 2FA password for {phone}")
                await client.sign_in(password=password)
            elif code:
                # Strip any spaces from the code (users might type them due to UI spacing)
                code = code.strip().replace(" ", "")
                logger.info(f"verify_auth: checking code for {phone}")
                await client.sign_in(phone, code)
            else:
                raise ValueError("Either code or password must be provided")

            if await client.is_user_authorized():
                # Success! Persist session string and user identity
                session_string = client.session.save()
                await _save_user_identity(client)
                await client.disconnect()
                await _save_session_string(session_string)

                _auth_clients.pop(token, None)
                _auth_tokens.pop(token, None)

                # Clear status cache
                _status_cache["expires"] = 0

                return {"state": "connected"}

            return {"state": "needs_auth", "error": "Authorization failed"}

        except errors.SessionPasswordNeededError:
            logger.info(f"verify_auth: password needed for {phone}")
            # Do NOT disconnect, we need the client for the password step
            return {
                "state": "phone_sent",
                "error": "Two-step verification required",
                "token": token,
            }
        except errors.PhoneCodeInvalidError:
            return {
                "state": "phone_sent",
                "error": "Invalid code. Try again.",
                "token": token,
            }
        except Exception as e:
            logger.error(f"Error in verification: {e}")
            raise ValueError(f"Verification failed: {e}")


async def verify_password(token: str, password: str) -> dict:
    """Complete auth with two-step verification password."""
    if token not in _auth_clients:
        raise ValueError("Invalid or expired auth token")

    client = _auth_clients[token]

    async with _telethon_lock:
        try:
            await client.sign_in(password=password)

            session_string = client.session.save()
            await _save_user_identity(client)
            await client.disconnect()
            await _save_session_string(session_string)

            _auth_clients.pop(token, None)
            _auth_tokens.pop(token, None)

            return {"state": "connected"}
        except Exception as e:
            logger.error(f"Error verifying password: {e}")
            raise ValueError(f"Password verification failed: {e}")


async def disconnect_telegram() -> dict:
    """Disconnect and clear Telegram session."""
    async with _telethon_lock:
        try:
            await _clear_session_string()

            # Clear user identity
            now = int(datetime.now().timestamp())
            for key in ["user_first_name", "user_last_name", "user_username"]:
                await execute_write(
                    """INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                    (key, "", now),
                )

            # Reload settings to update singleton
            from config import load_from_db

            await load_from_db()

            _status_cache["state"] = None
            _status_cache["expires"] = 0
            return {"state": "needs_auth"}
        except Exception as e:
            logger.error(f"Error disconnecting: {e}")
            raise ValueError(f"Disconnect failed: {e}")


# ---------------------------------------------------------------------------
# Background chat discovery (runs after auth, no SSE overhead)
# ---------------------------------------------------------------------------


async def auto_sync_chats() -> None:
    """Discover Telegram dialogs and populate the chats table silently.

    Called automatically after successful authentication so the Data tab
    shows chats immediately without requiring a manual sync.
    """
    session_string = await _load_session_string()
    if not session_string:
        return

    async with _telethon_lock:
        client = TelegramClient(
            StringSession(session_string),
            int(settings.telegram_api_id),
            settings.telegram_api_hash,
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=30)
            if not await client.is_user_authorized():
                logger.warning("auto_sync_chats: session not authorized")
                return

            dialogs = await client.get_dialogs()
            logger.info(f"auto_sync_chats: discovered {len(dialogs)} dialogs")

            for dialog in dialogs:
                chat_id = str(dialog.entity.id)
                chat_name = getattr(dialog.entity, "title", None) or getattr(
                    dialog.entity, "first_name", "Unknown"
                )
                try:
                    await ensure_chat_entry(
                        chat_id=chat_id,
                        chat_name=chat_name,
                        chat_type=_derive_chat_type(dialog.entity),
                    )
                except Exception as e:
                    logger.warning(f"auto_sync_chats: failed to add {chat_name}: {e}")

            await client.disconnect()
            logger.info("auto_sync_chats: complete")
        except Exception as e:
            logger.error(f"auto_sync_chats failed: {e}")
            try:
                await client.disconnect()
            except Exception as disconnect_err:
                logger.debug(f"auto_sync_chats: cleanup disconnect failed: {disconnect_err}")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


async def sync_telegram_messages(
    progress_callback: Optional[AsyncGenerator[dict, None]] = None,
) -> AsyncGenerator[dict, None]:
    """Sync all Telegram messages from all dialogs."""
    session_string = await _load_session_string()
    if not session_string:
        raise ValueError("No Telegram session. Please authenticate first.")

    async with _telethon_lock:
        client = TelegramClient(
            StringSession(session_string),
            int(settings.telegram_api_id),
            settings.telegram_api_hash,
        )

        try:
            logger.info("Sync started: connecting to Telegram...")
            await asyncio.wait_for(client.connect(), timeout=120)
            if not await client.is_user_authorized():
                raise ValueError("Telegram session not authorized.")

            logger.info("Sync: connected, fetching dialog list...")
            yield {
                "type": "progress",
                "stage": "ingest",
                "message": "Fetching dialogs...",
            }

            dialogs = await client.get_dialogs()
            logger.info(f"Sync: found {len(dialogs)} dialogs")

            # Fetch excluded chat IDs to skip them
            from db.database import execute_fetchall
            excluded_rows = await execute_fetchall("SELECT chat_id FROM chats WHERE included = 0")
            excluded_ids = {row[0] for row in excluded_rows}

            # Reset cancellation flag at the start of each run
            _sync_cancel.clear()

            total_messages = 0
            skipped_duplicate = 0
            skipped_empty = 0
            total_chats = 0
            cancelled = False

            for dialog in dialogs:
                if _sync_cancel.is_set():
                    logger.info("Sync cancelled by user after current chat")
                    cancelled = True
                    break
                
                chat_id = str(dialog.entity.id)
                if chat_id in excluded_ids:
                    logger.debug(f"Sync: skipping excluded chat {chat_id}")
                    continue
                chat_name = getattr(dialog.entity, "title", None) or getattr(
                    dialog.entity, "first_name", "Unknown"
                )

                last_msg_id = await _get_last_message_id(chat_id)

                yield {
                    "type": "progress",
                    "stage": "ingest",
                    "message": f"Syncing {chat_name}...",
                }

                chat_messages = 0
                last_timestamp = 0

                try:
                    from db.database import _write_lock

                    # Fetch from Telegram first (network I/O), outside the DB lock
                    fetched = []
                    async for message in client.iter_messages(
                        dialog.entity,
                        offset_id=last_msg_id or 0,
                        limit=settings.telegram_fetch_batch,
                        reverse=True,
                    ):
                        if not message.text:
                            skipped_empty += 1
                            continue
                        fetched.append(message)

                    # Now write to DB in a short transaction (no network I/O inside lock)
                    async with _write_lock:
                        db = await get_connection()
                        try:
                            await db.execute("BEGIN")
                            for message in fetched:
                                sender_name = _derive_sender_name(message.sender)
                                sender_id = (
                                    str(getattr(message.sender, "id", ""))
                                    if message.sender
                                    else ""
                                )

                                inserted = await _insert_message(
                                    message_id=str(message.id),
                                    chat_id=chat_id,
                                    chat_name=chat_name,  # type: ignore[arg-type]
                                    sender_id=sender_id,
                                    sender_name=sender_name,
                                    text=message.text,
                                    timestamp=int(message.date.timestamp()),
                                    db=db,
                                )

                                if inserted:
                                    total_messages += 1
                                    chat_messages += 1
                                    last_timestamp = int(message.date.timestamp())
                                else:
                                    skipped_duplicate += 1
                            await db.commit()
                        except Exception as e:
                            await db.rollback()
                            logger.error(f"Error batch inserting for {chat_name}: {e}")
                        finally:
                            await db.close()

                    if chat_messages > 0:
                        logger.info(f"Sync: {chat_name} — {chat_messages} new messages")
                    if chat_messages > 0 or last_msg_id is None:
                        total_chats += 1
                        await _update_chat_entry(
                            chat_id=chat_id,
                            chat_name=chat_name,  # type: ignore[arg-type]
                            chat_type=_derive_chat_type(dialog.entity),
                            message_count=await _get_chat_message_count(chat_id),
                            last_message_at=last_timestamp
                            if last_timestamp > 0
                            else int(datetime.now().timestamp()),
                        )

                except errors.FloodWaitError as e:
                    yield {
                        "type": "progress",
                        "stage": "ingest",
                        "message": f"Rate limited — waiting {e.seconds}s",
                    }
                    await asyncio.sleep(e.seconds)
                    continue

                yield {
                    "type": "progress",
                    "stage": "ingest",
                    "message": f"Added {chat_messages} messages from {chat_name}",
                }

            await client.disconnect()

            # Persist any session state updates (e.g. server salt) Telethon made during sync
            updated_string = client.session.save()
            if updated_string:
                await _save_session_string(updated_string)

            status_word = "cancelled" if cancelled else "complete"
            logger.info(
                f"Sync {status_word}: {total_messages} inserted, "
                f"{skipped_duplicate} duplicates skipped, {skipped_empty} empty skipped, "
                f"{total_chats} chats updated"
            )
            yield {
                "type": "done",
                "cancelled": cancelled,
                "inserted": total_messages,
                "skipped_duplicate": skipped_duplicate,
                "skipped_empty": skipped_empty,
                "chats_synced": total_chats,
            }

        except Exception as e:
            logger.error(f"Sync error: {e}")
            yield {"type": "error", "message": str(e)}
            try:
                await client.disconnect()
            except Exception as disconnect_err:
                logger.debug(f"sync_telegram_messages: cleanup disconnect failed: {disconnect_err}")
            raise


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


async def _update_chat_entry(
    chat_id: str,
    chat_name: str,
    chat_type: str,
    message_count: int,
    last_message_at: int,
) -> None:
    """Update or create a chat entry in the chats table."""
    await ensure_chat_entry(
        chat_id=chat_id,
        chat_name=chat_name,
        chat_type=chat_type,
        message_count=message_count,
        last_message_at=last_message_at,
    )


async def ensure_chat_entry(
    chat_id: str,
    chat_name: str,
    chat_type: str,
    message_count: int = 0,
    last_message_at: int = 0,
) -> None:
    """Create or update a chat entry in the chats table using central helpers."""
    from db.database import execute_fetchone, execute_write

    existing = await execute_fetchone(
        "SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,)
    )

    if existing:
        await execute_write(
            """
            UPDATE chats SET
                chat_name = ?,
                chat_type = ?,
                message_count = ?,
                last_message_at = ?
            WHERE chat_id = ?
            """,
            (chat_name, chat_type, message_count, last_message_at, chat_id),
        )
    else:
        created_at = int(datetime.now().timestamp())
        await execute_write(
            """
            INSERT INTO chats
            (chat_id, chat_name, chat_type, included, message_count, last_message_at, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            """,
            (chat_id, chat_name, chat_type, message_count, last_message_at, created_at),
        )


async def _get_chat_message_count(chat_id: str) -> int:
    """Get the total message count for a chat."""
    from db.database import execute_fetchone

    row = await execute_fetchone(
        "SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,)
    )
    return row[0] if row else 0


async def _get_last_message_id(chat_id: str) -> Optional[int]:
    """Get the last synced message ID for a chat."""
    from db.database import execute_fetchone

    row = await execute_fetchone(
        "SELECT MAX(CAST(message_id AS INTEGER)) FROM messages WHERE chat_id = ?",
        (chat_id,),
    )
    return row[0] if row and row[0] else None


async def _insert_message(
    message_id: str,
    chat_id: str,
    chat_name: str,
    sender_id: str,
    sender_name: str,
    text: str,
    timestamp: int,
    source: str = "telegram",
    db: Optional[aiosqlite.Connection] = None,
) -> bool:
    """Insert a message into the database. If db is provided, uses it directly."""
    from db.database import execute_fetchone, execute_write

    if not text or not text.strip():
        return False

    imported_at = int(datetime.now().timestamp())

    sql = """INSERT OR IGNORE INTO messages
             (message_id, chat_id, chat_name, sender_id, sender_name, text, timestamp, source, imported_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    params = (
        message_id,
        chat_id,
        chat_name,
        sender_id,
        sender_name,
        text,
        timestamp,
        source,
        imported_at,
    )

    if db:
        cursor = await db.execute(
            "SELECT 1 FROM messages WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id),
        )
        if await cursor.fetchone():
            return False
        await db.execute(sql, params)
        return True
    else:
        already_exists = await execute_fetchone(
            "SELECT 1 FROM messages WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id),
        )
        if already_exists:
            return False
        await execute_write(sql, params)
        return True
