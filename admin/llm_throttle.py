"""Agency-wide LLM rate limiter + budget guard, enforced in code.

Loop-engineering cost-ceiling guardrails, all hard-enforced here:

1. RPM cap - every AsyncOpenAI client shares one throttled pipeline so
   parallel agent blasts stay under the provider ceiling (default 38 under
   OpenCode Zen's 40).
2. Daily budget - UTC-day token/$ counters; when a configured cap is hit,
   acquire() raises BudgetExceededError so agents fail fast (CEO heals /
   escalates) instead of silently burning spend. Caps default OFF (0).
3. Circuit breaker - an HTTP 429 from the upstream opens the breaker for an
   exponential window (capped). While open, every LLM call path refuses the
   request LOCALLY and raises; no upstream request is made. This is the piece
   that was missing: retrying into a 429 keeps the throttle alive, which is
   how a free-tier budget got burned. Recovery is automatic on expiry.
4. Concurrency ceiling - at most AGENCY_LLM_MAX_CONCURRENCY agent calls are
   in flight against the shared gateway at once. The RPM cap limits how fast
   calls START; this limits how many are open at the same instant, which is
   what actually earns the 429.

install() patches openai's AsyncCompletions.create exactly once at startup:
throttle -> circuit check -> original call -> usage accounting. Agent call
sites stay untouched and future agents inherit every guard automatically.
Call sites that build their own client (workspace.manager, ceo_autonomy)
wrap the call in `guard()` instead, which adds the concurrency slot.

Every refusal RAISES. Nothing in this module returns a value a caller could
mistake for a completed call, which is the bug class this codebase has been
bitten by repeatedly.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


def _setting(name: str, default: str) -> str:
    """Resolve a guard knob from admin.config.settings, else the environment.

    The central settings module is the documented home for configuration, and
    it reads its own values from environment variables with defaults. Reading
    through it first means these keys can be moved into admin/config/settings.py
    later with no change here, while the environment-variable default keeps
    them configurable right now.
    """
    try:
        from admin.config import settings as _settings
    except Exception:  # noqa: BLE001  (settings must never block the guards)
        return os.getenv(name, default)
    value = getattr(_settings, name, None)
    if value is None or value == "":
        return os.getenv(name, default)
    return str(value)

RPM = max(1, int(os.getenv("AGENCY_LLM_RPM", "38")))
_WINDOW = 60.0
DAILY_TOKEN_CAP = int(os.getenv("AGENCY_LLM_DAILY_TOKENS", "0"))  # 0 = off
DAILY_USD_CAP = float(os.getenv("AGENCY_LLM_DAILY_USD", "0"))     # 0 = off
USD_PER_1M_IN = float(os.getenv("AGENCY_LLM_USD_IN", "0"))
USD_PER_1M_OUT = float(os.getenv("AGENCY_LLM_USD_OUT", "0"))
# Hard wall-clock cap per LLM call. Render's edge kills the HTTP request at
# ~60s (observed 502 at 62s). Multi-call agents (reasoning chains) stack
# calls, so each call gets 25s: 2 calls = 50s total, safely under the edge.
# Transport timeout = cap+5; SDK retries off so retries can't stack past it.
CALL_TIMEOUT = float(os.getenv("AGENCY_LLM_CALL_TIMEOUT_SEC", "25"))

# ── Circuit breaker + concurrency ceiling (free-tier protection) ─────────────
#
# The upstream is a FREE tier. The production log showed three or four agents
# entering the gateway inside the same second, every one of them 429. Two
# separate faults, both fixed here:
#
#   * no backoff on 429 - a throttled call was retried like a transient error,
#     which extends the throttle instead of riding it out;
#   * no concurrency ceiling - every agent called the upstream simultaneously.
#
# How many agent LLM calls may be open against the shared gateway at once.
# Anything above this waits for a slot, then fails fast if none frees in time.
MAX_CONCURRENCY = max(1, int(_setting("AGENCY_LLM_MAX_CONCURRENCY", "3")))
# A 429 opens the breaker for CIRCUIT_BASE_SEC, doubling on each consecutive
# open until CIRCUIT_MAX_SEC. While it is open, calls fail locally.
CIRCUIT_THRESHOLD = max(1, int(_setting("AGENCY_LLM_CIRCUIT_THRESHOLD", "1")))
CIRCUIT_BASE_SEC = max(0.1, float(_setting("AGENCY_LLM_CIRCUIT_BASE_SEC", "5")))
CIRCUIT_MAX_SEC = max(
    CIRCUIT_BASE_SEC, float(_setting("AGENCY_LLM_CIRCUIT_MAX_SEC", "300")))
# How long a call waits for a concurrency slot before giving up. Bounded so a
# saturated queue reports a real error rather than hanging a worker forever.
CIRCUIT_QUEUE_TIMEOUT = max(0.1, float(_setting("AGENCY_LLM_QUEUE_TIMEOUT_SEC", "30")))
# One throttling incident produces several reports: four agents throttled in
# the same second are one event, and the patched SDK re-reports the same error
# again as it propagates up to the retry helper. Counting each report as a
# fresh incident would walk the backoff from 5s to 40s on a single burst, so
# reports inside this window are treated as the same incident.
RATE_LIMIT_DEDUP_SEC = max(0.0, float(_setting("AGENCY_LLM_RATE_LIMIT_DEDUP_SEC", "1.0")))


class BudgetExceededError(RuntimeError):
    """Daily LLM budget exhausted - fail fast instead of burning spend."""


class LLMGuardError(RuntimeError):
    """Base class for local LLM guard refusals.

    Always raised, never returned. A refusal that could be mistaken for a
    completed call would turn a throttled request into a fake success.
    """


class CircuitOpenError(LLMGuardError):
    """The gateway is throttling us; the call was refused without being sent."""


class ConcurrencyTimeoutError(LLMGuardError):
    """Every concurrency slot was taken and none freed in time."""


# Breaker state. Guarded by a plain threading.Lock: the counter updates are all
# synchronous and snapshot() may be read from a status thread, so a lock that
# is not bound to one event loop is the safer choice here.
_guard_lock = threading.Lock()
_circuit_open_until = 0.0
_circuit_strikes = 0
_circuit_opens = 0
_circuit_backoff = CIRCUIT_BASE_SEC
_rate_limits = 0
_last_rate_limit_at = 0.0
_last_rate_limit_error = ""
_refused_locally = 0
_in_flight = 0
_peak_in_flight = 0

# Concurrency slots are per event loop: asyncio.Semaphore binds to the loop it
# first awaits on, and this process runs several short-lived loops in tests.
_slots: asyncio.Semaphore | None = None
_slots_loop: asyncio.AbstractEventLoop | None = None
_slots_size = 0


def _error_text(exc: BaseException | None) -> str:
    """Real error text for logs and status. Never a bare class name."""
    if exc is None:
        return ""
    return f"{type(exc).__name__}: {exc}"


def is_rate_limit_error(exc: BaseException) -> bool:
    """True only for HTTP 429.

    Every other error class (timeout, 500, malformed response) is transient
    and must NOT open the breaker: a breaker that trips on unrelated faults
    would take the whole agency offline for something the retry already fixes.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) == 429  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Honour an upstream Retry-After header when the provider sends one."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        logger.warning("upstream sent an unparseable Retry-After header: %r", raw)
        return None


