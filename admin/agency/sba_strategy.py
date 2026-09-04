# admin/agency/sba_strategy.py
"""Layer 2 + 3: the autopilot's self-review loop and strategy memory.

Layer 1 (sba_reason) makes the agent *think* about each decision. This module
makes the agent think about *its own business*: every pass it observes its
results (emails sent, replies, meetings, leads found), and on a schedule - or
when something important happens (first meeting booked, several passes with
zero leads) - it asks the LLM to review the strategy: is the message angle
working? which niches respond? what should change?

The decisions are saved to a strategy file and journaled (event
`strategy_review`) so the owner can watch the agent learning. It also owns
proactive owner communication: a daily digest email and failure alerts
("no new leads for N passes - here is my plan").
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from typing import Any

from admin.agency import sba_reason as reason

logger = logging.getLogger("sba.strategy")

# The reasoning journal lives in the reason module; expose the same path here
# so callers/tests can seed or inspect it through either module.
REASON_LOG = reason.REASON_LOG

STRATEGY_FILE = os.environ.get(
    "SBA_STRATEGY_FILE",
    "/home/ubuntu/sba-backend/sba_strategy.json",
)
# How often the agent reviews its own strategy (also runs on milestones).
REVIEW_INTERVAL_SECONDS = int(os.environ.get("SBA_STRATEGY_REVIEW_INTERVAL_SECONDS", str(60 * 60)))
# Owner emails: daily summary + failure alerts, never more often than this.
DIGEST_INTERVAL_SECONDS = int(os.environ.get("SBA_STRATEGY_DIGEST_INTERVAL_SECONDS", str(24 * 3600)))
# Alert thresholds: N straight passes with zero new leads / zero reply signal.
ALERT_ZERO_LEAD_PASSES = int(os.environ.get("SBA_ALERT_ZERO_LEAD_PASSES", "3"))
ALERT_ZERO_REPLY_PASSES = int(os.environ.get("SBA_ALERT_ZERO_REPLY_PASSES", "8"))
# Journal window the agent learns from in each review.
METRICS_WINDOW_HOURS = int(os.environ.get("SBA_STRATEGY_METRICS_HOURS", "48"))
# How long an LLM strategy review may take before the loop moves on.
STRATEGY_TIMEOUT_SECONDS = int(os.environ.get("SBA_STRATEGY_TIMEOUT_SECONDS", "30"))

DEFAULT_STRATEGY: dict[str, Any] = {
    "angle": "Help the business get more local customers with a professional website and local SEO",
    "focus": [],          # [["niche","city","ST"], ...] agent-chosen priority targets
    "notes": [],          # what the data says (recent learnings)
    "actions": [],        # concrete changes the agent decided to make
    "last_review": None,
    "last_digest": None,
    "last_alert": None,
    "zero_lead_passes": 0,
    "zero_reply_passes": 0,
    "history": [],        # [{ts, metrics, angle, focus, actions}] capped at 30
}

OWNER_DIGEST_SUBJECTS = {
    "daily": "SBA Agent: daily summary",
    "alert_leads": "SBA Agent: no new leads!",
    "alert_replies": "SBA Agent: outreach is not working",
}


def _ts() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ── Persistence ────────────────────────────────────────────────────────────


def load_strategy(path: str | None = None) -> dict[str, Any]:
    s = dict(DEFAULT_STRATEGY)
    try:
        with open(path or STRATEGY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULT_STRATEGY:
                if k in data:
                    s[k] = data[k]
    except Exception as exc:  # noqa: BLE001
        logger.debug("load_strategy: %s", exc)
    return s


def save_strategy(s: dict[str, Any], path: str | None = None) -> bool:
    try:
        target = path or STRATEGY_FILE
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, default=str)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("save_strategy failed: %s", exc)
        return False


def active_strategy(path: str | None = None) -> dict[str, Any]:
    """Public snapshot for the dashboard/API (defaults when nothing saved)."""
    return load_strategy(path=path)


# ── Observation: the agent tracks its own performance ─────────────────────


def observe_pass(stats: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    """Update streak counters from one pass's results. Never raises."""
    s = load_strategy(path=path)
    leads = int(stats.get("new_leads_found") or 0)
    s["zero_lead_passes"] = 0 if leads > 0 else int(s.get("zero_lead_passes", 0)) + 1
    sent = int(stats.get("emails_sent") or 0)
    signal = int(stats.get("owner_notified") or 0) + int(stats.get("meetings_scheduled") or 0)
    s["zero_reply_passes"] = 0 if (sent == 0 or signal > 0) else int(s.get("zero_reply_passes", 0)) + 1
    save_strategy(s, path=path)
    return s


