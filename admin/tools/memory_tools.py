"""Memory Agent — Real tools for workspace-wide key/value memory.

5 tools:
Persistence (4): save_memory, get_memory, list_memory, delete_memory
Recall (1): recall_others — read another agent's memory in the same workspace

All tools delegate to admin.agency.agent_persistence, which already provides
per-workspace, per-agent durable storage (Supabase when configured, in-memory
fallback otherwise). The MemoryAgent is just the conversational wrapper around
this existing infra — no new storage layer is introduced.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

# Agents that may hold useful memory in a workspace. Used by recall_others.
_KNOWN_AGENTS = ["sba", "seo", "content", "website", "ads", "social", "analytics"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Local fallback store ───────────────────────────────────────────────────────
# Used only when the canonical admin.agency.agent_persistence store is unavailable
# (e.g. Supabase not configured). Keeps the Memory Agent functional everywhere.
# Shape: {workspace: {agent: {key: value}}}.
_LOCAL: dict[str, dict[str, dict[str, Any]]] = {}


def _with_fallback(fn_name: str, *args, **kwargs):
    """Call a real admin.agency.agent_persistence function.

    Returns (result, store). On any failure (not configured, network down,
    exception), returns (None, "local") so the caller can use _LOCAL.
    """
    try:
        from admin.agency.agent_persistence import get_config

        if not getattr(get_config(), "url", None):
            return None, "local"
        import importlib

        mod = importlib.import_module("admin.agency.agent_persistence")
        fn = getattr(mod, fn_name)
        return fn(*args, **kwargs), "supabase"
    except Exception:
        return None, "local"


# ── Persistence Tools ─────────────────────────────────────────────────────────


def save_memory(workspace: str = "Default", key: str = "", value: Any = None) -> dict[str, Any]:
    """Store a key/value memory for this workspace (learnings, state, notes)."""
    if not key:
        return {"status": "error", "error": "key is required"}
    res, store = _with_fallback("save_memory", workspace, "memory", key, value)
    if res is not None:
        return {"status": "saved", "key": key, "value": value, "updated_at": _now(), "store": store}
    _LOCAL.setdefault(workspace, {}).setdefault("memory", {})[key] = value
    return {"status": "saved", "key": key, "value": value, "updated_at": _now(), "store": "local"}


def get_memory(workspace: str = "Default", key: str = "") -> dict[str, Any]:
    """Retrieve one memory value by key for this workspace."""
    if not key:
        return {"status": "error", "error": "key is required"}
    res, store = _with_fallback("get_memory", workspace, "memory", key)
    if res is not None:
        return {"status": "found", "key": key, "value": res, "store": store}
    val = _LOCAL.get(workspace, {}).get("memory", {}).get(key)
    if val is None:
        return {"status": "not_found", "key": key}
    return {"status": "found", "key": key, "value": val, "store": "local"}


def list_memory(workspace: str = "Default") -> dict[str, Any]:
    """List all keys the Memory Agent holds for this workspace."""
    res, store = _with_fallback("list_memory", workspace, "memory")
    if res is not None:
        rows = res or []
        return {
            "status": "ok",
            "workspace": workspace,
            "count": len(rows),
            "keys": [r.get("memory_key") for r in rows],
            "items": {r.get("memory_key"): r.get("value") for r in rows},
            "store": store,
        }
    items = _LOCAL.get(workspace, {}).get("memory", {})
    return {
        "status": "ok",
        "workspace": workspace,
        "count": len(items),
        "keys": list(items.keys()),
        "items": items,
        "store": "local",
    }


def delete_memory(workspace: str = "Default", key: str = "") -> dict[str, Any]:
    """Forget one memory by key (or all memory if key omitted)."""
    res, store = _with_fallback("delete_memory", workspace, "memory", key or None)
    if res is not None:
        if not res:
            return {"status": "error", "error": "delete failed", "key": key or None}
        return {"status": "deleted", "key": key or None, "store": store}
    mem = _LOCAL.setdefault(workspace, {}).setdefault("memory", {})
    if key:
        removed = mem.pop(key, None) is not None
    else:
        removed = bool(mem)
        mem.clear()
    if not removed:
        return {"status": "error", "error": "delete failed", "key": key or None}
    return {"status": "deleted", "key": key or None, "store": "local"}


def recall_others(workspace: str = "Default", agent: str = "") -> dict[str, Any]:
    """Read memory held by another agent in this workspace (sba, seo, ...)."""
    targets = [agent] if agent else _KNOWN_AGENTS
    out: dict[str, Any] = {}
    res, store = _with_fallback("list_memory", workspace, targets[0] if agent else "seo")
    if res is not None:
        for a in targets:
            try:
                rows = _with_fallback("list_memory", workspace, a)[0] or []
                if rows:
                    out[a] = {r.get("memory_key"): r.get("value") for r in rows}
            except Exception:  # noqa: BLE001
                continue
        return {"status": "ok", "workspace": workspace, "memories": out, "store": store}

    for a in targets:
        try:
            items = _LOCAL.get(workspace, {}).get(a, {})
            if items:
                out[a] = items
        except Exception:  # noqa: BLE001
            continue
    return {"status": "ok", "workspace": workspace, "memories": out, "store": "local"}


# ── Tool schema + dispatcher ──────────────────────────────────────────────────

MEMORY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save a memory (fact, learning, client preference, state) "
            "for this workspace under a key. Use this when the user asks the agent "
            "to 'remember' something.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Workspace name"},
                    "key": {"type": "string", "description": "Memory key (unique per workspace)"},
                    "value": {"description": "The value to remember (string, number, or object)"},
                },
                "required": ["workspace", "key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memory",
            "description": "Retrieve one memory value by key for this workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "key": {"type": "string"},
                },
                "required": ["workspace", "key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memory",
            "description": "List every key the Memory Agent currently holds for this workspace.",
            "parameters": {
                "type": "object",
                "properties": {"workspace": {"type": "string"}},
                "required": ["workspace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "Forget one memory by key, or all memory for the workspace if "
            "key is omitted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "key": {"type": "string", "description": "Optional. Omit to delete all."},
                },
                "required": ["workspace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_others",
            "description": "Read memory held by another agent in this workspace "
            "(sba, seo, content, website, ads, social, analytics).",
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string"},
                    "agent": {"type": "string", "description": "Optional specific agent name."},
                },
                "required": ["workspace"],
            },
        },
    },
]


def execute_memory_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a memory tool call by name. Returns a JSON-serializable dict."""
    handlers = {
        "save_memory": save_memory,
        "get_memory": get_memory,
        "list_memory": list_memory,
        "delete_memory": delete_memory,
        "recall_others": recall_others,
    }
    fn = handlers.get(name)
    if fn is None:
        return {"status": "error", "error": f"unknown memory tool: {name}"}
    try:
        return fn(**(args or {}))
    except Exception as exc:  # noqa: BLE001 — never crash the agent loop
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
