"""Agency Content Agent — Cross-Project Learning Collector with Persistence.

Interview Q11 (Two-tier):
  "Agency-level Content Agent is like a senior creative director who's seen
   hundreds of projects. Workspace Content Agent is the executor for that
   specific client."

Interview Q27 (Cross-project learning):
  "If I learn that certain prompts work better for real estate vs. SaaS,
   I document that. New workspace gets benefit of that learning."

Architecture:
  - Singleton at agency level (shared across all workspaces)
  - Collects reports from ALL workspace Content Agents
  - Aggregates learnings: prompt patterns, brand insights, platform performance
  - Shares accumulated knowledge with new workspace Content Agents
  - Persists data to JSON file for cross-restart survival

Reporting flow:
  Workspace Content Agent --report--> Agency Content Agent
  Agency Content Agent --knowledge--> New Workspace Content Agent
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Persistence path
_PERSIST_DIR = Path(os.getenv("TAGS_DATA_DIR", "data"))
_PERSIST_FILE = _PERSIST_DIR / "agency_content_agent.json"


# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class ContentReport:
    """A report from a Workspace Content Agent after completing visual work."""

    report_id: str = ""
    workspace_name: str = ""
    client_name: str = ""
    brief_from: str = ""  # Which domain agent gave the brief
    brief_summary: str = ""  # What was requested
    deliverables: list[str] = field(default_factory=list)  # What was created
    tools_used: list[str] = field(default_factory=list)  # Which tools were used
    prompts_used: list[str] = field(default_factory=list)  # Prompts that worked
    brand_discovered: dict[str, Any] = field(default_factory=dict)  # Brand info
    platform: str = ""  # instagram, facebook, etc.
    visual_type: str = ""  # image, video, ad, social
    gpu_minutes: float = 0.0  # GPU time used
    success: bool = True
    error: str = ""
    learnings: list[str] = field(default_factory=list)  # What worked/didn't
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class PromptPattern:
    """A learned prompt pattern that works well for specific contexts."""

    pattern_id: str = ""
    visual_type: str = ""  # image, video, ad, social
    platform: str = ""  # instagram, facebook, etc.
    industry: str = ""  # real estate, saas, ecommerce, etc.
    prompt_template: str = ""
    success_count: int = 0
    avg_quality: float = 0.0  # 1-10 scale
    notes: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class BrandInsight:
    """Learned brand/visual insight from a workspace."""

    insight_id: str = ""
    industry: str = ""
    insight_type: str = ""  # color_trend, style_pattern, platform_preference
    insight: str = ""
    evidence_count: int = 1  # How many workspaces confirmed this
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Agency Content Agent (Singleton) ─────────────────────────────────────────


class AgencyContentAgent:
    """Senior Creative Director — accumulates cross-project visual learnings.

    This is a singleton at the agency level. All workspace Content Agents
    report their learnings here. New workspaces inherit accumulated knowledge.

    Data persists to JSON file for cross-restart survival.
    """

    _instance: AgencyContentAgent | None = None

    def __new__(cls) -> AgencyContentAgent:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Cross-project learnings
        self.reports: list[ContentReport] = []
        self.prompt_patterns: list[PromptPattern] = []
        self.brand_insights: list[BrandInsight] = []
        self.platform_stats: dict[str, dict[str, Any]] = {}
        self.industry_stats: dict[str, dict[str, Any]] = {}

        # Load persisted data if exists
        self._load()

        logger.info(
            "Agency Content Agent initialized (singleton, %d reports, %d patterns loaded)",
            len(self.reports), len(self.prompt_patterns),
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load persisted data from JSON file."""
        if not _PERSIST_FILE.exists():
            return
        try:
            data = json.loads(_PERSIST_FILE.read_text(encoding="utf-8"))
            self.reports = [ContentReport(**r) for r in data.get("reports", [])]
            self.prompt_patterns = [PromptPattern(**p) for p in data.get("prompt_patterns", [])]
            self.brand_insights = [BrandInsight(**b) for b in data.get("brand_insights", [])]
            self.platform_stats = data.get("platform_stats", {})
            self.industry_stats = data.get("industry_stats", {})
            logger.info("Loaded persisted agency content data from %s", _PERSIST_FILE)
        except Exception as e:
            logger.warning("Failed to load persisted agency content data: %s", e)

    def _save(self) -> None:
        """Persist data to JSON file."""
        try:
            _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "reports": [asdict(r) for r in self.reports[-200:]],  # Keep last 200
                "prompt_patterns": [asdict(p) for p in self.prompt_patterns],
                "brand_insights": [asdict(b) for b in self.brand_insights],
                "platform_stats": self.platform_stats,
                "industry_stats": self.industry_stats,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            _PERSIST_FILE.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to persist agency content data: %s", e)

    # ── Receive Report from Workspace ─────────────────────────────────────

    def receive_report(self, report: ContentReport) -> dict[str, Any]:
        """Receive a report from a Workspace Content Agent.

        Called after the workspace agent completes visual work.
        Extracts learnings and updates cross-project knowledge.
        Persists to disk after each report.
        """
        self.reports.append(report)

        # Update platform stats
        if report.platform:
            if report.platform not in self.platform_stats:
                self.platform_stats[report.platform] = {
                    "total_jobs": 0,
                    "total_gpu_minutes": 0.0,
                    "success_rate": 1.0,
                    "common_visual_types": {},
                }
            ps = self.platform_stats[report.platform]
            ps["total_jobs"] += 1
            ps["total_gpu_minutes"] += report.gpu_minutes
            if report.visual_type:
                ps["common_visual_types"][report.visual_type] = (
                    ps["common_visual_types"].get(report.visual_type, 0) + 1
                )

        # Extract prompt patterns from successful jobs
        if report.success and report.prompts_used:
            for prompt in report.prompts_used:
                existing = self._find_similar_pattern(
                    prompt, report.visual_type, report.platform
                )
                if existing:
                    existing.success_count += 1
                else:
                    self.prompt_patterns.append(
                        PromptPattern(
                            pattern_id=f"pp_{len(self.prompt_patterns)+1}",
                            visual_type=report.visual_type,
                            platform=report.platform,
                            industry=self._guess_industry(report.client_name),
                            prompt_template=prompt,
                            success_count=1,
                        )
                    )

        # Extract brand insights
        if report.brand_discovered and report.brand_discovered.get("colors"):
            industry = self._guess_industry(report.client_name)
            self.brand_insights.append(
                BrandInsight(
                    insight_id=f"bi_{len(self.brand_insights)+1}",
                    industry=industry,
                    insight_type="color_trend",
                    insight=f"Colors used: {report.brand_discovered['colors'][:3]}",
                )
            )

        # Extract and store failure patterns from unsuccessful jobs
        if not report.success:
            industry = self._guess_industry(report.client_name)
            # Store as a brand insight of type "failure_pattern" for avoidance
            failure_insight_parts: list[str] = []
            if report.error:
                failure_insight_parts.append(f"Error: {report.error[:200]}")
            if report.learnings:
                for learning in report.learnings:
                    failure_insight_parts.append(f"Learning: {learning[:200]}")
            if failure_insight_parts:
                self.brand_insights.append(
                    BrandInsight(
                        insight_id=f"fi_{len(self.brand_insights)+1}",
                        industry=industry,
                        insight_type="failure_pattern",
                        insight=" | ".join(failure_insight_parts),
                    )
                )

        # Update industry stats
        industry = self._guess_industry(report.client_name)
        if industry not in self.industry_stats:
            self.industry_stats[industry] = {
                "total_jobs": 0,
                "platforms_used": [],
                "visual_types_used": [],
            }
        istat = self.industry_stats[industry]
        istat["total_jobs"] += 1
        if report.platform and report.platform not in istat["platforms_used"]:
            istat["platforms_used"].append(report.platform)
        if report.visual_type and report.visual_type not in istat["visual_types_used"]:
            istat["visual_types_used"].append(report.visual_type)

        # Persist after each report
        self._save()

        logger.info(
            "Agency Content Agent received report from workspace '%s': "
            "%d deliverables, %d prompts, %d learnings",
            report.workspace_name,
            len(report.deliverables),
            len(report.prompts_used),
            len(report.learnings),
        )

        return {
            "status": "received",
            "report_id": report.report_id,
            "total_reports": len(self.reports),
            "patterns_updated": len(report.prompts_used),
        }

    # ── Provide Knowledge to New Workspace ────────────────────────────────

    def get_knowledge_for_workspace(
        self,
        industry: str = "",
        platform: str = "",
        visual_type: str = "",
    ) -> dict[str, Any]:
        """Provide accumulated knowledge to a workspace Content Agent.

        Called when a new workspace Content Agent starts up or when
        it needs cross-project insights.
        """
        result: dict[str, Any] = {
            "total_reports": len(self.reports),
            "total_patterns": len(self.prompt_patterns),
            "total_insights": len(self.brand_insights),
            "prompt_patterns": [],
            "brand_insights": [],
            "platform_tips": [],
            "industry_tips": [],
        }

        # Filter prompt patterns by criteria
        for pp in self.prompt_patterns:
            match = True
            if visual_type and pp.visual_type != visual_type:
                match = False
            if platform and pp.platform != platform:
                match = False
            if industry and pp.industry != industry:
                match = False
            if match:
                result["prompt_patterns"].append(
                    {
                        "visual_type": pp.visual_type,
                        "platform": pp.platform,
                        "prompt_template": pp.prompt_template,
                        "success_count": pp.success_count,
                    }
                )

        # Filter brand insights by industry
        if industry:
            for bi in self.brand_insights:
                if bi.industry == industry:
                    result["brand_insights"].append(
                        {
                            "type": bi.insight_type,
                            "insight": bi.insight,
                            "evidence": bi.evidence_count,
                        }
                    )

        # Platform tips from stats
        if platform and platform in self.platform_stats:
            ps = self.platform_stats[platform]
            result["platform_tips"].append(
                {
                    "total_jobs": ps["total_jobs"],
                    "avg_gpu_minutes": ps["total_gpu_minutes"] / max(ps["total_jobs"], 1),
                    "common_visual_types": ps["common_visual_types"],
                }
            )

        # Industry tips
        if industry and industry in self.industry_stats:
            istat = self.industry_stats[industry]
            result["industry_tips"].append(
                {
                    "total_jobs": istat["total_jobs"],
                    "platforms_used": istat["platforms_used"],
                    "visual_types_used": istat["visual_types_used"],
                }
            )

        return result

    # ── Get Best Prompts ──────────────────────────────────────────────────

    def get_best_prompts(
        self,
        visual_type: str = "",
        platform: str = "",
        top_n: int = 5,
    ) -> list[dict[str, Any]]:
        """Get top-performing prompt templates for a visual type/platform."""
        candidates = self.prompt_patterns

        if visual_type:
            candidates = [p for p in candidates if p.visual_type == visual_type]
        if platform:
            candidates = [p for p in candidates if p.platform == platform]

        # Sort by success count
        candidates.sort(key=lambda p: p.success_count, reverse=True)

        return [
            {
                "prompt_template": p.prompt_template,
                "success_count": p.success_count,
                "visual_type": p.visual_type,
                "platform": p.platform,
                "industry": p.industry,
            }
            for p in candidates[:top_n]
        ]

    # ── Dashboard Stats ───────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Get agency-level content stats for the dashboard."""
        return {
            "total_reports": len(self.reports),
            "total_prompt_patterns": len(self.prompt_patterns),
            "total_brand_insights": len(self.brand_insights),
            "platforms_active": list(self.platform_stats.keys()),
            "industries_served": list(self.industry_stats.keys()),
            "platform_stats": {
                k: {
                    "jobs": v["total_jobs"],
                    "gpu_minutes": round(v["total_gpu_minutes"], 1),
                }
                for k, v in self.platform_stats.items()
            },
            "industry_stats": {
                k: {
                    "jobs": v["total_jobs"],
                    "platforms": v["platforms_used"],
                }
                for k, v in self.industry_stats.items()
            },
            "recent_reports": [
                {
                    "workspace": r.workspace_name,
                    "client": r.client_name,
                    "brief_from": r.brief_from,
                    "deliverables": len(r.deliverables),
                    "tools_used": r.tools_used,
                    "success": r.success,
                    "timestamp": r.timestamp,
                }
                for r in self.reports[-10:]
            ],
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    def _find_similar_pattern(
        self, prompt: str, visual_type: str, platform: str
    ) -> PromptPattern | None:
        """Find an existing prompt pattern that's similar enough to merge."""
        for pp in self.prompt_patterns:
            if pp.visual_type == visual_type and pp.platform == platform:
                prompt_words = set(prompt.lower().split())
                pp_words = set(pp.prompt_template.lower().split())
                overlap = len(prompt_words & pp_words)
                if overlap >= 3:
                    return pp
        return None

    def _guess_industry(self, client_name: str) -> str:
        """Guess industry from client name. Will improve with more data."""
        client_lower = client_name.lower()
        if any(w in client_lower for w in ["realestate", "real estate", "property", "home"]):
            return "real_estate"
        if any(w in client_lower for w in ["saas", "tech", "app", "software"]):
            return "saas"
        if any(w in client_lower for w in ["shop", "store", "ecommerce", "retail"]):
            return "ecommerce"
        if any(w in client_lower for w in ["health", "med", "clinic", "doctor"]):
            return "healthcare"
        if any(w in client_lower for w in ["food", "restaurant", "cafe", "delivery"]):
            return "food"
        if any(w in client_lower for w in ["edu", "learn", "school", "course"]):
            return "education"
        return "general"


# ── Module-level singleton accessor ───────────────────────────────────────────

_agency_content_agent: AgencyContentAgent | None = None


def get_agency_content_agent() -> AgencyContentAgent:
    """Get the singleton Agency Content Agent."""
    global _agency_content_agent
    if _agency_content_agent is None:
        _agency_content_agent = AgencyContentAgent()
    return _agency_content_agent


# ═══════════════════════════════════════════════════════════════════════════════
# EXTENDED AGENT METHODS (industry/platform insights, failure patterns)
# ═══════════════════════════════════════════════════════════════════════════════


def _get_industry_insights(self: AgencyContentAgent, industry: str) -> dict[str, Any]:
    """Return all accumulated knowledge for a specific industry."""
    result: dict[str, Any] = {
        "industry": industry,
        "total_jobs": 0,
        "platforms_used": [],
        "visual_types_used": [],
        "prompt_patterns": [],
        "brand_insights": [],
        "failure_patterns": [],
    }

    # Industry stats
    if industry in self.industry_stats:
        istat = self.industry_stats[industry]
        result["total_jobs"] = istat["total_jobs"]
        result["platforms_used"] = istat.get("platforms_used", [])
        result["visual_types_used"] = istat.get("visual_types_used", [])

    # Prompt patterns for this industry
    for pp in self.prompt_patterns:
        if pp.industry == industry:
            result["prompt_patterns"].append({
                "prompt_template": pp.prompt_template,
                "visual_type": pp.visual_type,
                "platform": pp.platform,
                "success_count": pp.success_count,
                "avg_quality": pp.avg_quality,
            })

    # Brand insights for this industry
    for bi in self.brand_insights:
        if bi.industry == industry:
            result["brand_insights"].append({
                "type": bi.insight_type,
                "insight": bi.insight,
                "evidence_count": bi.evidence_count,
            })

    # Failure patterns for this industry
    for report in self.reports:
        if not report.success and self._guess_industry(report.client_name) == industry:
            result["failure_patterns"].append({
                "error": report.error,
                "platform": report.platform,
                "visual_type": report.visual_type,
                "learnings": report.learnings,
                "brief_summary": report.brief_summary[:100],
            })

    return result


def _get_platform_insights(self: AgencyContentAgent, platform: str) -> dict[str, Any]:
    """Return accumulated knowledge for a specific platform."""
    result: dict[str, Any] = {
        "platform": platform,
        "total_jobs": 0,
        "gpu_minutes": 0.0,
        "avg_gpu_minutes": 0.0,
        "common_visual_types": {},
        "prompt_patterns": [],
        "failure_patterns": [],
    }

    # Platform stats
    if platform in self.platform_stats:
        ps = self.platform_stats[platform]
        result["total_jobs"] = ps["total_jobs"]
        result["gpu_minutes"] = ps["total_gpu_minutes"]
        result["avg_gpu_minutes"] = (
            ps["total_gpu_minutes"] / max(ps["total_jobs"], 1)
        )
        result["common_visual_types"] = ps.get("common_visual_types", {})

    # Prompt patterns for this platform
    for pp in self.prompt_patterns:
        if pp.platform == platform:
            result["prompt_patterns"].append({
                "prompt_template": pp.prompt_template,
                "visual_type": pp.visual_type,
                "industry": pp.industry,
                "success_count": pp.success_count,
                "avg_quality": pp.avg_quality,
            })

    # Failure patterns for this platform
    for report in self.reports:
        if not report.success and report.platform == platform:
            result["failure_patterns"].append({
                "error": report.error,
                "visual_type": report.visual_type,
                "learnings": report.learnings,
                "brief_summary": report.brief_summary[:100],
            })

    return result


def _get_failure_patterns(self: AgencyContentAgent) -> list[dict[str, Any]]:
    """Return all failure patterns across all workspaces to avoid repeating mistakes."""
    patterns: list[dict[str, Any]] = []

    for report in self.reports:
        if not report.success:
            pattern: dict[str, Any] = {
                "workspace": report.workspace_name,
                "client": report.client_name,
                "error": report.error,
                "platform": report.platform,
                "visual_type": report.visual_type,
                "brief_summary": report.brief_summary[:100],
                "learnings": report.learnings,
                "timestamp": report.timestamp,
            }
            patterns.append(pattern)

    return patterns


def _get_best_prompts_for(
    self: AgencyContentAgent,
    industry: str = "",
    platform: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return best performing prompts filtered by industry and/or platform."""
    candidates = self.prompt_patterns

    if industry:
        candidates = [p for p in candidates if p.industry == industry]
    if platform:
        candidates = [p for p in candidates if p.platform == platform]

    # Sort by success_count descending, then avg_quality descending
    candidates.sort(key=lambda p: (p.success_count, p.avg_quality), reverse=True)

    return [
        {
            "prompt_template": p.prompt_template,
            "success_count": p.success_count,
            "avg_quality": p.avg_quality,
            "visual_type": p.visual_type,
            "platform": p.platform,
            "industry": p.industry,
        }
        for p in candidates[:limit]
    ]


# ── Monkey-patch methods onto AgencyContentAgent ──────────────────────────────

AgencyContentAgent.get_industry_insights = _get_industry_insights  # type: ignore[attr-defined]
AgencyContentAgent.get_platform_insights = _get_platform_insights  # type: ignore[attr-defined]
AgencyContentAgent.get_failure_patterns = _get_failure_patterns  # type: ignore[attr-defined]
AgencyContentAgent.get_best_prompts_for = _get_best_prompts_for  # type: ignore[attr-defined]