def metrics_from_journal(hours: int | None = None, log_path: str | None = None) -> dict[str, Any]:
    """Aggregate the agent's own journal into a performance snapshot.

    Only pass summaries inside the window count; an empty journal yields an
    empty snapshot so the loop never wastes a review (or an email) on nothing.
    log_path selects a per-workspace journal.
    """
    hours = hours or METRICS_WINDOW_HOURS
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=hours)
    passes: list[dict[str, Any]] = []
    replies_yes = 0
    meetings = 0
    by_category: dict[str, dict[str, int]] = {}
    for e in reason.recent_decisions(limit=500, log_path=log_path):
        ts = e.get("ts") or ""
        try:
            t = dt.datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if t < cutoff:
            continue
        ev = e.get("event")
        if ev == "pass_summary":
            passes.append(e.get("stats") or {})
        elif ev == "reply_understood" and e.get("intent") == "yes":
            replies_yes += 1
        elif ev == "email_sent":
            cat = (e.get("category") or "other").strip().lower() or "other"
            by_category.setdefault(cat, {"sent": 0})["sent"] += 1
    sent = sum(int(p.get("emails_sent") or 0) for p in passes)
    found = sum(int(p.get("new_leads_found") or 0) for p in passes)
    rejected = sum(
        int(p.get("invalid_email") or 0) + int(p.get("no_email") or 0) + int(p.get("rejected") or 0)
        for p in passes
    )
    return {
        "passes": len(passes),
        "emails_sent": sent,
        "replies_yes": replies_yes,
        "meetings": meetings,
        "reply_rate": round(replies_yes / sent, 3) if sent else 0.0,
        "meeting_rate": round(meetings / sent, 3) if sent else 0.0,
        "leads_found": found,
        "rejected": rejected,
        "window_hours": hours,
        "by_category": by_category,
    }


# ── Review: the agent thinks about its own strategy ────────────────────────


async def _llm_review_call(system: str, user: str) -> dict[str, Any]:
    """Call the workspace LLM for a strategy review (longer timeout)."""
    import asyncio
    import openai

    from admin.config import settings

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
            temperature=0.4,
        ),
        timeout=STRATEGY_TIMEOUT_SECONDS,
    )
    content = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError("LLM did not return JSON")
    return json.loads(m.group(0))


def _sanitize_review(data: dict[str, Any]) -> dict[str, Any]:
    angle = str(data.get("angle") or DEFAULT_STRATEGY["angle"])[:240].strip() or DEFAULT_STRATEGY["angle"]
    focus: list[list[str]] = []
    for t in (data.get("focus") or []):
        if isinstance(t, (list, tuple)) and len(t) == 3 and all(isinstance(x, str) and x.strip() for x in t):
            focus.append([t[0].strip()[:60], t[1].strip()[:60], t[2].strip()[:2].upper()])
        if len(focus) >= 3:
            break
    notes = [str(x).strip()[:200] for x in (data.get("notes") or []) if str(x).strip()][:5]
    actions = [str(x).strip()[:200] for x in (data.get("actions") or []) if str(x).strip()][:6]
    return {"angle": angle, "focus": focus, "notes": notes, "actions": actions}


