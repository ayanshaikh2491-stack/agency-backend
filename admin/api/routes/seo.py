"""SEO Agent API routes — dedicated endpoints for SEO operations.

Endpoints:
  POST /api/seo/chat                  — Chat with SEO agent
  POST /api/seo/audit                 — Run site audit (no agent needed)
  GET  /api/seo/audits                — List saved audits
  POST /api/seo/keywords              — Run keyword research
  POST /api/seo/check                 — On-page SEO check (no agent needed)
  POST /api/seo/sitemap               — Parse sitemap
  POST /api/seo/robots                — Parse robots.txt
  POST /api/seo/serp                  — Check SERP for keyword
  GET  /api/seo/tracked               — List tracked keywords
  POST /api/seo/tracked               — Add tracked keyword
  DELETE /api/seo/tracked/{kid}       — Remove tracked keyword
  GET  /api/seo/reports               — List SEO reports
  POST /api/seo/reports               — Save SEO report
  GET  /api/seo/skills                — List SEO skills
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from admin.api.models.schemas import ChatResponse
from admin.tools.seo_tools import (
    keyword_research,
    onpage_check,
    parse_robots_txt,
    parse_sitemap,
    serp_check,
    site_audit,
    generate_meta_tags,
    generate_schema,
    fix_audit_issues,
    generate_seo_report,
    track_rankings,
)
from admin.agency.seo_store import (
    add_tracked_keyword,
    delete_report,
    get_tracked_keywords,
    list_audits,
    list_reports,
    remove_tracked_keyword,
    save_audit,
    save_report,
)
from admin.agency.seo_skills import build_skill_context, detect_skills, list_seo_skills

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/seo", tags=["seo"])


# ── Request Models ───────────────────────────────────────────────────────────

class SEOChatRequest(BaseModel):
    message: str
    session_id: str = "seo"
    skip_skills: bool = False


class AuditRequest(BaseModel):
    url: str
    max_pages: int = 10
    workspace_id: str = "default"


class KeywordRequest(BaseModel):
    seed_keyword: str
    language: str = "en"
    workspace_id: str = "default"


class OnPageRequest(BaseModel):
    url: str


class SitemapRequest(BaseModel):
    url: str


class RobotsRequest(BaseModel):
    url: str


class SERPRequest(BaseModel):
    keyword: str
    num_results: int = 10


class TrackedKeywordRequest(BaseModel):
    keyword: str
    workspace_id: str = "default"
    target_url: str = ""
    search_volume: str = ""
    difficulty: str = ""
    notes: str = ""


class ReportRequest(BaseModel):
    workspace_id: str = "default"
    report_type: str = "general"
    title: str = ""
    content: dict[str, Any] = {}


class MetaTagsRequest(BaseModel):
    url: str


class SchemaRequest(BaseModel):
    url: str


class FixRequest(BaseModel):
    audit_url: str


class SEOReportRequest(BaseModel):
    url: str
    keywords: list[str] | None = None


class TrackRankingsRequest(BaseModel):
    keyword: str
    target_url: str
    num_results: int = 20


# ── SEO Chat ─────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def seo_chat(body: SEOChatRequest):
    """Chat with the SEO agent."""
    from admin.workspace.agents.seo import SEOAgent

    message = body.message
    if not message:
        raise HTTPException(400, "Message is required")

    # Auto-detect skills
    skill_context = ""
    if not body.skip_skills:
        matched = detect_skills(message)
        if matched:
            skill_context = build_skill_context(matched)

    if skill_context:
        message = f"{skill_context}\n\n{message}"

    agent = SEOAgent(workspace_name="SEO Workspace", client_name="Client")
    response, phases = await agent.chat(message=message)

    return ChatResponse(
        response=response,
        conversation_id=body.session_id,
        agent_type="seo",
        thinking_phases=phases,
    )


# ── Site Audit ───────────────────────────────────────────────────────────────

@router.post("/audit")
async def run_audit(body: AuditRequest):
    """Run a site audit — no agent needed, direct tool call."""
    result = site_audit(body.url, max_pages=body.max_pages)

    # Save to store
    saved = save_audit(body.workspace_id, body.url, result)

    return {
        "success": True,
        "audit_id": saved["id"],
        "pages_crawled": result["pages_crawled"],
        "issues_count": len(result.get("issues", [])),
        "summary": result.get("summary", {}),
        "issues": result.get("issues", [])[:50],
    }


@router.get("/audits")
async def list_audits_api(workspace_id: str | None = None):
    """List saved audits."""
    audits = list_audits(workspace_id)
    return {"success": True, "data": {"audits": audits}}


# ── Keyword Research ─────────────────────────────────────────────────────────

@router.post("/keywords")
async def run_keyword_research(body: KeywordRequest):
    """Run keyword research — no agent needed."""
    result = keyword_research(body.seed_keyword, body.language)

    return {
        "success": True,
        "seed_keyword": result["seed_keyword"],
        "total_suggestions": result["total_suggestions"],
        "questions": result["questions"][:20],
        "long_tail": result["long_tail"][:30],
        "short_tail": result["short_tail"][:20],
        "all_keywords": result["all_keywords"][:50],
    }


# ── On-Page Check ────────────────────────────────────────────────────────────

@router.post("/check")
async def run_onpage_check(body: OnPageRequest):
    """On-page SEO check for a single URL."""
    result = onpage_check(body.url)
    return {"success": True, "data": result}


# ── Sitemap ──────────────────────────────────────────────────────────────────

@router.post("/sitemap")
async def run_sitemap(body: SitemapRequest):
    """Parse a website's sitemap."""
    result = parse_sitemap(body.url)
    return {"success": True, "data": result}


