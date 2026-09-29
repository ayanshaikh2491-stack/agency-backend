"""24/7 background workers for Render - handles CEO autonomy, schedulers, and agent task processing"""
import asyncio
import os
import logging
from datetime import datetime, timezone

# Ensure we use the Turso database if configured
from admin.config import settings
if settings.TURSO_DATABASE_URL and settings.TURSO_AUTH_TOKEN:
    os.environ["DATABASE_URL"] = f"sqlite+libsql://{settings.TURSO_DATABASE_URL}?authToken={settings.TURSO_AUTH_TOKEN}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

async def main():
    """Start all 24/7 workers"""
    logger.info("🚀 Starting 24/7 workers on Render...")
    
    try:
        # Import all the workers we need to run
        from admin.agency.ceo_autonomy import get_autonomy
        from admin.agency.scheduler import get_scheduler
        from admin.agency import service_delivery
        from admin.agency.sba_autopilot import start_autopilot
        from admin.workspace.manager import process_agent_task_queue, process_handoff_queue
        
        # Run all workers in parallel
        await asyncio.gather(
            get_autonomy().start(),                    # CEO event loop
            get_scheduler().start(),                   # Autonomous CEO triggers
            service_delivery.start_loop(),             # Website/SEO/content delivery
            start_autopilot(),                         # SBA email autopilot
            process_agent_task_queue(),                # Process agent_tasks table
            process_handoff_queue(),                   # Process approved handoffs → delivery agents
        )
    except Exception as e:
        logger.exception("Fatal error in 24/7 workers: %s", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())