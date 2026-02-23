"""SQLite database layer for LifeQuery."""

import asyncio
import getpass
import logging
import os
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

import aiosqlite

# Use the lifequery namespace directly — cannot use get_logger here because
# utils/logger.py imports DATA_DIR from this module (would be a circular import).
logger = logging.getLogger("lifequery.db.database")

# Enhanced DATA_DIR selection: environment variable > local 'data' folder > docker path
DATA_DIR_STR = os.environ.get("DATA_DIR")
if DATA_DIR_STR:
    DATA_DIR = Path(DATA_DIR_STR)
else:
    # Check if we are running in a local dev environment (looking for backend folder)
    local_data = Path(__file__).parent.parent / "data"
    if local_data.exists() or Path("backend").exists():
        DATA_DIR = local_data
    else:
        DATA_DIR = Path("/app/data")

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "data.db"

# Global lock to ensure only one task writes at a time.
# This is a 'soft' lock that helps prevent contention on the NAS filesystem.
_write_lock = asyncio.Lock()


def cleanup_stale_locks():
    """Aggressively clean up stale locks and 0-byte files."""
    try:
        # If DB is 0 bytes, it's a failed init. Delete it so we can try again.
        if DB_PATH.exists() and DB_PATH.stat().st_size == 0:
            logger.warning(f"Removing 0-byte database file: {DB_PATH}")
            DB_PATH.unlink()

        # Remove lock files
        for suffix in ["-wal", "-shm", "-journal"]:
            lock_file = DB_PATH.with_suffix(DB_PATH.suffix + suffix)
            if lock_file.exists():
                try:
                    lock_file.unlink()
                    logger.info(f"Cleaned up stale lock: {lock_file}")
                except Exception as e:
                    logger.error(f"Failed to clean {lock_file}: {e}")
    except Exception as e:
        logger.error(f"Error checking for stale locks: {e}")


cleanup_stale_locks()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    chat_name TEXT,
    sender_id TEXT,
    sender_name TEXT,
    text TEXT,
    timestamp INTEGER NOT NULL,
    source TEXT NOT NULL,
    imported_at INTEGER NOT NULL,
    UNIQUE(message_id, chat_id)
);
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
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    status TEXT,
    messages_added INTEGER,
    chunks_created INTEGER,
    skipped_duplicate INTEGER,
    skipped_empty INTEGER,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id TEXT PRIMARY KEY,
    chat_name TEXT,
    chat_type TEXT,
    included INTEGER DEFAULT 1,
    message_count INTEGER DEFAULT 0,
    last_message_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    base_url TEXT,
    api_key TEXT,
    last_model TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_chunks_chat_id ON chunks(chat_id);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);