async def review_strategy(force: bool = False, metrics: dict[str, Any] | None = None,
                         path: str | None = None, log_path: str | None = None) -> dict[str, Any] | None:
    """Ask the LLM to review the strategy and persist the changes.

    Never raises and never blocks the loop: on model failure it returns None
    and the previous strategy stays in place. `path`/`log_path` select a
    per-workspace strategy file and journal.
    """
    s = load_strategy(path=path)
    metrics = metrics or metrics_from_journal(log_path=log_path)
    if not metrics.get("passes"):
        logger.info("strategy review skipped: no pass data in window")
        return None
    if not force and s.get("last_review"):
        try:
            last = dt.datetime.fromisoformat(s["last_review"])
        except (TypeError, ValueError):
            last = None
        if last and (dt.datetime.now(dt.timezone.utc) - last).total_seconds() < REVIEW_INTERVAL_SECONDS:
            return None
    system = (
        "You are the strategy brain of a one-person AI agency that sells websites "
        "and local SEO to small local businesses via cold email. The outreach "
        "agent below ran its own campaigns and collected the results. Review the "
        "strategy and decide what to change: the message angle, which niche+city "
        "targets to prioritize next, what to keep doing, and what to stop. Be "
        "concrete, practical, and honest - if the data is thin, say so and keep "
        "the change small. "
        'Return ONLY JSON: {"angle": "one-sentence current message angle", '
        '"focus": [["niche","city","ST"], ...] max 3 niche-city targets to prioritize next '
        '(use the same niche/city names seen in the data; empty list = keep current rotation), '
        '"notes": ["what the data says", ...] max 3, '
        '"actions": ["concrete next action", ...] max 4}'
    )
    user = (
        "CURRENT STRATEGY:\n"
        f"angle: {s.get('angle')}\n"
        f"focus: {s.get('focus') or 'default rotation'}\n"
        f"recent notes: {s.get('notes') or []}\n"
        f"planned actions: {s.get('actions') or []}\n\n"
        f"RESULTS FROM THE LAST {metrics['window_hours']} HOURS:\n"
        + json.dumps({k: v for k, v in metrics.items()}, default=str, indent=2)
    )
    try:
        data = await _llm_review_call(system, user)
    except Exception as exc:  # noqa: BLE001
        logger.info("strategy review LLM failed: %s", exc)
        return None
    delta = _sanitize_review(data)
    s["angle"] = delta["angle"]
    if delta["focus"]:
        s["focus"] = delta["focus"]
    if delta["notes"]:
        s["notes"] = (delta["notes"] + (s.get("notes") or []))[:5]
    s["actions"] = delta["actions"] or (s.get("actions") or [])
    s["last_review"] = _ts()
    hist = list(s.get("history") or [])
    hist.append({
        "ts": s["last_review"],
        "metrics": {k: v for k, v in metrics.items() if k != "by_category"},
        "angle": s["angle"],
        "focus": s["focus"],
        "actions": s["actions"],
    })
    s["history"] = hist[-30:]
    save_strategy(s, path=path)
    reason.log_decision({
        "event": "strategy_review",
        "angle": s["angle"],
        "focus": s["focus"],
        "actions": s["actions"],
        "metrics": {k: v for k, v in metrics.items() if k != "by_category"},
    }, log_path=log_path)
    logger.info("strategy reviewed: angle=%r focus=%s", s["angle"], s["focus"])
    return s


async def maybe_review(stats: dict[str, Any], force: bool = False,
                       path: str | None = None, log_path: str | None = None) -> dict[str, Any] | None:
    """Called after every pass. Observes results, then reviews when due.

    Milestones force an early review: a meeting just booked (the agent wants
    to know why it worked) or an alert-level streak (things are broken).
    """
    s = observe_pass(stats, path=path)
    metrics = metrics_from_journal(log_path=log_path)
    milestone = (
        int(stats.get("meetings_scheduled") or 0) > 0
        or int(stats.get("owner_notified") or 0) > 0
        or int(s.get("zero_lead_passes", 0)) >= ALERT_ZERO_LEAD_PASSES
        or int(s.get("zero_reply_passes", 0)) >= ALERT_ZERO_REPLY_PASSES
    )
    return await review_strategy(force=force or milestone, metrics=metrics, path=path, log_path=log_path)


