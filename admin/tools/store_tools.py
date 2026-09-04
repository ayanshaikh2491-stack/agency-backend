"""Store tools for agents (CEO, SBA, Website).

Lets agency agents handle the client store conversationally:
  - get_client_store_link  -> return the client's storefront link + status
  - create_store_client_account -> create the client's login (email/password)
  - list_store_products    -> see what the client has added
  - publish_client_store   -> rebuild + deploy the client's live site from store

These wrap admin.store modules (PocketBase-backed). They are synchronous,
safe to call from any agent tool dispatcher.
"""

from __future__ import annotations

import json
from typing import Any

# ── Tool definitions (OpenAI-style, matches CEO_TOOLS / SBA_TOOLS shape) ─────

STORE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_client_store_link",
            "description": (
                "Get the client's storefront link (Shopify-style store) for a workspace. "
                "Returns the URL the client uses to log in, manage products, and publish "
                "their live website. Use when a client asks about their website or store."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID, e.g. ws_agency"},
                    "client": {"type": "string", "description": "Client name (default: Client)"},
                },
                "required": ["workspace_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_store_client_account",
            "description": (
                "Create a login account (email/password) for the client store. "
                "Use AFTER telling the client they can add products themselves, "
                "so they can log in at their store link. Returns the credentials to share."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID, e.g. ws_agency"},
                    "client": {"type": "string", "description": "Client name (default: Client)"},
                    "email": {"type": "string", "description": "Client email (login)"},
                    "password": {"type": "string", "description": "Client password (min 4 chars)"},
                    "name": {"type": "string", "description": "Optional display name"},
                },
                "required": ["workspace_id", "email", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_store_products",
            "description": (
                "List the products currently in the client's store for a workspace. "
                "Use to check what the client has added before publishing or discussing "
                "their store."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID, e.g. ws_agency"},
                    "client": {"type": "string", "description": "Client name (default: Client)"},
                },
                "required": ["workspace_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_client_store",
            "description": (
                "Rebuild and deploy the client's live website from their store products "
                "and settings. Use when the client confirms their products are ready or "
                "asks to go live."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID, e.g. ws_agency"},
                    "client": {"type": "string", "description": "Client name (default: Client)"},
                    "deploy": {"type": "boolean", "description": "Deploy to Vercel after build (default true)"},
                },
                "required": ["workspace_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_store_logo",
            "description": (
                "Set the client's store/website logo (image URL). The Website Agent reads "
                "this when building/updating the client's site. Use when the client shares "
                "a logo link."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID, e.g. ws_agency"},
                    "logo_url": {"type": "string", "description": "Public image URL of the logo"},
                    "client": {"type": "string", "description": "Client name (default: Client)"},
                },
                "required": ["workspace_id", "logo_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_store_product",
            "description": (
                "Add a product to the client's store (name, price, description, image). "
                "The client's live site shows it after an update. Use when the client wants "
                "a new product listed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID, e.g. ws_agency"},
                    "name": {"type": "string", "description": "Product name"},
                    "price": {"type": "string", "description": "Price, e.g. 4999 or ₹4,999"},
                    "description": {"type": "string", "description": "Short product description"},
                    "image_url": {"type": "string", "description": "Product image URL (optional)"},
                    "client": {"type": "string", "description": "Client name (default: Client)"},
                },
                "required": ["workspace_id", "name", "price"],
            },
        },
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _norm_workspace(workspace_id: str) -> str:
    ws = (workspace_id or "").strip()
    if ws and not ws.startswith("ws_"):
        ws = f"ws_{ws}"
    return ws or "ws_agency"


def _norm_client(client: str | None) -> str:
    return (client or "Client").strip() or "Client"


def _store_base_url() -> str:
    """Frontend origin for store links (configurable, defaults to EC2 box)."""
    import os

    return (os.environ.get("STORE_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "http://18.213.66.136:3000").rstrip("/")


# ── Tool implementations (sync) ───────────────────────────────────────────────


def get_client_store_link(workspace_id: str, client: str | None = None) -> str:
    """Return the storefront link + store status for a client."""
    ws = _norm_workspace(workspace_id)
    cl = _norm_client(client)
    slug = ws.replace("ws_", "")
    link = f"{_store_base_url()}/store/{slug}"

    try:
        from admin.store.store_store import get_settings, list_products, store_available

        if not store_available():
            return json.dumps({
                "store_link": link,
                "store_name": "My Store",
                "error": "Store backend not available",
            }, indent=2)

        settings = get_settings(ws, cl)
        products = list_products(ws, cl, active_only=True)
        accounts = _list_accounts(ws, cl)

        return json.dumps({
            "store_link": link,
            "store_name": settings.get("store_name") or "My Store",
            "tagline": settings.get("tagline") or "",
            "product_count": len(products),
            "client_account_exists": bool(accounts),
            "client_account": accounts[0].get("email") if accounts else None,
            "how_to_share": (
                f"Give the client this link: {link}. "
                "If they have no login yet, use create_store_client_account "
                "to create one (email + password), then tell them: "
                "'Aap apne store link pe login karke products add kar sakte ho, "
                "aur jab ready ho to Publish dabao.'"
            ),
        }, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "store_link": link,
            "error": f"get_client_store_link failed: {exc}",
        }, indent=2)


def _list_accounts(workspace: str, client: str) -> list[dict[str, Any]]:
    """Return all client accounts for (workspace, client)."""
    try:
        from admin.agency.website_supabase import _api, get_config
        from admin.agency.workspace_provision import schema_for
        import urllib.parse

        cfg = get_config()
        if not cfg:
            return []
        url, key = cfg
        q = "client_name=eq." + urllib.parse.quote(client)
        rows = _api(
            "GET", url, key,
            "/rest/v1/store_accounts?select=*&" + q,
            profile=schema_for(workspace),
        )
        out = []
        for r in rows or []:
            out.append({
                "email": r.get("email"),
                "name": r.get("name"),
                "active": r.get("active", True),
            })
        return out
    except Exception:  # noqa: BLE001
        return []


def create_store_client_account(
    workspace_id: str,
    email: str,
    password: str,
    client: str | None = None,
    name: str = "",
) -> str:
    """Create a client login for the store. Returns JSON result."""
    ws = _norm_workspace(workspace_id)
    cl = _norm_client(client)
    email = (email or "").strip().lower()
    password = (password or "").strip()

    if "@" not in email:
        return json.dumps({"error": "Valid email required"}, indent=2)
    if len(password) < 4:
        return json.dumps({"error": "Password must be at least 4 characters"}, indent=2)

    try:
        from admin.store.store_auth import create_account

        account = create_account(ws, cl, email, password, name or "")
        if not account:
            return json.dumps({"error": "Account create failed (backend unavailable or email exists)"}, indent=2)
        slug = ws.replace("ws_", "")
        link = f"{_store_base_url()}/store/{slug}"
        return json.dumps({
            "ok": True,
            "email": account.get("email", email),
            "name": account.get("name", ""),
            "store_link": link,
            "message": (
                f"Client account ready: {account.get('email', email)} / {password}. "
                f"Share: {link}. Bolo: 'Ye aapka store hai — is link pe login karke "
                "apne products add karo, jab ready ho to Publish dabao.'"
            ),
        }, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"create_store_client_account failed: {exc}"}, indent=2)


def list_store_products(workspace_id: str, client: str | None = None) -> str:
    """List store products for a client. Returns JSON text."""
    ws = _norm_workspace(workspace_id)
    cl = _norm_client(client)
    try:
        from admin.store.store_store import list_products

        products = list_products(ws, cl)
        out = []
        for p in products:
            out.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "price": p.get("price"),
                "compare_at": p.get("compare_at"),
                "category": p.get("category"),
                "stock": p.get("stock"),
                "active": p.get("active", True),
            })
        return json.dumps({"product_count": len(out), "products": out}, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"list_store_products failed: {exc}"}, indent=2)


