"""Website Agent API Routes — Full-stack web developer endpoints.

Endpoints:
  POST /api/website/chat              — Chat with Website Agent (SEO requests auto-route to SEO Agent)
  POST /api/website/analyze           — Analyze website structure + tech stack
  POST /api/website/performance       — Check page performance
  POST /api/website/links             — Find broken links
  POST /api/website/security          — Security headers check
  POST /api/website/accessibility     — a11y checks
  POST /api/website/tech-stack        — Recommend tech stack
  POST /api/website/design-plan       — Plan site architecture
  POST /api/website/competitors       — Scan competitor websites
  POST /api/website/request-content   — Brief Content Agent for visuals
  POST /api/website/request-seo       — Route SEO work to SEO Agent
  POST /api/website/generate-code     — Generate Next.js/HTML/CSS code
  POST /api/website/deploy            — Deploy to Vercel (frontend+backend)
  POST /api/website/domain            — Domain DNS + SSL + availability check
  POST /api/website/screenshot        — Capture website visual metadata
  POST /api/website/uptime            — Monitor site uptime + response time
  GET  /api/website/tools             — Available tools

NOTE: SEO endpoints (/seo, /sitemap) are handled by SEO Agent routes (/api/seo/*).
      Website Agent auto-routes SEO requests to SEO Agent via agent_bus.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/website", tags=["website-agent"])


# ── Request Models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    workspace_name: str = "Default"
    client_name: str = "Client"
    skip_skills: bool = False


class AnalyzeRequest(BaseModel):
    url: str


class PerformanceRequest(BaseModel):
    url: str


class LinksRequest(BaseModel):
    url: str
    max_links: int = 50


class SecurityRequest(BaseModel):
    url: str


class AccessibilityRequest(BaseModel):
    url: str


class TechStackRequest(BaseModel):
    site_type: str = ""
    needs_ecommerce: bool = False
    needs_blog: bool = False
    budget: str = "medium"
    client_preference: str = ""


class DesignPlanRequest(BaseModel):
    site_type: str = "landing"
    pages: str = "home, about, services, contact"
    style: str = "modern"


class CompetitorsRequest(BaseModel):
    urls: list[str]


class RequestContentRequest(BaseModel):
    workspace_name: str = "Default"
    content_type: str = "hero_image"
    topic: str = ""
    description: str = ""
    style: str = "professional"
    priority: str = "normal"


class GenerateCodeRequest(BaseModel):
    page_type: str = "landing"
    framework: str = "nextjs"
    style: str = "modern"
    sections: str = "hero,features,cta,footer"
    color_primary: str = "#2563EB"
    title: str = "My Website"


class DeployRequest(BaseModel):
    project_path: str = "."
    project_name: str = ""
    prod: bool = True
    env_vars: str = ""


class DomainRequest(BaseModel):
    domain: str


class ScreenshotRequest(BaseModel):
    url: str
    width: int = 1280
    height: int = 800


class UptimeRequest(BaseModel):
    url: str
    checks: int = 3
    interval: int = 2


class BuildSiteRequest(BaseModel):
    title: str = "My Website"
    tagline: str = ""
    industry: str = ""
    category: str = "business"
    sections: str = ""
    style: str = "modern"
    color_primary: str = "#2563EB"
    framework: str = "nextjs"
    services: str = ""
    business_email: str = ""
    output_dir: str = ""
    skills: list[str] = []
    workspace_id: str = "ws_agency"
    client_name: str = ""


class PublishSiteRequest(BaseModel):
    title: str = "My Website"
    tagline: str = ""
    industry: str = ""
    category: str = "business"
    sections: str = ""
    style: str = "modern"
    color_primary: str = "#2563EB"
    framework: str = "nextjs"
    services: str = ""
    business_email: str = ""
    project_name: str = ""
    output_dir: str = ""
    skills: list[str] = []
    prod: bool = True
    workspace_id: str = "ws_agency"
    client_name: str = ""


class DomainConnectRequest(BaseModel):
    project: str
    domain: str


class DomainStatusRequest(BaseModel):
    project: str
    domain: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest):
    """Chat with Website Agent."""
    from admin.workspace.agents.website import WebsiteAgent
    from admin.agency.website_skills import build_skill_context, detect_skills, list_website_skills

    message = req.message
    if not message:
        raise HTTPException(400, "Message is required")

    # Auto-detect website skills (frontend-design, nextjs, ui-design-system, etc.)
    skill_context = ""
    matched: list = []
    if not req.skip_skills:
        matched = detect_skills(message)
        if matched:
            skill_context = build_skill_context(matched)

    if skill_context:
        message = f"{skill_context}\n\n{message}"

    agent = WebsiteAgent(workspace_name=req.workspace_name, client_name=req.client_name)
    output, phases = await agent.chat(message, skills=[s["name"] for s in matched])

    # Persist the chat event into the workspace's Supabase build log (best-effort)
    try:
        from admin.agency.website_supabase import log_website_event
        log_website_event(
            workspace=req.workspace_name,
            client=req.client_name or req.workspace_name,
            event_type="chat",
            message=message[:300],
            actor="website_agent",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("website chat supabase log failed: %s", e)

    return {
        "response": output,
        "thread_id": agent._thread_id,
        "agent_type": "website",
        "matched_skills": [s["name"] for s in matched],
        "thinking_phases": phases,
    }


@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Analyze website: tech stack, structure, navigation, images."""
    from admin.tools.website_tools import analyze_website
    return analyze_website(req.url)


