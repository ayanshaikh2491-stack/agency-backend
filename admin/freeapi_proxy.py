"""FreeLLMAPI dashboard proxy.

/freeapi/* -> internal FreeLLMAPI Node server (127.0.0.1:3001).

The dashboard SPA is built with VITE_BASE=/freeapi/, so its assets live at
/freeapi/assets/* and its API calls at /freeapi/api/*. We strip the /freeapi
prefix and stream the request (headers, body, method) to the Node proxy.

The operator opens https://<backend-url>/freeapi/ in a browser to manage
provider keys; the backend's own LLM calls go directly to
http://127.0.0.1:3001/v1 (AGENCY_CEO_API_BASE in Render env).

Non-goals: auth here (the dashboard has its own session auth), caching, or
rewriting the SPA (it is already subpath-built).
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

router = APIRouter()

FREEAPI_INTERNAL = os.getenv("FREEAPI_INTERNAL_URL", "http://127.0.0.1:3001")

# Headers that must not be forwarded hop-by-hop.
_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


@router.get("/freeapi", include_in_schema=False)
async def freeapi_root_redirect():
    return RedirectResponse(url="/freeapi/")


@router.api_route(
    "/freeapi/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def freeapi_proxy(path: str, request: Request):
    target = f"{FREEAPI_INTERNAL}/{path}"
    if path == "" or path.endswith("/"):
        # Dashboard root: the SPA index served by the Node server at /
        target = f"{FREEAPI_INTERNAL}/"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}
    # The SPA was built with VITE_BASE=/freeapi/, but when it reaches the Node
    # server the original Host header is the public backend host — fine to forward.
    body = await request.body()

    async with httpx.AsyncClient(timeout=300.0) as client:
        upstream = await client.request(
            request.method,
            target,
            headers=headers,
            content=body,
            params=request.query_params,
        )

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_HEADERS
        and k.lower() not in ("content-encoding",)  # let httpx decode; server re-encodes
    }
    # Preserve Set-Cookie (rare here) — httpx exposes multi-headers via .headers.multi_items()
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
