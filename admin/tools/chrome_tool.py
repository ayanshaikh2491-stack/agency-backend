"""ChromeTool — bulletproof browser control for SBA.

Connects to persistent Chrome daemon via CDP.
Human-like behavior: character-by-character typing, random delays, mouse emulation.
Cookie save/load for LinkedIn/Upwork/Fiverr session persistence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
from typing import Any

logger = logging.getLogger(__name__)


class ChromeToolError(Exception):
    """Raised when an unknown/unimplemented chrome tool is dispatched."""


CDP_PORT = int(os.environ.get("SBA_CHROME_CDP_PORT", "9222"))
BASE_CDP_PORT = 9222

# ── Human-like behavior constants ────────────────────────────────
MIN_DELAY_AFTER_NAV = 1.0
MAX_DELAY_AFTER_NAV = 3.5
MIN_DELAY_BEFORE_CLICK = 0.3
MAX_DELAY_BEFORE_CLICK = 1.2
MIN_TYPING_SPEED = 60   # ms per character
MAX_TYPING_SPEED = 180
MIN_SCROLL_PAUSE = 0.5
MAX_SCROLL_PAUSE = 2.0

COOKIE_FILE_PATH = "/tmp/sba_cookies.json"


def _cdp_port_for_workspace(workspace: str) -> int:
    if workspace in ("agency", "sba", ""):
        return BASE_CDP_PORT
    # Stable across processes (builtin hash() is randomized per process).
    import hashlib
    digest = hashlib.sha256(workspace.encode("utf-8")).hexdigest()
    return BASE_CDP_PORT + 1 + (int(digest[:8], 16) % 100)


try:
    from playwright.async_api import async_playwright
    HAVE_PW = True
except ImportError:
    HAVE_PW = False


class ChromeTool:
    """Connects to persistent Chrome daemon via CDP."""

    def __init__(self, browser_name: str = "sba", workspace: str = "agency", profile_dir: str | None = None) -> None:
        self.browser_name = browser_name
        self.workspace = workspace
        self.cdp_port = _cdp_port_for_workspace(workspace)
        self.cdp_url = f"http://127.0.0.1:{self.cdp_port}"
        self.profile_dir = profile_dir or os.path.expanduser(r"~\.sba-chrome-profile")
        self._play = None
        self._browser = None
        self._page = None
        self._connected_once = False
        self._chrome_started = False

    def _ensure_daemon(self) -> None:
        """Auto-start Chrome daemon if not running (best-effort)."""
        if self._chrome_started:
            return
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("127.0.0.1", self.cdp_port))
                s.close()
                self._chrome_started = True
                return
            except ConnectionRefusedError:
                pass
            finally:
                s.close()

            # On Linux/EC2 the managed daemon service (sba-chrome.service)
            # owns this CDP port. Auto-starting a second Chrome here starts a
            # port/profile fight that kills the daemon mid-scrape. Only
            # auto-start when explicitly enabled (or on Windows dev boxes).
            if sys.platform.startswith("linux") and os.environ.get("SBA_AUTOSTART_CHROME", "0") != "1":
                logger.info(
                    "Chrome daemon on :%s managed by service; skipping auto-start",
                    self.cdp_port,
                )
                return

            # Start Chrome daemon
            import subprocess
            chrome_paths = [
                # Windows ms-playwright chromium (local dev)
                os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1228\chrome-win64\chrome.exe"),
                os.path.expandvars(r"%USERPROFILE%\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe"),
                # Linux ms-playwright chromium (EC2 autopilot)
                os.path.expanduser("~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome"),
                "/home/ubuntu/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome",
                # System chrome fallbacks
                "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium", "/usr/bin/chromium-browser",
                "/opt/google/chrome/chrome",
                "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
                "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
            ]
            chrome_exe = None
            for p in chrome_paths:
                if os.path.exists(p):
                    chrome_exe = p
                    break

            if not chrome_exe:
                logger.warning("No Chrome binary found for daemon auto-start")
                return

            user_data = self.profile_dir
            os.makedirs(user_data, exist_ok=True)

            # Kill stale daemons holding this profile (Chrome won't start with
            # the user-data-dir locked). Only targets our own daemons, never
            # the user's real Chrome.
            self._kill_stale_daemons(user_data)

            subprocess.Popen(
                [
                    chrome_exe,
                    f"--remote-debugging-port={self.cdp_port}",
                    "--headless",
                    "--disable-gpu", "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-extensions", "--disable-sync",
                    f"--user-data-dir={user_data}",
                    "--window-size=1920,1080",
                    "--no-first-run",
                    "--mute-audio",
                    "--js-flags=--max-old-space-size=4096",
                    "--disable-software-rasterizer",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            self._chrome_started = True
            logger.info("Auto-started Chrome daemon on port %s", self.cdp_port)
        except Exception as e:
            logger.warning("Failed to auto-start Chrome: %s", e)

    def _kill_stale_daemons(self, user_data: str) -> None:
        """Kill leftover SBA Chrome daemons locking the profile dir.

        Scans chrome.exe processes and kills only ones whose command line
        references ``user_data``. The current CDP port is left alone so an
        already-running daemon for this workspace is reused.
        """
        try:
            import subprocess as sp
            import json
            script = (
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
            )
            out = sp.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=15,
                creationflags=sp.CREATE_NO_WINDOW if hasattr(sp, "CREATE_NO_WINDOW") else 0,
            )
            raw = out.stdout.strip()
            if not raw or raw == "null":
                return
            try:
                procs = json.loads(raw)
            except json.JSONDecodeError:
                return
            if isinstance(procs, dict):
                procs = [procs]
            own_port = f"--remote-debugging-port={self.cdp_port}"
            for proc in procs:
                cmdline = proc.get("CommandLine") or ""
                pid = proc.get("ProcessId")
                if not pid or user_data not in cmdline:
                    continue
                if own_port in cmdline:
                    continue  # healthy daemon for this workspace
                sp.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=10,
                    creationflags=sp.CREATE_NO_WINDOW if hasattr(sp, "CREATE_NO_WINDOW") else 0,
                )
                logger.info("Killed stale SBA Chrome daemon (pid %s)", pid)
        except Exception as exc:
            logger.warning("Stale daemon cleanup skipped: %s", exc)

    async def _random_delay(self, min_s: float = 0.5, max_s: float = 2.0):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _ensure_page(self) -> Any:
        """Connect to Chrome daemon via CDP and return a page."""
        # Auto-start daemon if not running (with retry)
        self._ensure_daemon()

        if self._page:
            try:
                _ = await self._page.title()
                return self._page
            except Exception:
                self._page = None

        if not self._browser:
            try:
                if HAVE_PW:
                    # Retry in case daemon just started
                    import time
                    for attempt in range(20):
                        try:
                            self._play = await async_playwright().start()
                            self._browser = await self._play.chromium.connect_over_cdp(self.cdp_url)
                            self._connected_once = True
                            logger.info(f"Connected to Chrome daemon on :{self.cdp_port}")
                            break
                        except Exception as exc:
                            if attempt == 0:
                                logger.info("Waiting for Chrome daemon to be ready...")
                            if attempt < 19:
                                await asyncio.sleep(1)
                            else:
                                logger.warning("Failed to connect to Chrome daemon after 20s: %s", exc)
                                return None
                else:
                    return None
            except Exception as exc:
                if self._connected_once:
                    logger.warning("CDP reconnect failed: %s", exc)
                return None

        ctx = self._browser.contexts[0] if self._browser.contexts else None
        if ctx:
            pages = ctx.pages
            self._page = pages[0] if pages else await ctx.new_page()
        else:
            self._page = await self._browser.new_page()
        return self._page

    async def _safe(self, coro) -> dict[str, Any]:
        try:
            result = await coro
            return {"text": str(result) if result is not None else "ok"}
        except Exception as exc:
            logger.exception("Chrome CDP op failed")
            return {"error": str(exc)}

    # ── Navigation ───────────────────────────────────────────────

    async def goto(self, url: str, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        result = await self._safe(
            p.goto(url, wait_until="domcontentloaded",
                   timeout=int(os.environ.get("SBA_CHROME_GOTO_TIMEOUT", "120000")))
        )
        await self._random_delay(MIN_DELAY_AFTER_NAV, MAX_DELAY_AFTER_NAV)
        return result

    # ── Page inspection ──────────────────────────────────────────

    async def inspect(self, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        title = await p.title()
        url = p.url
        body = await p.inner_text("body")
        els = await p.locator(
            "button, a, input, textarea, select, [role=button], [role=link], [tabindex]"
        ).evaluate_all(
            "els => els.map((el,i) => ({"
            "uid:'n'+i, tag:el.tagName, type:el.type||'', "
            "text:(el.innerText||el.placeholder||el.value||'').trim().slice(0,40), "
            "href:el.href||'', "
            "aria:el.getAttribute('aria-label')||el.getAttribute('title')||''"
            "}))"
        )
        text = f"📄 Title: {title}\n🔗 URL: {url}\n\n"
        text += f"🔤 Body: {body[:500]}...\n\n"
        text += f"🔘 Elements ({len(els)}):\n"
        for b in els[:60]:
            text += f"  {b['uid']}: <{b['tag']}"
            if b.get("type"):
                text += f" type={b['type']}"
            text += f"> {b['text'][:30]}"
            if b.get("aria"):
                text += f"  [{b['aria'][:20]}]"
            if b.get("href"):
                text += f"  → {b['href'][:60]}"
            text += "\n"
        return {"text": text}

    # ── Click with human delay ───────────────────────────────────

    async def click(self, uid: str | None = None, selector: str | None = None, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        await self._random_delay(MIN_DELAY_BEFORE_CLICK, MAX_DELAY_BEFORE_CLICK)

        if selector:
            return await self._safe(p.locator(selector).click())
        if uid:
            idx = int(uid[1:]) if uid.startswith("n") else 0
            els = await p.locator("button, a, [role=button], input[type=submit]").all()
            if idx < len(els):
                return await self._safe(els[idx].click())
            return {"error": f"Element n{idx} not found"}
        return await self._safe(p.locator("button").first.click())

    # ── Human-like typing ────────────────────────────────────────

    async def fill(self, value: str, uid: str | None = None, selector: str | None = None, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}

        if selector:
            el = p.locator(selector)
        elif uid:
            idx = int(uid[1:]) if uid.startswith("n") else 0
            els = await p.locator("input, textarea, [contenteditable]").all()
            if idx >= len(els):
                return {"error": f"Input n{idx} not found"}
            el = els[idx]
        else:
            el = p.locator("input").first

        await el.click()
        await el.fill("")
        # Type character-by-character
        for char in value:
            await p.keyboard.type(char, delay=random.randint(MIN_TYPING_SPEED, MAX_TYPING_SPEED))
        await self._random_delay(0.2, 0.5)
        return {"text": f"typed {len(value)} chars"}

    # ── Data extraction ──────────────────────────────────────────

    async def extract(self, selector: str | None = None, limit: int = 20, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        sel = selector or "main, article, .results, table, [class*=result], [class*=card], li, .job-card"
        items = await p.locator(sel).all()
        results = []
        for item in items[:limit]:
            results.append(await item.inner_text())
        return {"text": json.dumps(results, indent=2) if results else "No matching elements"}

    async def text(self, uid: str | None = None, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        if uid:
            idx = int(uid[1:]) if uid.startswith("n") else 0
            els = await p.locator("*").all()
            if idx < len(els):
                return await self._safe(els[idx].inner_text())
        return {"text": (await p.inner_text("body"))[:4000]}

    async def read(self, **kwargs) -> dict[str, Any]:
        return await self.text(**kwargs)

    # ── Scrolling ────────────────────────────────────────────────

    async def scroll(self, target: str = "down", px: int = 500, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        delta = px if target == "down" else -px
        result = await self._safe(p.evaluate(f"window.scrollBy(0, {delta})"))
        await self._random_delay(MIN_SCROLL_PAUSE, MAX_SCROLL_PAUSE)
        return result

    # ── Screenshot ───────────────────────────────────────────────

    async def screenshot(self, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        path = f"/tmp/sba_shot_{self.workspace}.png"
        await p.screenshot(path=path)
        return {"text": f"screenshot: {path}"}

    # ── Wait conditions ──────────────────────────────────────────

    async def wait(self, what: str, pattern: str = "", **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        try:
            if what == "network-idle":
                await p.wait_for_load_state("networkidle", timeout=15000)
            elif what == "selector" and pattern:
                await p.wait_for_selector(pattern, timeout=15000)
            elif what == "text" and pattern:
                await p.wait_for_function(f'document.body.innerText.includes("{pattern}")', timeout=15000)
            return {"text": f"waited for {what}"}
        except Exception as e:
            return {"text": f"wait {what} done (timeout or success): {e}"}

    # ── Status ───────────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"text": "Chrome daemon: disconnected"}
        try:
            title = await p.title()
            url = p.url
            return {"text": f"✅ Connected | {title} | {url}"}
        except:
            return {"text": "Chrome daemon: connected (page pending)"}

    async def close(self, **kwargs) -> dict[str, Any]:
        self._page = None
        # Stop the playwright driver (Node subprocess) so no asyncio
        # transports leak at loop shutdown. The Chrome daemon itself is a
        # separate persistent process and stays alive for the next call.
        try:
            if self._play:
                await self._play.stop()
        except Exception:
            pass
        self._play = None
        self._browser = None
        return {"text": "page released"}

    async def eval(self, expression: str) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        return await self._safe(p.evaluate(expression))

    async def eval_json(self, expression: str) -> Any:
        """Evaluate JS and return the raw JSON-serializable value (not str)."""
        p = await self._ensure_page()
        if not p:
            return None
        try:
            return await p.evaluate(expression)
        except Exception as exc:
            logger.warning("Chrome eval_json failed: %s", exc)
            return None

    # ── Cookie management (LinkedIn/Upwork session persistence) ──

    async def save_cookies(self, path: str = COOKIE_FILE_PATH, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        try:
            ctx = self._browser.contexts[0] if self._browser and self._browser.contexts else None
            if not ctx:
                return {"error": "No browser context"}
            cookies = await ctx.cookies()
            with open(path, "w") as f:
                json.dump(cookies, f)
            return {"text": f"✅ Saved {len(cookies)} cookies → {path}"}
        except Exception as e:
            return {"error": f"Cookie save failed: {e}"}

    async def load_cookies(self, path: str = COOKIE_FILE_PATH, **kwargs) -> dict[str, Any]:
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        try:
            ctx = self._browser.contexts[0] if self._browser and self._browser.contexts else None
            if not ctx:
                return {"error": "No browser context"}
            with open(path) as f:
                cookies = json.load(f)
            await ctx.add_cookies(cookies)
            return {"text": f"✅ Loaded {len(cookies)} cookies from {path}"}
        except Exception as e:
            return {"error": f"Cookie load failed: {e}"}

    # ── Platform-specific helpers ────────────────────────────────

    async def linkedin_login(self, email: str, password: str) -> dict[str, Any]:
        """Login to LinkedIn and save cookies."""
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}

        await p.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        await self._random_delay(1, 2)

        # Try cookies first
        ctx = self._browser.contexts[0] if self._browser and self._browser.contexts else None
        cookie_file = f"/tmp/sba_cookies_{self.workspace}.json"
        if os.path.exists(cookie_file):
            try:
                with open(cookie_file) as f:
                    cookies = json.load(f)
                if ctx:
                    await ctx.add_cookies(cookies)
                await p.goto("https://www.linkedin.com/feed", wait_until="domcontentloaded")
                if "feed" in p.url:
                    return {"text": "✅ LinkedIn already logged in (via cookies)"}
            except:
                pass

        # Fresh login
        await self._random_delay(1, 2)
        await p.locator("#username").fill(email, delay=random.randint(60, 150))
        await self._random_delay(0.5, 1.5)
        await p.locator("#password").fill(password, delay=random.randint(60, 150))
        await self._random_delay(0.5, 1)
        await p.locator("[type=submit]").click()
        await self._random_delay(3, 5)

        # Save cookies
        if ctx:
            cookies = await ctx.cookies()
            with open(cookie_file, "w") as f:
                json.dump(cookies, f)
            return {"text": f"✅ LinkedIn logged in, {len(cookies)} cookies saved"}

        return {"text": "✅ LinkedIn login attempted"}

    async def facebook_login(self, email: str, password: str) -> dict[str, Any]:
        """Login to Facebook and save cookies (Groups/Marketplace session).

        Client gives email/password once; cookies persist so organic posts
        reuse the session. Mirrors linkedin_login.
        """
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}

        await p.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        await self._random_delay(1, 2)

        ctx = self._browser.contexts[0] if self._browser and self._browser.contexts else None
        cookie_file = f"/tmp/sba_fb_cookies_{self.workspace}.json"

        # Try cookies first
        if os.path.exists(cookie_file):
            try:
                with open(cookie_file) as f:
                    cookies = json.load(f)
                if ctx:
                    await ctx.add_cookies(cookies)
                await p.goto("https://www.facebook.com/", wait_until="domcontentloaded")
                await self._random_delay(2, 3)
                if "login" not in p.url:
                    return {"text": "✅ Facebook already logged in (via cookies)"}
            except Exception:  # noqa: BLE001
                pass

        # Fresh login
        await self._random_delay(1, 2)
        email_el = p.locator("#email")
        pass_el = p.locator("#pass")
        await email_el.fill(email, delay=random.randint(60, 150))
        await self._random_delay(0.5, 1.5)
        await pass_el.fill(password, delay=random.randint(60, 150))
        await self._random_delay(0.5, 1)
        await p.locator("[name=login]").click()
        await self._random_delay(3, 5)

        if "login" in p.url:
            return {"error": "Facebook login failed (wrong email/password or checkpoint). Check the account."}

        # Save cookies
        if ctx:
            cookies = await ctx.cookies()
            with open(cookie_file, "w") as f:
                json.dump(cookies, f)
            return {"text": f"✅ Facebook logged in, {len(cookies)} cookies saved"}
        return {"text": "✅ Facebook login attempted"}

    # ── Stub methods ─────────────────────────────────────────────

    async def back(self) -> dict[str, Any]:
        p = await self._ensure_page()
        if p: await p.go_back()
        return {"text": "ok"}

    async def forward(self) -> dict[str, Any]:
        return {"text": "ok"}

    async def fill_form(self, pairs, **kwargs):
        return await self.fill(pairs[0][1] if pairs else "")

    async def select(self, value, **kwargs):
        return {"text": "selected"}

    async def check(self, **kwargs):
        return {"text": "checked"}

    async def uncheck(self, **kwargs):
        return {"text": "unchecked"}

    async def type_text(self, text, **kwargs) -> dict[str, Any]:
        return await self.fill(text)

    async def upload(self, path: str, selector: str = "input[type=file]", **kwargs) -> dict[str, Any]:
        """Upload a local file into a file input (marketplace photos, group images)."""
        p = await self._ensure_page()
        if not p:
            return {"error": "Chrome daemon unavailable"}
        if not os.path.exists(path):
            return {"error": f"File not found: {path}"}
        return await self._safe(p.locator(selector).set_input_files(path))

    async def press(self, key: str) -> dict[str, Any]:
        p = await self._ensure_page()
        if p: await p.keyboard.press(key)
        return {"text": f"pressed {key}"}

    async def hover(self, uid: str) -> dict[str, Any]:
        return {"text": "hovered"}

    async def network(self, **kwargs) -> dict[str, Any]:
        return {"text": "n/a"}

    async def tabs(self) -> dict[str, Any]:
        return {"text": "single tab"}


# ── OpenAI function-calling tool definitions ─────────────────────

CHROME_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "chrome_goto",
            "description": "Navigate Chrome to a URL. Use to visit LinkedIn, Upwork, Fiverr, or any lead source.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_inspect",
            "description": "See all interactive elements (buttons, links, inputs) with their UIDs on the current page. Always inspect before clicking or filling.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_click",
            "description": "Click an element by UID (e.g. 'n47'). Has human-like pre-click delay. Always inspect first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Element UID e.g. 'n47'"},
                    "selector": {"type": "string", "description": "CSS selector alternative"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_fill",
            "description": "Type text into an input field. Types character-by-character like a human.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "description": "Text to type"},
                    "uid": {"type": "string", "description": "Element UID"},
                    "selector": {"type": "string", "description": "CSS selector"},
                },
                "required": ["value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_extract",
            "description": "Extract data from repeating elements — search results, job listings, profile cards.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS scope for results"},
                    "limit": {"type": "integer", "description": "Max items (default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_text",
            "description": "Read visible text from the page or a specific element.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Element UID (omit for full page)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_read",
            "description": "Read the main page content.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_scroll",
            "description": "Scroll down/up. Use on LinkedIn feed, Upwork search results, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "enum": ["down", "up"], "default": "down"},
                    "px": {"type": "integer", "default": 500},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_screenshot",
            "description": "Capture a screenshot of the current page.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_wait",
            "description": "Wait for text, selector, or network idle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "what": {"type": "string", "enum": ["text", "selector", "network-idle"]},
                    "pattern": {"type": "string"},
                },
                "required": ["what"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_status",
            "description": "Check if Chrome is connected and what page is loaded.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_save_cookies",
            "description": "Save cookies to disk. Call after LinkedIn/Upwork login to persist session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "/tmp/sba_cookies.json"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_load_cookies",
            "description": "Load saved cookies to restore logged-in session. Call before navigating to LinkedIn/Upwork/Fiverr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "/tmp/sba_cookies.json"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_linkedin_login",
            "description": "Login to LinkedIn with email/password and save cookies for future sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "LinkedIn email"},
                    "password": {"type": "string", "description": "LinkedIn password"},
                },
                "required": ["email", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_close",
            "description": "Release the current page (daemon stays running).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

SBA_LEAD_SOURCE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "sba_find_leads",
            "description": "Find local businesses WITHOUT a website from one platform (google_maps, yelp, yellowpages, bing_maps, facebook_pages).",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["google_maps", "yelp", "yellowpages", "bing_maps", "facebook_pages"]},
                    "category": {"type": "string"},
                    "city": {"type": "string"},
                    "state": {"type": "string"},
                    "max_candidates": {"type": "integer"},
                },
                "required": ["source", "category", "city", "state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sba_find_leads_all",
            "description": "Find local businesses WITHOUT a website from ALL platforms at once (Google Maps, Yelp, YellowPages, Bing, Facebook).",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "city": {"type": "string"},
                    "state": {"type": "string"},
                    "max_per_source": {"type": "integer"},
                },
                "required": ["category", "city", "state"],
            },
        },
    },
]

# Re-assign so lead-source tools ride along with the chrome toolset everywhere
# it is imported (SBA chat agent, workspace agents, etc.).
CHROME_TOOLS = [*CHROME_TOOLS, *SBA_LEAD_SOURCE_TOOLS]

CHROME_TOOL_DISPATCH: dict[str, str] = {
    "chrome_goto": "goto",
    "chrome_inspect": "inspect",
    "chrome_click": "click",
    "chrome_fill": "fill",
    "chrome_extract": "extract",
    "chrome_text": "text",
    "chrome_read": "read",
    "chrome_scroll": "scroll",
    "chrome_screenshot": "screenshot",
    "chrome_wait": "wait",
    "chrome_status": "status",
    "chrome_save_cookies": "save_cookies",
    "chrome_load_cookies": "load_cookies",
    "chrome_linkedin_login": "linkedin_login",
    "chrome_close": "close",
}


async def execute_chrome_tool(tool_name: str, tool_args: dict[str, Any], chrome: ChromeTool) -> str:
    """Execute a chrome tool call and return a string result for the LLM."""
    if tool_name == "sba_find_leads":
        from admin.tools.sba_lead_sources import find_leads
        leads = await find_leads(
            tool_args.get("source", "google_maps"),
            tool_args.get("category", ""),
            tool_args.get("city", ""),
            tool_args.get("state", ""),
            max_candidates=int(tool_args.get("max_candidates", 10)),
            chrome=chrome,
        )
        return json.dumps(leads, ensure_ascii=False, default=str)[:4000]
    if tool_name == "sba_find_leads_all":
        from admin.tools.sba_lead_sources import find_leads_all
        leads = await find_leads_all(
            tool_args.get("category", ""),
            tool_args.get("city", ""),
            tool_args.get("state", ""),
            max_per_source=int(tool_args.get("max_per_source", 5)),
            chrome=chrome,
        )
        return json.dumps(leads, ensure_ascii=False, default=str)[:4000]
    method_name = CHROME_TOOL_DISPATCH.get(tool_name)
    if not method_name:
        raise ChromeToolError(f"Unknown chrome tool: {tool_name}")
    method = getattr(chrome, method_name, None)
    if not method:
        raise ChromeToolError(f"chrome tool '{tool_name}' not implemented")
    try:
        result = await method(**tool_args)
    except Exception as exc:
        logger.exception("Chrome tool %s failed", tool_name)
        return f"Error executing {tool_name}: {exc}"
    if "error" in result:
        return f"[chrome-{method_name}] Error: {result['error']}"
    text = result.get("text") or result.get("content") or json.dumps(result, indent=2)
    if len(text) > 5000:
        text = text[:5000] + "\n... [truncated]"
    return f"[chrome-{method_name}]\n{text}"
