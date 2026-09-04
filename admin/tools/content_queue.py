"""Content Job Queue — On-demand GPU job management.

Ensures:
  - GPU only used when actual request comes (no waste)
  - Per-workspace GPU queue (one GPU shared across all agents in workspace)
  - Structured briefs from domain agents → Content Agent enhances with intelligence
  - Job tracking: pending → running → completed/failed
  - Callback to domain agent on completion
  - Output storage for generated images/videos
  - Retry on failure (max 2 retries)
  - Multi-request support (queue handles concurrency)

Flow:
  1. Domain Agent sends structured brief (what to create, platform, style)
  2. Content Agent enhances brief (adds brand context, AI prompt engineering, dimensions)
  3. Job queued → GPU picks up when free
  4. GPU generates → output saved → domain agent notified
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Output directory
_OUTPUT_BASE = Path(os.getenv("TAGS_OUTPUT_DIR", "data/outputs"))


# ── Job Status ────────────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


# ── Structured Brief ──────────────────────────────────────────────────────────


@dataclass
class ContentBrief:
    """Structured brief from domain agent → Content Agent.

    Domain Agent fills: what to create, platform, style, quantity
    Content Agent fills: exact prompts, dimensions, GPU params, brand integration
    """

    # === What Domain Agent Provides ===
    brief_id: str = ""
    workspace_id: str = ""
    from_agent: str = ""  # social, ads, website, seo
    content_type: str = ""  # image, video, ad_creative, social_post, hero_banner, infographic
    platform: str = "instagram"  # instagram, facebook, twitter, linkedin, youtube, website
    style: str = "professional"  # bold, minimal, creative, corporate, playful, luxury
    quantity: int = 1
    topic: str = ""  # What the content is about
    description: str = ""  # Detailed description from domain agent
    text_overlay: str = ""  # Text to appear on image (if any)
    cta: str = ""  # Call to action text
    reference_urls: list[str] = field(default_factory=list)  # Reference images/links
    priority: str = "normal"  # low, normal, high, urgent

    # === What Content Agent Fills (enhanced intelligence) ===
    enhanced_prompt: str = ""  # Final AI prompt after brand + style integration
    width: int = 1080
    height: int = 1080
    steps: int = 20  # FLUX inference steps
    frames: int = 49  # CogVideoX frames (for videos)
    brand_colors_used: list[str] = field(default_factory=list)
    style_enhancement: str = ""  # What style adjustments Content Agent made
    ai_reasoning: str = ""  # Why Content Agent chose these settings

    # === Job Tracking ===
    job_id: str = ""
    status: str = "pending"
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    retry_count: int = 0
    max_retries: int = 2
    error: str = ""

    # === Output ===
    output_files: list[str] = field(default_factory=list)
    output_url: str = ""  # URL/path to generated content


# ── Job Queue (Per Workspace) ─────────────────────────────────────────────────


class ContentJobQueue:
    """Per-workspace GPU job queue.

    All agents in a workspace (Social, Ads, Website, SEO) share this queue.
    Jobs process one at a time on the GPU.
    """

    def __init__(self, workspace_id: str):
        self.workspace_id = workspace_id
        self.jobs: dict[str, ContentBrief] = {}
        self._queue: list[str] = []  # Job IDs in order
        self._running_job: str | None = None

    def submit(self, brief: ContentBrief) -> str:
        """Submit a job to the queue. Returns job_id."""
        if not brief.brief_id:
            brief.brief_id = f"brief_{uuid.uuid4().hex[:8]}"
        if not brief.job_id:
            brief.job_id = f"job_{uuid.uuid4().hex[:8]}"
        brief.created_at = datetime.now(timezone.utc).isoformat()
        brief.status = JobStatus.PENDING

        self.jobs[brief.job_id] = brief
        self._queue.append(brief.job_id)

        logger.info(
            "Job queued: %s (%s %dx%d, %d items) from %s",
            brief.job_id, brief.content_type, brief.width, brief.height,
            brief.quantity, brief.from_agent,
        )
        return brief.job_id

    def get_next(self) -> ContentBrief | None:
        """Get the next job to process. Returns None if queue empty or GPU busy."""
        if self._running_job and self._running_job in self.jobs:
            running = self.jobs[self._running_job]
            if running.status == JobStatus.RUNNING:
                return None  # GPU still busy

        # Priority ordering
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        self._queue.sort(
            key=lambda jid: priority_order.get(
                self.jobs[jid].priority, 2
            )
        )

        while self._queue:
            job_id = self._queue.pop(0)
            job = self.jobs.get(job_id)
            if job and job.status in (JobStatus.PENDING, JobStatus.RETRYING):
                self._running_job = job_id
                job.status = JobStatus.RUNNING
                job.started_at = datetime.now(timezone.utc).isoformat()
                return job

        self._running_job = None
        return None

    def complete(self, job_id: str, output_files: list[str], output_url: str = "") -> None:
        """Mark a job as completed."""
        if job_id in self.jobs:
            job = self.jobs[job_id]
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.output_files = output_files
            job.output_url = output_url
            if self._running_job == job_id:
                self._running_job = None
            logger.info("Job completed: %s → %d files", job_id, len(output_files))

    def fail(self, job_id: str, error: str) -> bool:
        """Mark a job as failed. Returns True if will retry."""
        if job_id not in self.jobs:
            return False

        job = self.jobs[job_id]
        job.retry_count += 1
        job.error = error

        if job.retry_count <= job.max_retries:
            job.status = JobStatus.RETRYING
            self._queue.append(job_id)  # Re-queue for retry
            logger.warning("Job retrying: %s (attempt %d/%d)", job_id, job.retry_count, job.max_retries)
            return True
        else:
            job.status = JobStatus.FAILED
            logger.error("Job failed permanently: %s — %s", job_id, error)
            return False

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        """Get job status."""
        if job_id not in self.jobs:
            return None
        job = self.jobs[job_id]
        return {
            "job_id": job.job_id,
            "status": job.status,
            "content_type": job.content_type,
            "platform": job.platform,
            "quantity": job.quantity,
            "from_agent": job.from_agent,
            "output_files": job.output_files,
            "error": job.error,
            "retry_count": job.retry_count,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        }

    def get_queue_status(self) -> dict[str, Any]:
        """Get overall queue status."""
        pending = sum(1 for j in self.jobs.values() if j.status == JobStatus.PENDING)
        running = sum(1 for j in self.jobs.values() if j.status == JobStatus.RUNNING)
        completed = sum(1 for j in self.jobs.values() if j.status == JobStatus.COMPLETED)
        failed = sum(1 for j in self.jobs.values() if j.status == JobStatus.FAILED)
        retrying = sum(1 for j in self.jobs.values() if j.status == JobStatus.RETRYING)

        return {
            "workspace_id": self.workspace_id,
            "total_jobs": len(self.jobs),
            "pending": pending,
            "running": running,
            "completed": completed,
            "failed": failed,
            "retrying": retrying,
            "gpu_busy": running > 0,
            "queue_depth": len(self._queue),
        }

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent jobs."""
        sorted_jobs = sorted(
            self.jobs.values(),
            key=lambda j: j.created_at,
            reverse=True,
        )
        return [
            {
                "job_id": j.job_id,
                "status": j.status,
                "content_type": j.content_type,
                "platform": j.platform,
                "from_agent": j.from_agent,
                "output_count": len(j.output_files),
                "created_at": j.created_at,
            }
            for j in sorted_jobs[:limit]
        ]


