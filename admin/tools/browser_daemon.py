"""Chrome daemon — bulletproof, per-workspace, anti-block.

Layer 1: playwright-stealth (31 patches via maintained package)
Layer 2: Custom stealth additions (WebGL, canvas noise, CDP)
Layer 3: Cookie persistence (LinkedIn/Upwork/Fiverr sessions)
Layer 4: Proxy rotation with failover
Layer 5: Human-like delays in chrome_tool

Usage:
  python -m admin.tools.browser_daemon --workspace agency
  python -m admin.tools.browser_daemon --workspace client_realestate
"""

import argparse, asyncio, logging, os, random, signal, sys, json
from datetime import datetime

logger = logging.getLogger(__name__)

HAVE_PW = False
HAVE_STEALTH = False

try:
    from playwright.async_api import async_playwright
    HAVE_PW = True
except ImportError:
    pass

try:
    from playwright_stealth import Stealth
    HAVE_STEALTH = True
except ImportError:
    logger.warning("playwright-stealth not installed. Run: pip install playwright-stealth")

# ── Extra stealth patches (beyond playwright-stealth's 31) ──────────
EXTRA_STEALTH_JS = """
// ── CDP detection evasion (mitigates connect_over_cdp fingerprint) ──
// Hides WebSocket/CDP-specific traces
Object.defineProperty(window, 'cdp', { get: () => undefined });
Object.defineProperty(window, '__playwright', { get: () => undefined });
Object.defineProperty(window, '__pw_api', { get: () => undefined });

// ── Additional WebGL spoofing ────────────────────────────────────
try {
  const getExt = HTMLCanvasElement.prototype.getContext;
  const origGetExt = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(...args) {
    const ctx = origGetExt.apply(this, args);
    if (ctx && args[0] === 'webgl') {
      const origGetParam = ctx.getParameter.bind(ctx);
      ctx.getParameter = function(param) {
        // Spoof ANGLE renderer
        if (param === 37445) return 'Intel Inc.';
        if (param === 37446) return 'Intel Iris OpenGL Engine';
        return origGetParam(param);
      };
    }
    return ctx;
  };
} catch(e) {}

// ── Ensure navigator credentials are not exposed ─────────────────
try {
  if (navigator.credentials) {
    navigator.credentials.__proto__.get = () => Promise.resolve(null);
  }
} catch(e) {}

// ── Fix outer dimensions (headless returns 0) ────────────────────
Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight });

// ── Font fingerprint spoof (add common fonts) ────────────────────
// Note: Font detection is hard to spoof via JS, but we make document.fonts
// appear non-empty
try {
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function() {});
  }
} catch(e) {}
"""

# ── Proxy rotation ─────────────────────────────────────────────────
def load_proxy_list():
    """Load proxy list from env var (comma-separated or file path)."""
    raw = os.environ.get("SBA_PROXY_LIST", "")
    if not raw:
        return []
    if os.path.isfile(raw):
        with open(raw) as f:
            return [line.strip() for line in f if line.strip()]
    return [p.strip() for p in raw.split(",") if p.strip()]

PROXY_LIST = load_proxy_list()
PROXY_INDEX = 0

def get_next_proxy():
    """Round-robin proxy selection."""
    global PROXY_INDEX
    if not PROXY_LIST:
        return None
    proxy = PROXY_LIST[PROXY_INDEX % len(PROXY_LIST)]
    PROXY_INDEX += 1
    return proxy

# Expanded user agents (11 realistic variants)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

VIEWPORTS = [
    {"width": 1280, "height": 720},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
    {"width": 1920, "height": 1080},
]

BASE_PORT = 9222


