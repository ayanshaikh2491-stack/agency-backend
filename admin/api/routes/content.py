"""Content Agent API Routes — Workspace-Aware Visual Content.

Har workspace ka apna Content Agent with brand context.

Endpoints:
  POST /api/content/chat              — Visual brief (with workspace context)
  POST /api/content/init              — Initialize Content Agent for workspace
  POST /api/content/discover-brand    — Discover client brand from website
  POST /api/content/generate-image    — Direct image generation
  POST /api/content/generate-video    — Direct video generation
  POST /api/content/generate-ad       — Ad creative image
  POST /api/content/generate-social   — Social media image
  POST /api/content/generate-hero     — Hero/banner image
  POST /api/content/brief             — Domain agent briefs Content Agent
  GET  /api/content/status/{ws_id}    — Workspace Content Agent status
  GET  /api/content/tools             — Available tools
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/content", tags=["content-agent"])


# ── Request Models ───────────────────────────────────────────────────────────


class InitAgentRequest(BaseModel):
    """Initialize Content Agent for a workspace."""
    workspace_id: str
    workspace_name: str = "Default"
    client_name: str = "Client"
    client_website: str = ""  # Auto-discovers brand if provided


class ChatRequest(BaseModel):
    message: str
    workspace_id: str = ""
    workspace_name: str = "Default"
    client_name: str = "Client"
    client_website: str = ""
    brief_from: str = ""


class DiscoverBrandRequest(BaseModel):
    workspace_id: str
    website_url: str


class GenerateImageRequest(BaseModel):
    prompt: str
    workspace_id: str = ""
    platform: str = "instagram"
    width: int = 0
    height: int = 0
    steps: int = 20


class GenerateVideoRequest(BaseModel):
    prompt: str
    workspace_id: str = ""
    platform: str = "instagram"
    frames: int = 49


class GenerateAdRequest(BaseModel):
    product: str
    workspace_id: str = ""
    platform: str = "facebook"
    style: str = "professional"


class GenerateSocialRequest(BaseModel):
    topic: str
    workspace_id: str = ""
    platform: str = "instagram"


class GenerateHeroRequest(BaseModel):
    topic: str
    workspace_id: str = ""
    style: str = "modern"


class AnalyzeReadabilityRequest(BaseModel):
    url: str


class GenerateBlogPostRequest(BaseModel):
    topic: str
    keywords: list[str] | None = None
    word_count: int = 1500


class GenerateCalendarRequest(BaseModel):
    niche: str
    weeks: int = 4


class RewriteContentRequest(BaseModel):
    text: str
    style: str = "professional"


class MetaOptimizeRequest(BaseModel):
    url: str


class ContentGapsRequest(BaseModel):
    url: str
    competitors: list[str] | None = None


class AgencyKnowledgeRequest(BaseModel):
    industry: str = ""
    platform: str = ""
    visual_type: str = ""


class QueueStatusRequest(BaseModel):
    workspace_id: str


class BriefRequest(BaseModel):
    """Domain agent sends detailed visual brief."""
    from_agent: str  # seo, ads, social, website
    workspace_id: str
    workspace_name: str = ""
    client_name: str = ""
    client_website: str = ""
    task: str  # The detailed brief
    context: str = ""
    priority: str = "normal"


class GenerateVariationsRequest(BaseModel):
    """Generate 3-4 variations of a visual."""
    workspace_id: str
    message: str  # Brief text
    brief_from: str = ""
    num_variations: int = 3
    platforms: list[str] = ["instagram"]


class SelectVariationRequest(BaseModel):
    """Select best variation from generated set."""
    workspace_id: str
    variation_id: str


class GenerateUGCRequest(BaseModel):
    """Generate UGC/testimonial style video."""
    workspace_id: str
    subject: str
    style: str = "testimonial"  # testimonial, review, unboxing, reaction
    platform: str = "instagram"
    duration: str = "short"  # short, medium, long


class GenerateMarketingRequest(BaseModel):
    """Generate marketing/explainer video."""
    workspace_id: str
    subject: str
    style: str = "product_showcase"  # product_showcase, explainer, brand_story, before_after
    platform: str = "youtube"
    duration: str = "medium"


class GenerateCarouselRequest(BaseModel):
    """Generate Instagram/social carousel (multi-slide)."""
    workspace_id: str
    topic: str
    platform: str = "instagram"
    slides: int = 5  # 5-10 slides
    style: str = "modern"  # bold, minimal, professional, modern, elegant
    mood: str = "engaging"
    color_request: str = ""


class BatchGenerateRequest(BaseModel):
    """Generate multiple images for content calendar."""
    workspace_id: str
    briefs: list[str]  # List of brief texts
    platform: str = "instagram"
    consistent_style: bool = True


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/init")
async def init_agent(req: InitAgentRequest):
    """Content Agent initialize karo workspace ke liye — brand auto-discover hoga."""
    from admin.workspace.agents.content import get_or_create_content_agent

    agent = get_or_create_content_agent(
        workspace_id=req.workspace_id,
        workspace_name=req.workspace_name,
        client_name=req.client_name,
        client_website=req.client_website,
    )

    return {
        "status": "initialized",
        "workspace_id": req.workspace_id,
        "client_name": req.client_name,
        "brand_discovered": bool(agent.brand),
        "brand": agent.brand,
    }


@router.post("/discover-brand")
async def discover_brand(req: DiscoverBrandRequest):
    """Client ka brand discover karo website se."""
    from admin.workspace.agents.content import get_or_create_content_agent

    agent = get_or_create_content_agent(
        workspace_id=req.workspace_id,
        workspace_name=req.workspace_id,
    )
    brand = agent.discover_brand(req.website_url)
    return {"workspace_id": req.workspace_id, "brand": brand}


@router.get("/status/{workspace_id}")
async def get_status(workspace_id: str):
    """Workspace Content Agent ka status."""
    from admin.workspace.agents.content import get_content_agent

    agent = get_content_agent(workspace_id)
    if not agent:
        return {"status": "not_initialized", "workspace_id": workspace_id}

    return agent.status()


@router.get("/tools")
async def list_tools():
    """Available tools."""
    return {
        "tools": [
            "generate_image — FLUX image generation",
            "generate_video — CogVideoX video generation",
            "generate_carousel — Multi-slide carousel images",
            "generate_ad_image — Platform-specific ad creative",
            "generate_social_image — Social media post image",
            "generate_hero_image — Hero/banner",
            "generate_story — Instagram/Facebook story",
            "generate_thumbnail — YouTube/blog thumbnail",
            "generate_ugc — UGC style video",
            "generate_testimonial — Testimonial video",
            "generate_unboxing — Unboxing video",
            "generate_explainer — Explainer video",
            "generate_product_showcase — Product showcase video",
            "get_platform_specs — Platform sizes",
        ],
        "total": 14,
        "type": "visual_only",
    }


# ── Chat (LangGraph — brand-aware thinking) ──────────────────────────────────


@router.post("/chat")
async def chat(req: ChatRequest):
    """Content Agent se visual brief chatta hai — brand context + thinking ke saath."""
    from admin.workspace.agents.content import run_content_agent

    result = run_content_agent(
        message=req.message,
        workspace_id=req.workspace_id,
        workspace_name=req.workspace_name,
        client_name=req.client_name,
        client_website=req.client_website,
        brief_from=req.brief_from,
    )

    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown"))

    return result


# ── Direct Generation ────────────────────────────────────────────────────────


@router.post("/generate-image")
async def generate_image_endpoint(req: GenerateImageRequest):
    """Image generate karo — direct API."""
    from admin.tools.together_gpu import generate_image

    return generate_image(
        prompt=req.prompt,
        platform=req.platform,
        width=req.width,
        height=req.height,
        steps=req.steps,
    )


@router.post("/generate-video")
async def generate_video_endpoint(req: GenerateVideoRequest):
    """Video generate karo — direct API."""
    from admin.tools.together_gpu import generate_video

    return generate_video(
        prompt=req.prompt,
        platform=req.platform,
        frames=req.frames,
    )


@router.post("/generate-ad")
async def generate_ad_endpoint(req: GenerateAdRequest):
    """Ad creative image."""
    from admin.tools.together_gpu import generate_image

    prompt = f"A professional {req.style} advertisement for {req.product}, high quality marketing material"
    return generate_image(prompt=prompt, platform=req.platform)


@router.post("/generate-social")
async def generate_social_endpoint(req: GenerateSocialRequest):
    """Social media image."""
    from admin.tools.together_gpu import generate_image

    prompt = f"A beautiful, engaging social media post about {req.topic}, modern design, vibrant colors, professional quality"
    return generate_image(prompt=prompt, platform=req.platform)


@router.post("/generate-hero")
async def generate_hero_endpoint(req: GenerateHeroRequest):
    """Hero/banner image."""
    from admin.tools.together_gpu import generate_image

    prompt = f"A stunning hero banner image about {req.topic}, {req.style} design, wide format, professional quality"
    return generate_image(prompt=prompt, platform="blog_hero", width=1920, height=1080)


# ── Domain Agent Briefing ────────────────────────────────────────────────────


@router.post("/brief")
async def brief_content_agent(req: BriefRequest):
    """Domain agent Content Agent ko detailed brief bhejta hai.

    Content Agent:
      1. Brief parse karega
      2. Brand context dekhega
      3. Visual plan banayega
      4. Expert prompt engineering karega
      5. Kaggle GPU pe generate karega
      6. Report karega
    """
    from admin.workspace.agents.content import run_content_agent

    brief_msg = req.task
    if req.context:
        brief_msg += f"\n\nAdditional Context: {req.context}"
    brief_msg += f"\n\nPriority: {req.priority}"

    result = run_content_agent(
        message=brief_msg,
        workspace_id=req.workspace_id,
        workspace_name=req.workspace_name or req.workspace_id,
        client_name=req.client_name,
        client_website=req.client_website,
        brief_from=req.from_agent,
    )

    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Brief failed"))

    return result


# ── GPU Status ───────────────────────────────────────────────────────────────


@router.get("/gpu/status/{kernel_slug:path}")
async def gpu_status(kernel_slug: str):
    """Kaggle notebook status."""
    from admin.tools.free_gpu import get_backend_status as check_status
    return {"kernel_slug": kernel_slug, "status": check_status(kernel_slug)}


# ── Variations & Selection ─────────────────────────────────────────────────


@router.post("/generate-variations")
async def generate_variations_endpoint(req: GenerateVariationsRequest):
    """3-4 variations banao ek brief se."""
    from admin.workspace.agents.content import run_content_agent

    variations = []

    for i in range(req.num_variations):
        variation_prompt = (
            f"Variation {i + 1} of {req.num_variations}: {req.message}"
        )
        result = run_content_agent(
            message=variation_prompt,
            workspace_id=req.workspace_id,
            brief_from=req.brief_from or "variations",
        )
        variations.append({
            "variation_id": f"{req.workspace_id}-var-{i + 1}",
            "index": i + 1,
            "platforms": req.platforms,
            "result": result,
        })

    return {
        "workspace_id": req.workspace_id,
        "total_variations": len(variations),
        "variations": variations,
    }


@router.post("/select-variation")
async def select_variation_endpoint(req: SelectVariationRequest):
    """Best variation select karo."""
    from admin.workspace.agents.content import get_content_agent

    agent = get_content_agent(req.workspace_id)
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Workspace {req.workspace_id} not initialized",
        )

    return {
        "workspace_id": req.workspace_id,
        "selected_variation_id": req.variation_id,
        "status": "selected",
    }


# ── UGC & Marketing Videos ──────────────────────────────────────


@router.post("/generate-ugc")
async def generate_ugc_endpoint(req: GenerateUGCRequest):
    """UGC/testimonial style video banao."""
    from admin.workspace.agents.content import run_content_agent

    style_descriptions = {
        "testimonial": "authentic customer testimonial video, real person speaking to camera",
        "review": "hands-on product review video, close-up shots, genuine reaction",
        "unboxing": "exciting unboxing moment, first impressions, reveal shots",
        "reaction": "genuine reaction video, expressive, real emotion",
    }
    style_desc = style_descriptions.get(req.style, style_descriptions["testimonial"])

    duration_frames = {"short": 49, "medium": 81, "long": 121}
    frames = duration_frames.get(req.duration, 49)

    brief_msg = (
        f"Create a {req.style} UGC video about: {req.subject}. "
        f"Style: {style_desc}. "
        f"Platform: {req.platform}. "
        f"This should feel authentic and user-generated, not polished or corporate."
    )

    result = run_content_agent(
        message=brief_msg,
        workspace_id=req.workspace_id,
        brief_from="ugc_generator",
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "UGC generation failed"))

    return {
        "workspace_id": req.workspace_id,
        "type": "ugc",
        "style": req.style,
        "platform": req.platform,
        "duration": req.duration,
        "frames": frames,
        "result": result,
    }


@router.post("/generate-marketing")
async def generate_marketing_endpoint(req: GenerateMarketingRequest):
    """Marketing/explainer video banao."""
    from admin.workspace.agents.content import run_content_agent

    style_descriptions = {
        "product_showcase": "sleek product showcase, 360-degree views, premium feel",
        "explainer": "clear explainer video, step-by-step visuals, educational",
        "brand_story": "emotional brand story, cinematic shots, narrative arc",
        "before_after": "before-and-after transformation, split screen, dramatic reveal",
    }
    style_desc = style_descriptions.get(req.style, style_descriptions["product_showcase"])

    duration_frames = {"short": 49, "medium": 81, "long": 121}
    frames = duration_frames.get(req.duration, 81)

    brief_msg = (
        f"Create a {req.style.replace('_', ' ')} marketing video about: {req.subject}. "
        f"Style: {style_desc}. "
        f"Platform: {req.platform}. "
        f"Professional quality, polished production."
    )

    result = run_content_agent(
        message=brief_msg,
        workspace_id=req.workspace_id,
        brief_from="marketing_generator",
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Marketing video generation failed"))

    return {
        "workspace_id": req.workspace_id,
        "type": "marketing",
        "style": req.style,
        "platform": req.platform,
        "duration": req.duration,
        "frames": frames,
        "result": result,
    }


# ── Carousel Generation ──────────────────────────────────────────


@router.post("/generate-carousel")
async def generate_carousel_endpoint(req: GenerateCarouselRequest):
    """Instagram/social carousel banao — multiple slides."""
    from admin.workspace.agents.content import run_content_agent

    style_desc = f"{req.style} style, consistent visual theme across {req.slides} slides"
    color_note = f"Color request: {req.color_request}. " if req.color_request else ""

    brief_msg = (
        f"Create a {req.slides}-slide carousel about: {req.topic}. "
        f"Platform: {req.platform}. "
        f"Style: {style_desc}. Mood: {req.mood}. "
        f"{color_note}"
        f"Each slide should be cohesive and follow the same visual theme."
    )

    result = run_content_agent(
        message=brief_msg,
        workspace_id=req.workspace_id,
        brief_from="carousel_generator",
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Carousel generation failed"))

    return {
        "workspace_id": req.workspace_id,
        "type": "carousel",
        "slides": req.slides,
        "style": req.style,
        "platform": req.platform,
        "result": result,
    }


# ── Batch Generation ─────────────────────────────────────────────────


@router.post("/batch-generate")
async def batch_generate_endpoint(req: BatchGenerateRequest):
    """Multiple images batch mein banao."""
    from admin.workspace.agents.content import run_content_agent

    results = []

    for idx, brief_text in enumerate(req.briefs):
        result = run_content_agent(
            message=brief_text,
            workspace_id=req.workspace_id,
            brief_from="batch_generator",
        )
        results.append({
            "index": idx + 1,
            "brief": brief_text,
            "success": result.get("success", False),
            "result": result,
        })

    return {
        "workspace_id": req.workspace_id,
        "platform": req.platform,
        "consistent_style": req.consistent_style,
        "total_briefs": len(req.briefs),
        "results": results,
    }


# ── Workspace Memory & Outputs ──────────────────────────────────


@router.get("/workspace/{workspace_id}/memory")
async def get_workspace_memory(workspace_id: str):
    """Workspace Content Agent ki memory dikhaao."""
    from admin.workspace.agents.content import get_content_agent

    agent = get_content_agent(workspace_id)
    if not agent:
        return {
            "workspace_id": workspace_id,
            "initialized": False,
            "memory": {},
        }

    return {
        "workspace_id": workspace_id,
        "initialized": True,
        "brand": agent.brand,
        "memory": {
            "brand_context": agent.brand or {},
            "status": agent.status(),
        },
    }


@router.get("/workspace/{workspace_id}/outputs")
async def get_workspace_outputs(workspace_id: str):
    """Workspace ke saare outputs dikhaao."""
    from pathlib import Path

    output_dir = Path("outputs") / workspace_id
    files = []

    if output_dir.exists():
        for f in sorted(output_dir.iterdir()):
            if f.is_file():
                stat = f.stat()
                files.append({
                    "filename": f.name,
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                })

    return {
        "workspace_id": workspace_id,
        "total_outputs": len(files),
        "files": files,
    }


# ── Unified Tool List ──────────────────────────────────────────────────────────


@router.get("/tools/all")
async def list_all_tools():
    """Get all 21 tools with schemas — unified registry."""
    from admin.tools.registry import list_all_tools as _list_all_tools
    tools = _list_all_tools()
    return {
        "total": len(tools),
        "visual": sum(1 for t in tools if t["category"] == "visual"),
        "content": sum(1 for t in tools if t["category"] == "content"),
        "tools": tools,
    }


# ── Content Analysis Endpoints ─────────────────────────────────────────────────


@router.post("/analyze-readability")
async def analyze_readability_endpoint(req: AnalyzeReadabilityRequest):
    """URL ki content readability analyze karo."""
    from admin.tools.content_tools import analyze_readability
    return analyze_readability(req.url)


@router.post("/blog-post")
async def generate_blog_post_endpoint(req: GenerateBlogPostRequest):
    """SEO-optimized blog post generate karo."""
    from admin.tools.content_tools import generate_blog_post
    return generate_blog_post(req.topic, req.keywords, req.word_count)


@router.post("/calendar")
async def generate_calendar_endpoint(req: GenerateCalendarRequest):
    """Content calendar generate karo."""
    from admin.tools.content_tools import generate_content_calendar
    return generate_content_calendar(req.niche, req.weeks)


@router.post("/rewrite")
async def rewrite_content_endpoint(req: RewriteContentRequest):
    """Content rewrite karo for better readability."""
    from admin.tools.content_tools import rewrite_content
    return rewrite_content(req.text, req.style)


@router.post("/meta-optimize")
async def meta_optimize_endpoint(req: MetaOptimizeRequest):
    """Meta descriptions optimize karo."""
    from admin.tools.content_tools import optimize_meta_descriptions
    return optimize_meta_descriptions(req.url)


@router.post("/content-gaps")
async def content_gaps_endpoint(req: ContentGapsRequest):
    """Content gaps analyze karo competitors ke saath."""
    from admin.tools.content_tools import analyze_content_gaps
    return analyze_content_gaps(req.url, req.competitors)


# ── Agency Intelligence Endpoints ──────────────────────────────────────────────


@router.get("/agency/stats")
async def agency_stats_endpoint():
    """Agency-level content stats — dashboard ke liye."""
    from admin.agency.content_agent import get_agency_content_agent
    agency = get_agency_content_agent()
    return agency.get_stats()


@router.post("/agency/knowledge")
async def agency_knowledge_endpoint(req: AgencyKnowledgeRequest):
    """Cross-project knowledge get karo for workspace."""
    from admin.agency.content_agent import get_agency_content_agent
    agency = get_agency_content_agent()
    return agency.get_knowledge_for_workspace(
        industry=req.industry,
        platform=req.platform,
        visual_type=req.visual_type,
    )


@router.get("/agency/best-prompts")
async def agency_best_prompts_endpoint():
    """Best performing prompts across all workspaces."""
    from admin.agency.content_agent import get_agency_content_agent
    agency = get_agency_content_agent()
    return {
        "best_prompts": agency.get_best_prompts(top_n=10),
    }


# ── Queue Status Endpoint ──────────────────────────────────────────────────────


@router.get("/queue/status")
async def queue_status_endpoint(workspace_id: str = "default"):
    """GPU queue overview for a workspace."""
    from admin.tools.content_queue import get_queue
    queue = get_queue(workspace_id)
    return queue.get_queue_status()


# ── Reasoning Chain Endpoints ──────────────────────────────────────────────────

class ReasoningRequest(BaseModel):
    brief_text: str
    workspace_id: str = "default"
    domain: str = "content"
    brand_context: dict[str, Any] = {}


@router.post("/reasoning/run")
async def run_reasoning(req: ReasoningRequest):
    """Run the 5-step reasoning chain on a brief.
    
    Steps: UNDERSTAND -> RESEARCH -> STRATEGIZE -> EXECUTE -> VALIDATE
    """
    from admin.workspace.agents.reasoning_chain import run_reasoning_chain
    result = run_reasoning_chain(
        brief_text=req.brief_text,
        workspace_id=req.workspace_id,
        domain=req.domain,
        brand_context=req.brand_context,
    )
    return result


@router.get("/reasoning/{job_id}")
async def get_reasoning(job_id: str):
    """Get reasoning chain log for a specific job."""
    from admin.tools.reasoning_logger import ReasoningLogger
    log = ReasoningLogger.load(job_id)
    if not log:
        return {"status": "error", "error": f"Job {job_id} not found"}
    return {"status": "ok", "reasoning": log}


@router.get("/reasoning/stats/recent")
async def recent_reasoning_stats(limit: int = 10):
    """Recent reasoning chain statistics."""
    from admin.tools.reasoning_logger import ReasoningLogger
    logs = ReasoningLogger.get_recent(limit=limit)
    return {
        "status": "ok",
        "count": len(logs),
        "logs": logs,
    }
