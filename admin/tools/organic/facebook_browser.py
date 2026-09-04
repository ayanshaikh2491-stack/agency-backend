"""Facebook Groups + Marketplace posting via ChromeTool browser automation.

Phase 1b: real browser flow (Phase 1 was contract-only `queued`).

Requires a saved Chrome profile where the client is already logged into
Facebook (profile_dir in channel config). All browser actions reuse
ChromeTool's human-like delays. The DOM is volatile, so every step uses a
label-based finder with selector fallbacks, and failures return actionable
errors with a screenshot path for debugging.

Status contract (global): "published" | "error" | "config_missing" | "queued".
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

from admin.tools.organic.base import CHANNEL_TYPE_BROWSER, PostResult
from admin.tools.organic.config import get_channel_config

logger = logging.getLogger(__name__)

CHANNEL_META = {
    "id": "facebook",
    "name": "Facebook",
    "type": CHANNEL_TYPE_BROWSER,
    "auth": "browser_session",
    "capabilities": ["groups", "marketplace"],
    "required_fields": ["target"],
    "description": "Post to Facebook Groups and Marketplace listings via browser automation.",
}

MARKETPLACE_URL = "https://www.facebook.com/marketplace/create/listing"

# Label-based element finder. Returns {selector, kind} (kind: fill|click) or
# null. Runs fast inside the page (no 30s playwright timeouts per attempt).
_JS_FIND = """(label) => {
  const q = String(label);
  const els = [...document.querySelectorAll(
    'button, [role="button"], input, textarea, [contenteditable="true"], [role="textbox"]')];
  const txt = (el) => [
    (el.getAttribute('aria-label')||'').trim(),
    (el.getAttribute('placeholder')||'').trim(),
    (el.innerText||'').trim(),
    (el.value||'').trim(),
  ];
  const exact = (el) => txt(el).includes(q);
  const fuzzy = (el) => txt(el).some((t) => t && t.includes(q));
  const hit = els.find(exact) || els.find(fuzzy);
  if (!hit) return null;
  const isFill = hit.tagName === 'TEXTAREA' || hit.isContentEditable ||
                 hit.getAttribute('role') === 'textbox' ||
                 hit.tagName === 'INPUT';
  const aria = hit.getAttribute('aria-label');
  if (aria) return {selector: '[aria-label="' + aria + '"]', kind: isFill ? 'fill' : 'click'};
  const ph = hit.getAttribute('placeholder');
  if (ph) return {selector: '[placeholder="' + ph + '"]', kind: 'fill'};
  if (hit.id) return {selector: '#' + hit.id, kind: isFill ? 'fill' : 'click'};
  return null;
}"""


# ── Small helpers ──────────────────────────────────────────────────────────

def _err(msg: str) -> PostResult:
    return PostResult(status="error", channel="facebook", error=str(msg)[:300])


def _is_marketplace(target: str) -> bool:
    return target.strip().lower() == "marketplace"


def _new_chrome(profile_dir: str):
    """Create the ChromeTool instance (module-level so tests can patch it)."""
    from admin.tools.chrome_tool import ChromeTool
    return ChromeTool(browser_name="sba", workspace="agency", profile_dir=profile_dir)


def _download_image(url: str) -> str | None:
    """Download image_url to a temp file for FB file inputs. None on failure."""
    import tempfile
    import requests
    try:
        resp = requests.get(url, timeout=30)
        if not resp.ok:
            logger.warning("FB image download failed: HTTP %s", resp.status_code)
            return None
        fd, path = tempfile.mkstemp(suffix=".jpg")
        with os.fdopen(fd, "wb") as f:
            f.write(resp.content)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("FB image download error: %s", exc)
        return None


def _run_async(coro) -> PostResult:
    """Run an async browser coroutine from sync post() safely.

    Works both from a plain thread (pytest / CLI) and from inside a running
    event loop (FastAPI async routes) — the latter runs in a worker thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def _runner() -> None:
        box["result"] = asyncio.run(coro)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    return box["result"]


async def _find(chrome, label: str) -> dict | None:
    """Find a page element by label. Returns {selector, kind} or None."""
    safe_label = label.replace("\\", "\\\\").replace("'", "\\'")
    expr = f"({_JS_FIND})('{safe_label}')"
    try:
        found = await chrome.eval_json(expr)
    except Exception as exc:  # noqa: BLE001
        logger.debug("find(%r) failed: %s", label, exc)
        return None
    if isinstance(found, dict) and found.get("selector"):
        return found
    return None


async def _click_label(chrome, label: str) -> bool:
    """Click an element by label (aria-label, placeholder, or exact text)."""
    found = await _find(chrome, label)
    if found and found.get("kind") != "fill":
        res = await chrome.click(selector=found["selector"])
        if "error" not in res:
            return True
    # Fallback: JS click on any button whose label includes the text.
    js_click = (
        f"(() => {{ const q='{label}'; const el=[...document.querySelectorAll("
        "'button,[role=button]')].find(e => ((e.getAttribute('aria-label')||'')"
        f"+(e.innerText||'')).includes(q)); if(el){{ el.click(); return true; }} return false; }})()"
    )
    try:
        res = await chrome.eval_json(js_click)
        return bool(res)
    except Exception:  # noqa: BLE001
        return False


async def _fill_label(chrome, label: str, text: str) -> bool:
    found = await _find(chrome, label)
    if not found:
        return False
    res = await chrome.fill(text, selector=found["selector"])
    return "error" not in res


# ── Groups flow ────────────────────────────────────────────────────────────

