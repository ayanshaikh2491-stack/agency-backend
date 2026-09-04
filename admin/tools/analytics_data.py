"""Analytics Data — Real data aggregator for the Analytics Agent.

Pulls REAL metrics from the system's own stores instead of random numbers:

  - SBA leads / meetings  (admin.agency.sba_store)
  - SEO audits / keywords  (admin.agency.seo_store)
  - Website builds         (admin.agency.website_supabase)
  - Live ads metrics       (admin.ads_api_client, when tokens are connected)

Each function returns {} (or None) when no real data exists, so callers can
fall back to demo/provided data with a clear data_source flag.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def workspace_to_id(workspace: str) -> str:
    """Map a workspace display name to its id (slug)."""
    if not workspace:
        return "default"
    ws = workspace.strip().lower()
    return ws.replace(" ", "_").replace("-", "_")


def _safe(fn, default):
    try:
        return fn()
    except Exception as e:
        logger.debug("analytics_data: %s failed: %s", getattr(fn, "__name__", "?"), e)
        return default


def get_leads_data(workspace: str) -> dict[str, Any]:
    """Real lead + meeting counts from SBA store."""
    def load():
        from admin.agency import sba_store
        leads = sba_store.list_leads()
        meetings = sba_store.list_meetings()
        by_status: dict[str, int] = {}
        for lead in leads:
            st = (lead.get("status") or "new").lower()
            by_status[st] = by_status.get(st, 0) + 1
        hot = sum(1 for lead in leads if (lead.get("score") or 0) >= 70)
        return {
            "total_leads": len(leads),
            "by_status": by_status,
            "meetings": len(meetings),
            "hot_leads": hot,
            "new_leads": by_status.get("new", 0),
        }
    return _safe(load, {})


def get_seo_data(workspace: str) -> dict[str, Any]:
    """Real SEO audit + tracked keyword counts."""
    def load():
        from admin.agency import seo_store
        ws_id = workspace_to_id(workspace)
        audits = seo_store.list_audits(ws_id)
        keywords = seo_store.get_tracked_keywords(ws_id)
        issue_total = 0
        for a in audits:
            if a.get("issues_count") is not None:
                issue_total += int(a["issues_count"])
            elif isinstance(a.get("issues"), list):
                issue_total += len(a["issues"])
        return {
            "audits": len(audits),
            "tracked_keywords": len(keywords or []),
            "avg_issues": round(issue_total / len(audits), 1) if audits else 0,
        }
    return _safe(load, {})


def get_ads_data(workspace: str) -> dict[str, Any]:
    """Real live ad metrics from Meta/Google Ads API (if connected)."""
    def load():
        from admin.ads_api_client import get_ads_client, get_all_ads_clients
        ws_id = workspace_to_id(workspace)
        clients = get_all_ads_clients(ws_id)
        meta, google = clients["meta"], clients["google"]
        result: dict[str, Any] = {"meta_live": meta.is_live, "google_live": google.is_live}
        total_spend = total_rev = total_conv = total_clicks = total_imp = 0.0
        for client in (meta, google):
            if not client.is_live:
                continue
            metrics = None
            if client is google:
                metrics = client.get_campaign_metrics(days=30)
            else:
                insights = client.get_account_insights(date_preset="last_30d")
                if insights:
                    conv = 0
                    rev = 0.0
                    for action in insights.get("actions", []):
                        if action.get("action_type") in ("offsite_conversion", "purchase"):
                            conv += int(action.get("value", 0))
                    for attr in insights.get("action_values", []):
                        if attr.get("action_type") in ("offsite_conversion", "purchase"):
                            rev += float(attr.get("value", 0))
                    metrics = {
                        "impressions": int(insights.get("impressions", 0)),
                        "clicks": int(insights.get("clicks", 0)),
                        "spend": float(insights.get("spend", 0)),
                        "conversions": conv,
                        "revenue": rev,
                    }
            if metrics:
                total_spend += metrics.get("spend", 0)
                total_rev += metrics.get("revenue", 0)
                total_conv += metrics.get("conversions", 0)
                total_clicks += metrics.get("clicks", 0)
                total_imp += metrics.get("impressions", 0)
        if not (meta.is_live or google.is_live):
            return {"live": False}
        result.update({
            "live": True,
            "spend": total_spend,
            "revenue": total_rev,
            "conversions": total_conv,
            "clicks": total_clicks,
            "impressions": total_imp,
            "roas": round(total_rev / total_spend, 2) if total_spend else 0,
        })
        return result
    return _safe(load, {"live": False})


def get_website_data(workspace: str) -> dict[str, Any]:
    """Real website build records from Supabase bridge."""
    def load():
        from admin.agency import website_supabase
        ws = workspace
        builds = website_supabase.get_website_build(ws, ws)
        if not builds:
            return {"builds": 0, "status": ""}
        return {"builds": 1, "status": builds.get("status", "")}
    return _safe(load, {"builds": 0})


def get_real_metrics(workspace: str) -> dict[str, Any]:
    """Aggregate all real metrics for a workspace.

    Returns dict with keys: leads, seo, ads, website, any_live.
    Empty dicts mean "no real data available" for that channel.
    """
    leads = get_leads_data(workspace)
    seo = get_seo_data(workspace)
    ads = get_ads_data(workspace)
    website = get_website_data(workspace)
    has_leads = bool(leads.get("total_leads")) or bool(leads.get("meetings"))
    has_seo = bool(seo.get("audits")) or bool(seo.get("tracked_keywords"))
    has_ads = bool(ads.get("live")) and (bool(ads.get("spend")) or bool(ads.get("conversions")))
    has_website = bool(website.get("builds"))
    any_live = has_leads or has_seo or has_ads or has_website
    return {
        "workspace": workspace,
        "leads": leads,
        "seo": seo,
        "ads": ads,
        "website": website,
        "any_live": any_live,
    }
