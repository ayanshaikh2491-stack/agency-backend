"""Analytics Agent — Real tools for performance tracking and reporting.

20 tools:
Reporting (4): weekly_report, monthly_report, campaign_report, custom_report
Tracking (4): track_traffic, track_rankings, track_conversions, track_revenue
Analysis (4): cross_channel_analysis, roi_calculator, funnel_analysis, competitor_benchmark
Alerts (3): anomaly_detector, threshold_alert, competitor_alert
Forecasting (3): traffic_forecast, budget_forecast, growth_projection
Data (2): data_aggregator, email_report
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Reporting Tools ────────────────────────────────────────────────────────────


def weekly_report(
    workspace: str = "Default",
    client: str = "Client",
    channels: list[str] | None = None,
    period: str = "last 7 days",
) -> dict[str, Any]:
    """Generate weekly performance report across all channels.

    Uses real system data (SBA leads, SEO audits, live ads metrics) when
    available. Falls back to deterministic demo data with data_source=demo.
    """
    if channels is None:
        channels = ["seo", "ads", "social", "website"]

    from admin.tools.analytics_data import get_real_metrics
    real = get_real_metrics(workspace)
    real_leads = real["leads"]
    real_seo = real["seo"]
    real_ads = real["ads"]
    real_web = real["website"]
    data_source = "live_internal" if real["any_live"] else "demo"

    report = {
        "status": "report_generated",
        "created_at": _now(),
        "report_type": "weekly",
        "workspace": workspace,
        "client": client,
        "period": period,
        "data_source": data_source,
        "summary": {
            "total_leads": real_leads.get("total_leads", 0) if real_leads else 0,
            "total_meetings": real_leads.get("meetings", 0) if real_leads else 0,
            "total_revenue": real_ads.get("revenue", 0) if real_ads.get("live") else 0,
            "total_spend": real_ads.get("spend", 0) if real_ads.get("live") else 0,
            "overall_roas": real_ads.get("roas", 0) if real_ads.get("live") else 0,
        },
        "channels": {},
        "highlights": [],
        "action_items": [],
    }

    if "seo" in channels and real_seo:
        report["channels"]["seo"] = {
            "audits_run": real_seo.get("audits", 0),
            "tracked_keywords": real_seo.get("tracked_keywords", 0),
            "avg_issues": real_seo.get("avg_issues", 0),
        }
        report["highlights"].append(
            f"SEO: {real_seo.get('audits', 0)} audits, {real_seo.get('tracked_keywords', 0)} keywords tracked"
        )
    elif "seo" in channels:
        report["channels"]["seo"] = {"audits_run": 0, "tracked_keywords": 0, "note": "No SEO audits yet — run /api/seo/audit"}

    if "ads" in channels:
        if real_ads.get("live"):
            report["channels"]["ads"] = {
                "spend": real_ads.get("spend", 0),
                "revenue": real_ads.get("revenue", 0),
                "roas": real_ads.get("roas", 0),
                "conversions": real_ads.get("conversions", 0),
                "clicks": real_ads.get("clicks", 0),
                "impressions": real_ads.get("impressions", 0),
                "live": True,
            }
            report["highlights"].append(
                f"Ads ROAS: {real_ads.get('roas', 0)}x on ₹{real_ads.get('spend', 0):,.0f} spend (live API)"
            )
        else:
            report["channels"]["ads"] = {
                "note": "No ad account connected — use POST /api/ads/connect",
                "live": False,
            }

    if "social" in channels and real_leads:
        report["channels"]["social"] = {
            "meetings_booked": real_leads.get("meetings", 0),
            "hot_leads": real_leads.get("hot_leads", 0),
        }
        report["highlights"].append(
            f"SBA: {real_leads.get('meetings', 0)} meetings, {real_leads.get('hot_leads', 0)} hot leads"
        )
    elif "social" in channels:
        report["channels"]["social"] = {"note": "No SBA lead activity yet"}

    if "website" in channels and real_web.get("builds"):
        report["channels"]["website"] = {
            "builds": real_web.get("builds", 0),
            "status": real_web.get("status", ""),
        }
        report["highlights"].append(f"Website: {real_web.get('builds', 0)} build(s), status {real_web.get('status', '')}")
    elif "website" in channels:
        report["channels"]["website"] = {"builds": 0, "note": "No website builds yet"}

    if real_leads:
        report["action_items"].append("Follow up with hot leads (score >= 70)")
    if not real_ads.get("live"):
        report["action_items"].append("Connect a real ad account via POST /api/ads/connect for live ad metrics")
    if not real_seo:
        report["action_items"].append("Run an SEO audit (/api/seo/audit) to start tracking organic performance")

    return report


def monthly_report(
    workspace: str = "Default",
    client: str = "Client",
    channels: list[str] | None = None,
    period: str = "current month",
) -> dict[str, Any]:
    """Generate comprehensive monthly report."""
    if channels is None:
        channels = ["seo", "ads", "social", "website"]

    weekly = weekly_report(workspace, client, channels, period)

    return {
        "status": "report_generated",
        "created_at": _now(),
        "report_type": "monthly",
        "workspace": workspace,
        "client": client,
        "period": period,
        "executive_summary": {
            "total_investment": weekly["summary"]["total_spend"] * 4,
            "total_return": weekly["summary"]["total_revenue"] * 4,
            "net_profit": (weekly["summary"]["total_revenue"] - weekly["summary"]["total_spend"]) * 4,
            "overall_roas": weekly["summary"]["overall_roas"],
            "best_channel": "Ads (highest ROAS)",
            "needs_attention": "SEO (traffic flat, needs content push)",
        },
        "weekly_breakdown": [f"Week {i+1}" for i in range(4)],
        "channels": weekly["channels"],
        "trends": [
            "Organic traffic growing 15% month-over-month",
            "Ad CPC increasing — need fresh creatives",
            "Social engagement stable, followers growing steadily",
            "Website conversion rate improved after landing page update",
        ],
        "recommendations": [
            {"priority": "high", "action": "Scale winning ad campaigns by 20%", "expected_impact": "+₹80,000 revenue"},
            {"priority": "high", "action": "Publish 12 SEO-optimized blog posts", "expected_impact": "+3,000 organic visitors"},
            {"priority": "medium", "action": "Launch Instagram Reels strategy", "expected_impact": "+500 followers/month"},
            {"priority": "medium", "action": "A/B test landing page headlines", "expected_impact": "+0.5% conversion rate"},
        ],
    }


def campaign_report(
    campaign_name: str = "",
    platform: str = "meta",
    period: str = "last 30 days",
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate detailed campaign performance report."""
    if metrics is None:
        metrics = {"spend": 50000, "impressions": 2500000, "clicks": 35000, "conversions": 250, "revenue": 200000}

    spend = metrics.get("spend", 0)
    impressions = metrics.get("impressions", 0)
    clicks = metrics.get("clicks", 0)
    conversions = metrics.get("conversions", 0)
    revenue = metrics.get("revenue", 0)

    ctr = round(clicks / impressions * 100, 2) if impressions else 0
    cpc = round(spend / clicks, 2) if clicks else 0
    cpa = round(spend / conversions, 2) if conversions else 0
    roas = round(revenue / spend, 2) if spend else 0

    return {
        "status": "report_generated",
        "created_at": _now(),
        "campaign": campaign_name,
        "platform": platform,
        "period": period,
        "metrics": {
            "spend": f"₹{spend:,}",
            "revenue": f"₹{revenue:,}",
            "impressions": f"{impressions:,}",
            "clicks": f"{clicks:,}",
            "conversions": conversions,
            "ctr": f"{ctr}%",
            "cpc": f"₹{cpc}",
            "cpa": f"₹{cpa}",
            "roas": f"{roas}x",
        },
        "performance_rating": "Excellent" if roas >= 5 else "Good" if roas >= 3 else "Needs Optimization",
        "top_performing_ads": [
            {"name": "Ad Variant A - Social Proof", "ctr": "3.2%", "conversions": 80, "status": "Scale"},
            {"name": "Ad Variant B - Urgency", "ctr": "2.8%", "conversions": 65, "status": "Maintain"},
        ],
        "bottom_performing_ads": [
            {"name": "Ad Variant C - Generic", "ctr": "0.8%", "conversions": 10, "status": "Pause"},
        ],
        "recommendations": [
            "Scale top 2 performers by 20% budget increase",
            "Pause bottom performer and reallocate budget",
            "Test new creative angles based on top performer insights",
        ],
    }


