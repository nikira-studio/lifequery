"""LifeQuery Backend - Main FastAPI Application."""

import asyncio
import time
from contextlib import asynccontextmanager

from config import load_from_db
from db.database import init_db
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from routers import chat, data, openai_compatible, settings, telegram_auth
from utils.exceptions import LifeQueryError
from utils.scheduler import auto_sync_worker

# Configure structured logging
from utils.logger import get_logger, setup_logging

# Setup logging once at module level
setup_logging()

# Get properly scoped logger for this module
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup: initialize database and load config
    logger.info("Starting LifeQuery backend...")

    try:
        # Initialize database (create tables if they don't exist)
        await init_db()

        # Load settings from database
        await load_from_db()
        
        # Log startup verification stats
        try:
            from db.database import count
            num_chats = await count("chats")
            num_messages = await count("messages")
            
            # Run ChromaDB count in a thread to prevent blocking
            from vector_store.chroma import _get_collection
            num_chunks = await asyncio.to_thread(_get_collection().count)
            
            logger.info(f"Database Connected | Chats: {num_chats} | Messages: {num_messages} | ChromaDB Chunks: {num_chunks}")
        except Exception as e:
            logger.warning(f"Could not verify initial database stats: {e}")
            
        # Start background scheduler
        app.state.auto_sync_task = asyncio.create_task(auto_sync_worker())
        logger.info("LifeQuery backend started successfully")
    except Exception as e:
        logger.error(f"CRITICAL: Backend failed to initialize database: {e}")
        # We allow the app to continue so the frontend can receive a 503/Error
        # and display it to the user instead of a connection timeout.

    yield

    # Shutdown cleanup
    logger.info("Shutting down LifeQuery backend...")
    if hasattr(app.state, "auto_sync_task"):
        app.state.auto_sync_task.cancel()
        try:
            await app.state.auto_sync_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="LifeQuery API", lifespan=lifespan)

# Allow all origins — LifeQuery is self-hosted and access is controlled at
# the network/API key level, not by origin restrictions.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
from routers import models

app.include_router(settings.router)
app.include_router(telegram_auth.router)
app.include_router(data.router)
app.include_router(chat.router)
app.include_router(openai_compatible.router)
app.include_router(models.router)


# Exception handlers
@app.exception_handler(LifeQueryError)
async def lifequery_error_handler(request: Request, exc: LifeQueryError):
    """Handle LifeQuery custom exceptions."""
    logger.warning(f"LifeQuery error: {exc.message}")
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    logger.error(f"Unexpected error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


@app.get("/api/health")
async def health_check():
    """Health check endpoint - returns status and db connectivity."""
    # Cache the result for 30 seconds to avoid hammering the NAS
    now = time.time()
    if hasattr(app.state, "health_cache") and app.state.health_cache_time > now - 30:
        return app.state.health_cache

    db_ok = False
    try:
        from db.database import execute_fetchone
        await execute_fetchone("SELECT 1")
        db_ok = True
    except Exception:
        pass

    result = {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "error/locked",
        "version": "1.0.0"
    }
    
    app.state.health_cache = result
    app.state.health_cache_time = now
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
