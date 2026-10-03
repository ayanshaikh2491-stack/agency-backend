"""24/7 background workers for Render.

Runs the always-on loops (CEO autonomy, scheduler, service delivery, SBA email
autopilot, and the two queue drainers) as independently supervised tasks.

WHY SUPERVISION INSTEAD OF asyncio.gather
------------------------------------------
The original version was a single ``asyncio.gather(...)`` over six loops. That
couples their fates: the first one to raise propagates out of gather, main()
re-raises, the process exits, and Render restarts the container. One transient
SMTP error inside the SBA email autopilot therefore took down CEO autonomy, the
scheduler, service delivery and both queue drainers at the same time -- and any
in-flight agent task was lost with them.

Each loop now runs under _supervise(), which restarts that one loop with capped
exponential backoff and leaves the other five untouched.
"""
import asyncio
import logging
import os

# NOTE: admin.config.settings already prefers TURSO_* over RENDER_POSTGRES_URL
# over a local sqlite file, so setting DATABASE_URL here is belt-and-braces for
# any module that reads os.environ lazily. It must be done BEFORE importing
# admin.config to be meaningful -- the previous version imported settings first
# and then mutated os.environ, which could never affect the already-computed
# settings.DATABASE_URL that admin.database reads.
if os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"):
    os.environ["DATABASE_URL"] = (
        f"sqlite+libsql://{os.environ['TURSO_DATABASE_URL']}"
        f"?authToken={os.environ['TURSO_AUTH_TOKEN']}"
    )

from admin.config import settings  # noqa: E402  (must follow the env block above)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# A crashed loop should not hot-loop, but should also come back quickly.
_MIN_BACKOFF = 5.0
_MAX_BACKOFF = 300.0


async def _supervise(name: str, factory, *, min_backoff: float = _MIN_BACKOFF,
                     max_backoff: float = _MAX_BACKOFF) -> None:
    """Run `factory()` forever, restarting it with capped exponential backoff.

    Never returns under normal operation -- cancellation propagates so the
    whole process can still shut down cleanly on SIGTERM.
    """
    delay = min_backoff
    while True:
        try:
            logger.info("▶ %s started", name)
            await factory()
            # A 24/7 loop should not return. If it does, it means its internal
            # condition ended (e.g. a shutdown sentinel), so restart it rather
            # than leaving a capability silently offline.
            logger.warning("%s returned unexpectedly — restarting in %.0fs", name, delay)
        except asyncio.CancelledError:
            logger.info("■ %s cancelled", name)
            raise
        except Exception:  # noqa: BLE001
            logger.exception("✖ %s crashed — restarting in %.0fs", name, delay)
        await asyncio.sleep(delay)
        delay = min(max_backoff, delay * 2)


async def main() -> None:
    """Start all 24/7 workers, each independently supervised."""
    logger.info("🚀 Starting 24/7 workers on Render...")
    logger.info("   DATABASE_URL dialect in use: %s",
                getattr(settings, "DATABASE_URL", "<unset>").split(":", 1)[0])

    # Imported here (not at module scope) so a broken optional dependency in one
    # worker's import chain cannot stop the other five from booting.
    from admin.agency.ceo_autonomy import get_autonomy
    from admin.agency.scheduler import get_scheduler
    from admin.agency import service_delivery
    from admin.agency.sba_autopilot import start_autopilot
    from admin.workspace.manager import process_agent_task_queue, process_handoff_queue

    workers = [
        ("ceo_autonomy",     lambda: get_autonomy().start()),
        ("scheduler",        lambda: get_scheduler().start()),
        ("service_delivery", lambda: service_delivery.start_loop()),
        ("sba_autopilot",    lambda: start_autopilot()),
        ("agent_task_queue", lambda: process_agent_task_queue()),
        ("handoff_queue",    lambda: process_handoff_queue()),
    ]

    # gather here is only over supervisors, which by construction never raise
    # (except CancelledError, which should stop everything).
    await asyncio.gather(
        *(_supervise(name, factory) for name, factory in workers)
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 24/7 workers stopped")