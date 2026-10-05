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

WHY _supervise TAKES A TASK GETTER
----------------------------------
The first attempt at this file passed ``lambda: get_autonomy().start()`` to
_supervise. That was wrong and did not supervise anything. Three of the six
start() functions do not block -- they spawn an internal task and return:

    ceo_autonomy.py:593      self._task  = asyncio.create_task(self._loop()); return
    scheduler.py:466         self._task  = asyncio.create_task(self._loop()); return
    service_delivery.py:674  _loop_task  = asyncio.create_task(_loop());     return

So _supervise saw the factory "return unexpectedly", warned, slept, called
start() again, got an immediate return again, and hot-looped on backoff while
the real _loop task ran completely unsupervised, with nothing that would ever
restart it. _supervise is now given a getter for the spawned task and awaits
that, so a crash in the actual loop is what triggers a restart.

WHY _boot_databases RUNS FIRST
------------------------------
This was the reason CEO autonomy silently did nothing on Render. admin/main.py
(the FastAPI web service) ran set_persistent_mode + init_persistence + init_db
+ the two load_*_from_db calls in its lifespan. The worker ran none of them, so
the ceo_autonomy_* tables did not exist. Every autonomy query then raised, and
each raise was swallowed at logger.debug underneath a logger configured at
INFO -- so the loop reported {'status': 'ok', 'claimed': 0} forever while doing
nothing, and no endpoint could tell an operator that.