# ── Robots.txt ───────────────────────────────────────────────────────────────

@router.post("/robots")
async def run_robots(body: RobotsRequest):
    """Parse robots.txt."""
    result = parse_robots_txt(body.url)
    return {"success": True, "data": result}


# ── SERP Check ───────────────────────────────────────────────────────────────

@router.post("/serp")
async def run_serp_check(body: SERPRequest):
    """Check Google SERP for a keyword."""
    result = serp_check(body.keyword, body.num_results)
    return {"success": True, "data": result}


# ── Generate Meta Tags ────────────────────────────────────────────────────────

@router.post("/meta-tags")
async def api_generate_meta_tags(body: MetaTagsRequest):
    """Generate optimized meta tags for a URL. Returns ready-to-paste HTML."""
    result = generate_meta_tags(body.url)
    return {"success": True, "data": result}


# ── Generate Schema ───────────────────────────────────────────────────────────

@router.post("/schema")
async def api_generate_schema(body: SchemaRequest):
    """Generate JSON-LD schema markup for a URL."""
    result = generate_schema(body.url)
    return {"success": True, "data": result}


# ── Fix Audit Issues ──────────────────────────────────────────────────────────

@router.post("/fixes")
async def api_fix_audit_issues(body: FixRequest):
    """Run audit and generate ready-to-paste HTML fixes."""
    result = fix_audit_issues(body.audit_url)
    return {"success": True, "data": result}


# ── Generate SEO Report ──────────────────────────────────────────────────────

@router.post("/report")
async def api_generate_seo_report(body: SEOReportRequest):
    """Generate a client-ready SEO report."""
    result = generate_seo_report(body.url, body.keywords)
    return {"success": True, "data": result}


# ── Track Rankings ────────────────────────────────────────────────────────────

@router.post("/rankings")
async def api_track_rankings(body: TrackRankingsRequest):
    """Check SERP position and track rankings over time."""
    result = track_rankings(body.keyword, body.target_url, body.num_results)
    return {"success": True, "data": result}


# ── Tracked Keywords ─────────────────────────────────────────────────────────

@router.get("/tracked")
async def api_get_tracked(workspace_id: str = "default"):
    """List tracked keywords for a workspace."""
    kws = get_tracked_keywords(workspace_id)
    return {"success": True, "data": {"keywords": kws}}


@router.post("/tracked")
async def api_add_tracked(body: TrackedKeywordRequest):
    """Add a keyword to track."""
    entry = add_tracked_keyword(
        workspace_id=body.workspace_id,
        keyword=body.keyword,
        target_url=body.target_url,
        search_volume=body.search_volume,
        difficulty=body.difficulty,
        notes=body.notes,
    )
    return {"success": True, "data": {"keyword": entry}}


@router.delete("/tracked/{keyword_id}")
async def api_remove_tracked(keyword_id: str, workspace_id: str = "default"):
    """Remove a tracked keyword."""
    if not remove_tracked_keyword(workspace_id, keyword_id):
        raise HTTPException(404, "Keyword not found")
    return {"success": True}


# ── Reports ──────────────────────────────────────────────────────────────────

@router.get("/reports")
async def api_list_reports(workspace_id: str | None = None):
    """List SEO reports."""
    reports = list_reports(workspace_id)
    return {"success": True, "data": {"reports": reports}}


@router.post("/reports")
async def api_save_report(body: ReportRequest):
    """Save an SEO report."""
    report = save_report(
        workspace_id=body.workspace_id,
        report_type=body.report_type,
        title=body.title,
        content=body.content,
    )
    return {"success": True, "data": {"report": report}}


# ── Skills ───────────────────────────────────────────────────────────────────

@router.get("/skills")
async def api_list_seo_skills():
    """List all skills available to SEO agent."""
    return {"success": True, "data": {"skills": list_seo_skills()}}
