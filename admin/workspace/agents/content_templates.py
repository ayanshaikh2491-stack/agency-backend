"""Content Templates — Platform configs, prompt templates, and style presets.

Used by the 6-node Content Agent pipeline for consistent visual generation
across all workspaces and platforms.
"""
from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# PLATFORM CONFIGS (size, aspect ratio, best practices per platform)
# ═══════════════════════════════════════════════════════════════════════════════

PLATFORM_CONFIGS: dict[str, dict[str, Any]] = {
    "instagram": {
        "width": 1080,
        "height": 1080,
        "aspect": "1:1",
        "variants": {
            "square": (1080, 1080),
            "portrait": (1080, 1350),
            "story": (1080, 1920),
            "reel": (1080, 1920),
        },
        "video_frames": {"reel": 81, "story": 49},
        "tips": "High contrast, vibrant colors, clean composition, bold text space.",
    },
    "facebook": {
        "width": 1200,
        "height": 630,
        "aspect": "1.91:1",
        "variants": {
            "post": (1200, 630),
            "ad": (1080, 1080),
            "story": (1080, 1920),
            "cover": (820, 312),
        },
        "video_frames": 49,
        "tips": "Warm tones, clear focal point, avoid small text.",
    },
    "linkedin": {
        "width": 1200,
        "height": 627,
        "aspect": "1.91:1",
        "variants": {"post": (1200, 627), "article": (1200, 627)},
        "video_frames": 49,
        "tips": "Professional, corporate, muted tones, clean design.",
    },
    "twitter": {
        "width": 1200,
        "height": 675,
        "aspect": "16:9",
        "variants": {"post": (1200, 675)},
        "video_frames": 49,
        "tips": "Bold, eye-catching, works at small sizes.",
    },
    "youtube": {
        "width": 1280,
        "height": 720,
        "aspect": "16:9",
        "variants": {"thumbnail": (1280, 720), "banner": (2560, 1440)},
        "video_frames": 81,
        "tips": "High contrast thumbnail, face close-ups work well.",
    },
    "tiktok": {
        "width": 1080,
        "height": 1920,
        "aspect": "9:16",
        "variants": {"video": (1080, 1920)},
        "video_frames": 81,
        "tips": "Vertical, dynamic motion, bold colors, fast-paced.",
    },
    "blog_hero": {
        "width": 1200,
        "height": 600,
        "aspect": "2:1",
        "variants": {"hero": (1200, 600), "wide": (1920, 1080)},
        "video_frames": 49,
        "tips": "Wide format, clean left side for text overlay.",
    },
    "pinterest": {
        "width": 1000,
        "height": 1500,
        "aspect": "2:3",
        "variants": {"pin": (1000, 1500)},
        "video_frames": 49,
        "tips": "Tall format, lifestyle imagery, bright colors.",
    },
    "google_display": {
        "width": 1200,
        "height": 628,
        "aspect": "1.91:1",
        "variants": {
            "landscape": (1200, 628),
            "square": (1200, 1200),
            "portrait": (1200, 1500),
            "banner": (728, 90),
            "skyscraper": (300, 600),
        },
        "video_frames": 49,
        "tips": "Clean design, minimal text, strong CTA, high contrast.",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# CONTENT TYPE CONFIGS
# ═══════════════════════════════════════════════════════════════════════════════

CONTENT_TYPE_CONFIGS: dict[str, dict[str, Any]] = {
    "image": {
        "tool": "generate_image",
        "default_steps": 20,
        "best_steps": 30,
        "quality_steps": 50,
        "models": ["FLUX.1-dev", "SDXL"],
        "prompt_style": "detailed, descriptive, photographic/artistic language",
    },
    "video": {
        "tool": "generate_video",
        "default_frames": 49,
        "long_frames": 81,
        "models": ["CogVideoX-2b"],
        "prompt_style": "motion-focused, temporal description, scene dynamics",
    },
    "ugc": {
        "tool": "generate_video",
        "default_frames": 81,
        "models": ["CogVideoX-2b"],
        "prompt_style": "authentic, handheld feel, natural lighting, real-person aesthetic",
    },
    "marketing": {
        "tool": "generate_video",
        "default_frames": 49,
        "models": ["CogVideoX-2b"],
        "prompt_style": "polished, commercial, brand-forward, call-to-action space",
    },
    "carousel": {
        "tool": "generate_carousel",
        "default_slides": 5,
        "max_slides": 10,
        "default_steps": 20,
        "best_steps": 30,
        "quality_steps": 50,
        "models": ["FLUX.1-dev", "SDXL"],
        "prompt_style": "cohesive series, consistent style, visual storytelling, slide-by-slide narrative",
        "slide_formats": {
            "instagram": [(1080, 1080), (1080, 1350)],
            "facebook": [(1200, 630), (1080, 1080)],
            "linkedin": [(1200, 627), (1080, 1080)],
        },
    },
    "story": {
        "tool": "generate_image",
        "default_steps": 20,
        "best_steps": 30,
        "quality_steps": 50,
        "models": ["FLUX.1-dev", "SDXL"],
        "prompt_style": "vertical, bold, quick-glance, immersive, full-screen visual",
    },
    "ad_creative": {
        "tool": "generate_image",
        "default_steps": 25,
        "best_steps": 35,
        "quality_steps": 50,
        "models": ["FLUX.1-dev", "SDXL"],
        "prompt_style": "professional ad, CTA-friendly space, brand-forward, conversion-optimized",
    },
    "thumbnail": {
        "tool": "generate_image",
        "default_steps": 25,
        "best_steps": 35,
        "quality_steps": 50,
        "models": ["FLUX.1-dev", "SDXL"],
        "prompt_style": "high contrast, bold, eye-catching, face-friendly, click-worthy",
    },
    "unboxing": {
        "tool": "generate_video",
        "default_frames": 81,
        "models": ["CogVideoX-2b"],
        "prompt_style": "authentic unboxing moment, excitement, first impressions, reveal shots, genuine reaction",
    },
    "testimonial": {
        "tool": "generate_video",
        "default_frames": 81,
        "models": ["CogVideoX-2b"],
        "prompt_style": "authentic testimonial, real person, genuine emotion, trustworthy, relatable",
    },
    "explainer": {
        "tool": "generate_video",
        "default_frames": 49,
        "models": ["CogVideoX-2b"],
        "prompt_style": "clear explainer, step-by-step visuals, educational, clean transitions",
    },
    "product_showcase": {
        "tool": "generate_video",
        "default_frames": 49,
        "models": ["CogVideoX-2b"],
        "prompt_style": "polished product showcase, 360-degree views, premium feel, studio quality",
    },
    "trading": {
        "tool": "generate_video",
        "default_frames": 49,
        "models": ["CogVideoX-2b"],
        "prompt_style": "dynamic charts, data visualization, financial aesthetics",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# STYLE PRESETS
# ═══════════════════════════════════════════════════════════════════════════════

STYLE_PRESETS: dict[str, dict[str, str]] = {
    "bold": {
        "prompt_suffix": "bold, high contrast, vivid colors, strong composition, impactful",
        "negative": "faded, washed out, soft, pastel, blurry, low contrast",
    },
    "minimal": {
        "prompt_suffix": "minimalist, clean, white space, elegant, simple composition",
        "negative": "cluttered, busy, noisy, complex, many elements, busy background",
    },
    "professional": {
        "prompt_suffix": "professional, corporate, polished, clean, trustworthy",
        "negative": "amateur, cartoon, childish, messy, unprofessional, low quality",
    },
    "modern": {
        "prompt_suffix": "modern, contemporary, trendy, sleek design, current aesthetics",
        "negative": "retro, vintage, outdated, old-fashioned, dated",
    },
    "cinematic": {
        "prompt_suffix": "cinematic, dramatic lighting, movie-like, depth of field, film grade",
        "negative": "flat lighting, overexposed, underexposed, dull, lifeless",
    },
    "vibrant": {
        "prompt_suffix": "vibrant colors, saturated, energetic, lively, dynamic palette",
        "negative": "desaturated, gray, muted, monochrome, dull colors",
    },
    "elegant": {
        "prompt_suffix": "elegant, refined, luxurious, sophisticated, premium feel",
        "negative": "cheap, tacky, garish, overdone, gaudy",
    },
    "playful": {
        "prompt_suffix": "playful, fun, colorful, lighthearted, engaging",
        "negative": "serious, dark, somber, gloomy, depressing",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# VARIATION STYLES (for generating multiple prompt variations)
# ═══════════════════════════════════════════════════════════════════════════════

VARIATION_STYLES: dict[str, dict[str, str]] = {
    "hero": {
        "name": "Hero / Focal",
        "description": "Strong central subject, dramatic lighting, hero shot",
        "prompt_addon": "hero shot, central subject, dramatic rim lighting, bold presence, eye-level angle",
    },
    "lifestyle": {
        "name": "Lifestyle / Candid",
        "description": "Natural, in-use, real-world context",
        "prompt_addon": "lifestyle photography, natural setting, candid feel, real-world context, authentic moment",
    },
    "flat_lay": {
        "name": "Flat Lay / Overhead",
        "description": "Top-down view, arranged composition",
        "prompt_addon": "flat lay composition, overhead view, arranged items, clean background, product display",
    },
    "abstract": {
        "name": "Abstract / Artistic",
        "description": "Creative interpretation, artistic style",
        "prompt_addon": "abstract art style, creative interpretation, artistic composition, unique perspective",
    },
    "macro": {
        "name": "Macro / Detail",
        "description": "Close-up, detail focus, texture",
        "prompt_addon": "macro photography, extreme close-up, detailed texture, shallow depth of field, intricate details",
    },
    "aerial": {
        "name": "Aerial / Wide",
        "description": "Wide angle, expansive, landscape feel",
        "prompt_addon": "aerial view, wide angle, expansive landscape, panoramic, bird's eye perspective",
    },
    "dynamic": {
        "name": "Dynamic / Motion",
        "description": "Movement, energy, action",
        "prompt_addon": "dynamic motion, energy, action shot, movement blur, energetic composition",
    },
    "serene": {
        "name": "Serene / Calm",
        "description": "Peaceful, soft, calming",
        "prompt_addon": "serene atmosphere, soft lighting, peaceful, calming mood, gentle tones",
    },
    "carousel_slide": {
        "name": "Carousel Slide",
        "description": "Cohesive slide with consistent style, text-friendly space",
        "prompt_addon": "carousel slide design, clean layout, text-friendly space, cohesive series, modern graphic design",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO-SPECIFIC KEYWORDS
# ═══════════════════════════════════════════════════════════════════════════════

VIDEO_HUMAN_KEYWORDS: list[str] = [
    "natural hand movements",
    "subtle facial expressions",
    "authentic body language",
    "casual posture",
    "real person",
    "candid moment",
    "genuine emotion",
    "spontaneous gesture",
    "eye contact",
    "natural smile",
    "handheld camera feel",
    "slight camera shake",
    "warm natural lighting",
    "everyday environment",
    "relatable scenario",
]

VIDEO_AI_AVOID_KEYWORDS: list[str] = [
    "robotic movement",
    "uncanny valley",
    "static pose",
    "perfect symmetry",
    "artificial lighting",
    "stiff gestures",
    "CGI look",
    "3D render",
    "animation style",
    "cartoon",
    "motionless",
    "frozen",
    "unnatural skin",
    "plastic texture",
    "flat shading",
]


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT TEMPLATES (per content type)
# ═══════════════════════════════════════════════════════════════════════════════

IMAGE_PROMPT_TEMPLATE = (
    "Professional {style} image of {topic}. "
    "{brand_color_desc}"
    "Composition: {composition}. "
    "Lighting: {lighting}. "
    "Mood: {mood}. "
    "Color palette: {color_application}. "
    "{avoid_section}"
    "High quality, sharp focus, {platform} optimized, 4K detail."
)

VIDEO_PROMPT_TEMPLATE = (
    "A {motion_description} scene featuring {topic}. "
    "Camera movement: {camera_move}. "
    "Style: {style}. "
    "{brand_color_desc}"
    "Pacing: {pacing}. "
    "Mood: {mood}. "
    "Lighting: {lighting}. "
    "Smooth motion, professional video quality, cinematic look."
)

UGC_VIDEO_PROMPT_TEMPLATE = (
    "Authentic UGC-style video of {topic}. "
    "Handheld camera feel, natural lighting, casual real-person aesthetic. "
    "{human_keywords}"
    "Environment: {environment}. "
    "Mood: {mood}. "
    "Genuine, relatable, unscripted feel. "
    "Vertical format, natural color grading, smartphone-quality authenticity."
)

MARKETING_VIDEO_PROMPT_TEMPLATE = (
    "Polished marketing video showcasing {topic}. "
    "Brand colors: {brand_colors}. "
    "Motion: {motion_description}. "
    "Professional commercial look, smooth transitions, "
    "clean typography space, {pacing} pacing. "
    "Mood: {mood}. "
    "Studio quality, broadcast-ready, brand-forward presentation."
)

CAROUSEL_SLIDE_TEMPLATE = (
    "{slide_number}/{total_slides}: {slide_title}. "
    "{slide_description} "
    "Style: {style}. Brand: {brand_colors}. "
    "{composition} {lighting}"
)

CAROUSEL_PROMPT_TEMPLATE = (
    "Professional {style} carousel slide for {platform}. "
    "{brand_color_desc}"
    "Slide {slide_number} of {total_slides}: {slide_title}. "
    "{slide_description} "
    "Composition: {composition}. "
    "Lighting: {lighting}. "
    "Mood: {mood}. "
    "Cohesive series, consistent visual style across all slides. "
    "High quality, sharp focus, {platform} optimized, 4K detail."
)

TRADING_VIDEO_PROMPT_TEMPLATE = (
    "Dynamic financial visualization of {topic}. "
    "Animated chart elements: {chart_elements}. "
    "Color scheme: {color_application}. "
    "Motion: {motion_description}. "
    "Professional trading terminal aesthetic, data-driven visualization, "
    "smooth number animations, {pacing} pacing. "
    "Mood: {mood}. Clean, modern financial UI feel."
)

STORY_PROMPT_TEMPLATE = (
    "Full-screen vertical {style} story image of {topic}. "
    "{brand_color_desc}"
    "Immersive, bold, designed for quick glance. "
    "Composition: {composition}. "
    "Lighting: {lighting}. "
    "Mood: {mood}. "
    "Vertical format 9:16, mobile-first, vibrant and attention-grabbing. "
    "High quality, sharp focus, {platform} story optimized."
)

AD_CREATIVE_PROMPT_TEMPLATE = (
    "Professional {style} ad creative for {topic}. "
    "{brand_color_desc}"
    "Leave clean space for CTA button. "
    "Composition: {composition}. "
    "Lighting: {lighting}. "
    "Mood: {mood}. "
    "Conversion-optimized, brand-forward, clear focal point. "
    "High quality, sharp focus, {platform} ad optimized, clean design."
)

THUMBNAIL_PROMPT_TEMPLATE = (
    "Eye-catching {style} thumbnail for {topic}. "
    "{brand_color_desc}"
    "High contrast, bold, click-worthy design. "
    "Composition: {composition}. "
    "Lighting: {lighting}. "
    "Mood: {mood}. "
    "Face-friendly, expressive, dramatic, works at small sizes. "
    "High quality, sharp focus, YouTube/blog thumbnail optimized."
)

UNBOXING_VIDEO_PROMPT_TEMPLATE = (
    "Authentic unboxing video of {topic}. "
    "Genuine excitement, first impressions, hands-on reveal. "
    "{human_keywords}"
    "Environment: {environment}. "
    "Mood: {mood}. "
    "Close-up product shots, genuine reaction, anticipation building. "
    "Natural lighting, handheld camera feel, smartphone-quality authenticity."
)

TESTIMONIAL_VIDEO_PROMPT_TEMPLATE = (
    "Authentic testimonial video featuring {topic}. "
    "Real person speaking, genuine emotion, trustworthy delivery. "
    "{human_keywords}"
    "Environment: {environment}. "
    "Mood: {mood}. "
    "Face-to-camera, natural lighting, relatable, authentic experience. "
    "Vertical format, genuine, unscripted feel."
)

EXPLAINER_VIDEO_PROMPT_TEMPLATE = (
    "Clear explainer video about {topic}. "
    "Step-by-step visuals, educational, informative. "
    "Motion: {motion_description}. "
    "Pacing: {pacing}. "
    "Mood: {mood}. "
    "Clean transitions, professional, easy to follow. "
    "Smooth motion, high quality, broadcast-ready."
)

PRODUCT_SHOWCASE_VIDEO_PROMPT_TEMPLATE = (
    "Polished product showcase video of {topic}. "
    "{brand_color_desc}"
    "360-degree views, premium feel, studio quality. "
    "Motion: {motion_description}. "
    "Pacing: {pacing}. "
    "Mood: {mood}. "
    "Elegant lighting, smooth transitions, luxury presentation. "
    "Professional commercial look, studio-quality production."
)


# ═══════════════════════════════════════════════════════════════════════════════
# NEGATIVE PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════

IMAGE_NEGATIVE_PROMPT = (
    "blurry, low quality, pixelated, watermark, text overlay, logo, "
    "distorted, deformed, ugly, overexposed, underexposed, "
    "noise, grain, artifacts, cropped, out of frame, "
    "stock photo feel, generic, bland composition"
)

VIDEO_NEGATIVE_PROMPT = (
    "static, motionless, frozen, jerky movement, stuttering, "
    "low resolution, pixelated, dark, underlit, "
    "robotic motion, unnatural movement, repetitive, "
    "watermark, text, logo, artifacts, noise"
)


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY KEYWORDS (for auto-detection)
# ═══════════════════════════════════════════════════════════════════════════════

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "fitness": ["gym", "workout", "fitness", "exercise", "muscle", "training", "health", "wellness"],
    "food": ["restaurant", "food", "cuisine", "dish", "cooking", "recipe", "cafe", "menu"],
    "realestate": ["property", "house", "home", "real estate", "apartment", "interior", "architecture"],
    "fashion": ["fashion", "clothing", "outfit", "style", "wear", "apparel", "brand"],
    "tech": ["software", "app", "technology", "digital", "SaaS", "platform", "AI"],
    "beauty": ["beauty", "skincare", "makeup", "cosmetic", "spa", "wellness"],
    "travel": ["travel", "destination", "vacation", "hotel", "resort", "adventure"],
    "education": ["course", "learn", "education", "training", "class", "tutorial"],
    "finance": ["trading", "stock", "finance", "investment", "crypto", "market", "chart"],
    "ecommerce": ["product", "shop", "store", "sale", "deal", "offer", "buy"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO MOTION PRESETS
# ═══════════════════════════════════════════════════════════════════════════════

VIDEO_MOTION_PRESETS: dict[str, dict[str, str]] = {
    "slow_zoom": {
        "motion": "slow zoom into the subject",
        "camera_move": "gentle forward dolly zoom",
        "pacing": "slow, contemplative",
    },
    "pan": {
        "motion": "smooth horizontal pan across the scene",
        "camera_move": "left-to-right pan revealing details",
        "pacing": "steady, measured",
    },
    "orbit": {
        "motion": "circular orbit around the subject",
        "camera_move": "360-degree orbit, parallax movement",
        "pacing": "medium, dynamic",
    },
    "static_reveal": {
        "motion": "elements appearing and building up in frame",
        "camera_move": "locked-off static shot with animated elements",
        "pacing": "gradual buildup",
    },
    "handheld": {
        "motion": "natural handheld camera movement",
        "camera_move": "slight organic shake, documentary-style",
        "pacing": "natural, authentic",
    },
    "tracking": {
        "motion": "tracking shot following the subject",
        "camera_move": "smooth lateral tracking movement",
        "pacing": "fluid, cinematic",
    },
    "whip_pan": {
        "motion": "fast whip pan transition between scenes",
        "camera_move": "rapid horizontal whip",
        "pacing": "fast, energetic",
    },
    "crane": {
        "motion": "crane shot rising up or descending",
        "camera_move": "vertical crane movement, revealing perspective",
        "pacing": "dramatic, building",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# DETECTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_content_category(text: str) -> str:
    """Auto-detect content category from keywords in text."""
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[category] = score
    if scores:
        return max(scores, key=scores.get)  # type: ignore[arg-type]
    return "general"


def detect_platform(text: str) -> str:
    """Auto-detect target platform from keywords in text."""
    text_lower = text.lower()
    platform_signals = {
        "instagram": ["instagram", "insta", "ig ", "reel", "story", "feed post"],
        "facebook": ["facebook", "fb ", "meta ads"],
        "linkedin": ["linkedin", "professional network"],
        "twitter": ["twitter", " x ", "tweet"],
        "youtube": ["youtube", "yt ", "thumbnail", "channel"],
        "tiktok": ["tiktok", "tick", "tok "],
        "pinterest": ["pinterest", "pin "],
        "blog_hero": ["blog", "hero", "banner", "header"],
        "google_display": ["google display", "display ad", "google ads", "banner ad"],
    }
    for platform, signals in platform_signals.items():
        if any(s in text_lower for s in signals):
            return platform
    return "instagram"  # default


def get_platform_format(text: str) -> str:
    """Detect platform format variant from text."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["story", "stories"]):
        return "story"
    if any(kw in text_lower for kw in ["reel", "reels", "short"]):
        return "reel"
    if any(kw in text_lower for kw in ["portrait", "vertical", "tall"]):
        return "portrait"
    if any(kw in text_lower for kw in ["landscape", "wide", "horizontal"]):
        return "landscape"
    if any(kw in text_lower for kw in ["square", "1:1"]):
        return "square"
    if any(kw in text_lower for kw in ["thumbnail", "cover"]):
        return "thumbnail"
    if any(kw in text_lower for kw in ["carousel", "carousal", "swipe", "multiple slides"]):
        return "carousel"
    if any(kw in text_lower for kw in ["unboxing", "unpacking", "reveal"]):
        return "post"
    if any(kw in text_lower for kw in ["testimonial", "review", "feedback"]):
        return "post"
    if any(kw in text_lower for kw in ["explainer", "tutorial", "how-to", "how to"]):
        return "post"
    if any(kw in text_lower for kw in ["product showcase", "showcase", "360"]):
        return "post"
    if any(kw in text_lower for kw in ["banner", "skyscraper", "leaderboard"]):
        return "banner"
    return "post"  # default


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT BRIEF TEMPLATES (premium, client-facing starting points)
# ═══════════════════════════════════════════════════════════════════════════════
#
# These are opinionated, high-quality defaults. Domain agents clone them and
# override with their own brief fields, so every generated brief starts from a
# strong, usable baseline instead of an empty shell. Pure data — no network.

DEFAULT_BRIEF_TEMPLATES: dict[str, dict[str, Any]] = {
    "ads_lead_gen": {
        "domain": "ads",
        "content_type": "ad_creative",
        "platform": "facebook",
        "style": "bold",
        "priority": "high",
        "quantity": 3,
        "objective": "lead_generation",
        "emotional_hook": "curiosity",
        "cta": "sign_up",
        "key_message": "Your problem solved — faster and simpler than you expected.",
        "target_audience": {
            "age": "25-45",
            "interests": ["solutions", "deals", "productivity"],
            "pain_points": ["too expensive", "too complicated", "slow results"],
        },
        "copy_text": "",
        "competitor_context": "Competitors use feature-heavy static ads; we lead with outcome.",
        "constraints": "Must leave clean CTA space; no body copy on image.",
        "brand_voice": "confident",
        "tone": "aspirational",
        "success_metric": "Lead form fills / CTR",
        "do_nots": "Avoid clutter,avoid small text,avoid stocky clip-art",
        "description": (
            "Conversion-focused ad creative. One bold visual idea that stops the "
            "scroll and makes the CTA inevitable. Brand-forward but never busy."
        ),
    },
    "social_engagement": {
        "domain": "social",
        "content_type": "social_post",
        "platform": "instagram",
        "style": "vibrant",
        "priority": "normal",
        "quantity": 2,
        "objective": "engagement",
        "emotional_hook": "excitement",
        "cta": "learn_more",
        "key_message": "A moment worth stopping for.",
        "target_audience": {
            "age": "18-34",
            "interests": ["lifestyle", "trends", "community"],
            "pain_points": ["boring feed", "missing out"],
        },
        "copy_text": "",
        "competitor_context": "Trend-driven carousel formats are winning engagement.",
        "constraints": "Square or portrait; safe text area for overlay.",
        "brand_voice": "playful",
        "tone": "energetic",
        "success_metric": "Saves / shares / comments",
        "do_nots": "Avoid heavy text overlay,avoid generic stock photos",
        "description": (
            "Scroll-stopping social visual built for pattern interrupt and "
            "emotional resonance. Optimized for saves and shares."
        ),
    },
    "seo_traffic": {
        "domain": "seo",
        "content_type": "blog_hero",
        "platform": "blog_hero",
        "style": "professional",
        "priority": "normal",
        "quantity": 1,
        "objective": "traffic",
        "emotional_hook": "trust",
        "cta": "learn_more",
        "key_message": "Authoritative, useful, and instantly credible.",
        "target_audience": {
            "age": "25-55",
            "interests": ["research", "how-to", "comparisons"],
            "pain_points": ["unclear info", "untrustworthy sources"],
        },
        "copy_text": "",
        "competitor_context": "Top-ranking pages use clean hero + clear value prop.",
        "constraints": "Wide format, left-safe text area for H1 overlay.",
        "brand_voice": "authoritative",
        "tone": "calm",
        "success_metric": "Organic CTR from SERP",
        "do_nots": "Avoid clutter,avoid low-contrast text on image",
        "description": (
            "SEO hero image that earns the click from search results. Clean, "
            "trustworthy, and built to pair with an optimized title + meta."
        ),
    },
    "website_trust": {
        "domain": "website",
        "content_type": "hero_image",
        "platform": "website",
        "style": "elegant",
        "priority": "normal",
        "quantity": 1,
        "objective": "trust",
        "emotional_hook": "trust",
        "cta": "contact_us",
        "key_message": "We are the safe, premium choice.",
        "target_audience": {
            "age": "30-60",
            "interests": ["quality", "reliability", "service"],
            "pain_points": ["risk of bad vendor", "unclear pricing"],
        },
        "copy_text": "",
        "competitor_context": "Category leaders use restrained, premium hero shots.",
        "constraints": "Above-the-fold hero; text-free focal area.",
        "brand_voice": "trustworthy",
        "tone": "calm",
        "success_metric": "Time on page / contact conversions",
        "do_nots": "Avoid busy backgrounds,avoid hard-sell imagery",
        "description": (
            "Trust-building hero for an above-the-fold website section. Premium, "
            "restrained, and clearly communicates value at a glance."
        ),
    },
}


def get_default_brief_template(template_name: str) -> dict[str, Any]:
    """Return a deep copy of a default brief template (safe to mutate).

    Falls back to ``social_engagement`` for unknown names so callers always
    get a usable baseline instead of None.
    """
    import copy

    template = DEFAULT_BRIEF_TEMPLATES.get(template_name)
    if not template:
        template = DEFAULT_BRIEF_TEMPLATES["social_engagement"]
    return copy.deepcopy(template)


def resolve_content_type_config(content_type: str) -> dict[str, Any]:
    """Lookup CONTENT_TYPE_CONFIGS with a safe fallback (CPU-only)."""
    return CONTENT_TYPE_CONFIGS.get(content_type, CONTENT_TYPE_CONFIGS["image"])


def resolve_platform_config(platform: str) -> dict[str, Any]:
    """Lookup PLATFORM_CONFIGS with a safe fallback (CPU-only)."""
    return PLATFORM_CONFIGS.get(platform, PLATFORM_CONFIGS["instagram"])


def resolve_style_preset(style: str) -> dict[str, str]:
    """Lookup STYLE_PRESETS with a safe fallback (CPU-only)."""
    return STYLE_PRESETS.get(style, STYLE_PRESETS["bold"])


def get_negative_prompt(is_video: bool = False) -> str:
    """Return the appropriate baseline negative prompt (CPU-only)."""
    return VIDEO_NEGATIVE_PROMPT if is_video else IMAGE_NEGATIVE_PROMPT


def is_video_content_type(content_type: str) -> bool:
    """Heuristic: does this content type produce video? (CPU-only)."""
    cfg = resolve_content_type_config(content_type)
    return cfg.get("tool") in ("generate_video",)