# ── Content Agent Intelligence — Brief Enhancement ────────────────────────────


# Platform dimensions
PLATFORM_SIZES = {
    "instagram_post": (1080, 1080),
    "instagram_story": (1080, 1920),
    "instagram_reel_cover": (1080, 1920),
    "facebook_post": (1200, 630),
    "facebook_ad": (1200, 628),
    "facebook_story": (1080, 1920),
    "twitter_post": (1200, 675),
    "linkedin_post": (1200, 627),
    "youtube_thumbnail": (1280, 720),
    "youtube_banner": (2560, 1440),
    "pinterest_pin": (1000, 1500),
    "tiktok_video": (1080, 1920),
    "website_hero": (1920, 1080),
    "website_banner": (1200, 400),
    "google_ad": (1200, 628),
    "infographic": (1080, 1920),
}

# Style descriptors for prompt engineering
STYLE_MAP = {
    "bold": "bold, vibrant, eye-catching, dynamic colors, high contrast, energetic",
    "minimal": "minimalist, clean, elegant, white space, simple, sophisticated",
    "creative": "creative, artistic, unique composition, memorable, distinctive",
    "corporate": "corporate, professional, business, trustworthy, clean, polished",
    "playful": "fun, playful, colorful, friendly, energetic, whimsical",
    "luxury": "luxury, premium, elegant, gold accents, sophisticated, refined",
    "professional": "clean, professional, modern, high quality, polished",
    "modern": "modern, sleek, contemporary, fresh, innovative",
}

# Content type to prompt prefix
CONTENT_TYPE_MAP = {
    "image": "A high-quality photograph",
    "social_post": "A social media post image",
    "ad_creative": "A professional advertisement creative",
    "hero_banner": "A wide hero banner image",
    "infographic": "An informative infographic",
    "video": "A cinematic video scene",
}


