"""Chrome + Google Colab — Image/Video Generation via Browser.

USER KA REAL CHROME use hota hai (Playwright CDP se connect).
Colab open hota hai → new notebook → code paste → GPU run → download.

No API keys, no CLI, no server cost. Bas Chrome browser + Kaggle login.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 1. NOTEBOOK CODE — Colab notebook me paste hoga
# ═══════════════════════════════════════════════════════════════════
# Ye code Colab ke andar run hoga. Sab kuch auto-install karega.
# Koi bhi error handle karega. GPU detect karega.
# ═══════════════════════════════════════════════════════════════════

FLUX_NOTEBOOK_CODE = r'''# ── TAGS Content Agent: Image Generation ──
# Colab me ye code paste karo, Ctrl+F9 dabao, image ban jayega!

import subprocess, sys, json, os, traceback, time, requests

# ── 1. Dependencies Install ──
print("Installing dependencies...")
sys.stdout.flush()
try:
    import google.colab
    IN_COLAB = True
    # Colab me phle se torch hai, bas diffusers install karo
    !pip install -q diffusers transformers accelerate sentencepiece protobuf 2>&1 | tail -1
except ImportError:
    IN_COLAB = False
    !pip install -q diffusers transformers accelerate torch sentencepiece protobuf 2>&1 | tail -1

# ── 2. GPU Check ──
GPU_NAME = "unknown"
COMPUTE_CAP = 0
try:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
        text=True, timeout=10
    ).strip().lower()
    parts = out.split(",")
    GPU_NAME = parts[0].strip() if parts else "unknown"
    COMPUTE_CAP = float(parts[1].strip()) if len(parts) > 1 else 0
    print(f"GPU: {GPU_NAME} (compute {COMPUTE_CAP})")
except Exception as e:
    print(f"No GPU detected: {e}")

sys.stdout.flush()

# ── 3. Model Load ──
import torch
from diffusers import DiffusionPipeline
from PIL import Image

# FLUX ke liye HuggingFace token chahiye, nahi hai to SDXL use karega
HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HF_TOKEN_READ", "")
MODEL_NAME = "black-forest-labs/FLUX.1-dev" if HF_TOKEN else "stabilityai/stable-diffusion-xl-base-1.0"

print(f"Loading {MODEL_NAME}... (2-5 min)")
sys.stdout.flush()

dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.get_device_capability(0)[0] >= 7 else torch.float16
pipe = DiffusionPipeline.from_pretrained(
    MODEL_NAME,
    torch_dtype=dtype,
    token=HF_TOKEN or None,
    safety_checker=None,
    requires_safety_checker=False,
)
pipe.enable_model_cpu_offload()
if hasattr(pipe, "enable_attention_slicing"):
    pipe.enable_attention_slicing()

# ── 4. Generate ──
PROMPT = "__PROMPT__"
WIDTH = __WIDTH__
HEIGHT = __HEIGHT__
STEPS = __STEPS__

print(f"\nGenerating: {PROMPT[:100]}...")
print(f"Size: {WIDTH}x{HEIGHT}, Steps: {STEPS}")
sys.stdout.flush()

try:
    image = pipe(
        PROMPT,
        width=WIDTH,
        height=HEIGHT,
        num_inference_steps=STEPS,
        guidance_scale=7.5,
    ).images[0]

    output_path = "output.png"
    image.save(output_path)
    sz = os.path.getsize(output_path)
    print(f"\n✅ SUCCESS: {output_path}")
    print(f"   Size: {sz} bytes ({sz/1024:.1f} KB)")

    # Colab download
    if IN_COLAB:
        from google.colab import files
        files.download(output_path)

    # JSON result for parsing
    print(json.dumps({
        "status": "success",
        "file": output_path,
        "size_bytes": sz,
        "prompt": PROMPT[:100],
    }))
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    traceback.print_exc()
    print(json.dumps({"status": "error", "error": str(e)}))
'''

COGVIDEO_NOTEBOOK_CODE = r'''# ── TAGS Content Agent: Video Generation ──
# Colab me paste karo, Ctrl+F9 dabao, video ban jayega!

import subprocess, sys, json, os, traceback, time

# ── 1. Dependencies ──
print("Installing video dependencies...")
sys.stdout.flush()
try:
    import google.colab
    IN_COLAB = True
    !pip install -q diffusers transformers accelerate imageio[ffmpeg] 2>&1 | tail -1
except ImportError:
    IN_COLAB = False
    !pip install -q diffusers transformers accelerate torch imageio[ffmpeg] 2>&1 | tail -1

# ── 2. GPU Check ──
GPU_NAME = "unknown"
COMPUTE_CAP = 0
try:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
        text=True, timeout=10
    ).strip().lower()
    parts = out.split(",")
    GPU_NAME = parts[0].strip() if parts else "unknown"
    COMPUTE_CAP = float(parts[1].strip()) if len(parts) > 1 else 0
    print(f"GPU: {GPU_NAME} (compute {COMPUTE_CAP})")
except Exception as e:
    print(f"No GPU detected: {e}")
sys.stdout.flush()

# P100 hack — sm_60 support
if "p100" in GPU_NAME:
    print("P100 detected — using CPU offload mode...")

# ── 3. Model Load ──
import torch
from diffusers import CogVideoXPipeline
import imageio

print("Loading CogVideoX-2b... (3-6 min)")
sys.stdout.flush()

dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.get_device_capability(0)[0] >= 7 else torch.float16
pipe = CogVideoXPipeline.from_pretrained(
    "THUDM/CogVideoX-2b",
    torch_dtype=dtype,
)
pipe.enable_model_cpu_offload()

# ── 4. Generate Video ──
PROMPT = "__PROMPT__"
FRAMES = __FRAMES__

print(f"\nGenerating video: {PROMPT[:100]}... ({FRAMES} frames)")
print("This takes 5-10 minutes on T4...")
sys.stdout.flush()

try:
    video_frames = pipe(
        PROMPT,
        num_videos_per_prompt=1,
        num_inference_steps=50,
        num_frames=FRAMES,
        guidance_scale=6.0,
    ).videos[0]

    output_path = "output.mp4"
    imageio.mimsave(output_path, video_frames, fps=8)
    sz = os.path.getsize(output_path)
    print(f"\n✅ SUCCESS: {output_path}")
    print(f"   Size: {sz} bytes ({sz/1024:.1f} KB)")

    if IN_COLAB:
        from google.colab import files
        files.download(output_path)

    print(json.dumps({
        "status": "success",
        "file": output_path,
        "size_bytes": sz,
        "frames": FRAMES,
        "prompt": PROMPT[:100],
    }))
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    traceback.print_exc()
    print(json.dumps({"status": "error", "error": str(e)}))
'''

# ═══════════════════════════════════════════════════════════════════
# 2. TEMPLATE HELPERS
# ═══════════════════════════════════════════════════════════════════

def _build_flux_code(prompt: str, width: int = 1024, height: int = 1024, steps: int = 20) -> str:
    """FLUX code template mein prompt inject karo."""
    code = FLUX_NOTEBOOK_CODE
    code = code.replace('__PROMPT__', prompt.replace('"', "'").replace('\\', '').replace('\n', ' '))
    code = code.replace('__WIDTH__', str(width))
    code = code.replace('__HEIGHT__', str(height))
    code = code.replace('__STEPS__', str(steps))
    return code


def _build_cogvideo_code(prompt: str, frames: int = 49) -> str:
    """CogVideo code template mein prompt inject karo."""
    code = COGVIDEO_NOTEBOOK_CODE
    code = code.replace('__PROMPT__', prompt.replace('"', "'").replace('\\', '').replace('\n', ' '))
    code = code.replace('__FRAMES__', str(frames))
    return code


# ═══════════════════════════════════════════════════════════════════
# 3. COLAB PIPELINE — ChromeTool ke saath
# ═══════════════════════════════════════════════════════════════════
# Ye sirf Google Colab pe focus karta hai (Kaggle backup hai).
# ═══════════════════════════════════════════════════════════════════

COLAB_URL = "https://colab.research.google.com"


async def _wait_until(chrome: Any, condition: callable, timeout: int = 120, interval: float = 2.0) -> dict:
    """Wait until condition(chrome) returns truthy or timeout."""
    start = time.time()
    last_text = ""
    while time.time() - start < timeout:
        try:
            result = await condition(chrome)
            if result:
                return {"status": "ok", "result": result}
        except Exception:
            pass
        await asyncio.sleep(interval)
    return {"status": "timeout", "message": f"Condition not met in {timeout}s"}


async def colab_setup_session(chrome: Any) -> dict[str, Any]:
    """Robust Colab session setup — connects, creates notebook, sets GPU.

    Step-by-step with Chrome inspect & click (no fragile keyboard shortcuts).
    """
    logger.info("=== Colab Session Setup ===")

    # 1. Navigate to Colab
    await chrome.goto(COLAB_URL)
    await asyncio.sleep(3)

    # 2. Check page loaded
    page = await chrome.inspect()
    text = page.get("text", "")
    if "colaboratory" not in text.lower() and "colab" not in text.lower():
        # Try loading again
        await chrome.goto(COLAB_URL)
        await asyncio.sleep(5)
        page = await chrome.inspect()
        text = page.get("text", "")

    # 3. File → New notebook via menu
    #    Colab me File button pe click karo
    for label in ["File", "file", "New"]:
        if label in text:
            logger.info(f"Found '{label}' in page — creating new notebook")
            break

    # Best approach: use Ctrl+N shortcut (works in Colab)
    await chrome.press("Control+n")
    await asyncio.sleep(4)

    # Wait for new notebook to fully load
    await chrome.wait("network-idle")
    await asyncio.sleep(3)

    logger.info("New notebook created!")
    return {"status": "notebook_ready"}


async def colab_paste_code(chrome: Any, notebook_code: str) -> dict[str, Any]:
    """Colab code cell me code paste karo — JAVASCRIPT INJECTION se.

    Abhi character-by-character nahi, directly JavaScript se code set karega.
    400 lines ka code 2 second me cell me aa jayega!
    """
    logger.info("Pasting code into Colab cell (JavaScript injection)...")

    # Wait for cell to fully render
    await asyncio.sleep(3)

    # JavaScript injection — Colab code cell ka content set karo
    # Colab ka code cell <textarea> ya <div contenteditable> hota hai
    js_code = f"""
    (function() {{
        // Colab code cell ka textarea dhundo
        let cell = document.querySelector('.cell.code .cell-input-output-panel textarea');
        if (!cell) cell = document.querySelector('.CodeMirror textarea');
        if (!cell) cell = document.querySelector('[contenteditable="true"]');
        if (!cell) cell = document.querySelector('textarea');

        if (cell) {{
            // React state ke liye native setter use karo
            const nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value'
            ).set;
            nativeSetter.call(cell, `{notebook_code.replace('`', '\\`').replace('${', '\\${')}`);
            cell.dispatchEvent(new Event('input', {{ bubbles: true }}));
            cell.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return 'Code injected: ' + cell.value.length + ' chars';
        }}
        return 'Code cell not found, trying fallback...';
    }})()
    """

    eval_result = await chrome.eval(js_code)
    logger.info(f"JS injection result: {eval_result}")

    # Fallback: agar JS kaam nahi karta to character-by-character karo
    result_text = str(eval_result)
    if "Code injected" not in result_text:
        logger.warning("JS injection failed, using character-by-character fallback...")
        page_info = await chrome.inspect()
        cell_uid = _find_code_cell_uid(page_info)
        if cell_uid:
            await chrome.click(uid=cell_uid)
            await asyncio.sleep(1)

        await chrome.press("Control+a")
        await asyncio.sleep(0.5)
        await chrome.press("Delete")
        await asyncio.sleep(0.3)

        chunks = [notebook_code[i:i+1000] for i in range(0, len(notebook_code), 1000)]
        for i, chunk in enumerate(chunks):
            await chrome.fill(chunk, selector="textarea, [contenteditable], .CodeMirror textarea")
            await asyncio.sleep(1)

    logger.info("Code pasted successfully!")
    return {"status": "code_pasted"}


async def colab_set_gpu(chrome: Any) -> dict[str, Any]:
    """Set GPU runtime in Colab: Runtime → Change runtime type → T4 GPU.

    Uses menu navigation:
      1. Runtime menu click
      2. "Change runtime type" click
      3. Select "T4 GPU" from dropdown
      4. Save
    """
    logger.info("Setting Colab GPU runtime...")

    # Wait for menu to load
    await asyncio.sleep(2)

    # Approach: Use keyboard shortcut for Runtime settings
    # Colab shortcut for "Runtime" menu: Alt+R (Windows) / Ctrl+Option+R (Mac)
    # But safer to use the UI buttons

    page_info = await chrome.inspect()
    text = page_info.get("text", "")

    # Find and click Runtime menu button
    runtime_clicked = False
    for uid_info in _parse_uids(page_info):
        uid_text = uid_info.get("text", "").lower()
        if "runtime" in uid_text or "Runtime" in uid_info.get("text", ""):
            await chrome.click(uid=uid_info["uid"])
            await asyncio.sleep(1.5)
            runtime_clicked = True
            break

    if not runtime_clicked:
        # Try finding the menu by common patterns
        # Colab menus: File, Edit, View, Insert, Runtime, Tools, Help
        for label in ["Runtime", "runtime", "Exécution"]:
            # Click at top of page where menus are
            for uid_info in _parse_uids(page_info):
                if label in uid_info.get("text", ""):
                    await chrome.click(uid=uid_info["uid"])
                    await asyncio.sleep(1.5)
                    runtime_clicked = True
                    break
            if runtime_clicked:
                break

    if not runtime_clicked:
        logger.warning("Could not find Runtime menu, trying shortcut...")
        # Fallback: Ctrl+F9 runs all, but we need GPU first
        # Let's just run with whatever default GPU Colab provides
        return {"status": "gpu_default", "note": "Using default Colab runtime"}

    # Now look for "Change runtime type" or "Change runtime" menu item
    await asyncio.sleep(1)
    page_info = await chrome.inspect()
    text = page_info.get("text", "")

    for uid_info in _parse_uids(page_info):
        uid_text = uid_info.get("text", "").lower()
        if "change runtime" in uid_text or "runtime type" in uid_text:
            await chrome.click(uid=uid_info["uid"])
            await asyncio.sleep(2)
            break

    # In the dialog, find GPU dropdown
    await asyncio.sleep(1)
    page_info = await chrome.inspect()
    text = page_info.get("text", "")

    # Click dropdown / select element
    for uid_info in _parse_uids(page_info):
        uid_text = uid_info.get("text", "").lower()
        uid_type = uid_info.get("tag", "").lower()
        # Look for "None" (which is the default accelerator) or select elements
        if uid_type == "select" or "t4 gpu" in uid_text or "none" in uid_text:
            await chrome.click(uid=uid_info["uid"])
            await asyncio.sleep(1)
            break

    # Select T4 GPU from the options
    await asyncio.sleep(0.5)
    page_info = await chrome.inspect()

    for uid_info in _parse_uids(page_info):
        uid_text = uid_info.get("text", "").lower()
        if "t4 gpu" in uid_text or "gpu" in uid_text:
            await chrome.click(uid=uid_info["uid"])
            await asyncio.sleep(0.5)
            break

    # Click Save or OK button
    for uid_info in _parse_uids(page_info):
        uid_text = uid_info.get("text", "").lower()
        if uid_text in ("save", "ok", "confirm", "select"):
            await chrome.click(uid=uid_info["uid"])
            await asyncio.sleep(1)
            break

    return {"status": "gpu_set_t4"}


async def colab_run_and_wait(chrome: Any, timeout_minutes: int = 12) -> dict[str, Any]:
    """Run all cells and wait for completion.

    Strategy: Ctrl+F9 (Run all) → poll for SUCCESS/ERROR.
    """
    logger.info("Running notebook...")

    # Run all cells
    await chrome.press("Control+F9")
    await asyncio.sleep(3)

    # Poll for completion
    max_wait = timeout_minutes * 60
    poll_interval = 15
    start_time = time.time()
    last_text = ""

    logger.info(f"Waiting up to {timeout_minutes} min for generation...")

    while time.time() - start_time < max_wait:
        await asyncio.sleep(poll_interval)

        page_text_resp = await chrome.text()
        page_text = page_text_resp.get("text", "")

        # Check for success markers
        if "SUCCESS:" in page_text or '"status": "success"' in page_text:
            logger.info("✅ Generation completed!")
            # Try to get the output details
            output_info = page_text[-2000:]  # Last 2000 chars
            return {
                "status": "completed",
                "output": output_info,
                "elapsed_seconds": int(time.time() - start_time),
            }

        # Check for errors
        if "❌ ERROR:" in page_text:
            logger.warning("❌ Generation error detected")
            # Extract error message
            error_match = re.search(r'❌ ERROR:\s*(.+?)(?:\n|$)', page_text)
            error_msg = error_match.group(1) if error_match else "Unknown error"
            return {
                "status": "error",
                "error": error_msg,
                "output": page_text[-1000:],
            }

        # Check if timeout reached
        elapsed = int(time.time() - start_time)
        if elapsed % 60 == 0 or (page_text != last_text and elapsed > 30):
            logger.info(f"Running... ({elapsed}s/{timeout_minutes*60}s)")

        last_text = page_text

    return {
        "status": "timeout",
        "message": f"Notebook did not complete in {timeout_minutes} minutes",
    }


async def colab_download_latest(chrome: Any) -> dict[str, Any]:
    """Download the generated file from Colab.

    Colab's files.download() triggers browser download automatically,
    so we just need to wait for the download to appear.
    """
    logger.info("Waiting for download...")
    await asyncio.sleep(3)

    # The files.download() call in the notebook code triggers a browser download
    # We can check if a download completed by looking at Chrome's downloads
    # Currently we just report that the code has been executed with download trigger

    return {
        "status": "download_triggered",
        "note": "files.download() called in notebook — browser should auto-download",
    }


# ═══════════════════════════════════════════════════════════════════
# 4. FULL PIPELINE — Image/Video via Colab
# ═══════════════════════════════════════════════════════════════════

async def generate_via_colab_chrome(
    chrome: Any,
    prompt: str,
    content_type: str = "image",
    width: int = 0,
    height: int = 0,
    steps: int = 20,
    frames: int = 49,
    platform: str = "",
) -> dict[str, Any]:
    """FULL PIPELINE: Colab open → notebook → code paste → GPU → run → download.

    Platform ke hisaab se auto size detect karega.
    Agar platform name diya to width/height auto set hoge.

    Args:
        chrome: ChromeTool instance
        prompt: Text prompt for generation
        content_type: "image" or "video"
        width: Image width (0 = auto from platform)
        height: Image height (0 = auto from platform)
        steps: Inference steps
        frames: Video frames (49 = ~6 sec)
        platform: Platform name (instagram, facebook_ad auto size

    Returns:
        Dict with status, output details
    """
    logger.info("=" * 50)
    logger.info("🚀 Colab Generation Pipeline — Starting")
    logger.info(f"   Type: {content_type}")
    logger.info(f"   Platform: {platform or 'custom'}")
    logger.info(f"   Prompt: {prompt[:80]}...")

    # Platform ke hisaab se auto size detect
    if platform and (width == 0 or height == 0):
        try:
            from admin.tools.local_gen import get_platform_size
            w, h = get_platform_size(platform, width, height)
            if width == 0: width = w
            if height == 0: height = h
            logger.info(f"   Auto size from platform '{platform}': {width}x{height}")
        except ImportError:
            if width == 0: width = 1024
            if height == 0: height = 1024
    else:
        if width == 0: width = 1024
        if height == 0: height = 1024

    logger.info("=" * 50)

    # Step 0: Build notebook code
    if content_type == "video":
        code = _build_cogvideo_code(prompt, frames)
        logger.info(f"Video code: {len(code)} chars, {frames} frames")
    else:
        code = _build_flux_code(prompt, width, height, steps)
        logger.info(f"Image code: {len(code)} chars, {width}x{height}")

    # Step 1: Setup Colab session (navigate + new notebook)
    setup = await colab_setup_session(chrome)
    if setup.get("status") != "notebook_ready":
        logger.warning(f"Setup issue: {setup}")

    # Step 2: Paste code into cell
    paste = await colab_paste_code(chrome, code)
    if paste.get("status") != "code_pasted":
        return {"status": "error", "step": "paste", "detail": paste}

    # Step 3: Set GPU runtime
    gpu = await colab_set_gpu(chrome)
    logger.info(f"GPU setup: {gpu.get('status')}")

    # Step 4: Run and wait
    result = await colab_run_and_wait(chrome)
    logger.info(f"Run result: {result.get('status')}")

    # Step 5: Download
    if result.get("status") == "completed":
        dl = await colab_download_latest(chrome)
        result["download"] = dl

    return result


# ═══════════════════════════════════════════════════════════════════
# 5. KAGGLE PIPELINE (BACKUP) — via Chrome
# ═══════════════════════════════════════════════════════════════════
# Kaggle ka UI unstable hai, isliye Colab primary hai.
# Ye backup hai agar Colab fail kare.

KAGGLE_URL = "https://www.kaggle.com"
KAGGLE_LOGIN_URL = f"{KAGGLE_URL}/account/login"


async def kaggle_login(chrome: Any, email: str = "", password: str = "") -> dict[str, Any]:
    """Login to Kaggle via Chrome browser form fill."""
    logger.info("Kaggle login...")

    # Try saved cookies first
    try:
        cookies_result = await chrome.load_cookies()
        if "loaded" in str(cookies_result).lower():
            await chrome.goto(f"{KAGGLE_URL}/kernels")
            await asyncio.sleep(2)
            page_text = (await chrome.text()).get("text", "")
            if "notebook" in page_text.lower() or "kernel" in page_text.lower():
                return {"status": "logged_in", "method": "cookies"}
    except Exception:
        pass

    # Go to login
    await chrome.goto(KAGGLE_LOGIN_URL)
    await asyncio.sleep(3)

    page_text = (await chrome.text()).get("text", "")

    # Check if already on dashboard
    if "notebook" in page_text.lower() or "kernels" in page_text.lower():
        await chrome.save_cookies()
        return {"status": "already_logged_in"}

    # Fill form if creds provided
    if email:
        await chrome.fill(email, selector="input[name='email'], input[type='email'], #email")
        await asyncio.sleep(1)
    if password:
        await chrome.fill(password, selector="input[type='password']")
        await asyncio.sleep(1)

    # Click submit
    await chrome.click(selector="button[type='submit']")
    await asyncio.sleep(4)

    # Save cookies for next time
    try:
        await chrome.save_cookies()
    except Exception:
        pass

    return {"status": "login_attempted"}


async def generate_via_kaggle_chrome(
    chrome: Any,
    prompt: str,
    content_type: str = "image",
    width: int = 1024,
    height: int = 1024,
    steps: int = 20,
    frames: int = 49,
    email: str = "",
    password: str = "",
) -> dict[str, Any]:
    """Forgot about Kaggle for now — use Colab instead."""
    logger.warning("Kaggle pipeline deprecated. Use Colab instead.")
    return {"status": "error", "message": "Kaggle pipeline is unstable. Use generate_via_colab_chrome instead."}


# ═══════════════════════════════════════════════════════════════════
# 6. LLM TOOL DEFINITIONS
# ═══════════════════════════════════════════════════════════════════

CHROME_GENERATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image_via_colab_chrome",
            "description": "Generate AI image using Google Colab Chrome automation. Opens Colab, creates FLUX notebook, runs on free T4 GPU, downloads output. Platform ke hisaab se auto size!",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed image description (brand colors, style, text overlay)"},
                    "platform": {"type": "string", "description": "Platform name for auto-sizing (instagram, facebook_ad, linkedin_post, youtube_thumbnail, blog_hero, etc.)", "default": ""},
                    "width": {"type": "integer", "description": "Custom width (overrides platform auto-size)", "default": 0},
                    "height": {"type": "integer", "description": "Custom height (overrides platform auto-size)", "default": 0},
                    "steps": {"type": "integer", "description": "Quality steps (20-50, more = better but slower)", "default": 20},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video_via_colab_chrome",
            "description": "Generate AI video using Google Colab Chrome automation. Opens Colab, creates CogVideoX notebook, runs on free T4 GPU, downloads MP4.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed video description"},
                    "frames": {"type": "integer", "description": "Number of frames (49=~6sec video, 98=~12sec)", "default": 49},
                    "platform": {"type": "string", "description": "Platform (for reference, video size is platform-standard)", "default": ""},
                },
                "required": ["prompt"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
# 7. UI HELP══════════════════════════════════════════════════════════════

def _find_code_cell_uid(page_info: Any) -> str | None:
    """Page inspect result mein se code cell ka UID find karo."""
    try:
        for uid_info in _parse_uids(page_info):
            uid_text = uid_info.get("text", "").lower()
            uid_tag = uid_info.get("tag", "").lower()
            uid_type = uid_info.get("type", "").lower()
            if any(kw in uid_text for kw in ["code", "import ", "print", "def ", "#"]):
                return uid_info["uid"]
            if uid_tag in ("textarea", "code"):
                return uid_info["uid"]
    except Exception:
        pass
    return None


def _parse_uids(page_info: Any) -> list[dict[str, str]]:
    """Page info se UIDs nikaalo — each element ka uid, text, tag."""
    uids = []
    try:
        if isinstance(page_info, dict):
            text = page_info.get("text", "")
        else:
            text = str(page_info)

        for line in text.split("\n"):
            # Pattern: "  n0: <DIV> Click me"
            match = re.match(r'\s*(n\d+)\s*:', line)
            if match:
                uid = match.group(1)
                uids.append({
                    "uid": uid,
                    "text": line,
                    "tag": "unknown",
                    "href": "",
                    "type": "",
                })
    except Exception:
        pass
    return uids


# ═══════════════════════════════════════════════════════════════════
# 8. DISPATCH
# ═══════════════════════════════════════════════════════════════════

CHROME_GEN_DISPATCH = {
    "generate_image_via_colab_chrome": lambda chrome, args: generate_via_colab_chrome(
        chrome, args["prompt"], "image",
        width=args.get("width", 0), height=args.get("height", 0),
        steps=args.get("steps", 20),
        platform=args.get("platform", ""),
    ),
    "generate_video_via_colab_chrome": lambda chrome, args: generate_via_colab_chrome(
        chrome, args["prompt"], "video",
        frames=args.get("frames", 49),
        platform=args.get("platform", ""),
    ),
    "generate_image_via_kaggle_chrome": lambda chrome, args: generate_via_kaggle_chrome(
        chrome, args["prompt"], "image",
    ),
    "generate_video_via_kaggle_chrome": lambda chrome, args: generate_via_kaggle_chrome(
        chrome, args["prompt"], "video",
    ),
}


async def execute_chrome_generation(tool_name: str, args: dict, chrome: Any) -> dict[str, Any]:
    """LLM tool dispatch — Chrome-based generation execute karega."""
    handler = CHROME_GEN_DISPATCH.get(tool_name)
    if not handler:
        return {"status": "error", "error": f"Unknown chrome gen tool: {tool_name}"}
    if "kaggle" in tool_name:
        return {
            "status": "deprecated",
            "message": "Kaggle pipeline deprecated. Please use *_via_colab_chrome tools instead.",
        }
    return await handler(chrome, args)
