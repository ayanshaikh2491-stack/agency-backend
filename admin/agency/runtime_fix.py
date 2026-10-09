"""CEO runtime doctor — system-level self-diagnosis and self-repair.

Extends the agent-level self_heal loop with ENVIRONMENT-level fixes the CEO
can actually perform at runtime:

  1. system_selfcheck() — proactive full-stack scan:
     critical imports, DB connectivity, LLM reachability, every workspace
     agent ping. Returns a structured health report the CEO can act on
     (and route errors from) without the boss ever being pinged.
  2. fix_module_error() — ModuleNotFoundError self-repair: runtime
     ``pip install <pkg>`` then re-import + retry the failed call. The
     container is ephemeral, so this unblocks the session NOW; the
     requirements.txt fix still belongs to the dev/deploy loop, and the
     diagnosis names the exact line to add.

Everything is best-effort and read-only except the pip install, which is
scoped to the single missing distribution named in the error.
"""
from __future__ import annotations

import importlib
import json
import logging
import re
import subprocess
import sys

logger = logging.getLogger("agency.runtime_fix")

# Modules the backend must be able to import (name -> pip package if different)
_CRITICAL_IMPORTS: dict[str, str] = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "openai": "openai",
    "sqlalchemy": "sqlalchemy",
    "httpx": "httpx",
    "selectolax": "selectolax",
    "bs4": "beautifulsoup4",
    "dns": "dnspython",
    "langgraph": "langgraph",
    "requests": "requests",
}

# admin.* subpackages that must exist for agents to run
_CRITICAL_ADMIN_MODULES = (
    "admin.runtime",
    "admin.tools.sba_tools",
    "admin.tools.sba_lead_sources",
    "admin.tools.osm_lead_source",
    "admin.tools.chrome_tool",
    "admin.agency.self_heal",
    "admin.workspace.manager",
    "admin.agency.ceo",
)


