# admin/agency/sba_reason.py
"""LLM reasoning layer for the SBA autopilot.

The 24/7 loop must never hang, so every reasoning step runs inside a hard
timeout and falls back to a deterministic rule when the model is slow or
unavailable. When the model answers, the autopilot *thinks*: it scores new
leads, double-checks that a candidate email really belongs to the business,
understands replies (including meeting times), and writes a journal line so
the owner can see why each decision was made.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
from typing import Any

from admin.config import settings

logger = logging.getLogger("sba.reason")

REASON_TIMEOUT_SECONDS = int(os.environ.get("SBA_REASON_TIMEOUT_SECONDS", "12"))
REASON_LOG = os.environ.get(
    "SBA_REASON_LOG",
    "/home/ubuntu/sba-backend/sba_reasoning.log",
)
# How many leads the agent may judge concurrently in one pass.
JUDGE_CONCURRENCY = 4


async def _llm_call(system: str, user: str, timeout: int = REASON_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Call the workspace LLM and parse the first JSON object it returns."""
    import openai

    client = openai.AsyncOpenAI(
        api_key=settings.WORKSPACE_API_KEY or None,
        base_url=settings.WORKSPACE_API_BASE or None,
    )
    resp = await asyncio.wait_for(
        client.chat.completions.create(
            model=settings.WORKSPACE_AGENT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        ),
        timeout=timeout,
    )
    content = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError("LLM did not return JSON")
    return json.loads(m.group(0))


async def judge_lead(lead: dict[str, Any]) -> dict[str, Any]:
    """Score a scraped lead 0-100 and recommend contact/wait/skip.

    Fallback (model down): score 50, action 'contact' — same as today, so a
    flaky model never stops the pipeline from working.
    """
    name = (lead.get("name") or "").strip()
    if not name:
        return {"score": 0, "action": "skip", "reason": "empty name"}
    system = (
        "You are a sharp B2B sales-qualification analyst for a web-design and "
        "local-SEO agency that sells to local small businesses. Score whether "
        "this business is worth a cold email TODAY. Consider: is it a real local "
        "business (not a national chain/brand/franchise or a fake listing), does "
        "it likely need more customers, and does it have its own website? "
        'Return ONLY JSON: {"score": 0-100, "action": "contact"|"wait"|"skip", '
        '"reason": "one short sentence explaining your score"}'
    )
    user = (
        f"Business: {name}\nCategory: {lead.get('category') or ''}\n"
        f"Location: {lead.get('city_state') or ''}\n"
        f"Website: {lead.get('website') or 'none found'}"
    )
    try:
        data = await _llm_call(system, user)
        score = max(0, min(100, int(data.get("score", 50))))
        action = data.get("action") if data.get("action") in ("contact", "wait", "skip") else "contact"
        reason = str(data.get("reason") or "")[:300]
        return {"score": score, "action": action, "reason": reason}
    except Exception as exc:  # noqa: BLE001
        logger.info("judge_lead fallback for %s: %s", name, exc)
        return {"score": 50, "action": "contact", "reason": ""}


async def verify_email(name: str, email: str, sources: list[str] | None = None) -> dict[str, Any]:
    """Ask the LLM whether this candidate email really belongs to this business.

    This is a *second opinion* on top of the deterministic gate: it only ever
    blocks a send (never unblocks one). Fallback (model down) is ok=True so a
    flaky model never silently blocks a legitimate mailbox.
    """
    system = (
        "You are an email-verification analyst. A cold-outreach system scraped a "
        "candidate email for a local business. Decide whether this email is almost "
        "certainly the BUSINESS'S OWN mailbox (owned by the owner or manager), not "
        "a third party, a placeholder, a big parent brand, or an unrelated company. "
        'Return ONLY JSON: {"ok": true|false, "confidence": 0-1, '
        '"reason": "one short sentence explaining your verdict"}'
    )
    src = ", ".join((sources or [])[:3]) or "unknown source"
    user = f"Business: {name}\nCandidate email: {email}\nFound on: {src}"
    try:
        data = await _llm_call(system, user)
        ok = bool(data.get("ok"))
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        reason = str(data.get("reason") or "")[:300]
        return {"ok": ok, "confidence": conf, "reason": reason}
    except Exception as exc:  # noqa: BLE001
        logger.info("verify_email fallback for %s: %s", email, exc)
        return {"ok": True, "confidence": 0.5, "reason": ""}


