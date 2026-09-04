"""Social Reach — real research data for the Social Media Agent.

Wraps the FREE, zero-config channels of Agent-Reach (Web via Jina Reader,
V2EX public API) so the social agent's trend / competitor / hashtag /
audience tools return REAL data instead of LLM-only scaffolding.

Design rules (per user):
- ONLY the Social Media Agent uses this. No other agent touches it.
- Lightweight, ₹0, no Chrome, no login. Only Web + V2EX (free tiers).
- Safe: if Agent-Reach import fails or network errors, fall back to a
  structured "unavailable" dict so the agent never crashes.

Usage:
    from admin.tools.social_reach import (
        reach_trending, reach_competitor, reach_hashtags, reach_audience
    )
"""
from __future__ import annotations

from urllib.parse import quote

import logging
from typing import Any
import re

logger = logging.getLogger(__name__)


# ── Lazy Agent-Reach loader (keeps import cheap + safe) ─────────────────────
def _load_reach():
    """Return (WebChannel, V2EXChannel) or (None, None) if unavailable."""
    try:
        # Agent-Reach uses root-style imports internally (from agent_reach...).
        # Register our bundled copy under that name so those imports resolve.
        import admin.tools.agent_reach as _pkg
        import sys as _sys
        _sys.modules.setdefault("agent_reach", _pkg)
        from admin.tools.agent_reach.channels.web import WebChannel
        from admin.tools.agent_reach.channels.v2ex import V2EXChannel
        return WebChannel(), V2EXChannel()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Agent-Reach unavailable: %s", exc)
        return None, None


def _web_search(web, query: str, max_results: int = 8) -> list[dict[str, Any]]:
    """Topic-specific web search via Jina Reader (DuckDuckGo/Bing).

    Google direct returns 403; DuckDuckGo/Bing HTML via r.jina.ai works and
    returns REAL, query-specific results. We parse the returned markdown into
    a list of {title, url, snippet} instead of dumping raw text.
    Returns [] on any failure (caller falls back safely).
    """
    if web is None:
        return []
    for engine in (
        f"https://r.jina.ai/https://duckduckgo.com/html/?q={quote(query)}",
        f"https://r.jina.ai/https://www.bing.com/search?q={quote(query)}",
    ):
        try:
            text = web.read(engine)
            if not text or len(text.strip()) < 50:
                continue
            results = _parse_search_results(text, max_results)
            if results:
                return results
        except Exception as exc:
            logger.warning("web search %s failed: %s", engine, exc)
    return []


def _parse_search_results(text: str, max_results: int) -> list[dict[str, Any]]:
    """Extract {title, url, snippet} from Jina's DuckDuckGo/Bing markdown."""
    results: list[dict[str, Any]] = []
    # Split on markdown headings that start a result: '## [Title](url)'
    parts = re.split(r"\n##\s+", text)
    for part in parts[1:]:
        m = re.match(r"\[(?P<title>.+?)\]\((?P<url>https?://[^)\s]+)\)", part)
        if not m:
            continue
        title = m.group("title").strip()
        url = m.group("url").strip()
        if not title or "duckduckgo.com" in url and "uddg=" not in url and "bing.com" not in url:
            continue
        body = part[m.end():].strip()
        lines = []
        for ln in body.splitlines():
            s = ln.strip()
            if not s:
                continue
            if s.startswith("!["):
                continue
            # The snippet arrives as a markdown link: [snippet text](url).
            # The url is a duckduckgo.com/bing.com redirect, so we must test the
            # LINK TEXT, not the whole line, or we'd drop the real snippet.
            lm = re.match(r"^\[(.+?)\]\(https?://[^)]+\)\s*$", s, re.S)
            if lm:
                inner = lm.group(1).strip()
                if inner.startswith("!["):
                    continue  # image, not text
                if re.search(r"\.(com|net|org|io|co|uk|cn)\b", inner) and len(inner) < 80:
                    continue  # host label, not snippet
                if len(inner) >= 20:
                    lines.append(inner)
                continue
            # Bare redirect url line (no link text) -> skip
            if "duckduckgo.com" in s or "bing.com" in s:
                continue
            lines.append(s)
        snippet = " ".join(lines)[:240]
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


