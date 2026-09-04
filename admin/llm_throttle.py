"""Agency-wide LLM rate limiter + budget guard, enforced in code.

Loop-engineering cost-ceiling guardrails, both hard-enforced here:

1. RPM cap - every AsyncOpenAI client shares one throttled pipeline so
   parallel agent blasts stay under the provider ceiling (default 38 under
   OpenCode Zen's 40). No 429s ever reach an agent.
2. Daily budget - UTC-day token/$ counters; when a configured cap is hit,
   acquire() raises BudgetExceededError so agents fail fast (CEO heals /
   escalates) instead of silently burning spend. Caps default OFF (0).

install() patches openai's AsyncCompletions.create exactly once at startup:
throttle -> original call -> usage accounting. Agent call sites stay
untouched and future agents inherit both guards automatically.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import time
from collections import deque

logger = logging.getLogger(__name__)

RPM = max(1, int(os.getenv("AGENCY_LLM_RPM", "38")))
_WINDOW = 60.0
DAILY_TOKEN_CAP = int(os.getenv("AGENCY_LLM_DAILY_TOKENS", "0"))  # 0 = off
DAILY_USD_CAP = float(os.getenv("AGENCY_LLM_DAILY_USD", "0"))     # 0 = off
USD_PER_1M_IN = float(os.getenv("AGENCY_LLM_USD_IN", "0"))
USD_PER_1M_OUT = float(os.getenv("AGENCY_LLM_USD_OUT", "0"))


class BudgetExceededError(RuntimeError):
    """Daily LLM budget exhausted - fail fast instead of burning spend."""


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
    """Current counters - wire into health/status surfaces as needed."""
    return {
        "day": _day or _today(),
        "rpm": RPM,
        "daily_token_cap": DAILY_TOKEN_CAP,
        "daily_usd_cap": DAILY_USD_CAP,
        "tokens_in": _tokens_in,
        "tokens_out": _tokens_out,
        "est_usd": round(_usd, 6),
        "calls_last_minute": len(_hits),
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
        await acquire()
        resp = await orig_create(self, *args, **kwargs)
        try:
            await record_usage(getattr(resp, "usage", None))
        except Exception:  # noqa: BLE001
            logger.debug("LLM usage accounting failed", exc_info=True)
        return resp

    def patched_init(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        kwargs.setdefault(
            "http_client",
            httpx.AsyncClient(timeout=120.0),
        )
        orig_init(self, *args, **kwargs)

    AsyncCompletions.create = patched_create  # type: ignore[method-assign]
    openai.AsyncOpenAI.__init__ = patched_init
    openai.AsyncOpenAI._tags_throttled = True  # type: ignore[attr-defined]
    logger.info(
        "LLM guards installed (RPM %d, daily cap %s tok / $%.2f)",
        RPM, DAILY_TOKEN_CAP or "off", DAILY_USD_CAP)
    return True
