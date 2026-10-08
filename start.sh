#!/usr/bin/env bash
# Combined boot: FreeLLMAPI (Node, internal :3001) + FastAPI backend (public $PORT).
# Runs as PID 1's child (Render runs the container CMD directly); traps SIGTERM.
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

FREEAPI_PORT="${FREEAPI_PORT:-3001}"
export PORT="${PORT:-7860}"

ENCRYPTION_KEY="${ENCRYPTION_KEY:-}"          # 64-hex; set in Render env
FREEAPI_ADMIN_EMAIL="${FREEAPI_ADMIN_EMAIL:-}"
FREEAPI_ADMIN_PASSWORD="${FREEAPI_ADMIN_PASSWORD:-}"
UNIFIED_API_KEY="${UNIFIED_API_KEY:-}"        # e.g. freellmapi-<48 hex>
PROVIDER_KEYS_JSON="${PROVIDER_KEYS_JSON:-}"  # [{"platform":"groq","key":"gsk_..."}, ...]
CUSTOM_ENDPOINTS_JSON="${CUSTOM_ENDPOINTS_JSON:-}"  # custom OpenAI-compatible providers

# ── DB restore from GitHub data branch (before node boots: freeapi.db must
# exist before the FreeLLMAPI server first-open/migrates a fresh one) ─────────
# GH_BACKUP_TOKEN must stay defaulted. Under `set -u` a bare $GH_BACKUP_TOKEN
# is fatal, and this script runs with set -u on line 4: the whole container
# exited with status 1 at boot, taking the API and the gateway down with it,
# because one OPTIONAL feature's credential was absent. Every other env var
# here already carries a :- default; this one did not. A missing backup token
# must degrade to "no backups", never to "no service".
if [ -n "${GH_BACKUP_TOKEN:-}" ]; then
  echo "[start] restoring DBs from backup branch"
  # Hard wall-clock bound. Each GitHub call inside restore has its own 120s
  # timeout and restore makes several in sequence, so an API that is slow or
  # rate-limiting this IP can spend many minutes in here. Every deploy then
  # failed with update_failed: the image built, then the container sat in this
  # step until the health check on /api/health gave up, so it never bound its
  # port. An empty database is recoverable; a container that never starts is
  # not. Boot continues either way.
  timeout "${DB_RESTORE_TIMEOUT:-120}" python -m admin.db_backup restore \
    || echo "[start] WARN: db restore did not finish in ${DB_RESTORE_TIMEOUT:-120}s (boot continues)"
else
  echo "[start] WARN: GH_BACKUP_TOKEN unset - DB backups off, and any previously"
  echo "[start]       backed-up data (LLM provider keys, unified key) is NOT restored."
  echo "[start]       The gateway will start with an empty database."
fi

# ── DB diagnostic ──────────────────────────────────────────────────────────
# The branch snapshots decrypt cleanly but are not SQLite files, while the same
# databases are valid locally. The difference is inside the container, and the
# container is the only place that can see it: the Render REST API does not
# return logs and nobody is watching the dashboard at boot. So the container
# reports its own state to the data branch (db_diagnose.txt) and to stdout.
#
# Non-fatal by construction. This must never be a reason the service fails to
# come up.
timeout "${DB_DIAGNOSE_TIMEOUT:-60}" python -m admin.db_diagnose \
  || echo "[start] WARN: db_diagnose did not finish (boot continues)"

# ── KV restore: download workspace DB from Cloudflare KV if available ────────
# Render FREE has an ephemeral disk — every deploy wipes the local SQLite file.
# Cloudflare D1 is blocked from Render IPs (403 error 1010). KV has no IP blocks.
# This restore runs BEFORE the app boots, so the CEO's memory survives deploys.
if [ -n "${CF_KV_TOKEN:-}" ]; then
  echo "[start] restoring workspace DB from Cloudflare KV"
  timeout "${KV_RESTORE_TIMEOUT:-60}" python "$ROOT/kv_backup.py" restore \
    || echo "[start] WARN: KV restore did not finish (boot continues)"
else
  echo "[start] WARN: CF_KV_TOKEN unset - workspace DB will be empty on each deploy"
fi

echo "[start] booting FreeLLMAPI on internal port $FREEAPI_PORT"

# HOST=127.0.0.1 keeps the proxy loopback-only so Render detects only the
# FastAPI port as public (healthCheckPath hits the backend, not the proxy).
NODE_ENV=production HOST=127.0.0.1 PORT="$FREEAPI_PORT" node server/dist/index.js > /var/log/freeapi.log 2>&1 &
FREEAPI_PID=$!
echo "[start] FreeLLMAPI pid=$FREEAPI_PID (port $FREEAPI_PORT)"

# Wait for the proxy (max ~30s).
up=0
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$FREEAPI_PORT/api/auth/status" > /dev/null 2>&1; then
    echo "[start] FreeLLMAPI up after ~$((i/2))s"
    up=1
    break
  fi
  sleep 0.5
done
if [ "$up" != "1" ]; then
  echo "[start] WARN: FreeLLMAPI did not answer /api/auth/status; continuing (backend may fall back to direct LLM)"
  echo "[start] freeapi tail:"; tail -n 20 /var/log/freeapi.log || true
