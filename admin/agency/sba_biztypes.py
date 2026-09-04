# admin/agency/sba_biztypes.py
"""Per-workspace decision support for the SBA lead-gen autopilot.

The autopilot needs to know, for each workspace, whether the client business
actually needs SBA-style local cold-email lead generation, and - when it does -
what to work on: a niche rotation, an outreach angle, and where to keep the
strategy / reasoning-journal / rotation-state files.

This module is deliberately dumb and synchronous: it classifies businesses from
keywords (D2C/ecommerce/etc. sell direct and get ``needs_sba False``; local
services get ``True``; unknown stays ``False`` so the owner can enable manually)
and merges the classification with persisted overrides from a per-workspace
config file. It never raises and never makes network calls, so the autopilot can
call it unconditionally on every pass.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("sba.biztypes")

# Persisted per-workspace config; overridable for tests / other hosts.
CONFIG_FILE = os.environ.get(
    "SBA_WORKSPACES_CONFIG_FILE",
    "/home/ubuntu/sba-backend/sba_workspaces.json",
)

# Keys callers may persist per workspace.
PERSISTED_KEYS = (
    "enabled", "owner_email", "industry", "category", "rotation", "angle",
    "aeo_angle", "geo_angle",
    # Per-workspace email identity: each client uses ITS OWN inbox, never the
    # agency's. smtp_email defaults to owner_email when not set explicitly.
    "smtp_email", "smtp_password", "smtp_host", "smtp_port",
    "imap_host", "imap_port",
)

# Businesses that sell direct (D2C / ecommerce / product / software): no local
# cold-email lead generation needed.
DIRECT_SALE_KEYWORDS = (
    "d2c", "ecommerce", "e-commerce", "e commerce", "retail", "saas", "software",
    "app", "fashion", "apparel", "store", "restaurant", "cafe", "food",
    "beverage", "cosmetics", "beauty brand",
)

# Local-services businesses: exactly the niche cold-email lead gen targets.
LOCAL_SERVICES_KEYWORDS = (
    "real estate", "realtor", "property", "plumbing", "plumber", "hvac",
    "heating", "air conditioning", "electrical", "electrician", "roofing",
    "roofer", "landscaping", "lawn", "pest control", "cleaning", "construction",
    "contractor", "painting", "painter", "auto repair", "garage", "mechanic",
    "salon", "spa", "barber", "dentist", "clinic", "law", "legal", "accounting",
    "tax", "marketing agency", "staffing", "it services", "commercial cleaning",
)

AGENCY_ANGLE = (
    "Help local businesses get more customers with a professional website and local SEO"
)

# Default niche rotation for the agency workspace (38 targets).
DEFAULT_ROTATION: list[tuple[str, str, str]] = [
    ("plumber", "Houston", "TX"), ("electrician", "San Antonio", "TX"),
    ("hvac", "Austin", "TX"), ("roofer", "Dallas", "TX"),
    ("landscaper", "Fort Worth", "TX"), ("auto repair", "Houston", "TX"),
    ("cleaning service", "San Antonio", "TX"), ("handyman", "Austin", "TX"),
    ("painter", "Dallas", "TX"), ("dentist", "Fort Worth", "TX"),
    ("plumber", "Austin", "TX"), ("electrician", "Houston", "TX"),
    ("hvac", "Dallas", "TX"), ("roofer", "San Antonio", "TX"),
    ("landscaper", "Houston", "TX"), ("auto repair", "Austin", "TX"),
    ("cleaning service", "Dallas", "TX"), ("handyman", "Fort Worth", "TX"),
    ("painter", "San Antonio", "TX"), ("salon", "Houston", "TX"),
    ("plumber", "Phoenix", "AZ"), ("electrician", "Atlanta", "GA"),
    ("hvac", "Charlotte", "NC"), ("roofer", "Tampa", "FL"),
    ("landscaper", "Orlando", "FL"), ("auto repair", "Denver", "CO"),
    ("cleaning service", "Las Vegas", "NV"), ("handyman", "Nashville", "TN"),
    ("painter", "Oklahoma City", "OK"), ("salon", "Memphis", "TN"),
    ("plumber", "San Diego", "CA"), ("electrician", "Columbus", "OH"),
    ("hvac", "Kansas City", "MO"), ("roofer", "New Orleans", "LA"),
    ("landscaper", "Louisville", "KY"), ("auto repair", "Albuquerque", "NM"),
    ("cleaning service", "Tulsa", "OK"), ("handyman", "El Paso", "TX"),
]

# A few tailored rotations for common categories; everything else falls back to
# the generic Texas rotation.
TAILORED_ROTATIONS: dict[str, list[tuple[str, str, str]]] = {
    "real estate": [
        ("real estate agent", "Houston", "TX"),
        ("realtor", "Dallas", "TX"),
        ("property management", "Austin", "TX"),
    ],
    "plumbing": [
        ("plumber", "Houston", "TX"),
        ("plumber", "Austin", "TX"),
        ("plumber", "Dallas", "TX"),
    ],
}

TAILORED_ANGLES: dict[str, str] = {
    "real estate": (
        "Help local property owners and real estate businesses get more clients "
        "with a strong online presence"
    ),
}

# Per-category AEO (Answer Engine Optimization) angle: how the business should
# show up inside AI answers (ChatGPT / Perplexity / Gemini / AI Overviews).
TAILORED_AEO_ANGLES: dict[str, str] = {
    "real estate": (
        "Be the answer when AI is asked 'who is the best realtor in {city}' or "
        "'how do I sell my house fast in {city}' -- optimize for AI Overviews and "
        "ChatGPT recommendations with entity-rich bios and FAQ content"
    ),
    "plumbing": (
        "Rank inside AI answers for 'emergency plumber near me' and 'best plumber "
        "in {city}' -- structure service pages as clear Q&A AI can cite"
    ),
    "hvac": (
        "Show up when AI answers 'who repairs AC in {city}' or 'best HVAC company "
        "near me' -- FAQ + service-area schema AI can quote"
    ),
    "roofing": (
        "Be cited by AI for 'roof replacement cost in {city}' and 'best roofers "
        "near me' -- quote-style content and local proof"
    ),
    "dentist": (
        "Appear in AI answers for 'best dentist for implants near me' and 'top "
        "dentist in {city}' -- treatment FAQs and verified reviews AI trusts"
    ),
    "electrician": (
        "Rank in AI Overviews for 'emergency electrician in {city}' -- clear "
        "service Q&A and licensing credentials AI can verify"
    ),
    "law": (
        "Be the cited source when AI answers 'best lawyer for divorce in {city}' "
        "-- authority content and case-result FAQs"
    ),
}

# Per-category GEO (Generative Engine Optimization) angle: being the source AI
# search engines cite / recommend.
TAILORED_GEO_ANGLES: dict[str, str] = {
    "real estate": (
        "Become a primary citation for AI real-estate guides in {city} -- publish "
        "original market data and neighborhood insights LLMs reference"
    ),
    "plumbing": (
        "Get cited by generative engines as the trusted {city} plumbing authority "
        "-- original how-to guides and structured data AI pulls from"
    ),
    "hvac": (
        "Be the source Perplexity/Gemini quote for {city} HVAC help -- "
        "diagnostic guides and entity-verified business profile"
    ),
    "roofing": (
        "Earn AI citations for roofing advice in {city} -- cost-breakdown content "
        "and verifiable local presence"
    ),
    "dentist": (
        "Be referenced by AI for dental care in {city} -- treatment explainers and "
        "EEAT signals (credentials, reviews, citations)"
    ),
    "electrician": (
        "Get quoted by AI search for {city} electrical help -- safety guides and "
        "licensed-professional proof"
    ),
    "law": (
        "Be the authority AI cites for {city} legal questions -- practice-area "
        "explainers and verifiable case expertise"
    ),
}


def _aeo_angle_for(category: str, city: str = "your city") -> str:
    return TAILORED_AEO_ANGLES.get(
        category,
        f"Optimize for AI answers about {category} in {city} -- FAQ + entity "
        "structured data so ChatGPT/Perplexity cite you",
    ).format(city=city)


def _geo_angle_for(category: str, city: str = "your city") -> str:
    return TAILORED_GEO_ANGLES.get(
        category,
        f"Become a citation source for AI search on {category} in {city} -- "
        "original, trustworthy content LLMs reference",
    ).format(city=city)


# ── Classification ───────────────────────────────────────────────────────────


def _matches(haystack: str, keywords: tuple[str, ...]) -> str | None:
    """Return the first keyword found in the lowercased haystack, else None."""
    lowered = haystack.lower()
    for kw in keywords:
        if kw in lowered:
            return kw
    return None


def classify_business(
    workspace_name: str,
    industry: str = "",
    description: str = "",
    category: str = "",
) -> dict[str, Any]:
    """Decide whether a workspace's client business needs SBA lead generation.

    Returns ``{"needs_sba": bool, "category": str, "rotation": [...], "angle": str,
    "aeo_angle": str, "geo_angle": str}``.
    The agency workspace is always enabled (it *is* the lead-gen business).
    Businesses that sell direct (D2C / ecommerce / software / etc.) are never
    SBA candidates. Local-services keywords enable it. Unknown stays disabled
    (conservative: the owner enables manually).
    """
    if workspace_name.strip().lower() == "agency":
        return {
            "needs_sba": True,
            "category": "local business",
            "rotation": [list(t) for t in DEFAULT_ROTATION],
            "angle": AGENCY_ANGLE,
            "aeo_angle": (
                "Be the answer when AI is asked 'best marketing agency near me' or "
                "'how do I get more local customers' -- agency FAQs + case studies AI cites"
            ),
            "geo_angle": (
                "Become a citation source for AI 'how to get more customers' guides -- "
                "original local-marketing playbooks LLMs reference"
            ),
        }

    haystack = " ".join([industry, description, category, workspace_name])
    if _matches(haystack, DIRECT_SALE_KEYWORDS):
        return {
            "needs_sba": False,
            "category": _matches(haystack, DIRECT_SALE_KEYWORDS) or "",
            "rotation": [],
            "angle": "",
            "aeo_angle": "",
            "geo_angle": "",
        }

    matched = _matches(haystack, LOCAL_SERVICES_KEYWORDS)
    if not matched:
        return {
            "needs_sba": False,
            "category": "",
            "rotation": [],
            "angle": "",
            "aeo_angle": "",
            "geo_angle": "",
        }

    rotation = TAILORED_ROTATIONS.get(
        matched,
        [[matched, "Houston", "TX"], [matched, "Dallas", "TX"], [matched, "Austin", "TX"]],
    )
    angle = TAILORED_ANGLES.get(
        matched, f"Help {matched} businesses get more local customers"
    )
    city = rotation[0][1] if rotation else "your city"
    return {
        "needs_sba": True,
        "category": matched,
        "rotation": [list(t) for t in rotation],
        "angle": angle,
        "aeo_angle": _aeo_angle_for(matched, city),
        "geo_angle": _geo_angle_for(matched, city),
    }


# ── Persistence ──────────────────────────────────────────────────────────────


def _load_all() -> dict[str, Any]:
    """Read the whole config file as a dict; never raise."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("sba workspaces config is not a dict: %r", type(data))
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.warning("could not read sba workspaces config %s: %s", CONFIG_FILE, exc)
    return {}


