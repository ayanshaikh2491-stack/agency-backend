"""Workspace Content Agent Store — Per-workspace Content Agent with own memory.

When a new workspace is created, it gets its own Content Agent with:
  - Its own memory (what it created, what worked, what failed)
  - Its own history (all briefs received, all outputs generated)
  - Brand learnings specific to that client
  - Cross-project knowledge from Agency Content Agent

When workspace Content Agent completes work:
  - Reports SUCCESS to Agency Content Agent (what worked)
  - Reports FAILURE to Agency Content Agent (what failed, what to avoid)
  - Saves its own history for future reference

When a new workspace starts:
  - Gets Agency Content Agent's accumulated knowledge
  - Starts fresh memory for this client
  - Can reference past learnings for same industry
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

# Persistence
_DATA_DIR = Path(os.getenv("TAGS_DATA_DIR", "data"))
_AGENTS_DIR = _DATA_DIR / "workspace_content_agents"


# ── Workspace Content Agent Memory ────────────────────────────────────────────


@dataclass
class ContentAgentMemory:
    """Per-workspace Content Agent memory — tracks everything it does."""

    workspace_id: str = ""
    workspace_name: str = ""
    client_name: str = ""
    industry: str = ""

    # History of all work done
    briefs_received: list[dict[str, Any]] = field(default_factory=list)
    outputs_generated: list[dict[str, Any]] = field(default_factory=list)
    prompts_used: list[dict[str, Any]] = field(default_factory=list)

    # Success/Failure tracking
    successes: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    # Brand learnings (learned over time for this client)
    brand_learnings: list[str] = field(default_factory=list)

    # Platform performance (what works on which platform)
    platform_performance: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Industry tips (learned from this client)
    industry_tips: list[str] = field(default_factory=list)

    # What mistakes to avoid (learned from failures)
    mistakes_to_avoid: list[str] = field(default_factory=list)

    # Timestamps
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_active: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_jobs: int = 0
    success_count: int = 0
    failure_count: int = 0

    # Agency knowledge received (from previous projects)
    agency_knowledge_received: list[dict[str, Any]] = field(default_factory=list)

    # Variation history (tracks all variations generated per brief)
    variations_history: list[dict[str, Any]] = field(default_factory=list)
    # Quality scores for generated content (0.0 - 1.0 scale)
    quality_scores: list[float] = field(default_factory=list)
    # Best performing prompts ranked by quality score
    best_prompts: list[dict[str, Any]] = field(default_factory=list)
    # Styles that worked well for this workspace
    style_history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Store Manager ─────────────────────────────────────────────────────────────


class WorkspaceContentStore:
    """Manages per-workspace Content Agent memories.

    Creates, loads, saves, and provides workspace-specific memory.
    """

    def __init__(self):
        self._memories: dict[str, ContentAgentMemory] = {}
        self._ensure_dir()
        self._load_all()

    def _ensure_dir(self):
        _AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    def _mem_file(self, workspace_id: str) -> Path:
        return _AGENTS_DIR / f"{workspace_id}.json"

    def _load_all(self):
        """Load all workspace memories from disk."""
        if not _AGENTS_DIR.exists():
            return
        for f in _AGENTS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                mem = ContentAgentMemory(**data)
                self._memories[mem.workspace_id] = mem
            except Exception as e:
                logger.warning("Failed to load memory from %s: %s", f.name, e)

    def _save(self, workspace_id: str):
        """Save a workspace memory to disk."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return
        try:
            filepath = self._mem_file(workspace_id)
            filepath.write_text(
                json.dumps(mem.to_dict(), indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save memory for %s: %s", workspace_id, e)

    def get_or_create(self, workspace_id: str, workspace_name: str = "",
                      client_name: str = "", industry: str = "") -> ContentAgentMemory:
        """Get existing memory or create new one for a workspace."""
        if workspace_id in self._memories:
            mem = self._memories[workspace_id]
            mem.last_active = datetime.now(timezone.utc).isoformat()
            self._save(workspace_id)
            return mem

        # Create new
        mem = ContentAgentMemory(
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            client_name=client_name,
            industry=industry,
        )
        self._memories[workspace_id] = mem
        self._save(workspace_id)

        logger.info(
            "Created new Content Agent memory for workspace '%s' (client: %s, industry: %s)",
            workspace_name, client_name, industry,
        )
        return mem

    # ── Record Events ─────────────────────────────────────────────────────

    def record_brief_received(self, workspace_id: str, brief: dict[str, Any]) -> None:
        """Record when this workspace's Content Agent receives a brief."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return
        mem.briefs_received.append({
            **brief,
            "received_at": datetime.now(timezone.utc).isoformat(),
        })
        mem.total_jobs += 1
        mem.last_active = datetime.now(timezone.utc).isoformat()
        self._save(workspace_id)

    def record_success(self, workspace_id: str, job_id: str, brief_summary: str,
                       deliverables: list[str], prompts_used: list[str],
                       platform: str, visual_type: str, gpu_minutes: float,
                       learnings: list[str] | None = None) -> None:
        """Record a successful job — report to Agency Content Agent."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return

        success_record = {
            "job_id": job_id,
            "brief_summary": brief_summary,
            "deliverables": deliverables,
            "prompts_used": prompts_used,
            "platform": platform,
            "visual_type": visual_type,
            "gpu_minutes": gpu_minutes,
            "learnings": learnings or [],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        mem.successes.append(success_record)
        mem.success_count += 1
        mem.last_active = datetime.now(timezone.utc).isoformat()

        # Track platform performance
        if platform:
            if platform not in mem.platform_performance:
                mem.platform_performance[platform] = {
                    "total_jobs": 0, "successes": 0, "failures": 0,
                    "avg_gpu_minutes": 0.0, "common_types": {},
                }
            pp = mem.platform_performance[platform]
            pp["total_jobs"] += 1
            pp["successes"] += 1
            pp["avg_gpu_minutes"] = (
                (pp["avg_gpu_minutes"] * (pp["successes"] - 1) + gpu_minutes)
                / pp["successes"]
            )
            if visual_type:
                pp["common_types"][visual_type] = pp["common_types"].get(visual_type, 0) + 1

        # Add learnings
        for learning in (learnings or []):
            if learning not in mem.brand_learnings:
                mem.brand_learnings.append(learning)

        self._save(workspace_id)

        # Report to Agency Content Agent
        self._report_to_agency(workspace_id, success_record, is_success=True)

    def record_failure(self, workspace_id: str, job_id: str, brief_summary: str,
                       error: str, platform: str = "", visual_type: str = "",
                       what_failed: str = "", avoid_next_time: str = "") -> None:
        """Record a failed job — report to Agency Content Agent so it learns what to avoid."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return

        failure_record = {
            "job_id": job_id,
            "brief_summary": brief_summary,
            "error": error,
            "platform": platform,
            "visual_type": visual_type,
            "what_failed": what_failed,
            "avoid_next_time": avoid_next_time,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        mem.failures.append(failure_record)
        mem.failure_count += 1
        mem.last_active = datetime.now(timezone.utc).isoformat()

        # Add to mistakes_to_avoid
        if avoid_next_time and avoid_next_time not in mem.mistakes_to_avoid:
            mem.mistakes_to_avoid.append(avoid_next_time)

        # Track platform failures
        if platform:
            if platform not in mem.platform_performance:
                mem.platform_performance[platform] = {
                    "total_jobs": 0, "successes": 0, "failures": 0,
                    "avg_gpu_minutes": 0.0, "common_types": {},
                }
            mem.platform_performance[platform]["total_jobs"] += 1
            mem.platform_performance[platform]["failures"] += 1

        self._save(workspace_id)

        # Report to Agency Content Agent
        self._report_to_agency(workspace_id, failure_record, is_success=False)

    def record_agency_knowledge(self, workspace_id: str, knowledge: dict[str, Any]) -> None:
        """Record agency knowledge received by this workspace Content Agent."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return
        mem.agency_knowledge_received.append({
            **knowledge,
            "received_at": datetime.now(timezone.utc).isoformat(),
        })
        self._save(workspace_id)

    # ── Agency Reporting ──────────────────────────────────────────────────

    def _report_to_agency(self, workspace_id: str, record: dict[str, Any],
                          is_success: bool) -> None:
        """Report success or failure to Agency Content Agent."""
        try:
            from admin.agency.content_agent import (
                get_agency_content_agent, ContentReport,
            )
            agency = get_agency_content_agent()
            mem = self._memories.get(workspace_id, ContentAgentMemory())

            report = ContentReport(
                report_id=f"rpt_{workspace_id}_{mem.total_jobs}",
                workspace_name=mem.workspace_name,
                client_name=mem.client_name,
                brief_from="workspace_content_agent",
                brief_summary=record.get("brief_summary", ""),
                deliverables=record.get("deliverables", []),
                tools_used=[],
                prompts_used=record.get("prompts_used", []),
                brand_discovered={},
                platform=record.get("platform", ""),
                visual_type=record.get("visual_type", ""),
                gpu_minutes=record.get("gpu_minutes", 0.0),
                success=is_success,
                error=record.get("error", ""),
                learnings=record.get("learnings", []) if is_success else [
                    f"FAILED: {record.get('what_failed', '')}",
                    f"AVOID: {record.get('avoid_next_time', '')}",
                ],
            )
            agency.receive_report(report)

            logger.info(
                "Reported %s to Agency Content Agent from workspace '%s': %s",
                "SUCCESS" if is_success else "FAILURE",
                mem.workspace_name,
                record.get("brief_summary", "")[:80],
            )
        except Exception as e:
            logger.warning("Failed to report to Agency Content Agent: %s", e)

    # ── Get Memory for Content Agent ──────────────────────────────────────

    def get_memory_summary(self, workspace_id: str) -> str:
        """Get a human-readable memory summary for the Content Agent's system prompt."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return ""

        parts = []

        # Success rate
        total = mem.success_count + mem.failure_count
        if total > 0:
            rate = (mem.success_count / total) * 100
            parts.append(f"Success rate: {rate:.0f}% ({mem.success_count}/{total} jobs)")

        # Brand learnings
        if mem.brand_learnings:
            parts.append(f"Brand learnings: {'; '.join(mem.brand_learnings[:5])}")

        # Mistakes to avoid
        if mem.mistakes_to_avoid:
            parts.append(f"MISTAKES TO AVOID: {'; '.join(mem.mistakes_to_avoid[:3])}")

        # Platform performance
        for platform, stats in mem.platform_performance.items():
            if stats["total_jobs"] > 0:
                parts.append(
                    f"{platform}: {stats['successes']}/{stats['total_jobs']} success, "
                    f"avg {stats['avg_gpu_minutes']:.1f} min GPU"
                )

        # Industry tips
        if mem.industry_tips:
            parts.append(f"Industry tips: {'; '.join(mem.industry_tips[:3])}")

        # Style memory
        if mem.style_history:
            parts.append(f"Best styles: {'; '.join(mem.style_history[:5])}")

        # Best prompts
        if mem.best_prompts:
            top = mem.best_prompts[:3]
            prompt_summaries = [
                f"\"{p['prompt'][:60]}\" (score: {p['quality_score']:.2f})"
                for p in top
            ]
            parts.append(f"Top prompts: {'; '.join(prompt_summaries)}")

        # Variation count
        if mem.variations_history:
            total_variations = sum(len(v.get("variations", [])) for v in mem.variations_history)
            parts.append(f"Total variations generated: {total_variations}")

        if not parts:
            return ""

        return "\n## WORKSPACE CONTENT AGENT MEMORY\n" + "\n".join(f"- {p}" for p in parts)

    def get_stats(self, workspace_id: str) -> dict[str, Any]:
        """Get workspace Content Agent stats."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return {}
        return {
            "workspace_id": mem.workspace_id,
            "total_jobs": mem.total_jobs,
            "success_count": mem.success_count,
            "failure_count": mem.failure_count,
            "success_rate": (mem.success_count / max(mem.total_jobs, 1)) * 100,
            "brand_learnings_count": len(mem.brand_learnings),
            "mistakes_to_avoid_count": len(mem.mistakes_to_avoid),
            "platforms_used": list(mem.platform_performance.keys()),
            "last_active": mem.last_active,
        }

    def list_all_stats(self) -> list[dict[str, Any]]:
        """Get stats for all workspace Content Agents."""
        return [self.get_stats(wid) for wid in self._memories]

    # ── Variation Tracking ──────────────────────────────────────────────────

    def record_variation(
        self,
        workspace_id: str,
        brief_summary: str,
        variations: list[dict[str, Any]],
        quality_scores: list[float] | None = None,
    ) -> None:
        """Record variations generated for a brief and their quality scores.

        Args:
            workspace_id: Target workspace.
            brief_summary: Summary of the brief.
            variations: List of variation dicts with prompt, file, settings etc.
            quality_scores: Optional parallel list of scores (0.0-1.0).
        """
        mem = self._memories.get(workspace_id)
        if not mem:
            return

        entry = {
            "brief_summary": brief_summary,
            "variations": variations,
            "quality_scores": quality_scores or [],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        mem.variations_history.append(entry)

        # Merge quality scores into global quality_scores
        if quality_scores:
            mem.quality_scores.extend(quality_scores)

            # Update best_prompts: pair prompts with their scores and keep top
            for i, variation in enumerate(variations):
                if i < len(quality_scores) and quality_scores[i] > 0.0:
                    mem.best_prompts.append({
                        "prompt": variation.get("prompt", ""),
                        "quality_score": quality_scores[i],
                        "platform": variation.get("platform", ""),
                        "brief_summary": brief_summary,
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                    })
            # Keep only top 50 best prompts sorted by score descending
            mem.best_prompts.sort(key=lambda x: x["quality_score"], reverse=True)
            mem.best_prompts = mem.best_prompts[:50]

        mem.last_active = datetime.now(timezone.utc).isoformat()
        self._save(workspace_id)

    def record_style_preference(
        self,
        workspace_id: str,
        style_name: str,
        score: float = 1.0,
    ) -> None:
        """Track which visual styles the client responds well to.

        Args:
            workspace_id: Target workspace.
            style_name: Style identifier (e.g. "minimal", "bold", "flat").
            score: Preference score (0.0 = bad, 1.0 = great).
        """
        mem = self._memories.get(workspace_id)
        if not mem:
            return

        # Add to style_history if score is positive and style not yet listed
        if score >= 0.5 and style_name not in mem.style_history:
            mem.style_history.append(style_name)

        # Also store structured style data in best_prompts as a style entry
        if score >= 0.5:
            mem.best_prompts.append({
                "prompt": f"[STYLE] {style_name}",
                "quality_score": score,
                "platform": "",
                "brief_summary": f"Style preference: {style_name}",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            })
            mem.best_prompts.sort(key=lambda x: x["quality_score"], reverse=True)
            mem.best_prompts = mem.best_prompts[:50]

        mem.last_active = datetime.now(timezone.utc).isoformat()
        self._save(workspace_id)

    def get_best_prompts(
        self, workspace_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Return top prompts by quality score for this workspace."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return []
        return [
            {k: v for k, v in p.items() if k != "recorded_at"}
            for p in mem.best_prompts[:limit]
        ]

    def get_style_memory(self, workspace_id: str) -> list[str]:
        """Return the list of styles that worked best for this workspace."""
        mem = self._memories.get(workspace_id)
        if not mem:
            return []
        return list(mem.style_history)


# ── Global Store ──────────────────────────────────────────────────────────────

_store: WorkspaceContentStore | None = None


def get_content_store() -> WorkspaceContentStore:
    global _store
    if _store is None:
        _store = WorkspaceContentStore()
    return _store
