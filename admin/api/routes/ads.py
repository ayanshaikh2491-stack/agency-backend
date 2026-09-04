"""Ads Agent — API routes for Meta + Google Ads management.

20 tool endpoints + chat + status + Content Agent brief
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from admin.tools.ads_tools import (
    ADS_TOOLS,
    execute_ads_tool,
    campaign_strategy,
    audience_research,
    budget_planner,
    competitor_ads,
    platform_selection,
    ad_copy_generator,
    creative_brief,
    ad_variations,
    landing_page_strategy,
    ad_hashtag_tags,
    audience_builder,
    lookalike_audience,
    retargeting_setup,
    exclusion_list,
    performance_analyzer,
    auto_optimize,
    ab_test_setup,
    campaign_report,
    roas_calculator,
    creative_score,
)
from admin.workspace.agent_bus import send_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ads", tags=["ads"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class CampaignStrategyRequest(BaseModel):
    platform: str = "meta"
    objective: str = "conversions"
    budget: str = ""
    audience: str = ""
    industry: str = ""
    goals: str = ""


class AudienceResearchRequest(BaseModel):
    industry: str = ""
    product: str = ""
    platform: str = "meta"
    location: str = "India"


class BudgetPlannerRequest(BaseModel):
    total_budget: float = 10000
    duration_days: int = 30
    objective: str = "conversions"
    platform: str = "meta"


class CompetitorAdsRequest(BaseModel):
    competitors: list[str] = []
    platform: str = "meta"
    industry: str = ""


class PlatformSelectionRequest(BaseModel):
    industry: str = ""
    goals: str = ""
    budget: float = 0
    audience_age: str = ""


class AdCopyRequest(BaseModel):
    product: str = ""
    platform: str = "meta"
    objective: str = "conversions"
    tone: str = "professional"
    audience: str = ""
    usp: str = ""


class CreativeBriefRequest(BaseModel):
    campaign_name: str = ""
    product: str = ""
    platform: str = "meta"
    creative_type: str = "image"
    target_audience: str = ""
    key_message: str = ""
    style: str = "bold"


class AdVariationsRequest(BaseModel):
    base_copy: str = ""
    platform: str = "meta"
    count: int = 3
    angles: list[str] = []


class LandingPageRequest(BaseModel):
    product: str = ""
    objective: str = "conversions"
    audience: str = ""
    budget: float = 0


class HashtagTagsRequest(BaseModel):
    product: str = ""
    platform: str = "meta"
    count: int = 15
    niche: str = ""


class AudienceBuilderRequest(BaseModel):
    age_min: int = 25
    age_max: int = 45
    gender: str = "all"
    location: str = "India"
    interests: list[str] = []
    behaviors: list[str] = []
    platform: str = "meta"


class LookalikeRequest(BaseModel):
    source_audience: str = "converters"
    country: str = "IN"
    percentage: float = 1.0
    platform: str = "meta"


class RetargetingRequest(BaseModel):
    website_visitors_days: int = 30
    cart_abandoners: bool = True
    video_viewers: bool = True
    engagers: bool = True
    platform: str = "meta"


class ExclusionRequest(BaseModel):
    exclude_converters: bool = True
    exclude_employees: bool = True
    custom_exclusions: list[str] = []
    platform: str = "meta"


class PerformanceAnalyzerRequest(BaseModel):
    metrics: dict[str, Any] = {}
    period: str = "7d"
    campaign_name: str = ""


class AutoOptimizeRequest(BaseModel):
    campaign_data: dict[str, Any] = {}
    rules: list[str] = []


class ABTestRequest(BaseModel):
    test_name: str = ""
    variable: str = "headline"
    variants: list[str] = []
    budget_per_variant: float = 500
    duration_days: int = 7


class CampaignReportRequest(BaseModel):
    campaign_name: str = ""
    period: str = "30d"
    metrics: dict[str, Any] = {}


class ROASCalcRequest(BaseModel):
    ad_spend: float = 0
    revenue: float = 0
    target_roas: float = 4.0
    timeframe: str = "30d"


class CreativeScoreRequest(BaseModel):
    creative_data: dict[str, Any] = {}


class BriefContentRequest(BaseModel):
    content_type: str = "image"
    topic: str = ""
    platform: str = "facebook"
    description: str = ""
    style: str = "bold"
    priority: str = "normal"
    quantity: int = 1


# ─────────


@router.get("/status")
async def ads_status():
    """Ads Agent status."""
    return {
        "success": True,
        "ads": {
            "status": "running",
            "platforms": ["Meta (Facebook + Instagram)", "Google Ads"],
            "tools_count": len(ADS_TOOLS),
            "capabilities": [
                "Campaign Strategy", "Audience Research", "Budget Planning",
                "Competitor Analysis", "Ad Copy Generation", "Creative Briefs",
                "A/B Testing", "Auto-Optimization", "Performance Analytics",
                "Retargeting", "Lookalike Audiences", "ROAS Tracking",
            ],
        },
    }


# ── Tools List ──────────────────────────────────────────────────────────────


@router.get("/tools")
async def list_ads_tools():
    """List all available ads tools."""
    tools = []
    for t in ADS_TOOLS:
        fn = t.get("function", {})
        tools.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
        })
    return {"success": True, "data": {"tools": tools, "count": len(tools)}}


# ── Strategy Tools ─────────────────────────────────────────────────────────


@router.post("/campaign-strategy")
async def api_campaign_strategy(body: CampaignStrategyRequest):
    return {"success": True, "data": campaign_strategy(**body.model_dump())}


@router.post("/audience-research")
async def api_audience_research(body: AudienceResearchRequest):
    return {"success": True, "data": audience_research(**body.model_dump())}


@router.post("/budget-planner")
async def api_budget_planner(body: BudgetPlannerRequest):
    return {"success": True, "data": budget_planner(**body.model_dump())}


@router.post("/competitor-ads")
async def api_competitor_ads(body: CompetitorAdsRequest):
    return {"success": True, "data": competitor_ads(**body.model_dump())}


@router.post("/platform-selection")
async def api_platform_selection(body: PlatformSelectionRequest):
    return {"success": True, "data": platform_selection(**body.model_dump())}


# ── Content Tools ──────────────────────────────────────────────────────────


@router.post("/ad-copy")
async def api_ad_copy(body: AdCopyRequest):
    return {"success": True, "data": ad_copy_generator(**body.model_dump())}


@router.post("/creative-brief")
async def api_creative_brief(body: CreativeBriefRequest):
    return {"success": True, "data": creative_brief(**body.model_dump())}


@router.post("/ad-variations")
async def api_ad_variations(body: AdVariationsRequest):
    return {"success": True, "data": ad_variations(**body.model_dump())}


@router.post("/landing-page")
async def api_landing_page(body: LandingPageRequest):
    return {"success": True, "data": landing_page_strategy(**body.model_dump())}


@router.post("/hashtag-tags")
async def api_hashtag_tags(body: HashtagTagsRequest):
    return {"success": True, "data": ad_hashtag_tags(**body.model_dump())}


# ── Targeting Tools ────────────────────────────────────────────────────────


@router.post("/audience-builder")
async def api_audience_builder(body: AudienceBuilderRequest):
    return {"success": True, "data": audience_builder(**body.model_dump())}


@router.post("/lookalike")
async def api_lookalike(body: LookalikeRequest):
    return {"success": True, "data": lookalike_audience(**body.model_dump())}


@router.post("/retargeting")
async def api_retargeting(body: RetargetingRequest):
    return {"success": True, "data": retargeting_setup(**body.model_dump())}


@router.post("/exclusions")
async def api_exclusions(body: ExclusionRequest):
    return {"success": True, "data": exclusion_list(**body.model_dump())}


# ── Optimization Tools ────────────────────────────────────────────────────


@router.post("/performance")
async def api_performance(body: PerformanceAnalyzerRequest):
    return {"success": True, "data": performance_analyzer(**body.model_dump())}


@router.post("/auto-optimize")
async def api_auto_optimize(body: AutoOptimizeRequest):
    return {"success": True, "data": auto_optimize(**body.model_dump())}


@router.post("/ab-test")
async def api_ab_test(body: ABTestRequest):
    return {"success": True, "data": ab_test_setup(**body.model_dump())}


# ── Reporting Tools ───────────────────────────────────────────────────────


@router.post("/report")
async def api_report(body: CampaignReportRequest):
    return {"success": True, "data": campaign_report(**body.model_dump())}


@router.post("/roas")
async def api_roas(body: ROASCalcRequest):
    return {"success": True, "data": roas_calculator(**body.model_dump())}


@router.post("/creative-score")
async def api_creative_score(body: CreativeScoreRequest):
    return {"success": True, "data": creative_score(**body.model_dump())}


# ── Content Agent Brief ───────────────────────────────────────────────────


@router.post("/brief-content-agent")
async def api_brief_content(body: BriefContentRequest):
    """Brief Content Agent for ad creative visuals via agent_bus."""
    try:
        send_message(
            from_agent="ads",
            to_agent="content",
            workspace_id="agency",
            subject=f"Ads needs {body.content_type}: {body.topic[:50]}",
            content=(
                f"Ad Creative Request:\n"
                f"- Type: {body.content_type}\n"
                f"- Topic: {body.topic}\n"
                f"- Platform: {body.platform}\n"
                f"- Description: {body.description}\n"
                f"- Style: {body.style}\n"
                f"- Priority: {body.priority}\n"
                f"- Quantity: {body.quantity}"
            ),
            message_type="brief",
            metadata={
                "content_type": body.content_type,
                "platform": body.platform,
                "style": body.style,
                "quantity": body.quantity,
                "priority": body.priority,
            },
        )
        return {
            "success": True,
            "data": {
                "status": "brief_sent",
                "to_agent": "content",
                "content_type": body.content_type,
                "topic": body.topic,
            },
        }
    except Exception as e:
        raise HTTPException(500, f"Brief failed: {e}")


# ── Real Account Connections ────────────────────────────────────────────────


class AdsConnectRequest(BaseModel):
    """Connect a real ads account for a workspace.

    Meta:
      platform="meta", access_token + ad_account_id (act_XXXXXXXXX)
    Google:
      platform="google", developer_token + client_id + client_secret +
      refresh_token + customer_id
    """
    workspace_id: str
    platform: str = "meta"
    # Meta
    access_token: str = ""
    ad_account_id: str = ""
    business_id: str = ""
    # Google
    developer_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    customer_id: str = ""
    login_customer_id: str = ""


@router.post("/connect")
async def ads_connect(body: AdsConnectRequest):
    """Save real ad account credentials for a workspace.

    Once saved, performance/report tools switch from demo to live API data
    automatically (Meta Marketing API or Google Ads API).
    """
    from admin.ads_api_client import save_meta_ads_token, save_google_ads_credentials

    if body.platform.lower() in ("meta", "facebook", "meta_ads"):
        if not body.access_token or not body.ad_account_id:
            raise HTTPException(422, "Meta connection needs access_token + ad_account_id (act_XXX)")
        result = save_meta_ads_token(
            workspace_id=body.workspace_id,
            access_token=body.access_token,
            ad_account_id=body.ad_account_id,
            business_id=body.business_id,
        )
        return {"success": True, "platform": "meta", "workspace_id": body.workspace_id, "result": result}

    elif body.platform.lower() in ("google", "google_ads", "adwords"):
        required = [body.developer_token, body.client_id, body.client_secret,
                    body.refresh_token, body.customer_id]
        if not all(required):
            raise HTTPException(422, "Google connection needs developer_token + client_id + client_secret + refresh_token + customer_id")
        result = save_google_ads_credentials(
            workspace_id=body.workspace_id,
            developer_token=body.developer_token,
            client_id=body.client_id,
            client_secret=body.client_secret,
            refresh_token=body.refresh_token,
            customer_id=body.customer_id,
            login_customer_id=body.login_customer_id,
        )
        return {"success": True, "platform": "google", "workspace_id": body.workspace_id, "result": result}

    raise HTTPException(422, f"Unknown platform: {body.platform}")


@router.get("/connection/{workspace_id}")
async def ads_connection_status(workspace_id: str):
    """Check live vs demo connection status for a workspace."""
    from admin.ads_api_client import get_all_ads_clients
    clients = get_all_ads_clients(workspace_id)
    return {
        "workspace_id": workspace_id,
        "meta": clients["meta"].health_check(),
        "google": clients["google"].health_check(),
    }