def _save_all(data: dict[str, Any]) -> bool:
    """Atomically persist the config file; never raise."""
    try:
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, CONFIG_FILE)
        return True
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.warning("could not write sba workspaces config %s: %s", CONFIG_FILE, exc)
        return False


def get_workspace_config(workspace_name: str) -> dict[str, Any]:
    """Merged per-workspace config: persisted overrides beat classification.

    Always includes ``name`` and ``needs_sba`` (== ``enabled``). Persisted keys:
    ``enabled`` (default = classified ``needs_sba``), ``owner_email`` (default ""),
    ``industry``, ``category``, ``rotation``, ``angle``.
    """
    name = workspace_name.strip()
    base = classify_business(name)
    persisted = (_load_all() or {}).get(name, {})

    enabled = persisted.get("enabled", base["needs_sba"])
    category = persisted.get("category", base["category"])
    rotation = persisted.get("rotation", base["rotation"])
    angle = persisted.get("angle", base["angle"])
    aeo_angle = persisted.get("aeo_angle", base.get("aeo_angle", ""))
    geo_angle = persisted.get("geo_angle", base.get("geo_angle", ""))

    return {
        "name": name,
        "needs_sba": bool(enabled),
        "enabled": bool(enabled),
        "owner_email": str(persisted.get("owner_email", "")),
        "industry": str(persisted.get("industry", "")),
        "category": str(category),
        "rotation": rotation,
        "angle": str(angle),
        "aeo_angle": str(aeo_angle),
        "geo_angle": str(geo_angle),
        # Per-workspace SMTP/IMAP identity (client's own app password).
        "smtp_email": str(persisted.get("smtp_email", "")),
        "smtp_password": str(persisted.get("smtp_password", "")),
        "smtp_host": str(persisted.get("smtp_host", "")),
        "smtp_port": str(persisted.get("smtp_port", "")),
        "imap_host": str(persisted.get("imap_host", "")),
        "imap_port": str(persisted.get("imap_port", "")),
    }


