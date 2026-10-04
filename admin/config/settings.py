"""Central configuration for the TAGS Agency backend."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# Load .env from project root (e.g. C:\Users\TAUSHEF\Downloads\int\.env)
load_dotenv(PROJECT_ROOT / ".env")

ADMIN_ROOT = PROJECT_ROOT / "admin"

# ── Free provider defaults ─────────────────────────────────────────────────
#
# Default models fall back to Groq's free tier only when .env sets nothing:
#   Groq →  groq/llama-3.3-70b-versatile  (fast, free, ~30 req/min)
#   Sign up: https://console.groq.com  →  copy your GROQ_API_KEY
#
# PRODUCTION (EC2) and this machine use the owner's OpenAI-compatible key:
#   base  = https://opencode.ai/zen/v1   (OPENCODE_ZEN_API_KEY / WORKSPACE_API_KEY)
#   model = big-pickle
# See .env - those values override the defaults below.
#
# Other free options (swap in .env / env vars):
#   Google Gemini  →  gemini/gemini-2.0-flash          (API key: GEMINI_API_KEY)
#   OpenRouter     →  openrouter/meta-llama/llama-3.3-70b-instruct  (OPENROUTER_API_KEY)
#   GitHub Models  →  github/gpt-4o-mini                         (GITHUB_TOKEN)
#
# Override everyhing via environment variables or .env file.

# ── Agency CEO model ──────────────────────────────────────────────────────
# Uses OpenAI-compatible client directly. Works with:
#   Groq API   → base_url = https://api.groq.com/openai/v1   model = llama-3.3-70b-versatile
#   OpenAI     → base_url = (empty)                           model = gpt-4o
#   OpenRouter → base_url = https://openrouter.ai/api/v1      model = meta-llama/llama-3.3-70b-instruct
AGENCY_CEO_MODEL = os.getenv("AGENCY_CEO_MODEL", "llama-3.3-70b-versatile")
AGENCY_CEO_API_KEY = os.getenv("AGENCY_CEO_API_KEY", "")
AGENCY_CEO_API_BASE = os.getenv("AGENCY_CEO_API_BASE", "")

# ── Workspace (per‑client) agent model ─────────────────────────────────────
WORKSPACE_AGENT_MODEL = os.getenv("WORKSPACE_AGENT_MODEL", "llama-3.3-70b-versatile")
WORKSPACE_API_KEY = os.getenv("WORKSPACE_API_KEY", "")
WORKSPACE_API_BASE = os.getenv("WORKSPACE_API_BASE", "")

# ── Server ─────────────────────────────────────────────────────────────────
HOST = os.getenv("ADMIN_HOST", "0.0.0.0")
PORT = int(os.getenv("ADMIN_PORT", "9002"))

# ── EC2 backend (frontend proxy target) ────────────────────────────────────
EC2_BACKEND_URL = os.getenv("EC2_BACKEND_URL", "http://18.213.66.136:8000")

# ── Client dashboard (store admin) base URL ─────────────────────────────────
# The dashboard is a Vercel app; each client's admin lives at /store/<workspace>.
STORE_DASHBOARD_BASE_URL = os.getenv("STORE_DASHBOARD_BASE_URL", "https://agency-frontend-seven.vercel.app")

# ── Chrome-agent (SBA's dedicated browser) ─────────────────────────────────
CHROME_AGENT_PATH = os.getenv(
    "CHROME_AGENT_PATH",
    str(ADMIN_ROOT.parent / "chrome-agent" / "target" / "release" / "chrome-agent.exe"),
)
CHROME_AGENT_BROWSER = os.getenv("CHROME_AGENT_BROWSER", "sba")
CHROME_AGENT_STEALTH = os.getenv("CHROME_AGENT_STEALTH", "true").lower() in ("1", "true", "yes")

# ── Database Configuration ─────────────────────────────────────────────────────
# Turso (libSQL) takes precedence if set, then Render PostgreSQL, then local SQLite
TURSO_DATABASE_URL: str = os.getenv("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN: str = os.getenv("TURSO_AUTH_TOKEN", "")

# Build DATABASE_URL based on available configuration
if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
    # Turso libSQL database.
    #
    # The host must sit in the SQLAlchemy authority. The older form,
    # f"sqlite+libsql://{TURSO_DATABASE_URL}?authToken=...", produces
    # "sqlite+libsql://libsql://my-db.turso.io?authToken=..." because
    # TURSO_DATABASE_URL already carries its own scheme, and
    # sqlalchemy.engine.make_url rejects that outright with
    # "invalid literal for int() with base 10: ''". admin/database.py builds
    # its engine from this at module scope, so the old shape killed the whole
    # backend the moment TURSO_* was set. That is the most likely reason those
    # variables stayed empty on Render.
    _turso_host = TURSO_DATABASE_URL.split("://", 1)[-1].split("/", 1)[0]
    DATABASE_URL = f"sqlite+libsql://{_turso_host}?authToken={TURSO_AUTH_TOKEN}"
elif os.getenv("RENDER_POSTGRES_URL"):
    # Render PostgreSQL (when deployed on Render)
    DATABASE_URL = os.getenv("RENDER_POSTGRES_URL")
else:
    # Default to SQLite local DB for development
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./tags_agency.db",
    )

# ── Workspace store backend (admin/persistence.py) ──────────────────────────
# The CEO brain/memory store. Values:
#   "auto"   -> Turso/libSQL when TURSO_DATABASE_URL is set, local SQLite
#               otherwise. This is the default, and it is what keeps local dev
#               at zero configuration.
#   "turso"  -> demand Turso. Boot fails if TURSO_DATABASE_URL is missing.
#               Use this on Render so a forgotten env var is a crash, not a
#               silent fall back to a file the next deploy deletes.
#   "sqlite" -> pin the local file even when TURSO_* is set. Dev only.
WORKSPACE_DB_BACKEND: str = (
    os.getenv("AGENCY_WORKSPACE_DB_BACKEND", "auto").strip().lower() or "auto"
)

# Local SQLite file used when WORKSPACE_DB_BACKEND resolves to "sqlite".
# Empty means "the repository default", which lives on an ephemeral disk when
# this backend runs on Render's free plan.
WORKSPACE_DB_SQLITE_PATH: str = os.getenv("AGENCY_WORKSPACE_DB_PATH", "").strip()

# Full dialect URL override for the workspace store. Normally built from
# TURSO_DATABASE_URL + TURSO_AUTH_TOKEN. Set this only if your libSQL driver
# expects a different URL shape than the one built here; the effective URL is
# logged (token redacted) at boot so a mismatch is visible immediately.
WORKSPACE_DB_URL: str = os.getenv("AGENCY_WORKSPACE_DB_URL", "").strip()


# ── Multi‑phase thinking loop steps ───────────────────────────────────────
CEO_THINKING_PHASES = [
    "deconstruct",   # Break the request into atomic components
    "seek",          # Gather context, recall past decisions
    "envision",      # Imagine possible approaches
    "analyse",       # Evaluate trade-offs for each approach
    "plan",          # Produce a structured plan
    "execute",       # Generate the final output / delegations
]

# ── Real-tool runtime (E2B sandbox + Composio integrations) ────────────
# Per-agent, per-workspace isolation layer. All OFFLINE-SAFE: without these
# keys the system falls back to a local sandbox and reports integrations as
# unavailable instead of crashing. Drop the keys in to go fully live.
E2B_API_KEY: str = os.getenv("E2B_API_KEY", "")
E2B_TEMPLATE_ID: str = os.getenv("E2B_TEMPLATE_ID", "")
COMPOSIO_API_KEY: str = os.getenv("COMPOSIO_API_KEY", "")
# Owner-tunable spend-policy envelope (borrowed from automaton design):
# cap external writes per hour and optionally allow HIGH-risk actions.
AGENT_MAX_EXTERNAL_PER_HOUR: int = int(os.getenv("AGENT_MAX_EXTERNAL_PER_HOUR", "25"))
AGENT_ALLOW_HIGH_RISK: bool = os.getenv("AGENT_ALLOW_HIGH_RISK", "false").lower() in ("1", "true", "yes")

# ── SBA Email (Gmail App Password) ──────────────────────────────────────────
# Owner's email credentials for SBA to send/receive lead emails.
# Uses App Password (NOT regular password):
#   Google → Manage Account → Security → 2-Step Verification → App Passwords
SBA_OWNER_EMAIL: str = os.getenv("SBA_OWNER_EMAIL", "")
SBA_OWNER_EMAIL_PASSWORD: str = os.getenv("SBA_OWNER_EMAIL_PASSWORD", "")
SBA_OWNER_NAME: str = os.getenv("SBA_OWNER_NAME", "Ayan")
SBA_SMTP_HOST: str = os.getenv("SBA_SMTP_HOST", "smtp.gmail.com")
SBA_SMTP_PORT: int = int(os.getenv("SBA_SMTP_PORT", "587"))
SBA_IMAP_HOST: str = os.getenv("SBA_IMAP_HOST", "imap.gmail.com")
SBA_IMAP_PORT: int = int(os.getenv("SBA_IMAP_PORT", "993"))

# ── SBA 24/7 Autopilot ────────────────────────────────────────────
SBA_OWNER_TIMEZONE: str = os.getenv("SBA_OWNER_TIMEZONE", "Asia/Kolkata")
SBA_AUTOPILOT_INTERVAL_MINUTES: int = int(os.getenv("SBA_AUTOPILOT_INTERVAL_MINUTES", "15"))
SBA_DAILY_EMAIL_CAP: int = int(os.getenv("SBA_DAILY_EMAIL_CAP", "30"))

# ── SBA Proactive Follow-up (non-responder re-engagement) ─────────
# OFF by default. Mass re-emailing non-responders is a high-impact action
# (prompt #14), so the autopilot NEVER sends follow-ups unless the owner
# explicitly opts in via .env / env var. When ON, only 'contacted' leads that
# have NOT replied after FOLLOWUP_MIN_DAYS are touched, under a bounded
# multi-touch cadence (FOLLOWUP_TOUCHES separate sends, each after its own
# gap), each inside business hours, under the same daily/SMTP caps. Once a
# lead has received all its touches it leaves the follow-up pool permanently.
# A proposed meeting slot can optionally be suggested in the email
# (FOLLOWUP_SUGGEST_CALENDAR).
SBA_FOLLOWUP_ENABLED: bool = os.getenv("SBA_FOLLOWUP_ENABLED", "false").lower() in ("1", "true", "yes")
SBA_FOLLOWUP_MIN_DAYS: int = int(os.getenv("SBA_FOLLOWUP_MIN_DAYS", "4"))
SBA_FOLLOWUP_MAX_PER_PASS: int = int(os.getenv("SBA_FOLLOWUP_MAX_PER_PASS", "10"))
SBA_FOLLOWUP_TOUCHES: int = int(os.getenv("SBA_FOLLOWUP_TOUCHES", "1"))
SBA_FOLLOWUP_GAP_DAYS: int = int(os.getenv("SBA_FOLLOWUP_GAP_DAYS", "7"))
SBA_FOLLOWUP_SUGGEST_CALENDAR: bool = os.getenv("SBA_FOLLOWUP_SUGGEST_CALENDAR", "false").lower() in ("1", "true", "yes")

# Lowercase aliases (consumed by tests and some call sites).
sba_owner_timezone: str = SBA_OWNER_TIMEZONE
sba_autopilot_interval_minutes: int = SBA_AUTOPILOT_INTERVAL_MINUTES
sba_daily_email_cap: int = SBA_DAILY_EMAIL_CAP

# ── Always-on Agency Agent Loop ─────────────────────────────────────
# Backend background loop that runs due scheduled tasks (SEO/Website/Ads/
# Analytics/Analyzing) and auto-provisions client workspaces from SBA
# handoffs. This is what makes the specialist agents self-scheduled (L2).
# The SBA 24/7 email autopilot is a SEPARATE process and is not affected.
AGENCY_AGENT_LOOP_INTERVAL_SECONDS: int = int(
    os.getenv("AGENCY_AGENT_LOOP_INTERVAL_SECONDS", "60")
)
AGENCY_AGENT_LOOP_TICK_TIMEOUT_SECONDS: int = int(
    os.getenv("AGENCY_AGENT_LOOP_TICK_TIMEOUT_SECONDS", "120")
)

# ── Telegram Bot ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_WEBHOOK_URL: str = os.getenv("TELEGRAM_WEBHOOK_URL", "")

# ── External PocketBase (survives container restart) ───────────────────────
# Owner rule: PocketBase is THE database for key agency state (workspaces,
# custom agents, agent outputs, CEO lifecycle/error state). When POCKETBASE_URL
# is set, those writes ALSO mirror to PocketBase so they survive an App Runner
# container restart (local SQLite/file state does not). Leave empty to keep pure
# local behaviour. All PB writes are best-effort and never break local runs.
POCKETBASE_URL: str = os.getenv("POCKETBASE_URL", "")
POCKETBASE_ADMIN_EMAIL: str = os.getenv("POCKETBASE_ADMIN_EMAIL", "")
POCKETBASE_ADMIN_PASSWORD: str = os.getenv("POCKETBASE_ADMIN_PASSWORD", "")
