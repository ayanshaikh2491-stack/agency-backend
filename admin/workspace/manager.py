"""Workspace manager — CRUD + agent output tracking, reviews, error routing.

Dual-writes to in-memory (fast) and SQLite (persistent).
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from admin.api.models.schemas import WorkspaceCreate, WorkspaceOut
from admin.persistence import get_workspace_db, row_to_dict
from admin.workspace.agent_bus import _fire_and_forget

import logging
logger = logging.getLogger(__name__)

# ── In-memory store (swap with DB later) ───────────────────────────────────
_workspaces: dict[str, dict[str, Any]] = {}
_agent_outputs: list[dict[str, Any]] = []     # CEO tracks agent work for review
_pending_reviews: list[dict[str, Any]] = []   # Outputs awaiting CEO review
_completed_reviews: list[dict[str, Any]] = [] # CEO review verdicts
_error_logs: list[dict[str, Any]] = []        # Error routing history

# Live agent activity on the floor (drives the CEO Control Room UI).
# Keyed by (workspace_id, agent_type) → {status, task, updated_at}
_agent_activity: dict[tuple[str, str], dict[str, Any]] = {}

# Append-only transcript of agent activity (drives the Munder-Difflin
# terminal-style log in the CEO Control Room). Single-agent era: only the
# CEO "agent" writes here, but the schema is generic.
_agent_activity_log: list[dict[str, Any]] = []

MAX_ACTIVITY_LOG = 2000


def append_agent_activity_log(workspace_id: str, agent_type: str, kind: str, text: str) -> None:
    """Append a line to the agent activity transcript.

    In the single-agent (CEO Michael) design this is the audit trail the
    Control Room renders. Safe to call from any route; never raises.
    """
    _agent_activity_log.append({
        "workspace_id": workspace_id,
        "agent_type": agent_type,
        "kind": kind,
        "text": text,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    if len(_agent_activity_log) > MAX_ACTIVITY_LOG:
        del _agent_activity_log[: len(_agent_activity_log) - MAX_ACTIVITY_LOG]


def get_agent_activity_log(workspace_id: str, agent_type: str, limit: int = 80) -> list[dict[str, Any]]:
    """Return the transcript for one agent (newest first), bounded by limit."""
    out: list[dict[str, Any]] = []
    for rec in reversed(_agent_activity_log):
        if rec["workspace_id"] == workspace_id and rec["agent_type"] == agent_type:
            out.append(rec)
            if len(out) >= limit:
                break
    return out

# Default agent types every workspace gets
DEFAULT_AGENTS = ["sba", "seo", "content", "website", "ads", "social", "analytics", "analyzing", "memory"]


def update_agent_activity(workspace_id: str, agent_type: str, status: str, task: str = "") -> None:
    """Mark an agent's live status on the floor (working/thinking/idle + what it's doing)."""
    _agent_activity[(workspace_id, agent_type)] = {
        "workspace_id": workspace_id,
        "agent_type": agent_type,
        "status": status,            # idle | working | thinking
        "task": task,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_floor_activity(workspace_id: str | None = None) -> list[dict[str, Any]]:
    """Return live floor activity for a workspace (or all workspaces if None)."""
    out: list[dict[str, Any]] = []
    for (ws, atype), rec in _agent_activity.items():
        if workspace_id is None or ws == workspace_id:
            out.append(rec)
    return out


def _build_knowledge_context(knowledge: dict) -> str:
    """Build a human-readable knowledge context string for Content Agent.
    
    Injected into Content Agent's message so it benefits from cross-project
    learnings when starting work on a new workspace.
    """
    parts = ["\n## AGENCY CROSS-PROJECT KNOWLEDGE (from previous projects)"]
    
    patterns = knowledge.get("prompt_patterns", [])
    if patterns:
        parts.append("\n### Prompt Patterns That Work")
        for p in patterns[:5]:
            parts.append(
                f"- [{p.get('visual_type', '?')}/{p.get('platform', '?')}] "
                f"\"{p.get('prompt_template', '')[:120]}\" "
                f"(used {p.get('success_count', 0)} times)"
            )
    
    insights = knowledge.get("brand_insights", [])
    if insights:
        parts.append("\n### Brand/Visual Insights")
        for i in insights[:5]:
            parts.append(f"- {i.get('type', '?')}: {i.get('insight', '')}")
    
    tips = knowledge.get("platform_tips", [])
    if tips:
        parts.append("\n### Platform Tips")
        for t in tips[:3]:
            parts.append(
                f"- {t.get('total_jobs', 0)} jobs done, "
                f"avg {t.get('avg_gpu_minutes', 0):.1f} min GPU per job"
            )
    
    if len(parts) == 1:
        return ""  # No knowledge to inject
    
    parts.append("\nUse these learnings when enhancing briefs and creating prompts.")
    return "\n".join(parts)


# ── Workspace CRUD ───────────────────────────────────────────────────────────

def _sync_ws_to_db(record: dict[str, Any]) -> None:
    """Write workspace record to SQLite (fire-and-forget)."""
    async def _write():
        try:
            db = await get_workspace_db()
            created_at = record["created_at"]
            if hasattr(created_at, "isoformat"):
                created_at = created_at.isoformat()
            ctx = record.get("client_context")
            ctx_json = _json.dumps(ctx) if ctx else "{}"
            agents = record.get("agents", [])
            agents_json = _json.dumps(agents) if isinstance(agents, list) else agents
            await db.execute(
                "INSERT OR REPLACE INTO workspaces "
                "(id, name, client_name, description, agents, client_context, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["name"], record.get("client_name", record["name"]),
                 record.get("description", ""), agents_json, ctx_json, str(created_at)),
            )
            await db.commit()
        except Exception as e:
            logger.debug("SQLite write failed: %s", e)
    _fire_and_forget(_write())
    _mirror_to_pb("workspaces", record)


_PB_ENSURED: set[str] = set()

# PocketBase collection schemas (all values stored as text/json-strings).
_PB_SCHEMAS: dict[str, dict[str, str]] = {
    "workspaces": {
        "record_id": "text", "name": "text", "client_name": "text",
        "description": "text", "agents": "text", "client_context": "text",
        "created_at": "text",
    },
    "agent_outputs": {
        "record_id": "text", "workspace_id": "text", "agent_type": "text",
        "task": "text", "output": "text", "output_preview": "text",
        "timestamp": "text", "reviewed": "text",
    },
}


def _mirror_to_pb(collection: str, record: dict) -> None:
    """Mirror a key record to PocketBase AND a plain JSON file under data/store.

    PocketBase = durable source of truth; files = boss-readable backup that
    works even with POCKETBASE_URL unset. Both best-effort, never fatal.
    """
    import json
    try:
        from admin.file_store import save_record as _fs_save

        _fs_save(collection, str(record.get("id", "")), record)
    except Exception as exc:  # noqa: BLE001
        logger.debug("file mirror (%s) failed (non-fatal): %s", collection, exc)
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        if collection in _PB_SCHEMAS and collection not in _PB_ENSURED:
            pb.ensure_collection(collection, _PB_SCHEMAS[collection])
            _PB_ENSURED.add(collection)
        safe = json.loads(json.dumps(
            record, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)))
        payload = {k: v for k, v in safe.items() if k != "id"}
        # Nested dicts/lists must become JSON strings for PB text fields.
        for k, v in list(payload.items()):
            if isinstance(v, (dict, list)):
                payload[k] = json.dumps(v)
        pb.upsert_by_key(collection, "record_id", payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase mirror (%s) failed (non-fatal): %s", collection, exc)


def _merge_files_into_cache() -> int:
    """Boot fallback: load data/store JSON backups into the local cache.

    Runs after the PocketBase pull, so PB rows win and files only fill gaps
    (e.g. PB was unreachable). Returns how many records were restored.
    """
    restored = 0
    try:
        from admin.file_store import load_all as fs_load_all

        for rec in fs_load_all("workspaces"):
            rid = str(rec.get("id") or "")
            if rid and rid not in _workspaces:
                rec.setdefault("agents", list(DEFAULT_AGENTS))
                rec.setdefault("client_context", None)
                rec.setdefault("created_at", datetime.now(timezone.utc))
                _workspaces[rid] = rec
                _sync_ws_to_db(rec)
                restored += 1
        seen = {o.get("id") for o in _agent_outputs}
        for rec in fs_load_all("agent_outputs"):
            rid = str(rec.get("id") or "")
            if rid and rid not in seen:
                _agent_outputs.append(rec)
                if not rec.get("reviewed"):
                    _pending_reviews.append(rec)
                restored += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("file-store merge failed (non-fatal): %s", exc)
    return restored


async def seed_from_pocketbase() -> None:
    """Boot-time pull: PocketBase -> local memory + workspace SQLite.

    Makes PocketBase the durable source of truth while keeping all existing
    read paths untouched (they keep reading the fast local cache). Local
    records win only when PB has nothing; PB rows are merged by `record_id`.
    Falls back to data/store JSON files when PB is unset/unreachable.
    Fully best-effort: any failure just keeps current local behaviour.
    """
    try:
        from admin.pocketbase_client import get_pb_client
    except Exception:  # noqa: BLE001
        _merge_files_into_cache()
        return
    pb = get_pb_client()
    if not pb or not pb.is_configured():
        _merge_files_into_cache()
        return

    # ── workspaces ──────────────────────────────────────────────────────
    pulled = 0
    try:
        db = await get_workspace_db()
        for row in pb.pull_all("workspaces"):
            rid = str(row.get("record_id") or "")
            if not rid:
                continue
            def _loads(v: Any) -> Any:
                try:
                    return json.loads(v) if isinstance(v, str) else v
                except (ValueError, TypeError):
                    return v
            record = {
                "id": rid,
                "name": row.get("name") or rid,
                "client_name": row.get("client_name") or row.get("name") or rid,
                "description": row.get("description") or "",
                "created_at": row.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "agents": _loads(row.get("agents")) or list(DEFAULT_AGENTS),
                "client_context": _loads(row.get("client_context")),
            }
            existing = _workspaces.get(rid)
            if existing and existing != record:
                continue  # local is newer/equal — don't clobber live state
            _workspaces[rid] = record
            ctx_json = json.dumps(record["client_context"]) if record["client_context"] else "{}"
            agents_json = json.dumps(record["agents"]) if isinstance(record["agents"], list) else str(record["agents"])
            await db.execute(
                "INSERT OR REPLACE INTO workspaces "
                "(id, name, client_name, description, agents, client_context, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, record["name"], record["client_name"],
                 record["description"], agents_json, ctx_json,
                 str(record["created_at"])),
            )
            pulled += 1
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("PocketBase seed (workspaces) failed (non-fatal): %s", exc)

    # ── agent outputs ───────────────────────────────────────────────────
    try:
        seen = {o.get("id") for o in _agent_outputs}
        for row in pb.pull_all("agent_outputs"):
            rid = str(row.get("record_id") or "")
            if not rid or rid in seen:
                continue
            rec = {
                "id": rid,
                "workspace_id": row.get("workspace_id") or "",
                "agent_type": row.get("agent_type") or "",
                "task": row.get("task") or "",
                "output": row.get("output") or "",
                "output_preview": row.get("output_preview") or "",
                "timestamp": row.get("timestamp") or "",
                "reviewed": str(row.get("reviewed", "")).lower() in ("true", "1"),
            }
            _agent_outputs.append(rec)
            if not rec["reviewed"]:
                _pending_reviews.append(rec)
            pulled += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("PocketBase seed (agent_outputs) failed (non-fatal): %s", exc)

    if pulled:
        logger.info("PocketBase seed complete: %d record(s) restored from PB.", pulled)

    # Files fill any gaps left by PocketBase (belt-and-suspenders).
    _file_restored = _merge_files_into_cache()
    if _file_restored:
        logger.info("File-store restored %d additional record(s).", _file_restored)


def create_workspace(payload: WorkspaceCreate) -> WorkspaceOut:
    wid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    ctx_dict = payload.client_context.model_dump() if payload.client_context else None
    record: dict[str, Any] = {
        "id": wid,
        "name": payload.name,
        "client_name": payload.client_name or payload.name,
        "description": payload.description or "",
        "created_at": now,
        "agents": list(DEFAULT_AGENTS),
        "client_context": ctx_dict,
    }
    _workspaces[wid] = record
    _sync_ws_to_db(record)

    # Create per-workspace Content Agent memory
    try:
        from admin.workspace.content_store import get_content_store
        store = get_content_store()
        client_ctx = payload.client_context.model_dump() if payload.client_context else {}
        store.get_or_create(
            workspace_id=wid,
            workspace_name=payload.name,
            client_name=payload.client_name or payload.name,
            industry=client_ctx.get("industry", ""),
        )
        logger.info("Created Content Agent memory for workspace '%s'", payload.name)
    except Exception as e:
        logger.warning("Failed to create Content Agent memory: %s", e)

    return WorkspaceOut(**record)


def get_workspace(wid: str) -> WorkspaceOut | None:
    record = _workspaces.get(wid)
    return WorkspaceOut(**record) if record else None


def list_workspaces() -> list[WorkspaceOut]:
    return [WorkspaceOut(**r) for r in _workspaces.values()]


# ── Seeded workspaces (match Supabase ws_<slug> schemas) ─────────────────────

def seed_workspace(
    wid: str,
    name: str,
    client_name: str = "",
    description: str = "",
    agents: list[str] | None = None,
    client_context: dict | None = None,
) -> WorkspaceOut:
    """Register a workspace under an explicit id (e.g. `ws_agency`).

    Used at startup to bind workspaces to pre-provisioned Supabase schemas
    (ws_<slug>), so the Website Agent can write to the right schema. The
    workspace is skipped if it already exists so seeded records don't
    clobber real client data.
    """
    if wid in _workspaces:
        return WorkspaceOut(**_workspaces[wid])
    now = datetime.now(timezone.utc)
    record: dict[str, Any] = {
        "id": wid,
        "name": name,
        "client_name": client_name or name,
        "description": description,
        "created_at": now,
        "agents": list(agents) if agents else list(DEFAULT_AGENTS),
        "client_context": client_context,
    }
    _workspaces[wid] = record
    _sync_ws_to_db(record)
    logger.info("Seeded workspace '%s' (%s)", wid, name)
    return WorkspaceOut(**record)


def delete_workspace(wid: str) -> bool:
    return _workspaces.pop(wid, None) is not None


# ── Agent Output Tracking ────────────────────────────────────────────────────

def store_agent_output(
    workspace_id: str,
    agent_type: str,
    task: str,
    output: str,
) -> dict[str, Any]:
    """Store agent output for CEO review (Q20)."""
    record = {
        "id": f"out_{uuid.uuid4().hex[:8]}",
        "workspace_id": workspace_id,
        "agent_type": agent_type,
        "task": task,
        "output": output,
        "output_preview": output[:300],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reviewed": False,
    }
    _agent_outputs.append(record)
    _pending_reviews.append(record)
    _mirror_to_pb("agent_outputs", record)

    # Fire-and-forget SQLite write
    async def _write():
        try:
            db = await get_workspace_db()
            await db.execute(
                "INSERT OR REPLACE INTO agent_outputs "
                "(id, workspace_id, agent_type, task, output, output_preview, timestamp, reviewed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["workspace_id"], record["agent_type"],
                 record["task"], record["output"], record["output_preview"],
                 record["timestamp"], 0),
            )
            await db.commit()
        except Exception as e:
            logger.debug("SQLite agent_output write failed: %s", e)
    _fire_and_forget(_write())
    return record


def list_pending_reviews() -> list[dict[str, Any]]:
    """List agent outputs pending CEO review."""
    return [r for r in _pending_reviews if not r.get("reviewed")]


# ── Review Storage ───────────────────────────────────────────────────────────

def store_review(
    workspace_id: str,
    agent_type: str,
    output_id: str,
    verdict: str,
    feedback: str = "",
) -> dict[str, Any]:
    """Store CEO review verdict (Q20)."""
    record = {
        "id": f"rev_{uuid.uuid4().hex[:8]}",
        "workspace_id": workspace_id,
        "agent_type": agent_type,
        "output_id": output_id,
        "verdict": verdict,
        "feedback": feedback,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _completed_reviews.append(record)

    # Mark output as reviewed
    for r in _pending_reviews:
        if r.get("id") == output_id:
            r["reviewed"] = True
            break

    # Fire-and-forget SQLite write
    async def _write():
        try:
            db = await get_workspace_db()
            await db.execute(
                "INSERT OR REPLACE INTO reviews "
                "(id, workspace_id, agent_type, output_id, verdict, feedback, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["workspace_id"], record["agent_type"],
                 record["output_id"], record["verdict"], record["feedback"],
                 record["timestamp"]),
            )
            # Also update agent_outputs reviewed flag
            await db.execute(
                "UPDATE agent_outputs SET reviewed=1 WHERE id=?",
                (output_id,),
            )
            await db.commit()
        except Exception as e:
            logger.debug("SQLite review write failed: %s", e)
    _fire_and_forget(_write())

    return record


def list_reviews(workspace_id: str | None = None) -> list[dict[str, Any]]:
    """List completed reviews."""
    reviews = _completed_reviews
    if workspace_id:
        reviews = [r for r in reviews if r["workspace_id"] == workspace_id]
    return reviews


# ── Error Log ────────────────────────────────────────────────────────────────

def store_error(
    workspace_id: str,
    error_type: str,
    severity: str,
    description: str,
    routed_to: str = "",
) -> dict[str, Any]:
    """Log error routing (Q21)."""
    record = {
        "id": f"err_{uuid.uuid4().hex[:8]}",
        "workspace_id": workspace_id,
        "error_type": error_type,
        "severity": severity,
        "description": description,
        "routed_to": routed_to,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
    }
    _error_logs.append(record)

    # Fire-and-forget SQLite write
    async def _write():
        try:
            db = await get_workspace_db()
            await db.execute(
                "INSERT OR REPLACE INTO error_logs "
                "(id, workspace_id, error_type, severity, description, routed_to, timestamp, resolved) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["workspace_id"], record["error_type"],
                 record["severity"], record["description"], record.get("routed_to", ""),
                 record["timestamp"], 0),
            )
            await db.commit()
        except Exception as e:
            logger.debug("SQLite error_log write failed: %s", e)
    _fire_and_forget(_write())

    return record


def list_errors(workspace_id: str | None = None, unresolved_only: bool = False) -> list[dict[str, Any]]:
    """List error logs."""
    errors = _error_logs
    if workspace_id:
        errors = [e for e in errors if e["workspace_id"] == workspace_id]
    if unresolved_only:
        errors = [e for e in errors if not e.get("resolved")]
    return errors


# ── Agent Routing ────────────────────────────────────────────────────────────

async def _call_with_retry(agent, message: str, max_retries: int = 2) -> str:
    """Call agent.chat with async retry + exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response, _ = await agent.chat(message)
            return response
        except TimeoutError as e:
            last_error = e
            logger.warning("Agent timeout (attempt %d/%d)", attempt + 1, max_retries + 1)
        except Exception as e:
            last_error = e
            logger.warning("Agent error (attempt %d/%d): %s", attempt + 1, max_retries + 1, e)

        if attempt < max_retries:
            wait = 2 ** attempt  # 1s, 2s
            await asyncio.sleep(wait)

    raise last_error or RuntimeError("Agent call failed")