def set_workspace_config(workspace_name: str, **updates: Any) -> bool:
    """Persist overrides for one workspace (create the entry if missing).

    Allowed keys: ``enabled``, ``owner_email``, ``industry``, ``category``,
    ``rotation``, ``angle``. Returns True on a successful write.
    """
    name = workspace_name.strip()
    if not name:
        return False
    data = _load_all()
    entry = data.get(name)
    if not isinstance(entry, dict):
        entry = {}
    changed = False
    for key, value in updates.items():
        if key in PERSISTED_KEYS:
            entry[key] = value
            changed = True
    if not changed:
        return False
    data[name] = entry
    return _save_all(data)


def list_sba_workspaces() -> list[dict[str, Any]]:
    """Every workspace with ``enabled`` True.

    Each entry: ``{"name", "category", "owner_email", "rotation", "angle"}``.
    If the agency workspace is missing from the config, it is included enabled
    with the default category / rotation / angle. Never raises.
    """
    try:
        data = _load_all()
        if "agency" not in data:
            data["agency"] = {
                "enabled": True,
                "category": "local business",
                "rotation": [list(t) for t in DEFAULT_ROTATION],
                "angle": AGENCY_ANGLE,
            }
        out: list[dict[str, Any]] = []
        for name, entry in data.items():
            if not isinstance(entry, dict):
                continue
            if not entry.get("enabled", False):
                continue
            out.append(
                {
                    "name": str(name),
                    "category": str(entry.get("category", "")),
                    "owner_email": str(entry.get("owner_email", "")),
                    "rotation": entry.get("rotation", []),
                    "angle": str(entry.get("angle", "")),
                    # SBA-autopilot reads the full per-workspace config itself
                    # (incl. smtp creds) via get_workspace_config().
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.warning("list_sba_workspaces failed: %s", exc)
        return []


# ── Paths ────────────────────────────────────────────────────────────────────


def _sanitize(workspace_name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", workspace_name.strip().lower()) or "workspace"


def strategy_path(workspace_name: str) -> str:
    """Strategy-file path for a workspace (name sanitized)."""
    return f"/home/ubuntu/sba-backend/sba_strategy_{_sanitize(workspace_name)}.json"


def journal_path(workspace_name: str) -> str:
    """Reasoning-journal path for a workspace (name sanitized)."""
    return f"/home/ubuntu/sba-backend/sba_reasoning_{_sanitize(workspace_name)}.log"


def rotation_state_path(workspace_name: str) -> str:
    """Rotation-state path for a workspace (name sanitized)."""
    return f"/home/ubuntu/sba-backend/sba_rotation_{_sanitize(workspace_name)}.state"
