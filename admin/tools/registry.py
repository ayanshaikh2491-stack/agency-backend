"""Unified Content Agent Tool Registry — 21 tools in one place.

Combines Visual Tools (10) + Content Tools (11) = 21 tools.
Single entry point for execution, lookup, and listing.

Categories:
  visual  — image/video generation, brand discovery, brief parsing
  content — text analysis, blog generation, calendars, rewriting
  kaggle  — GPU-powered image/video generation
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _load_visual_tools() -> list[dict[str, Any]]:
    """Load visual tools from visual_tools.py."""
    try:
        from admin.tools.visual_tools import VISUAL_TOOLS
        return VISUAL_TOOLS
    except ImportError as e:
        logger.warning("Could not load visual tools: %s", e)
        return []


def _load_content_tools() -> list[dict[str, Any]]:
    """Load content tools from content_tools.py and normalize format."""
    try:
        from admin.tools.content_tools import CONTENT_TOOLS
        normalized = []
        for tool in CONTENT_TOOLS:
            # Normalize to OpenAI function calling format
            if "function" not in tool:
                normalized.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    },
                })
            else:
                normalized.append(tool)
        return normalized
    except ImportError as e:
        logger.warning("Could not load content tools: %s", e)
        return []


def _get_tool_name(tool: dict[str, Any]) -> str:
    """Extract tool name from either format."""
    if "function" in tool:
        return tool["function"].get("name", "")
    return tool.get("name", "")


def _get_tool_description(tool: dict[str, Any]) -> str:
    """Extract tool description from either format."""
    if "function" in tool:
        return tool["function"].get("description", "")
    return tool.get("description", "")


def _get_tool_params(tool: dict[str, Any]) -> dict[str, Any]:
    """Extract tool parameters from either format."""
    if "function" in tool:
        return tool["function"].get("parameters", {})
    return tool.get("parameters", {})


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL LOADING (lazy)
# ═══════════════════════════════════════════════════════════════════════════════

_visual_tools_cache: list[dict[str, Any]] | None = None
_content_tools_cache: list[dict[str, Any]] | None = None


def _get_visual_tools() -> list[dict[str, Any]]:
    global _visual_tools_cache
    if _visual_tools_cache is None:
        _visual_tools_cache = _load_visual_tools()
    return _visual_tools_cache


def _get_content_tools() -> list[dict[str, Any]]:
    global _content_tools_cache
    if _content_tools_cache is None:
        _content_tools_cache = _load_content_tools()
    return _content_tools_cache


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def get_all_tools() -> list[dict[str, Any]]:
    """Get all 21 tools combined (visual + content)."""
    return _get_visual_tools() + _get_content_tools()


def get_visual_tools() -> list[dict[str, Any]]:
    """Get visual tools only (10 tools)."""
    return _get_visual_tools()


def get_content_tools() -> list[dict[str, Any]]:
    """Get content tools only (11 tools)."""
    return _get_content_tools()


def list_all_tools() -> list[dict[str, Any]]:
    """List all tools with name, description, and category."""
    result = []
    for tool in _get_visual_tools():
        result.append({
            "name": _get_tool_name(tool),
            "description": _get_tool_description(tool),
            "category": "visual",
            "parameters": _get_tool_params(tool),
        })
    for tool in _get_content_tools():
        result.append({
            "name": _get_tool_name(tool),
            "description": _get_tool_description(tool),
            "category": "content",
            "parameters": _get_tool_params(tool),
        })
    return result


def list_tools_by_category(category: str) -> list[dict[str, Any]]:
    """Filter tools by category: visual, content, or all."""
    if category == "visual":
        tools = _get_visual_tools()
    elif category == "content":
        tools = _get_content_tools()
    else:
        tools = get_all_tools()
    return [
        {
            "name": _get_tool_name(t),
            "description": _get_tool_description(t),
            "category": category,
            "parameters": _get_tool_params(t),
        }
        for t in tools
    ]


def get_tool_by_name(name: str) -> dict[str, Any] | None:
    """Lookup a tool schema by name."""
    for tool in get_all_tools():
        if _get_tool_name(tool) == name:
            return {
                "name": _get_tool_name(tool),
                "description": _get_tool_description(tool),
                "parameters": _get_tool_params(tool),
            }
    return None


def execute_agent_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool by name — auto-detects visual vs content.

    This is the single entry point for all 21 tools.
    """
    # Try visual tools first
    from admin.tools.visual_tools import execute_visual_tool
    try:
        result = execute_visual_tool(tool_name, params)
        if result and not result.get("error", "").startswith("Unknown tool"):
            return result
    except Exception:
        pass

    # Try content tools
    from admin.tools.content_tools import execute_content_tool
    try:
        result = execute_content_tool(tool_name, params)
        if result and not result.get("error", "").startswith("Unknown tool"):
            return result
    except Exception:
        pass

    return {"error": f"Unknown tool: {tool_name}"}


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL COUNTS (for quick verification)
# ═══════════════════════════════════════════════════════════════════════════════


def get_tool_counts() -> dict[str, int]:
    """Get count of tools by category."""
    visual = len(_get_visual_tools())
    content = len(_get_content_tools())
    return {
        "visual": visual,
        "content": content,
        "total": visual + content,
    }
