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
if [ -n "$PROVIDER_KEYS_JSON" ] && [ -n "$seed_token" ]; then
  FREEAPI_PORT="$FREEAPI_PORT" SEED_TOKEN="$seed_token" node -e '
    const list = JSON.parse(process.env.PROVIDER_KEYS_JSON);
    (async () => {
      for (const it of list) {
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
      for (const it of list) {
        try {
          const r = await fetch("http://127.0.0.1:" + process.env.FREEAPI_PORT + "/api/keys/custom", {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: "Bearer " + process.env.SEED_TOKEN },
            body: JSON.stringify(it),
          });
          console.log("[seed] custom", it.label || it.baseUrl, "->", r.status);
        } catch (e) { console.log("[seed] custom", it.label || it.baseUrl, "failed:", e.message); }
      }
    })();
  ' || echo "[start] WARN: custom endpoint seeding failed"
fi

echo "[start] starting FastAPI backend on $PORT (public)"

python app.py &
BACKEND_PID=$!

term() {
  kill "$FREEAPI_PID" "$BACKEND_PID" 2>/dev/null || true
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