# ── Real trending topics ─────────────────────────────────────────────────────
def reach_trending(topic: str = "", platform: str = "instagram", limit: int = 10) -> dict[str, Any]:
    """Real trending discussion + formats for a topic (Web + V2EX)."""
    web, v2ex = _load_reach()
    result: dict[str, Any] = {
        "topic": topic,
        "platform": platform,
        "source": "agent-reach",
        "trending_topics": [],
        "trending_formats": ["Reels", "Carousel", "Stories", "Live"],
        "note": "",
    }
    if v2ex is None and web is None:
        result["note"] = "real-research-unavailable"
        return result

    # V2EX: only useful for its own (Chinese-tech) community nodes. When a
    # topic maps to a real V2EX node we surface it, else skip the global-noise
    # hot list so we don't pollute plumber/roofer queries with keyboard posts.
    if v2ex is not None and topic:
        node = topic.lower().replace(" ", "")
        if node in {"tech", "programmer", "python", "java", "nodejs", "create", "jobs", "share", "hardware", "golang", "design", "apple", "android"}:
            try:
                for t in v2ex.get_node_topics(node, limit=limit):
                    result["trending_topics"].append({
                        "title": t.get("title", ""),
                        "replies": t.get("replies", 0),
                        "node": t.get("node_title", ""),
                        "url": t.get("url", ""),
                    })
            except Exception as exc:
                logger.warning("V2EX trend fetch for %s failed: %s", node, exc)

    # Web: topic-specific search (DuckDuckGo via Jina) -> REAL plumber etc.
    if web is not None and topic:
        for r in _web_search(web, f"{topic} tips trends discussion", max_results=limit):
            result["trending_topics"].append({
                "title": r["title"],
                "source": "web",
                "url": r["url"],
                "snippet": r["snippet"],
            })

    if not result["trending_topics"]:
        result["note"] = "no-real-data-returned"
    return result


# ── Real competitor analysis ─────────────────────────────────────────────────
def reach_competitor(competitor: str = "", platform: str = "instagram") -> dict[str, Any]:
    """Real public info about a competitor (Web read of their page)."""
    web, _ = _load_reach()
    result: dict[str, Any] = {
        "competitor": competitor,
        "platform": platform,
        "source": "agent-reach",
        "profile": {},
        "posts_sample": [],
        "note": "",
    }
    if web is None or not competitor:
        result["note"] = "real-research-unavailable"
        return result
    # Try to read a public profile / page for the competitor
    handle = competitor.lstrip("@").replace(" ", "")
    urls = [
        f"https://www.instagram.com/{handle}/",
        f"https://www.linkedin.com/company/{handle}/",
        f"https://twitter.com/{handle}",
    ]
    for u in urls:
        try:
            text = web.read(u)
            result["profile"][u] = text[:1200].strip()
        except Exception as exc:
            logger.warning("Competitor fetch %s failed: %s", u, exc)
    if not result["profile"]:
        result["note"] = "profile-unreachable-read-only"
    return result


# ── Real hashtag research ────────────────────────────────────────────────────
def reach_hashtags(topic: str = "", platform: str = "instagram", count: int = 20) -> dict[str, Any]:
    """Real hashtag context for a topic (Web + V2EX node topics)."""
    web, v2ex = _load_reach()
    result: dict[str, Any] = {
        "topic": topic,
        "platform": platform,
        "requested_count": count,
        "categories": {
            "high_reach": "Hashtags with 1M+ posts (for visibility)",
            "medium_reach": "Hashtags with 100k-1M posts (for balance)",
            "niche": "Hashtags with 10k-100k posts (for targeting)",
            "branded": "Brand-specific hashtags",
        },
        "suggested": [],
        "note": "",
    }
    if web is None and v2ex is None:
        result["note"] = "real-research-unavailable"
        return result

    # Web: topic-specific hashtag search -> real usage context + example posts
    if web is not None and topic:
        for r in _web_search(web, f"{topic} hashtags {platform} popular", max_results=10):
            result["suggested"].append({
                "idea": r["title"],
                "url": r["url"],
                "snippet": r["snippet"],
            })

    if not result["suggested"]:
        result["note"] = "no-real-hashtags-returned"
    return result


# ── Real audience analysis ────────────────────────────────────────────────────
def reach_audience(platform: str = "instagram", industry: str = "") -> dict[str, Any]:
    """Audience dimensions for a platform + industry (real platform intel)."""
    web, _ = _load_reach()
    # Platform demographics come from the social agent's own intel (real, local).
    try:
        from admin.tools.social_tools import get_platform_intelligence
        intel = get_platform_intelligence(platform)
    except Exception:
        intel = {}
    result: dict[str, Any] = {
        "platform": platform,
        "industry": industry,
        "analysis_dimensions": ["demographics", "psychographics", "behavior", "preferences"],
        "algorithm_factors": intel.get("algorithm_factors", []),
        "best_times": intel.get("best_times", {}),
        "best_practices": intel.get("best_practices", []),
        "source": "local-intel",
        "note": "",
    }
    if web is not None and industry:
        rows = _web_search(web, f"{industry} audience demographics {platform}", max_results=3)
        if rows:
            result["web_signal"] = [
                {"title": r["title"], "snippet": r["snippet"]} for r in rows
            ]
            result["source"] = "local-intel + web"
    return result
