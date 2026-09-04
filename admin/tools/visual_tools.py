"""Visual Content Tools — brand discovery + visual production for Content Agent.

Content Agent is VISUAL-ONLY. These tools support:
  1. Brand Discovery — auto-scan client's online presence for brand identity
  2. Visual Brief Parser — interpret requests from domain agents
  3. Production Planning — plan what visuals to create
  4. Visual Generation — delegate to Kaggle GPU (FLUX/CogVideo)

Interview Q1: Visual-only execution engine. NO strategy, NO text content.
Interview Q5: Brand discovery — agent self-discovers from social media/website.
Interview Q3: Kaggle API (FLUX for images, CogVideo for videos).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. BRAND DISCOVERY (Interview Q5)
# ═══════════════════════════════════════════════════════════════

def discover_brand_identity(website_url: str) -> dict[str, Any]:
    """Auto-discover client's brand identity from their website.

    Scans for:
    - Logo (og:image, logo class, header images)
    - Color palette (CSS meta theme-color, dominant colors)
    - Brand name (title, og:site_name)
    - Visual style (image types, layout patterns)
    - Social media links

    Interview Q5: "Content Agent ko dimag do — wo khud client ka social media,
    website, existing presence analyze karega"
    """
    result = {
        "website_url": website_url,
        "discovered_at": _now(),
        "brand_name": "",
        "logo_url": "",
        "colors": [],
        "social_links": {},
        "visual_style": "",
        "image_urls": [],
        "meta_info": {},
    }

    try:
        resp = requests.get(website_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Brand name
        result["brand_name"] = _extract_brand_name(soup)

        # Logo
        result["logo_url"] = _extract_logo(soup, website_url)

        # Colors
        result["colors"] = _extract_colors(soup)

        # Social links
        result["social_links"] = _extract_social_links(soup)

        # Visual style inference
        result["visual_style"] = _infer_visual_style(soup)

        # Sample images
        result["image_urls"] = _extract_sample_images(soup, website_url, limit=10)

        # Meta info
        result["meta_info"] = _extract_meta_info(soup)

    except Exception as e:
        result["error"] = str(e)
        logger.warning("Brand discovery failed for %s: %s", website_url, e)

    return result


def _extract_brand_name(soup: BeautifulSoup) -> str:
    """Extract brand name from various sources."""
    # og:site_name
    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        return og_site["content"].strip()

    # Title tag (strip suffixes like " | Home" or " - Welcome")
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        for sep in [" | ", " - ", " — ", " :: "]:
            if sep in title:
                return title.split(sep)[0].strip()
        return title

    # Logo alt text
    logo_img = soup.find("img", class_=re.compile(r"logo", re.I))
    if logo_img and logo_img.get("alt"):
        return logo_img["alt"].strip()

    # H1
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)[:100]

    return ""


def _extract_logo(soup: BeautifulSoup, base_url: str) -> str:
    """Extract logo URL from page."""
    # og:image
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        return og_img["content"]

    # Logo class/id
    for pattern in [r"logo", r"brand"]:
        logo = soup.find("img", class_=re.compile(pattern, re.I))
        if not logo:
            logo = soup.find("img", id=re.compile(pattern, re.I))
        if logo and logo.get("src"):
            return _make_absolute(logo["src"], base_url)

    # First large image in header/nav
    header = soup.find(["header", "nav"])
    if header:
        img = header.find("img")
        if img and img.get("src"):
            return _make_absolute(img["src"], base_url)

    return ""


def _extract_colors(soup: BeautifulSoup) -> list[str]:
    """Extract brand colors from CSS."""
    colors = []

    # theme-color meta
    theme = soup.find("meta", attrs={"name": "theme-color"})
    if theme and theme.get("content"):
        colors.append(theme["content"])

    # Inline style colors (look for hex/rgb in style attributes)
    for tag in soup.find_all(style=True)[:50]:
        style = tag["style"]
        hex_colors = re.findall(r"#(?:[0-9a-fA-F]{3}){1,2}\b", style)
        rgb_colors = re.findall(r"rgb\([^)]+\)", style)
        colors.extend(hex_colors[:2])
        colors.extend(rgb_colors[:1])

    # CSS custom properties in <style> tags
    for style_tag in soup.find_all("style"):
        text = style_tag.string or ""
        css_colors = re.findall(
            r"(?:--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8}|rgb[^;]+)", text
        )
        colors.extend(css_colors[:5])

    # Deduplicate, keep first 6
    seen = set()
    unique = []
    for c in colors:
        c_lower = c.lower().strip()
        if c_lower not in seen and len(unique) < 6:
            seen.add(c_lower)
            unique.append(c)
    return unique


def _extract_social_links(soup: BeautifulSoup) -> dict[str, str]:
    """Extract social media profile links."""
    socials = {}
    platforms = {
        "instagram": r"instagram\.com/([^/\s\"'?#]+)",
        "twitter": r"(?:twitter|x)\.com/([^/\s\"'?#]+)",
        "linkedin": r"linkedin\.com/(?:company|in)/([^/\s\"'?#]+)",
        "facebook": r"facebook\.com/([^/\s\"'?#]+)",
        "youtube": r"youtube\.com/(?:@|channel/|c/)([^/\s\"'?#]+)",
        "tiktok": r"tiktok\.com/@([^/\s\"'?#]+)",
        "pinterest": r"pinterest\.(?:com|co)/([^/\s\"'?#]+)",
    }

    for link in soup.find_all("a", href=True):
        href = link["href"]
        for platform, pattern in platforms.items():
            if platform not in socials:
                match = re.search(pattern, href)
                if match:
                    socials[platform] = href if href.startswith("http") else f"https://{href}"

    return socials


def _infer_visual_style(soup: BeautifulSoup) -> str:
    """Infer visual style from page structure."""
    # Count design signals
    images = soup.find_all("img")
    videos = soup.find_all(["video", "iframe"])
    large_headings = sum(1 for h in soup.find_all(["h1", "h2"]) if len(h.get_text(strip=True)) > 30)

    # Check for specific style indicators
    has_dark = bool(soup.find(class_=re.compile(r"dark", re.I)))
    has_minimal = len(images) < 5 and large_headings < 3
    has_bold = any(
        h.get("style", "").lower().find("font-weight") != -1
        or h.get("style", "").lower().find("bold") != -1
        for h in soup.find_all(["h1", "h2"])
    )

    if has_minimal:
        return "minimal"
    if has_dark:
        return "dark/moody"
    if has_bold or len(images) > 15:
        return "bold/visual-heavy"
    if videos:
        return "video-forward"

    return "modern/clean"


def _extract_sample_images(soup: BeautifulSoup, base_url: str, limit: int = 10) -> list[str]:
    """Extract sample images for style reference."""
    images = []
    for img in soup.find_all("img", src=True)[:limit * 2]:
        src = _make_absolute(img["src"], base_url)
        # Filter out tiny icons/tracking pixels
        width = img.get("width", "")
        height = img.get("height", "")
        if width and height:
            try:
                if int(width) < 50 or int(height) < 50:
                    continue
            except (ValueError, TypeError):
                pass
        if any(skip in src.lower() for skip in ["icon", "pixel", "spacer", "1x1"]):
            continue
        images.append(src)
        if len(images) >= limit:
            break
    return images


def _extract_meta_info(soup: BeautifulSoup) -> dict[str, str]:
    """Extract meta information relevant to visual style."""
    meta = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name", "") or tag.get("property", "")
        content = tag.get("content", "")
        if name and content and name in [
            "description", "og:title", "og:description",
            "og:type", "theme-color", "msapplication-TileColor",
        ]:
            meta[name] = content
    return meta


def _make_absolute(url: str, base_url: str) -> str:
    """Convert relative URL to absolute."""
    if url.startswith(("http://", "https://")):
        return url
    parsed = urlparse(base_url)
    if url.startswith("//"):
        return f"{parsed.scheme}:{url}"
    if url.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{url}"
    return f"{parsed.scheme}://{parsed.netloc}/{url}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VISUAL BRIEF PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_visual_brief(brief_text: str) -> dict[str, Any]:
    """Parse a visual content brief from a domain agent.

    Extracts:
    - What type of visual is needed (image, video, graphic, etc.)
    - Platform/dimensions
    - Subject/topic
    - Style preferences
    - Quantity
    """
    brief_lower = brief_text.lower()

    # Detect visual type
    visual_type = "image"  # default
    if any(w in brief_lower for w in ["video", "reel", "motion", "animation", "mp4"]):
        visual_type = "video"
    elif any(w in brief_lower for w in ["infographic", "data visualization", "chart"]):
        visual_type = "infographic"
    elif any(w in brief_lower for w in ["carousel", "multiple", "series"]):
        visual_type = "carousel"
    elif any(w in brief_lower for w in ["banner", "hero", "header"]):
        visual_type = "banner"
    elif any(w in brief_lower for w in ["logo", "icon", "brand mark"]):
        visual_type = "logo"

    # Detect platform
    platform = "general"
    platform_map = {
        "instagram": ["instagram", "ig", "reel", "story", "post"],
        "facebook": ["facebook", "fb", "meta"],
        "twitter": ["twitter", "x post", "tweet"],
        "linkedin": ["linkedin", "professional"],
        "youtube": ["youtube", "yt", "thumbnail"],
        "tiktok": ["tiktok", "tt"],
        "pinterest": ["pinterest", "pin"],
        "google": ["google ads", "display ad", "banner ad"],
    }
    for plat, keywords in platform_map.items():
        if any(kw in brief_lower for kw in keywords):
            platform = plat
            break

    # Detect style
    style = "professional"  # default
    style_keywords = {
        "bold": ["bold", "vibrant", "loud", "eye-catching"],
        "minimal": ["minimal", "clean", "simple", "elegant"],
        "creative": ["creative", "artistic", "unique", "quirky"],
        "corporate": ["corporate", "formal", "business", "serious"],
        "playful": ["fun", "playful", "colorful", "friendly"],
    }
    for s, keywords in style_keywords.items():
        if any(kw in brief_lower for kw in keywords):
            style = s
            break

    # Detect quantity
    quantity = 1
    qty_match = re.search(r"(\d+)\s*(?:images?|visuals?|creatives?|pieces?)", brief_lower)
    if qty_match:
        quantity = min(int(qty_match.group(1)), 10)

    return {
        "visual_type": visual_type,
        "platform": platform,
        "style": style,
        "quantity": quantity,
        "raw_brief": brief_text,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PRODUCTION PLANNER
# ═══════════════════════════════════════════════════════════════════════════════

def plan_visual_production(
    brief: dict[str, Any],
    brand_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a visual production plan from a parsed brief.

    Determines:
    - Exact dimensions per platform
    - Prompt engineering for image/video generation
    - Brand-aware style adjustments
    - Estimated GPU time
    """
    platform_sizes = {
        "instagram": (1080, 1080),
        "facebook": (1200, 630),
        "twitter": (1200, 675),
        "linkedin": (1200, 627),
        "youtube": (1280, 720),
        "tiktok": (1080, 1920),
        "pinterest": (1000, 1500),
        "google": (1200, 628),
        "general": (1024, 1024),
    }

    visual_type = brief.get("visual_type", "image")
    platform = brief.get("platform", "general")
    style = brief.get("style", "professional")
    quantity = brief.get("quantity", 1)

    width, height = platform_sizes.get(platform, (1024, 1024))

    # Override for specific types
    if visual_type == "banner":
        width, height = 1920, 1080
    elif visual_type == "infographic":
        width, height = 1080, 1920
    elif visual_type == "video":
        width, height = (1080, 1920) if platform == "tiktok" else (1280, 720)

    # Build generation prompts
    items = []
    for i in range(quantity):
        prompt = _build_generation_prompt(
            brief=brief,
            brand=brand_identity,
            index=i,
            visual_type=visual_type,
        )
        items.append({
            "index": i + 1,
            "type": visual_type,
            "platform": platform,
            "width": width,
            "height": height,
            "prompt": prompt,
            "tool": "generate_video_kaggle" if visual_type == "video" else "generate_image_kaggle",
        })

    # Estimate GPU time
    if visual_type == "video":
        est_minutes = quantity * 5  # ~5 min per video on T4
    else:
        est_minutes = quantity * 1  # ~1 min per image on T4

    return {
        "total_items": len(items),
        "visual_type": visual_type,
        "platform": platform,
        "estimated_gpu_minutes": est_minutes,
        "items": items,
        "brand_applied": bool(brand_identity and brand_identity.get("brand_name")),
    }