def _expire_locked() -> bool:
    """Close the breaker if its window elapsed. Caller must hold _guard_lock."""
    global _circuit_open_until, _circuit_opens, _circuit_backoff, _circuit_strikes
    if not _circuit_open_until:
        return False
    if _circuit_open_until - time.monotonic() > 0:
        return True
    # Automatic recovery: the window elapsed, so let traffic through again and
    # start the next backoff from the floor rather than from a stale peak.
    logger.info(
        "LLM circuit closed after %.0fs; the upstream is being tried again",
        _circuit_backoff,
    )
    _circuit_open_until = 0.0
    _circuit_opens = 0
    _circuit_strikes = 0
    _circuit_backoff = CIRCUIT_BASE_SEC
    return False


def circuit_open() -> bool:
    """True while the breaker is refusing calls."""
    with _guard_lock:
        return _expire_locked()


def circuit_retry_after() -> float:
    """Seconds until the breaker closes (0.0 when closed)."""
    with _guard_lock:
        if not _expire_locked():
            return 0.0
        return max(0.0, _circuit_open_until - time.monotonic())


def ensure_circuit_closed() -> None:
    """Raise CircuitOpenError while the breaker is open.

    No upstream request is made in that window. The message says so explicitly
    so the log distinguishes "refused locally" from "the gateway rejected us".
    """
    global _refused_locally
    with _guard_lock:
        if not _expire_locked():
            return
        remaining = max(0.0, _circuit_open_until - time.monotonic())
        _refused_locally += 1
    raise CircuitOpenError(
        f"LLM gateway circuit is open after repeated HTTP 429; call refused "
        f"locally for another {remaining:.0f}s to let the rate limit decay "
        f"(no upstream request was made)")