async def _post_to_group(chrome, target: str, payload: dict, cfg: dict) -> PostResult:
    res = await chrome.goto(target)
    if "error" in res:
        return _err(f"goto {target} failed: {res.get('error')}")
    await chrome.wait("network-idle")

    message = payload.get("message", "")
    if not message:
        return _err("Group post needs a 'message'.")

    composer = await _find(chrome, "Write something")
    if not composer:
        return _err("Group composer not found. Is the profile logged into Facebook?")
    await chrome.click(selector=composer["selector"])
    await chrome.wait("network-idle")
    # FB re-renders the expanded composer; re-resolve before typing.
    composer = await _find(chrome, "Write something") or composer
    await chrome.fill(message, selector=composer["selector"])
    await chrome.wait("network-idle")

    if payload.get("image_url"):
        img_path = _download_image(payload["image_url"])
        if img_path:
            up = await chrome.upload(img_path, selector='input[type="file"]')
            if "error" not in up:
                await chrome.wait("network-idle")

    if not await _click_label(chrome, "Post"):
        shot = await chrome.screenshot()
        return _err(f"Post button not found after typing. Screenshot: {shot.get('text', '')}")

    await chrome.wait("network-idle")
    page_text = (await chrome.text()).get("text", "")
    if message[:40] in page_text:
        return PostResult(status="published", channel="facebook", post_id="", post_url=target)
    shot = await chrome.screenshot()
    return _err(f"Post submitted but not verified in feed. Screenshot: {shot.get('text', '')}")


# ── Marketplace flow ───────────────────────────────────────────────────────

async def _post_to_marketplace(chrome, payload: dict, cfg: dict) -> PostResult:
    res = await chrome.goto(MARKETPLACE_URL)
    if "error" in res:
        return _err(f"goto {MARKETPLACE_URL} failed: {res.get('error')}")
    await chrome.wait("network-idle")

    # Marketplace requires >= 1 photo.
    image_url = payload.get("image_url") or (cfg.get("default_images") or [""])[0]
    uploaded = False
    if image_url:
        img_path = _download_image(image_url)
        if img_path:
            up = await chrome.upload(img_path, selector='input[type="file"]')
            uploaded = "error" not in up
            if uploaded:
                await chrome.wait("network-idle")
    if not uploaded:
        return _err("Marketplace listing needs a photo (image_url or default_images in config).")

    category = payload.get("category") or cfg.get("default_category", "")
    if category:
        if await _click_label(chrome, "Choose a category"):
            await chrome.wait("network-idle")
            await _click_label(chrome, category)
            await chrome.wait("network-idle")

    if not await _fill_label(chrome, "What are you selling?", str(payload.get("title", ""))):
        return _err("Could not find the listing title field.")
    price = str(payload.get("price", "")).replace("$", "").replace(",", "").strip()
    if not await _fill_label(chrome, "Price", price):
        return _err("Could not find the price field.")
    if payload.get("description"):
        await _fill_label(chrome, "Description", str(payload["description"]))
    await chrome.wait("network-idle")

    # Wizard: Next → Next → Publish.
    for _ in range(3):
        if await _click_label(chrome, "Next"):
            await chrome.wait("network-idle")
        else:
            break
    published = await _click_label(chrome, "Publish") or await _click_label(chrome, "List item")
    await chrome.wait("network-idle")

    if published:
        return PostResult(
            status="published", channel="facebook", post_id="",
            post_url="https://www.facebook.com/marketplace",
        )
    shot = await chrome.screenshot()
    return _err(f"Marketplace publish failed. Screenshot: {shot.get('text', '')}")


# ── Orchestration ──────────────────────────────────────────────────────────

async def _post_via_browser(profile_dir: str, payload: dict, cfg: dict, target: str) -> PostResult:
    chrome = _new_chrome(profile_dir)
    try:
        status = await chrome.status()
        if "disconnected" in status.get("text", ""):
            return _err("Chrome daemon unavailable. Start Chrome or fix profile_dir in config.")
        if _is_marketplace(target):
            return await _post_to_marketplace(chrome, payload, cfg)
        return await _post_to_group(chrome, target, payload, cfg)
    finally:
        try:
            await chrome.close()
        except Exception:  # noqa: BLE001
            pass


def post(workspace_id: str, payload: dict) -> PostResult:
    """Post to a Facebook Group or Marketplace listing via ChromeTool.

    Config (per workspace, via /api/social/organic/config):
      profile_dir        — Chrome profile with a logged-in Facebook session (required)
      default_groups     — list of group URLs used when payload target is empty
      default_images     — list of image URLs used when payload image_url is empty
      default_category   — marketplace category label (optional)
    """
    cfg = get_channel_config(workspace_id, "facebook")
    profile_dir = cfg.get("profile_dir", "")
    if not profile_dir:
        return PostResult(status="config_missing", channel="facebook",
                          error="No profile_dir in config. Set a Chrome profile logged into Facebook.")

    target = str(payload.get("target") or (cfg.get("default_groups") or [""])[0] or "")
    if not target:
        return _err("No target. Pass a group URL, 'marketplace', or set default_groups in config.")

    if _is_marketplace(target):
        missing = []
        if not payload.get("title"):
            missing.append("title")
        if not payload.get("price"):
            missing.append("price")
        if missing:
            return PostResult(status="error", channel="facebook",
                              error=f"Marketplace listing needs: {', '.join(missing)}.")
    elif "facebook.com/groups/" not in target:
        return _err("target must be a facebook group URL or 'marketplace'.")

    return _run_async(_post_via_browser(profile_dir, payload, cfg, target))