def _build_generation_prompt(
    brief: dict[str, Any],
    brand: dict[str, Any] | None,
    index: int,
    visual_type: str,
) -> str:
    """Build an optimized prompt for AI generation."""
    style = brief.get("style", "professional")
    platform = brief.get("platform", "general")
    raw = brief.get("raw_brief", "professional marketing visual")

    style_map = {
        "bold": "bold, vibrant, eye-catching, dynamic colors, high contrast",
        "minimal": "minimalist, clean, elegant, white space, simple",
        "creative": "creative, artistic, unique composition, memorable",
        "corporate": "corporate, professional, business, trustworthy, clean",
        "playful": "fun, playful, colorful, friendly, energetic",
        "professional": "clean, professional, modern, high quality",
    }
    style_desc = style_map.get(style, style_map["professional"])

    # Add brand colors if available
    brand_colors = ""
    if brand and brand.get("colors"):
        brand_colors = f", brand colors: {', '.join(brand['colors'][:3])}"

    # Platform-specific prompt engineering
    platform_hints = {
        "instagram": "Instagram post format, square or portrait, social media ready",
        "facebook": "Facebook ad format, horizontal, engaging",
        "twitter": "Twitter/X post format, landscape, eye-catching",
        "linkedin": "LinkedIn professional post, business-appropriate",
        "youtube": "YouTube thumbnail, bold text space, high contrast",
        "tiktok": "TikTok vertical format, 9:16 aspect ratio, trendy",
        "pinterest": "Pinterest pin, tall vertical, informative",
        "google": "Google Display ad format, clean CTA space",
    }
    platform_hint = platform_hints.get(platform, "professional marketing visual")

    prompt = f"A {style_desc} {visual_type} for {platform_hint}{brand_colors}"
    if raw and raw != "professional marketing visual":
        prompt = f"{raw}, {style_desc}, {platform_hint}{brand_colors}"

    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 4. VISUAL TOOL EXECUTION (delegates to Kaggle tools)