def check_imports() -> list[dict]:
    """Try importing every critical module. Returns [{module, ok, error}]."""
    results: list[dict] = []
    for mod, pkg in _CRITICAL_IMPORTS.items():
        try:
            importlib.import_module(mod)
            results.append({"module": mod, "ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"module": mod, "ok": False, "error": f"{type(exc).__name__}: {exc}", "pip_package": pkg})
    for mod in _CRITICAL_ADMIN_MODULES:
        try:
            importlib.import_module(mod)
            results.append({"module": mod, "ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"module": mod, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return results


def check_db() -> dict:
    """Open the SQLite DB and run a trivial query."""
    try:
        import sqlite3

        # Use the same file the FastAPI app uses by default.
        import os

        db_path = os.getenv("TAGS_DB_PATH", "tags_agency.db")
        if not os.path.exists(db_path):
            for cand in ("./tags_agency.db", "./admin/data/tags_agency.db"):
                if os.path.exists(cand):
                    db_path = cand
                    break
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("SELECT 1")
        conn.close()
        return {"ok": True, "db": db_path}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def check_llm() -> dict:
    """One-token ping through the workspace LLM settings (freeapi router)."""
    try:
        from admin.config import settings

        if not (settings.WORKSPACE_API_BASE and settings.WORKSPACE_API_KEY):
            return {"ok": False, "error": "WORKSPACE_API_BASE/KEY not set"}
        import httpx

        r = httpx.post(
            settings.WORKSPACE_API_BASE.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.WORKSPACE_API_KEY}"},
            json={
                "model": settings.WORKSPACE_AGENT_MODEL or "auto",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 5,
            },
            timeout=45,
        )
        return {"ok": r.status_code == 200, "status": r.status_code, "model": settings.WORKSPACE_AGENT_MODEL}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def check_gateway_keys() -> dict:
    """Report what the freeapi gateway actually holds, against what we meant to seed.

    The gateway is a separate process on its own port with its own database, and
    it is the only component whose state is invisible from outside the
    container. Every agent failing with 503 "no candidate model has a
    configured, usable provider key" points here, and start.sh seeding uses
    curl -f, which discards the HTTP status of a failed seed. So there was no
    way to tell an unseeded key from a seeded-but-unusable one. This reports
    both sides of that comparison and never raises: it is a diagnostic.
    """
    out: dict = {"ok": False}
    try:
        import os

        import httpx

        raw = os.getenv("PROVIDER_KEYS_JSON", "")
        try:
            intended = [str(i.get("platform")) for i in json.loads(raw)] if raw else []
        except Exception:
            intended = []
        out["intended_platforms"] = intended

        # Read the env directly rather than through settings: this is a runtime
        # diagnostic about a dependency that is configured at boot, and settings
        # caches its values at import, so an env change after import is
        # invisible there. The gateway really does listen on the env value.
        base = (os.getenv("WORKSPACE_API_BASE") or "").rstrip("/")
        # The gateway router lives one path up from the openai-compatible base.
        root = base[:-3] if base.endswith("/v1") else base
        out["gateway_root"] = root

        email = os.getenv("FREEAPI_ADMIN_EMAIL", "")
        password = os.getenv("FREEAPI_ADMIN_PASSWORD", "")
        out["admin_configured"] = bool(email and password)
        if not root or not email:
            out["error"] = "no gateway root or no admin credentials to read the key list"
            return out

        token = ""
        for path in ("/api/auth/setup", "/api/auth/login"):
            try:
                r = httpx.post(root + path, json={"email": email, "password": password}, timeout=15)
                body = r.json() if r.content else {}
                token = str(body.get("token") or "")
            except Exception:
                token = ""
            if token:
                break
        out["token_acquired"] = bool(token)
        if not token:
            out["error"] = "gateway did not issue a dashboard token, so its key list is unreadable"
            return out

        r = httpx.get(root + "/api/keys", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        payload = r.json() if r.content else {}
        keys = payload if isinstance(payload, list) else (payload.get("keys") or [])
        out["seeded"] = [
            {"platform": str(k.get("platform")), "label": str(k.get("label") or "")}
            for k in keys
        ]
        out["seeded_platforms"] = sorted({str(k.get("platform")) for k in keys})
        out["missing"] = sorted(set(intended) - set(out["seeded_platforms"]))
        out["ok"] = not out["missing"] and bool(out["seeded_platforms"])
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _probe_workspace_id() -> str:
    """Find the workspace the agency actually runs in.

    The probe used to hardcode "agency", but the real id is "ws_agency", so
    every probe came back Workspace not found and the check reported the same
    failure for all agents while telling you nothing about any of them.
    Resolved from the store instead, preferring the busiest workspace so a
    future rename does not silently break the diagnostic again.
    """
    try:
        from admin.workspace.manager import list_workspaces

        best, best_n = "", -1
        for w in list_workspaces():
            wid = str(w.get("id") or w.get("workspace_id") or "")
            if not wid:
                continue
            n = len(getattr(w, "agents", None) or w.get("agents") or {}) or 0
            if n > best_n:
                best, best_n = wid, n
        if best:
            return best
    except Exception:
        logger.debug("could not list workspaces for the probe", exc_info=True)
    return "ws_agency"


def check_agents() -> list[dict]:
    """Ping each registered workspace agent with a 1-word health probe."""
    from admin.workspace.manager import route_to_agent

    probes: list[dict] = []
    workspace_id = _probe_workspace_id()
    try:
        from admin.runtime import get_workspace

        ws = get_workspace(workspace_id)
        agent_types = list(getattr(ws, "agents", {}).keys()) if ws else []
    except Exception:  # noqa: BLE001
        agent_types = []
    if not agent_types:
        agent_types = ["sba", "seo", "content", "website"]
    import asyncio

    # get_event_loop() raises in a worker thread on Python 3.10 and later
    # because no loop is set there, and this is called from a threadpool
    # precisely so the blocking probes do not stall the event loop. So get a
    # loop without depending on one already being current, and close it after.
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop is closed")
    except (RuntimeError, DeprecationWarning):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def _probe(at: str) -> dict:
        try:
            resp = await asyncio.wait_for(
                route_to_agent(workspace_id=workspace_id, agent_type=at, message="Health probe: reply with the single word OK"),
                timeout=90,
            )
            bad = any(
                kw in (resp or "").lower()
                for kw in ("modulenotfound", "error code", "traceback", "failed:", "not found")
            )
            return {"agent": at, "ok": not bad, "detail": (resp or "")[:120]}
        except Exception as exc:  # noqa: BLE001
            return {"agent": at, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    async def _all() -> list[dict]:
        return await asyncio.gather(*[_probe(a) for a in agent_types])

    if loop.is_running():
        # Called from inside a running loop (CEO tool path) — spawn a task.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(_all())).result()
    try:
        return loop.run_until_complete(_all())
    finally:
        # Only the loop we created here gets closed. A loop that belonged to
        # the caller is still theirs to use.
        if not loop.is_closed():
            loop.close()


def system_selfcheck(deep: bool = False) -> dict:
    """Full-stack health scan. CEO runs this proactively and after any error.

    Quick mode (default): imports + DB + LLM — always <30s (Render request
    timeout safe). Deep mode also pings every workspace agent (slow, each
    probe is a full LLM round-trip) — use only when diagnosing agents.
    """
    report: dict = {
        "imports": check_imports(),
        "db": check_db(),
        "llm": check_llm(),
        "gateway_keys": check_gateway_keys(),
    }
    if deep:
        try:
            report["agents"] = check_agents()
        except Exception as exc:  # noqa: BLE001
            report["agents"] = [{"agent": "all", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}]
    else:
        report["agents"] = "skipped (quick mode; deep=true probes agents)"

    # Compact summary for the CEO's short replies.
    bad_imports = [r["module"] for r in report["imports"] if not r["ok"]]
    bad_agents = [r["agent"] for r in report.get("agents", []) if isinstance(r, dict) and not r.get("ok")]
    report["summary"] = {
        "healthy": not bad_imports and not bad_agents and report["db"]["ok"] and report["llm"].get("ok"),
        "bad_imports": bad_imports,
        "bad_agents": bad_agents,
        "db_ok": report["db"]["ok"],
        "llm_ok": report["llm"].get("ok", False),
    }
    return report


# ── Runtime module fix ───────────────────────────────────────────────────────

_MODULE_RE = re.compile(r"No module named ['\"]?([A-Za-z0-9_\.]+)['\"]?")


def extract_missing_module(error: str) -> str:
    """Pull the missing module name out of a ModuleNotFoundError string."""
    m = _MODULE_RE.search(error or "")
    return m.group(1) if m else ""


_PIP_NAME_FIXES = {
    "dns": "dnspython",
    "bs4": "beautifulsoup4",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "crypto": "pycryptodome",
}


def fix_module_error(error: str) -> dict:
    """Self-repair a ModuleNotFoundError via runtime pip install.

    Installs only the single missing distribution (pip-name mapped),
    then re-imports to verify. Returns what to add to requirements.txt
    so the fix is permanent for the dev loop.
    """
    missing = extract_missing_module(error)
    if not missing:
        return {"ok": False, "error": "no ModuleNotFoundError found in the error text"}
    top = missing.split(".")[0]
    if top == "admin":
        # admin.* is repo code, not a pip package — runtime install cannot fix it.
        return {
            "ok": False,
            "module": missing,
            "error": (
                f"{missing} repo code hai (pip package nahi) — runtime install se fix nahi hoga. "
                "Iska matlab deploy/build mein file missing hai. Boss ko deploy-loop fix chahiye "
                "(backend-deploy/admin/ mein file + .dockerignore check)."
            ),
        }
    pip_pkg = _PIP_NAME_FIXES.get(top, top)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--no-warn-script-location", pip_pkg],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            return {"ok": False, "module": missing, "error": f"pip install {pip_pkg} failed: {proc.stderr[-300:]}"}
        importlib.import_module(missing)
        logger.info("SELF-FIX: runtime-installed %s for %s", pip_pkg, missing)
        return {
            "ok": True,
            "module": missing,
            "installed": pip_pkg,
            "note": (
                f"Runtime-install ho gaya — session chal padegi. PERMANENT fix: "
                f"requirements.txt mein '{pip_pkg}' add karna hai (deploy loop)."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "module": missing, "error": f"{type(exc).__name__}: {exc}"}
