"""Reasoning Chain — Content Agent ke 5-step reasoning workflow.

Yeh module Content Agent ko "samajhne ki ability" deta hai.
Brief aane pe sochta hai, research karta hai, strategize karta hai,
phir execute karta hai, aur validate bhi karta hai.

Hardened version:
- Settings-driven, hy3-free HTTP client (no global mutable model state).
- Think-block stripping on EVERY LLM path (mirrors website.py / sba.py).
- Workspace + client context threaded through (multi-tenant safe).
- CPU-friendly / offline-safe: never imports heavy GPU deps.
"""
from __future__ import annotations

import json
import re
import time
import logging
from typing import Any

from admin.workspace.agents.reasoning_prompts import (
    UNDERSTAND_SYSTEM,
    RESEARCH_SYSTEM,
    STRATEGIZE_SYSTEM,
    EXECUTE_SYSTEM,
    VALIDATE_SYSTEM,
    build_context_line,
)
from admin.config import settings
from admin.tools.reasoning_logger import ReasoningLogger

logger = logging.getLogger(__name__)

# Hy3-free default model (cheap, fast, CPU-friendly inference path).
# Overridable per-workspace via settings.WORKSPACE_AGENT_MODEL.
DEFAULT_REASONING_MODEL = "llama-3.3-70b-versatile"


