"""Structured Brief Builder — Domain Agents ke liye shared brief format.

Har domain agent isi format mein brief bhejta hai Content Agent ko.
Reasoning Chain ko sab information mil jaati hai.

Hardened version:
- Threads workspace_id / client_name / industry (multi-client safe).
- Crash-safe: never raises on missing keys; safe defaults only.
- Richer premium fields (brand_voice, tone, deliverable_spec, do_nots,
  success_metric) so Content Agent gets client-facing, usable briefs.
- Pure data + text — CPU-friendly, no GPU/network deps.
"""
from __future__ import annotations

from typing import Any


# Domain -> default objective + focus flag. Single source of truth.
_DOMAIN_PRESETS: dict[str, dict[str, Any]] = {
    "ads": {"objective": "lead_generation", "focus_key": "conversion_focus", "focus_value": True},
    "social": {"objective": "engagement", "focus_key": "engagement_focus", "focus_value": True},
    "seo": {"objective": "traffic", "focus_key": "traffic_focus", "focus_value": True},
    "website": {"objective": "trust", "focus_key": "trust_focus", "focus_value": True},
}

_VALID_PRIORITIES = {"normal", "high", "urgent"}
_VALID_PLATFORMS = {
    "instagram", "facebook", "linkedin", "twitter", "youtube",
    "tiktok", "blog_hero", "pinterest", "google_display", "website",
}


def _clean_priority(priority: str) -> str:
    return priority if priority in _VALID_PRIORITIES else "normal"


def _clean_platform(platform: str) -> str:
    p = (platform or "").lower()
    return p if p in _VALID_PLATFORMS else "instagram"


def build_domain_brief(
    domain: str,
    content_type: str,
    topic: str,
    platform: str = "instagram",
    description: str = "",
    style: str = "bold",
    priority: str = "normal",
    quantity: int = 1,
    # Domain-specific fields
    objective: str = "",
    target_audience: dict[str, Any] | None = None,
    emotional_hook: str = "curiosity",
    cta: str = "learn_more",
    key_message: str = "",
    competitor_context: str = "",
    constraints: str = "",
    copy_text: str = "",
    brand_guidelines: dict[str, Any] | None = None,
    # Multi-tenant context (NEW)
    workspace_id: str = "",
    client_name: str = "",
    industry: str = "",
    # Premium enrichment (NEW)
    brand_voice: str = "",
    tone: str = "",
    success_metric: str = "",
    do_nots: str = "",
) -> dict[str, Any]:
    """Structured brief banao jo Content Agent ko samajh aaye.

    Args:
        domain: "ads", "social", "seo", "website"
        content_type: "ad_creative", "social_post", "hero_image", etc.
        topic: Brief topic/subject
        platform: Where it will be published
        description: Detailed description
        style: "bold", "minimal", "professional", etc.
        priority: "normal", "high", "urgent"
        quantity: Number of variations needed
        objective: "lead_generation", "brand_awareness", "engagement", "conversions"
        target_audience: {"age": "...", "interests": [...], "pain_points": [...]}
        emotional_hook: "fear", "curiosity", "trust", "excitement", "urgency", "FOMO"
        cta: "sign_up", "buy_now", "learn_more", "contact_us"
        key_message: The ONE thing this visual must communicate
        competitor_context: What competitors are doing
        constraints: Any limitations
        copy_text: Text that will appear on the visual
        brand_guidelines: Brand-specific guidelines
        workspace_id: Owning workspace (multi-tenant isolation)
        client_name: Client/brand name for client-facing output
        industry: Vertical for smarter defaults (e.g. "realestate", "fitness")
        brand_voice: e.g. "playful", "authoritative", "friendly"
        tone: e.g. "energetic", "calm", "aspirational"
        success_metric: How success is measured (CTR, leads, saves)
        do_nots: Explicit things to avoid in the creative
    """
    preset = _DOMAIN_PRESETS.get(domain, {})
    resolved_objective = objective or preset.get("objective", "")

    brief: dict[str, Any] = {
        "domain": domain,
        "workspace_id": workspace_id or "",
        "client_name": client_name or "",
        "industry": industry or "",
        "content_type": content_type,
        "topic": topic,
        "platform": _clean_platform(platform),
        "description": description,
        "style": style,
        "priority": _clean_priority(priority),
        "quantity": max(1, int(quantity or 1)),
        "objective": resolved_objective,
        "target_audience": target_audience or {
            "age": "25-45",
            "interests": [],
            "pain_points": [],
        },
        "emotional_hook": emotional_hook,
        "cta": cta,
        "key_message": key_message or topic,
        "competitor_context": competitor_context,
        "constraints": constraints,
        "copy_text": copy_text,
        "brand_guidelines": brand_guidelines or {},
        # Premium enrichment
        "brand_voice": brand_voice,
        "tone": tone,
        "success_metric": success_metric,
        "do_nots": do_nots,
    }

    # Domain-specific focus flag (keeps legacy consumers working)
    if preset:
        brief[preset["focus_key"]] = preset["focus_value"]

    return brief