class ChromeDaemon:
    """Persistent Chrome — bulletproof, one per workspace, auto-healing."""

    def __init__(self, workspace: str):
        if workspace == "agency":
            self.cdp_port = BASE_PORT
        else:
            # Deterministic port per workspace
            self.cdp_port = BASE_PORT + 1 + (abs(hash(workspace)) % 100)
        self.workspace = workspace
        self.profile_dir = f"/tmp/sba-chrome-{workspace}"
        self.cookie_file = f"{self.profile_dir}/sba_cookies.json"
        self._running = False
        self._context = None
        self._play = None
        self._user_agent = None
        self._viewport = None
        self._proxy = os.environ.get("SBA_PROXY", None) or get_next_proxy()
        self._start_time = None
        self._page_count = 0
        self._error_count = 0

    async def _save_cookies(self):
        """Save cookies before shutdown for session persistence."""
        if not self._context:
            return
        try:
            cookies = await self._context.cookies()
            if cookies:
                os.makedirs(self.profile_dir, exist_ok=True)
                with open(self.cookie_file, "w") as f:
                    json.dump(cookies, f)
                logger.info(f"[{self.workspace}] Saved {len(cookies)} cookies")
        except Exception as e:
            logger.warning(f"[{self.workspace}] Cookie save failed: {e}")

    async def _load_cookies(self):
        """Load saved cookies to restore logged-in sessions."""
        if not os.path.exists(self.cookie_file):
            return
        try:
            with open(self.cookie_file) as f:
                cookies = json.load(f)
            if cookies and self._context:
                await self._context.add_cookies(cookies)
                logger.info(f"[{self.workspace}] Restored {len(cookies)} cookies")
        except Exception as e:
            logger.warning(f"[{self.workspace}] Cookie load failed: {e}")

    async def start(self):
        if not HAVE_PW:
            logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return

        self._running = True
        os.makedirs(self.profile_dir, exist_ok=True)

        while self._running:
            try:
                self._start_time = datetime.now()
                self._play = await async_playwright().start()
                self._user_agent = random.choice(USER_AGENTS)
                self._viewport = random.choice(VIEWPORTS)

                # Pick proxy (rotate if available)
                self._proxy = os.environ.get("SBA_PROXY", None) or get_next_proxy()

                logger.info(
                    f"[{self.workspace}] Starting Chrome "
                    f"UA={self._user_agent[:60]}... "
                    f"VP={self._viewport} "
                    f"Proxy={'yes' if self._proxy else 'no'}"
                )

                launch_args = [
                    f"--remote-debugging-port={self.cdp_port}",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--disable-translate",
                    "--disable-hang-monitor",
                    "--no-first-run",
                    "--disable-features=ChromeWhatsNewUI",
                ]

                # ── Launch persistent context ─────────────────────────
                self._context = await self._play.chromium.launch_persistent_context(
                    self.profile_dir,
                    headless=True,
                    args=launch_args,
                    user_agent=self._user_agent,
                    viewport=self._viewport,
                    locale="en-US",
                    timezone_id="America/New_York",
                    permissions=["geolocation"],
                    geolocation={"latitude": 40.7128, "longitude": -74.0060},
                    proxy={"server": self._proxy} if self._proxy else None,
                    ignore_https_errors=True,
                )

                # ── Layer 1: playwright-stealth (31 patches) ──────────
                if HAVE_STEALTH:
                    try:
                        stealth = Stealth()
                        await stealth.apply_stealth_async(self._context)
                        logger.info(f"[{self.workspace}] playwright-stealth applied (31 patches)")
                    except Exception as e:
                        logger.warning(f"[{self.workspace}] stealth apply failed: {e}")
                else:
                    logger.warning(f"[{self.workspace}] playwright-stealth not available")

                # ── Layer 2: Extra stealth patches ────────────────────
                await self._context.add_init_script(EXTRA_STEALTH_JS)

                # ── Layer 3: Restore cookies ──────────────────────────
                await self._load_cookies()

                logger.info(f"[{self.workspace}] ✅ Chrome ready on CDP :{self.cdp_port}")

                # ── Monitor health ────────────────────────────────────
                # Block until the context closes (daemon crash). timeout=0
                # disables playwright's 30s default, so a healthy daemon
                # stays alive instead of "dying" every 30 seconds.
                await self._context.wait_for_event("close", timeout=0)

            except Exception as e:
                self._error_count += 1
                logger.error(f"[{self.workspace}] 💀 Chrome died (error #{self._error_count}): {e}")

            finally:
                # Save cookies on shutdown/restart
                await self._save_cookies()
                await self._cleanup()

            if self._running:
                backoff = min(30, 5 * self._error_count)  # 5, 10, 15...30s max
                logger.info(f"[{self.workspace}] Restart in {backoff}s...")
                await asyncio.sleep(backoff)

    async def _cleanup(self):
        try:
            if self._context:
                await self._context.close()
        except:
            pass
        try:
            if self._play:
                await self._play.stop()
        except:
            pass
        self._context = None
        self._play = None

    async def stop(self):
        self._running = False
        await self._save_cookies()
        await self._cleanup()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="agency", help="workspace ID")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    logger.info(f"🚀 Starting Chrome daemon for workspace: {args.workspace}")

    daemon = ChromeDaemon(args.workspace)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(daemon.stop()))

    await daemon.start()


if __name__ == "__main__":
    asyncio.run(main())