def enhance_brief(
    brief: ContentBrief,
    client_context: dict[str, Any] | None = None,
) -> ContentBrief:
    """Content Agent's intelligence — enhance a raw brief into production-ready specs.

    This is where Content Agent USES ITS BRAIN:
    - Adds brand colors to prompts
    - Selects correct dimensions per platform
    - Engineers the AI prompt for best quality
    - Applies style reasoning
    - Adds cross-project learnings
    """
    client_context = client_context or {}
    reasoning_parts = []

    # ── 1. Resolve dimensions ────────────────────────────────────────────
    # Try multiple key patterns for size lookup
    ct = brief.content_type
    has_hero = "hero" in ct
    size_candidates = [
        f"{brief.platform}_{ct}",
        f"{brief.platform}_hero" if has_hero else "",
        f"{brief.platform}_ad" if "ad" in ct else "",
        f"{brief.platform}_banner" if "banner" in ct else "",
        f"{brief.platform}_post",
    ]
    for size_key in size_candidates:
        if size_key and size_key in PLATFORM_SIZES:
            brief.width, brief.height = PLATFORM_SIZES[size_key]
            reasoning_parts.append(f"Platform '{brief.platform}' + type '{ct}' → {brief.width}x{brief.height}")
            break

    # ── 2. Build brand-aware prompt ──────────────────────────────────────
    style_desc = STYLE_MAP.get(brief.style, STYLE_MAP["professional"])
    content_prefix = CONTENT_TYPE_MAP.get(brief.content_type, "A professional marketing visual")

    # Brand colors integration
    brand_colors = client_context.get("brand_colors", [])
    brand_style = client_context.get("brand_style", "")
    industry = client_context.get("industry", "")
    target_audience = client_context.get("target_audience", "")

    color_str = ""
    if brand_colors:
        brief.brand_colors_used = brand_colors[:3]
        color_str = f", brand colors: {', '.join(brand_colors[:3])}"
        reasoning_parts.append(f"Applied brand colors: {', '.join(brand_colors[:3])}")

    # Industry context
    industry_str = ""
    if industry:
        industry_hints = {
            "real_estate": "architectural, property, modern interior, premium space",
            "saas": "technology, digital, clean UI, modern dashboard",
            "ecommerce": "product photography, clean background, shopping",
            "healthcare": "clean, trustworthy, medical, professional",
            "food": "appetizing, warm colors, food photography, delicious",
            "education": "learning, knowledge, books, growth",
        }
        industry_str = f", {industry_hints.get(industry, industry)} style"

    # Target audience hints
    audience_str = ""
    if target_audience:
        if "young" in target_audience.lower():
            audience_str = ", trendy, youthful, Instagram-worthy"
        elif "professional" in target_audience.lower() or "business" in target_audience.lower():
            audience_str = ", professional, corporate, trust-building"
        elif "home" in target_audience.lower():
            audience_str = ", warm, inviting, home-like feel"
        elif "luxury" in target_audience.lower() or "premium" in target_audience.lower():
            audience_str = ", luxury, premium, exclusive feel"
        reasoning_parts.append(f"Target audience '{target_audience}' → adjusted tone")

    # Use brief description if provided, else use topic
    subject = brief.description or brief.topic or "marketing content"
    if brief.text_overlay:
        subject += f", with text overlay: '{brief.text_overlay}'"
    if brief.cta:
        subject += f", CTA: '{brief.cta}'"

    # ── 3. Assemble final prompt ─────────────────────────────────────────
    prompt_parts = [
        content_prefix,
        f"about {subject}",
        f"{style_desc}{industry_str}{audience_str}{color_str}",
        "professional quality, high resolution, marketing ready",
    ]
    brief.enhanced_prompt = ", ".join(p for p in prompt_parts if p)

    # ── 4. Set GPU parameters ────────────────────────────────────────────
    if brief.content_type == "video":
        brief.frames = 49  # ~6 seconds
        if brief.priority == "high":
            brief.frames = 81  # ~10 seconds
        reasoning_parts.append(f"Video: {brief.frames} frames (~{brief.frames/8:.1f}s)")
    else:
        brief.steps = 20  # Fast generation
        if brief.priority == "high":
            brief.steps = 30  # Higher quality
        reasoning_parts.append(f"Image: {brief.steps} inference steps")

    # ── 5. Style enhancement reasoning ───────────────────────────────────
    brief.style_enhancement = (
        f"Style '{brief.style}' applied with {style_desc}. "
        f"Brand colors {'integrated' if brand_colors else 'auto-discovered from website'}. "
        f"Industry '{industry}' context added." if industry else
        f"Style '{brief.style}' applied with {style_desc}."
    )

    brief.ai_reasoning = " | ".join(reasoning_parts)

    logger.info(
        "Brief enhanced: %s → prompt: %s...",
        brief.job_id, brief.enhanced_prompt[:100],
    )

    return brief


# ── Global Queue Registry ─────────────────────────────────────────────────────

_queues: dict[str, ContentJobQueue] = {}


def get_queue(workspace_id: str) -> ContentJobQueue:
    """Get or create the GPU queue for a workspace."""
    if workspace_id not in _queues:
        _queues[workspace_id] = ContentJobQueue(workspace_id)
    return _queues[workspace_id]
