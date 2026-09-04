# admin/tools/sba_email_draft.py
"""Professional cold outreach email drafting for the SBA autopilot.

Uses the SBA skill registry (cold-email, alex-hormozi-pitch, etc.) to build
context, then the configured LLM (WORKSPACE_*) drafts a personalized email.
Falls back to a clean template if the LLM is unavailable or fails.
"""
from __future__ import annotations

import logging
from typing import Any

from admin.config import settings

logger = logging.getLogger(__name__)


def fallback_email(lead: dict) -> tuple[str, str]:
    name = lead.get("name") or "there"
    category = lead.get("category") or "local business"
    subject = f"Quick question for {name}"
    body = (
        f"Hi {name},\n\n"
        f"I came across {name} and noticed you're a {category} in the local area. "
        "We're TAGS Agency, and we help local businesses get more customers with "
        "a professional website and local SEO.\n\n"
        "Would you be open to a quick 15-minute call this week to see if we can help?\n\n"
        "Best,\nAyan\nTAGS Agency"
    )
    return subject, body


def fallback_followup(lead: dict, touch_index: int = 0, total_touches: int = 1) -> tuple[str, str]:
    """Polite, non-pushy second (or later) touch for a non-responding first email.

    We do NOT present a fake 'per my last email' thread; the lead may have missed
    the first one, so we keep it short and give a low-friction out (no reply
    needed). Re-emailing non-responders is opt-in and bounded (see config). The
    copy varies slightly by touch so repeat touches don't read as identical spam.
    """
    name = lead.get("name") or "there"
    category = lead.get("category") or "local business"
    # First follow-up vs later ones: a touch more direct on the last attempt.
    if touch_index == 0:
        subject = f"Following up - {name}"
        body = (
            f"Hi {name},\n\n"
            f"I'll keep this short. We help {category} businesses like yours get found "
            "by more local customers through a modern website and local SEO.\n\n"
            "If now isn't the right time, no worries at all - just ignore this. "
            "But if a quick 15-minute chat this week could be useful, I'm happy to make time.\n\n"
            "Best,\nAyan\nTAGS Agency"
        )
    else:
        subject = f"Last note from me - {name}"
        body = (
            f"Hi {name},\n\n"
            f"One last note so I'm not cluttering your inbox. If growing "
            f"{category} through a better website + local SEO is on your radar this "
            "quarter, I'd love to help. If not, no hard feelings - just reply 'no' and "
            "I'll close your file.\n\n"
            "Best,\nAyan\nTAGS Agency"
        )
    return subject, body


async def _llm_draft(lead: dict, skill_context: str, angle: str | None = None) -> tuple[str, str]:
    """Call the configured LLM to draft a personalized email."""
    import openai

    client = openai.AsyncOpenAI(
        api_key=settings.WORKSPACE_API_KEY or None,
        base_url=settings.WORKSPACE_API_BASE or None,
    )
    name = lead.get("name") or "the business"
    category = lead.get("category") or "local business"
    city_state = f"{lead.get('city')}, {lead.get('state')}".strip(" ,")
    system = (
        "You are a sharp cold-outreach copywriter for TAGS Agency, a web design + "
        "local SEO agency. Write ONE professional, personalized cold email to a "
        "local business. Keep it short (under 120 words), friendly, specific, "
        "and end with a soft CTA for a 15-minute call.\n\n"
        f"RELEVANT SKILLS:\n{skill_context}"
    )
    if angle:
        # Layer 3: the agent's current strategy angle, decided by its own review.
        system += f"\n\nCURRENT MESSAGE ANGLE (weave this in naturally, do not quote it):\n{angle}"
    user = (
        f"Lead: {name} ({category}) in {city_state}. "
        "Return JSON: {\"subject\": \"...\", \"body\": \"...\"}. Body plain text only."
    )
    resp = await client.chat.completions.create(
        model=settings.WORKSPACE_AGENT_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.5,
    )
    content = (resp.choices[0].message.content or "").strip()
    import json
    import re

    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError("LLM did not return JSON")
    data = json.loads(m.group(0))
    subject = data.get("subject") or f"Quick question for {name}"
    body = data.get("body") or fallback_email(lead)[1]
    return subject, body


async def _llm_followup(lead: dict, skill_context: str, touch_index: int = 0,
                      total_touches: int = 1) -> tuple[str, str]:
    """Call the configured LLM to draft a polite follow-up touch."""
    import openai

    client = openai.AsyncOpenAI(
        api_key=settings.WORKSPACE_API_KEY or None,
        base_url=settings.WORKSPACE_API_BASE or None,
    )
    name = lead.get("name") or "the business"
    category = lead.get("category") or "local business"
    city_state = f"{lead.get('city')}, {lead.get('state')}".strip(" ,")
    touch_label = f"follow-up touch {touch_index + 1} of {total_touches}"
    system = (
        "You are a sharp cold-outreach copywriter for TAGS Agency, a web design + "
        "local SEO agency. Write ONE short, polite follow-up email to a local "
        "business that did not reply to our earlier email(s). Keep it under 90 words, "
        "friendly, never pushy, give a low-friction out (no reply needed), and end "
        f"with a soft CTA for a 15-minute call. This is the {touch_label} "
        "(later touches may be slightly more direct but must stay respectful)."
        f"\n\nRELEVANT SKILLS:\n{skill_context}"
    )
    user = (
        f"Lead: {name} ({category}) in {city_state}. "
        "Return JSON: {\"subject\": \"...\", \"body\": \"...\"}. Body plain text only."
    )
    resp = await client.chat.completions.create(
        model=settings.WORKSPACE_AGENT_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.5,
    )
    content = (resp.choices[0].message.content or "").strip()
    import json
    import re

    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError("LLM did not return JSON")
    data = json.loads(m.group(0))
    # Vary the subject on later touches so they don't look identical.
    default_subject = (f"Last note from me - {name}" if touch_index > 0
                      else f"Following up - {name}")
    subject = data.get("subject") or default_subject
    body = data.get("body") or fallback_followup(lead, touch_index, total_touches)[1]
    return subject, body


async def draft_email(lead: dict, angle: str | None = None) -> tuple[str, str]:
    """Draft a professional email; fall back to template on any failure."""
    try:
        from admin.agency.sba_skills import build_skill_context, detect_skills

        skills = detect_skills(f"cold outreach email to {lead.get('name', '')}", max_skills=2)
        ctx = build_skill_context(skills) if skills else "No extra skills matched."
        return await _llm_draft(lead, ctx, angle=angle)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM email draft failed (%s); using template", exc)
        return fallback_email(lead)


async def draft_followup(lead: dict, touch_index: int = 0,
                         total_touches: int = 1) -> tuple[str, str]:
    """Draft a polite non-responder follow-up touch; fall back to template on failure."""
    try:
        from admin.agency.sba_skills import build_skill_context, detect_skills

        skills = detect_skills(f"follow-up email to {lead.get('name', '')}", max_skills=2)
        ctx = build_skill_context(skills) if skills else "No extra skills matched."
        return await _llm_followup(lead, ctx, touch_index=touch_index, total_touches=total_touches)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM follow-up draft failed (%s); using template", exc)
        return fallback_followup(lead, touch_index, total_touches)