async def understand_reply(text: str) -> dict[str, Any]:
    """Understand a lead's email reply: interest level + any meeting time.

    Fallback: keyword classifier (same behaviour as today).
    """
    system = (
        "You read replies from local business owners to a cold outreach email. "
        "Classify the interest and extract any proposed meeting time. "
        'Return ONLY JSON: {"intent": "yes"|"no"|"maybe"|"stop"|"other", '
        '"meeting_time": "HH:MM" or "", "reason": "one short sentence"}'
    )
    try:
        data = await _llm_call(system, (text or "")[:2000])
        intent = data.get("intent") if data.get("intent") in ("yes", "no", "maybe", "stop", "other") else "other"
        mt = str(data.get("meeting_time") or "").strip()
        if mt and not re.match(r"^\d{1,2}:\d{2}$", mt):
            mt = ""
        return {"intent": intent, "meeting_time": mt, "reason": str(data.get("reason") or "")[:300]}
    except Exception as exc:  # noqa: BLE001
        logger.info("understand_reply fallback: %s", exc)
        # Be HONEST when the analysis model is unavailable (e.g. free key hit a
        # 429 rate limit): do NOT fabricate an answer. The caller surfaces the
        # raw reply count and flags the uncertainty instead of inventing intent.
        from admin.agency.sba_pipeline import classify_reply  # lazy: avoid cycles

        kind = classify_reply(text)
        return {
            "intent": kind, "meeting_time": "", "reason": "",
            "uncertain": True,
            "note": "LLM unavailable — could not analyze reply (raw count still surfaced)",
        }


def prioritize(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order leads so the pass spends its caps on the best prospects first.

    The agent score decides within a tier. Tier bumps (added to the score):
      +2000  already has an email -> ready to send now (daily send cap is
             scarce, so sendable leads beat leads that still need enrichment)
      +1000  has a website but no email -> enrichment-ready: the homepage can
             be crawled for a real mailbox, so they yield emails far more often
             than no-website leads (Bing-only). Without this bump the 12/pass
             enrichment budget burns through 600+ no-website leads first and
             the good leads wait ~17h for their turn.
    """
    def _score(lead: dict[str, Any]) -> float:
        raw = lead.get("raw") or {}
        s = raw.get("lead_score")
        try:
            base = float(s) if s is not None else 50.0
        except (TypeError, ValueError):
            base = 50.0
        email = (lead.get("email") or "").strip()
        website = (lead.get("website") or "").strip()
        if email:
            return base + 2000.0
        if website:
            return base + 1000.0
        return base

    return sorted(leads, key=lambda l: -_score(l))


def log_decision(entry: dict[str, Any], log_path: str | None = None) -> None:
    """Append one reasoning line to the decision journal (JSONL).

    log_path overrides the global journal (used for per-workspace journals).
    """
    try:
        path = log_path or REASON_LOG
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        entry = dict(entry)
        entry.setdefault("ts", dt.datetime.now(dt.timezone.utc).isoformat())
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("log_decision failed: %s", exc)


def recent_decisions(limit: int = 50, event: str | None = None, log_path: str | None = None) -> list[dict[str, Any]]:
    """Tail of the decision journal (newest first), for the dashboard/API."""
    out: list[dict[str, Any]] = []
    try:
        with open(log_path or REASON_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event and entry.get("event") != event:
                    continue
                out.append(entry)
    except FileNotFoundError:
        return out
    return out[-limit:][::-1]