async def route_to_agent(
    workspace_id: str,
    agent_type: str,
    message: str,
) -> str:
    """Route a message to an agent with expert-mode wrapping.

    Expert mode (AGENCY_EXPERT_MODE=0 to disable) makes the agent work
    like a real domain human: it first gets its account brief (client
    facts, own memories, recent deliverables), then a senior-reviewer
    pass polices quality before anything reaches the CEO.
    """
    if os.getenv("AGENCY_EXPERT_MODE", "1") != "0":
        try:
            from admin.workspace.agents.expert_mode import build_brief

            brief = await build_brief(workspace_id, agent_type)
            if brief:
                message = (
                    f"{message}\n\n[EXPERT BRIEF - your account notes]\n{brief}"
                )
        except Exception:  # noqa: BLE001
            logger.debug("expert brief unavailable", exc_info=True)
        draft = await _route_to_agent_raw(workspace_id, agent_type, message)
        try:
            from admin.workspace.agents.expert_mode import review

            return await review(agent_type, message, draft)
        except Exception:  # noqa: BLE001
            logger.debug("expert review skipped", exc_info=True)
            return draft
    return await _route_to_agent_raw(workspace_id, agent_type, message)


async def _route_to_agent_raw(
    workspace_id: str,
    agent_type: str,
    message: str,
) -> str:
    """Original router (sba / domain LangGraph agents / generic LLM)."""
    from admin.runtime import get_agent_runtime

    ws = get_workspace(workspace_id)
    if not ws:
        return f"Workspace '{workspace_id}' not found."
    if agent_type not in ws.agents:
        return f"Agent '{agent_type}' not in workspace. Available: {ws.agents}"

    # SBA has its own LangGraph agent class with Chrome + lead gen tools
    if agent_type == "sba":
        from admin.workspace.agents.sba import SBAAgent
        # Inject the per-agent real-tool runtime (E2B sandbox + Composio +
        # spend-policy) so the flagship SBA graph self-executes with real tools.
        from admin.runtime import get_agent_runtime
        runtime = get_agent_runtime("sba", workspace_id)
        update_agent_activity(workspace_id, "sba", "working", f"Task from CEO/owner: {message[:80]}")
        agent = SBAAgent(
            workspace_name=ws.name,
            client_name=ws.client_name,
            workspace_id=workspace_id,
            runtime=runtime,
        )
        try:
            return await _call_with_retry(agent, message)
        finally:
            update_agent_activity(workspace_id, "sba", "idle")

    # Domain-specific workspace agents (LangGraph-powered)
    _domain_agents = {
        "seo": ("admin.workspace.agents.seo", "SEOAgent"),
        "ads": ("admin.workspace.agents.ads", "AdsAgent"),
        "website": ("admin.workspace.agents.website", "WebsiteAgent"),
        "social": ("admin.workspace.agents.social", "SocialAgent"),
        "content": ("admin.workspace.agents.content", "ContentAgent"),
        "analytics": ("admin.workspace.agents.analytics", "AnalyticsAgent"),
        "analyzing": ("admin.workspace.agents.analyzing", "AnalyzingAgent"),
        "memory": ("admin.workspace.agents.memory", "MemoryAgent"),
    }

    if agent_type in _domain_agents:
        module_path, class_name = _domain_agents[agent_type]
        try:
            import importlib
            module = importlib.import_module(module_path)
            agent_class = getattr(module, class_name)
            
            # Build agent kwargs
            agent_kwargs = {
                "workspace_name": ws.name,
                "client_name": ws.client_name,
            }
            if hasattr(ws, 'client_context') and ws.client_context:
                agent_kwargs["client_context"] = ws.client_context

            # Inject the per-agent real-tool runtime (E2B + Composio + policy)
            # so every workspace agent self-executes with real tools.
            agent_kwargs["runtime"] = get_agent_runtime(agent_type, workspace_id)

            # For Content Agent, inject workspace_id for queue/memory
            if agent_type == "content":
                agent_kwargs["workspace_id"] = workspace_id
            
            # For Content Agent, inject cross-project knowledge + workspace memory
            if agent_type == "content":
                try:
                    from admin.agency.content_agent import get_agency_content_agent
                    from admin.workspace.content_store import get_content_store
                    
                    agency_agent = get_agency_content_agent()
                    store = get_content_store()
                    client_ctx = ws.client_context or {}
                    
                    # Get agency knowledge (from ALL workspaces)
                    knowledge = agency_agent.get_knowledge_for_workspace(
                        industry=client_ctx.get("industry", ""),
                    )
                    
                    # Get workspace-specific memory
                    ws_memory = store.get_memory_summary(workspace_id)
                    
                    # Build combined context
                    knowledge_parts = []
                    
                    if knowledge.get("prompt_patterns") or knowledge.get("brand_insights"):
                        knowledge_parts.append(_build_knowledge_context(knowledge))
                    
                    if ws_memory:
                        knowledge_parts.append(ws_memory)
                    
                    # Also add mistakes to avoid from this workspace
                    mem = store.get_or_create(
                        workspace_id=workspace_id,
                        workspace_name=ws.name,
                        client_name=ws.client_name,
                        industry=client_ctx.get("industry", ""),
                    )
                    if mem.mistakes_to_avoid:
                        knowledge_parts.append(
                            "\n## MISTAKES TO AVOID (from this workspace's history)\n"
                            + "\n".join(f"- {m}" for m in mem.mistakes_to_avoid[:5])
                        )
                    
                    if knowledge_parts:
                        knowledge_text = "\n\n".join(knowledge_parts)
                        agent_kwargs["_agency_knowledge"] = knowledge_text
                        logger.info(
                            "Injecting knowledge into Content Agent for workspace '%s': "
                            "agency + workspace memory + %d mistakes to avoid",
                            ws.name, len(mem.mistakes_to_avoid),
                        )
                except Exception as e:
                    logger.warning("Failed to load agency/workspace knowledge: %s", e)

            update_agent_activity(workspace_id, agent_type, "working", f"Task from CEO/owner: {message[:80]}")
            agent = agent_class(**agent_kwargs)
            try:
                return await _call_with_retry(agent, message)
            finally:
                update_agent_activity(workspace_id, agent_type, "idle")
        except TypeError:
            # Fallback: agents that don't accept client_context yet
            try:
                agent = agent_class(workspace_name=ws.name, client_name=ws.client_name)
                return await _call_with_retry(agent, message)
            except Exception as exc2:
                logger.warning("Domain agent %s failed: %s", agent_type, exc2)
        except Exception as exc:
            logger.warning("Domain agent %s failed, falling back to generic: %s", agent_type, exc)

    # Fallback: generic LLM agent (analytics, memory, unknown types)
    from admin.config import settings
    import openai

    client = openai.AsyncOpenAI(
        api_key=settings.WORKSPACE_API_KEY or None,
        base_url=settings.WORKSPACE_API_BASE or None,
    )

    system_prompt = (
        f"You are the {agent_type.upper()} agent for workspace "
        f"'{ws.name}' (client: {ws.client_name}). "
        f"You are a specialist in your domain. Respond helpfully and concisely."
    )

    try:
        resp = await client.chat.completions.create(
            model=settings.WORKSPACE_AGENT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        )
        return resp.choices[0].message.content or "No response generated."
    except Exception as exc:
        return f"{agent_type.upper()} agent LLM call failed: {exc}"