WHY THE BOOT BURST IS STAGGERED
-------------------------------
Every loop used to be handed to asyncio.gather with no offset, so on each
deploy all six started inside the same event-loop iteration. The CEO autonomy
tick, the CEO scheduler and the two queue drainers then all reached the LLM
gateway together, and the production log showed three or four agents hitting
HTTP 429 inside a single second. Staggering the starts by a small
deterministic per-worker offset spreads that burst across the first few
seconds. The offset is derived from a hash of the worker name, not from
random(), so a restart reproduces the same schedule and the behaviour is
testable. Set AGENCY_BOOT_STAGGER_SEC=0 to start them together.
"""
import asyncio
import hashlib
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

# Seconds of spread across the boot burst. Each worker waits
# BOOT_STAGGER_SEC * (index + jitter) before its first start(), so six loops
# spread over roughly 0 .. 6 * BOOT_STAGGER_SEC seconds instead of landing in
# the same instant. 0 disables the stagger entirely.
BOOT_STAGGER_SEC = max(0.0, float(os.getenv("AGENCY_BOOT_STAGGER_SEC", "2.5")))


def boot_offset(name: str, index: int) -> float:
    """Deterministic per-worker start offset, in seconds.

    The jitter comes from a hash of the worker name rather than random(), so
    the same container always staggers the same way. That makes the boot order
    reproducible in a test and keeps a restart from reshuffling which loop
    happens to go first.
    """
    if BOOT_STAGGER_SEC <= 0:
        return 0.0
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    jitter = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return round(BOOT_STAGGER_SEC * (index + jitter), 3)

# Tables that must exist before the 24/7 loops are allowed to start.
_REQUIRED_TABLES = (
    "ceo_autonomy_events",
    "ceo_autonomy_state",
    "ceo_autonomy_tasks",
    "ceo_autonomy_approvals",
    "agent_tasks",
)


async def _assert_tables(required=_REQUIRED_TABLES) -> None:
    """Fail the boot if any table the loops depend on is missing.

    Without this the loops start, every query raises, and the failures are
    invisible. Failing at boot is the only honest outcome.
    """
    from admin.persistence import get_workspace_db

    db = await get_workspace_db()
    placeholders = ",".join("?" * len(required))
    cursor = await db.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        list(required),
    )
    found = {row[0] for row in await cursor.fetchall()}
    missing = [t for t in required if t not in found]
    if missing:
        raise RuntimeError(
            "Refusing to start the 24/7 workers: missing table(s) "
            f"{missing}. The autonomy loops would silently no-op. "
            f"Found: {sorted(found) or '<none>'}."
        )
    logger.info("boot table check OK: %s", sorted(found))


async def _boot_databases() -> None:
    """Create and populate every table the 24/7 loops read and write.

    Mirrors the web service's lifespan (admin/main.py). Must run before any
    loop starts.
    """
    from admin.database import init_db
    from admin.persistence import init_persistence, set_persistent_mode
    from admin.agency.sba_store import load_all_from_db as load_sba_from_db
    from admin.workspace.manager import load_all_from_db as load_workspaces_from_db

    set_persistent_mode(True)
    await init_persistence()
    await init_db()
    await load_sba_from_db()
    await load_workspaces_from_db()
    await _assert_tables()


async def _supervise(name: str, start, get_task=None, *,
                     start_delay: float = 0.0,
                     min_backoff: float = _MIN_BACKOFF,
                     max_backoff: float = _MAX_BACKOFF) -> None:
    """Run a 24/7 loop forever, restarting it with capped exponential backoff.

    `start` either blocks for the life of the loop, or -- when `get_task` is
    given -- spawns the loop's task internally and returns. We await the real
    task, never the spawn call, so a crash in the loop itself triggers a
    restart rather than a spawn that returns instantly.

    `start_delay` is applied once, before the first start(), so the loops do
    not all wake the LLM gateway in the same event-loop iteration. Restarts are
    not delayed: they already wait out `min_backoff`.
    """
    if start_delay > 0:
        logger.info("⏱ %s starts in %.1fs (boot stagger)", name, start_delay)
        await asyncio.sleep(start_delay)
    delay = min_backoff
    while True:
        try:
            logger.info("▶ %s started", name)
            await start()
            if get_task is not None:
                # start() spawned internally and returned; await that task.
                task = get_task()
                if task is None:
                    # It refused to start (e.g. disabled via env). Hold the
                    # supervisor instead of hot-looping on the restart path.
                    logger.warning(
                        "%s spawned no task; holding supervisor (see earlier log)",
                        name,
                    )
                    await asyncio.Event().wait()
                await task
            # A 24/7 loop should not return. If it does, its internal condition
            # ended, so restart rather than leave a capability silently offline.
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

    # The LLM guards were only ever installed by the web service lifespan in
    # admin/main.py. This worker process is where the CEO autonomy loop, the
    # scheduler and the queue drainers actually call the gateway, so without
    # this the RPM cap, the circuit breaker and usage accounting were all absent
    # from the process doing most of the calling.
    try:
        from admin.llm_throttle import install as install_llm_throttle

        install_llm_throttle()
    except Exception as exc:
        logger.warning("LLM guards not installed in the worker: %s", exc)

    # Tables first. If this raises, the process exits and Render restarts it --
    # which is the intended, visible failure.
    await _boot_databases()

    # Imported here (not at module scope) so a broken optional dependency in one
    # worker's import chain cannot stop the other five from booting.
    from admin.agency.ceo_autonomy import get_autonomy
    from admin.agency.scheduler import get_scheduler
    from admin.agency import service_delivery
    from admin.agency.sba_autopilot import start_autopilot
    from admin.workspace.manager import process_agent_task_queue, process_handoff_queue

    autonomy = get_autonomy()
    scheduler = get_scheduler()

    workers = [
        # start() spawns and returns -> supervise the spawned task.
        ("ceo_autonomy",     autonomy.start,    lambda: autonomy._task),
        ("scheduler",        scheduler.start,   lambda: scheduler._task),
        ("service_delivery", service_delivery.start_loop,
         lambda: service_delivery._loop_task),
        # These two loop forever inside the call itself -> plain supervision.
        ("sba_autopilot",    start_autopilot,   None),
        ("agent_task_queue", process_agent_task_queue, None),
        ("handoff_queue",    process_handoff_queue,    None),
    ]

    # gather here is only over supervisors, which by construction never raise
    # (except CancelledError, which should stop everything). Each supervisor
    # waits its own deterministic offset first, so the boot burst is spread out
    # instead of six loops reaching the LLM gateway in the same instant.
    await asyncio.gather(
        *(_supervise(name, start, get_task, start_delay=boot_offset(name, i))
          for i, (name, start, get_task) in enumerate(workers))
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 24/7 workers stopped")
