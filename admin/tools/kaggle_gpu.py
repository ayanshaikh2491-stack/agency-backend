"""Kaggle GPU — On-demand visual content generation (v2).

GPU: T4x2 (enforced via accelerator metadata)
Image: FLUX.1-schnell (bfloat16, 4 steps, guidance 3.5, text-in-image)
Video: CogVideoX-2b (float16, on-demand only)

Flow:
  1. generate_visual(content_type, prompt, ...)
  2. Creates Kaggle notebook with FLUX/CogVideoX code
  3. Submits to T4x2 GPU via Kaggle API
  4. Polls until complete
  5. Downloads output to data/outputs/
  6. Returns local file path
  7. Memory cleanup after each task
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(os.getenv("TAGS_OUTPUT_DIR", "data/outputs"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# NOTEBOOK CODE TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

FLUX_CODE_T4 = '''# TAGS Content Agent -- FLUX.1-schnell Image Generation (T4x2 optimized)
# Runs on Kaggle T4x2 GPU -- bfloat16 natively, no PyTorch downgrade
import json, os, gc, sys, time, traceback

_start = time.time()
print("=== FLUX.1-schnell Image Generation (T4x2) ===")

import torch
from diffusers import FluxPipeline
from PIL import Image

print(f"PyTorch: {{torch.__version__}}")

if not torch.cuda.is_available():
    print(json.dumps({{"status": "error", "reason": "No CUDA GPU available"}}))
    sys.exit(1)

gpu_name = torch.cuda.get_device_name(0)
vram = torch.cuda.get_device_properties(0).total_mem / (1024**3)
print(f"GPU: {{gpu_name}} | VRAM: {{vram:.1f}}GB")

# T4x2 supports bfloat16 natively -- no PyTorch downgrade needed
DTYPE = torch.bfloat16
print(f"Using dtype: {{DTYPE}}")

# -- Load model --
HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HF_TOKEN_READ", "")
if HF_TOKEN:
    MODEL_NAME = "black-forest-labs/FLUX.1-schnell"
    print(f"Loading {{MODEL_NAME}}...")
    pipe = FluxPipeline.from_pretrained(MODEL_NAME, torch_dtype=DTYPE, token=HF_TOKEN)
else:
    MODEL_NAME = "stabilityai/stable-diffusion-xl-base-1.0"
    print(f"No HF_TOKEN - loading {{MODEL_NAME}} (free, no auth)...")
    from diffusers import DiffusionPipeline
    pipe = DiffusionPipeline.from_pretrained(MODEL_NAME, torch_dtype=DTYPE)

pipe.enable_model_cpu_offload()
print("Model loaded into VRAM")
sys.stdout.flush()

# -- Generate --
PROMPT = "{prompt}"
WIDTH = {width}
HEIGHT = {height}

print(f"Generating: {{PROMPT[:120]}}")
print(f"Params: {{WIDTH}}x{{HEIGHT}}, steps=4, guidance=3.5")
sys.stdout.flush()
try:
    image = pipe(
        PROMPT,
        width=WIDTH, height=HEIGHT,
        num_inference_steps=4,
        guidance_scale=3.5,
    ).images[0]
    elapsed = round(time.time() - _start, 1)
    out_path = f"output_{{int(time.time())}}.png"
    image.save(out_path)
    sz = os.path.getsize(out_path)
    print(f"Saved: {{out_path}} ({{sz}} bytes, {{elapsed}}s)")
    print(json.dumps({{
        "status": "success", "file": out_path, "size_bytes": sz,
        "width": WIDTH, "height": HEIGHT,
        "gpu": gpu_name, "dtype": str(DTYPE),
        "elapsed_seconds": elapsed,
    }}))
except Exception as e:
    print(f"GENERATION ERROR: {{e}}")
    traceback.print_exc()
    print(json.dumps({{"status": "error", "error": str(e)}}))

# -- Cleanup --
try:
    del pipe
    gc.collect()
    torch.cuda.empty_cache()
    print("VRAM cleaned up")
except:
    pass
'''

COGVIDEO_CODE_T4 = '''# TAGS Content Agent -- CogVideoX-2b Video Generation (T4x2 optimized)
# Runs on Kaggle T4x2 GPU -- bfloat16 natively, no PyTorch downgrade
import json, os, gc, sys, time, traceback

_start = time.time()
print("=== CogVideoX-2b Video Generation (T4x2) ===")

import torch
from diffusers import CogVideoXPipeline
import imageio

print(f"PyTorch: {{torch.__version__}}")

if not torch.cuda.is_available():
    print(json.dumps({{"status": "error", "reason": "No CUDA GPU available"}}))
    sys.exit(1)

gpu_name = torch.cuda.get_device_name(0)
vram = torch.cuda.get_device_properties(0).total_mem / (1024**3)
print(f"GPU: {{gpu_name}} | VRAM: {{vram:.1f}}GB")

# T4x2: bfloat16 supported natively
DTYPE = torch.bfloat16
print(f"Using dtype: {{DTYPE}}")

# -- Load CogVideoX-2b (ON-DEMAND) --
print("Loading THUDM/CogVideoX-2b (~5GB)...")
sys.stdout.flush()
try:
    pipe = CogVideoXPipeline.from_pretrained("THUDM/CogVideoX-2b", torch_dtype=DTYPE)
    pipe.enable_model_cpu_offload()
    print("Model loaded into VRAM")
    sys.stdout.flush()
except torch.cuda.OutOfMemoryError:
    print(json.dumps({{"status": "error", "error": "VRAM_OOM", "reason": "CUDA OOM during model load"}}))
    sys.exit(1)
except Exception as e:
    print(f"MODEL LOAD ERROR: {{e}}")
    traceback.print_exc()
    sys.exit(1)

# -- Generate video --
PROMPT = "{prompt}"
NUM_FRAMES = {frames}

print(f"Generating video: {{PROMPT[:120]}}")
print(f"Params: {{NUM_FRAMES}} frames, 50 steps, guidance 6.0")
sys.stdout.flush()
try:
    video = pipe(
        PROMPT,
        num_videos_per_prompt=1,
        num_inference_steps=50,
        num_frames=NUM_FRAMES,
        guidance_scale=6.0,
    ).videos[0]
    imageio.mimsave("output.mp4", video, fps=8)
    sz = os.path.getsize("output.mp4")
    elapsed = round(time.time() - _start, 1)
    print(json.dumps({{
        "status": "success", "file": "output.mp4", "size_bytes": sz,
        "frames": NUM_FRAMES, "duration_seconds": round(NUM_FRAMES / 8, 1),
        "gpu": gpu_name, "dtype": str(DTYPE), "elapsed_seconds": elapsed
    }}))
except torch.cuda.OutOfMemoryError:
    reduced_frames = max(16, NUM_FRAMES // 2)
    print(f"OOM at {{NUM_FRAMES}} frames - retrying with {{reduced_frames}}")
    torch.cuda.empty_cache()
    gc.collect()
    try:
        video = pipe(PROMPT, num_videos_per_prompt=1, num_inference_steps=50, num_frames=reduced_frames, guidance_scale=6.0).videos[0]
        imageio.mimsave("output.mp4", video, fps=8)
        sz = os.path.getsize("output.mp4")
        elapsed = round(time.time() - _start, 1)
        print(json.dumps({{"status": "success", "file": "output.mp4", "size_bytes": sz, "frames": reduced_frames, "elapsed_seconds": elapsed}}))
    except Exception as e2:
        print(json.dumps({{"status": "error", "error": "VRAM_OOM", "reason": str(e2)}}))
        sys.exit(1)
except Exception as e:
    print(f"VIDEO ERROR: {{e}}")
    traceback.print_exc()
    sys.exit(1)
finally:
    try:
        del pipe
        gc.collect()
        torch.cuda.empty_cache()
        print("VRAM cleaned up")
    except Exception:
        pass
'''

# Default templates (T4x2 optimized, no P100 bloat)
FLUX_CODE = FLUX_CODE_T4
COGVIDEO_CODE = COGVIDEO_CODE_T4

# P100 fallback templates (legacy, includes PyTorch downgrade)
FLUX_CODE_P100 = '''# TAGS Content Agent — FLUX.1-schnell Image Generation (T4x2)
# On-demand: model loaded ONLY during generation, cleaned after
import subprocess, sys, json, os, gc, traceback, time

_start = time.time()
print("=== FLUX.1-schnell Image Generation ===")
print(f"PyTorch: {{__import__('torch').__version__}}")

import torch
from diffusers import FluxPipeline
from PIL import Image

# ── GPU validation (must be sm_70+ for bfloat16) ──
if not torch.cuda.is_available():
    print(json.dumps({{"status": "error", "reason": "No CUDA GPU — T4x2 required"}}))
    sys.exit(1)

cap = torch.cuda.get_device_capability(0)
gpu_name = torch.cuda.get_device_name(0)
vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
print(f"GPU: {{gpu_name}} | sm_{{cap[0]}}{{cap[1]}} | {{vram:.1f}}GB VRAM")

if cap[0] < 7:
    print(json.dumps({{"status": "error", "reason": f"GPU sm_{{cap[0]}}{{cap[1]}} too old — need sm_70+ (T4)"}}))
    sys.exit(1)

DTYPE = torch.bfloat16
print(f"Using dtype: {{DTYPE}}")

# ── Load FLUX.1-schnell (ON-DEMAND — loaded only for this task) ──
HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HF_TOKEN_READ", "")
print(f"HF_TOKEN: {{'set' if HF_TOKEN else 'not set — using SDXL fallback'}}")

if HF_TOKEN:
    MODEL_NAME = "black-forest-labs/FLUX.1-schnell"
    print(f"Loading {{MODEL_NAME}}...")
else:
    MODEL_NAME = "stabilityai/stable-diffusion-xl-base-1.0"
    print(f"No HF_TOKEN — loading {{MODEL_NAME}} (free, no auth)...")

sys.stdout.flush()
try:
    if HF_TOKEN:
        pipe = FluxPipeline.from_pretrained(
            MODEL_NAME, torch_dtype=DTYPE, token=HF_TOKEN,
        )
    else:
        from diffusers import DiffusionPipeline
        pipe = DiffusionPipeline.from_pretrained(
            MODEL_NAME, torch_dtype=DTYPE,
        )
    pipe.enable_model_cpu_offload()
    print("Model loaded into VRAM")
    sys.stdout.flush()
except Exception as e:
    print(f"MODEL LOAD ERROR: {{e}}")
    traceback.print_exc()
    sys.exit(1)

# ── Generate ──
PROMPT = "{prompt}"
WIDTH = {width}
HEIGHT = {height}

# FLUX.1-schnell: optimized for 4 steps, guidance 3.5
# Supports text-in-image for ad banners/posters
print(f"Generating: {{PROMPT[:120]}}")
print(f"Params: {{WIDTH}}x{{HEIGHT}}, steps=4, guidance=3.5")
sys.stdout.flush()
try:
    image = pipe(
        PROMPT,
        width=WIDTH,
        height=HEIGHT,
        num_inference_steps=4,
        guidance_scale=3.5,
    ).images[0]
    image.save("output.png")
    sz = os.path.getsize("output.png")
    elapsed = round(time.time() - _start, 1)
    print(json.dumps({{
        "status": "success",
        "file": "output.png",
        "size_bytes": sz,
        "gpu": gpu_name,
        "sm": f"sm_{{cap[0]}}{{cap[1]}}",
        "dtype": str(DTYPE),
        "model": "{model}",
        "steps": 4,
        "guidance": 3.5,
        "elapsed_seconds": elapsed
    }}))
except torch.cuda.OutOfMemoryError:
    print(json.dumps({{"status": "error", "error": "VRAM_OOM", "reason": "CUDA out of memory - try smaller resolution", "gpu": gpu_name, "vram_gb": round(vram, 1)}}))
    sys.exit(1)
except Exception as e:
    print(f"GENERATION ERROR: {{e}}")
    traceback.print_exc()
    sys.exit(1)
finally:
    # ── MEMORY CLEANUP (on-demand: free VRAM after task) ──
    try:
        del pipe
        gc.collect()
        torch.cuda.empty_cache()
        print("VRAM cleaned up")
    except Exception:
        pass
'''


COGVIDEO_CODE_P100 = '''# TAGS Content Agent — CogVideoX-2b Video Generation (T4x2)
# Triggered ONLY when video prompt is explicitly provided
# On-demand: model loaded ONLY during generation, cleaned after
import subprocess, sys, json, os, gc, traceback, time

_start = time.time()
print("=== CogVideoX-2b Video Generation ===")
print(f"PyTorch: {{__import__('torch').__version__}}")

import torch
from diffusers import CogVideoXPipeline
import imageio

# ── GPU validation (needs sm_70+ for float16) ──
if not torch.cuda.is_available():
    print(json.dumps({{"status": "error", "reason": "No CUDA GPU — T4x2 required"}}))
    sys.exit(1)

cap = torch.cuda.get_device_capability(0)
gpu_name = torch.cuda.get_device_name(0)
vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
print(f"GPU: {{gpu_name}} | sm_{{cap[0]}}{{cap[1]}} | {{vram:.1f}}GB VRAM")

if cap[0] < 7:
    print(json.dumps({{"status": "error", "reason": f"GPU sm_{{cap[0]}}{{cap[1]}} too old — need sm_70+ (T4)"}}))
    sys.exit(1)

DTYPE = torch.bfloat16 if cap[0] >= 8 else torch.float16
print(f"Using dtype: {{DTYPE}}")

# ── Load CogVideoX-2b (ON-DEMAND — loaded only for this task) ──
print("Loading THUDM/CogVideoX-2b (~5GB)...")
sys.stdout.flush()
try:
    pipe = CogVideoXPipeline.from_pretrained("THUDM/CogVideoX-2b", torch_dtype=DTYPE)
    pipe.enable_model_cpu_offload()
    print("Model loaded into VRAM")
    sys.stdout.flush()
except torch.cuda.OutOfMemoryError:
    print(json.dumps({{"status": "error", "error": "VRAM_OOM", "reason": "CUDA OOM during model load - CogVideoX needs ~8GB"}}))
    sys.exit(1)
except Exception as e:
    print(f"MODEL LOAD ERROR: {{e}}")
    traceback.print_exc()
    sys.exit(1)

# ── Generate video ──
PROMPT = "{prompt}"
NUM_FRAMES = {frames}

print(f"Generating video: {{PROMPT[:120]}}")
print(f"Params: {{NUM_FRAMES}} frames, 50 steps, guidance 6.0")
sys.stdout.flush()
try:
    video = pipe(
        PROMPT,
        num_videos_per_prompt=1,
        num_inference_steps=50,
        num_frames=NUM_FRAMES,
        guidance_scale=6.0,
    ).videos[0]
    imageio.mimsave("output.mp4", video, fps=8)
    sz = os.path.getsize("output.mp4")
    elapsed = round(time.time() - _start, 1)
    print(json.dumps({{
        "status": "success",
        "file": "output.mp4",
        "size_bytes": sz,
        "frames": NUM_FRAMES,
        "duration_seconds": round(NUM_FRAMES / 8, 1),
        "gpu": gpu_name,
        "sm": f"sm_{{cap[0]}}{{cap[1]}}",
        "dtype": str(DTYPE),
        "elapsed_seconds": elapsed
    }}))
except torch.cuda.OutOfMemoryError:
    reduced_frames = max(16, NUM_FRAMES // 2)
    print(f"OOM at {{NUM_FRAMES}} frames - retrying with {{reduced_frames}} frames")
    torch.cuda.empty_cache()
    gc.collect()
    try:
        video = pipe(PROMPT, num_videos_per_prompt=1, num_inference_steps=50, num_frames=reduced_frames, guidance_scale=6.0).videos[0]
        imageio.mimsave("output.mp4", video, fps=8)
        sz = os.path.getsize("output.mp4")
        elapsed = round(time.time() - _start, 1)
        print(json.dumps({{"status": "success", "file": "output.mp4", "size_bytes": sz, "frames": reduced_frames, "duration_seconds": round(reduced_frames / 8, 1), "gpu": gpu_name, "sm": f"sm_{{cap[0]}}{{cap[1]}}", "dtype": str(DTYPE), "elapsed_seconds": elapsed, "warning": f"Reduced from {{NUM_FRAMES}} to {{reduced_frames}} frames due to VRAM limit"}}))
    except Exception as e2:
        print(json.dumps({{"status": "error", "error": "VRAM_OOM", "reason": f"OOM even at {{reduced_frames}} frames: {{e2}}", "gpu": gpu_name, "vram_gb": round(vram, 1)}}))
        sys.exit(1)
except Exception as e:
    print(f"VIDEO ERROR: {{e}}")
    traceback.print_exc()
    sys.exit(1)
finally:
    # ── MEMORY CLEANUP (on-demand: free VRAM after task) ──
    try:
        del pipe
        gc.collect()
        torch.cuda.empty_cache()
        print("VRAM cleaned up")
    except Exception:
        pass
'''


# ═══════════════════════════════════════════════════════════════════════════════
# PLATFORM SIZES
# ═══════════════════════════════════════════════════════════════════════════════

PLATFORM_SIZES: dict[str, tuple[int, int]] = {
    "instagram_square": (1080, 1080),
    "instagram_portrait": (1080, 1350),
    "instagram_story": (1080, 1920),
    "instagram_reel": (1080, 1920),
    "facebook_post": (1200, 630),
    "facebook_ad": (1080, 1080),
    "facebook_story": (1080, 1920),
    "linkedin_post": (1200, 627),
    "twitter_post": (1200, 675),
    "youtube_thumbnail": (1280, 720),
    "blog_hero": (1200, 600),
    "og_image": (1200, 630),
    "poster_a4": (2480, 3508),
    "ad_banner": (1200, 628),
    "ad_poster": (1080, 1350),
    "square": (1024, 1024),
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
}


def get_platform_size(platform: str) -> tuple[int, int]:
    """Platform name se size nikaalo."""
    key = platform.lower().replace(" ", "_").replace("-", "_")
    if key in PLATFORM_SIZES:
        return PLATFORM_SIZES[key]
    for pkey, psize in PLATFORM_SIZES.items():
        if key in pkey or pkey in key:
            return psize
    return (1024, 1024)


# ═══════════════════════════════════════════════════════════════════════════════
# KAGGLE CLI HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _check_kaggle() -> bool:
    """Kaggle CLI installed hai ya nahi."""
    for cmd in [["kaggle", "--version"], [sys.executable, "-m", "kaggle", "--version"]]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


def _get_username() -> str:
    """Kaggle username from env or kaggle.json."""
    username = os.getenv("KAGGLE_USERNAME", "")
    if username:
        return username
    data = _read_kaggle_json()
    return data.get("username", "")


def _run_kaggle(args: list[str], timeout: int = 30) -> dict[str, Any]:
    """Kaggle CLI command run karo."""
    for cmd in [["kaggle"] + args, [sys.executable, "-m", "kaggle"] + args]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return {
                "success": r.returncode == 0,
                "stdout": r.stdout.strip(),
                "stderr": r.stderr.strip(),
            }
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "timeout"}
    return {"success": False, "error": "kaggle CLI not found"}


# ═══════════════════════════════════════════════════════════════════════════════
# NOTEBOOK BUILD + SUBMIT
# ═══════════════════════════════════════════════════════════════════════════════

def _source_to_lines(source: str) -> list[str]:
    """Convert source to Jupyter notebook cell format."""
    lines = source.split("\n")
    return [line + "\n" for line in lines[:-1]] + [lines[-1]]


def _build_notebook(code_source: str) -> dict[str, Any]:
    """Build Kaggle-format notebook JSON with T4x2 GPU enforcement."""
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kaggle": {
                "gpu_type": "nvidia-tesla-t4-x2",
                "dataSources": [],
                "isGpuEnabled": "true",
                "isInternetEnabled": "true",
                "language": "python",
            },
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
        },
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": _source_to_lines(code_source),
            }
        ],
    }


def _build_metadata(username: str, title: str) -> dict[str, Any]:
    """Build kernel-metadata.json for Kaggle CLI — T4x2 GPU enforced."""
    unique = uuid.uuid4().hex[:8]
    full_title = f"TAGS {title} {unique}"
    slug = re.sub(r"[^a-z0-9\s-]", "", full_title.lower())
    slug = re.sub(r"[\s]+", "-", slug).strip("-")
    kernel_id = f"{username}/{slug}"

    return {
        "id": kernel_id,
        "title": full_title,
        "code_file": "notebook.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": "true",
        "enable_gpu": "true",
        "enable_internet": "true",
        "kernel_sources": [],
        "dataset_sources": [],
        "gpu_type": "nvidia-tesla-t4-x2",
    }


def _submit_notebook(code_source: str, title: str) -> dict[str, Any]:
    """Notebook banao aur Kaggle pe submit karo. Returns kernel_slug."""
    username = _get_username()
    if not username:
        return {"status": "error", "error": "Kaggle credentials not found. Set KAGGLE_USERNAME in env or ~/.kaggle/kaggle.json"}

    kernel_dir = tempfile.mkdtemp(prefix=f"kaggle_{title}_")

    try:
        notebook = _build_notebook(code_source)
        with open(os.path.join(kernel_dir, "notebook.ipynb"), "w") as f:
            json.dump(notebook, f, indent=2)

        metadata = _build_metadata(username, title)
        with open(os.path.join(kernel_dir, "kernel-metadata.json"), "w") as f:
            json.dump(metadata, f, indent=2)

        kernel_slug = metadata["id"]

        result = _run_kaggle(["kernels", "push", "-p", kernel_dir], timeout=60)
        if result["success"]:
            return {
                "status": "submitted",
                "kernel_slug": kernel_slug,
                "url": f"https://www.kaggle.com/code/{kernel_slug}",
            }
        else:
            return {
                "status": "error",
                "error": result.get("stderr", result.get("error", "Submit failed")),
            }
    finally:
        shutil.rmtree(kernel_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# POLL + DOWNLOAD
# ═══════════════════════════════════════════════════════════════════════════════

def poll_status(kernel_slug: str) -> str:
    """Notebook status check karo.

    Returns: queued | running | complete | error | cancelled | unknown
    """
    result = _run_kaggle(["kernels", "status", kernel_slug])
    if not result["success"]:
        logger.warning("kaggle kernels status failed for %s: %s", kernel_slug, result.get("stderr"))
        return "error"

    raw = result["stdout"].strip()
    output = raw.lower()

    # Parse status from Kaggle output (format: "Status: <status>")
    status_match = _re.search(r"status:\s*(\w+)", output)
    status_str = status_match.group(1) if status_match else output

    if "complete" in status_str or "success" in status_str:
        return "complete"
    elif "running" in status_str or "loading" in status_str:
        return "running"
    elif "queued" in status_str or "waiting" in status_str or "pending" in status_str:
        return "queued"
    elif "error" in status_str or "fail" in status_str:
        return "error"
    elif "cancel" in status_str:
        return "cancelled"
    else:
        logger.info("Unknown status for %s: %s", kernel_slug, raw[:200])
        return "unknown"


def download_output(kernel_slug: str, dest_dir: str | Path | None = None) -> dict[str, Any]:
    """Completed notebook se output files download karo. Includes retry + file verification."""
    if dest_dir is None:
        dest_dir = _OUTPUT_DIR / kernel_slug.split("/")[-1]
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    _VALID_EXT = {".png", ".jpg", ".jpeg", ".mp4", ".webm", ".gif", ".webp"}

    for attempt in range(1, 3):
        result = _run_kaggle(
            ["kernels", "output", kernel_slug, "-p", str(dest_dir)],
            timeout=120,
        )
        if result["success"]:
            files = [f for f in dest_dir.iterdir() if f.is_file()]
            valid = [f.name for f in files if f.suffix.lower() in _VALID_EXT and f.stat().st_size > 1024]
            if valid:
                return {"status": "downloaded", "dir": str(dest_dir), "files": valid}
            # Files exist but invalid — retry once
            if files and attempt == 1:
                for f in files:
                    f.unlink(missing_ok=True)
                time.sleep(5)
                continue
        if attempt == 1:
            time.sleep(5)

    return {"status": "error", "error": result.get("stderr", "Download failed after 2 attempts")}


def wait_for_completion(
    kernel_slug: str,
    timeout: int = 900,
    poll_interval: int = 30,
) -> dict[str, Any]:
    """Notebook complete hone tak wait karo.

    Polling: every 30 seconds (GPU queue slow hai, 15s bahut fast hai)
    Timeout: 15 minutes max (900s) — GPU quota ke hisaab se
    Statuses: queued -> running -> complete | error | cancelled

    Returns:
        {
            status: "complete" | "error" | "cancelled" | "timeout",
            elapsed_seconds: int,
            error: str (only on error/cancelled),
        }
    """
    start = time.time()
    last_status = ""
    poll_count = 0

    logger.info("Polling started: %s (interval=%ds, timeout=%ds)", kernel_slug, poll_interval, timeout)

    while time.time() - start < timeout:
        status = poll_status(kernel_slug)
        elapsed = int(time.time() - start)
        poll_count += 1

        if status != last_status:
            logger.info("[%s] Status: %s -> %s (%ds elapsed, poll #%d)",
                        kernel_slug, last_status or "start", status, elapsed, poll_count)
            last_status = status

        if status == "complete":
            logger.info("[%s] COMPLETE in %ds (%d polls)", kernel_slug, elapsed, poll_count)
            return {"status": "complete", "elapsed_seconds": elapsed}

        elif status == "error":
            return {
                "status": "error",
                "elapsed_seconds": elapsed,
                "error": "Notebook failed on GPU — check Kaggle logs",
                "kaggle_url": f"https://www.kaggle.com/code/{kernel_slug}",
            }

        elif status == "cancelled":
            return {
                "status": "cancelled",
                "elapsed_seconds": elapsed,
                "error": "Notebook was cancelled on Kaggle",
                "kaggle_url": f"https://www.kaggle.com/code/{kernel_slug}",
            }

        # Log progress every 5 polls (~2.5 min)
        if poll_count % 5 == 0:
            logger.info("[%s] Still %s... (%ds elapsed, poll #%d)",
                        kernel_slug, status, elapsed, poll_count)

        time.sleep(poll_interval)

    logger.warning("[%s] TIMEOUT after %ds (%d polls)", kernel_slug, timeout, poll_count)
    return {
        "status": "timeout",
        "elapsed_seconds": timeout,
        "error": f"GPU generation did not complete within {timeout}s",
        "kaggle_url": f"https://www.kaggle.com/code/{kernel_slug}",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def generate_visual(
    content_type: str,
    prompt: str,
    platform: str = "instagram",
    width: int = 0,
    height: int = 0,
    steps: int = 4,
    frames: int = 49,
    timeout: int = 900,
) -> dict[str, Any]:
    """Visual content generate karo — on-demand T4x2 GPU.

    FULL PIPELINE:
      1. Build notebook code (FLUX for images, CogVideoX for video)
      2. Push to Kaggle via `kaggle kernels push`
      3. Poll every 30s until complete (max 15 min)
      4. Auto-download output when done
      5. Return clean JSON with file path

    Args:
        content_type: "image" or "video"
        prompt: AI prompt for generation (supports text-in-image for ads)
        platform: Target platform (for size auto-detection)
        width/height: Override platform size (0 = auto from platform)
        steps: Ignored — FLUX.1-schnell always uses 4 steps
        frames: CogVideoX frames (49=~6s, 60=~7.5s, 81=~10s)
        timeout: Max wait time in seconds (default 900 = 15 min)

    Returns:
        Success:
            {"status": "success", "file_path": "data/outputs/.../output.png",
             "content_type": "image", "kernel_slug": "...", "kaggle_url": "...",
             "elapsed_seconds": 123}
        Error:
            {"status": "error", "error": "...", "kaggle_url": "..."}
    """
    if not _check_kaggle():
        return {"status": "error", "error": "Kaggle CLI not installed. Run: pip install kaggle"}

    # Platform size auto-detect
    if width == 0 or height == 0:
        pw, ph = get_platform_size(platform)
        width = width or pw
        height = height or ph

    # Build code
    if content_type == "video":
        code = COGVIDEO_CODE.format(prompt=prompt.replace('"', '\\"'), frames=frames)
        title = "cogvideo"
        estimated = f"~{frames // 8} sec video"
    else:
        code = FLUX_CODE.format(
            prompt=prompt.replace('"', '\\"'),
            width=width, height=height,
            model="FLUX.1-schnell",
        )
        title = "flux-schnell"
        estimated = f"{width}x{height}"

    logger.info("Generating %s: %s (%s)", content_type, prompt[:80], estimated)

    # ── STEP 1: Push notebook ──
    submit = _submit_notebook(code, title)
    if submit["status"] == "error":
        return {"status": "error", "error": submit["error"]}

    kernel_slug = submit["kernel_slug"]
    kaggle_url = submit["url"]
    logger.info("Notebook pushed: %s — starting poll loop...", kernel_slug)

    # ── STEP 2: Poll until complete ──
    wait_result = wait_for_completion(kernel_slug, timeout=timeout)

    if wait_result["status"] != "complete":
        return {
            "status": wait_result["status"],
            "error": wait_result.get("error", "GPU generation did not complete"),
            "kernel_slug": kernel_slug,
            "kaggle_url": kaggle_url,
            "elapsed_seconds": wait_result.get("elapsed_seconds", 0),
        }

    # ── STEP 3: Auto-download output ──
    download = download_output(kernel_slug)
    if download["status"] == "error":
        return {
            "status": "error",
            "error": f"Output download failed: {download.get('error', 'unknown')}",
            "kernel_slug": kernel_slug,
            "kaggle_url": kaggle_url,
        }

    # ── STEP 4: Find output file ──
    output_files = download.get("files", [])
    output_file = ""
    for f in output_files:
        if f.endswith((".png", ".jpg", ".jpeg", ".mp4", ".webm")):
            output_file = os.path.join(download["dir"], f)
            break

    # ── Clean JSON return ──
    return {
        "status": "success",
        "file_path": output_file,
        "content_type": content_type,
        "kernel_slug": kernel_slug,
        "kaggle_url": kaggle_url,
        "elapsed_seconds": wait_result.get("elapsed_seconds", 0),
        "output_dir": download["dir"],
        "all_files": output_files,
        "prompt": prompt,
        "platform": platform,
        "size": f"{width}x{height}" if content_type == "image" else f"{frames} frames",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS (for API routes + direct use)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_image(prompt: str, platform: str = "instagram", width: int = 0, height: int = 0, steps: int = 4) -> dict[str, Any]:
    """Image generate karo — FLUX.1-schnell (4 steps, text-in-image support)."""
    return generate_visual("image", prompt, platform, width, height, steps=steps)


def generate_video(prompt: str, platform: str = "instagram", frames: int = 49) -> dict[str, Any]:
    """Video generate karo — CogVideoX-2b (triggered only when video prompt provided)."""
    return generate_visual("video", prompt, platform, frames=frames)


def generate_ad_image(product: str, platform: str = "facebook", style: str = "professional") -> dict[str, Any]:
    """Ad creative image generate karo — supports text-in-image for banners/posters."""
    style_desc = {
        "professional": "clean, professional, modern, corporate",
        "bold": "bold, vibrant, eye-catching, dynamic",
        "minimal": "minimalist, clean, white space, elegant",
        "creative": "creative, artistic, unique, memorable",
    }.get(style, "professional, modern")
    prompt = f"A professional {style} advertisement for {product}, {style_desc}, high quality marketing material"
    return generate_image(prompt, platform)


def generate_social_image(topic: str, platform: str = "instagram") -> dict[str, Any]:
    """Social media post image generate karo."""
    prompt = f"A beautiful, engaging social media post about {topic}, modern design, vibrant colors, professional quality"
    return generate_image(prompt, platform)


def generate_hero_image(topic: str, style: str = "modern") -> dict[str, Any]:
    """Hero/banner image generate karo."""
    prompt = f"A stunning hero banner image about {topic}, {style} design, wide format, professional quality"
    return generate_image(prompt, "blog_hero", width=1920, height=1080)


def check_status(kernel_slug: str) -> str:
    """Notebook status check karo."""
    return poll_status(kernel_slug)


# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK + BATCH + SMART RETRY
# ═══════════════════════════════════════════════════════════════════════════════

def generate_with_fallback(
    prompt: str,
    platform: str = "instagram",
    width: int = 0,
    height: int = 0,
    steps: int = 4,
    content_type: str = "image",
    frames: int = 49,
) -> dict[str, Any]:
    """Try generate_visual; on failure simplify prompt and retry (max 3 attempts)."""
    _ADJECTIVE_WORDS = {
        "beautiful", "stunning", "amazing", "gorgeous", "elegant",
        "vibrant", "mesmerizing", "breathtaking", "captivating",
        "exquisite", "luminous", "radiant", "glorious", "magnificent",
        "fantastic", "brilliant", "wonderful", "spectacular", "fabulous",
        "luxurious", "premium", "high-quality", "ultra", "professional",
    }

    def _simplify_prompt(original: str) -> str:
        words = original.split()
        simplified = [w for w in words if w.lower().strip(",.!?;:") not in _ADJECTIVE_WORDS]
        return " ".join(simplified) if simplified else original

    last_error = ""

    for attempt in range(1, 4):
        current_prompt = prompt
        current_type = content_type

        if attempt == 2:
            current_prompt = _simplify_prompt(prompt)
            logger.info("Fallback attempt 2: simplified prompt '%s'", current_prompt[:80])
        elif attempt == 3:
            current_type = "image"
            current_prompt = _simplify_prompt(prompt)
            width = width or 1024
            height = height or 1024
            logger.info("Fallback attempt 3: force image mode with simplified prompt")

        result = generate_visual(
            content_type=current_type,
            prompt=current_prompt,
            platform=platform,
            width=width,
            height=height,
            steps=steps,
            frames=frames,
        )

        if result.get("status") == "success":
            result["attempts"] = attempt
            if attempt > 1:
                result["fallback_used"] = True
                result["fallback_reason"] = f"Attempt {attempt}: {'simplified prompt' if attempt == 2 else 'forced image mode'}"
            return result

        last_error = result.get("error", f"Attempt {attempt} failed")
        logger.warning("generate_with_fallback attempt %d failed: %s", attempt, last_error)

        # Quota exhausted — early exit (no point retrying)
        _quota_kw = {"quota", "limit exceeded", "too many requests", "rate limit", "429"}
        if any(kw in last_error.lower() for kw in _quota_kw):
            return {"status": "error", "error": f"Kaggle GPU quota exhausted: {last_error}", "attempts": attempt, "quota_exhausted": True}

        # Exponential backoff between attempts
        if attempt < 3:
            _backoff = [5, 15, 30]
            time.sleep(_backoff[min(attempt - 1, 2)])

    return {
        "status": "error",
        "error": f"All 3 attempts failed. Last error: {last_error}",
        "attempts": 3,
        "fallback_used": True,
    }


def batch_generate(prompts: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate visuals for a list of prompt dicts sequentially."""
    results: list[dict[str, Any]] = []
    success_count = 0
    failure_count = 0

    for i, item in enumerate(prompts):
        prompt_text = item.get("prompt", "")
        if not prompt_text:
            results.append({"index": i, "status": "skipped", "error": "Empty prompt"})
            failure_count += 1
            continue

        logger.info("batch_generate [%d/%d]: %s", i + 1, len(prompts), prompt_text[:60])

        result = generate_with_fallback(
            prompt=prompt_text,
            platform=item.get("platform", "instagram"),
            width=item.get("width", 0),
            height=item.get("height", 0),
            steps=item.get("steps", 4),
            content_type=item.get("content_type", "image"),
            frames=item.get("frames", 49),
        )

        result["index"] = i
        results.append(result)

        if result.get("status") == "success":
            success_count += 1
        else:
            failure_count += 1

        if i < len(prompts) - 1:
            time.sleep(5)

    return {
        "status": "completed",
        "results": results,
        "success_count": success_count,
        "failure_count": failure_count,
        "total": len(prompts),
    }