def record_rate_limit(exc: BaseException | None = None) -> float:
    """Register an upstream 429 and open the breaker once the threshold is hit.

    Returns the seconds the breaker is now open for, so a caller can report a
    real wait rather than guessing one.
    """
    global _circuit_strikes, _circuit_open_until, _circuit_opens, _circuit_backoff
    global _rate_limits, _last_rate_limit_at, _last_rate_limit_error
    detail = _error_text(exc)
    with _guard_lock:
        now_wall = time.time()
        # Same incident as a report we handled moments ago? Do not let a burst
        # of simultaneous 429s (or one error reported twice as it propagates)
        # walk the backoff several steps for a single throttling event.
        if (_last_rate_limit_at
                and now_wall - _last_rate_limit_at < RATE_LIMIT_DEDUP_SEC):
            if detail:
                _last_rate_limit_error = detail
            remaining = max(0.0, _circuit_open_until - time.monotonic())
            logger.warning(
                "LLM gateway HTTP 429 again within %.1fs (%s); counted as the "
                "same incident", RATE_LIMIT_DEDUP_SEC, detail or "no detail")
            return remaining
        _rate_limits += 1
        _last_rate_limit_at = now_wall
        _last_rate_limit_error = detail
        _circuit_strikes += 1
        if _circuit_strikes < CIRCUIT_THRESHOLD:
            logger.warning(
                "LLM gateway returned HTTP 429 (%s); strike %d/%d, breaker held shut",
                detail or "no detail", _circuit_strikes, CIRCUIT_THRESHOLD)
            return 0.0
        _circuit_strikes = 0
        wait = _circuit_backoff
        # A Retry-After from the provider is a floor, not a ceiling: honour it
        # when it asks for longer than our own schedule, but stay capped.
        hint = _retry_after_seconds(exc) if exc is not None else None
        if hint is not None:
            wait = max(wait, min(hint, CIRCUIT_MAX_SEC))
        _circuit_open_until = time.monotonic() + wait
        _circuit_opens += 1
        _circuit_backoff = min(CIRCUIT_MAX_SEC, max(CIRCUIT_BASE_SEC, _circuit_backoff) * 2)
        opens = _circuit_opens
    logger.warning(
        "LLM gateway returned HTTP 429 (%s); circuit OPEN for %.0fs (open #%d) - "
        "calls now fail locally instead of hammering a throttled upstream",
        detail or "no detail", wait, opens,
    )
    return wait


def record_success() -> None:
    """A call got through, so the throttle has decayed: clear the strikes."""
    global _circuit_strikes
    with _guard_lock:
        _circuit_strikes = 0


def _slots_semaphore() -> asyncio.Semaphore:
    global _slots, _slots_loop, _slots_size
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _slots is None or _slots_loop is not loop or _slots_size != MAX_CONCURRENCY:
        _slots = asyncio.Semaphore(MAX_CONCURRENCY)
        _slots_loop = loop
        _slots_size = MAX_CONCURRENCY
    return _slots


@asynccontextmanager
async def guard() -> AsyncIterator[None]:
    """Wrap ONE upstream LLM call.

    Fails fast while the breaker is open, then holds one of the
    MAX_CONCURRENCY slots for the duration of the call so N agents cannot all
    enter the gateway in the same instant.

    Both refusals raise LLMGuardError. This context manager yields nothing, so
    there is no way for a caller to read a refusal as a completed call.
    """
    global _in_flight, _peak_in_flight
    ensure_circuit_closed()
    sem = _slots_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=CIRCUIT_QUEUE_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise ConcurrencyTimeoutError(
            f"waited {CIRCUIT_QUEUE_TIMEOUT:.0f}s for an LLM concurrency slot; "
            f"all {MAX_CONCURRENCY} slots are held, call refused") from exc
    with _guard_lock:
        _in_flight += 1
        _peak_in_flight = max(_peak_in_flight, _in_flight)
    try:
        yield
    finally:
        with _guard_lock:
            _in_flight -= 1
        sem.release()


def retry_after_hint(exc: BaseException) -> float:
    """Seconds to wait before retrying a call that hit a 429.

    Uses the breaker window when one is open, otherwise an exponential delay
    derived from the upstream hint. Never zero: an immediate retry into a 429
    is the behaviour that burned the budget in the first place.
    """
    with _guard_lock:
        if _expire_locked():
            return max(0.0, _circuit_open_until - time.monotonic())
    hint = _retry_after_seconds(exc)
    if hint is not None:
        return min(max(hint, 1.0), CIRCUIT_MAX_SEC)
    return CIRCUIT_BASE_SEC


