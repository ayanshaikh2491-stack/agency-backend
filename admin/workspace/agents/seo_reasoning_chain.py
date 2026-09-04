"""SEO Agent — Reasoning Chain Implementation.

5-step reasoning workflow for SEO analysis and optimization.
Har step mein LLM call hota hai for deep thinking.
"""
from __future__ import annotations

import json
import os
import re
import time
import logging
from typing import Any

from admin.config import settings
from admin.workspace.agents.seo_reasoning_prompts import (
    SEO_UNDERSTAND_SYSTEM,
    SEO_RESEARCH_SYSTEM,
    SEO_STRATEGIZE_SYSTEM,
    SEO_EXECUTE_SYSTEM,
    SEO_VALIDATE_SYSTEM,
)
from admin.tools.reasoning_logger import ReasoningLogger

logger = logging.getLogger(__name__)


def _strip_think_blocks(text: str | None) -> str:
    """Remove model ```think / <think> reasoning blocks (gold standard)."""
    if not text:
        return ""
    cleaned = re.sub(r"```think.*?```", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _get_llm_client():
    """OpenAI-compatible LLM client with CEO-key fallback (gold standard).

    Never hard-codes a hy3 model. CPU-friendly: remote API only.
    """
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    try:
        import openai

        if base_url:
            return openai.OpenAI(api_key=api_key, base_url=base_url)
        return openai.OpenAI(api_key=api_key)
    except ImportError:
        logger.warning("openai not installed")
        return None


def _llm_call(system_prompt_text: str, user_prompt: str, temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """LLM call with retry + timeout + fallback key (crash-proof)."""
    client = _get_llm_client()
    if not client:
        return '{"error": "LLM client not available"}'

    model = settings.WORKSPACE_AGENT_MODEL or "llama-3.3-70b-versatile"
    last_err: str | None = None
    for attempt in range(1, 3):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt_text},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=120,
            )
            return _strip_think_blocks(response.choices[0].message.content) or ""
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            logger.warning("SEO reasoning LLM call failed (attempt %d/2): %s", attempt, e)
    logger.exception("SEO reasoning LLM call failed after 2 attempts")
    return json.dumps({"error": last_err or "unknown"})


def _parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extract: strip think blocks, fenced code, then parse."""
    if not text:
        return {"raw_response": "", "parse_error": True}
    t = _strip_think_blocks(text).strip()
    # Drop any non-JSON preamble before the first {
    first_brace = t.find("{")
    last_brace = t.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        t = t[first_brace : last_brace + 1]
    if "```" in t:
        # Grab the first fenced block (json or otherwise)
        parts = t.split("```")
        if len(parts) >= 2:
            block = parts[1]
            if block.startswith("json"):
                block = block[4:]
            t = block.strip()
    try:
        return json.loads(t)
    except (json.JSONDecodeError, IndexError):
        return {"raw_response": text, "parse_error": True}


class SEOReasoningChain:
    """SEO Agent ka 5-step reasoning workflow.

    Usage:
        chain = SEOReasoningChain(workspace_id="xyz")
        result = chain.run(brief_text, brand_context)
    """

    def __init__(self, workspace_id: str = "", client_id: str | None = None):
        self.workspace_id = workspace_id
        self.client_id = client_id or workspace_id
        self.job_id = ReasoningLogger.create_job_id()
        self.logger = ReasoningLogger(self.job_id, workspace_id, "seo")

    def _ctx_header(self, brief_text: str) -> str:
        return f"[Workspace: {self.workspace_id} | Client: {self.client_id}]\n\n{brief_text}"

    def run(self, brief_text: str, brand_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Full 5-step SEO reasoning chain run karo."""
        brand_context = brand_context or {}
        brand_context.setdefault("_workspace_id", self.workspace_id)
        brand_context.setdefault("_client_id", self.client_id)
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

        # Step 4: EXECUTE
        step4 = self._step_execute(step1, step2, step3)

        # Step 5: VALIDATE
        step5 = self._step_validate(step1, step3, step4)

        # Save reasoning log
        log_path = self.logger.save()

        total_time = round(time.time() - start, 1)

        return {
            "status": "success",
            "job_id": self.job_id,
            "reasoning_chain": {
                "understand": step1,
                "research": step2,
                "strategize": step3,
                "execute": step4,
                "validate": step5,
            },
            "quality_score": step5.get("overall_score", 0),
            "pass": step5.get("pass", False),
            "executive_summary": step5.get("executive_summary", ""),
            "total_tokens": self.logger.total_tokens,
            "total_time_seconds": total_time,
            "log_path": log_path,
        }

    def _step_understand(self, brief_text: str, brand_context: dict) -> dict[str, Any]:
        """Step 1: SEO request ko deeply samjho."""
        t0 = time.time()
        user_prompt = f"SEO Request:\n{self._ctx_header(brief_text)}\n\nBrand Context:\n{json.dumps(brand_context, indent=2)}"
        raw = _llm_call(SEO_UNDERSTAND_SYSTEM, user_prompt)
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
        """Step 2: SEO data gather karo."""
        t0 = time.time()
        user_prompt = f"Parsed Request:\n{json.dumps(understand, indent=2)}\n\nBrand Data:\n{json.dumps(brand_context, indent=2)}"
        raw = _llm_call(SEO_RESEARCH_SYSTEM, user_prompt)
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
        """Step 3: SEO strategy banao."""
        t0 = time.time()
        user_prompt = (
            f"Request Understanding:\n{json.dumps(understand, indent=2)}\n\n"
            f"Research Findings:\n{json.dumps(research, indent=2)}"
        )
        raw = _llm_call(SEO_STRATEGIZE_SYSTEM, user_prompt)
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
        """Step 4: SEO deliverables create karo."""
        t0 = time.time()
        user_prompt = (
            f"Request:\n{json.dumps(understand, indent=2)}\n\n"
            f"Research:\n{json.dumps(research, indent=2)}\n\n"
            f"Strategy:\n{json.dumps(strategize, indent=2)}"
        )
        raw = _llm_call(SEO_EXECUTE_SYSTEM, user_prompt)
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
        user_prompt = (
            f"Request Requirements:\n{json.dumps(understand, indent=2)}\n\n"
            f"Strategy:\n{json.dumps(strategize, indent=2)}\n\n"
            f"Deliverables:\n{json.dumps(execute, indent=2)}"
        )
        raw = _llm_call(SEO_VALIDATE_SYSTEM, user_prompt)
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


def run_seo_reasoning_chain(brief_text: str, workspace_id: str = "", brand_context: dict | None = None) -> dict[str, Any]:
    """Quick function to run the SEO reasoning chain."""
    chain = SEOReasoningChain(workspace_id=workspace_id)
    return chain.run(brief_text, brand_context=brand_context)
