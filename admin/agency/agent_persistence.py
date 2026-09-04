"""Per-agent persistence for every workspace — memory, chat history, data,
and LangGraph checkpoints, all stored in the workspace's own schema.

Each workspace owns a Postgres schema ``ws_<slug>``. Inside it, every agent
(sba, website, seo, social, content, ads, analytics, ...) has generic
tables created by ``public.provision_workspace``:

  agent_memory           key/value memory per agent (learnings, state, notes)
  agent_messages         per-agent chat history
  agent_data             arbitrary JSON documents per agent
  agent_checkpoints      LangGraph thread checkpoints (the agent's memory)
  agent_checkpoint_writes pending writes per checkpoint task

This module provides:
  - CRUD helpers for memory / messages / data (workspace + agent scoped)
  - ``SupabaseSaver`` — a LangGraph ``BaseCheckpointSaver``-compatible
    checkpointer that persists thread state into ``agent_checkpoints``, so
    agents keep their memory across restarts, per workspace.
  - ``get_checkpointer(workspace, agent)`` — returns ``SupabaseSaver`` when
    Supabase is configured, else falls back to in-memory ``MemorySaver``.

Workspace isolation comes from the schema (PostgREST ``Accept-Profile`` /
``Content-Profile`` headers), exactly like ``website_supabase``.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any, Iterator

from admin.agency.website_supabase import _api, _client_q, get_config
from admin.agency.workspace_provision import schema_for

logger = logging.getLogger(__name__)

# Well-known agents (extend as new agents are added)
AGENTS = ["sba", "website", "seo", "social", "content", "ads", "analytics"]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _agent_q(agent: str) -> str:
    return "agent_name=eq." + urllib.parse.quote(agent)


def _json_safe(value: Any) -> Any:
    """Recursively convert non-JSON values (deque, tuple, set) to JSON-safe ones.

    langgraph >= 1.0 passes pending writes whose values can be ``deque``s
    (e.g. interrupt payloads); PocketBase/JSON storage can't serialize them.
    """
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    try:
        import collections
        if isinstance(value, collections.deque):
            return [_json_safe(v) for v in value]
    except ImportError:  # pragma: no cover - collections always exists
        pass
    return value


# ── Memory (key/value) ────────────────────────────────────────────────────

def save_memory(workspace: str, agent: str, key: str, value: Any) -> dict[str, Any] | None:
    """Upsert one memory value for (workspace, agent)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key_cfg = cfg
    try:
        rows = _api(
            "POST", url, key_cfg, "/rest/v1/agent_memory",
            {
                "agent_name": agent,
                "memory_key": key,
                "value": value,
                "updated_at": _now_iso(),
            },
            on_conflict="agent_name,memory_key",
            profile=schema_for(workspace),
        )
        result = rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: save_memory failed: %s", e)
        return None
    _mirror_save_memory(workspace, agent, key, value)
    return result


# ── PocketBase mirror (best-effort, never fatal) ───────────────────────────

