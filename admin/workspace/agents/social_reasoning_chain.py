"""Social Agent — Reasoning Chain Implementation.

5-step reasoning workflow for Social Media content creation.
Har step mein LLM call hota hai for deep thinking.
"""
from __future__ import annotations

import json
import os
import re
import time
import logging
from typing import Any

import openai
from admin.config import settings
from admin.workspace.agents.social_reasoning_prompts import (
    SOCIAL_UNDERSTAND_SYSTEM,
    SOCIAL_RESEARCH_SYSTEM,
    SOCIAL_STRATEGIZE_SYSTEM,
    SOCIAL_EXECUTE_SYSTEM,
    SOCIAL_VALIDATE_SYSTEM,
)
from admin.tools.reasoning_logger import ReasoningLogger

logger = logging.getLogger(__name__)

MAX_LLM_RETRIES = 2
LLM_TIMEOUT_SECONDS = 120


def _get_llm_client():
    """OpenAI-compatible client with CEO-key fallback (gold standard).

    Uses per-workspace key/base first, then agency CEO key/base (via settings,
    not raw env vars). Never hard-codes a hy3 model. CPU-friendly.
    """
    api_key = settings.WORKSPACE_API_KEY or settings.AGENCY_CEO_API_KEY or "dummy"
    base_url = settings.WORKSPACE_API_BASE or settings.AGENCY_CEO_API_BASE or None
    try:
        if base_url:
            return openai.OpenAI(api_key=api_key, base_url=base_url)
        return openai.OpenAI(api_key=api_key)
    except ImportError:
        logger.warning("openai not installed")
        return None


def _strip_think_blocks(content: str | None) -> str:
    """Remove model "thinking" tokens so only the final answer reaches the client.

    Tolerates every known model-output quirk (mirrors sba.py gold standard).
    """
    if not content:
        return ""
    cleaned = re.sub(r"```think.*?```", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    if cleaned.strip().lower().startswith("think"):
        body = cleaned.strip()[5:].strip()
        lines = body.split("\n")
        response_lines: list[str] = []
        in_thinking = False
        for line in lines:
            if re.match(r"^\s*\d+\.\s+\w", line):
                in_thinking = True
                continue
            if in_thinking and re.match(r"^\s*$", line):
                in_thinking = False
                continue
            if not in_thinking:
                response_lines.append(line)
        cleaned = "\n".join(response_lines).strip() or body
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"```", "", cleaned).strip()
    return cleaned