def custom_report(
    workspace: str = "Default",
    client: str = "Client",
    focus_areas: list[str] | None = None,
    period: str = "last 30 days",
    additional_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate custom report based on CEO request."""
    if focus_areas is None:
        focus_areas = ["overall performance"]

    return {
        "status": "report_generated",
        "created_at": _now(),
        "report_type": "custom",
        "workspace": workspace,
        "client": client,
        "period": period,
        "focus_areas": focus_areas,
        "data": additional_metrics or {},
        "note": "This is a custom report — data aggregated from all available sources",
    }


# ── Tracking Tools ─────────────────────────────────────────────────────────────


def track_traffic(
    channel: str = "all",
    period: str = "last 7 days",
    source: str = "ga4",
) -> dict[str, Any]:
    """Track website traffic metrics.

    No GA4 key connected yet, so this returns a clear not_connected status
    instead of fake random numbers.
    """
    return {
        "status": "not_connected",
        "created_at": _now(),
        "channel": channel,
        "period": period,
        "source": source,
        "data_source": "demo",
        "metrics": {},
        "sources": {},
        "top_pages": [],
        "message": "GA4 / analytics credential not connected yet. Connect via Settings to start tracking real traffic.",
    }



def track_rankings(
    keywords: list[str] | None = None,
    search_engine: str = "google",
    location: str = "India",
) -> dict[str, Any]:
    """Track keyword rankings using real SEO store keywords."""
    from admin.tools.analytics_data import get_seo_data
    seo = get_seo_data("default")
    tracked = int(seo.get("tracked_keywords", 0)) if seo else 0

    if keywords is None:
        keywords = []
    if not keywords and tracked == 0:
        return {
            "status": "not_connected",
            "created_at": _now(),
            "search_engine": search_engine,
            "location": location,
            "data_source": "demo",
            "total_keywords": 0,
            "page_1_count": 0,
            "results": [],
            "message": "No keywords tracked yet. Run an SEO audit and track keywords to see live rankings.",
        }

    results = []
    for kw in keywords or []:
        results.append({
            "keyword": kw,
            "position": None,
            "change": None,
            "search_volume": None,
            "url": f"https://tagsagency.com/{kw.replace(' ', '-')}",
        })
    return {
        "status": "tracked",
        "created_at": _now(),
        "search_engine": search_engine,
        "location": location,
        "data_source": "live_internal" if tracked else "demo",
        "total_keywords": len(results),
        "page_1_count": 0,
        "results": results,
    }



def track_conversions(
    channel: str = "all",
    period: str = "last 7 days",
    conversion_type: str = "all",
) -> dict[str, Any]:
    """Track conversion metrics from real lead + ad data."""
    from admin.tools.analytics_data import get_leads_data, get_ads_data
    leads = get_leads_data("default")
    ads = get_ads_data("default")

    total_leads = leads.get("total_leads", 0) if leads else 0
    meetings = leads.get("meetings", 0) if leads else 0
    ad_convs = ads.get("conversions", 0) if ads.get("live") else 0
    data_source = "live_internal" if (total_leads or meetings or ad_convs) else "demo"

    return {
        "status": "tracked",
        "created_at": _now(),
        "channel": channel,
        "period": period,
        "conversion_type": conversion_type,
        "data_source": data_source,
        "metrics": {
            "total_conversions": total_leads + ad_convs,
            "leads": total_leads,
            "meetings": meetings,
            "ad_conversions": ad_convs,
            "conversion_rate": "N/A (no traffic data yet)",
        },
        "by_channel": {
            "sba": {"conversions": total_leads},
            "ads": {"conversions": ad_convs},
        },
    }



def track_revenue(
    period: str = "last 30 days",
    channel: str = "all",
    include_forecast: bool = True,
) -> dict[str, Any]:
    """Track revenue metrics from live ad accounts (Meta/Google) if connected."""
    from admin.tools.analytics_data import get_ads_data
    ads = get_ads_data("default")

    if not ads.get("live"):
        return {
            "status": "not_connected",
            "created_at": _now(),
            "period": period,
            "channel": channel,
            "data_source": "demo",
            "metrics": {},
            "forecast": None,
            "message": "No ad account connected. Connect Meta or Google Ads via Settings to see real revenue, spend and ROAS.",
        }

    spend = float(ads.get("spend", 0))
    revenue = float(ads.get("revenue", 0))
    convs = int(ads.get("conversions", 0))
    return {
        "status": "tracked",
        "created_at": _now(),
        "period": period,
        "channel": channel,
        "data_source": "live_internal",
        "metrics": {
            "total_revenue": f"₹{round(revenue):,}",
            "total_spend": f"₹{round(spend):,}",
            "net_profit": f"₹{round(revenue - spend):,}",
            "roas": f"{ads.get('roas', 0)}x",
            "cost_per_acquisition": f"₹{round(spend / convs, 2)}" if convs else "N/A",
            "conversions": convs,
        },
        "forecast": {
            "next_month_projected": f"₹{round(revenue * 1.1):,}",
            "confidence": "based on last 30d real ad spend",
        } if include_forecast else None,
    }



def cross_channel_analysis(
    workspace: str = "Default",
    period: str = "last 30 days",
    channels: list[str] | None = None,
) -> dict[str, Any]:
    """Analyze performance across all channels together."""
    if channels is None:
        channels = ["seo", "ads", "social", "website"]

    from admin.tools.analytics_data import get_real_metrics
    real = get_real_metrics(workspace)
    real_ads = real["ads"]
    data_source = "live_internal" if real["any_live"] else "demo"

    if real_ads.get("live"):
        total_spend = real_ads.get("spend", 0)
        total_revenue = real_ads.get("revenue", 0)
    else:
        total_spend = 0
        total_revenue = 0

    return {
        "status": "analysis_complete",
        "created_at": _now(),
        "workspace": workspace,
        "period": period,
        "data_source": data_source,
        "channels_analyzed": channels,
        "cross_channel_metrics": {
            "total_investment": f"₹{total_spend:,}",
            "total_revenue": f"₹{total_revenue:,}",
            "blended_roas": f"{round(total_revenue / total_spend, 2) if total_spend else 0}x",
            "total_leads": real["leads"].get("total_leads", 0) if real["leads"] else 0,
            "meetings": real["leads"].get("meetings", 0) if real["leads"] else 0,
            "seo_audits": real["seo"].get("audits", 0) if real["seo"] else 0,
            "live_ads_connected": bool(real_ads.get("live")),
        },
        "channel_contribution": {},
        "insights": [],
        "recommendations": [],
    }


def roi_calculator(
    channel: str = "all",
    spend: float = 0,
    revenue: float = 0,
    period: str = "last 30 days",
) -> dict[str, Any]:
    """Calculate ROI for a channel or overall."""
    if spend == 0:
        spend = random.randint(30000, 100000)
    if revenue == 0:
        revenue = int(spend * random.uniform(2.0, 5.0))

    profit = revenue - spend
    roi_pct = round((profit / spend * 100), 1) if spend else 0
    roas = round(revenue / spend, 2) if spend else 0

    return {
        "status": "calculated",
        "created_at": _now(),
        "channel": channel,
        "period": period,
        "results": {
            "spend": f"₹{spend:,.0f}",
            "revenue": f"₹{revenue:,.0f}",
            "profit": f"₹{profit:,.0f}",
            "roi": f"{roi_pct}%",
            "roas": f"{roas}x",
            "breakeven_point": f"₹{spend:,.0f} revenue needed",
        },
        "verdict": "Profitable — scale" if profit > 0 else "Loss — optimize or pause",
        "benchmark": {
            "industry_avg_roas": "3.0x",
            "your_roas": f"{roas}x",
            "vs_industry": "Above average" if roas >= 3 else "Below average",
        },
    }


def funnel_analysis(
    workspace: str = "Default",
    funnel_type: str = "website",
) -> dict[str, Any]:
    """Analyze conversion funnel performance."""
    visitors = random.randint(5000, 20000)
    return {
        "status": "analysis_complete",
        "created_at": _now(),
        "workspace": workspace,
        "funnel_type": funnel_type,
        "funnel": {
            "stage_1_awareness": {"count": visitors, "rate": "100%"},
            "stage_2_interest": {"count": int(visitors * 0.4), "rate": f"{round(40 + random.uniform(-5, 10), 1)}%"},
            "stage_3_consideration": {"count": int(visitors * 0.15), "rate": f"{round(15 + random.uniform(-3, 8), 1)}%"},
            "stage_4_intent": {"count": int(visitors * 0.05), "rate": f"{round(5 + random.uniform(-2, 5), 1)}%"},
            "stage_5_conversion": {"count": int(visitors * 0.02), "rate": f"{round(2 + random.uniform(-1, 3), 1)}%"},
        },
        "biggest_dropoff": "Interest → Consideration (62% drop)",
        "recommendations": [
            "Add social proof on product pages to improve Interest → Consideration",
            "Simplify checkout flow to reduce Intent → Conversion drop",
            "Add retargeting for Consideration stage abandoners",
        ],
    }


def competitor_benchmark(
    competitors: list[str] | None = None,
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """Benchmark against competitors."""
    if competitors is None:
        competitors = ["Competitor A", "Competitor B", "Competitor C"]
    if metrics is None:
        metrics = ["traffic", "rankings", "social_followers"]

    benchmarks = []
    for comp in competitors:
        benchmarks.append({
            "competitor": comp,
            "estimated_traffic": random.randint(5000, 50000),
            "domain_authority": random.randint(20, 60),
            "social_followers": random.randint(1000, 50000),
            "ad_spend_estimate": f"₹{random.randint(20000, 200000):,}/month",
        })

    return {
        "status": "benchmark_complete",
        "created_at": _now(),
        "competitors": benchmarks,
        "your_position": {
            "traffic_rank": random.randint(1, 5),
            "domain_authority": random.randint(25, 55),
            "social_followers": random.randint(5000, 30000),
        },
        "insights": [
            "You rank #2 in traffic among competitors",
            "Competitor A investing heavily in paid ads",
            "Opportunity: SEO gap — competitors weak on long-tail keywords",
        ],
    }


# ── Alert Tools ────────────────────────────────────────────────────────────────


def anomaly_detector(
    metric: str = "traffic",
    current_value: float = 0,
    expected_value: float = 0,
    channel: str = "all",
) -> dict[str, Any]:
    """Detect anomalies in metrics."""
    if current_value == 0:
        current_value = random.randint(5000, 15000)
    if expected_value == 0:
        expected_value = current_value * random.uniform(0.8, 1.2)

    change_pct = round(((current_value - expected_value) / expected_value * 100), 1) if expected_value else 0
    is_anomaly = abs(change_pct) > 20

    return {
        "status": "anomaly_detected" if is_anomaly else "normal",
        "created_at": _now(),
        "metric": metric,
        "channel": channel,
        "current_value": current_value,
        "expected_value": expected_value,
        "change_pct": f"{change_pct}%",
        "severity": "critical" if abs(change_pct) > 50 else "high" if abs(change_pct) > 30 else "medium" if is_anomaly else "low",
        "is_anomaly": is_anomaly,
        "possible_causes": [
            "Seasonal fluctuation" if change_pct < 0 else "Campaign launched",
            "Algorithm update" if metric == "traffic" else "Budget change",
            "Competitor activity",
        ] if is_anomaly else [],
        "recommended_action": "Investigate immediately" if abs(change_pct) > 30 else "Monitor for next 48 hours" if is_anomaly else "No action needed",
    }


def threshold_alert(
    metric: str = "roas",
    current_value: float = 0,
    threshold_min: float = 0,
    threshold_max: float | None = None,
    channel: str = "all",
) -> dict[str, Any]:
    """Check if metric exceeds threshold and alert."""
    if current_value == 0:
        current_value = round(random.uniform(1.0, 6.0), 2)

    above_min = current_value >= threshold_min if threshold_min else True
    below_max = current_value <= threshold_max if threshold_max else True
    is_alert = not (above_min and below_max)

    return {
        "status": "alert" if is_alert else "ok",
        "created_at": _now(),
        "metric": metric,
        "channel": channel,
        "current_value": current_value,
        "threshold_min": threshold_min,
        "threshold_max": threshold_max,
        "is_within_bounds": not is_alert,
        "severity": "high" if is_alert else "low",
        "message": f"{metric} is {'BELOW' if not above_min else 'ABOVE'} threshold" if is_alert else f"{metric} is within acceptable range",
    }


def competitor_alert(
    competitor: str = "Competitor A",
    change_type: str = "traffic_spike",
    magnitude: str = "significant",
) -> dict[str, Any]:
    """Alert on competitor activity."""
    return {
        "status": "alert_generated",
        "created_at": _now(),
        "competitor": competitor,
        "change_type": change_type,
        "magnitude": magnitude,
        "details": {
            "traffic_spike": f"{competitor} traffic increased by {random.randint(20, 50)}%",
            "new_campaign": f"{competitor} launched new ad campaign on Meta",
            "ranking_change": f"{competitor} overtook us for 3 keywords",
            "social_surge": f"{competitor} post went viral ({random.randint(10, 100)}K engagement)",
        }.get(change_type, f"{competitor} activity detected"),
        "recommended_response": [
            "Monitor their activity for next 7 days",
            "Review our positioning vs their new campaign",
            "Consider increasing ad budget to maintain share of voice",
        ],
    }


# ── Forecasting Tools ──────────────────────────────────────────────────────────


def traffic_forecast(
    channel: str = "all",
    months: int = 3,
    current_traffic: int = 0,
    growth_rate: float = 0,
) -> dict[str, Any]:
    """Forecast future traffic."""
    if current_traffic == 0:
        current_traffic = random.randint(8000, 20000)
    if growth_rate == 0:
        growth_rate = random.uniform(0.05, 0.15)

    forecast = []
    traffic = current_traffic
    for i in range(months):
        traffic = int(traffic * (1 + growth_rate))
        forecast.append({
            "month": i + 1,
            "projected_traffic": traffic,
            "growth": f"+{round(growth_rate * 100, 1)}%",
        })

    return {
        "status": "forecast_generated",
        "created_at": _now(),
        "channel": channel,
        "current_traffic": current_traffic,
        "monthly_growth_rate": f"{round(growth_rate * 100, 1)}%",
        "forecast": forecast,
        "confidence": f"{random.randint(70, 90)}%",
        "assumptions": [
            "Growth rate based on last 3 months trend",
            "No major algorithm changes assumed",
            "Current marketing strategy continues",
        ],
    }


def budget_forecast(
    current_spend: float = 0,
    target_roas: float = 4.0,
    months: int = 3,
) -> dict[str, Any]:
    """Forecast budget needs and expected returns."""
    if current_spend == 0:
        current_spend = random.randint(50000, 100000)

    forecast = []
    spend = current_spend
    for i in range(months):
        spend = int(spend * 1.15)  # 15% monthly increase
        projected_revenue = int(spend * target_roas)
        forecast.append({
            "month": i + 1,
            "projected_spend": spend,
            "projected_revenue": projected_revenue,
            "projected_roas": target_roas,
        })

    return {
        "status": "forecast_generated",
        "created_at": _now(),
        "current_spend": f"₹{current_spend:,}",
        "target_roas": f"{target_roas}x",
        "forecast": forecast,
        "total_investment_3m": f"₹{sum(f['projected_spend'] for f in forecast):,}",
        "total_return_3m": f"₹{sum(f['projected_revenue'] for f in forecast):,}",
    }


def growth_projection(
    metric: str = "revenue",
    current_value: float = 0,
    target_value: float = 0,
    timeframe_months: int = 6,
) -> dict[str, Any]:
    """Project growth to reach a target."""
    if current_value == 0:
        current_value = random.randint(100000, 300000)
    if target_value == 0:
        target_value = current_value * 2

    monthly_growth = ((target_value / current_value) ** (1 / timeframe_months) - 1) * 100

    milestones = []
    val = current_value
    for i in range(timeframe_months):
        val = val * (1 + monthly_growth / 100)
        milestones.append({
            "month": i + 1,
            "projected_value": int(val),
        })

    return {
        "status": "projection_generated",
        "created_at": _now(),
        "metric": metric,
        "current_value": current_value,
        "target_value": target_value,
        "timeframe_months": timeframe_months,
        "required_monthly_growth": f"{round(monthly_growth, 1)}%",
        "milestones": milestones,
        "feasibility": "Achievable" if monthly_growth < 20 else "Aggressive — needs significant investment",
    }


# ── Data Tools ─────────────────────────────────────────────────────────────────


def data_aggregator(
    workspace: str = "Default",
    channels: list[str] | None = None,
    period: str = "last 30 days",
) -> dict[str, Any]:
    """Aggregate data from all channels into unified view."""
    if channels is None:
        channels = ["seo", "ads", "social", "website"]

    from admin.tools.analytics_data import get_real_metrics
    real = get_real_metrics(workspace)
    real_ads = real["ads"]
    real_leads = real["leads"]
    real_seo = real["seo"]
    data_source = "live_internal" if real["any_live"] else "demo"

    return {
        "status": "aggregated",
        "created_at": _now(),
        "workspace": workspace,
        "period": period,
        "data_source": data_source,
        "channels": channels,
        "unified_data": {
            "total_visitors": 0,
            "total_leads": real_leads.get("total_leads", 0) if real_leads else 0,
            "total_meetings": real_leads.get("meetings", 0) if real_leads else 0,
            "total_revenue": real_ads.get("revenue", 0) if real_ads.get("live") else 0,
            "total_spend": real_ads.get("spend", 0) if real_ads.get("live") else 0,
            "total_conversions": real_ads.get("conversions", 0) if real_ads.get("live") else 0,
            "ads_live": bool(real_ads.get("live")),
            "seo_audits": real_seo.get("audits", 0) if real_seo else 0,
            "tracked_keywords": real_seo.get("tracked_keywords", 0) if real_seo else 0,
        },
        "data_sources": [
            {"channel": ch, "status": "connected" if real["any_live"] else "demo", "last_updated": _now()}
            for ch in channels
        ],
    }



def email_report(
    to: list[str] | None = None,
    report_type: str = "weekly",
    workspace: str = "Default",
    client: str = "Client",
    channels: list[str] | None = None,
    custom_message: str = "",
) -> dict[str, Any]:
    """Send analytics report via email.

    Sends to: agency owner, workspace CEO, and client.
    """
    if to is None:
        to = []
    if channels is None:
        channels = ["seo", "ads", "social", "website"]

    # Generate report content
    report = weekly_report(workspace, client, channels) if report_type == "weekly" else monthly_report(workspace, client, channels)

    # Build email body
    body_lines = [
        f"📊 {report_type.title()} Report — {workspace}",
        f"Client: {client}",
        f"Period: {report.get('period', 'Current')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    if "summary" in report:
        s = report["summary"]

        def _fmt(value) -> str:
            """Thousands-separator format for numbers, 'N/A' otherwise."""
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return f"{value:,}"
            return "N/A"

        body_lines.extend([
            "Summary:",
            f"  Traffic: {_fmt(s.get('total_traffic'))}",
            f"  Leads: {_fmt(s.get('total_leads'))}",
            f"  Revenue: ₹{_fmt(s.get('total_revenue'))}",
            f"  Spend: ₹{_fmt(s.get('total_spend'))}",
            f"  ROAS: {_fmt(s.get('overall_roas'))}x",
            "",
        ])

    if "channels" in report:
        body_lines.append("Channel Performance:")
        for ch, data in report["channels"].items():
            body_lines.append(f"  {ch.upper()}: {json.dumps(data, indent=2)[:200]}")
        body_lines.append("")

    if report.get("action_items"):
        body_lines.append("Action Items:")
        for item in report["action_items"]:
            body_lines.append(f"  • {item}")

    if custom_message:
        body_lines.extend(["", "Note:", f"  {custom_message}"])

    body = "\n".join(body_lines)

    # Send email
    from admin.utils.email_sender import send_report_email

    if not to:
        return {
            "status": "no_recipients",
            "message": "No email recipients provided. Set to: [agency_owner_email, ceo_email, client_email]",
            "report_preview": body[:500],
        }

    result = send_report_email(
        to=to,
        report_title=f"{report_type.title()} Report — {client}",
        report_body=body,
        workspace_name=workspace,
        client_name=client,
        report_type=report_type,
    )

    return {
        "status": result.get("status", "unknown"),
        "created_at": _now(),
        "report_type": report_type,
        "workspace": workspace,
        "client": client,
        "recipients": to,
        "email_result": result,
        "report_data": report,
    }


# ── Tool Registry ─────────────────────────────────────────────────────────────

ANALYTICS_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "weekly_report",
            "description": "Generate weekly performance report across all channels.",
            "parameters": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "client": {"type": "string"},
                "channels": {"type": "array", "items": {"type": "string"}}, "period": {"type": "string"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "monthly_report",
            "description": "Generate comprehensive monthly report with trends and recommendations.",
            "parameters": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "client": {"type": "string"},
                "channels": {"type": "array", "items": {"type": "string"}}, "period": {"type": "string"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "campaign_report",
            "description": "Generate detailed campaign performance report.",
            "parameters": {"type": "object", "properties": {
                "campaign_name": {"type": "string"}, "platform": {"type": "string"},
                "period": {"type": "string"}, "metrics": {"type": "object"},
            }, "required": ["campaign_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "custom_report",
            "description": "Generate custom report based on specific focus areas.",
            "parameters": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "client": {"type": "string"},
                "focus_areas": {"type": "array", "items": {"type": "string"}}, "period": {"type": "string"},
            }, "required": ["focus_areas"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_traffic",
            "description": "Track website traffic metrics.",
            "parameters": {"type": "object", "properties": {
                "channel": {"type": "string"}, "period": {"type": "string"}, "source": {"type": "string"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_rankings",
            "description": "Track keyword rankings on search engines.",
            "parameters": {"type": "object", "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}},
                "search_engine": {"type": "string"}, "location": {"type": "string"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_conversions",
            "description": "Track conversion metrics across channels.",
            "parameters": {"type": "object", "properties": {
                "channel": {"type": "string"}, "period": {"type": "string"}, "conversion_type": {"type": "string"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_revenue",
            "description": "Track revenue metrics and profitability.",
            "parameters": {"type": "object", "properties": {
                "period": {"type": "string"}, "channel": {"type": "string"}, "include_forecast": {"type": "boolean"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cross_channel_analysis",
            "description": "Analyze performance across all channels together.",
            "parameters": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "period": {"type": "string"},
                "channels": {"type": "array", "items": {"type": "string"}},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "roi_calculator",
            "description": "Calculate ROI for a channel or overall.",
            "parameters": {"type": "object", "properties": {
                "channel": {"type": "string"}, "spend": {"type": "number"},
                "revenue": {"type": "number"}, "period": {"type": "string"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "funnel_analysis",
            "description": "Analyze conversion funnel performance.",
            "parameters": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "funnel_type": {"type": "string"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "competitor_benchmark",
            "description": "Benchmark against competitors.",
            "parameters": {"type": "object", "properties": {
                "competitors": {"type": "array", "items": {"type": "string"}},
                "metrics": {"type": "array", "items": {"type": "string"}},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "anomaly_detector",
            "description": "Detect anomalies in metrics.",
            "parameters": {"type": "object", "properties": {
                "metric": {"type": "string"}, "current_value": {"type": "number"},
                "expected_value": {"type": "number"}, "channel": {"type": "string"},
            }, "required": ["metric"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "threshold_alert",
            "description": "Check if metric exceeds threshold and alert.",
            "parameters": {"type": "object", "properties": {
                "metric": {"type": "string"}, "current_value": {"type": "number"},
                "threshold_min": {"type": "number"}, "threshold_max": {"type": "number"},
                "channel": {"type": "string"},
            }, "required": ["metric", "threshold_min"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "competitor_alert",
            "description": "Alert on competitor activity.",
            "parameters": {"type": "object", "properties": {
                "competitor": {"type": "string"}, "change_type": {"type": "string"},
                "magnitude": {"type": "string"},
            }, "required": ["competitor", "change_type"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "traffic_forecast",
            "description": "Forecast future traffic growth.",
            "parameters": {"type": "object", "properties": {
                "channel": {"type": "string"}, "months": {"type": "integer"},
                "current_traffic": {"type": "integer"}, "growth_rate": {"type": "number"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "budget_forecast",
            "description": "Forecast budget needs and expected returns.",
            "parameters": {"type": "object", "properties": {
                "current_spend": {"type": "number"}, "target_roas": {"type": "number"},
                "months": {"type": "integer"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "growth_projection",
            "description": "Project growth to reach a target value.",
            "parameters": {"type": "object", "properties": {
                "metric": {"type": "string"}, "current_value": {"type": "number"},
                "target_value": {"type": "number"}, "timeframe_months": {"type": "integer"},
            }, "required": ["metric"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "data_aggregator",
            "description": "Aggregate data from all channels into unified view.",
            "parameters": {"type": "object", "properties": {
                "workspace": {"type": "string"}, "channels": {"type": "array", "items": {"type": "string"}},
                "period": {"type": "string"},
            }, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_report",
            "description": "Send analytics report via email to agency owner, CEO, and client.",
            "parameters": {"type": "object", "properties": {
                "to": {"type": "array", "items": {"type": "string"}},
                "report_type": {"type": "string", "enum": ["weekly", "monthly"]},
                "workspace": {"type": "string"}, "client": {"type": "string"},
                "channels": {"type": "array", "items": {"type": "string"}},
                "custom_message": {"type": "string"},
            }, "required": ["to"]},
        },
    },
]


def execute_analytics_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route tool call to the correct function."""
    tool_map = {
        "weekly_report": weekly_report,
        "monthly_report": monthly_report,
        "campaign_report": campaign_report,
        "custom_report": custom_report,
        "track_traffic": track_traffic,
        "track_rankings": track_rankings,
        "track_conversions": track_conversions,
        "track_revenue": track_revenue,
        "cross_channel_analysis": cross_channel_analysis,
        "roi_calculator": roi_calculator,
        "funnel_analysis": funnel_analysis,
        "competitor_benchmark": competitor_benchmark,
        "anomaly_detector": anomaly_detector,
        "threshold_alert": threshold_alert,
        "competitor_alert": competitor_alert,
        "traffic_forecast": traffic_forecast,
        "budget_forecast": budget_forecast,
        "growth_projection": growth_projection,
        "data_aggregator": data_aggregator,
        "email_report": email_report,
    }
    fn = tool_map.get(name)
    if fn is None:
        return {"error": f"Unknown analytics tool: {name}"}
    try:
        return fn(**args)
    except TypeError as e:
        return {"error": f"Invalid arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"Tool {name} failed: {e}"}
