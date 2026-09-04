"""SBA Reasoning Chain — premium lead-finding + meeting-booking reasoning.

5-step, workspace-aware reasoning pipeline for SBA:
  1. DIAGNOSE  — extract ICP + offer from the client brief
  2. SOURCE    — choose platforms + concrete search queries
  3. QUALIFY   — score a discovered lead (BANT + CHAMP)
  4. OUTREACH  — draft a client-facing first-touch message
  5. BOOK      — propose a concrete meeting + handoff brief

Design notes (matches the hardened website.py / langgraph_sba.py patterns):
  - Every entrypoint accepts and threads ``workspace_id`` / ``client_name`` so
    nothing mixes clients across workspaces.
  - Uses the hy3-free model via settings (``big-pickle`` on opencode.ai/zen/v1).
  - Pure-Python, CPU-friendly (no heavy deps).
  - All LLM output is parsed defensively; malformed JSON never crashes.
  - Each step degrades gracefully: on LLM failure it returns a structured
    fallback so downstream code still gets a usable object.

This module does NOT touch the Store or Website logic (SBA = sales/lead dept).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import openai

from admin.config import settings
from admin.workspace.agents.sba_reasoning_prompts import (
    SBA_BOOK_SYSTEM,
    SBA_DIAGNOSE_SYSTEM,
    SBA_OUTREACH_SYSTEM,
    SBA_QUALIFY_SYSTEM,
    SBA_SOURCE_SYSTEM,
)

logger = logging.getLogger(__name__)

# CPU-friendly, no network timeout bombs — bounded wait so we never hang forever.
_LLM_TIMEOUT_SECONDS = 60.0


def _get_llm_client() -> "openai.OpenAI | None":
    """OpenAI-compatible client, mirrored from website.py (the hardened gold standard).

    Uses settings.WORKSPACE_API_KEY / WORKSPACE_API_BASE / WORKSPACE_AGENT_MODEL,
    which point at the hy3-free model (big-pickle on https://opencode.ai/zen/v1)
    in this project's .env. Never reads a Groq hy3 model by default.
    """
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    try:
        if base_url:
            return openai.OpenAI(api_key=api_key, base_url=base_url)
        return openai.OpenAI(api_key=api_key)
    except Exception:  # noqa: BLE001
        logger.warning("openai client unavailable for SBA reasoning chain")
        return None


def _llm_call(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 1200,
) -> str:
    """Single hardened LLM call with timeout + retry, always returns a string."""
    client = _get_llm_client()
    if not client:
        return json.dumps({"error": "LLM client not available"})

    model = settings.WORKSPACE_AGENT_MODEL or "big-pickle"
    last_err: Exception | None = None
    for attempt in range(2):  # 1 initial + 1 retry
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=_LLM_TIMEOUT_SECONDS,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("SBA reasoning LLM call failed (attempt %d): %s", attempt + 1, exc)
    return json.dumps({"error": str(last_err)}) if last_err else ""


def _parse_json(text: str) -> dict[str, Any]:
    """Defensive JSON extraction.

    Handles fenced ```json blocks, trailing prose, and ``think`` tags.
    Never raises — returns a dict with ``parse_error`` on failure.
    """
    if not text:
        return {"parse_error": True, "raw_response": ""}
    t = text.strip()

    # Strip <think>...</think> and ```think...``` noise first
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.DOTALL | re.IGNORECASE).strip()
    t = re.sub(r"```think.*?```", "", t, flags=re.DOTALL).strip()

    # Pull out a fenced json block if present
    if "```" in t:
        parts = t.split("```")
        for chunk in parts:
            chunk = chunk.strip()
            if chunk.lower().startswith("json"):
                chunk = chunk[4:].strip()
            try:
                return json.loads(chunk)
            except (json.JSONDecodeError, IndexError):
                continue

    # Try the whole thing
    try:
        return json.loads(t)
    except (json.JSONDecodeError, IndexError):
        pass

    # Last resort: extract the first balanced {...} block
    start = t.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = t[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, IndexError):
                        break
    return {"parse_error": True, "raw_response": text[:1000]}


class SBALeadReasoningChain:
    """Workspace-aware reasoning chain for lead-finding + meeting-booking.

    Usage:
        chain = SBALeadReasoningChain(workspace_id="ws_acme", client_name="Acme")
        result = chain.run(brief_text)
    """

    def __init__(self, workspace_id: str = "", client_name: str = "Client"):
        # Never let a blank workspace cross-contaminate: fall back to a stable id.
        self.workspace_id = workspace_id or "agency"
        self.client_name = client_name or "Client"

    # ── helpers ────────────────────────────────────────────────────────────

    def _step(self, system: str, user: str, label: str) -> dict[str, Any]:
        raw = _llm_call(system, user)
        parsed = _parse_json(raw)
        if "error" in parsed or parsed.get("parse_error"):
            logger.warning("SBA reasoning step '%s' degraded: %s", label, parsed)
        return parsed

    def run(self, brief_text: str, lead_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the full 5-step chain. Always returns a structured dict."""
        lead_context = lead_context or {}
        brief = (brief_text or "").strip()

        start = time.time()

        diagnose = self._step(
            SBA_DIAGNOSE_SYSTEM,
            f"Client: {self.client_name} (workspace: {self.workspace_id})\n\n"
            f"Brief:\n{brief}\n\n"
            f"Existing lead context:\n{json.dumps(lead_context, indent=2)}",
            "diagnose",
        )

        source = self._step(
            SBA_SOURCE_SYSTEM,
            f"Diagnosed ICP:\n{json.dumps(diagnose, indent=2)}\n\n"
            f"Find concrete platforms + search queries for this ICP.",
            "source",
        )

        qualify = self._step(
            SBA_QUALIFY_SYSTEM,
            f"ICP:\n{json.dumps(diagnose.get('icp', {}), indent=2)}\n\n"
            f"Candidate lead:\n{json.dumps(lead_context, indent=2)}",
            "qualify",
        )

        outreach = self._step(
            SBA_OUTREACH_SYSTEM,
            f"Client offer: {diagnose.get('offer', '')}\n"
            f"Lead angle: {diagnose.get('lead_angle', '')}\n"
            f"Lead to contact:\n{json.dumps(lead_context, indent=2)}",
            "outreach",
        )

        book = self._step(
            SBA_BOOK_SYSTEM,
            f"Lead interest: {lead_context.get('interest', 'positive')}\n"
            f"Lead context:\n{json.dumps(lead_context, indent=2)}",
            "book",
        )

        total_time = round(time.time() - start, 1)

        return {
            "status": "success",
            "workspace_id": self.workspace_id,
            "client_name": self.client_name,
            "reasoning_chain": {
                "diagnose": diagnose,
                "source": source,
                "qualify": qualify,
                "outreach": outreach,
                "book": book,
            },
            "total_time_seconds": total_time,
        }


def run_sba_lead_reasoning(
    brief_text: str,
    workspace_id: str = "",
    client_name: str = "Client",
    lead_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience function for the SBA lead reasoning chain."""
    chain = SBALeadReasoningChain(workspace_id=workspace_id, client_name=client_name)
    return chain.run(brief_text, lead_context=lead_context)