# ═══════════════════════════════════════════════════════════════

def execute_visual_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Execute a visual tool by name."""
    tool_map = {
        "discover_brand_identity": lambda p: discover_brand_identity(p["website_url"]),
        "parse_visual_brief": lambda p: parse_visual_brief(p["brief_text"]),
        "plan_visual_production": lambda p: plan_visual_production(
            brief=p.get("brief", {}),
            brand_identity=p.get("brand_identity"),
        ),
    }
    if tool_name in tool_map:
        return tool_map[tool_name](params)

    # Delegate to Kaggle GPU tools for actual generation
    try:
        from admin.tools.kaggle_gpu import (
            generate_image as _kimg,
            generate_video as _kvid,
            generate_ad_image as _kad,
            generate_social_image as _ksoc,
            generate_hero_image as _khero,
        )
        _dispatch = {
            "generate_image": lambda p: _kimg(p["prompt"], p.get("platform", "instagram"), p.get("width", 0), p.get("height", 0)),
            "generate_video": lambda p: _kvid(p["prompt"], p.get("platform", "instagram"), p.get("frames", 49)),
            "generate_ad_image": lambda p: _kad(p["product"], p.get("platform", "facebook"), p.get("style", "professional")),
            "generate_social_image": lambda p: _ksoc(p["topic"], p.get("platform", "instagram")),
            "generate_hero_image": lambda p: _khero(p["topic"], p.get("style", "modern")),
        }
        if tool_name in _dispatch:
            return _dispatch[tool_name](params)
        return {"error": f"Tool '{tool_name}' not found in Kaggle GPU tools"}
    except ImportError:
        return {"error": f"Tool '{tool_name}' not found"}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TOOL DEFINITIONS (for LLM function calling)
# ═══════════════════════════════════════════════════════════════════════════════

VISUAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "discover_brand_identity",
            "description": "Scan a client's website to auto-discover brand identity: logo, colors, style, social links. Use when receiving a new client or need brand reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "website_url": {
                        "type": "string",
                        "description": "Client's website URL to scan",
                    },
                },
                "required": ["website_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_visual_brief",
            "description": "Parse a visual content brief from a domain agent. Extracts visual type, platform, style, quantity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "brief_text": {
                        "type": "string",
                        "description": "The brief text from a domain agent (SEO, Ads, Social, Website)",
                    },
                },
                "required": ["brief_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_visual_production",
            "description": "Create a production plan with exact prompts, dimensions, and GPU estimate for visual content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "brief": {
                        "type": "object",
                        "description": "Parsed brief from parse_visual_brief tool",
                    },
                    "brand_identity": {
                        "type": "object",
                        "description": "Brand identity from discover_brand_identity (optional)",
                    },
                },
                "required": ["brief"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image_kaggle",
            "description": "Generate AI image using FLUX on Kaggle GPU. Free 30hrs/week quota.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed image description"},
                    "width": {"type": "integer", "description": "Width in pixels", "default": 1024},
                    "height": {"type": "integer", "description": "Height in pixels", "default": 1024},
                    "steps": {"type": "integer", "description": "Inference steps (20-50)", "default": 20},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video_kaggle",
            "description": "Generate AI video using CogVideoX on Kaggle GPU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed video description"},
                    "frames": {"type": "integer", "description": "Frames (49=~6s, 81=~10s)", "default": 49},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_ad_image",
            "description": "Generate ad creative image sized for specific platform (Facebook, Instagram, Google, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "Product/service to advertise"},
                    "platform": {"type": "string", "description": "Target platform", "default": "facebook"},
                    "style": {"type": "string", "description": "professional, bold, minimal, creative", "default": "professional"},
                },
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_social_image",
            "description": "Generate social media post image for specific platform.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Post topic"},
                    "platform": {"type": "string", "description": "Target platform", "default": "instagram"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_hero_image",
            "description": "Generate hero/banner image for website or blog.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Hero image topic"},
                    "style": {"type": "string", "description": "modern, minimal, bold", "default": "modern"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video_ad",
            "description": "Generate video advertisement using CogVideoX on Kaggle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "Product/service to advertise"},
                    "duration": {"type": "string", "description": "short(6s), medium(10s), long(14s)", "default": "short"},
                },
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "batch_generate_images",
            "description": "Generate multiple images for content calendar (up to 5).",
            "parameters": {
                "type": "object",
                "properties": {
                    "topics": {"type": "array", "items": {"type": "string"}, "description": "List of topics"},
                    "platform": {"type": "string", "description": "Target platform", "default": "instagram"},
                },
                "required": ["topics"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# 6. COMPETITOR & REFERENCE STYLE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════


def analyze_competitor_style(competitor_urls: list[str]) -> dict:
    """Scan competitor websites and extract their visual style patterns.

    Analyzes colors, imagery types, layout patterns across multiple
    competitor sites and returns an aggregated style analysis.

    Args:
        competitor_urls: List of competitor website URLs to scan.

    Returns:
        Aggregated dict with color_frequencies, imagery_types,
        layout_patterns, common_styles, and per-site breakdowns.
    """
    all_colors: list[str] = []
    all_image_urls: list[str] = []
    all_styles: list[str] = []
    site_results: list[dict] = []

    for url in competitor_urls:
        site_data = discover_brand_identity(url)
        site_results.append(site_data)
        all_colors.extend(site_data.get("colors", []))
        all_image_urls.extend(site_data.get("image_urls", []))
        style = site_data.get("visual_style", "")
        if style:
            all_styles.append(style)

    # Aggregate color frequencies
    color_freq: dict[str, int] = {}
    for c in all_colors:
        key = c.lower().strip()
        color_freq[key] = color_freq.get(key, 0) + 1
    sorted_colors = sorted(color_freq.items(), key=lambda x: x[1], reverse=True)

    # Aggregate style frequencies
    style_freq: dict[str, int] = {}
    for s in all_styles:
        style_freq[s] = style_freq.get(s, 0) + 1
    common_styles = sorted(style_freq.items(), key=lambda x: x[1], reverse=True)

    # Infer imagery types from image count and site styles
    imagery_types: list[str] = []
    for site in site_results:
        img_count = len(site.get("image_urls", []))
        vs = site.get("visual_style", "")
        if img_count > 10:
            imagery_types.append("photography-heavy")
        if "video" in vs.lower():
            imagery_types.append("video-forward")
        if "minimal" in vs.lower():
            imagery_types.append("minimalist imagery")
    imagery_freq: dict[str, int] = {}
    for it in imagery_types:
        imagery_freq[it] = imagery_freq.get(it, 0) + 1
    sorted_imagery = sorted(imagery_freq.items(), key=lambda x: x[1], reverse=True)

    # Layout pattern inference
    layout_patterns: list[str] = []
    for site in site_results:
        vs = site.get("visual_style", "")
        img_count = len(site.get("image_urls", []))
        if "bold" in vs.lower():
            layout_patterns.append("large hero images")
        if "minimal" in vs.lower():
            layout_patterns.append("whitespace-heavy")
        if img_count > 15:
            layout_patterns.append("image grid layouts")
        if "dark" in vs.lower():
            layout_patterns.append("dark theme")
    layout_freq: dict[str, int] = {}
    for lp in layout_patterns:
        layout_freq[lp] = layout_freq.get(lp, 0) + 1
    sorted_layouts = sorted(layout_freq.items(), key=lambda x: x[1], reverse=True)

    return {
        "analyzed_at": _now(),
        "competitor_count": len(competitor_urls),
        "color_frequencies": [{"color": c, "count": n} for c, n in sorted_colors[:10]],
        "imagery_types": [{"type": t, "count": n} for t, n in sorted_imagery],
        "layout_patterns": [{"pattern": p, "count": n} for p, n in sorted_layouts],
        "common_styles": [{"style": s, "count": n} for s, n in common_styles],
        "total_images_scanned": len(all_image_urls),
        "per_site": [
            {
                "url": sr.get("website_url", ""),
                "brand_name": sr.get("brand_name", ""),
                "colors": sr.get("colors", []),
                "visual_style": sr.get("visual_style", ""),
                "image_count": len(sr.get("image_urls", [])),
            }
            for sr in site_results
        ],
    }


def get_reference_style(reference_image_url: str) -> dict:
    """Analyze a reference image URL for style attributes.

    Fetches the page containing the reference image and extracts
    style attributes from image metadata, surrounding context, and
    page-level visual signals.

    Args:
        reference_image_url: URL of the reference image or page containing it.

    Returns:
        Dict with colors, composition hints, mood, and metadata.
    """
    result: dict[str, Any] = {
        "url": reference_image_url,
        "analyzed_at": _now(),
        "colors": [],
        "composition": "unknown",
        "mood": "neutral",
        "content_type": "image",
        "metadata": {},
    }

    try:
        # Determine if this is a direct image URL
        image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")
        is_direct_image = any(
            reference_image_url.lower().split("?")[0].endswith(ext)
            for ext in image_exts
        )

        if is_direct_image:
            # Direct image -- extract metadata from URL/path
            result["content_type"] = "direct_image"
            result["metadata"]["source_url"] = reference_image_url
            parsed = urlparse(reference_image_url)
            result["metadata"]["host"] = parsed.netloc
        else:
            # Page containing the image -- fetch and parse
            resp = requests.get(
                reference_image_url, headers=_HEADERS, timeout=15
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract colors from the page context
            result["colors"] = _extract_colors(soup)

            # Extract composition hints from page structure
            images = soup.find_all("img", src=True)
            result["metadata"]["nearby_image_count"] = len(images)

            # Check for aspect ratio hints in image attributes
            for img in images:
                w = img.get("width", "")
                h = img.get("height", "")
                if w and h:
                    try:
                        ratio = int(w) / max(int(h), 1)
                        if ratio > 1.5:
                            result["composition"] = "landscape/wide"
                        elif ratio < 0.67:
                            result["composition"] = "portrait/tall"
                        else:
                            result["composition"] = "square/balanced"
                        break
                    except (ValueError, ZeroDivisionError):
                        pass

            # Mood from visual style
            result["mood"] = _infer_visual_style(soup)

            # Meta info
            result["metadata"]["title"] = ""
            if soup.title and soup.title.string:
                result["metadata"]["title"] = soup.title.string.strip()

            og_desc = soup.find("meta", property="og:description")
            if og_desc and og_desc.get("content"):
                result["metadata"]["description"] = og_desc["content"][:200]

    except Exception as e:
        result["error"] = str(e)
        logger.warning("Reference style analysis failed for %s: %s", reference_image_url, e)

    return result


def suggest_visual_variations(
    brief: dict,
    brand: dict,
    count: int = 3,
) -> list[dict]:
    """Generate variation suggestions based on a brief and brand identity.

    Creates distinct visual direction suggestions, each with a name,
    style key, mood, description, and prompt hint for image generation.

    Args:
        brief: Parsed brief dict (from parse_visual_brief) or similar.
        brand: Brand identity dict (from discover_brand_identity) or similar.
        count: Number of variations to suggest (default 3).

    Returns:
        List of variation dicts, each containing:
        name, style_key, mood, description, prompt_hint.
    """
    style = brief.get("style", "professional")
    platform = brief.get("platform", "general")
    visual_type = brief.get("visual_type", "image")
    raw_brief = brief.get("raw_brief", "")
    brand_name = brand.get("brand_name", "") if brand else ""
    brand_colors = brand.get("colors", []) if brand else []

    color_hint = ""
    if brand_colors:
        color_hint = f" using brand colors ({', '.join(brand_colors[:3])})"

    # Define variation archetypes
    archetypes = [
        {
            "name": "Bold & Vibrant",
            "style_key": "bold",
            "mood": "energetic",
            "description": "High-contrast, eye-catching design with vivid colors and dynamic composition.",
            "prompt_template": "bold vibrant {visual_type} for {platform}, high contrast, dynamic composition{color_hint}, {raw_brief}",
        },
        {
            "name": "Clean & Minimal",
            "style_key": "minimal",
            "mood": "calm",
            "description": "Elegant, whitespace-focused design that lets the subject breathe.",
            "prompt_template": "minimalist clean {visual_type} for {platform}, elegant whitespace, simple composition{color_hint}, {raw_brief}",
        },
        {
            "name": "Warm & Authentic",
            "style_key": "warm",
            "mood": "inviting",
            "description": "Warm tones, natural feel, approachable and human-centered.",
            "prompt_template": "warm authentic {visual_type} for {platform}, natural warm tones, approachable{color_hint}, {raw_brief}",
        },
        {
            "name": "Sleek & Premium",
            "style_key": "premium",
            "mood": "sophisticated",
            "description": "Polished, premium aesthetic with refined details and luxury feel.",
            "prompt_template": "sleek premium {visual_type} for {platform}, luxury feel, refined details{color_hint}, {raw_brief}",
        },
        {
            "name": "Playful & Fun",
            "style_key": "playful",
            "mood": "joyful",
            "description": "Colorful, energetic, friendly design that puts a smile on the viewer.",
            "prompt_template": "playful fun {visual_type} for {platform}, colorful, friendly, energetic{color_hint}, {raw_brief}",
        },
        {
            "name": "Dark & Moody",
            "style_key": "dark",
            "mood": "dramatic",
            "description": "Dark palette with rich accents, dramatic lighting and atmosphere.",
            "prompt_template": "dark moody {visual_type} for {platform}, dramatic lighting, rich dark palette{color_hint}, {raw_brief}",
        },
        {
            "name": "Corporate & Trustworthy",
            "style_key": "corporate",
            "mood": "professional",
            "description": "Clean corporate aesthetic, trustworthy, business-appropriate.",
            "prompt_template": "corporate professional {visual_type} for {platform}, trustworthy, clean business aesthetic{color_hint}, {raw_brief}",
        },
        {
            "name": "Creative & Artistic",
            "style_key": "creative",
            "mood": "inspiring",
            "description": "Artistic composition with unique angles and creative flair.",
            "prompt_template": "creative artistic {visual_type} for {platform}, unique composition, artistic flair{color_hint}, {raw_brief}",
        },
    ]

    # Match current style to boost matching archetype to first position
    style_to_key = {
        "bold": "bold",
        "minimal": "minimal",
        "creative": "creative",
        "corporate": "corporate",
        "playful": "playful",
        "professional": "corporate",
    }
    preferred_key = style_to_key.get(style, "bold")

    # Reorder: put preferred style first, then pick diverse others
    matched = [a for a in archetypes if a["style_key"] == preferred_key]
    others = [a for a in archetypes if a["style_key"] != preferred_key]
    ordered = matched + others

    # Ensure we return at least `count` variations (loop if fewer archetypes)
    variations = []
    seen_keys: set[str] = set()
    for archetype in ordered:
        if len(variations) >= count:
            break
        if archetype["style_key"] in seen_keys:
            continue
        seen_keys.add(archetype["style_key"])
        prompt_hint = archetype["prompt_template"].format(
            visual_type=visual_type,
            platform=platform,
            color_hint=color_hint,
            raw_brief=raw_brief or "marketing visual",
        )
        if brand_name:
            prompt_hint = f"{brand_name} brand, {prompt_hint}"
        variations.append({
            "name": archetype["name"],
            "style_key": archetype["style_key"],
            "mood": archetype["mood"],
            "description": archetype["description"],
            "prompt_hint": prompt_hint,
        })

    return variations