"""


async def get_connection() -> aiosqlite.Connection:
    """Get a database connection optimized for NAS storage with retries."""
    last_err = None
    for attempt in range(5):
        try:
            # 1. Open the connection with a generous timeout and nolock=1
            # nolock=1 is critical for SMB mounts that don't support byte-range locking.
            # We use .as_uri() to correctly format the path for all OSs, then append nolock.
            uri = DB_PATH.absolute().as_uri()
            connection_uri = f"{uri}?nolock=1"
            db = await aiosqlite.connect(connection_uri, timeout=30.0, uri=True)

            # Configure connection-level pragmas (NAS Optimized)
            await db.execute("PRAGMA busy_timeout=60000")
            await db.execute("PRAGMA temp_store=MEMORY")
            # journal_mode=MEMORY keeps the rollback journal in RAM, avoiding NAS file locking issues
            await db.execute("PRAGMA journal_mode=MEMORY")
            # synchronous=OFF trades durability for speed on NAS.
            # WARNING: Power loss during a write can corrupt the DB. This is an intentional tradeoff for NAS stability.
            await db.execute("PRAGMA synchronous=OFF")
            # Disable mmap as it often fails on network shares
            await db.execute("PRAGMA mmap_size=0")
            await db.execute("PRAGMA cache_size=-5000")
            await db.execute("PRAGMA foreign_keys=ON")

            return db
        except Exception as e:
            last_err = e
            error_msg = str(e).lower()

            # If the database is malformed, retrying immediately is unlikely to help
            # unless it was a transient filesystem glitch.
            if "malformed" in error_msg:
                logger.error(f"CORRUPTION DETECTED: {DB_PATH} is malformed.")
                # We don't delete it automatically here to prevent data loss,
                # but we'll flag it for the permanent failure logic below.
                break

            logger.warning(f"Connection attempt {attempt + 1} failed: {e}. Retrying...")
            await asyncio.sleep(1)

    # If all retries fail
    if "malformed" in str(last_err).lower():
        logger.critical("!!! DATABASE CORRUPTION !!!")
        logger.critical(f"The file at {DB_PATH} is corrupted and cannot be opened.")
        logger.critical(
            "This often happens on network drives if writes are interrupted."
        )

        # 1. Attempt to rename the corrupted file for recovery
        timestamp = int(time.time())
        corrupted_path = DB_PATH.with_name(f"{DB_PATH.name}.corrupted.{timestamp}")
        try:
            if DB_PATH.exists():
                os.rename(DB_PATH, corrupted_path)
                logger.critical(f"Renamed corrupted database to {corrupted_path}.")

                # 2. Also rename ChromaDB folder to keep them in sync
                chroma_path = DATA_DIR / "chroma"
                if chroma_path.exists():
                    corrupted_chroma = chroma_path.with_name(
                        f"chroma.corrupted.{timestamp}"
                    )
                    try:
                        os.rename(chroma_path, corrupted_chroma)
                        logger.critical(
                            f"Renamed old ChromaDB folder to {corrupted_chroma}."
                        )
                    except Exception as c_err:
                        logger.error(f"Failed to rename chroma folder: {c_err}")

                # 3. EMERGENCY RETRY: Now that the corrupted file is gone,
                # a fresh connection should work and create a new file.
                logger.info("Attempting to create a fresh database file...")
                try:
                    abs_path = DB_PATH.absolute()
                    connection_uri = f"file://{abs_path}?nolock=1"
                    db = await aiosqlite.connect(connection_uri, timeout=30.0, uri=True)

                    # Configure connection-level pragmas (read-safe)
                    await db.execute("PRAGMA busy_timeout=60000")
                    await db.execute("PRAGMA temp_store=MEMORY")
                    await db.execute("PRAGMA foreign_keys=ON")

                    logger.info("Fresh database file created successfully.")
                    return db
                except Exception as retry_err:
                    logger.critical(f"Failed to create fresh database: {retry_err}")
                    raise retry_err
        except Exception as rename_err:
            logger.critical(f"Failed to rename corrupted database: {rename_err}")

        raise last_err

    logger.error(
        f"PERMANENT FAILURE connecting to DB at {DB_PATH}. User: {getpass.getuser()}. Error: {last_err}"
    )
    # If connection fails, check if the parent directory is writable
    if not DATA_DIR.exists():
        logger.critical(f"Data directory {DATA_DIR} does not exist!")
    elif not os.access(str(DATA_DIR), os.W_OK):
        logger.critical(f"Data directory {DATA_DIR} is NOT writable!")
    raise last_err


async def execute_write(sql: str, params: tuple = ()) -> None:
    """Execute a write operation with a global lock and retries for NAS safety."""
    async with _write_lock:
        db = await get_connection()
        try:
            await db.execute(sql, params)
            await db.commit()
        finally:
            await db.close()


async def execute_fetchall(sql: str, params: tuple = ()) -> list:
    """Execute a query and return all results, handling connection cleanup."""
    db = await get_connection()
    try:
        cursor = await db.execute(sql, params)
        return await cursor.fetchall()
    finally:
        await db.close()


async def execute_fetchone(sql: str, params: tuple = ()) -> Optional[tuple]:
    """Execute a query and return one result, handling connection cleanup."""
    db = await get_connection()
    try:
        cursor = await db.execute(sql, params)
        return await cursor.fetchone()
    finally:
        await db.close()


async def seed_providers(db: aiosqlite.Connection) -> None:
    """Pre-populate the providers table with default records."""
    import time

    now = int(time.time())

    defaults = [
        ("ollama", "Ollama (Local)", "ollama", "http://ollama:11434", None, "qwen3:8b"),
        (
            "openai",
            "OpenAI",
            "openai",
            "https://api.openai.com/v1",
            None,
            "gpt-4o-mini",
        ),
        (
            "openrouter",
            "OpenRouter (Cloud)",
            "openrouter",
            "https://openrouter.ai/api/v1",
            None,
            "",
        ),
        (
            "minimax",
            "MiniMax Coding Plan",
            "minimax",
            "https://api.minimax.io/v1",
            None,
            "MiniMax-M2.5",
        ),
        (
            "glmai",
            "Z.AI Coding Plan",
            "glmai",
            "https://api.z.ai/api/coding/paas/v4",
            None,
            "glm-4.7",
        ),
    ]

    for pid, name, ptype, url, key, model in defaults:
        # INSERT OR IGNORE so we don't overwrite user changes if they exist
        await db.execute(
            """INSERT OR IGNORE INTO providers
               (id, name, provider_type, base_url, api_key, last_model, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (pid, name, ptype, url, key, model, now),
        )
    await db.commit()