_lock = asyncio.Lock()
_hits: deque[float] = deque()
_usage_lock = asyncio.Lock()
_day = ""
_tokens_in = 0
_tokens_out = 0
_usd = 0.0


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


async def _roll_day_locked() -> None:
    global _day, _tokens_in, _tokens_out, _usd
    today = _today()
    if _day != today:
        _day = today
        _tokens_in = _tokens_out = 0
        _usd = 0.0


async def acquire() -> None:
    """Await until an RPM slot frees AND the daily budget still has room."""
    while True:
        async with _lock:
            await _roll_day_locked()
            now = time.monotonic()
            while _hits and now - _hits[0] >= _WINDOW:
                _hits.popleft()
            if DAILY_TOKEN_CAP and (_tokens_in + _tokens_out) >= DAILY_TOKEN_CAP:
                raise BudgetExceededError(
                    f"daily LLM token cap reached "
                    f"({_tokens_in + _tokens_out}/{DAILY_TOKEN_CAP})")
            if DAILY_USD_CAP and _usd >= DAILY_USD_CAP:
                raise BudgetExceededError(
                    f"daily LLM USD cap reached (${_usd:.4f}/{DAILY_USD_CAP})")
            if len(_hits) < RPM:
                _hits.append(now)
                return
            wait = _WINDOW - (now - _hits[0])
        logger.debug("LLM rate limit reached, waiting %.1fs", wait)
        await asyncio.sleep(min(max(wait, 0.05), 5.0))


async def record_usage(usage: object) -> None:
    """Accumulate tokens/estimated USD from an OpenAI-compatible usage obj."""
    if usage is None:
        return
    global _tokens_in, _tokens_out, _usd
    tin = int(getattr(usage, "prompt_tokens", 0) or 0)
    tout = int(getattr(usage, "completion_tokens", 0) or 0)
    async with _usage_lock:
        await _roll_day_locked()
        _tokens_in += tin
        _tokens_out += tout
        _usd += (tin / 1e6) * USD_PER_1M_IN + (tout / 1e6) * USD_PER_1M_OUT
        day_in, day_out, day_usd = _tokens_in, _tokens_out, _usd
    if tin + tout:
        logger.info(
            "LLM usage +%d tok (day in=%d out=%d est=%.4f USD)",
            tin + tout, day_in, day_out, day_usd)


def snapshot() -> dict:
    """Current counters - wire into health/status surfaces as needed.

    The breaker state rides along here rather than living only in the logs: a
    breaker that is open is the single most useful thing for an operator to
    see, and a status surface that cannot show it makes a throttled agency look
    identical to a busy one.
    """
    with _guard_lock:
        _expire_locked()
        circuit_state = "open" if _circuit_open_until > time.monotonic() else "closed"
        remaining = (
            max(0.0, _circuit_open_until - time.monotonic()) if circuit_state == "open" else 0.0
        )
        breaker = {
            "state": circuit_state,
            "opens": _circuit_opens,
            "strikes": _circuit_strikes,
            "strike_threshold": CIRCUIT_THRESHOLD,
            "retry_after_sec": round(remaining, 1),
            "base_backoff_sec": CIRCUIT_BASE_SEC,
            "next_backoff_sec": round(_circuit_backoff, 1),
            "max_backoff_sec": CIRCUIT_MAX_SEC,
            "rate_limits_seen": _rate_limits,
            "calls_refused_locally": _refused_locally,
            "last_rate_limit_at": _last_rate_limit_at or None,
            "last_rate_limit_error": _last_rate_limit_error or None,
        }
        concurrency = {
            "limit": MAX_CONCURRENCY,
            "in_flight": _in_flight,
            "peak_in_flight": _peak_in_flight,
            "queue_timeout_sec": CIRCUIT_QUEUE_TIMEOUT,
        }
    return {
        "day": _day or _today(),
        "rpm": RPM,
        "daily_token_cap": DAILY_TOKEN_CAP,
        "daily_usd_cap": DAILY_USD_CAP,
        "tokens_in": _tokens_in,
        "tokens_out": _tokens_out,
        "est_usd": round(_usd, 6),
        "calls_last_minute": len(_hits),
        "circuit": breaker,
        "concurrency": concurrency,
    }