fi

# ── Seed: account + unified key + provider keys + custom endpoints ────────────
seed_token=""
if [ -n "$FREEAPI_ADMIN_EMAIL" ] && [ -n "$FREEAPI_ADMIN_PASSWORD" ]; then
  seed_token="$(curl -fsS -X POST "http://127.0.0.1:$FREEAPI_PORT/api/auth/setup" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"$FREEAPI_ADMIN_EMAIL\",\"password\":\"$FREEAPI_ADMIN_PASSWORD\"}" \
    | sed -n 's/.*"token":"\([^"]*\)".*/\1/p' || true)"
  if [ -z "$seed_token" ]; then
    seed_token="$(curl -fsS -X POST "http://127.0.0.1:$FREEAPI_PORT/api/auth/login" \
      -H 'Content-Type: application/json' \
      -d "{\"email\":\"$FREEAPI_ADMIN_EMAIL\",\"password\":\"$FREEAPI_ADMIN_PASSWORD\"}" \
      | sed -n 's/.*"token":"\([^"]*\)".*/\1/p' || true)"
  fi
  if [ -n "$seed_token" ]; then echo "[start] dashboard auth token acquired"; fi
fi

# Pin the unified key BEFORE any /v1 use, so redeploy re-seeds the same key the
# backend env expects. Loopback socket -> no setup code needed for API calls.
if [ -n "$UNIFIED_API_KEY" ]; then
  node -e '
    const Database = require("better-sqlite3");
    const db = new Database(process.env.FREEAPI_DB_PATH || "/app/server/data/freeapi.db");
    db.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").run("unified_api_key", process.argv[1]);
    console.log("[seed] unified key pinned");
    db.close();
  ' "$UNIFIED_API_KEY" || echo "[start] WARN: unified key pin failed"
fi

# Provider keys + custom endpoints via the REST API (needs the auth token).
# Idempotent: fetches existing keys first and only adds platforms/endpoints
# that are not already present (a restored DB already has them).
if [ -n "$PROVIDER_KEYS_JSON" ] && [ -n "$seed_token" ]; then
  FREEAPI_PORT="$FREEAPI_PORT" SEED_TOKEN="$seed_token" node -e '
    const list = JSON.parse(process.env.PROVIDER_KEYS_JSON);
    (async () => {
      let existing = new Set();
      try {
        const r = await fetch("http://127.0.0.1:" + process.env.FREEAPI_PORT + "/api/keys", {
          headers: { Authorization: "Bearer " + process.env.SEED_TOKEN },
        });
        const body = await r.json();
        const keys = Array.isArray(body) ? body : (body.keys || []);
        for (const k of keys) existing.add(k.platform + ":" + (k.label || ""));
      } catch (e) { console.log("[seed] existing-key fetch failed:", e.message); }
      for (const it of list) {
        if (existing.has(it.platform + ":" + (it.label || ""))) {
          console.log("[seed] key", it.platform, "-> exists (skip)");
          continue;
        }
        try {
          const r = await fetch("http://127.0.0.1:" + process.env.FREEAPI_PORT + "/api/keys", {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: "Bearer " + process.env.SEED_TOKEN },
            body: JSON.stringify({ platform: it.platform, key: it.key, label: it.label || "" }),
          });
          console.log("[seed] key", it.platform, "->", r.status);
        } catch (e) { console.log("[seed] key", it.platform, "failed:", e.message); }
      }
    })();
  ' || echo "[start] WARN: provider key seeding failed"
fi

if [ -n "$CUSTOM_ENDPOINTS_JSON" ] && [ -n "$seed_token" ]; then
  FREEAPI_PORT="$FREEAPI_PORT" SEED_TOKEN="$seed_token" node -e '
    const list = JSON.parse(process.env.CUSTOM_ENDPOINTS_JSON);
    (async () => {
      let existing = new Set();
      try {
        const r = await fetch("http://127.0.0.1:" + process.env.FREEAPI_PORT + "/api/keys", {
          headers: { Authorization: "Bearer " + process.env.SEED_TOKEN },
        });
        const body = await r.json();
        const keys = Array.isArray(body) ? body : (body.keys || []);
        for (const k of keys) if (k.baseUrl) existing.add(k.baseUrl);
      } catch (e) { console.log("[seed] existing-custom fetch failed:", e.message); }
      for (const it of list) {
        if (existing.has(it.baseUrl)) {
          console.log("[seed] custom", it.label || it.baseUrl, "-> exists (skip)");
          continue;
        }
        try {
          const r = await fetch("http://127.0.0.1:" + process.env.FREEAPI_PORT + "/api/keys/custom", {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: "Bearer " + process.env.SEED_TOKEN },
            // The route schema names the credential `apiKey`. Accepting `key`
            // too, because that is the obvious name to reach for and the only
            // evidence of a rejection used to be a bare "-> 400".
            body: JSON.stringify(it.apiKey || !it.key ? it : { ...it, apiKey: it.key }),
          });
          console.log("[seed] custom", it.label || it.baseUrl, "->", r.status);
          if (!r.ok) {
            console.log("[seed] custom REJECTED:", r.status, (await r.text()).slice(0, 400));
          }
        } catch (e) { console.log("[seed] custom", it.label || it.baseUrl, "failed:", e.message); }
      }
    })();
  ' || echo "[start] WARN: custom endpoint seeding failed"