async def init_db() -> None:
    """Initialize the database schema and run migrations.

    Creates all required tables if they don't exist, seeds default providers,
    and runs any necessary migrations for existing databases.
    """
    logger.info(f"Initializing database at {DB_PATH}")
    db = await get_connection()
    try:
        # Performance configuration for NAS
        await db.execute("PRAGMA journal_mode=MEMORY")
        await db.execute("PRAGMA synchronous=OFF")
        await db.execute("PRAGMA mmap_size=0")

        await db.executescript(SCHEMA_SQL)
        await db.commit()

        # Seed initial providers
        await seed_providers(db)

        # Migration: add deduplication counts to sync_log table for existing databases
        try:
            await db.execute(
                "ALTER TABLE sync_log ADD COLUMN skipped_duplicate INTEGER"
            )
            logger.info("Added skipped_duplicate column to sync_log table")
        except Exception as e:
            # Column likely already exists
            if "duplicate column" not in str(e).lower():
                logger.warning(f"Could not add skipped_duplicate column: {e}")

        try:
            await db.execute("ALTER TABLE sync_log ADD COLUMN skipped_empty INTEGER")
            logger.info("Added skipped_empty column to sync_log table")
        except Exception as e:
            # Column likely already exists
            if "duplicate column" not in str(e).lower():
                logger.warning(f"Could not add skipped_empty column: {e}")

        # Migration: create chats table for existing databases
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id TEXT PRIMARY KEY,
                    chat_name TEXT,
                    chat_type TEXT,
                    included INTEGER DEFAULT 1,
                    message_count INTEGER DEFAULT 0,
                    last_message_at INTEGER,
                    created_at INTEGER NOT NULL
                )
            """)
            logger.info("Created chats table")
        except Exception as e:
            logger.warning(f"Could not create chats table: {e}")

        await db.commit()
    finally:
        await db.close()
    logger.info("Database initialized successfully")


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Yield a database connection for use in FastAPI dependencies.

    This is a FastAPI dependency that automatically handles connection
    cleanup after the request completes.
    """
    db = await get_connection()
    try:
        yield db
    finally:
        await db.close()


# ============================================================================
# Query Helper Functions
# ============================================================================
# These helpers reduce boilerplate and ensure consistent query patterns.


async def count(table: str, where: str | None = None, params: tuple = ()) -> int:
    """Generic count query."""
    if where:
        query = f"SELECT COUNT(*) FROM {table} WHERE {where}"
    else:
        query = f"SELECT COUNT(*) FROM {table}"

    row = await execute_fetchone(query, params)
    return row[0] if row else 0


async def fetch_one(query: str, params: tuple = ()) -> dict | None:
    """Execute a query and fetch one row as a dictionary."""
    db = await get_connection()
    try:
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))
    finally:
        await db.close()


async def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    """Execute a query and fetch all rows as dictionaries."""
    db = await get_connection()
    try:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        if not rows:
            return []
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        await db.close()
