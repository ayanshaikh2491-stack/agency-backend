# syntax=docker/dockerfile:1.7
# ── Combined service: FastAPI backend (public PORT) + FreeLLMAPI Node proxy (internal :3001) ──
# One Render free service = one 512MB instance, one 750h/month budget slot. Both processes
# share it: Python backend ~161MB idle + Node proxy ~60-90MB.
# LLM calls flow: backend -> http://127.0.0.1:3001/v1 (AGENCY_CEO_API_BASE etc. set in Render env).

FROM node:20-bookworm-slim AS nodebuild

# better-sqlite3 is native; linux/amd64 has a prebuilt binary, but keep the
# toolchain so linux/arm64 (if ever needed) can compile from source.
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# All four npm workspace manifests must exist for `npm ci` (lockfile validation).
COPY freellmapi/package.json freellmapi/package-lock.json ./
COPY freellmapi/server/package.json server/
COPY freellmapi/shared/package.json shared/
COPY freellmapi/client/package.json client/
COPY freellmapi/cli/package.json cli/
# Desktop manifest is not an npm workspace member; copy into build tree so the
# runtime stage can pick it up (the dashboard reads its version from it).
COPY freellmapi/desktop/package.json desktop/

RUN npm ci

# Compile the Node server TypeScript to dist/. The client dashboard is consumed
# prebuilt (see COPY freellmapi/client/dist below) so we skip `npm run build -w client`.
COPY freellmapi/shared ./shared
COPY freellmapi/server/package.json ./server/
COPY freellmapi/server/tsconfig.json ./server/
COPY freellmapi/server/src ./server/src
RUN npm run build -w server

RUN npm prune --omit=dev


# ── Runtime stage: python:3.11-slim + node binary ──────────────────────────────
FROM python:3.11-slim-bookworm

# Node runtime binary from the build image (npm itself not needed at runtime).
COPY --from=nodebuild /usr/local/bin/node /usr/local/bin/node

ENV NODE_ENV=production
ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /app

# ── FreeLLMAPI Node app ──
COPY --from=nodebuild /build/package.json /build/package-lock.json ./
COPY --from=nodebuild /build/node_modules ./node_modules
# npm nests some production packages under the workspace instead of hoisting
# them (undici lives at server/node_modules/undici) — copy both trees.
COPY --from=nodebuild /build/server/node_modules ./server/node_modules
COPY --from=nodebuild /build/shared ./shared
COPY --from=nodebuild /build/server/package.json ./server/package.json
COPY --from=nodebuild /build/desktop/package.json ./desktop/package.json
COPY --from=nodebuild /build/server/dist ./server/dist
# Prebuilt dashboard client (VITE_BASE=/freeapi) — served at /freeapi/*
COPY freellmapi/client/dist ./client/dist

# SQLite data dir (ephemeral on Render free; keys re-seeded by start.sh at boot)
RUN mkdir -p /app/server/data

# ── Python backend ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY admin ./admin
COPY app.py ./
COPY start.sh ./
RUN chmod +x start.sh

# Fixed internal port for the proxy; the public port comes from Render's $PORT.
ENV FREEAPI_PORT=3001
ENV FREEAPI_DB_PATH=/app/server/data/freeapi.db

EXPOSE 7860

CMD ["bash", "start.sh"]
