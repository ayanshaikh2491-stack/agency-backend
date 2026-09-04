"""Together.ai FREE FLUX API — no credit card, API key milta hai free mein.

Sign up: https://api.together.xyz (GitHub/Google login)
Free: FLUX.1-schnell endpoint (3 months unlimited)
"""
import base64
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

_OUTPUT_DIR = Path(os.getenv("TAGS_OUTPUT_DIR", "data/outputs"))
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PLATFORM_SIZES = {
    "instagram": (1080, 1080),
    "facebook": (1200, 630),
    "linkedin": (1200, 627),
    "twitter": (1200, 675),
    "youtube": (1280, 720),
    "tiktok": (1080, 1920),
    "pinterest": (1000, 1500),
    "blog_hero": (1200, 630),
    "google_display": (1080, 1080),
    "custom": (1024, 1024),
}


def get_platform_size(platform: str) -> tuple[int, int]:
    return PLATFORM_SIZES.get(platform, (1024, 1024))


def _get_api_key() -> str:
    return os.getenv("TOGETHER_API_KEY", "")


def generate_image(
    prompt: str,
    platform: str = "instagram",
    width: int = 0,
    height: int = 0,
    steps: int = 4,
) -> dict[str, Any]:
    """Together.ai FLUX.1-schnell se image generate karo — FREE."""
    api_key = _get_api_key()
    if not api_key:
        return {"status": "error", "error": "TOGETHER_API_KEY not set. Get free key at https://api.together.xyz"}

    if width == 0 or height == 0:
        pw, ph = get_platform_size(platform)
        width = width or pw
        height = height or ph

    start = time.time()

    payload = json.dumps({
        "model": "black-forest-labs/FLUX.1-schnell-free",
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": max(steps, 4),
        "n": 1,
        "response_format": "b64_json",
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.together.xyz/v1/images/generations",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"status": "error", "error": f"API error {e.code}: {body[:300]}"}
    except Exception as e:
        return {"status": "error", "error": f"Request failed: {e}"}

    images = data.get("data", [])
    if not images:
        return {"status": "error", "error": "No images returned from Together.ai"}

    b64_data = images[0].get("b64_json", "")
    if not b64_data:
        # Try URL format
        img_url = images[0].get("url", "")
        if img_url:
            out_path = _OUTPUT_DIR / f"together_{int(time.time())}.png"
            urllib.request.urlretrieve(img_url, str(out_path))
            return {
                "status": "success",
                "file_path": str(out_path),
                "content_type": "image",
                "source": "together.ai",
                "model": "FLUX.1-schnell",
                "size": f"{width}x{height}",
                "elapsed_seconds": round(time.time() - start, 1),
            }
        return {"status": "error", "error": "No image data in response"}

    # Save base64 image
    img_bytes = base64.b64decode(b64_data)
    out_path = _OUTPUT_DIR / f"together_{int(time.time())}.png"
    with open(out_path, "wb") as f:
        f.write(img_bytes)

    return {
        "status": "success",
        "file_path": str(out_path),
        "content_type": "image",
        "source": "together.ai",
        "model": "FLUX.1-schnell",
        "size": f"{width}x{height}",
        "elapsed_seconds": round(time.time() - start, 1),
        "file_size": len(img_bytes),
    }


def generate_video(
    prompt: str,
    platform: str = "instagram",
    frames: int = 49,
) -> dict[str, Any]:
    """Video — Together.ai pe FLUX video available nahi, error return."""
    return {
        "status": "error",
        "error": "Together.ai free tier pe video nahi hai. Kaggle GPU use karo for video.",
    }


def generate_ad_image(product: str, platform: str = "facebook", style: str = "professional") -> dict[str, Any]:
    prompt = f"A professional {style} advertisement for {product}, high quality marketing material, studio lighting"
    return generate_image(prompt=prompt, platform=platform)


def generate_social_image(topic: str, platform: str = "instagram") -> dict[str, Any]:
    prompt = f"A beautiful, engaging social media post about {topic}, modern design, vibrant colors, professional quality"
    return generate_image(prompt=prompt, platform=platform)


def generate_hero_image(topic: str, style: str = "modern") -> dict[str, Any]:
    prompt = f"A stunning {style} hero banner image about {topic}, wide format, professional web design quality"
    return generate_image(prompt=prompt, platform="blog_hero")