# ── Owner communication: the agent reports to its boss ─────────────────────


def digest_kind_needed(stats: dict[str, Any], metrics: dict[str, Any], path: str | None = None) -> str:
    """Which owner email (if any) is due right now: '' = none."""
    s = load_strategy(path=path)
    if int(s.get("zero_lead_passes", 0)) >= ALERT_ZERO_LEAD_PASSES and digest_due("alert_leads", path=path):
        return "alert_leads"
    if int(s.get("zero_reply_passes", 0)) >= ALERT_ZERO_REPLY_PASSES and digest_due("alert_replies", path=path):
        return "alert_replies"
    if metrics.get("passes") and digest_due("daily", path=path):
        return "daily"
    return ""


def digest_due(kind: str, path: str | None = None) -> bool:
    s = load_strategy(path=path)
    key = "last_digest" if kind == "daily" else "last_alert"
    last = s.get(key)
    if not last:
        return True
    try:
        last_t = dt.datetime.fromisoformat(str(last))
    except (TypeError, ValueError):
        return True
    return (dt.datetime.now(dt.timezone.utc) - last_t).total_seconds() >= DIGEST_INTERVAL_SECONDS


def mark_digest(kind: str, path: str | None = None) -> None:
    s = load_strategy(path=path)
    s["last_digest" if kind == "daily" else "last_alert"] = _ts()
    save_strategy(s, path=path)


def build_digest_body(kind: str, stats: dict[str, Any], metrics: dict[str, Any], strategy: dict[str, Any]) -> str:
    """Plain-text owner email body; deterministic (no extra LLM cost)."""
    focus = ", ".join(f"{c} {ci},{st}" for c, ci, st in (strategy.get("focus") or []))
    lines: list[str] = []
    if kind == "daily":
        lines += [
            "Daily SBA Agent summary",
            "=" * 32,
            f"Last pass: {stats.get('emails_sent', 0)} emails sent, "
            f"{stats.get('new_leads_found', 0)} new leads found, "
            f"{stats.get('owner_notified', 0)} interested replies, "
            f"{stats.get('meetings_scheduled', 0)} meetings scheduled.",
            f"Last {metrics.get('window_hours', '48')}h: {metrics.get('emails_sent', 0)} emails, "
            f"{metrics.get('replies_yes', 0)} yes-replies, {metrics.get('meetings', 0)} meetings, "
            f"{metrics.get('leads_found', 0)} leads found.",
            "",
            f"Message angle: {strategy.get('angle') or '-'}",
            f"Agent focus: {focus or 'default rotation'}",
            "",
            "What the agent decided to do:",
        ]
        for a in (strategy.get("actions") or [])[:5]:
            lines.append(f"  - {a}")
        if not (strategy.get("actions") or []):
            lines.append("  - keep rotating through the configured niches")
        lines += ["", "Full reasoning journal: GET /api/sba/reasoning"]
    elif kind == "alert_leads":
        lines += [
            "SBA Agent alert: no new leads found",
            "=" * 32,
            f"The agent found 0 new leads for {strategy.get('zero_lead_passes', 0)} straight "
            "passes. The rotation may be exhausted or a scraper is blocked.",
            "",
            "Agent's plan:",
        ]
        for a in (strategy.get("actions") or [])[:4]:
            lines.append(f"  - {a}")
        lines += ["", "It will keep retrying on the next pass and review again if it stays zero."]
    elif kind == "alert_replies":
        lines += [
            "SBA Agent alert: outreach is not getting replies",
            "=" * 32,
            f"The agent sent emails for {strategy.get('zero_reply_passes', 0)} straight passes "
            "with no interested reply. It reviewed its own strategy and is changing tack.",
            "",
            "Agent's plan:",
        ]
        for a in (strategy.get("actions") or [])[:4]:
            lines.append(f"  - {a}")
        lines += ["", "Details are in the reasoning journal: GET /api/sba/reasoning"]
    return "\n".join(lines)