def brief_to_text(brief: dict[str, Any]) -> str:
    """Structured brief ko human-readable text mein convert karo.

    Crash-safe: tolerates missing keys and non-list values.
    Yeh text Content Agent ko seedha milta hai.
    """
    if not isinstance(brief, dict):
        return str(brief)

    def _join(values) -> str:
        if not values:
            return ""
        if isinstance(values, (list, tuple, set)):
            try:
                return ", ".join(str(v) for v in values if v is not None)
            except Exception:
                return str(values)
        return str(values)

    lines = [
        f"=== {(brief.get('domain') or 'content').upper()} CONTENT REQUEST ===",
        "",
    ]

    # Client / workspace context (premium, client-facing)
    if brief.get("client_name"):
        lines.append(f"Client: {brief['client_name']}")
    if brief.get("industry"):
        lines.append(f"Industry: {brief['industry']}")
    if brief.get("workspace_id"):
        lines.append(f"Workspace ID: {brief['workspace_id']}")
    if any(brief.get(k) for k in ("client_name", "industry", "workspace_id")):
        lines.append("")

    lines += [
        f"Content Type: {brief.get('content_type', '')}",
        f"Topic: {brief.get('topic', '')}",
        f"Platform: {brief.get('platform', '')}",
        f"Style: {brief.get('style', '')}",
        f"Priority: {brief.get('priority', '')}",
        f"Quantity: {brief.get('quantity', 1)}",
        "",
        f"--- STRATEGY ---",
        f"Objective: {brief.get('objective', '')}",
        f"Emotional Hook: {brief.get('emotional_hook', '')}",
        f"CTA: {brief.get('cta', '')}",
        f"Key Message: {brief.get('key_message', '')}",
    ]

    # Premium enrichment
    for label, key in (
        ("Brand Voice", "brand_voice"),
        ("Tone", "tone"),
        ("Success Metric", "success_metric"),
        ("Do NOTs", "do_nots"),
    ):
        if brief.get(key):
            lines.append(f"{label}: {brief[key]}")

    # Target audience
    audience = brief.get("target_audience") or {}
    if isinstance(audience, dict) and audience:
        lines += ["", "--- TARGET AUDIENCE ---", f"Age: {audience.get('age', '25-45')}"]
        interests = _join(audience.get("interests"))
        if interests:
            lines.append(f"Interests: {interests}")
        pain = _join(audience.get("pain_points"))
        if pain:
            lines.append(f"Pain Points: {pain}")

    # Additional context
    for label, key in (
        ("DESCRIPTION", "description"),
        ("COPY TEXT", "copy_text"),
        ("COMPETITOR CONTEXT", "competitor_context"),
        ("CONSTRAINTS", "constraints"),
    ):
        if brief.get(key):
            lines += ["", f"--- {label} ---", str(brief[key])]

    # Brand guidelines (flattened, crash-safe)
    bg = brief.get("brand_guidelines")
    if isinstance(bg, dict) and bg:
        lines += ["", "--- BRAND GUIDELINES ---"]
        for k, v in bg.items():
            lines.append(f"{k}: {_join(v) if isinstance(v, (list, tuple, set)) else v}")

    return "\n".join(lines)
