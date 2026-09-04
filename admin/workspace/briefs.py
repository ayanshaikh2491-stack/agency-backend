"""Structured Brief System — Every domain agent produces a proper brief for Content Agent.

The problem: Domain agents currently say "5 images chahiye" — too vague.
The fix: Each domain agent has a structured brief template for Content Agent.

Flow:
  1. Domain Agent (Social/Ads/SEO/Website) creates a STRUCTURED brief
     - KYA chahiye (what visual)
     - KYUN chahiye (why, what purpose)  
     - KAISA dikhna chahiye (mood, style, description)
     - KAB tak chahiye (timeline)
     - KIS PLATFORM ke liye (instagram, facebook, etc.)
     
  2. Content Agent receives brief + applies its OWN intelligence:
     - Brand colors/style from client context
     - Platform dimensions
     - AI prompt engineering
     - Previous learnings from Agency Content Agent
     - Industry best practices
     
  3. Content Agent submits to GPU queue → generates → reports back

This makes BOTH agents think properly:
  Social Agent = Strategy + Content Planning + Brief Writing
  Content Agent = Creative Execution + Prompt Engineering + GPU Management
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Social Agent Brief ────────────────────────────────────────────────────────


@dataclass
class SocialVisualBrief:
    """Structured brief from Social Agent to Content Agent.
    
    Social Agent fills: WHAT content, WHY it works, WHAT mood
    Content Agent fills: HOW to make it (prompts, dimensions, colors)
    """
    # Social Agent provides
    brief_id: str = field(default_factory=lambda: f"sb_{uuid.uuid4().hex[:8]}")
    day: str = ""  # Monday, Tuesday, etc.
    post_type: str = ""  # listing, testimonial, behind_scenes, education, event, engagement
    title: str = ""  # Short title for the post
    description: str = ""  # Detailed description of what to show
    mood: str = ""  # Premium, warm, professional, fun, etc.
    text_overlay: str = ""  # Text on image
    cta: str = ""  # Call to action
    hashtags: str = ""  # Relevant hashtags
    why_this_works: str = ""  # Why Social Agent chose this
    
    # Platform & scheduling
    platform: str = "instagram"
    post_time: str = ""  # Best time to post
    content_mix_category: str = ""  # listings, testimonial, education, engagement
    
    # Strategy context
    target_audience: str = ""  # Who this targets
    competitor_reference: str = ""  # What competitors do for this
    performance_expectation: str = ""  # Expected: saves, shares, comments, etc.


# ── Ads Agent Brief ───────────────────────────────────────────────────────────


@dataclass
class AdsVisualBrief:
    """Structured brief from Ads Agent to Content Agent.
    
    Ads Agent fills: Campaign goal, ad format, audience, messaging
    Content Agent fills: Visual execution
    """
    brief_id: str = field(default_factory=lambda: f"ab_{uuid.uuid4().hex[:8]}")
    campaign_name: str = ""
    ad_type: str = ""  # carousel, single_image, video, story_ad, reel_ad
    platform: str = "facebook"
    objective: str = ""  # awareness, leads, conversions, traffic
    headline: str = ""
    primary_text: str = ""
    description: str = ""
    cta: str = ""  # Learn More, Sign Up, Buy Now, etc.
    visual_style: str = ""  # product_focused, lifestyle, testimonial
    mood: str = ""  # Professional, fun, urgent, premium
    target_audience: str = ""
    budget_note: str = ""  # High budget = premium visuals
    a_b_test_note: str = ""  # What to vary in A/B test


# ── SEO Agent Brief ───────────────────────────────────────────────────────────


@dataclass
class SEOVisualBrief:
    """Structured brief from SEO Agent to Content Agent.
    
    SEO Agent fills: Blog topic, keywords, content structure
    Content Agent fills: Featured image, social share image, infographics
    """
    brief_id: str = field(default_factory=lambda: f"seob_{uuid.uuid4().hex[:8]}")
    blog_title: str = ""
    keywords: list[str] = field(default_factory=list)
    content_type: str = ""  # blog_image, featured_image, social_share, infographic
    topic_summary: str = ""
    mood: str = ""  # Educational, authoritative, friendly
    target_audience: str = ""
    article_section: str = ""  # For section-specific images
    alt_text_suggestion: str = ""  # For SEO-optimized alt text


# ── Website Agent Brief ───────────────────────────────────────────────────────


@dataclass
class WebsiteVisualBrief:
    """Structured brief from Website Agent to Content Agent.
    
    Website Agent fills: Page, section, purpose
    Content Agent fills: Visual assets
    """
    brief_id: str = field(default_factory=lambda: f"wb_{uuid.uuid4().hex[:8]}")
    page: str = ""  # home, about, services, contact, landing
    section: str = ""  # hero, features, testimonials, footer
    asset_type: str = ""  # hero_banner, feature_icon, background, logo_variation
    purpose: str = ""  # First impression, trust building, conversion
    mood: str = ""  # Professional, welcoming, innovative
    dimensions_needed: str = ""  # Specific size if needed
    style_reference: str = ""  # "Like Stripe's homepage" etc.
    cta_context: str = ""  # What the CTA says near this visual


# ── Brief Registry — Maps agents to their brief types ─────────────────────────

BRIEF_TYPES = {
    "social": SocialVisualBrief,
    "ads": AdsVisualBrief,
    "seo": SEOVisualBrief,
    "website": WebsiteVisualBrief,
}


def create_brief(agent_type: str, **kwargs) -> Any:
    """Create a structured brief for any domain agent type."""
    brief_class = BRIEF_TYPES.get(agent_type)
    if not brief_class:
        raise ValueError(f"No brief type for agent: {agent_type}. Available: {list(BRIEF_TYPES.keys())}")
    return brief_class(**kwargs)


def brief_to_dict(brief: Any) -> dict[str, Any]:
    """Convert a brief dataclass to dict."""
    return {k: v for k, v in brief.__dict__.items() if v}


def brief_to_content_agent_input(brief: Any) -> str:
    """Convert a structured brief into a natural language prompt for Content Agent.
    
    Content Agent receives this as its input — a clear, structured brief
    that tells it exactly what to create, and the Content Agent applies
    its own creative intelligence to enhance and execute.
    """
    if isinstance(brief, SocialVisualBrief):
        return _social_brief_to_text(brief)
    elif isinstance(brief, AdsVisualBrief):
        return _ads_brief_to_text(brief)
    elif isinstance(brief, SEOVisualBrief):
        return _seo_brief_to_text(brief)
    elif isinstance(brief, WebsiteVisualBrief):
        return _website_brief_to_text(brief)
    else:
        return str(brief_to_dict(brief))


def _social_brief_to_text(brief: SocialVisualBrief) -> str:
    lines = [
        f"## Social Media Visual Brief — {brief.day}",
        f"**Post Type:** {brief.post_type}",
        f"**Title:** {brief.title}",
        f"**Description:** {brief.description}",
        f"**Mood:** {brief.mood}",
    ]
    if brief.text_overlay:
        lines.append(f"**Text on Image:** {brief.text_overlay}")
    if brief.cta:
        lines.append(f"**CTA:** {brief.cta}")
    lines.extend([
        f"**Platform:** {brief.platform}",
        f"**Target Audience:** {brief.target_audience}",
        f"**Why This Works:** {brief.why_this_works}",
        f"**Performance Goal:** {brief.performance_expectation}",
        "",
        "Use your creative intelligence to enhance this brief. Apply brand colors, "
        "choose the best dimensions for the platform, and engineer the perfect "
        "AI generation prompt.",
    ])
    return "\n".join(lines)


def _ads_brief_to_text(brief: AdsVisualBrief) -> str:
    lines = [
        f"## Ad Creative Brief — {brief.campaign_name}",
        f"**Ad Type:** {brief.ad_type}",
        f"**Platform:** {brief.platform}",
        f"**Objective:** {brief.objective}",
        f"**Headline:** {brief.headline}",
        f"**Primary Text:** {brief.primary_text}",
        f"**CTA Button:** {brief.cta}",
        f"**Visual Style:** {brief.visual_style}",
        f"**Mood:** {brief.mood}",
        f"**Target Audience:** {brief.target_audience}",
    ]
    if brief.a_b_test_note:
        lines.append(f"**A/B Test Note:** {brief.a_b_test_note}")
    lines.extend([
        "",
        "Use your creative intelligence to enhance this brief. Apply brand colors, "
        "optimize for the ad platform, and create scroll-stopping visuals.",
    ])
    return "\n".join(lines)


def _seo_brief_to_text(brief: SEOVisualBrief) -> str:
    lines = [
        f"## SEO Visual Brief — {brief.blog_title}",
        f"**Content Type:** {brief.content_type}",
        f"**Keywords:** {', '.join(brief.keywords)}",
        f"**Topic:** {brief.topic_summary}",
        f"**Mood:** {brief.mood}",
        f"**Target Audience:** {brief.target_audience}",
    ]
    if brief.alt_text_suggestion:
        lines.append(f"**Suggested Alt Text:** {brief.alt_text_suggestion}")
    lines.extend([
        "",
        "Use your creative intelligence to enhance this brief. Create SEO-optimized "
        "visuals with appropriate keywords in mind.",
    ])
    return "\n".join(lines)


def _website_brief_to_text(brief: WebsiteVisualBrief) -> str:
    lines = [
        f"## Website Visual Brief — {brief.page} page, {brief.section} section",
        f"**Asset Type:** {brief.asset_type}",
        f"**Purpose:** {brief.purpose}",
        f"**Mood:** {brief.mood}",
        f"**Dimensions:** {brief.dimensions_needed}" if brief.dimensions_needed else "",
        f"**Style Reference:** {brief.style_reference}" if brief.style_reference else "",
        f"**CTA Context:** {brief.cta_context}" if brief.cta_context else "",
    ]
    lines.extend([
        "",
        "Use your creative intelligence to enhance this brief. Apply brand identity, "
        "ensure responsive design compatibility, and create conversion-optimized visuals.",
    ])
    return "\n".join(l for l in lines if l)  # Filter empty strings