@router.post("/performance")
async def performance(req: PerformanceRequest):
    """Check page performance: load time, size, resources."""
    from admin.tools.website_tools import check_performance
    return check_performance(req.url)


@router.post("/links")
async def links(req: LinksRequest):
    """Find broken links on a page."""
    from admin.tools.website_tools import check_links
    return check_links(req.url, req.max_links)



@router.post("/security")
async def security(req: SecurityRequest):
    """Check security headers."""
    from admin.tools.website_tools import security_check
    return security_check(req.url)


@router.post("/accessibility")
async def accessibility(req: AccessibilityRequest):
    """Accessibility checks."""
    from admin.tools.website_tools import check_accessibility
    return check_accessibility(req.url)


@router.post("/tech-stack")
async def tech_stack(req: TechStackRequest):
    """Recommend tech stack."""
    from admin.tools.website_tools import tech_stack_advisor
    return tech_stack_advisor(
        site_type=req.site_type,
        needs_ecommerce=req.needs_ecommerce,
        needs_blog=req.needs_blog,
        budget=req.budget,
        client_preference=req.client_preference,
    )


@router.post("/design-plan")
async def design_plan(req: DesignPlanRequest):
    """Plan site architecture."""
    from admin.tools.website_tools import design_planner
    return design_planner(
        site_type=req.site_type,
        pages=req.pages,
        style=req.style,
    )


@router.post("/competitors")
async def competitors(req: CompetitorsRequest):
    """Scan competitor websites."""
    from admin.tools.website_tools import competitor_sites
    return competitor_sites(req.urls)



@router.post("/request-content")
async def request_content(req: RequestContentRequest):
    """Brief Content Agent for website visuals."""
    from admin.workspace.agents.website import WebsiteAgent
    agent = WebsiteAgent(workspace_name=req.workspace_name, client_name=req.workspace_name)
    return agent.request_content(
        content_type=req.content_type,
        topic=req.topic,
        description=req.description,
        style=req.style,
        priority=req.priority,
    )


@router.post("/request-seo")
async def request_seo(req: RequestContentRequest):
    """Route SEO work to SEO Agent — SEO Agent karega kaam aur report dega."""
    from admin.workspace.agents.seo import SEOAgent
    seo_agent = SEOAgent(workspace_name=req.workspace_name, client_name=req.workspace_name)
    
    task = req.topic or req.description
    if not task:
        raise HTTPException(400, "topic or description required for SEO task")
    
    response, phases = await seo_agent.chat(message=task)
    
    # Also send brief via agent_bus for audit trail
    from admin.workspace.agent_bus import send_message
    send_message(
        from_agent="website",
        to_agent="seo",
        workspace_id=req.workspace_name,
        subject=f"SEO task from Website Agent: {task[:80]}",
        content=response,
        message_type="response",
    )
    
    return {
        "status": "completed",
        "routed_to": "seo",
        "task": task[:200],
        "response": response,
        "thinking_phases": phases,
    }


@router.post("/generate-code")
async def generate_code(req: GenerateCodeRequest):
    """Generate Next.js/HTML/CSS code for a page."""
    from admin.tools.website_tools import generate_code
    return generate_code(
        page_type=req.page_type,
        framework=req.framework,
        style=req.style,
        sections=req.sections,
        color_primary=req.color_primary,
        title=req.title,
    )


@router.post("/deploy")
async def deploy(req: DeployRequest):
    """Deploy project to Vercel (frontend+backend)."""
    from admin.tools.website_tools import deploy_vercel
    return deploy_vercel(
        project_path=req.project_path,
        project_name=req.project_name,
        prod=req.prod,
        env_vars=req.env_vars,
    )


@router.post("/domain")
async def domain_check(req: DomainRequest):
    """Check domain: DNS records, SSL, website status."""
    from admin.tools.website_tools import check_domain
    return check_domain(req.domain)