# ── Load from SQLite on startup ───────────────────────────────────────────────

async def load_all_from_db() -> None:
    """Load all persisted data from SQLite into in-memory stores.

    Called once at startup after init_persistence().
    """
    try:
        db = await get_workspace_db()

        # Load workspaces
        cursor = await db.execute("SELECT * FROM workspaces")
        rows = await cursor.fetchall()
        for row in rows:
            d = dict(row)
            d["created_at"] = datetime.fromisoformat(d["created_at"])
            try:
                d["agents"] = _json.loads(d.get("agents", "[]"))
            except (TypeError, _json.JSONDecodeError):
                d["agents"] = list(DEFAULT_AGENTS)
            try:
                ctx_raw = d.get("client_context", "{}")
                if isinstance(ctx_raw, str):
                    ctx_raw = _json.loads(ctx_raw)
                d["client_context"] = ctx_raw
            except (TypeError, _json.JSONDecodeError):
                d["client_context"] = None
            _workspaces[d["id"]] = d

        # Load agent outputs
        cursor = await db.execute("SELECT * FROM agent_outputs ORDER BY timestamp")
        rows = await cursor.fetchall()
        for row in rows:
            d = dict(row)
            d["reviewed"] = bool(d.get("reviewed", 0))
            _agent_outputs.append(d)
            if not d["reviewed"]:
                _pending_reviews.append(d)

        # Load reviews
        cursor = await db.execute("SELECT * FROM reviews ORDER BY timestamp")
        rows = await cursor.fetchall()
        for row in rows:
            _completed_reviews.append(dict(row))

        # Load error logs
        cursor = await db.execute("SELECT * FROM error_logs ORDER BY timestamp")
        rows = await cursor.fetchall()
        for row in rows:
            d = dict(row)
            d["resolved"] = bool(d.get("resolved", 0))
            _error_logs.append(d)

        logger.info(
            "Loaded from DB: %d workspaces, %d outputs, %d reviews, %d errors",
            len(_workspaces), len(_agent_outputs),
            len(_completed_reviews), len(_error_logs),
        )
    except Exception as e:
        logger.warning("Failed to load data from SQLite: %s", e)
