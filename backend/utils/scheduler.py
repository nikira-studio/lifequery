"""Background scheduler for periodic tasks."""

import asyncio

from config import settings, load_from_db
from utils.logger import get_logger

logger = get_logger(__name__)

async def auto_sync_worker():
    """Background worker for periodic Telegram sync."""
    logger.info("Auto-sync worker started")
    
    # Wait a bit after startup
    await asyncio.sleep(60)
    
    while True:
        try:
            # Refresh settings to ensure we have latest interval
            await load_from_db()
            
            interval_mins = settings.auto_sync_interval
            
            if interval_mins > 0:
                # Check Telegram status before syncing
                from telegram.telethon_sync import get_telegram_status
                status = await get_telegram_status()
                
                if status.get("state") == "connected":
                    logger.info("Auto-sync worker: Starting sync task")
                    from routers.data import sync_generator
                    async for _ in sync_generator():
                        pass
                    logger.info("Auto-sync worker: Sync task completed")
                else:
                    logger.info("Auto-sync worker: Telegram not connected, skipping sync")
                
                logger.info(f"Auto-sync worker: Next sync in {interval_mins} minutes")
                await asyncio.sleep(interval_mins * 60)
            else:
                # Auto-sync disabled, check again in a minute
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("Auto-sync worker shutting down")
            break
        except Exception as e:
            logger.error(f"Auto-sync worker error: {e}", exc_info=True)
            # Wait a bit before retrying on error
            await asyncio.sleep(300)