def publish_client_store(workspace_id: str, client: str | None = None, deploy: bool = True) -> str:
    """Rebuild + deploy the client's live site from store data. Returns JSON text."""
    ws = _norm_workspace(workspace_id)
    cl = _norm_client(client)
    try:
        from admin.tools.website_tools import build_site_from_store

        result = build_site_from_store(workspace=ws, client=cl, deploy=deploy)
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"publish_client_store failed: {exc}"}, indent=2)


def update_store_logo(workspace_id: str, logo_url: str, client: str | None = None) -> str:
    """Set the client's store logo (image URL). Returns JSON text."""
    ws = _norm_workspace(workspace_id)
    cl = _norm_client(client)
    logo_url = (logo_url or "").strip()
    if not logo_url:
        return json.dumps({"error": "logo_url required"}, indent=2)
    try:
        from admin.store.store_store import upsert_settings

        row = upsert_settings(ws, cl, {"logo_url": logo_url})
        if not row:
            return json.dumps({"error": "Logo update failed (store backend unavailable)"}, indent=2)
        return json.dumps({
            "ok": True,
            "logo_url": logo_url,
            "message": "Logo set. Ab Website Agent ko bolo site update kare (update_store_site).",
        }, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"update_store_logo failed: {exc}"}, indent=2)


def add_store_product(
    workspace_id: str,
    name: str,
    price: str,
    description: str = "",
    image_url: str = "",
    client: str | None = None,
) -> str:
    """Add a product to the client's store. Returns JSON text."""
    ws = _norm_workspace(workspace_id)
    cl = _norm_client(client)
    name = (name or "").strip()
    price = (price or "").strip()
    if not name or not price:
        return json.dumps({"error": "name and price required"}, indent=2)
    try:
        from admin.store.store_store import create_product

        product = create_product(
            ws, cl,
            {"name": name, "price": price, "description": (description or "").strip(),
             "image_url": (image_url or "").strip(), "active": True},
        )
        if not product:
            return json.dumps({"error": "Product add failed (store backend unavailable)"}, indent=2)
        return json.dumps({
            "ok": True,
            "product": {"name": product.get("name"), "price": product.get("price")},
            "message": "Product added. Ab Website Agent ko bolo site update kare (update_store_site).",
        }, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"add_store_product failed: {exc}"}, indent=2)


def execute_store_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch for agent tool runners. Returns JSON text (always)."""
    if name == "get_client_store_link":
        return get_client_store_link(
            args.get("workspace_id", ""),
            args.get("client"),
        )
    if name == "create_store_client_account":
        return create_store_client_account(
            args.get("workspace_id", ""),
            args.get("email", ""),
            args.get("password", ""),
            args.get("client"),
            args.get("name", ""),
        )
    if name == "list_store_products":
        return list_store_products(args.get("workspace_id", ""), args.get("client"))
    if name == "publish_client_store":
        return publish_client_store(
            args.get("workspace_id", ""),
            args.get("client"),
            bool(args.get("deploy", True)),
        )
    if name == "update_store_logo":
        return update_store_logo(
            args.get("workspace_id", ""),
            args.get("logo_url", ""),
            args.get("client"),
        )
    if name == "add_store_product":
        return add_store_product(
            args.get("workspace_id", ""),
            args.get("name", ""),
            args.get("price", ""),
            args.get("description", ""),
            args.get("image_url", ""),
            args.get("client"),
        )
    return json.dumps({"error": f"Unknown store tool: {name}"})


def store_tool_names() -> list[str]:
    return [t["function"]["name"] for t in STORE_TOOLS]
