# admin/agency/sba_autopilot.py
"""SBA 24/7 Autopilot — the always-on loop.

Never sleeps: repeatedly checks for work (new leads to email, replies to
process, meetings to confirm) and does it immediately. Emails are only sent
inside each lead's local business hours (human-style timing); everything
else (lead finding, drafting, planning) runs continuously.

Run:  python -m admin.agency.sba_autopilot
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import random
import re
import sys
import time
from typing import Any

# Make sure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from admin.agency import sba_pipeline as pipe  # noqa: E402
from admin.agency import sba_reason as reason  # noqa: E402
from admin.agency import sba_strategy as strat  # noqa: E402
from admin.agency.sba_pipeline import (  # noqa: E402
    load_leads,
    parse_owner_command,
    sb_patch_lead,
    supabase_config,
)
from admin.tools.sba_email_client import OWNER_EMAIL, SBAEmailClient, build_workspace_email_client  # noqa: E402
from admin.tools.sba_email_draft import draft_email  # noqa: E402
from admin.tools.sba_email_draft import draft_followup  # noqa: E402
from admin.tools.sba_meeting import SBAMeetingManager  # noqa: E402
from admin.tools import agentmail_notify  # noqa: E402
from admin.tools.sba_time import (  # noqa: E402
    human_time,
    lead_business_hours,
    lead_timezone,
    meeting_slot,
    next_business_time,
    now_in,
)

logger = logging.getLogger("sba.autopilot")


async def _safe_book_meeting(self, lead: dict, iso: str) -> str:
    """Book a meeting into the owner's custom store calendar.

    Returns ``"booked"`` on success, ``"pending_manual"`` if booking is
    disabled or the store write failed (the meeting module raises and the
    owner is notified). Never reports a fake Google Meet success.
    """
    try:
        await self.meetings.create_meeting(
            lead_id=str(lead["id"]),
            lead_name=lead.get("name") or "Lead",
            lead_email=lead.get("email") or "",
            proposed_time=iso,
            lead_phone=lead.get("phone") or "",
        )
        # Owner notification via the agent's own AgentMail inbox (best-effort).
        try:
            agentmail_notify.notify_owner(
                "sba",
                f"Meeting booked with {lead.get('name') or 'lead'}",
                f"A meeting was booked for {lead.get('name') or 'lead'} "
                f"at {iso}.\nLead email: {lead.get('email') or 'n/a'}.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("owner AgentMail notify (meeting) failed (non-fatal): %s", exc)
        return "booked"
    except RuntimeError as exc:
        logger.warning("Meeting auto-book failed (manual booking queued): %s", exc)
        return "pending_manual"


def _s(v) -> str:
    """Coerce a DB value to a stripped string. PocketBase's json fields can
    return digit-only values as int (e.g. 3464049915); every .strip() call on
    a loaded field must go through here so the loop never crashes."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v).strip()

INTERVAL_MINUTES = int(os.environ.get("SBA_AUTOPILOT_INTERVAL_MINUTES", "15"))
DAILY_EMAIL_CAP = int(os.environ.get("SBA_DAILY_EMAIL_CAP", "30"))
# Global cap on REAL SMTP sends per autopilot cycle ACROSS ALL workspaces.
# Each workspace still has its own DAILY_EMAIL_CAP, but without this global
# ceiling N client workspaces would multiply the load (N x 30 sends and N x
# CPU per cycle). The runner shares one counter so the whole agency never
# burns more than this per 15-min pass. Future-proofs multi-client scaling.
GLOBAL_CYCLE_EMAIL_CAP = int(os.environ.get("SBA_GLOBAL_CYCLE_EMAIL_CAP", "30"))
# Hard ceiling for one full pass. A wedged CDP/Supabase call (seen: 9h hang)
# must not freeze the loop; on timeout the pass is dropped and the browser
# handle is reset for the next iteration.
PASS_TIMEOUT_SECONDS = int(os.environ.get("SBA_AUTOPILOT_PASS_TIMEOUT_SECONDS", "1500"))
# Per-call ceiling for the lead-finding sub-pass (browser scraping).
LEAD_PASS_TIMEOUT_SECONDS = int(os.environ.get("SBA_LEAD_PASS_TIMEOUT_SECONDS", "900"))
# Max auto-enrichments (Bing + site crawls) per pass; each lead is retried at
# most once per 24h so we don't hammer search engines on every cycle.
MAX_ENRICH_PER_PASS = int(os.environ.get("SBA_MAX_ENRICH_PER_PASS", "12"))
OWNER_TZ = os.environ.get("SBA_OWNER_TIMEZONE", "Asia/Kolkata")
# Where the lead-rotation cursor lives so process restarts don't reset it.
# Without this, every deploy re-scrapes target #0 (all dupes -> 0 new leads).
_ROTATION_STATE_FILE = os.environ.get(
    "SBA_ROTATION_STATE_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 ".sba_rotation_state"),
)
# Same persistence pattern for enrichment retries: the 24h throttle in
# _email_lead only works if the per-lead last-try timestamp survives restarts.
# A fresh instance every pass (run_all_once) would otherwise re-hammer the
# same stuck lead every ~20 min and starve every other lead of enrichment.
_ENRICH_STATE_FILE = os.environ.get(
    "SBA_ENRICH_STATE_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 ".sba_enrichment_state"),
)
# Per-lead enrichment ceiling: 45s keeps one slow domain from eating the pass.
ENRICH_TIMEOUT_SECONDS = int(os.environ.get("SBA_ENRICH_TIMEOUT_SECONDS", "45"))
# Don't re-hammer a recipient for 24h after an SMTP failure (Gmail 550
# daily-limit resets next day; retrying every 15 min just burns the limit).
EMAIL_RETRY_BACKOFF_SECONDS = int(os.environ.get("SBA_EMAIL_RETRY_SECONDS", str(24 * 3600)))

# ── Proactive follow-up (non-responder re-engagement) ─────────────
# Re-emailing non-responders is a high-impact action, so it is OFF unless the
# owner opts in. See config (SBA_FOLLOWUP_ENABLED). Bounds below keep it safe:
# never follow up a lead that has already replied/booked, never follow up before
# MIN_DAYS since first contact, only up to TOUCHES times total (each after a
# GAP_DAYS gap), each inside business hours, under caps. Per-lead progress is
# persisted so the cadence + once-only guarantee survive restarts.
from admin.config import settings as _settings  # noqa: E402

FOLLOWUP_ENABLED = _settings.SBA_FOLLOWUP_ENABLED
FOLLOWUP_MIN_DAYS = _settings.SBA_FOLLOWUP_MIN_DAYS
FOLLOWUP_MAX_PER_PASS = _settings.SBA_FOLLOWUP_MAX_PER_PASS
FOLLOWUP_TOUCHES = _settings.SBA_FOLLOWUP_TOUCHES
FOLLOWUP_GAP_DAYS = _settings.SBA_FOLLOWUP_GAP_DAYS
FOLLOWUP_SUGGEST_CALENDAR = _settings.SBA_FOLLOWUP_SUGGEST_CALENDAR
# Persisted per-lead follow-up progress. Value is a dict
# {"touches": int, "last": float(epoch)} keyed by lead id. Tracks how many
# follow-ups a lead has received and when the last one went out, so the
# multi-touch cadence + once-only guarantee survive the process being restarted.
_FOLLOWUP_STATE_FILE = os.environ.get(
    "SBA_FOLLOWUP_STATE_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 ".sba_followup_state"),
)


