"""Local Generation Engine — Ollama API for text generation.

Content Agent text ke liye Ollama use karega (CPU, 24/7, 2-5 sec).
Image/Video ke liye sirf ChromeTool → Colab → FLUX/CogVideo.

Setup:
  1. Install Ollama: https://ollama.com/download
  2. ollama pull phi3.5:3.8b-mini
  3. Ollama API runs at http://localhost:11434
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

OLLAMA_BASE_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_TEXT_MODEL", "phi3.5:3.8b-mini")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "60"))

# ── Platform Image Sizes ──────────────────────────────────────────
# Content Agent LLM decide karega — platform ke hisaab se size

PLATFORM_SIZES = {
    # Social Media Posts
    "instagram_square": (1080, 1080),
    "instagram_portrait": (1080, 1350),
    "instagram_story": (1080, 1920),
    "instagram_reel": (1080, 1920),
    "facebook_post": (1200, 630),
    "facebook_story": (1080, 1920),
    "facebook_cover": (820, 312),
    "linkedin_post": (1200, 627),
    "linkedin_banner": (1584, 396),
    "linkedin_profile": (400, 400),
    "twitter_post": (1200, 675),
    "twitter_banner": (1500, 500),
    "twitter_profile": (400, 400),

    # Ads
    "google_display": (300, 250),
    "google_leaderboard": (728, 90),
    "facebook_ad": (1080, 1080),
    "facebook_ad_story": (1080, 1920),
    "instagram_ad_square": (1080, 1080),
    "instagram_ad_story": (1080, 1920),
    "linkedin_ad": (1200, 627),
    "tiktok_ad": (1080, 1920),
    "youtube_thumbnail": (1280, 720),
    "youtube_ad": (1920, 1080),

    # Website / Blog
    "blog_hero": (1200, 600),
    "blog_thumbnail": (800, 450),
    "og_image": (1200, 630),
    "email_header": (600, 200),

    # Posters / Print
    "poster_a4": (2480, 3508),
    "poster_square": (2000, 2000),
    "flyer": (1080, 1920),

    # General
    "square": (1024, 1024),
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "custom": (1024, 1024),
}


def get_platform_size(platform: str, width: int = 0, height: int = 0) -> tuple[int, int]:
    """Domain agent ki brief ke hisaab se platform size nikaalo."""
    key = platform.lower().replace(" ", "_").replace("-", "_")
    if key in PLATFORM_SIZES:
        return PLATFORM_SIZES[key]
    # Partial match
    for pkey, psize in PLATFORM_SIZES.items():
        if key in pkey or pkey in key:
            return psize
    if width and height:
        return (width, height)
    return (1024, 1024)


# ── Ollama API Client ─────────────────────────────────────────────

async def ollama_generate(
    prompt: str,
    model: str = OLLAMA_MODEL,
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """Ollama API se text generate karo (localhost:11434/api/generate)."""
    client = httpx.AsyncClient(timeout=OLLAMA_TIMEOUT)
    try:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system

        response = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
        response.raise_for_status()
        data = response.json()

        return {
            "status": "success",
            "text": data.get("response", "").strip(),
            "model": model,
            "tokens": data.get("eval_count", 0),
            "duration_ms": data.get("eval_duration", 0) // 1_000_000 if data.get("eval_duration") else 0,
        }
    except httpx.ConnectError:
        logger.warning("Ollama not running at %s", OLLAMA_BASE_URL)
        return {"status": "error", "error": f"Ollama not running at {OLLAMA_BASE_URL}. Start with: ollama serve"}
    except Exception as exc:
        logger.exception("Ollama generate failed")
        return {"status": "error", "error": str(exc)}
    finally:
        await client.aclose()


async def ollama_list_models() -> list[str]:
    """Available models check karo."""
    client = httpx.AsyncClient(timeout=10)
    try:
        resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []
    finally:
        await client.aclose()


# ── Platform-aware Content Generation ─────────────────────────────

CONTENT_SYSTEM_PROMPT = """You are the TAGS Content Agent text engine.
You write content for {platform} based on the brief given.

Brand Info:
{client_context}

Output ONLY the content, no explanations. Follow the format requested."""


async def generate_platform_content(
    brief: str,
    platform: str = "instagram",
    client_context: str = "",
    content_type: str = "caption",
) -> dict[str, Any]:
    """Domain agent ke brief ke hisaab se platform-specific text generate karo.

    Uses Ollama (local CPU) — 24/7 available, 2-10 sec response.
    """
    system = CONTENT_SYSTEM_PROMPT.format(
        platform=platform,
        client_context=client_context or "No brand info provided",
    )

    prompts = {
        "caption": f"Write a {platform} caption/post for:\n{brief}\n\nInclude relevant hashtags.",
        "ad_copy": f"Write an ad copy for {platform} about:\n{brief}\n\nInclude headline, body, CTA.",
        "blog": f"Write a blog post about:\n{brief}\n\nInclude title, introduction, 3 sections, conclusion.",
        "seo": f"Write SEO-optimized meta title and description for:\n{brief}\n\nReturn as JSON.",
        "script": f"Write a {platform} video script for:\n{brief}\n\nInclude hook, body, CTA.",
        "description": f"Write a product/service description for:\n{brief}",
        "hashtags": f"Generate 15 relevant hashtags for:\n{brief}\n\nComma separated.",
        "general": f"{brief}",
    }

    prompt = prompts.get(content_type, prompts["general"])
    return await ollama_generate(prompt, system=system)


# ── Tool─────────────────────────────

TEXT_GENERATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_text",
            "description": "Generate text content (captions, ad copy, blogs, hashtags) using local Ollama AI. 24/7 available, no internet needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "brief": {"type": "string", "description": "What to write about — from domain agent brief"},
                    "platform": {"type": "string", "description": "Target platform (instagram, facebook, linkedin, twitter, tiktok, blog, website, email)", "default": "instagram"},
                    "content_type": {"type": "string", "description": "Type of content", "enum": ["caption", "ad_copy", "blog", "seo", "script", "description", "hashtags", "general"], "default": "caption"},
                    "tone": {"type": "string", "description": "Tone of voice (professional, casual, funny, inspirational, urgent)", "default": "professional"},
                },
                "required": ["brief"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_platform_specs",
            "description": "Get image/video size specifications for any social platform. Content Agent yeh use karega FLUX prompt me size dene ke liye.",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "description": "Platform name e.g. instagram_story, facebook_ad, linkedin_post, youtube_thumbnail"},
                },
                "required": ["platform"],
            },
        },
    },
]


TEXT_TOOL_DISPATCH = {
    "generate_text": lambda args: (
        generate_platform_content(
            brief=args["brief"],
            platform=args.get("platform", "instagram"),
            content_type=args.get("content_type", "caption"),
            client_context=args.get("client_context", ""),
        )
        if isinstance(args, dict) else args
    ),
    "get_platform_specs": lambda args: {
        "status": "success",
        "platform": args.get("platform", "instagram"),
        "size": get_platform_size(args.get("platform", "instagram")),
        "all_sizes": PLATFORM_SIZES,
    },
}


async def execute_text_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """LLM tool dispatch — text generation tools."""
    handler = TEXT_TOOL_DISPATCH.get(tool_name)
    if not handler:
        return {"status": "error", "error": f"Unknown text tool: {tool_name}"}
    return await handler(args)
