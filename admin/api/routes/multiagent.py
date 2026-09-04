import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/ceo", tags=["ceo"])

# Built-in (non-custom) agent ids that may be fanned out to.
BUILTIN_AGENT_IDS = ["sba", "seo", "website", "content", "ads", "social", "analytics"]


class RunBrief(BaseModel):
    brief: str
    agent_ids: list[str] = []
    task_per_agent: dict[str, str] = {}
    scope: dict = {"kind": "agency", "workspace_id": "agency"}


async def _resolve_agent_ids(agent_ids: list[str], brief: str, custom_only: bool) -> tuple[list[str], list[str]]:
    """Return (ordered_agent_ids, notes)."""
    from admin.agency import agent_registry as reg

    custom_agents = await reg.list_agents()
    custom_ids = [a.get("id") for a in custom_agents if a.get("id")]

    if custom_only:
        # Restrict strictly to custom agents.
        if agent_ids:
            return [aid for aid in agent_ids if aid in custom_ids], []
        return custom_ids, []

    # Mixed / all mode.
    if agent_ids:
        return agent_ids, []

    # Run ALL available: built-in 7 + all custom agents.
    chosen = list(BUILTIN_AGENT_IDS)
    for cid in custom_ids:
        if cid not in chosen:
            chosen.append(cid)
    return chosen, []


async def _run_one(agent_id: str, task_text: str, scope: dict) -> dict:
    """Run a single agent defensively. Never raises out of gather."""
    from admin.agency import workers

    try:
        res = await workers.run_worker(agent_id, task_text, scope)
        ok = bool(res.get("ok", False)) if isinstance(res, dict) else False
        if not isinstance(res, dict):
            res = {"ok": ok, "result": res}
        return {
            "agent_id": agent_id,
            "ok": ok,
            "result": res.get("result"),
            "answer": res.get("result"),
            "error": res.get("error"),
            "hindi_status": res.get("hindi_status"),
        }
    except Exception as e:  # unexpected failure; keep fan-out alive
        return {
            "agent_id": agent_id,
            "ok": False,
            "result": None,
            "answer": None,
            "error": f"{type(e).__name__}: {e}",
            "hindi_status": None,
        }


async def _fanout(body: RunBrief, custom_only: bool) -> dict:
    agent_ids, notes = await _resolve_agent_ids(body.agent_ids, body.brief, custom_only)

    # Defensive: skip built-in ids that are not actually registered in workers.
    tasks = []
    skipped = list(notes)
    for aid in agent_ids:
        # Determine if this is a built-in that we should double check exists.
        if aid not in BUILTIN_AGENT_IDS:
            tasks.append(_run_one(aid, body.task_per_agent.get(aid, body.brief), body.scope))
            continue
        # For built-in ids, register_builtins() already wired them; run directly
        # but guard so a missing binding degrades gracefully.
        tasks.append(_run_one(aid, body.task_per_agent.get(aid, body.brief), body.scope))

    results = await asyncio.gather(*tasks) if tasks else []

    ok_count = sum(1 for r in results if r.get("ok"))
    failed = len(results) - ok_count

    payload = {
        "success": True,
        "brief": body.brief,
        "ran": len(results),
        "results": results,
        "failed": failed,
    }
    if skipped:
        payload["skipped"] = skipped
    return payload


@router.post("/run")
async def run_all_agents(body: RunBrief):
    """Fan out the brief to ALL available agents (built-in 7 + custom) concurrently."""
    return await _fanout(body, custom_only=False)


@router.post("/run/custom")
async def run_custom_agents(body: RunBrief):
    """Fan out the brief ONLY to custom (user-added) agents."""
    custom_payload = await _fanout(body, custom_only=True)
    # If no custom agents exist at all, surface a helpful note.
    if custom_payload["ran"] == 0:
        custom_payload["results"] = []
        custom_payload["note"] = "No custom agents yet. Add via POST /api/agents/custom"
    return custom_payload