fi

echo "[start] starting FastAPI backend on $PORT (public)"

python app.py &
BACKEND_PID=$!

# ── Seed CEO autonomous schedules (idempotent; after backend is up) ──────────
# The scheduler stores schedules on ephemeral disk, so every redeploy would
# otherwise start with ZERO autonomous runs. Wait for /api/health, then
# POST the 3 baseline schedules only if the slug doesn't exist yet.
(
  for i in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1 && break
    sleep 2
  done
  SEEDS='[{"slug":"morning-brief","task":"Morning brief banao: kitne leads hain (list_saved_leads), pending reviews/handoffs, agents ka status, aur aaj ka TOP priority suggest karo. Short report.","interval_minutes":1440,"workspace_id":"","enabled":true},{"slug":"lead-pipeline","task":"start_agent_task se SBA ko background mein bolo: naye leads dhundo (find_leads_http tool, dentist/hvac/salon categories, Austin + Dallas TX), har naye lead ko qualify + save karo (3-5 leads). Task start hone ke baad turant 2-line confirmation de — result next run ya check_task se report hoga.","interval_minutes":360,"workspace_id":"","enabled":true},{"slug":"self-monitor","task":"system_selfcheck quick mode chalao. Koi issue ho to heal_agent se fix karo. 2 line report.","interval_minutes":60,"workspace_id":"","enabled":true}]'
  echo "$SEEDS" | python -c '
import json, sys, urllib.request
seeds = json.load(sys.stdin)
for s in seeds:
    try:
        req = urllib.request.Request("http://127.0.0.1:" + __import__("os").environ.get("PORT", "7860") + "/api/ceo/schedules")
        with urllib.request.urlopen(req, timeout=30) as r:
            existing = {x.get("slug") for x in json.loads(r.read()).get("schedules", [])}
        if s["slug"] in existing:
            print("[sched-seed]", s["slug"], "exists (skip)")
            continue
        req2 = urllib.request.Request(
            "http://127.0.0.1:" + __import__("os").environ.get("PORT", "7860") + "/api/ceo/schedules",
            data=json.dumps(s).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req2, timeout=60) as r2:
            print("[sched-seed]", s["slug"], "->", r2.status)
    except Exception as e:
        print("[sched-seed]", s.get("slug"), "failed:", e)
'
) &
echo "[start] CEO schedule seed started (background)"

# ── Periodic DB backup to GitHub data branch (every 8 min) ───────────────────
# Same reasoning as the restore above: default it, or an unset backup token is
# a fatal error rather than a disabled feature.
#
# This interval is the size of the data-loss window on a deploy. The database is
# container-local SQLite, so a deploy outside this window restores a snapshot
# that predates recent writes. It was 20 minutes, and that is how fifteen real
# leads were lost: written, then deployed two minutes later. Shortened to 8.
# POST /api/sba/db-sync forces an immediate snapshot when a deploy is imminent.
if [ -n "${GH_BACKUP_TOKEN:-}" ]; then
  (
    while true; do
      sleep 480
      python -m admin.db_backup sync || echo "[start] WARN: periodic db sync failed"
    done
  ) &
  SYNC_PID=$!
  echo "[start] db backup loop started (every 8 min, pid=$SYNC_PID)"
fi

# ── Periodic KV backup (every 5 min) ────────────────────────────────────────
# The shutdown trap only fires on a graceful SIGTERM. Render's free tier can
# also recycle a container without one, so a periodic snapshot means at most
# 5 minutes of CEO memory is ever at risk, instead of everything since boot.
if [ -n "${CF_KV_TOKEN:-}" ]; then
  (
    while true; do
      sleep 300
      python "$ROOT/kv_backup.py" backup || true
    done
  ) &
  KV_SYNC_PID=$!
  echo "[start] KV backup loop started (every 5 min, pid=$KV_SYNC_PID)"
fi

term() {
  # Final sync so the last window of changes survives the restart.
  [ -n "${GH_BACKUP_TOKEN:-}" ] && python -m admin.db_backup sync || true
  # KV backup: upload current workspace DB to Cloudflare KV so next deploy restores it
  [ -n "${CF_KV_TOKEN:-}" ] && python "$ROOT/kv_backup.py" backup || true
  kill "$FREEAPI_PID" "$BACKEND_PID" ${SYNC_PID:-} ${KV_SYNC_PID:-} 2>/dev/null || true
  wait || true
  exit 0
}
trap term TERM INT

# Exit if either process dies.
wait -n "$FREEAPI_PID" "$BACKEND_PID"
code=$?
echo "[start] a process exited (code=$code); shutting down"
kill "$FREEAPI_PID" "$BACKEND_PID" 2>/dev/null || true
wait || true
exit $code
