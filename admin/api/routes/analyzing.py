"""Analyzing Agent API routes — senior cross-channel data analyst (insight engine).

Endpoints:
  POST /api/analyzing/chat   — Ask the analyst for a structured insight brief
  GET  /api/analyzing/status — Agent + tool status
  GET  /api/analyzing/tools  — List available analytics tools

NOTE: This is the ANALYST layer. The metrics reporter lives at /api/analytics/*.
This agent reasons ACROSS those reports and returns decisions, not raw numbers.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from admin.tools.analytics_tools import ANALYTICS_TOOLS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyzing", tags=["analyzing-agent"])


# ── Request Models ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    workspace_name: str = "Default"
    client_name: str = "Client"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def analyzing_status():
    """Analyzing Agent status."""
    return {
        "success": True,
        "analyzing": {
            "status": "running",
            "role": "cross-channel data analyst / insight engine",
            "tools_count": len(ANALYTICS_TOOLS),
            "capabilities": [
                "Cross-channel trend analysis", "Channel comparison / benchmarking",
                "Root-cause hypotheses", "Anomaly & alert triage",
                "ROI / budget efficiency", "Forecasting & projections",
                "Decision-ready briefs (summary, evidence, drivers, actions)",
            ],
        },
    }


@router.get("/tools")
async def list_analyzing_tools():
    """List the analytics tools the analyzing agent can use."""
    tools = []
    for t in ANALYTICS_TOOLS:
        fn = t.get("function", {})
        tools.append({"name": fn.get("name", ""), "description": fn.get("description", "")})
    return {"success": True, "data": {"tools": tools, "count": len(tools)}}


@router.post("/chat")
async def chat(req: ChatRequest):
    """Chat with the Analyzing Agent — returns a structured insight brief."""
    from admin.workspace.agents.analyzing import AnalyzingAgent

    message = req.message
    if not message:
        return {"success": False, "error": "Message is required"}

    agent = AnalyzingAgent(workspace_name=req.workspace_name, client_name=req.client_name)
    output, phases = await agent.chat(message)

    return {
        "success": True,
        "response": output,
        "thread_id": agent._thread_id,
        "agent_type": "analyzing",
        "thinking_phases": phases,
    }