def _strip_think_blocks(content: str) -> str:
    """Remove model ``<think>...</think>`` / ```think``` blocks.

    Mirrors the hardened sba.py / seo.py behavior so reasoning JSON is
    never polluted by chain-of-thought noise.
    """
    if not content:
        return ""
    cleaned = re.sub(r"```think.*?```", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _get_llm_client():
    """OpenAI-compatible LLM client (hy3-free path, per-workspace safe).

    Uses the owner's OpenAI-compatible key/base from settings. Falls back to
    a dummy client when openai is unavailable so callers degrade gracefully.
    """
    try:
        import openai
    except ImportError:
        logger.warning("openai not installed, using dummy client")
        return None

    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    if base_url:
        return openai.OpenAI(api_key=api_key, base_url=base_url)
    return openai.OpenAI(api_key=api_key)


def _llm_call(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    model: str | None = None,
) -> str:
    """Simple LLM call. Returns stripped assistant content string.

    Never raises: on any failure returns a JSON error string so the
    downstream parse step stays crash-free.
    """
    client = _get_llm_client()
    if not client:
        return '{"error": "LLM client not available"}'

    resolved_model = model or settings.WORKSPACE_AGENT_MODEL or DEFAULT_REASONING_MODEL
    try:
        response = client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        return _strip_think_blocks(content)
    except Exception as e:
        logger.exception("LLM call failed")
        return json.dumps({"error": str(e)})


def _parse_json(text: str) -> dict[str, Any]:
    """LLM response se JSON extract karo. Crash-safe."""
    if not text:
        return {"raw_response": "", "parse_error": True}
    try:
        t = text.strip()
        # Strip fenced code blocks (```json ... ```)
        if "```" in t:
            parts = t.split("```")
            # take the first code block if present, else the original
            t = parts[1] if len(parts) > 1 else t
            if t.startswith("json"):
                t = t[4:]
            t = t.strip()
        # Drop a leading/trailing ``` if it survived splitting
        t = t.strip("`").strip()
        return json.loads(t)
    except (json.JSONDecodeError, IndexError, ValueError):
        return {"raw_response": text, "parse_error": True}


class ReasoningChain:
    """Content Agent ka 5-step reasoning workflow.

    Usage:
        chain = ReasoningChain(workspace_id="xyz", domain="ads", client_name="Acme")
        result = chain.run(brief_text, brand_context)
    """

    def __init__(
        self,
        workspace_id: str = "",
        domain: str = "content",
        client_name: str = "",
        industry: str = "",
        model: str | None = None,
    ):
        self.workspace_id = workspace_id or ""
        self.domain = domain or "content"
        self.client_name = client_name or ""
        self.industry = industry or ""
        self.model = model
        self.job_id = ReasoningLogger.create_job_id()
        self.logger = ReasoningLogger(
            self.job_id, workspace_id, domain, client_name=client_name
        )

    def run(
        self, brief_text: str, brand_context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Full 5-step reasoning chain run karo."""
        brand_context = brand_context or {}
        # Thread workspace/client/industry so the LLM tailors output per client.
        brand_context.setdefault("workspace_id", self.workspace_id)
        brand_context.setdefault("client_name", self.client_name)
        brand_context.setdefault("industry", self.industry)

        start = time.time()

        # Step 1: UNDERSTAND
        step1 = self._step_understand(brief_text, brand_context)
        if "error" in step1:
            return {"status": "error", "step": "understand", "error": step1["error"]}

        # Step 2: RESEARCH
        step2 = self._step_research(step1, brand_context)
        if "error" in step2:
            return {"status": "error", "step": "research", "error": step2["error"]}

        # Step 3: STRATEGIZE
        step3 = self._step_strategize(step1, step2)
        if "error" in step3:
            return {"status": "error", "step": "strategize", "error": step3["error"]}

        # Step 4: EXECUTE (generate prompts)
        step4 = self._step_execute(step1, step2, step3)

        # Step 5: VALIDATE (quality check)
        step5 = self._step_validate(step1, step3, step4)

        # Save reasoning log
        log_path = self.logger.save()

        total_time = round(time.time() - start, 1)

        return {
            "status": "success",
            "job_id": self.job_id,
            "workspace_id": self.workspace_id,
            "domain": self.domain,
            "reasoning_chain": {
                "understand": step1,
                "research": step2,
                "strategize": step3,
                "execute": step4,
                "validate": step5,
            },
            "quality_score": step5.get("overall_score", 0),
            "pass": step5.get("pass", False),
            "total_tokens": self.logger.total_tokens,
            "total_time_seconds": total_time,
            "log_path": log_path,
        }

    def _step_understand(self, brief_text: str, brand_context: dict) -> dict[str, Any]:
        """Step 1: Brief ko deeply samjho."""
        t0 = time.time()
        ctx = build_context_line(self.workspace_id, self.client_name, self.industry)
        user_prompt = (
            f"[Context: {ctx}]" if ctx else ""
            f"Brief:\n{brief_text}\n\n"
            f"Brand Context:\n{json.dumps(brand_context, indent=2, ensure_ascii=False)}"
        )
        raw = _llm_call(UNDERSTAND_SYSTEM, user_prompt, model=self.model)
        result = _parse_json(raw)
        duration = (time.time() - t0) * 1000

        self.logger.log_step(
            "understand",
            reasoning=result.get("reasoning", raw[:500]),
            output=result,
            tokens_used=len(raw) // 4,
            duration_ms=duration,
        )
        return result

    def _step_research(self, understand: dict, brand_context: dict) -> dict[str, Any]:
        """Step 2: Context gather karo."""
        t0 = time.time()
        ctx = build_context_line(self.workspace_id, self.client_name, self.industry)
        user_prompt = (
            f"[Context: {ctx}]\n\n" if ctx else ""
            f"Parsed Brief:\n{json.dumps(understand, indent=2, ensure_ascii=False)}\n\n"
            f"Brand Data:\n{json.dumps(brand_context, indent=2, ensure_ascii=False)}"
        )
        raw = _llm_call(RESEARCH_SYSTEM, user_prompt, model=self.model)
        result = _parse_json(raw)
        duration = (time.time() - t0) * 1000

        self.logger.log_step(
            "research",
            reasoning=result.get("reasoning", raw[:500]),
            output=result,
            tokens_used=len(raw) // 4,
            duration_ms=duration,
        )
        return result

    def _step_strategize(self, understand: dict, research: dict) -> dict[str, Any]:
        """Step 3: Visual strategy banao."""
        t0 = time.time()
        ctx = build_context_line(self.workspace_id, self.client_name, self.industry)
        user_prompt = (
            f"[Context: {ctx}]\n\n" if ctx else ""
            f"Brief Understanding:\n{json.dumps(understand, indent=2, ensure_ascii=False)}\n\n"
            f"Research:\n{json.dumps(research, indent=2, ensure_ascii=False)}"
        )
        raw = _llm_call(STRATEGIZE_SYSTEM, user_prompt, model=self.model)
        result = _parse_json(raw)
        duration = (time.time() - t0) * 1000

        self.logger.log_step(
            "strategize",
            reasoning=result.get("reasoning", raw[:500]),
            output=result,
            tokens_used=len(raw) // 4,
            duration_ms=duration,
        )
        return result

    def _step_execute(self, understand: dict, research: dict, strategize: dict) -> dict[str, Any]:
        """Step 4: Prompts generate karo for GPU."""
        t0 = time.time()
        ctx = build_context_line(self.workspace_id, self.client_name, self.industry)
        user_prompt = (
            f"[Context: {ctx}]\n\n" if ctx else ""
            f"Brief:\n{json.dumps(understand, indent=2, ensure_ascii=False)}\n\n"
            f"Research:\n{json.dumps(research, indent=2, ensure_ascii=False)}\n\n"
            f"Strategy:\n{json.dumps(strategize, indent=2, ensure_ascii=False)}"
        )
        raw = _llm_call(EXECUTE_SYSTEM, user_prompt, model=self.model)
        result = _parse_json(raw)
        duration = (time.time() - t0) * 1000

        self.logger.log_step(
            "execute",
            reasoning=result.get("reasoning", raw[:500]),
            output=result,
            tokens_used=len(raw) // 4,
            duration_ms=duration,
        )
        return result

    def _step_validate(self, understand: dict, strategize: dict, execute: dict) -> dict[str, Any]:
        """Step 5: Quality validation karo."""
        t0 = time.time()
        ctx = build_context_line(self.workspace_id, self.client_name, self.industry)
        user_prompt = (
            f"[Context: {ctx}]\n\n" if ctx else ""
            f"Brief Requirements:\n{json.dumps(understand, indent=2, ensure_ascii=False)}\n\n"
            f"Strategy:\n{json.dumps(strategize, indent=2, ensure_ascii=False)}\n\n"
            f"Generated Prompts:\n{json.dumps(execute, indent=2, ensure_ascii=False)}"
        )
        raw = _llm_call(VALIDATE_SYSTEM, user_prompt, model=self.model)
        result = _parse_json(raw)
        duration = (time.time() - t0) * 1000

        self.logger.log_step(
            "validate",
            reasoning=result.get("reasoning", raw[:500]),
            output=result,
            tokens_used=len(raw) // 4,
            duration_ms=duration,
        )
        return result


def run_reasoning_chain(
    brief_text: str,
    workspace_id: str = "",
    domain: str = "content",
    brand_context: dict | None = None,
    client_name: str = "",
    industry: str = "",
) -> dict[str, Any]:
    """Quick function to run the reasoning chain."""
    chain = ReasoningChain(
        workspace_id=workspace_id,
        domain=domain,
        client_name=client_name,
        industry=industry,
    )
    return chain.run(brief_text, brand_context=brand_context)
