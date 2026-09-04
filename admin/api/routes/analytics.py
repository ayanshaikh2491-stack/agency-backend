"""Analytics Agent — API routes for performance tracking and reporting.

20 tool endpoints + status + tools list + email report
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from admin.tools.analytics_tools import (
    ANALYTICS_TOOLS,
    execute_analytics_tool,
    weekly_report,
    monthly_report,
    campaign_report,
    custom_report,
    track_traffic,
    track_rankings,
    track_conversions,
    track_revenue,
    cross_channel_analysis,
    roi_calculator,
    funnel_analysis,
    competitor_benchmark,
    anomaly_detector,
    threshold_alert,
    competitor_alert,
    traffic_forecast,
    budget_forecast,
    growth_projection,
    data_aggregator,
    email_report,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class WeeklyReportRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    channels: list[str] = []
    period: str = "last 7 days"


class MonthlyReportRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    channels: list[str] = []
    period: str = "current month"


class CampaignReportRequest(BaseModel):
    campaign_name: str = ""
    platform: str = "meta"
    period: str = "last 30 days"
    metrics: dict[str, Any] = {}


class CustomReportRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    focus_areas: list[str] = []
    period: str = "last 30 days"


class TrackTrafficRequest(BaseModel):
    channel: str = "all"
    period: str = "last 7 days"
    source: str = "ga4"


class TrackRankingsRequest(BaseModel):
    keywords: list[str] = []
    search_engine: str = "google"
    location: str = "India"


class TrackConversionsRequest(BaseModel):
    channel: str = "all"
    period: str = "last 7 days"
    conversion_type: str = "all"


class TrackRevenueRequest(BaseModel):
    period: str = "last 30 days"
    channel: str = "all"
    include_forecast: bool = True


class CrossChannelRequest(BaseModel):
    workspace: str = "Default"
    period: str = "last 30 days"
    channels: list[str] = []


class ROIRequest(BaseModel):
    channel: str = "all"
    spend: float = 0
    revenue: float = 0
    period: str = "last 30 days"


class FunnelRequest(BaseModel):
    workspace: str = "Default"
    funnel_type: str = "website"


class BenchmarkRequest(BaseModel):
    competitors: list[str] = []
    metrics: list[str] = []


class AnomalyRequest(BaseModel):
    metric: str = "traffic"
    current_value: float = 0
    expected_value: float = 0
    channel: str = "all"


class ThresholdRequest(BaseModel):
    metric: str = "roas"
    current_value: float = 0
    threshold_min: float = 0
    threshold_max: float | None = None
    channel: str = "all"


class CompetitorAlertRequest(BaseModel):
    competitor: str = "Competitor A"
    change_type: str = "traffic_spike"
    magnitude: str = "significant"


class TrafficForecastRequest(BaseModel):
    channel: str = "all"
    months: int = 3
    current_traffic: int = 0
    growth_rate: float = 0


class BudgetForecastRequest(BaseModel):
    current_spend: float = 0
    target_roas: float = 4.0
    months: int = 3


class GrowthProjectionRequest(BaseModel):
    metric: str = "revenue"
    current_value: float = 0
    target_value: float = 0
    timeframe_months: int = 6


class DataAggregatorRequest(BaseModel):
    workspace: str = "Default"
    channels: list[str] = []
    period: str = "last 30 days"


class EmailReportRequest(BaseModel):
    to: list[str] = []
    report_type: str = "weekly"
    workspace: str = "Default"
    client: str = "Client"
    channels: list[str] = []
    custom_message: str = ""


# ─────────


@router.get("/status")
async def analytics_status():
    """Analytics Agent status."""
    return {
        "success": True,
        "analytics": {
            "status": "running",
            "tools_count": len(ANALYTICS_TOOLS),
            "capabilities": [
                "Weekly/Monthly Reports", "Campaign Reports", "Traffic Tracking",
                "Rankings Tracking", "Conversion Tracking", "Revenue Tracking",
                "Cross-Channel Analysis", "ROI Calculation", "Funnel Analysis",
                "Competitor Benchmarking", "Anomaly Detection", "Threshold Alerts",
                "Traffic Forecasting", "Budget Forecasting", "Growth Projections",
                "Email Reports",
            ],
        },
    }


@router.get("/tools")
async def list_analytics_tools():
    """List all available analytics tools."""
    tools = []
    for t in ANALYTICS_TOOLS:
        fn = t.get("function", {})
        tools.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
        })
    return {"success": True, "data": {"tools": tools, "count": len(tools)}}


# ── Reporting Tools ─────────────────────────────────────────────────────────


@router.post("/weekly-report")
async def api_weekly_report(body: WeeklyReportRequest):
    return {"success": True, "data": weekly_report(**body.model_dump())}


@router.post("/monthly-report")
async def api_monthly_report(body: MonthlyReportRequest):
    return {"success": True, "data": monthly_report(**body.model_dump())}


@router.post("/campaign-report")
async def api_campaign_report(body: CampaignReportRequest):
    return {"success": True, "data": campaign_report(**body.model_dump())}


@router.post("/custom-report")
async def api_custom_report(body: CustomReportRequest):
    return {"success": True, "data": custom_report(**body.model_dump())}


# ── Tracking Tools ───────────────────────────────────────────────────────────


@router.post("/track-traffic")
async def api_track_traffic(body: TrackTrafficRequest):
    return {"success": True, "data": track_traffic(**body.model_dump())}


@router.post("/track-rankings")
async def api_track_rankings(body: TrackRankingsRequest):
    return {"success": True, "data": track_rankings(**body.model_dump())}


@router.post("/track-conversions")
async def api_track_conversions(body: TrackConversionsRequest):
    return {"success": True, "data": track_conversions(**body.model_dump())}


@router.post("/track-revenue")
async def api_track_revenue(body: TrackRevenueRequest):
    return {"success": True, "data": track_revenue(**body.model_dump())}


# ── Analysis Tools ───────────────────────────────────────────────────────────


@router.post("/cross-channel")
async def api_cross_channel(body: CrossChannelRequest):
    return {"success": True, "data": cross_channel_analysis(**body.model_dump())}


@router.post("/roi")
async def api_roi(body: ROIRequest):
    return {"success": True, "data": roi_calculator(**body.model_dump())}


@router.post("/funnel")
async def api_funnel(body: FunnelRequest):
    return {"success": True, "data": funnel_analysis(**body.model_dump())}


@router.post("/competitor-benchmark")
async def api_benchmark(body: BenchmarkRequest):
    return {"success": True, "data": competitor_benchmark(**body.model_dump())}


# ── Alert Tools ──────────────────────────────────────────────────────────────


@router.post("/anomaly")
async def api_anomaly(body: AnomalyRequest):
    return {"success": True, "data": anomaly_detector(**body.model_dump())}


@router.post("/threshold")
async def api_threshold(body: ThresholdRequest):
    return {"success": True, "data": threshold_alert(**body.model_dump())}


@router.post("/competitor-alert")
async def api_competitor_alert(body: CompetitorAlertRequest):
    return {"success": True, "data": competitor_alert(**body.model_dump())}


# ── Forecast───────────────────────


@router.post("/traffic-forecast")
async def api_traffic_forecast(body: TrafficForecastRequest):
    return {"success": True, "data": traffic_forecast(**body.model_dump())}


@router.post("/budget-forecast")
async def api_budget_forecast(body: BudgetForecastRequest):
    return {"success": True, "data": budget_forecast(**body.model_dump())}


@router.post("/growth-projection")
async def api_growth_projection(body: GrowthProjectionRequest):
    return {"success": True, "data": growth_projection(**body.model_dump())}


# ── Data Tools ───────────────────────────────────────────────────────────────


@router.post("/aggregate")
async def api_aggregate(body: DataAggregatorRequest):
    return {"success": True, "data": data_aggregator(**body.model_dump())}


@router.post("/email-report")
async def api_email_report(body: EmailReportRequest):
    """Send analytics report via email."""
    result = email_report(**body.model_dump())
    if result.get("status") == "no_recipients":
        return {"success": False, "error": result.get("message", "No recipients")}
    return {"success": True, "data": result}