def _mirror_save_memory(workspace: str, agent: str, key: str, value: Any) -> None:
    """Mirror one memory record to PocketBase so the boss can see it.

    Stable key ``record_id`` = ``<workspace>:<agent>:<key>``. The value is
    serialised to a JSON string. Best-effort: failures are logged at debug
    level and never break the main save path.
    """
    try:
        payload = {
            "record_id": f"{workspace}:{agent}:{key}",
            "workspace": workspace,
            "agent": agent,
            "memory_key": key,
            "value": json.dumps(value, default=str),
            "updated_at": _now_iso(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent_memory mirror payload build failed (non-fatal): %s", exc)
        return
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        pb.upsert_by_key("agent_memory", "record_id", payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase mirror (agent_memory) failed (non-fatal): %s", exc)


def _mirror_delete_memory(workspace: str, agent: str, key: str) -> None:
    """Best-effort PocketBase delete mirror for a single memory key."""
    try:
        from admin.pocketbase_client import get_pb_client
        pb = get_pb_client()
        if not pb or not pb.is_configured():
            return
        pb.delete_by_key("agent_memory", "record_id", f"{workspace}:{agent}:{key}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("PocketBase delete mirror (agent_memory) failed (non-fatal): %s", exc)


def get_memory(workspace: str, agent: str, key: str) -> Any:
    cfg = get_config()
    if not cfg:
        return None
    url, key_cfg = cfg
    try:
        rows = _api(
            "GET", url, key_cfg,
            "/rest/v1/agent_memory?select=value&" + _agent_q(agent)
            + "&memory_key=eq." + urllib.parse.quote(key),
            profile=schema_for(workspace),
        )
        return rows[0]["value"] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: get_memory failed: %s", e)
        return None


def list_memory(workspace: str, agent: str) -> list[dict[str, Any]]:
    cfg = get_config()
    if not cfg:
        return []
    url, key_cfg = cfg
    try:
        return _api(
            "GET", url, key_cfg,
            "/rest/v1/agent_memory?select=memory_key,value&" + _agent_q(agent),
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: list_memory failed: %s", e)
        return []


def delete_memory(workspace: str, agent: str, key: str | None = None) -> bool:
    cfg = get_config()
    if not cfg:
        return False
    url, key_cfg = cfg
    q = _agent_q(agent)
    if key:
        q += "&memory_key=eq." + urllib.parse.quote(key)
    try:
        _api("DELETE", url, key_cfg, "/rest/v1/agent_memory?" + q, profile=schema_for(workspace))
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: delete_memory failed: %s", e)
        return False
    if key:
        _mirror_delete_memory(workspace, agent, key)
    return True


# ── Messages (chat history) ───────────────────────────────────────────────

def append_message(
    workspace: str,
    agent: str,
    role: str,
    content: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key_cfg = cfg
    try:
        rows = _api(
            "POST", url, key_cfg, "/rest/v1/agent_messages",
            {"agent_name": agent, "role": role, "content": content, "meta": meta or {}},
            profile=schema_for(workspace),
        )
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: append_message failed: %s", e)
        return None


def get_messages(workspace: str, agent: str, limit: int = 100) -> list[dict[str, Any]]:
    cfg = get_config()
    if not cfg:
        return []
    url, key_cfg = cfg
    try:
        return _api(
            "GET", url, key_cfg,
            "/rest/v1/agent_messages?select=role,content,meta,created_at&"
            + _agent_q(agent) + "&order=created_at.desc&limit=" + str(limit),
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: get_messages failed: %s", e)
        return []


def clear_messages(workspace: str, agent: str) -> bool:
    cfg = get_config()
    if not cfg:
        return False
    url, key_cfg = cfg
    try:
        _api("DELETE", url, key_cfg, "/rest/v1/agent_messages?" + _agent_q(agent), profile=schema_for(workspace))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: clear_messages failed: %s", e)
        return False


# ── Data (arbitrary JSON documents) ───────────────────────────────────────

def save_data(workspace: str, agent: str, key: str, payload: Any) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key_cfg = cfg
    try:
        rows = _api(
            "POST", url, key_cfg, "/rest/v1/agent_data",
            {"agent_name": agent, "data_key": key, "payload": payload, "updated_at": _now_iso()},
            on_conflict="agent_name,data_key",
            profile=schema_for(workspace),
        )
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: save_data failed: %s", e)
        return None


def get_data(workspace: str, agent: str, key: str) -> Any:
    cfg = get_config()
    if not cfg:
        return None
    url, key_cfg = cfg
    try:
        rows = _api(
            "GET", url, key_cfg,
            "/rest/v1/agent_data?select=payload&" + _agent_q(agent)
            + "&data_key=eq." + urllib.parse.quote(key),
            profile=schema_for(workspace),
        )
        return rows[0]["payload"] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: get_data failed: %s", e)
        return None


# ── LangGraph checkpoints (agent memory across restarts) ─────────────────

try:
    from langgraph.checkpoint.base import BaseCheckpointSaver as _BaseCheckpointSaver
except ImportError:  # pragma: no cover - langgraph not installed
    _BaseCheckpointSaver = object  # type: ignore[misc,assignment]


class SupabaseSaver(_BaseCheckpointSaver):
    """LangGraph ``BaseCheckpointSaver``-compatible checkpointer backed by
    the workspace schema's ``agent_checkpoints`` tables.

    Drop-in for ``MemorySaver()``: agents call
    ``compile(checkpointer=SupabaseSaver(workspace_name, "seo"))`` and their
    full thread state persists per workspace in Supabase.
    """

    def __init__(self, workspace_name: str = "Default", agent_name: str = "agent"):
        super().__init__()
        self.workspace = workspace_name
        self.agent = agent_name

    @property
    def available(self) -> bool:
        return get_config() is not None

    # ── storage primitives (pure, no langgraph dependency) ──

    def _storage_put(
        self,
        thread_id: str,
        checkpoint_id: str,
        checkpoint: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        parent_checkpoint_id: str = "",
    ) -> bool:
        cfg = get_config()
        if not cfg:
            return False
        url, key_cfg = cfg
        try:
            _api(
                "POST", url, key_cfg, "/rest/v1/agent_checkpoints",
                {
                    "agent_name": self.agent,
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint": checkpoint,
                    "metadata": metadata or {},
                    "parent_checkpoint_id": parent_checkpoint_id,
                },
                on_conflict="agent_name,thread_id,checkpoint_id",
                profile=schema_for(self.workspace),
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_persistence: storage_put failed: %s", e)
            return False

    def _storage_get(self, thread_id: str, checkpoint_id: str | None = None) -> dict[str, Any] | None:
        cfg = get_config()
        if not cfg:
            return None
        url, key_cfg = cfg
        q = "/rest/v1/agent_checkpoints?select=*&" + _agent_q(self.agent) + "&thread_id=eq." + urllib.parse.quote(thread_id)
        if checkpoint_id:
            q += "&checkpoint_id=eq." + urllib.parse.quote(checkpoint_id)
        q += "&order=created_at.desc&limit=1"
        try:
            rows = _api("GET", url, key_cfg, q, profile=schema_for(self.workspace))
            return rows[0] if rows else None
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_persistence: storage_get failed: %s", e)
            return None

    def _storage_list(self, thread_id: str, limit: int = 10) -> list[dict[str, Any]]:
        cfg = get_config()
        if not cfg:
            return []
        url, key_cfg = cfg
        q = ("/rest/v1/agent_checkpoints?select=*&" + _agent_q(self.agent)
             + "&thread_id=eq." + urllib.parse.quote(thread_id)
             + "&order=created_at.desc&limit=" + str(limit))
        try:
            return _api("GET", url, key_cfg, q, profile=schema_for(self.workspace))
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_persistence: storage_list failed: %s", e)
            return []

    def _storage_put_writes(
        self,
        thread_id: str,
        checkpoint_id: str,
        task_id: str,
        writes: list[Any],
    ) -> bool:
        cfg = get_config()
        if not cfg:
            return False
        url, key_cfg = cfg
        try:
            _api(
                "POST", url, key_cfg, "/rest/v1/agent_checkpoint_writes",
                {
                    "agent_name": self.agent,
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "writes": _json_safe(writes),
                },
                on_conflict="agent_name,thread_id,checkpoint_id,task_id",
                profile=schema_for(self.workspace),
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_persistence: storage_put_writes failed: %s", e)
            return False

    def _storage_get_writes(self, thread_id: str, checkpoint_id: str) -> list[Any]:
        cfg = get_config()
        if not cfg:
            return []
        url, key_cfg = cfg
        q = ("/rest/v1/agent_checkpoint_writes?select=writes&" + _agent_q(self.agent)
             + "&thread_id=eq." + urllib.parse.quote(thread_id)
             + "&checkpoint_id=eq." + urllib.parse.quote(checkpoint_id))
        try:
            rows = _api("GET", url, key_cfg, q, profile=schema_for(self.workspace))
            out: list[Any] = []
            for r in rows:
                w = r.get("writes")
                items = w if isinstance(w, list) else [w]
                for it in items:
                    # langgraph >= 1.0 expects PendingWrite = (task_id, channel, value)
                    if isinstance(it, (list, tuple)) and len(it) == 2:
                        out.append(("", it[0], it[1]))
                    else:
                        out.append(it)
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_persistence: storage_get_writes failed: %s", e)
            return []

    # ── langgraph-facing API (lazy import so tests run without langgraph) ──

    @staticmethod
    def _config_ids(config: dict[str, Any]) -> tuple[str, str | None]:
        cfg = (config or {}).get("configurable", {})
        return cfg.get("thread_id", "thread"), cfg.get("checkpoint_id")

    def get_tuple(self, config: dict[str, Any]) -> Any:
        from langgraph.checkpoint.base import CheckpointTuple

        thread_id, checkpoint_id = self._config_ids(config)
        row = self._storage_get(thread_id, checkpoint_id)
        if not row:
            return None
        parent_cfg = None
        if row.get("parent_checkpoint_id"):
            parent_cfg = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": row["parent_checkpoint_id"],
                }
            }
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": row["checkpoint_id"],
                }
            },
            checkpoint=row["checkpoint"],
            metadata=row.get("metadata") or {},
            parent_config=parent_cfg,
            pending_writes=self._storage_get_writes(thread_id, row["checkpoint_id"]),
        )

    def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        thread_id, _ = self._config_ids(config)

        # langgraph < 1.0 exposed empty_checkpoint_id / uuid_type; newer
        # versions removed them in favor of uuid6 / plain uuids. Import them
        # when available and fall back to a plain uuid otherwise.
        try:
            from langgraph.checkpoint.base import empty_checkpoint_id, uuid_type
            ckpt_id = (metadata or {}).get("checkpoint_id") or str(uuid_type())
        except ImportError:  # langgraph-checkpoint >= 4
            import uuid as _uuid
            empty_checkpoint_id = ""
            ckpt_id = (metadata or {}).get("checkpoint_id") or str(_uuid.uuid4())
        parent = (config.get("configurable", {}) or {}).get("checkpoint_id") or empty_checkpoint_id
        self._storage_put(thread_id, ckpt_id, checkpoint, metadata, parent)
        return {"configurable": {"thread_id": thread_id, "checkpoint_id": ckpt_id}}

    def put_writes(
        self,
        config: dict[str, Any],
        writes: list[Any],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, checkpoint_id = self._config_ids(config)
        self._storage_put_writes(thread_id, checkpoint_id or "", task_id, writes)

    def list(
        self,
        config: dict[str, Any] | None = None,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002 - langgraph v4 signature
        limit: int | None = None,
        before: dict[str, Any] | None = None,
    ) -> Iterator[Any]:
        from langgraph.checkpoint.base import CheckpointTuple

        config = config or {}
        thread_id, _ = self._config_ids(config)
        rows = self._storage_list(thread_id, limit or 10)
        for row in rows:
            parent_cfg = None
            if row.get("parent_checkpoint_id"):
                parent_cfg = {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": row["parent_checkpoint_id"],
                    }
                }
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": row["checkpoint_id"],
                    }
                },
                checkpoint=row["checkpoint"],
                metadata=row.get("metadata") or {},
                parent_config=parent_cfg,
                pending_writes=self._storage_get_writes(thread_id, row["checkpoint_id"]),
            )

    # async variants used by langgraph when awaiting the checkpointer
    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        return self.get_tuple(config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return self.put_writes(config, writes, task_id, task_path)

    async def alist(self, config=None, *, filter=None, limit=None, before=None):  # noqa: A002
        return self.list(config, filter=filter, limit=limit, before=before)


def get_checkpointer(workspace_name: str = "Default", agent_name: str = "agent") -> Any:
    """Return a Supabase-backed checkpointer for (workspace, agent), falling
    back to in-memory MemorySaver when Supabase isn't configured."""
    try:
        saver = SupabaseSaver(workspace_name, agent_name)
        if saver.available:
            return saver
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: SupabaseSaver unavailable, using MemorySaver: %s", e)
    try:
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_persistence: langgraph MemorySaver unavailable: %s", e)
        return SupabaseSaver(workspace_name, agent_name)
