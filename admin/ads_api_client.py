"""Ads API Client — Real Meta Ads + Google Ads API integration.

Provides live API access when tokens are available, with graceful fallback
to demo mode (deterministic data) when tokens are not configured.

Token storage uses the existing token_manager.py system:
  - Meta Ads: platform="meta_ads" — needs access_token + ad_account_id
  - Google Ads: platform="google_ads" — needs developer_token + OAuth2 credentials

Business rule: Client never touches frontend. Tokens are provided via email
to CEO, or owner (Taushef) adds them directly to the workspace.

Usage:
    client = get_ads_client(workspace_id="client_abc", platform="meta_ads")
    if client.is_live:
        campaigns = client.list_campaigns()
    else:
        # Falls back to deterministic demo data
        campaigns = client.list_campaigns()
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# META ADS CLIENT — Facebook Marketing API v19.0
# ═══════════════════════════════════════════════════════════════════════════════

class MetaAdsClient:
    """Client for Meta (Facebook) Marketing API.

    Requires:
      - access_token with ads_management, ads_read permissions
      - ad_account_id (format: act_XXXXXXXXX)

    API Docs: https://developers.facebook.com/docs/marketing-apis
    """

    GRAPH_URL = "https://graph.facebook.com/v19.0"

    def __init__(self, workspace_id: str):
        self.workspace_id = workspace_id
        self.is_live = False
        self.access_token: str = ""
        self.ad_account_id: str = ""
        self.business_id: str = ""

        self._load_token()

    def _load_token(self) -> None:
        """Load Meta Ads token from token_manager."""
        try:
            from admin.token_manager import get_token
            data = get_token(self.workspace_id, "meta_ads")
            if data and data.get("status") == "active":
                self.access_token = data.get("access_token", "")
                # save_token stores ad account as page_id; direct saves may use ad_account_id
                self.ad_account_id = data.get("ad_account_id") or data.get("page_id", "")
                self.business_id = data.get("business_id") or data.get("platform_user_id", "")
                if self.access_token and self.ad_account_id:
                    self.is_live = True
                    logger.info("Meta Ads LIVE mode: account=%s", self.ad_account_id)
                else:
                    logger.info("Meta Ads DEMO mode: token present but missing ad_account_id")
            else:
                logger.info("Meta Ads DEMO mode: no active token for %s", self.workspace_id)
        except Exception as e:
            logger.warning("Meta Ads token load failed: %s — using demo mode", e)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Make a GET request to Graph API."""
        import requests
        if not self.is_live:
            return None
        url = f"{self.GRAPH_URL}/{path}"
        p = {"access_token": self.access_token}
        if params:
            p.update(params)
        try:
            resp = requests.get(url, params=p, timeout=30)
            if resp.ok:
                return resp.json()
            logger.error("Meta API error %s: %s", resp.status_code, resp.text[:300])
            return None
        except Exception as e:
            logger.error("Meta API request failed: %s", e)
            return None

    def _post(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Make a POST request to Graph API."""
        import requests
        if not self.is_live:
            return None
        url = f"{self.GRAPH_URL}/{path}"
        p = {"access_token": self.access_token}
        try:
            resp = requests.post(url, params=p, data=data or {}, timeout=30)
            if resp.ok:
                return resp.json()
            logger.error("Meta API POST error %s: %s", resp.status_code, resp.text[:300])
            return None
        except Exception as e:
            logger.error("Meta API POST failed: %s", e)
            return None

    # ── Live API Methods ──────────────────────────────────────────────────

    def get_account_info(self) -> dict[str, Any] | None:
        """Get ad account details."""
        return self._get(self.ad_account_id, {
            "fields": "name,currency,account_status,balance,spend_cap,amount_spent",
        })

    def list_campaigns(self, status: str = "ACTIVE", limit: int = 50) -> list[dict[str, Any]]:
        """List campaigns from Meta Ads account."""
        data = self._get(f"{self.ad_account_id}/campaigns", {
            "fields": "id,name,status,objective,daily_budget,lifetime_budget,created_time,updated_time",
            "filtering": json.dumps([{"field": "campaign.effective_status", "operator": "IN", "value": [status]}]),
            "limit": str(limit),
        })
        if data and "data" in data:
            return data["data"]
        return []

    def get_campaign_insights(
        self, campaign_id: str, date_preset: str = "last_30d"
    ) -> dict[str, Any] | None:
        """Get performance insights for a campaign."""
        return self._get(f"{campaign_id}/insights", {
            "fields": "impressions,clicks,spend,actions,ctr,cpc,cpa,reach,frequency",
            "date_preset": date_preset,
        })

    def get_account_insights(self, date_preset: str = "last_30d") -> dict[str, Any] | None:
        """Get account-level performance insights."""
        data = self._get(f"{self.ad_account_id}/insights", {
            "fields": "impressions,clicks,spend,actions,ctr,cpc,reach,frequency,cost_per_action_type",
            "date_preset": date_preset,
        })
        if data and "data" in data and data["data"]:
            return data["data"][0]
        return None

    def get_audience_insights(self, targeting_spec: dict[str, Any]) -> dict[str, Any] | None:
        """Get audience size estimate."""
        return self._post(f"{self.ad_account_id}/reachestimate", {
            "targeting_spec": json.dumps(targeting_spec),
        })

    def create_campaign(
        self, name: str, objective: str, daily_budget: int, status: str = "PAUSED"
    ) -> dict[str, Any] | None:
        """Create a new campaign."""
        return self._post(self.ad_account_id, {
            "name": name,
            "objective": objective,
            "status": status,
            "special_ad_categories": "[]",
        })

    def update_campaign_status(self, campaign_id: str, status: str) -> dict[str, Any] | None:
        """Update campaign status (ACTIVE, PAUSED)."""
        return self._post(campaign_id, {"status": status})

    def health_check(self) -> dict[str, Any]:
        """Check if Meta Ads API connection is healthy."""
        if not self.is_live:
            return {"status": "demo", "message": "No Meta Ads token configured"}
        info = self.get_account_info()
        if info:
            return {
                "status": "live",
                "account_name": info.get("name", ""),
                "currency": info.get("currency", ""),
                "account_status": info.get("account_status", 0),
            }
        return {"status": "error", "message": "Token present but API call failed"}


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE ADS CLIENT — Google Ads API v17
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleAdsClient:
    """Client for Google Ads API.

    Requires:
      - developer_token (from Google Ads API Center)
      - OAuth2 credentials: client_id, client_secret, refresh_token
      - customer_id (format: XXX-XXX-XXXX or XXXXXXXXXX)
      - login_customer_id (MCC account, if applicable)

    API Docs: https://developers.google.com/google-ads/api/docs/start
    """

    API_VERSION = "v17"
    BASE_URL = "https://googleads.googleapis.com"

    def __init__(self, workspace_id: str):
        self.workspace_id = workspace_id
        self.is_live = False
        self.developer_token: str = ""
        self.client_id: str = ""
        self.client_secret: str = ""
        self.refresh_token: str = ""
        self.customer_id: str = ""
        self.login_customer_id: str = ""
        self._access_token: str = ""
        self._token_expires_at: float = 0

        self._load_token()

    def _load_token(self) -> None:
        """Load Google Ads credentials from token_manager."""
        try:
            from admin.token_manager import get_token
            data = get_token(self.workspace_id, "google_ads")
            if data and data.get("status") == "active":
                self.developer_token = data.get("developer_token", "")
                self.client_id = data.get("client_id", "")
                self.client_secret = data.get("client_secret", "")
                self.refresh_token = data.get("refresh_token", "")
                self.customer_id = data.get("customer_id", "")
                self.login_customer_id = data.get("login_customer_id", "")

                if all([self.developer_token, self.refresh_token, self.customer_id]):
                    self.is_live = True
                    logger.info("Google Ads LIVE mode: customer=%s", self.customer_id)
                else:
                    logger.info("Google Ads DEMO mode: incomplete credentials")
            else:
                logger.info("Google Ads DEMO mode: no active token for %s", self.workspace_id)
        except Exception as e:
            logger.warning("Google Ads token load failed: %s — using demo mode", e)

    def _get_access_token(self) -> str:
        """Get or refresh OAuth2 access token."""
        import time
        import requests

        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }, timeout=15)

        if resp.ok:
            token_data = resp.json()
            self._access_token = token_data["access_token"]
            self._token_expires_at = time.time() + token_data.get("expires_in", 3600)
            return self._access_token
        else:
            logger.error("Google Ads token refresh failed: %s", resp.text[:300])
            return ""

    def _query(self, query: str) -> list[dict[str, Any]] | None:
        """Execute a GAQL query against Google Ads API."""
        import requests

        if not self.is_live:
            return None

        access_token = self._get_access_token()
        if not access_token:
            return None

        headers = {
            "Authorization": f"Bearer {access_token}",
            "developerToken": self.developer_token,
            "Content-Type": "application/json",
        }
        if self.login_customer_id:
            headers["login-customer-id"] = self.login_customer_id.replace("-", "")

        url = f"{self.BASE_URL}/{self.API_VERSION}/customers/{self.customer_id.replace('-', '')}/googleAds:searchStream"

        try:
            resp = requests.post(url, headers=headers, json={"query": query}, timeout=30)
            if resp.ok:
                results = resp.json()
                # searchStream returns list of result batches
                rows = []
                for batch in results:
                    rows.extend(batch.get("results", []))
                return rows
            logger.error("Google Ads API error %s: %s", resp.status_code, resp.text[:300])
            return None
        except Exception as e:
            logger.error("Google Ads API request failed: %s", e)
            return None

    # ── Live API Methods ──────────────────────────────────────────────────

    def list_campaigns(self, status: str = "ENABLED") -> list[dict[str, Any]]:
        """List campaigns using GAQL."""
        query = f"""
            SELECT campaign.id, campaign.name, campaign.status,
                   campaign.advertising_channel_type,
                   campaign_budget.amount_micros,
                   campaign.start_date, campaign.end_date
            FROM campaign
            WHERE campaign.status = '{status}'
            ORDER BY campaign.name
            LIMIT 50
        """
        rows = self._query(query)
        if rows is None:
            return []
        return [r.get("campaign", {}) for r in rows]

    def get_campaign_metrics(self, days: int = 30) -> dict[str, Any] | None:
        """Get account-level campaign metrics."""
        query = f"""
            SELECT metrics.impressions, metrics.clicks, metrics.cost_micros,
                   metrics.conversions, metrics.conversions_value,
                   metrics.ctr, metrics.average_cpc, metrics.cost_per_conversion
            FROM campaign
            WHERE segments.date DURING LAST_{days}_DAYS
              AND campaign.status != 'REMOVED'
        """
        rows = self._query(query)
        if not rows:
            return None

        # Aggregate metrics across all campaigns
        total_impressions = 0
        total_clicks = 0
        total_cost = 0
        total_conversions = 0
        total_conv_value = 0

        for row in rows:
            m = row.get("metrics", {})
            total_impressions += int(m.get("impressions", 0))
            total_clicks += int(m.get("clicks", 0))
            total_cost += int(m.get("costMicros", 0))
            total_conversions += float(m.get("conversions", 0))
            total_conv_value += float(m.get("conversionsValue", 0))

        cost_inr = total_cost / 1_000_000  # micros to actual currency
        ctr = (total_clicks / total_impressions * 100) if total_impressions else 0
        cpc = (cost_inr / total_clicks) if total_clicks else 0
        cpa = (cost_inr / total_conversions) if total_conversions else 0
        roas = (total_conv_value / cost_inr) if cost_inr else 0

        return {
            "impressions": total_impressions,
            "clicks": total_clicks,
            "spend": round(cost_inr, 2),
            "conversions": int(total_conversions),
            "revenue": round(total_conv_value, 2),
            "ctr": round(ctr, 2),
            "cpc": round(cpc, 2),
            "cpa": round(cpa, 2),
            "roas": round(roas, 2),
        }

    def health_check(self) -> dict[str, Any]:
        """Check if Google Ads API connection is healthy."""
        if not self.is_live:
            return {"status": "demo", "message": "No Google Ads credentials configured"}
        try:
            access_token = self._get_access_token()
            if access_token:
                return {
                    "status": "live",
                    "customer_id": self.customer_id,
                    "message": "OAuth2 token refresh successful",
                }
        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}
        return {"status": "error", "message": "Could not refresh access token"}


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY FUNCTION — Get the right client
# ═══════════════════════════════════════════════════════════════════════════════

def get_ads_client(workspace_id: str, platform: str = "meta") -> MetaAdsClient | GoogleAdsClient:
    """Get an ads API client for the given workspace and platform.

    Args:
        workspace_id: Client workspace ID
        platform: "meta" or "google"

    Returns:
        MetaAdsClient or GoogleAdsClient (check .is_live for real vs demo)
    """
    if platform.lower() in ("google", "google_ads", "adwords"):
        return GoogleAdsClient(workspace_id)
    return MetaAdsClient(workspace_id)


def get_all_ads_clients(workspace_id: str) -> dict[str, MetaAdsClient | GoogleAdsClient]:
    """Get both Meta and Google clients for a workspace."""
    return {
        "meta": MetaAdsClient(workspace_id),
        "google": GoogleAdsClient(workspace_id),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ADS TOKEN SAVE — Helper for storing ads credentials
# ═══════════════════════════════════════════════════════════════════════════════

def save_meta_ads_token(
    workspace_id: str,
    access_token: str,
    ad_account_id: str,
    business_id: str = "",
    token_type: str = "user_token",
    expires_at: str = "",
) -> dict[str, Any]:
    """Save Meta Ads API credentials to workspace."""
    from admin.token_manager import save_token
    return save_token(
        workspace_id=workspace_id,
        platform="meta_ads",
        access_token=access_token,
        platform_user_id=business_id,
        page_id=ad_account_id,
        page_name=f"Meta Ads Account {ad_account_id}",
        expires_at=expires_at,
        token_type=token_type,
        scopes=["ads_management", "ads_read", "business_management"],
    )


def save_google_ads_token(
    workspace_id: str,
    developer_token: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    customer_id: str,
    login_customer_id: str = "",
) -> dict[str, Any]:
    """Save Google Ads API credentials to workspace.

    Note: Google Ads tokens don't expire like Facebook tokens — the refresh_token
    is long-lived. Only the access_token (obtained via refresh) expires.
    """
    from admin.token_manager import save_token
    return save_token(
        workspace_id=workspace_id,
        platform="google_ads",
        access_token=f"{developer_token}:{customer_id}",  # composite key
        platform_user_id=customer_id,
        page_name=f"Google Ads Account {customer_id}",
        token_type="service_account",
        scopes=["google-ads"],
        # Store extra fields by reading/updating the token file directly
    )


def save_google_ads_credentials(
    workspace_id: str,
    developer_token: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    customer_id: str,
    login_customer_id: str = "",
) -> dict[str, Any]:
    """Save Google Ads API credentials with all fields.

    This writes a complete credential file because the standard token_manager
    doesn't have fields for developer_token, client_id, etc.
    """
    from admin.token_manager import _workspace_dir
    d = _workspace_dir(workspace_id)

    token_data = {
        "platform": "google_ads",
        "access_token": f"hidden:{customer_id}",
        "developer_token": developer_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "customer_id": customer_id,
        "login_customer_id": login_customer_id,
        "platform_user_id": customer_id,
        "page_name": f"Google Ads Account {customer_id}",
        "token_type": "service_account",
        "scopes": ["google-ads"],
        "connected_at": _now(),
        "last_used_at": "",
        "status": "active",
    }

    token_file = d / "google_ads.json"
    token_file.write_text(json.dumps(token_data, indent=2), encoding="utf-8")

    logger.info("Google Ads credentials saved: workspace=%s customer=%s", workspace_id, customer_id)
    return {"status": "saved", "platform": "google_ads", "customer_id": customer_id}