@router.post("/screenshot")
async def screenshot(req: ScreenshotRequest):
    """Capture website visual metadata."""
    from admin.tools.website_tools import screenshot_site
    return screenshot_site(url=req.url, width=req.width, height=req.height)


@router.post("/uptime")
async def uptime(req: UptimeRequest):
    """Monitor site uptime and response time."""
    from admin.tools.website_tools import check_uptime
    return check_uptime(url=req.url, checks=req.checks, interval=req.interval)


@router.post("/build-site")
async def build_site_route(req: BuildSiteRequest):
    """Build a complete website project on disk from business info."""
    from admin.tools.website_tools import build_site
    result = build_site(
        title=req.title,
        tagline=req.tagline,
        industry=req.industry,
        category=req.category,
        sections=req.sections,
        style=req.style,
        color_primary=req.color_primary,
        framework=req.framework,
        services=req.services,
        business_email=req.business_email,
        output_dir=req.output_dir,
        skills=req.skills,
    )

    # ── Persist to Supabase workspace schema (best-effort) ──────────────
    # Writes the build row, the 6-doc trail (site_requirements + tech plan),
    # and a build log event into ws_<workspace>.* tables.
    client = req.client_name or req.title or req.workspace_id
    try:
        from admin.agency.website_supabase import (
            log_website_event,
            save_website_doc,
            upsert_website_build,
        )

        upsert_website_build(
            workspace=req.workspace_id,
            client=client,
            status="code_generated",
            current_stage="code_generation",
            framework=req.framework,
        )
        brief_json = {
            "title": req.title,
            "tagline": req.tagline,
            "industry": req.industry,
            "sections": req.sections,
            "style": req.style,
            "color_primary": req.color_primary,
            "framework": req.framework,
            "services": req.services,
            "business_email": req.business_email,
            "skills": req.skills,
        }
        save_website_doc(
            req.workspace_id, client, "site_requirements",
            title=f"{req.title} — Site Requirements",
            content=json.dumps(brief_json, indent=2),
        )
        save_website_doc(
            req.workspace_id, client, "tech_deploy_plan",
            title=f"{req.title} — Tech + Deploy Plan",
            content=f"Framework: {req.framework}\nStyle: {req.style}\nPages/Sections: {req.sections}",
        )
        log_website_event(
            req.workspace_id, client, "build_step",
            f"Generated {req.framework} site for '{req.title}' (stage: code_generation)",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("build-site supabase persist failed: %s", e)

    result["persisted"] = {"workspace": req.workspace_id, "client": client}
    return result


@router.get("/skills")
async def list_skills():
    """List all skills available to the Website Agent."""
    from admin.agency.website_skills import list_website_skills
    return {"success": True, "data": {"skills": list_website_skills()}}


@router.post("/publish")
async def publish_site_route(req: PublishSiteRequest):
    """One-shot pipeline: build real website -> deploy to Vercel -> live URL."""
    from admin.tools.website_tools import publish_site

    result = publish_site(
        title=req.title,
        tagline=req.tagline,
        industry=req.industry,
        category=req.category,
        sections=req.sections,
        style=req.style,
        color_primary=req.color_primary,
        framework=req.framework,
        services=req.services,
        business_email=req.business_email,
        project_name=req.project_name,
        output_dir=req.output_dir,
        skills=req.skills,
        prod=req.prod,
    )

    # Persist the publish attempt into the workspace build log (best-effort)
    client = req.client_name or req.title or req.workspace_id
    try:
        from admin.agency.website_supabase import log_website_event, upsert_website_build

        upsert_website_build(
            workspace=req.workspace_id,
            client=client,
            status=result.get("status", "failed"),
            current_stage="deploy",
            framework=req.framework,
        )
        log_website_event(
            req.workspace_id, client, "deploy_step",
            f"Publish '{req.title}': {result.get('status')} -> {result.get('live_url', '')}",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("publish supabase persist failed: %s", e)

    return result


@router.post("/domain/connect")
async def connect_domain_route(req: DomainConnectRequest):
    """Attach a custom domain to a Vercel project and return DNS records to add."""
    from admin.tools.website_tools import connect_domain
    return connect_domain(project=req.project, domain=req.domain)


@router.get("/domain/status")
async def domain_status_route(project: str, domain: str):
    """Check whether a custom domain is verified on the Vercel project."""
    from admin.tools.website_tools import domain_status
    return domain_status(project=project, domain=domain)


@router.get("/tools")
async def list_tools():
    """List available tools."""
    from admin.tools.website_tools import WEBSITE_TOOLS
    return {
        "tools": [
            {"name": t["function"]["name"], "description": t["function"]["description"]}
            for t in WEBSITE_TOOLS
        ],
        "count": len(WEBSITE_TOOLS),
        "seo_routing": "SEO requests are routed to SEO Agent",
    }
