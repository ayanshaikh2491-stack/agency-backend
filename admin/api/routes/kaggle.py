"""Kaggle GPU API Routes -- image and video generation.

Endpoints:
  POST /api/kaggle/image        -- Generate image with FLUX
  POST /api/kaggle/video        -- Generate video with CogVideoX
  POST /api/kaggle/ad-image     -- Generate ad creative image
  POST /api/kaggle/social-image -- Generate social media image
  POST /api/kaggle/hero-image   -- Generate hero/banner image
  POST /api/kaggle/video-ad     -- Generate video ad
  POST /api/kaggle/batch        -- Batch generate images
  GET  /api/kaggle/tools        -- List Kaggle tools
  GET  /api/kaggle/status       -- Check Kaggle setup status
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kaggle", tags=["kaggle-gpu"])


class ImageRequest(BaseModel):
    prompt: str
    width: int = 1024
    height: int = 1024
    steps: int = 20

class VideoRequest(BaseModel):
    prompt: str
    frames: int = 49

class AdImageRequest(BaseModel):
    product: str
    platform: str = "facebook"
    style: str = "professional"

class SocialImageRequest(BaseModel):
    topic: str
    platform: str = "instagram"

class HeroImageRequest(BaseModel):
    topic: str
    style: str = "modern"

class VideoAdRequest(BaseModel):
    product: str
    duration: str = "short"

class BatchRequest(BaseModel):
    topics: list[str]
    platform: str = "instagram"


@router.get("/tools")
async def list_tools():
    from admin.tools.kaggle_gpu import KAGGLE_TOOLS
    return {"tools": KAGGLE_TOOLS, "count": len(KAGGLE_TOOLS)}


@router.get("/status")
async def kaggle_status():
    from admin.tools.kaggle_gpu import _check_kaggle, _get_kaggle_creds
    cli_ok = _check_kaggle()
    creds = _get_kaggle_creds()
    return {
        "cli_installed": cli_ok,
        "credentials_found": bool(creds["username"]),
        "status": "ready" if (cli_ok and creds["username"]) else "setup_required",
        "gpu_quota": "30hrs/week free on Kaggle",
        "models": ["FLUX.1-schnell (images)", "CogVideoX-2b (videos)"],
    }


@router.post("/image")
async def generate_image(body: ImageRequest):
    from admin.tools.kaggle_gpu import generate_image_kaggle
    return {"success": True, "data": generate_image_kaggle(body.prompt, body.width, body.height, body.steps)}


@router.post("/video")
async def generate_video(body: VideoRequest):
    from admin.tools.kaggle_gpu import generate_video_kaggle
    return {"success": True, "data": generate_video_kaggle(body.prompt, body.frames)}


@router.post("/ad-image")
async def ad_image(body: AdImageRequest):
    from admin.tools.kaggle_gpu import generate_ad_image
    return {"success": True, "data": generate_ad_image(body.product, body.platform, body.style)}


@router.post("/social-image")
async def social_image(body: SocialImageRequest):
    from admin.tools.kaggle_gpu import generate_social_image
    return {"success": True, "data": generate_social_image(body.topic, body.platform)}


@router.post("/hero-image")
async def hero_image(body: HeroImageRequest):
    from admin.tools.kaggle_gpu import generate_hero_image
    return {"success": True, "data": generate_hero_image(body.topic, body.style)}


@router.post("/video-ad")
async def video_ad(body: VideoAdRequest):
    from admin.tools.kaggle_gpu import generate_video_ad
    return {"success": True, "data": generate_video_ad(body.product, body.duration)}


@router.post("/batch")
async def batch_generate(body: BatchRequest):
    from admin.tools.kaggle_gpu import batch_generate_images
    return {"success": True, "data": batch_generate_images(body.topics, body.platform)}