def _llm_call(system_prompt: str, user_prompt: str, temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """LLM call with retry + timeout. Returns assistant content (think-block stripped)."""
    client = _get_llm_client()
    if not client:
        return '{"error": "LLM client not available"}'

    model = settings.WORKSPACE_AGENT_MODEL or "llama-3.3-70b-versatile"
    last_error: str | None = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            raw = response.choices[0].message.content or ""
            return _strip_think_blocks(raw) or raw
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            logger.warning("Social reasoning LLM call failed (attempt %d/%d): %s", attempt, MAX_LLM_RETRIES, e)
    logger.exception("Social reasoning LLM call failed after %d attempts", MAX_LLM_RETRIES)
    return json.dumps({"error": last_error or "unknown"})


def _parse_json(text: str) -> dict[str, Any]:
    """LLM response se JSON extract karo (robust: fences, think blocks, prose)."""
    try:
        t = _strip_think_blocks(text).strip()
        if "```" in t:
            parts = t.split("```")
            if len(parts) >= 3:
                block = parts[1]
                if block.lstrip().lower().startswith("json"):
                    block = block.lstrip()[4:]
                t = block.strip()
        try:
            parsed = json.loads(t)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        # Find first balanced {...} span
        start = t.find("{")
        if start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(t)):
                ch = t[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return json.loads(t[start : i + 1])
        return {"raw_response": text, "parse_error": True}
    except (json.JSONDecodeError, IndexError, ValueError):
        return {"raw_response": text, "parse_error": True}


class SocialReasoningChain:
    """Social Agent ka 5-step reasoning workflow.

    Usage:
        chain = SocialReasoningChain(workspace_id="xyz")
        result = chain.run(brief_text, brand_context)
    """

    def __init__(self, workspace_id: str = ""):
        self.workspace_id = workspace_id
        self.job_id = ReasoningLogger.create_job_id()
        self.logger = ReasoningLogger(self.job_id, workspace_id, "social")

    def run(self, brief_text: str, brand_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Full 5-step social reasoning chain run karo."""
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

        # Assemble a client-facing, ready-to-publish bundle from the execute step.
        execute = step4 or {}
        caption = (execute.get("caption") or "").strip()
        hashtags = execute.get("hashtags") or []
        if isinstance(hashtags, list):
            hashtag_line = " ".join(str(h) for h in hashtags if h)
        else:
            hashtag_line = str(hashtags)
        publishable_post = ""
        if caption:
            publishable_post = caption
            if hashtag_line:
                publishable_post = f"{caption}\n\n{hashtag_line}"

        # Save reasoning log
        log_path = self.logger.save()

        total_time = round(time.time() - start, 1)

        return {
            "status": "success",
            "job_id": self.job_id,
            "workspace_id": self.workspace_id,
            "reasoning_chain": {
                "understand": step1,
                "research": step2,
                "strategize": step3,
                "execute": step4,
                "validate": step5,
            },
            "publishable_post": publishable_post,
            "caption": caption,
            "hashtags": hashtags,
            "visual_brief": execute.get("visual_brief", {}),
            "quality_score": step5.get("overall_score", 0),
            "pass": step5.get("pass", False),
            "engagement_prediction": step5.get("engagement_prediction", {}),
            "total_tokens": self.logger.total_tokens,
            "total_time_seconds": total_time,
            "log_path": log_path,
        }

    def _step_understand(self, brief_text: str, brand_context: dict) -> dict[str, Any]:
        """Step 1: Social brief ko deeply samjho."""
        t0 = time.time()
        user_prompt = f"Social Media Brief:\n{brief_text}\n\nBrand Context:\n{json.dumps(brand_context, indent=2)}"
        raw = _llm_call(SOCIAL_UNDERSTAND_SYSTEM, user_prompt)
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
        """Step 2: Social context gather karo."""
        t0 = time.time()
        user_prompt = f"Parsed Brief:\n{json.dumps(understand, indent=2)}\n\nBrand Data:\n{json.dumps(brand_context, indent=2)}"
        raw = _llm_call(SOCIAL_RESEARCH_SYSTEM, user_prompt)
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
        """Step 3: Social strategy banao."""
        t0 = time.time()
        user_prompt = (
            f"Brief Understanding:\n{json.dumps(understand, indent=2)}\n\n"
            f"Research:\n{json.dumps(research, indent=2)}"
        )
        raw = _llm_call(SOCIAL_STRATEGIZE_SYSTEM, user_prompt)
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
        """Step 4: Social content create karo."""
        t0 = time.time()
        user_prompt = (
            f"Brief:\n{json.dumps(understand, indent=2)}\n\n"
            f"Research:\n{json.dumps(research, indent=2)}\n\n"
            f"Strategy:\n{json.dumps(strategize, indent=2)}"
        )
        raw = _llm_call(SOCIAL_EXECUTE_SYSTEM, user_prompt)
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
            f"Brief Requirements:\n{json.dumps(understand, indent=2)}\n\n"
            f"Strategy:\n{json.dumps(strategize, indent=2)}\n\n"
            f"Generated Content:\n{json.dumps(execute, indent=2)}"
        )
        raw = _llm_call(SOCIAL_VALIDATE_SYSTEM, user_prompt)
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


def run_social_reasoning_chain(brief_text: str, workspace_id: str = "", brand_context: dict | None = None) -> dict[str, Any]:
    """Quick function to run the social reasoning chain."""
    chain = SocialReasoningChain(workspace_id=workspace_id)
    return chain.run(brief_text, brand_context=brand_context)