# ═══════════════════════════════════════════════════════════════════════════════
# BACKWARD-COMPAT ALIASES (for kaggle routes + tool registry)
# ═══════════════════════════════════════════════════════════════════════════════

def _read_kaggle_json() -> dict[str, str]:
    """Read kaggle.json, handling escaped quotes gracefully."""
    kaggle_json = os.path.expanduser("~/.kaggle/kaggle.json")
    if not os.path.exists(kaggle_json):
        return {}
    with open(kaggle_json) as f:
        raw = f.read().strip()
    raw = raw.replace('\"', '"')
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _get_kaggle_creds() -> dict[str, str]:
    """Kaggle credentials dict return karo."""
    username = _get_username()
    key = os.getenv("KAGGLE_KEY", "")
    if not key:
        data = _read_kaggle_json()
        key = data.get("key", "")
    return {"username": username, "key": key}


def generate_image_kaggle(prompt: str, width: int = 1024, height: int = 1024, steps: int = 4) -> dict[str, Any]:
    """Alias for generate_image (kaggle routes backward compat)."""
    return generate_image(prompt=prompt, platform="custom", width=width, height=height, steps=steps)


def generate_video_kaggle(prompt: str, frames: int = 49) -> dict[str, Any]:
    """Alias for generate_video (kaggle routes backward compat)."""
    return generate_video(prompt=prompt, frames=frames)