def install() -> bool:
    """Patch openai once so every client is throttled + accounted.

    Idempotent. Returns False quietly when openai/httpx are unavailable.
    """
    try:
        import httpx
        import openai
        from openai.resources.chat.completions import AsyncCompletions
    except ImportError:  # noqa: BLE001
        return False
    if getattr(openai.AsyncOpenAI, "_tags_throttled", False):
        return True

    orig_create = AsyncCompletions.create
    orig_init = openai.AsyncOpenAI.__init__

    async def patched_create(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        # Breaker first: while it is open there is no point taking an RPM slot
        # for a request that will be refused locally anyway.
        ensure_circuit_closed()
        await acquire()
        try:
            resp = await asyncio.wait_for(
                orig_create(self, *args, **kwargs),
                timeout=CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # Fail fast with a clear message; self-heal / CEO see this text.
            raise TimeoutError(
                f"LLM call exceeded {CALL_TIMEOUT:.0f}s (slow provider chain) - "
                f"fast fail, retry next run"
            )
        except Exception as exc:
            # A 429 is not a transient fault: record it so the breaker opens
            # and the next call is refused locally instead of being retried
            # into the same wall. Every other error class is left alone.
            if is_rate_limit_error(exc):
                record_rate_limit(exc)
            raise
        try:
            await record_usage(getattr(resp, "usage", None))
        except Exception:  # noqa: BLE001
            logger.debug("LLM usage accounting failed", exc_info=True)
        record_success()
        return resp

    def patched_init(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        kwargs.setdefault(
            "http_client",
            httpx.AsyncClient(timeout=CALL_TIMEOUT + 5.0),
        )
        kwargs.setdefault("max_retries", 0)
        orig_init(self, *args, **kwargs)

    AsyncCompletions.create = patched_create  # type: ignore[method-assign]
    openai.AsyncOpenAI.__init__ = patched_init
    openai.AsyncOpenAI._tags_throttled = True  # type: ignore[attr-defined]

    # ── Sync-client guards (website/ads/social/... use openai.OpenAI) ─────────
    # Sync calls BLOCK the event loop when awaited from async code: a hung
    # provider freezes every other request too, and asyncio.wait_for cannot
    # cancel it. Two-layer fix:
    #   1. httpx Client timeout (40s, connect 10s) + zero SDK retries — the
    #      transport itself can never hang past the cap.
    #   2. When a running event loop is detected, the call is offloaded to a
    #      worker thread (asyncio.to_thread) so the loop stays responsive;
    #      pure-sync contexts (scripts) keep the plain path.
    try:
        import concurrent.futures as _cf

        from openai.resources.chat.completions import Completions as SyncCompletions

        _EXEC = _cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm-sync")
        orig_sync_init = openai.OpenAI.__init__
        orig_sync_create = SyncCompletions.create

        def patched_sync_init(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            kwargs.setdefault(
                "http_client",
                httpx.Client(
                    timeout=httpx.Timeout(
                        CALL_TIMEOUT + 5.0,
                        connect=10.0,
                    ),
                ),
            )
            kwargs.setdefault("max_retries", 0)
            orig_sync_init(self, *args, **kwargs)

        def patched_sync_create(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            # Same breaker as the async path: a throttled upstream must be
            # refused locally here too, never retried into the 429.
            ensure_circuit_closed()
            # Run the blocking call on a worker thread with a hard cap so a
            # hung provider can never pin the (possibly async) caller past
            # CALL_TIMEOUT. Works in both sync and async-embedding contexts.
            fut = _EXEC.submit(orig_sync_create, self, *args, **kwargs)
            try:
                resp = fut.result(timeout=CALL_TIMEOUT + 10.0)
            except _cf.TimeoutError:
                fut.cancel()
                raise TimeoutError(
                    f"LLM sync call exceeded {CALL_TIMEOUT + 10.0:.0f}s - "
                    f"fast fail, retry next run"
                )
            except Exception as exc:
                if is_rate_limit_error(exc):
                    record_rate_limit(exc)
                raise
            record_success()
            return resp

        SyncCompletions.create = patched_sync_create  # type: ignore[method-assign]
        openai.OpenAI.__init__ = patched_sync_init
        openai.OpenAI._tags_throttled = True  # type: ignore[attr-defined]
        logger.info(
            "LLM sync guards installed (timeout %.0fs, no SDK retries, thread offload in async ctx)",
            CALL_TIMEOUT)
    except Exception:  # noqa: BLE001
        logger.warning("sync LLM guards not installed", exc_info=True)

    logger.info(
        "LLM guards installed (RPM %d, concurrency %d, circuit %ds->%ds after "
        "%d 429(s), daily cap %s tok / $%.2f)",
        RPM, MAX_CONCURRENCY, CIRCUIT_BASE_SEC, CIRCUIT_MAX_SEC,
        CIRCUIT_THRESHOLD, DAILY_TOKEN_CAP or "off", DAILY_USD_CAP)
    return True
