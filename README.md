# Agency OS — TAGS Agency Backend

Fullstack AI agent system: CEO agent, multi-agent orchestration, PocketBase persistence,
LLM budget guards, SBA leads pipeline, workspace-scoped agents.

## Stack

| Layer | Tech |
|-------|------|
| **API** | FastAPI (uvicorn, port 7860) |
| **DB** | PocketBase (internal 8090, SQLite) |
| **Data survival** | Cloudflare R2 (+B2/e2 optional) multi-provider sync |
| **RAM** | 16GB (HF free CPU Basic) |

## Endpoints (public URL)

- `GET  /api/health` — health check
- `GET  /api/status` — pipeline summary
- `POST /api/ceo/chat` — CEO chat (timeout 120s+ recommended)
- `GET  /api/workspaces` — list workspaces
- `GET  /test/ceo` — CEO chat browser test page

## Env vars (Settings → Variables and secrets)

```
R2_SYNC_BUCKET   = <cloudflare r2 bucket>          # data survival
R2_ENDPOINT      = https://<account>.r2.cloudflarestorage.com
R2_ACCESS_KEY    = <r2 key id>
R2_SECRET_KEY    = <r2 secret>                     # mark as Secret
POCKETBASE_URL   = http://127.0.0.1:8090
OPENAI_API_KEY   = <llm key>                       # mark as Secret
```

Optional extra backup providers (~55GB free stack):

```
SYNC_EXTRA_PROVIDERS = b2,e2
B2_ENDPOINT  = https://s3.us-west-004.backblaze.com   # example
B2_ACCESS_KEY / B2_SECRET_KEY / B2_BUCKET = ...
E2_* likewise
```

## Data flow

```
Boot → hf_sync.py restore (R2 → pb_data + JSON stores)
Runtime → PocketBase serve (internal) + FastAPI (7860)
Every 5 min → hf_sync.py sync (pb_data WAL-checkpointed → R2)
```

Working files HF ke ephemeral 50GB pe chalte hain (fast local disk);
survival-critical data R2 pe replicate hota hai. Restart = restore se wapas.

## Keep-alive

Free Spaces 48h inactivity pe sleep karte hain. cron-job.org (free) se
har 30 min `GET /api/health` ping karo.

## Cost

$0/month — HF CPU Basic (16GB RAM, free) + Cloudflare R2 (10GB, free).
