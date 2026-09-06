"""Per-agent / per-workspace sandbox.

Real layer: when ``E2B_API_KEY`` is present, every agent gets its OWN
isolated E2B sandbox (https://e2b.dev) for real code/command execution and
arbitrary file writes. When the key is absent (e.g. tonight, before the key
is dropped in), the sandbox transparently falls back to a local subprocess
with a sandboxed temp directory so the agent keeps working without error.

The public surface (``exec``, ``write_file``, ``read_file``) is identical
for both backends, so callers never branch on which one is live.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

from admin.config import settings

logger = logging.getLogger(__name__)

# Module-level cache so repeated ``get_agent_sandbox`` calls for the same
# (agent_type, workspace_id) return the same sandbox (and the SAME real E2B
# sandbox) — like the per-agent isolation in the automaton reference design.
_SANDBOX_CACHE: dict[tuple[str, str], "AgentSandbox"] = {}


@dataclass
class SandboxResult:
    """Normalized result across both backends."""

    stdout: str
    stderr: str
    exit_code: int
    backend: str  # "e2b" | "local"
    sandbox_id: str


class _LocalSandboxBackend:
    """Safe subprocess fallback used when E2B is not configured.

    Writes files under a per-agent, per-workspace temp directory and runs
    commands in it so agents can still execute real logic locally.
    """

    backend = "local"

    def __init__(self, agent_type: str, workspace_id: str) -> None:
        self.agent_type = agent_type
        self.workspace_id = workspace_id
        safe_ws = "".join(c if c.isalnum() else "_" for c in str(workspace_id))
        safe_agent = "".join(c if c.isalnum() else "_" for c in str(agent_type))
        self.root = os.path.join(tempfile.gettempdir(), "tags_sandbox", safe_ws, safe_agent)
        os.makedirs(self.root, exist_ok=True)

    def write_file(self, path: str, content: str) -> None:
        full = os.path.join(self.root, path.lstrip("/\\"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    def read_file(self, path: str) -> str:
        full = os.path.join(self.root, path.lstrip("/\\"))
        with open(full, "r", encoding="utf-8") as fh:
            return fh.read()

    def exec(self, cmd: str, timeout: int = 60) -> SandboxResult:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return SandboxResult(
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                exit_code=proc.returncode or 0,
                backend="local",
                sandbox_id=f"local:{self.workspace_id}:{self.agent_type}",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult("", "timeout", 124, "local", f"local:{self.workspace_id}:{self.agent_type}")
        except Exception as exc:  # pragma: no cover - defensive
            return SandboxResult("", str(exc), 1, "local", f"local:{self.workspace_id}:{self.agent_type}")


class _E2BBackend:
    """Real per-agent E2B sandbox (only constructed when the key exists)."""

    backend = "e2b"

    def __init__(self, agent_type: str, workspace_id: str, template: str | None = None) -> None:
        from e2b import Sandbox  # imported only when actually used

        self.agent_type = agent_type
        self.workspace_id = workspace_id
        # Sandbox.create() provisions a real, isolated sandbox and reads the
        # E2B_API_KEY from the environment automatically. Per-agent isolation:
        # each (agent_type, workspace_id) gets its own sandbox via the cache in
        # get_agent_sandbox().
        tmpl = template or getattr(settings, "E2B_TEMPLATE_ID", "") or None
        create_kwargs: dict[str, Any] = {}
        if tmpl:
            create_kwargs["template"] = tmpl
        self._sb = Sandbox.create(**create_kwargs)
        self.sandbox_id = getattr(self._sb, "sandbox_id", f"e2b:{workspace_id}:{agent_type}")

    def write_file(self, path: str, content: str) -> None:
        self._sb.files.write(path, content)

    def read_file(self, path: str) -> str:
        return self._sb.files.read(path)

    def exec(self, cmd: str, timeout: int = 60) -> SandboxResult:
        self._sb.set_timeout(timeout)
        # E2B's `commands.run` returns a result with stdout/stderr/exit_code.
        res = self._sb.commands.run(cmd)
        return SandboxResult(
            stdout=getattr(res, "stdout", "") or "",
            stderr=getattr(res, "stderr", "") or "",
            exit_code=int(getattr(res, "exit_code", 0) or 0),
            backend="e2b",
            sandbox_id=self.sandbox_id,
        )

    def close(self) -> None:
        try:
            self._sb.kill()
        except Exception:  # pragma: no cover - defensive
            pass


class AgentSandbox:
    """Unified sandbox handle for one agent inside one workspace.

    Construction is cheap and never raises: if E2B is not configured it
    silently selects the local backend. Use :func:`get_agent_sandbox` to
    get a cached, shared instance per (agent_type, workspace_id).
    """

    def __init__(
        self,
        agent_type: str,
        workspace_id: str,
        *,
        template: str | None = None,
        force_local: bool = False,
    ) -> None:
        self.agent_type = agent_type
        self.workspace_id = workspace_id
        self.backend_kind = "local"
        self._backend: Any = None

        key = getattr(settings, "E2B_API_KEY", "") or os.environ.get("E2B_API_KEY", "")
        if key and not force_local:
            try:
                self._backend = _E2BBackend(agent_type, workspace_id, template=template)
                self.backend_kind = "e2b"
                logger.info("AgentSandbox[%s/%s] -> E2B real sandbox", workspace_id, agent_type)
            except Exception as exc:
                logger.warning(
                    "AgentSandbox[%s/%s] E2B init failed, using local: %s",
                    workspace_id, agent_type, exc,
                )
                self._backend = None

        if self._backend is None:
            self._backend = _LocalSandboxBackend(agent_type, workspace_id)
            logger.info("AgentSandbox[%s/%s] -> local fallback", workspace_id, agent_type)

    # ── Public API (same for both backends) ─────────────────────────────────

    def exec(self, cmd: str, timeout: int = 60) -> SandboxResult:
        return self._backend.exec(cmd, timeout=timeout)

    def write_file(self, path: str, content: str) -> None:
        self._backend.write_file(path, content)

    def read_file(self, path: str) -> str:
        return self._backend.read_file(path)

    def python(self, code: str, timeout: int = 60) -> SandboxResult:
        """Run a Python snippet inside the sandbox (real interpreter).

        Writes the snippet to a temp file and runs that file, so multiline
        code and special characters work cross-platform (avoids fragile
        ``python -c '...'`` shell quoting, which breaks on Windows cmd.exe).
        """
        import uuid as _uuid

        script_name = f"_rt_{_uuid.uuid4().hex[:8]}.py"
        if self.backend_kind == "e2b":
            self.write_file(f"/home/user/{script_name}", code)
            return self.exec(f"python /home/user/{script_name}", timeout=timeout)
        # local backend: write into the sandbox root and run
        self.write_file(script_name, code)
        return self.exec(f"python {script_name}", timeout=timeout)

    def close(self) -> None:
        if self.backend_kind == "e2b" and hasattr(self._backend, "close"):
            self._backend.close()

    @property
    def sandbox_id(self) -> str:
        return getattr(self._backend, "sandbox_id", f"{self.backend_kind}:{self.workspace_id}:{self.agent_type}")

    def __repr__(self) -> str:
        return f"<AgentSandbox {self.workspace_id}/{self.agent_type} [{self.backend_kind}]>"


def get_agent_sandbox(
    agent_type: str,
    workspace_id: str,
    *,
    template: str | None = None,
    force_local: bool = False,
) -> AgentSandbox:
    """Cached, shared sandbox per (agent_type, workspace_id).

    Reusing one sandbox per agent keeps state between tool calls (the agent
    can write a script in one step and run it in the next) and matches the
    "per-agent isolation" design — no agent can see another agent's files.
    """
    key = (str(workspace_id), str(agent_type))
    cached = _SANDBOX_CACHE.get(key)
    if cached is None or force_local:
        cached = AgentSandbox(agent_type, workspace_id, template=template, force_local=force_local)
        _SANDBOX_CACHE[key] = cached
    return cached


def _shell_quote(text: str) -> str:
    """Best-effort single-quote shell escaping (cross-platform)."""
    escaped = text.replace("'", "'\\''")
    return f"'{escaped}'"
