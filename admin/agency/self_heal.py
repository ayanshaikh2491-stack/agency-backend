"""CEO self-healing routine (§2.5 — "CEO khud heal karega").

When an agent fails (tool crash, API/429 error, timeout, missing credential),
the CEO does NOT stall and does NOT wait for the boss. It:
  1. detects the failure (pushed by the delegation path),
  2. classifies the error (transient vs config/credential vs tool/code),
  3. fixes/retries the ORIGINAL task (not just "analyze" — it re-runs the work),
  4. escalates to the boss only after N failed attempts.

This is the "CEO agent backend mai khud jayega, error + tool sahi karega, kaam
rukna nahi chahiye" requirement. Built on top of the existing
`_tool_route_error` error-routing concept, but adds automatic detection,
re-dispatch of the ORIGINAL brief, retry-with-backoff, and escalation.
"""
from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger("agency.self_heal")

_MAX_ATTEMPTS = 3

_TRANSIENT_RE = re.compile(
    r"429|rate.?limit|too many requests|timeout|timed out|50[23]|"
    r"connection reset|temporarily|service unavailable|econn",
    re.I,
)
_CONFIG_RE = re.compile(
    r"api[_ ]?key|unauthor|401|403|forbidden|invalid.*token|missing.*credential|"
    r"authentication|not.?configured|api[_ ]?key.*(not|missing)|quota",
    re.I,
)
_TOOL_RE = re.compile(
    r"traceback|exception:|typeerror|attributeerror|modulenotfound|importerror|"
    r"valueerror|keyerror|tool.*(fail|error|crash)|could not (find|load)|"
    r"attribute.*not.*found|method.*not.*found",
    re.I,
)


def classify_error(error: str) -> str:
    """Return 'transient' | 'config' | 'tool' | 'unknown'."""
    e = error or ""
    if _TRANSIENT_RE.search(e):
        return "transient"
    if _CONFIG_RE.search(e):
        return "config"
    if _TOOL_RE.search(e):
        return "tool"
    return "unknown"


def looks_like_error(text: str) -> bool:
    """Conservative check: does a built-in agent's response text look like a failure?"""
    return bool(_TOOL_RE.search(text or "")) or bool(_TRANSIENT_RE.search(text or ""))


async def _redispatch(
    slug: str, workspace_id: str, task: str, context: str = ""
) -> tuple[bool, str]:
    """Re-run the ORIGINAL task on the agent. Returns (ok, response_or_error)."""
    # Custom / user-added agent (Munder-style)?
    try:
        from admin.agency import agent_registry as reg

        custom = await reg.get_agent(slug)
    except Exception:  # noqa: BLE001
        custom = None

    try:
        if custom is not None:
            from admin.agency.workers import run_worker

            result = await run_worker(
                slug, task,
                {"scope": {"kind": "workspace", "workspace_id": workspace_id}},
            )
            ok = result.get("ok", False)
            resp = (
                (result.get("result") or {}).get("answer")
                or result.get("error")
                or str(result)
            )
            return ok, resp

        # Built-in domain agent
        from admin.workspace.manager import route_to_agent

        resp = await route_to_agent(
            workspace_id=workspace_id, agent_type=slug, message=task
        )
        # Built-in returns a string; clear error markers => failure.
        if looks_like_error(resp):
            return False, resp
        return True, resp
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


async def heal_agent(
    slug: str,
    workspace_id: str,
    task: str,
    context: str = "",
    error: str = "",
    attempt: int = 1,
    max_attempts: int = _MAX_ATTEMPTS,
) -> dict:
    """CEO self-heal loop for one failed agent run.

    Returns dict: {ok, healed, attempts, result, escalate, diagnosis}.
    """
    classification = classify_error(error)
    logger.info(
        "SELF-HEAL [%s/%s] attempt=%d classification=%s",
        slug, workspace_id, attempt, classification,
    )

    # Record state for /api/ceo/state visibility.
    try:
        from admin.agency.lifecycle import mark_error

        mark_error(slug, f"heal attempt {attempt}: {error[:200]}")
    except Exception:  # noqa: BLE001
        pass

    # ── Transient (429 / timeout / 503) ──────────────────────────────────────
    # Backoff then re-run the ORIGINAL task.
    if classification == "transient":
        await asyncio.sleep(min(2 ** attempt, 10))  # 4s, 8s, ...
        ok, resp = await _redispatch(slug, workspace_id, task, context)
        if ok:
            return {
                "ok": True, "healed": True, "attempts": attempt,
                "result": resp, "escalate": False, "diagnosis": "transient-retried",
            }
        if attempt < max_attempts:
            return await heal_agent(
                slug, workspace_id, task, context, resp, attempt + 1, max_attempts
            )
        return {
            "ok": False, "healed": False, "attempts": attempt,
            "result": resp, "escalate": True, "diagnosis": "transient-exhausted",
        }

    # ── Config / credential (missing key, 401/403, quota) ────────────────────
    # CEO cannot mint secrets by itself -> escalate with a clear owner action.
    if classification == "config":
        return {
            "ok": False, "healed": False, "attempts": attempt,
            "result": (
                f"{slug} needs a credential/API-key fix (owner action): {error}"
            ),
            "escalate": True, "diagnosis": "config-escalate",
        }

    # ── Tool / code crash ────────────────────────────────────────────────────
    # Try a re-run; if still failing, escalate with root cause (CEO can't patch
    # code itself — a fixer/debug agent could be dispatched here if registered).
    ok, resp = await _redispatch(slug, workspace_id, task, context)
    if ok:
        return {
            "ok": True, "healed": True, "attempts": attempt,
            "result": resp, "escalate": False, "diagnosis": "tool-retried",
        }
    if attempt < max_attempts:
        return await heal_agent(
            slug, workspace_id, task, context, resp, attempt + 1, max_attempts
        )
    return {
        "ok": False, "healed": False, "attempts": attempt,
        "result": resp, "escalate": True, "diagnosis": "tool-exhausted",
    }


async def heal_and_report(
    slug: str,
    workspace_id: str,
    task: str,
    context: str = "",
    error: str = "",
) -> str:
    """Run heal and return a boss-readable Hindi/English status string."""
    res = await heal_agent(slug, workspace_id, task, context, error)
    if res["ok"]:
        return (
            f"CEO self-heal ✅: {slug} fail hua tha ({res['diagnosis']}), "
            f"CEO ne khud fix kiya aur kaam dobara chalaya. "
            f"Result: {str(res['result'])[:300]}"
        )
    if res["escalate"]:
        return (
            f"CEO self-heal ⚠️: {slug} {res['attempts']} attempts ke baad bhi fail. "
            f"Diagnosis: {res['diagnosis']}. CEO khud nahi kar paya — owner ko dekhna "
            f"padega. Last error: {str(res['result'])[:300]}"
        )
    return f"CEO self-heal: {slug} — {res['diagnosis']}. {str(res['result'])[:300]}"