def _load_followup_state() -> dict[str, Any]:
    try:
        with open(_FOLLOWUP_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read() or "{}")
        # Normalise: ensure each entry has the expected shape.
        out: dict[str, Any] = {}
        for lid, v in data.items():
            if isinstance(v, dict):
                out[lid] = {"touches": int(v.get("touches", 0) or 0),
                            "last": float(v.get("last", 0.0) or 0.0)}
            else:
                # Legacy format: a bare timestamp -> 1 touch already sent.
                out[lid] = {"touches": 1, "last": float(v or 0.0)}
        return out
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("followup state load failed: %s", exc)
        return {}


def _save_followup_state(state: dict[str, Any]) -> None:
    try:
        tmp = _FOLLOWUP_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(state))
        os.replace(tmp, _FOLLOWUP_STATE_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("followup state save failed: %s", exc)


def _load_enrich_state() -> dict[str, float]:
    """Load the persisted per-lead enrichment last-try timestamps."""
    try:
        with open(_ENRICH_STATE_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return {str(k): float(v) for k, v in raw.items() if v}
    except (OSError, ValueError, TypeError):
        return {}


def _save_enrich_state(state: dict[str, float]) -> None:
    """Atomically persist the enrichment retry state (best-effort)."""
    try:
        tmp = f"{_ENRICH_STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, _ENRICH_STATE_FILE)
    except OSError:
        logger.warning("could not persist enrichment state", exc_info=True)

# ── Email sanity ─────────────────────────────────────────────────────────
# Only send cold emails to real-looking business addresses. The browser
# lead source scrapes contact hints that are often junk (support@discord,
# admissions@a-university, u003eaccountrecovery@deviantart, ...), so we
# gate sends behind a strict regex + a junk-domain blocklist.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
# TLDs that are never a real mailbox host (JS bundle filenames, placeholder
# domains, internal names). "preact@10.5.13.compat.module.min.js" passes the
# regex above, so we also reject file-extension TLDs and fake TLDs.
_JUNK_TLDS = {
    "js", "css", "png", "jpg", "jpeg", "gif", "svg", "webp", "html", "htm",
    "json", "xml", "php", "local", "internal", "invalid", "test", "example",
    "localhost", "donotuse", "company", "home", "lan", "intranet",
}
# Government/education/ISP-style domains are never a local business target.
_GOV_EDU_TLDS = ("gov", "edu", "mil")
_JUNK_EMAIL_DOMAINS = {
    "discord.com", "deviantart.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "reddit.com", "youtube.com", "google.com",
    "gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "aol.com",
    "zoho.com", "proton.me", "protonmail.com", "icloud.com", "me.com",
    "live.com", "msn.com", "qq.com", "163.com", "126.com", "tutanota.com",
    "github.com", "wikipedia.org", "quora.com", "linkedin.com", "tiktok.com",
    "pinterest.com", "snapchat.com", "whatsapp.com", "telegram.org",
    "starz.com", "visitdallas.com", "jetblue.com", "denison.edu", "hcfl.gov",
    "wellsfargo.com", "wellsfargoadvisors.com",
    # Media/news/consumer sites whose scraped "emails" are editorial addresses,
    # never a small-business decision maker.
    "wikihow.com", "zhihu.com", "biblegateway.com", "salon.com",
    "indianexpress.com", "grubhub.com", "rent.com", "joinbelle.com",
    "repeallouisville.com", "salemwebnetwork.com", "the-uptown.com",
    "52pojie.cn", "roamartists.com", "sa-comms.com", "whichiscorrect.com",
    "central.com", "volarerevere.com", "tnvacation.com", "midtownatl.com",
    "lenoxtools.com", "icstucson.org", "wiltondentalassoc.com",
    "districtgov.org", "bizjournals.com", "chamberofcommerce.com",
    "company.com", "yourdomain.com", "sentry.io", "wixpress.com",
    "godaddy.com", "domainsbyproxy.com", "googleusercontent.com",
    # News aggregators / template placeholder domains that homepage-check can
    # mistake for a business (a news article or a builder template mentions the
    # name, so enrichment crawls it and finds an editorial/template mailbox).
    "ground.news", "mystore.com", "wixsite.com", "myshopify.com",
    "squarespace.com", "godaddysites.com", "weebly.com", "wordpress.com",
    # Template placeholder domains (user@domain.com, john@doe.com) that a
    # homepage crawl can mistake for a business mailbox.
    "domain.com", "doe.com", "your-domain.com", "somedomain.com",
}
# Consumer / free mailboxes (gmail, yahoo, ...). Many local small businesses
# run their business mailbox on these. They are only acceptable as a send
# target when email_provenance says the address came from the business's own
# verified page; without that flag they are junk (a random gmail is not a
# business decision maker).
_CONSUMER_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "proton.me",
    "protonmail.com", "zoho.com", "qq.com", "163.com", "126.com",
    "tutanota.com", "gmx.com", "gmx.net", "mail.com", "yandex.com",
    "yandex.ru", "fastmail.com", "hey.com", "pm.me", "mail.ru",
    "rediffmail.com", "bol.com.br", "uol.com.br", "web.de", "orange.fr",
    "wanadoo.fr", "libero.it", "virgilio.it", "t-online.de", "btinternet.com",
    "sky.com", "virginmedia.com", "cox.net", "verizon.net", "att.net",
    "sbcglobal.net", "comcast.net", "charter.net", "earthlink.net",
    "frontiernet.net", "roadrunner.com", "optimum.net", "suddenlink.net",
}
# Domains that look like the *first party* but actually are just a big
# conglomerate/parent brand — not the local decision maker either.
# Prefixes that NEVER name a human decision maker — rejected even when the
# address came from the business's own verified page (noreply@/unsubscribe@/
# careers@ can never be a cold-email target).
_HARD_JUNK_EMAIL_PREFIXES = (
    "press@", "noreply@", "no-reply@", "careers@",
    "jobs@", "hr@", "pr@", "media@", "newsletter@", "unsubscribe@",
    "editor@", "tips@", "newsroom@", "submissions@", "stories@",
    "advertise@", "partners@", "founders@", "team@",
    "privacy@", "legal@", "addressadmissions@",
    "recreationdepartment@", "parkingservices@",
    "mychartsupport@", "subscriptionsupport@",
    "guest@", "stop@", "care@", "name@",
    "feedback@", "hi@", "user@", "billing@", "mailer@", "bounce@",
    "postmaster@", "webmaster@", "abuse@", "automated@",
)
# Generic front-desk prefixes. For a local small business info@/contact@/hello@
# IS the owner's inbox, so these are allowed ONLY when the caller proved the
# address came from the business's own verified page (email_provenance is
# consumer/own_domain/homepage — set by enrichment). In an unverified context
# (a scraper's listing info@) they stay junk.
_GENERIC_EMAIL_PREFIXES = (
    "info@", "contact@", "hello@", "help@", "sales@", "admin@",
    "service@", "office@", "dispatch@", "bookings@", "support@",
    "enquiries@", "inquiries@", "reservations@", "scheduling@", "mail@",
)
# Local parts that scream "automated/aggregator", not a human decision maker
# (ad-alerts@, notifications@, alert@, ...).
_JUNK_LOCAL_PAT = re.compile(
    r"(alert|notif|noreply|no-?reply|donotreply|automated|mailer|bounce|"
    r"postmaster|webmaster|abuse|marketing@|promo@|deals@|offers@)",
    re.I,
)
# A school/university/campus domain is not a small-business decision maker.
_SCHOOL_DOMAIN_MARKERS = (
    "school", "academy", "k12", "college", "univ", "campus", "faculty",
    "alumni", "edu.",
)
# HTML/JS-escape leftovers mean the scraped value is a mangled page fragment
# (e.g. "u003e" is the unicode escape for ">"), not a real mailbox.
_MALFORMED_TOKENS = ("u003e", "u003c", "%3e", "%3c", "&gt;", "&lt;", "\\u003e", "\\u003c")


# ── Lead discovery rotation ──────────────────────────────────────────────
# Each autopilot pass searches a different niche+city so new businesses keep
# arriving instead of re-scraping the same "plumber Houston" results forever.
# Override with SBA_LEAD_ROTATION=[["hvac","Dallas","TX"],...] (JSON).
_LEAD_TARGETS = [
    ("plumber", "Houston", "TX"),
    ("electrician", "San Antonio", "TX"),
    ("hvac", "Austin", "TX"),
    ("roofer", "Dallas", "TX"),
    ("landscaper", "Fort Worth", "TX"),
    ("auto repair", "Houston", "TX"),
    ("cleaning service", "San Antonio", "TX"),
    ("handyman", "Austin", "TX"),
    ("painter", "Dallas", "TX"),
    ("dentist", "Fort Worth", "TX"),
    ("plumber", "Austin", "TX"),
    ("electrician", "Houston", "TX"),
    ("hvac", "Dallas", "TX"),
    ("roofer", "San Antonio", "TX"),
    ("landscaper", "Houston", "TX"),
    ("auto repair", "Austin", "TX"),
    ("cleaning service", "Dallas", "TX"),
    ("handyman", "Fort Worth", "TX"),
    ("painter", "San Antonio", "TX"),
    ("salon", "Houston", "TX"),
    ("plumber", "Phoenix", "AZ"),
    ("electrician", "Atlanta", "GA"),
    ("hvac", "Charlotte", "NC"),
    ("roofer", "Tampa", "FL"),
    ("landscaper", "Orlando", "FL"),
    ("auto repair", "Denver", "CO"),
    ("cleaning service", "Las Vegas", "NV"),
    ("handyman", "Nashville", "TN"),
    ("painter", "Oklahoma City", "OK"),
    ("salon", "Memphis", "TN"),
    ("plumber", "San Diego", "CA"),
    ("electrician", "Columbus", "OH"),
    ("hvac", "Kansas City", "MO"),
    ("roofer", "New Orleans", "LA"),
    ("landscaper", "Louisville", "KY"),
    ("auto repair", "Albuquerque", "NM"),
    ("cleaning service", "Tulsa", "OK"),
    ("handyman", "El Paso", "TX"),
]



def _rotation_targets() -> list[tuple[str, str, str]]:
    raw = os.environ.get("SBA_LEAD_ROTATION", "")
    if raw:
        try:
            import json as _json

            items = _json.loads(raw)
            if items and all(isinstance(i, (list, tuple)) and len(i) == 3 for i in items):
                return [tuple(i) for i in items]
        except Exception:  # noqa: BLE001
            logger.warning("SBA_LEAD_ROTATION invalid, using default rotation")
    base = list(_LEAD_TARGETS)
    # Layer 3: the agent's own strategy review can pick priority niche+city
    # targets; those are tried first before the default rotation.
    try:
        focus = [
            tuple(t) for t in (strat.load_strategy().get("focus") or [])
            if isinstance(t, (list, tuple)) and len(t) == 3
            and all(isinstance(x, str) and x.strip() for x in t)
        ]
        if focus:
            return focus[:3] + base
    except Exception:  # noqa: BLE001
        pass
    return base


def _is_valid_lead_email(email: str, allow_consumer: bool = False) -> bool:
    """True only for a plausible business cold-email target.

    allow_consumer=True permits gmail/yahoo/... mailboxes, but ONLY when the
    caller can prove the address came from the business's own verified page
    (email_provenance == 'consumer' set by enrichment). Everything else is
    checked identically in both modes.
    """
    raw = (email or "").strip()
    if not raw:
        return False
    if any(tok in raw.lower() for tok in _MALFORMED_TOKENS):
        return False
    e = raw.lower()
    if not e or not _EMAIL_RE.match(e):
        return False
    if e == "test@example.com" or "example.com" in e:
        return False
    domain = e.split("@", 1)[1]
    local = e.split("@", 1)[0]
    tld = domain.rsplit(".", 1)[-1]
    if tld in _JUNK_TLDS:
        return False
    # gov/edu/mil — a municipality, school, or military site, not a local biz.
    if domain.endswith(_GOV_EDU_TLDS):
        return False
    # School/university/campus domains are never a local business mailbox.
    if any(m in domain for m in _SCHOOL_DOMAIN_MARKERS):
        return False
    if domain in _JUNK_EMAIL_DOMAINS:
        if allow_consumer and domain in _CONSUMER_DOMAINS:
            pass
        else:
            return False
    if not allow_consumer or domain in _CONSUMER_DOMAINS:
        # Generic front-desk prefixes (info@, contact@, ...) are fine on the
        # business's OWN verified page, but junk in an unverified scrape. On
        # consumer mail domains (gmail/yahoo/...) a role account like info@
        # can never be proven to belong to this specific business, so it stays
        # junk even when the caller says consumer mail is otherwise acceptable.
        for prefix in _GENERIC_EMAIL_PREFIXES:
            if e.startswith(prefix):
                return False
    for prefix in _HARD_JUNK_EMAIL_PREFIXES:
        if e.startswith(prefix):
            return False
    if _JUNK_LOCAL_PAT.search(local):
        return False
    return True


class SBAAutopilot:
    """Always-on autonomous SBA loop for one workspace.

    Each workspace gets its own agent: its own lead pool (workspace_name),
    niche rotation, strategy file, reasoning journal, and owner email. The
    agency workspace ("agency") behaves exactly as before.
    """

    def __init__(self, email_client: SBAEmailClient | None = None,
                 meeting_manager: SBAMeetingManager | None = None,
                 workspace_name: str = "agency",
                 owner_email: str | None = None) -> None:
        self.workspace_name = workspace_name or "agency"
        self.email = email_client
        self.meetings = meeting_manager
        # Per-workspace state: rotation, strategy file, journal, owner email,
        # and email identity (each client uses ITS OWN inbox + app password,
        # never the agency's).
        try:
            from admin.agency import sba_biztypes as biztypes
            cfg = biztypes.get_workspace_config(self.workspace_name)
            self._rotation = list(cfg.get("rotation") or _rotation_targets())
            self._rotation_state_file = biztypes.rotation_state_path(self.workspace_name)
            self._strategy_path = biztypes.strategy_path(self.workspace_name)
            self._journal_path = biztypes.journal_path(self.workspace_name)
            self._owner_email = owner_email or cfg.get("owner_email") or ""
            self._email_cfg = cfg
        except Exception:  # noqa: BLE001
            logger.warning("biztypes config failed for %r, using defaults", self.workspace_name)
            self._rotation = list(_rotation_targets())
            self._rotation_state_file = _ROTATION_STATE_FILE
            self._strategy_path = strat.STRATEGY_FILE
            self._journal_path = reason.REASON_LOG
            self._owner_email = owner_email or ""
            self._email_cfg = {}
        self._build_email_client()
        # Custom booking lives in the owner's store (no Google Calendar).
        _client = (self._email_cfg or {}).get("client", self.workspace_name)
        self.meetings = meeting_manager or SBAMeetingManager(
            email_client=self.email,
            workspace=self.workspace_name,
            client=_client,
            store_base_url=os.environ.get("STORE_BASE_URL", ""),
        )
        self._last_status: dict[str, Any] = {"started": now_in(OWNER_TZ).isoformat()}
        self._target_idx = self._load_rotation_idx()
        self._email_retry_until: dict[str, float] = {}
        self._enriched_at: dict[str, float] = _load_enrich_state()
        self._followup_state: dict[str, Any] = _load_followup_state()
        self._enrichments_this_pass = 0
        self._last_notified_lead_id: str | None = None

    def _build_email_client(self) -> None:
        """Pick the right inbox for this workspace (see build_workspace_email_client).

        - agency: env creds (SBA_OWNER_EMAIL / SBA_OWNER_EMAIL_PASSWORD).
        - client workspace: its OWN smtp_email + smtp_password from config.
          If the client has no creds configured yet, email is DISABLED so we
          never accidentally send from the agency inbox on a client's behalf.
        - explicit email_client (tests/CLI) always wins.
        """
        if self.email is None:
            self.email = build_workspace_email_client(self.workspace_name)

    def _load_rotation_idx(self) -> int:
        try:
            with open(self._rotation_state_file, encoding="utf-8") as f:
                return int(f.read().strip() or "0")
        except Exception:  # noqa: BLE001
            return 0

    def _save_rotation_idx(self, idx: int) -> None:
        try:
            with open(self._rotation_state_file, "w", encoding="utf-8") as f:
                f.write(str(idx))
        except Exception:  # noqa: BLE001
            pass

    def status(self) -> dict:
        return dict(self._last_status)

    async def _find_new_leads(self) -> int:
        """Run one lead-finding pass across platforms (best-effort).

        Rotates through niche+city targets each pass, dedupes against leads
        already in Supabase, and saves rows in the leads table's column shape
        (raw JSONB carries the extra scraped fields).
        """
        try:
            from admin.tools.sba_lead_sources import find_leads_lightweight

            cfg = supabase_config()
            if not cfg:
                return 0
            url, key = cfg, None
            targets = self._rotation or _rotation_targets()
            category, city, state = targets[self._target_idx % len(targets)]
            self._target_idx += 1
            self._save_rotation_idx(self._target_idx)
            logger.info(
                "lead rotation: %s in %s, %s (pass %d/%d)",
                category, city, state, self._target_idx, len(targets),
            )
            # Browser scraping can wedge on a dead CDP transport; the
            # lightweight finder uses plain HTTP (no Chrome) and is bounded so
            # the autopilot loop always survives.
            leads = await asyncio.wait_for(
                asyncio.to_thread(
                    find_leads_lightweight, category, city, state, 5
                ),
                timeout=LEAD_PASS_TIMEOUT_SECONDS,
            )

            # Dedupe against leads already stored for THIS workspace (name + phone).
            existing = load_leads(url, key)
            existing_by_key: dict[tuple[str, str], dict] = {}
            for l in existing:
                l_ws = (l.get("workspace_name") or (l.get("context") or {}).get("workspace_name") or "agency")
                if l_ws != self.workspace_name:
                    continue
                n = _s(l.get("name")).lower()
                p = _s(l.get("phone"))
                if n and p:
                    existing_by_key[(n, p)] = l

            added = 0
            refreshed = 0
            rows: list[dict] = []
            for lead in leads:
                n = _s(lead.get("name")).lower()
                p = _s(lead.get("phone"))
                # Defensive: never save a lead with a generic UI label as a
                # name. Phone is optional now: the lightweight finder often
                # returns business name + website only, and enrichment/judge
                # decide whether to keep and how to reach the lead.
                if not n:
                    continue
                key_pair = (n, p)
                old = existing_by_key.get(key_pair)
                if old is not None:
                    # Backfill: old leads were scraped before the maps card
                    # captured websites. Now that we see the website, patch it
                    # so enrichment can find the business email.
                    new_site = _s(lead.get("website"))
                    if new_site and not _s(old.get("website")):
                        if sb_patch_lead(url, key, str(old.get("id") or ""), {
                            "website": new_site,
                            "has_website": True,
                            "website_status": "has_website",
                        }):
                            refreshed += 1
                    continue
                row = {
                    "name": lead.get("name") or "",
                    "phone": lead.get("phone") or "",
                    "email": lead.get("email") or "",
                    "website": lead.get("website") or "",
                    "category": lead.get("category") or category,
                    "city_state": f"{city}, {state}",
                    "href": lead.get("href") or "",
                    "address": lead.get("address") or "",
                    "has_website": bool(lead.get("website")),
                    "website_status": "has_website" if lead.get("website") else "verified_none",
                    "mode": "card",
                    "text": lead.get("text") or "",
                    "raw": {
                        k: v for k, v in lead.items()
                        if k not in ("name", "phone", "email", "category", "city",
                                     "state", "href", "address", "website", "text")
                    },
                    "status": "candidate",
                    "workspace_name": self.workspace_name,
                    "client_id": "00000000-0000-0000-0000-000000000001",
                }
                rows.append(row)

            # The agent thinks about every new lead before it enters the funnel:
            # score 0-100, an action (contact/wait/skip) and a one-line reason.
            sem = asyncio.Semaphore(reason.JUDGE_CONCURRENCY)

            async def _judge(row: dict) -> tuple[dict, str]:
                async with sem:
                    verdict = await reason.judge_lead(row)
                raw = dict(row.get("raw") or {})
                raw["lead_score"] = verdict["score"]
                raw["lead_reason"] = verdict["reason"]
                raw["lead_action"] = verdict["action"]
                row["raw"] = raw
                reason.log_decision({
                    "event": "lead_judged",
                    "name": row.get("name") or "",
                    "category": category,
                    "city_state": row.get("city_state") or "",
                    "verdict": verdict,
                }, log_path=self._journal_path)
                return row, verdict["action"]

            if rows:
                judged = await asyncio.gather(*(_judge(r) for r in rows))
                rows = [r for r, action in judged if action != "skip"]
                skipped = sum(1 for _, action in judged if action == "skip")
                if skipped:
                    logger.info("agent skipped %d lead(s) as not worth contacting", skipped)

            for row in rows:
                res = pipe.save_lead(url, key, row)
                if res is not None:
                    added += 1
                    existing_by_key[(_s(row.get("name")).lower(), _s(row.get("phone")))] = row
            logger.info("autopilot: found %d new leads, refreshed %d websites from %d scraped (%s in %s)",
                        added, refreshed, len(leads), category, city)
            # Owner notification via the agent's own AgentMail inbox (best-effort;
            # never blocks the lead pipeline). Cold-lead outreach still uses the
            # reputed SMTP sender separately.
            if added:
                try:
                    agentmail_notify.notify_owner(
                        "sba",
                        f"SBA: {added} new lead(s) found ({category}, {city})",
                        f"Found {added} new lead(s) in {category}, {city}.\n"
                        f"Refreshed {refreshed} websites. Autopilot continues to "
                        f"enrich + email them on the reputed sender.",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("owner AgentMail notify failed (non-fatal): %s", exc)
            return added
        except Exception as exc:  # noqa: BLE001
            logger.warning("lead finding pass failed: %s", exc)
            return 0

    async def _enrich_lead_email(self, url: str, key: str, lead: dict) -> str:
        """Auto-fill a candidate lead's email via safe domain-trust enrichment.

        Only candidate leads get enriched (never re-contact already-contacted
        ones). The enrichment crawls only domains whose homepage mentions the
        business name, so grubhub.com/wikihow.com-type junk never gets saved.
        Returns (email, provenance); both '' when nothing trustworthy was found.
        """
        name = _s(lead.get("name"))
        if not name:
            return "", ""
        city_state = lead.get("city_state") or lead.get("context", {}).get("city_state") or ""
        city = city_state.split(",")[0].strip() if city_state else ""
        try:
            from admin.tools.lead_enrichment import find_lead_email
        except Exception as exc:  # noqa: BLE001
            logger.warning("lead_enrichment import failed: %s", exc)
            return "", ""
        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(
                    find_lead_email,
                    name,
                    city,
                    lead.get("category") or "",
                    lead.get("website") or "",
                    False,  # we PATCH below so failure is logged consistently
                    lead.get("id"),
                ),
                timeout=ENRICH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.info("enrichment timed out for %s", name)
            return "", ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("enrichment failed for %s: %s", name, exc)
            return "", ""
        email = (res or {}).get("email") or ""
        provenance = (res or {}).get("provenance") or ""
        # Enrichment only collects mailboxes from the business's own verified
        # page, so a first-party address it returns is trusted.
        if email and _is_valid_lead_email(
            email,
            allow_consumer=(provenance in ("consumer", "own_domain", "homepage")),
        ):
            logger.info("enriched %s -> %s (provenance=%s, sources=%s)",
                        name, email, provenance, res.get("domains"))
            self._email_retry_until.pop(email, None)
            return email, provenance
        return "", ""

    async def _email_lead(self, url: str, key: str, lead: dict, angle: str | None = None,
                          global_budget: dict[str, int] | None = None) -> str:
        """Send a professional cold email if lead is in business hours.

        global_budget: optional shared {"sent": int} counter. When the agency
        ceiling is reached this pass, sends are skipped (returned as a no-op)
        so one workspace can't starve the others of the global quota.
        """
        email = _s(lead.get("email"))
        provenance = _s(lead.get("email_provenance"))
        status = lead.get("status") or "new"
        if status in ("contacted", "meeting", "replied", "owner_confirm"):
            return "already_contacted"
        if not email and status in ("candidate", "new") and self._enrichments_this_pass < MAX_ENRICH_PER_PASS:
            # Auto-enrichment: fill real business emails before giving up.
            # Each lead is tried at most once per 24h (Bing + site crawls are
            # expensive; a miss today is unlikely to be a hit tomorrow).
            lid = str(lead.get("id") or "")
            last_try = self._enriched_at.get(lid, 0.0)
            if time.time() - last_try > 24 * 3600:
                self._enriched_at[lid] = time.time()
                _save_enrich_state(self._enriched_at)
                self._enrichments_this_pass += 1
                email, prov = await self._enrich_lead_email(url, key, lead)
                if email and not sb_patch_lead(url, key, lid, {"email": email, "email_provenance": prov}):
                    logger.warning("could not persist enriched email for %s", lead.get("name"))
                if email:
                    provenance = prov or provenance
        if not email:
            return "no_email"
        if not _is_valid_lead_email(
            email,
            allow_consumer=(provenance in ("consumer", "own_domain", "homepage")),
        ):
            logger.info("skip junk email %s for %s", email, lead.get("name") or "")
            return "invalid_email"
        # SMTP failure (e.g. Gmail 550 daily limit): don't re-hammer this
        # recipient until the backoff window has passed.
        blocked_until = self._email_retry_until.get(email)
        if blocked_until and time.time() < blocked_until:
            return "retry_backoff"
        if not lead_business_hours(lead):
            return "deferred"
        # LLM second opinion: for consumer/homepage mailboxes (anything that is
        # NOT the lead's own domain), confirm the address really belongs to this
        # business before spending a send. A model failure falls back to ok=True
        # so a flaky model never silently blocks a legitimate mailbox.
        if provenance != "own_domain":
            verdict = await reason.verify_email(
                lead.get("name") or "", email,
                sources=[lead.get("href") or ""],
            )
            if not verdict.get("ok"):
                reason.log_decision({
                    "event": "email_rejected",
                    "name": lead.get("name") or "",
                    "email": email,
                    "confidence": verdict.get("confidence"),
                    "reason": verdict.get("reason") or "not the business",
                }, log_path=self._journal_path)
                logger.info("agent rejected email %s for %s: %s",
                            email, lead.get("name") or "", verdict.get("reason") or "not the business")
                return "invalid_email"
        subject, body = await draft_email(lead, angle=angle)
        ok = await self.email.send_email(to_email=email, subject=subject, body_text=body, cc_owner=True)
        if ok:
            sb_patch_lead(url, key, str(lead.get("id") or ""), {"status": "contacted"})
            # Seed the follow-up cadence for this lead: record first-contact
            # time (with 0 touches sent yet) so the (opt-in) follow-up pass can
            # enforce MIN_DAYS + a bounded multi-touch cadence that survives
            # restarts. If a lead is re-contacted cold, reset its progress.
            lid = str(lead.get("id") or "")
            self._followup_state[lid] = {"touches": 0, "last": time.time()}
            _save_followup_state(self._followup_state)
            reason.log_decision({
                "event": "email_sent",
                "name": lead.get("name") or "",
                "email": email,
                "provenance": provenance,
                "category": lead.get("category") or "",
            }, log_path=self._journal_path)
            if global_budget is not None:
                global_budget["sent"] = global_budget.get("sent", 0) + 1
            return "sent"
        self._email_retry_until[email] = time.time() + EMAIL_RETRY_BACKOFF_SECONDS
        return "send_failed"

    def _is_owner(self, from_addr: str) -> bool:
        """True when the reply came from this workspace's owner (the agency
        owner or the client workspace's owner_email) — such replies are owner
        commands, not lead replies.

        Matches on any of:
          - the canonical OWNER_EMAIL,
          - the per-workspace owner_email configured for this client,
          - the domain of the workspace's own configured inbox (so owner
            aliases / replies from the agency domain count as owner).
        """
        low = (from_addr or "").lower().strip()
        if not low:
            return False
        candidates = {OWNER_EMAIL, self._owner_email}
        # Add the agency's own inbox domain so owner aliases resolve too.
        for c in (OWNER_EMAIL, self._owner_email):
            if "@" in c:
                candidates.add("@" + c.split("@", 1)[1].lower())
        return any(bool(e) and e.lower() in low for e in candidates)

    def _match_lead(self, from_addr: str, leads: list[dict]) -> dict | None:
        """Match an incoming reply to a stored lead.

        Tries, in order: exact email substring (original behaviour), then a
        softer match on the sender's domain, then on distinctive name tokens.
        This stops legit replies from being silently dropped just because the
        stored email differs in casing/alias from the sender's From address.
        """
        low = (from_addr or "").lower()
        # 1) exact email substring (original behaviour)
        exact = next((l for l in leads if (l.get("email") or "").lower() and (l.get("email") or "").lower() in low), None)
        if exact:
            return exact
        # 2) same domain as a stored lead
        dom = low.split("@")[-1] if "@" in low else ""
        if dom:
            for l in leads:
                le = (l.get("email") or "").lower()
                if "@" in le and le.split("@")[-1] == dom:
                    return l
        # 3) distinctive name tokens from the lead appear in the sender address
        for l in leads:
            name = (l.get("name") or "").lower()
            tokens = [t for t in re.findall(r"[a-z0-9]+", name) if len(t) > 2]
            if tokens and any(t in low for t in tokens):
                return l
        return None

    def _is_auto_reply(self, subject: str, from_addr: str) -> bool:
        """Delegate to the email client's auto-reply detector if present."""
        fn = getattr(self.email, "_is_auto", None)
        if callable(fn):
            try:
                return bool(fn(subject, from_addr))
            except Exception:  # noqa: BLE001
                return False
        return False

    def _looks_like_lead(self, from_addr: str) -> bool:
        """Conservative gate for sending an unsolicited auto-ack to an unknown
        sender. Reuse the same lead-email validity rules as the send gate so we
        never ack obvious auto/noreply/junk addresses (avoids spam ping-pong)."""
        low = (from_addr or "").lower().strip()
        if not low or "@" not in low:
            return False
        # Reuse the autopilot's lead-email validity filter (imports locally to
        # avoid a top-level cycle with lead_enrichment).
        try:
            from admin.tools.lead_enrichment import _is_valid_email
        except Exception:  # noqa: BLE001
            _is_valid_email = None
        if callable(_is_valid_email):
            return bool(_is_valid_email(low))
        # Fallback: block obvious auto/junk local parts.
        local = low.split("@", 1)[0]
        junk = ("noreply", "no-reply", "donotreply", "mailer", "postmaster", "bounce")
        return not any(local.startswith(j) for j in junk)

    def _resolve_owner_lead(self, cmd_lead_id: str, leads: list[dict]) -> dict | None:
        """The lead an owner reply refers to: try the id embedded in the reply,
        then the lead we last notified this owner about, then any lead waiting
        on owner confirmation."""
        if cmd_lead_id:
            for l in leads:
                if str(l.get("id")) == str(cmd_lead_id):
                    return l
        if self._last_notified_lead_id:
            for l in leads:
                if str(l.get("id")) == str(self._last_notified_lead_id):
                    return l
        for l in leads:
            if (l.get("status") or "") == "owner_confirm":
                return l
        return None

    async def _process_followups(self, url: str, key: str,
                                 global_budget: dict[str, int] | None = None,
                                 cold_sent: int = 0) -> dict[str, int]:
        """Re-touch non-responding leads with a bounded, multi-touch cadence.

        Safety model (prompt #14: mass re-email is high-impact → bounded +
        opt-in):
          - NO-OP unless SBA_FOLLOWUP_ENABLED is true.
          - Only leads still in 'contacted' status (never replied/booked).
          - First touch only after FOLLOWUP_MIN_DAYS since first contact.
          - At most FOLLOWUP_TOUCHES total per lead, each after a
            FOLLOWUP_GAP_DAYS gap since the previous touch (multi-touch
            cadence). Per-lead progress (touches + last time) is persisted, so
            the cadence + once-only guarantee survive restarts.
          - At most FOLLOWUP_MAX_PER_PASS per pass, inside the lead's own
            business hours, under the SAME caps as cold sends: the per-pass
            DAILY_EMAIL_CAP (counted together with cold sends this pass) and the
            agency-wide global_budget ceiling, both decremented here so
            multi-workspace runs can't over-send.
          - Each follow-up is counted in stats["emails_sent"] for the owner
            digest. Optional FOLLOWUP_SUGGEST_CALENDAR appends a proposed
            meeting slot (computed via meeting_slot) so the lead can just say
            yes.
        """
        stats = {"followups_sent": 0, "followups_eligible": 0, "followups_skipped": 0}
        if not FOLLOWUP_ENABLED:
            return stats
        if not (self.email and self.email.enabled):
            return stats
        leads = load_leads(url, key)
        ws_leads = [l for l in leads if (l.get("workspace_name") or "agency") == self.workspace_name]
        for lead in reason.prioritize(ws_leads):
            # Bounds: per-pass follow-up ceiling + shared daily cap (cold+followup).
            if stats["followups_sent"] >= FOLLOWUP_MAX_PER_PASS:
                break
            if cold_sent + stats["followups_sent"] >= DAILY_EMAIL_CAP:
                break
            # Agency-wide ceiling (shared across all workspaces this cycle).
            if global_budget is not None and global_budget.get("sent", 0) >= GLOBAL_CYCLE_EMAIL_CAP:
                break
            status = lead.get("status") or "new"
            # Never follow up someone who already engaged.
            if status != "contacted":
                continue
            lid = str(lead.get("id") or "")
            email = _s(lead.get("email"))
            if not email or not _is_valid_lead_email(
                email,
                allow_consumer=(_s(lead.get("email_provenance")) in ("consumer", "own_domain", "homepage")),
            ):
                stats["followups_skipped"] += 1
                continue
            # Respect the SMTP backoff window (don't re-hammer a failing inbox).
            blocked_until = self._email_retry_until.get(email)
            if blocked_until and time.time() < blocked_until:
                stats["followups_skipped"] += 1
                continue
            # Cadence gate: how many touches already sent, and when.
            prog = self._followup_state.get(lid, {"touches": 0, "last": 0.0})
            touches = int(prog.get("touches", 0) or 0)
            last = float(prog.get("last", 0.0) or 0.0)
            if touches >= FOLLOWUP_TOUCHES:
                continue  # Cadence complete: drop from pool permanently.
            # First touch: MIN_DAYS since first contact. Later touches: GAP_DAYS
            # since the previous touch.
            wait_days = FOLLOWUP_MIN_DAYS if touches == 0 else FOLLOWUP_GAP_DAYS
            if last and (time.time() - last) < wait_days * 86400:
                continue
            if not lead_business_hours(lead):
                stats["followups_skipped"] += 1
                continue
            stats["followups_eligible"] += 1
            subject, body = await draft_followup(lead, touch_index=touches, total_touches=FOLLOWUP_TOUCHES)
            if FOLLOWUP_SUGGEST_CALENDAR and touches == 0:
                # Suggest a concrete slot on the very first follow-up so the
                # lead can accept in one word (falls back silently on failure).
                try:
                    iso, slot_text = meeting_slot(lead, OWNER_TZ)
                    if slot_text:
                        body = body.rstrip() + f"\n\nHow about {slot_text}? Just reply 'yes' and I'll lock it in."
                except Exception as exc:  # noqa: BLE001
                    logger.debug("calendar suggest skipped: %s", exc)
            ok = await self.email.send_email(to_email=email, subject=subject, body_text=body, cc_owner=True)
            if ok:
                # Advance the cadence: record this touch + time. When the lead
                # has now received all touches, it stays in the map with
                # touches==TOUCHES and is skipped on future passes (once-only).
                self._followup_state[lid] = {"touches": touches + 1, "last": time.time()}
                _save_followup_state(self._followup_state)
                stats["followups_sent"] += 1
                # Count toward the agency-wide + owner-visible send totals.
                if global_budget is not None:
                    global_budget["sent"] = global_budget.get("sent", 0) + 1
                reason.log_decision({
                    "event": "followup_sent",
                    "lead_id": lid,
                    "touch": touches + 1,
                    "total_touches": FOLLOWUP_TOUCHES,
                    "name": lead.get("name") or "",
                    "email": email,
                }, log_path=self._journal_path)
            else:
                self._email_retry_until[email] = time.time() + EMAIL_RETRY_BACKOFF_SECONDS
                stats["followups_skipped"] += 1
        return stats

    async def _process_replies(self, url: str, key: str, leads: list[dict]) -> dict[str, int]:
        stats = {"owner_notified": 0, "meetings_scheduled": 0, "rejected": 0, "unmatched_sender": 0}
        replies = await self.email.check_replies(mark_read=True)
        for rep in replies:
            from_addr = rep.get("from_addr", "")
            body = rep.get("body_preview", "") or rep.get("body_full", "")
            subject = rep.get("subject", "")
            if self._is_owner(from_addr):
                cmd = parse_owner_command(subject, body)
                lead = self._resolve_owner_lead(cmd.get("lead_id") or "", leads)
                if not lead or cmd.get("action") == "unknown":
                    # Owner asked a question we can't attribute to a lead. Be
                    # HONEST: never fabricate. If the analysis model is down,
                    # tell the owner the real raw reply count instead of a
                    # made-up answer. Otherwise just note we couldn't act on it.
                    owner_to = self._owner_email or OWNER_EMAIL
                    raw_count = len(replies)
                    try:
                        await self.email.send_email(
                            to_email=owner_to,
                            subject="About your question",
                            body_text=(
                                "I couldn't fully answer that from the data I have. "
                                f"I saw {raw_count} raw reply/replies in the inbox this pass and "
                                "couldn't parse them because the analysis model is rate-limited "
                                "(or the message didn't map to a tracked lead). "
                                "No action was taken - here are the replies I have:\n\n"
                                + "\n".join(
                                    f"- {r.get('from_addr') or '?'}: "
                                    f"{(r.get('subject') or '')[:80]}"
                                    for r in replies
                                )
                            ),
                            cc_owner=False,
                        )
                        stats["owner_notified"] += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("owner notify failed: %s", exc)
                    continue
                if cmd["action"] == "haan":
                    hour = None
                    t = cmd.get("time") or ""
                    if t and len(t) >= 2:
                        try:
                            hour = int(t[:2])
                        except (TypeError, ValueError):
                            hour = None
                    iso, text = meeting_slot(lead, OWNER_TZ, hour=hour)
                    result = await _safe_book_meeting(self, lead, iso)
                    if result == "booked":
                        sb_patch_lead(url, key, str(lead["id"]), {"status": "meeting"})
                        stats["meetings_scheduled"] += 1
                    else:
                        # Manual booking queued + owner alerted by meeting module.
                        sb_patch_lead(url, key, str(lead["id"]),
                                      {"status": "pending_manual_booking"})
                        stats["owner_notified"] += 1
                else:
                    await self.email.send_email(
                        to_email=lead.get("email") or "", subject="Thanks",
                        body_text=pipe.rejected_body(lead), cc_owner=True,
                    )
                    sb_patch_lead(url, key, str(lead["id"]), {"status": "rejected"})
                    stats["rejected"] += 1
            else:
                # Lead reply — the agent understands intent (and any meeting
                # time) before deciding what to do.
                rep = await reason.understand_reply(body)
                kind = rep["intent"]
                meeting_time = rep.get("meeting_time") or ""
                reason.log_decision({
                    "event": "reply_understood",
                    "from": from_addr,
                    "intent": kind,
                    "meeting_time": meeting_time,
                    "reason": rep.get("reason") or "",
                    "uncertain": bool(rep.get("uncertain")),
                }, log_path=self._journal_path)
                lead = self._match_lead(from_addr, leads)
                if not lead:
                    # An unmatched sender arrived: never silently drop it.
                    stats["unmatched_sender"] += 1
                    body_preview = (body or "")[:200]
                    reason.log_decision({
                        "event": "unmatched_sender",
                        "from": from_addr,
                        "subject": subject,
                        "body_preview": body_preview,
                    }, log_path=self._journal_path)
                    owner_to = self._owner_email or OWNER_EMAIL
                    try:
                        await self.email.send_email(
                            to_email=owner_to,
                            subject="Unmatched email in inbox",
                            body_text=(
                                f"Got an email from {from_addr} I couldn't match to a "
                                f"lead - {subject}\n\nPreview:\n{body_preview}"
                            ),
                            cc_owner=False,
                        )
                        stats["owner_notified"] += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("owner notify (unmatched) failed: %s", exc)
                    # Optional, conservative sender ack: only if it looks like a
                    # real business/lead and is NOT an auto-reply (avoid spamming).
                    if (not self._is_auto_reply(subject, from_addr)
                            and self._looks_like_lead(from_addr)):
                        try:
                            await self.email.send_email(
                                to_email=from_addr, subject="Thanks for reaching out",
                                body_text=(
                                    "Thanks for your email - I'm the autopilot for this "
                                    "business and I'll get your message to the right person. "
                                    "I couldn't auto-match it to an existing lead, but a human "
                                    "owner has been notified."
                                ),
                                cc_owner=False,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("sender ack failed: %s", exc)
                    continue
                if kind in ("yes", "maybe"):
                    # The lead said yes - the agent books the meeting right away
                    # (Meet link + calendar + confirmation email to the lead).
                    # No owner round-trip needed; the owner is CC'd on the
                    # confirmation and gets a summary below.
                    hour = None
                    mt = meeting_time
                    if mt and len(mt) >= 2:
                        try:
                            hour = int(mt[:2])
                        except (TypeError, ValueError):
                            hour = None
                    iso, text = meeting_slot(lead, OWNER_TZ, hour=hour)
                    result = await _safe_book_meeting(self, lead, iso)
                    if result == "booked":
                        sb_patch_lead(url, key, str(lead["id"]), {"status": "meeting"})
                        stats["meetings_scheduled"] += 1
                        owner_to = self._owner_email or OWNER_EMAIL
                        await self.email.send_email(
                            to_email=owner_to,
                            subject="Meeting booked automatically!",
                            body_text=(
                                f"{lead.get('name') or 'Lead'} said yes, so the agent "
                                f"booked the meeting on its own.\n\n{text}\n\n"
                                f"Lead email: {lead.get('email') or ''}\n"
                                f"Their reply: {body[:300]}\n\n"
                                "No action needed - the lead got the confirmation "
                                "with the Meet link."
                            ),
                            cc_owner=False,
                        )
                        # Hand the booked lead to the CEO agent so it provisions
                        # the client workspace + registers every specialist agent.
                        # The always-on agent loop auto-processes this (L2), so the
                        # client's SEO/Website/Ads/etc. work starts without you.
                        # Fire-and-forget: a handoff failure must never block the
                        # autopilot's meeting booking.
                        try:
                            from admin.agency.sba_store import create_handoff
                            await create_handoff(
                                str(lead["id"]),
                                ceo_message=(
                                    f"Lead {lead.get('name') or ''} booked a meeting "
                                    f"({text}). Provision their client workspace and "
                                    f"spin up the specialist agents."
                                ),
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("handoff creation failed for lead %s: %s",
                                          lead.get("id"), exc)
                    else:
                        # Manual booking queued + owner alerted by meeting module;
                        # do NOT claim a meeting was booked here.
                        sb_patch_lead(url, key, str(lead["id"]),
                                      {"status": "pending_manual_booking"})
                        stats["owner_notified"] += 1
                    reason.log_decision({
                        "event": "meeting_auto_booked",
                        "lead_id": str(lead.get("id") or ""),
                        "name": lead.get("name") or "",
                        "time": iso,
                        "text": text,
                    }, log_path=self._journal_path)
                elif kind in ("no", "stop"):
                    await self.email.send_email(
                        to_email=lead.get("email") or "", subject="Thanks",
                        body_text=pipe.rejected_body(lead), cc_owner=True,
                    )
                    sb_patch_lead(url, key, str(lead["id"]), {"status": "rejected"})
                    stats["rejected"] += 1
                # kind == "other": nothing actionable, leave the lead as-is.
        return stats

    async def run_once(self, global_budget: dict[str, int] | None = None) -> dict:
        """One full autopilot pass. Returns stats.

        global_budget: optional shared {"sent": int} counter the runner uses
        to cap REAL SMTP sends across all workspaces in one cycle (multi-client
        fairness). When provided, this workspace stops sending once the shared
        ceiling is reached for the whole agency this pass.
        """
        stats: dict[str, Any] = {
            "emails_sent": 0, "deferred_to_business_hours": 0, "no_email": 0,
            "invalid_email": 0, "send_failed": 0, "owner_notified": 0,
            "meetings_scheduled": 0, "rejected": 0, "new_leads_found": 0,
            "retry_backoff": 0, "unmatched_sender": 0, "followups_sent": 0,
            "followups_eligible": 0, "followups_skipped": 0,
        }
        cfg = supabase_config()
        if not cfg:
            self._last_status.update(stats)
            return stats
        url, key = cfg
        self._enrichments_this_pass = 0
        # Layer 3: the agent's own current message angle (from its last review).
        angle = strat.load_strategy(path=self._strategy_path).get("angle") or None
        # The agent emails the best-scored prospects first within the daily cap,
        # from THIS workspace's own lead pool only.
        all_leads = load_leads(url, key)
        ws_leads = [l for l in all_leads if (l.get("workspace_name") or "agency") == self.workspace_name]
        leads = reason.prioritize(ws_leads)
        attempts = 0
        for lead in leads:
            # Honour the shared agency-wide send ceiling for this cycle.
            if global_budget is not None and global_budget.get("sent", 0) >= GLOBAL_CYCLE_EMAIL_CAP:
                logger.info("global cycle email cap (%d) reached; %s workspace pausing sends",
                            GLOBAL_CYCLE_EMAIL_CAP, self.workspace_name)
                break
            result = await self._email_lead(url, key, lead, angle=angle, global_budget=global_budget)
            if result == "sent":
                stats["emails_sent"] += 1
                attempts += 1
            elif result == "send_failed":
                stats["send_failed"] += 1
                attempts += 1
            elif result == "deferred":
                stats["deferred_to_business_hours"] += 1
            elif result == "no_email":
                stats["no_email"] += 1
            elif result == "invalid_email":
                stats["invalid_email"] += 1
            elif result == "retry_backoff":
                stats["retry_backoff"] += 1
            # Cap real SMTP attempts per pass (failed sends burned the whole
            # Gmail daily limit before this cap existed).
            if attempts >= DAILY_EMAIL_CAP:
                break
        # Proactive follow-up: re-touch non-responders (owner-gated, bounded).
        # Off unless SBA_FOLLOWUP_ENABLED=true; never sends to replied/booked
        # leads, enforces MIN_DAYS + once-only, and shares the SAME daily/agency
        # send ceilings with the cold-send loop above (so the combined outbound
        # volume is bounded). Follow-up sends are folded into emails_sent so the
        # owner digest reflects real outbound volume.
        followup_stats = await self._process_followups(
            url, key, global_budget=global_budget, cold_sent=stats["emails_sent"])
        stats.update(followup_stats)
        stats["emails_sent"] += followup_stats.get("followups_sent", 0)
        reply_stats = await self._process_replies(url, key, leads)
        stats.update(reply_stats)
        stats["new_leads_found"] = await self._find_new_leads()
        stats["last_run"] = now_in(OWNER_TZ).isoformat()
        self._last_status = stats
        reason.log_decision({
            "event": "pass_summary",
            "workspace": self.workspace_name,
            "stats": {k: v for k, v in stats.items() if k != "last_run"},
        }, log_path=self._journal_path)
        await self._learn_and_report(stats)
        logger.info("autopilot pass: %s", stats)
        return stats

    async def _learn_and_report(self, stats: dict[str, Any]) -> None:
        """Layer 2 + 3: observe this pass, review strategy when due, and email
        the workspace owner a digest/alert when something important happened.
        Never blocks the loop (all failures are caught inside strat)."""
        try:
            s = await strat.maybe_review(stats, path=self._strategy_path, log_path=self._journal_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("strategy review failed: %s", exc)
            s = strat.load_strategy(path=self._strategy_path)
        owner_to = self._owner_email or OWNER_EMAIL
        if not (self.email.enabled and owner_to):
            return
        try:
            metrics = strat.metrics_from_journal(log_path=self._journal_path)
            kind = strat.digest_kind_needed(stats, metrics, path=self._strategy_path)
            if not kind:
                return
            body = strat.build_digest_body(kind, stats, metrics, s)
            subject = strat.OWNER_DIGEST_SUBJECTS[kind]
            await self.email.send_email(to_email=owner_to, subject=subject, body_text=body, cc_owner=False)
            strat.mark_digest(kind, path=self._strategy_path)
            logger.info("owner %s email sent (%s)", kind, subject)
        except Exception as exc:  # noqa: BLE001
            logger.warning("owner digest email failed: %s", exc)

    # NOTE: There is intentionally NO run_forever / while-True loop here. The SBA
    # agent is CEO-gated: it only ever runs run_once() via Lifecycle.wake (called
    # from a CEO tool). This keeps the server light — no 24/7 SBA loop (boss rule).
    async def _reset_chrome(self) -> None:
        """Best-effort close of the cached ChromeTool connection."""
        try:
            from admin.agency import langgraph_sba as lg
            for ws in list(lg._chrome_registry.keys()):
                ch = lg._chrome_registry.pop(ws, None)
                if ch is not None:
                    try:
                        await asyncio.wait_for(ch.close(), timeout=10)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass


class SBAWorkspaceRunner:
    """Runs one SBA pass for every enabled workspace (agency + clients).

    Each workspace gets its own SBAAutopilot instance (own leads, rotation,
    strategy, journal, owner email). One service runs them all in sequence so
    the lead-finding browser work never overlaps between workspaces.
    """

    def __init__(self) -> None:
        self._last_status: dict[str, Any] = {"started": now_in(OWNER_TZ).isoformat()}

    async def run_all_once(self) -> dict[str, Any]:
        from admin.agency import sba_biztypes as biztypes
        # One shared send budget for the whole agency this cycle: even with N
        # client workspaces, the autopilot never exceeds GLOBAL_CYCLE_EMAIL_CAP
        # real sends per pass, so CPU/SMTP load stays flat as clients scale.
        global_budget: dict[str, int] = {"sent": 0}
        stats: dict[str, Any] = {}
        for i, ws in enumerate(biztypes.list_sba_workspaces()):
            name = ws.get("name") or "agency"
            # Tiny stagger between workspaces so their (cheap) CPU bursts don't
            # land on the same instant as the client count grows. Browser/
            # enrichment work is already bounded; this just spreads the load.
            if i:
                await asyncio.sleep(2)
            try:
                ap = SBAAutopilot(workspace_name=name, owner_email=ws.get("owner_email") or "")
                s = await asyncio.wait_for(ap.run_once(global_budget=global_budget), timeout=PASS_TIMEOUT_SECONDS)
                stats[name] = {k: v for k, v in s.items() if k != "last_run"}
            except asyncio.TimeoutError:
                logger.exception("workspace %s pass timed out", name)
                stats[name] = {"timeout": True}
            except Exception as exc:  # noqa: BLE001
                logger.warning("workspace %s pass failed: %s", name, exc)
                stats[name] = {"error": str(exc)[:200]}
        self._last_status = {
            "started": self._last_status.get("started"),
            "last_run": now_in(OWNER_TZ).isoformat(),
            "workspaces": stats,
        }
        return stats

    # NOTE: run_all_once() above is the ONLY entry. There is intentionally NO
    # run_forever / while-True loop — the CEO wakes this runner via Lifecycle,
    # not a 24/7 scheduler (boss rule: no always-on agent loops).

    async def run_mandated(self, brief_id: str | None = None) -> dict[str, Any]:
        """CEO-gated entry point: run all workspaces once, self-sleep after.

        Called ONLY from a CEO tool via Lifecycle.wake + Lifecycle.sleep.
        """
        from admin.agency import lifecycle as lc
        lc.wake("sba", brief_id=brief_id)
        try:
            stats = await self.run_all_once()
            lc.sleep("sba")
            return stats
        except Exception as exc:  # noqa: BLE001
            lc.mark_error("sba", str(exc)[:200])
            raise


if __name__ == "__main__":
    # No 24/7 loop. To run a single mandated pass locally, use:
    #   python -c "import asyncio,admin.agency.sba_autopilot as a; print(asyncio.run(a.SBAWorkspaceRunner().run_all_once()))"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger(__name__).info("sba_autopilot: run_mandated() is CEO-gated; no standalone loop. Use run_all_once() directly for a one-off pass.")

