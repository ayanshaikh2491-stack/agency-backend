"""Website Agent — Reasoning Chain Implementation.

5-step reasoning workflow for website design, development, and management.
Har step mein LLM call hota hai for deep thinking.
"""
from __future__ import annotations

import json
import os
import time
import logging
from typing import Any

from admin.workspace.agents.website_reasoning_prompts import (
    WEBSITE_UNDERSTAND_SYSTEM,
    WEBSITE_RESEARCH_SYSTEM,
    WEBSITE_STRATEGIZE_SYSTEM,
    WEBSITE_EXECUTE_SYSTEM,
    WEBSITE_VALIDATE_SYSTEM,
)
from admin.tools.reasoning_logger import ReasoningLogger

logger = logging.getLogger(__name__)


def _get_llm_client():
    """OpenAI-compatible LLM client."""
    api_key = os.getenv("AGENCY_CEO_API_KEY", "dummy")
    base_url = os.getenv("AGENCY_CEO_API_BASE", None)
    try:
        import openai
        if base_url:
            return openai.OpenAI(api_key=api_key, base_url=base_url)
        return openai.OpenAI(api_key=api_key)
    except ImportError:
        logger.warning("openai not installed")
        return None


def _llm_call(system_prompt: str, user_prompt: str, temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """Simple LLM call."""
    client = _get_llm_client()
    if not client:
        return '{"error": "LLM client not available"}'

    model = os.getenv("WORKSPACE_AGENT_MODEL", "llama-3.3-70b-versatile")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.exception("LLM call failed")
        return json.dumps({"error": str(e)})


def _parse_json(text: str) -> dict[str, Any]:
    """LLM response se JSON extract karo."""
    try:
        t = text.strip()
        if "```" in t:
            t = t.split("```")[1]
            if t.startswith("json"):
                t = t[4:]
            t = t.strip()
        return json.loads(t)
    except (json.JSONDecodeError, IndexError):
        return {"raw_response": text, "parse_error": True}


class WebsiteReasoningChain:
    """Website Agent ka 5-step reasoning workflow.

    Usage:
        chain = WebsiteReasoningChain(workspace_id="xyz")
        result = chain.run(brief_text, brand_context)
    """

    def __init__(self, workspace_id: str = ""):
        self.workspace_id = workspace_id
        self.job_id = ReasoningLogger.create_job_id()
        self.logger = ReasoningLogger(self.job_id, workspace_id, "website")

    def run(self, brief_text: str, brand_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Full 5-step website reasoning chain run karo."""
        brand_context = brand_context or {}
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
        """Step 1: Website request ko deeply samjho."""
        t0 = time.time()
        user_prompt = f"Website Request:\n{brief_text}\n\nBrand Context:\n{json.dumps(brand_context, indent=2)}"
        raw = _llm_call(WEBSITE_UNDERSTAND_SYSTEM, user_prompt)
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
        """Step 2: Website data gather karo."""
        t0 = time.time()
        user_prompt = f"Parsed Request:\n{json.dumps(understand, indent=2)}\n\nBrand Data:\n{json.dumps(brand_context, indent=2)}"
        raw = _llm_call(WEBSITE_RESEARCH_SYSTEM, user_prompt)
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
        """Step 3: Website strategy banao."""
        t0 = time.time()
        user_prompt = (
            f"Request Understanding:\n{json.dumps(understand, indent=2)}\n\n"
            f"Research Findings:\n{json.dumps(research, indent=2)}"
        )
        raw = _llm_call(WEBSITE_STRATEGIZE_SYSTEM, user_prompt)
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
        """Step 4: Website deliverables create karo."""
        t0 = time.time()
        user_prompt = (
            f"Request:\n{json.dumps(understand, indent=2)}\n\n"
            f"Research:\n{json.dumps(research, indent=2)}\n\n"
            f"Strategy:\n{json.dumps(strategize, indent=2)}"
        )
        raw = _llm_call(WEBSITE_EXECUTE_SYSTEM, user_prompt)
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
        raw = _llm_call(WEBSITE_VALIDATE_SYSTEM, user_prompt)
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


def run_website_reasoning_chain(brief_text: str, workspace_id: str = "", brand_context: dict | None = None) -> dict[str, Any]:
    """Quick function to run the website reasoning chain."""
    chain = WebsiteReasoningChain(workspace_id=workspace_id)
    return chain.run(brief_text, brand_context=brand_context)