def generate_video_ad(product: str, duration: str = "short") -> dict[str, Any]:
    """Video ad generate karo -- CogVideoX on-demand."""
    duration_frames = {"short": 49, "medium": 81, "long": 121}
    frames = duration_frames.get(duration, 49)
    prompt = (
        f"A professional video advertisement for {product}, "
        f"cinematic, high quality, marketing material"
    )
    return generate_video(prompt=prompt, frames=frames)


def batch_generate_images(prompts: list[str], platform: str = "instagram") -> dict[str, Any]:
    """Batch generate images from prompt strings (backward compat)."""
    items = [{"prompt": p, "platform": platform} for p in prompts]
    return batch_generate(items)


KAGGLE_TOOLS: list[dict[str, Any]] = [
    {"name": "generate_image", "description": "FLUX.1-schnell image generation (4 steps, T4x2 GPU)"},
    {"name": "generate_video", "description": "CogVideoX-2b video generation (T4x2 GPU)"},
    {"name": "generate_ad_image", "description": "Ad creative image with text-in-image support"},
    {"name": "generate_social_image", "description": "Social media post image"},
    {"name": "generate_hero_image", "description": "Hero/banner image"},
    {"name": "generate_video_ad", "description": "Video advertisement"},
    {"name": "batch_generate", "description": "Batch generate multiple visuals"},
    {"name": "generate_with_fallback", "description": "Generate with auto-retry on failure"},
]
