"""SBA (Sales/Business Agent) — LangGraph-powered multi-phase thinking agent.

The SBA agent handles the full lead lifecycle inside a workspace:
  - Lead discovery & prospecting (via dedicated Chrome browser)
  - Lead qualification & nurture
  - Client handoff to the Workspace CEO

Uses LangGraph StateGraph instead of a manual OpenAI loop,
providing proper state management, sub-graph support per workspace,
and the NodeFunction communication pattern (per interview Q12).
"""

from __future__ import annotations

import logging
from typing import Any

from admin.agency.langgraph_sba import build_sba_graph, register_chrome
from admin.config import settings
from admin.tools.chrome_tool import ChromeTool

logger = logging.getLogger(__name__)


class SBAAgent:
    """SBA agent powered by LangGraph StateGraph.

    Each workspace has its own compiled graph instance (sub-graph per workspace).
    Multi-phase thinking + Chrome browser automation via LangGraph nodes.
    """

    def __init__(
        self,
        workspace_name: str,
        client_name: str | None = None,
        *,
        chrome: ChromeTool | None = None,
    ) -> None:
        self.workspace_name = workspace_name
        self.client_name = client_name or workspace_name
        self.chrome = chrome or ChromeTool(browser_name="sba", workspace=workspace_name)
        register_chrome(workspace_name, self.chrome)

        # Build the LangGraph state graph for this workspace
        self.graph = build_sba_graph()

        # Thread ID for conversation persistence via checkpointer
        self._thread_id = f"sba_{workspace_name}"

    async def chat(
        self,
        message: str,
        *,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Send a message to SBA via LangGraph state graph.

        The graph handles:
          - call_llm: multi-phase thinking with chrome tool definitions
          - run_tools: execute chrome browser actions
          - finalize: strip think blocks, return clean response

        LangGraph's built-in checkpointer persists conversation state
        across calls (per workspace thread). The frontend's
        conversation_history is accepted but not required — the graph
        manages its own persistence.

        Returns
        -------
        - response: the final text shown to the CEO
        - thinking_phases: list of {phase, content} dicts
        """
        # Initial state for the graph
        # Note: messages only has the new user message. The checkpointer
        # persists previous conversation. conversation_history from
        # frontend is available for reference but graph manages its own.
        initial_state = {
            "messages": [{"role": "user", "content": message}],
            "workspace_name": self.workspace_name,
            "client_name": self.client_name,
            "browser_name": getattr(self.chrome, "browser_name", "sba"),
            "stealth_mode": True,
            "conversation_history": conversation_history or [],
            "thinking_phases": [],
            "tool_round": 0,
            "final_output": "",
            "error": None,
        }

        # Run the graph
        try:
            result = await self.graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": self._thread_id}},
            )
        except Exception:
            logger.exception("SBA LangGraph execution failed")
            return (
                "Bhai, SBA ka thinking engine abhi issue mein hai. "
                "Thodi der mein try karte hain.",
                [],
            )

        final_output = result.get("final_output", "")
        thinking_phases = result.get("thinking_phases", [])

        # Debug: log the full result state
        logger.info("SBA graph result keys: %s", list(result.keys()))
        logger.info("SBA graph error: %s", result.get("error"))
        logger.info("SBA graph final_output length: %d", len(final_output) if final_output else 0)
        logger.info("SBA graph messages count: %d", len(result.get("messages", [])))
        if result.get("error"):
            logger.warning("SBA LLM error detail: %s", result["error"])

        if not final_output:
            if result.get("error"):
                final_output = (
                    "Bhai, SBA ka LLM reachable nahi hai. "
                    f"Error: {result['error'][:200]}"
                )
            else:
                final_output = (
                    "SBA ne analysis complete kar liya hai. "
                    "Aap kya next step chahte hain?"
                )

        return final_output, thinking_phases
