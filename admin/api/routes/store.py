"""Client Store API — Shopify-like storefront management per workspace.

Endpoints:
  GET   /api/store/status              — store availability + product stats
  GET   /api/store/public              — public storefront (no auth): settings + products
  GET   /api/store/products            — list products (?workspace=&client=)
  POST  /api/store/products            — create a product (token required)
  PATCH /api/store/products/{pid}      — update a product (token required)
  DELETE /api/store/products/{pid}     — delete a product (token required)
  GET   /api/store/settings            — store settings (?workspace=&client=)
  PATCH /api/store/settings            — upsert store settings (token required)
  GET   /api/store/booking/settings    — booking config (?workspace=&client=)
  PATCH /api/store/booking/settings    — upsert booking config (token required)
  GET   /api/store/meetings            — list meeting requests (?workspace=&client=&status=)
  POST  /api/store/meetings            — create a meeting request (token required)
  GET   /api/store/meetings/{mid}      — get a meeting request
  PATCH /api/store/meetings/{mid}      — update status/notes (token required)
  GET   /api/store/book/:token         — public booking page data (token from link)
  POST  /api/store/book/:token         — public books a slot (no auth)
  POST  /api/store/sync                — rebuild + redeploy client site from store (token)
  POST  /api/store/accounts            — create a client account (agency/admin)
  POST  /api/store/client/login        — client login -> signed token
  GET   /api/store/client/me           — current client identity (token)
  GET   /api/store/sales               — real store sales (revenue, orders, units)
  POST  /api/store/orders              — public checkout: place an order
  GET   /api/store/orders              — list orders (token required)
  PATCH /api/store/orders/{oid}        — update status + dispatch info (token)
  GET   /api/store/track               — public order tracking (order# + email)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from admin.agency.website_supabase import get_config
from admin.store import store_auth, store_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/store", tags=["client-store"])


def _auth_workspace(x_store_token: str = Header("", alias="X-Store-Token"),
                    workspace: str = Query("")) -> dict[str, Any]:
    """Require a valid store token, pinned to the requested workspace."""
    payload = store_auth.verify_token(x_store_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired store token")
    if workspace and payload.get("ws") != workspace:
        raise HTTPException(status_code=403, detail="Token not valid for this workspace")
    return payload


def _auth_optional(x_store_token: str = Header("", alias="X-Store-Token")) -> dict[str, Any] | None:
    """Verify a token when supplied. Returns payload or None (agency context).

    Clients must send their token; the agency admin UI may manage the store
    without one. A supplied token always pins the caller to its workspace.
    """
    if not x_store_token:
        return None
    return store_auth.verify_token(x_store_token)


def _enforce_client_scope(auth: dict[str, Any] | None, workspace: str, client: str) -> None:
    """Reject a logged-in client touching another workspace/client."""
    if auth:
        if auth.get("ws") != workspace or auth.get("client") != client:
            raise HTTPException(status_code=403, detail="Cannot modify another workspace's store")


# ── Request Models ───────────────────────────────────────────────────────────

class ProductRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    product: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


class ProductUpdateRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    data: dict[str, Any]


class ServiceRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    service: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


class ServiceUpdateRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    data: dict[str, Any]


class SettingsRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    data: dict[str, Any]


class SyncRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    deploy: bool = True


class LoginRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    email: str
    password: str


class AccountRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    email: str
    password: str
    name: str = ""


class OrderRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    product_id: str = ""
    quantity: int = 1
    items: list[dict[str, Any]] | None = None
    customer: dict[str, Any] | None = None
    payment_method: str = ""
    notes: str = ""
    coupon_code: str = ""


class CouponRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    coupon: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


class CouponValidateRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    code: str = ""
    subtotal: float = 0


class ReviewRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    review: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


class ReviewPatchRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    data: dict[str, Any]


class MeetingRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    lead_id: str = ""
    lead_name: str = ""
    lead_email: str = ""
    lead_phone: str = ""
    proposed_time: str = ""
    duration_minutes: int = 30
    notes: str = ""


class MeetingStatusRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    status: str = "confirmed"
    notes: str = ""


class BookingSettingsRequest(BaseModel):
    workspace: str = "Default"
    client: str = "Client"
    data: dict[str, Any]


class OrderStatusPATCH(BaseModel):
    status: str
    tracking_number: str = ""
    carrier: str = ""
    dispatch_note: str = ""


def _require_store() -> None:
    if not get_config():
        raise HTTPException(status_code=503, detail="Store backend not configured (POCKETBASE_URL/POCKETBASE_SERVICE_KEY missing)")


async def _notify_order_placed(workspace: str, client: str, order: dict[str, Any]) -> None:
    """Best-effort emails when an order is placed.

    Sends a confirmation to the customer and a notification to the store
    owner (settings.contact_email). Both are fire-and-forget: any failure is
    logged, never raised, so checkout never depends on email.
    """
    from admin.store import store_store as ss
    from admin.tools.sba_email_client import SBAEmailClient

    email_client = SBAEmailClient()
    if not email_client.enabled:
        return

    customer_email = str(order.get("customer_email") or "").strip()
    settings = ss.get_settings(workspace, client)
    owner_email = str(settings.get("contact_email") or "").strip()
    owner_whatsapp = str(settings.get("whatsapp") or "").strip()
    store_name = str(settings.get("store_name") or "").strip() or client
    order_number = str(order.get("order_number") or order.get("id") or "")
    total = str(order.get("total") or "")
    currency = str(settings.get("currency") or "₹")
    items = order.get("items") or []
    items_txt = "\n".join(
        f"  - {it.get('name', 'Item')} x{it.get('quantity', 1)} @ {currency}{it.get('price', 0)}"
        for it in items
    ) or f"  - {order.get('product_name', 'Item')} x{order.get('quantity', 1)}"

    # WhatsApp deep link: owner tap kare to message prefilled ready rahe.
    wa_link = ""
    if owner_whatsapp:
        try:
            import urllib.parse
            phone = re.sub(r"[^0-9]", "", owner_whatsapp)
            if phone:
                wa_text = (
                    f"New order {order_number} — {store_name}\n"
                    f"Customer: {order.get('customer_name') or '—'}\n"
                    f"Phone: {order.get('customer_phone') or '—'}\n"
                    f"Address: {order.get('customer_address') or '—'}\n"
                    f"Items:\n{items_txt}\n"
                    f"Total: {currency}{total} ({order.get('payment_method') or 'COD'})"
                )
                wa_link = f"https://wa.me/{phone}?text={urllib.parse.quote(wa_text)}"
        except Exception:  # noqa: BLE001
            wa_link = ""

    if customer_email:
        await email_client.send_email(
            customer_email,
            f"✅ Order {order_number} confirmed — {store_name}",
            f"Namaste {order.get('customer_name') or 'there'},\n\n"
            f"Your order at {store_name} is confirmed.\n\n"
            f"Order number: {order_number}\n"
            f"Status: Placed\n\n"
            f"Items:\n{items_txt}\n"
            f"\nTotal: {currency}{total}\n\n"
            f"We'll update you as the order moves to processing, shipping and delivery.\n"
            f"Thank you for shopping with us!\n\n— {store_name}",
            cc_owner=False,
        )

    if owner_email and owner_email != customer_email:
        location = " · ".join(x for x in [
            order.get("customer_city") or "",
            order.get("customer_state") or "",
            order.get("customer_pincode") or "",
        ] if x)
        source = order.get("source") or "Direct"
        wa_txt = f"\nWhatsApp pe chat karo: {wa_link}\n" if wa_link else "\n"
        await email_client.send_email(
            owner_email,
            f"🛒 New order {order_number} — {store_name}",
            f"A new order was placed on your store.\n\n"
            f"Order number: {order_number}\n"
            f"Customer: {order.get('customer_name') or '—'} <{customer_email}>\n"
            f"Phone: {order.get('customer_phone') or '—'}\n"
            f"Address: {order.get('customer_address') or '—'}\n"
            f"Location: {location or '—'}  (source: {source})\n\n"
            f"Items:\n{items_txt}\n"
            f"\nTotal: {currency}{total}  (payment: {order.get('payment_method') or 'COD'})\n"
            f"{wa_txt}"
            f"Login to your store dashboard to update the order status and "
            f"dispatch (tracking number/carrier).",
            cc_owner=False,
        )


async def _notify_order_shipped(workspace: str, client: str, order: dict[str, Any]) -> None:
    """Best-effort dispatch email to the customer when an order ships."""
    from admin.store import store_store as ss
    from admin.tools.sba_email_client import SBAEmailClient

    email_client = SBAEmailClient()
    if not email_client.enabled:
        return

    customer_email = str(order.get("customer_email") or "").strip()
    if not customer_email:
        return
    settings = ss.get_settings(workspace, client)
    store_name = str(settings.get("store_name") or "").strip() or client
    order_number = str(order.get("order_number") or order.get("id") or "")
    carrier = str(order.get("carrier") or "").strip()
    tracking = str(order.get("tracking_number") or "").strip()
    currency = str(settings.get("currency") or "₹")

    await email_client.send_email(
        customer_email,
        f"📦 Order {order_number} dispatched — {store_name}",
        f"Namaste {order.get('customer_name') or 'there'},\n\n"
        f"Good news! Your order {order_number} has been dispatched.\n\n"
        f"Carrier: {carrier or '—'}\n"
        f"Tracking number: {tracking or '—'}\n"
        f"{order.get('dispatch_note') or ''}\n"
        f"Total: {currency}{order.get('total') or ''}\n\n"
        f"Track online: {store_name} website ka Track Order section use karo "
        f"(order number + apna email).\n\n— {store_name}",
        cc_owner=False,
    )


# ── Status ───────────────────────────────────────────────────────────────────


@router.get("/status")
async def store_status(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    _require_store()
    site_url = ""
    try:
        from admin.agency.website_supabase import get_website_build
        build = get_website_build(workspace, client)
        site_url = (build or {}).get("site_url") or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("store: status site_url lookup failed: %s", e)
    return {
        "available": True,
        "workspace": workspace,
        "client": client,
        "products": store_store.product_stats(workspace, client),
        "services": store_store.service_stats(workspace, client),
        "settings": store_store.get_settings(workspace, client),
        "site_url": site_url,
    }


# ── Products ─────────────────────────────────────────────────────────────────


@router.get("/products")
async def list_products(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
    active_only: bool = Query(False),
):
    _require_store()
    return store_store.list_products(workspace, client, active_only=active_only)


@router.post("/products")
async def create_product(req: ProductRequest, auth: dict | None = Depends(_auth_optional)):
    _require_store()
    _enforce_client_scope(auth, req.workspace, req.client)
    product = req.product or req.data or {}
    created = store_store.create_product(req.workspace, req.client, product)
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create product")
    return created


@router.patch("/products/{pid}")
async def update_product(pid: str, req: ProductUpdateRequest, auth: dict | None = Depends(_auth_optional)):
    _require_store()
    _enforce_client_scope(auth, req.workspace, req.client)
    updated = store_store.update_product(req.workspace, req.client, pid, req.data)
    if not updated:
        raise HTTPException(status_code=404, detail="Product not found or update failed")
    return updated


@router.delete("/products/{pid}")
async def delete_product(pid: str, workspace: str = Query("Default"), client: str = Query("Client"),
                         auth: dict | None = Depends(_auth_optional)):
    _require_store()
    _enforce_client_scope(auth, workspace, client)
    ok = store_store.delete_product(workspace, client, pid)
    if not ok:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"success": True, "deleted": pid}


# ── Services ─────────────────────────────────────────────────────────────────


@router.get("/services")
async def list_services(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
    active_only: bool = Query(False),
):
    _require_store()
    return store_store.list_services(workspace, client, active_only=active_only)


@router.post("/services")
async def create_service(req: ServiceRequest, auth: dict | None = Depends(_auth_optional)):
    _require_store()
    _enforce_client_scope(auth, req.workspace, req.client)
    service = req.service or req.data or {}
    created = store_store.create_service(req.workspace, req.client, service)
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create service")
    return created


@router.patch("/services/{sid}")
async def update_service(sid: str, req: ServiceUpdateRequest, auth: dict | None = Depends(_auth_optional)):
    _require_store()
    _enforce_client_scope(auth, req.workspace, req.client)
    updated = store_store.update_service(req.workspace, req.client, sid, req.data)
    if not updated:
        raise HTTPException(status_code=404, detail="Service not found or update failed")
    return updated


@router.delete("/services/{sid}")
async def delete_service(sid: str, workspace: str = Query("Default"), client: str = Query("Client"),
                         auth: dict | None = Depends(_auth_optional)):
    _require_store()
    _enforce_client_scope(auth, workspace, client)
    ok = store_store.delete_service(workspace, client, sid)
    if not ok:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"success": True, "deleted": sid}


# ── Settings ─────────────────────────────────────────────────────────────────


@router.get("/settings")
async def get_settings(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    _require_store()
    return store_store.get_settings(workspace, client)


@router.patch("/settings")
async def update_settings(req: SettingsRequest, auth: dict | None = Depends(_auth_optional)):
    _require_store()
    _enforce_client_scope(auth, req.workspace, req.client)
    saved = store_store.upsert_settings(req.workspace, req.client, req.data)
    if not saved:
        raise HTTPException(status_code=500, detail="Failed to save settings")
    return store_store.get_settings(req.workspace, req.client)


# ── Public storefront (no auth) ──────────────────────────────────────────────


@router.get("/public")
async def public_storefront(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """Public view: store settings + active products + services + approved reviews. No token required."""
    _require_store()
    return {
        "workspace": workspace,
        "client": client,
        "settings": store_store.get_settings(workspace, client),
        "products": store_store.list_products(workspace, client, active_only=True),
        "services": store_store.list_services(workspace, client, active_only=True),
        "reviews": store_store.list_reviews(workspace, client, approved_only=True),
        "review_stats": store_store.review_stats(workspace, client),
    }


# ── Client accounts + auth ───────────────────────────────────────────────────


@router.post("/accounts")
async def create_account(req: AccountRequest):
    """Create a client account for a workspace (agency admin UI)."""
    _require_store()
    account = store_auth.create_account(req.workspace, req.client, req.email, req.password, req.name)
    if not account:
        raise HTTPException(status_code=400, detail="Account not created (invalid email/password or backend down)")
    return account


@router.post("/client/login")
async def client_login(req: LoginRequest):
    """Client login -> signed token bound to (workspace, client)."""
    _require_store()
    account = store_auth.verify_login(req.workspace, req.client, req.email, req.password)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = store_auth.issue_token(req.workspace, req.client, account["email"], account["name"])
    return {"token": token, "account": account, "workspace": req.workspace}


@router.get("/client/me")
async def client_me(payload: dict = Depends(_auth_workspace)):
    """Current client identity (requires token)."""
    return {
        "workspace": payload.get("ws", ""),
        "client": payload.get("client", ""),
        "email": payload.get("email", ""),
        "name": payload.get("name", ""),
    }


@router.get("/sales")
async def store_sales(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """Real store sales for this workspace/client (revenue, orders, units).

    Not SBA lead stats — this is actual revenue from orders placed through
    the client's storefront.
    """
    _require_store()
    return store_store.sales_stats(workspace, client)


# ── Orders (public checkout + owner list) ───────────────────────────────────


@router.post("/orders")
async def create_order(req: OrderRequest):
    """Public checkout — a customer places an order for one product.

    No auth needed: anyone can buy from the public storefront. Stock is
    decremented and the order is recorded for the owner's sales dashboard.
    """
    _require_store()
    result = store_store.place_order(
        req.workspace, req.client, req.product_id, req.quantity,
        items=req.items, customer=req.customer,
        payment_method=req.payment_method, notes=req.notes,
        coupon_code=req.coupon_code,
    )
    if result is None:
        raise HTTPException(status_code=503, detail="Store backend not available")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    # Best-effort order notifications (customer confirmation + owner alert).
    # Never blocks/fails the checkout when email is unavailable.
    try:
        await _notify_order_placed(req.workspace, req.client, result)
    except Exception:  # noqa: BLE001
        logger.exception("store: order notification failed (order still placed)")
    # Owner's WhatsApp deep link so they can ping the customer instantly.
    try:
        settings = store_store.get_settings(req.workspace, req.client)
        owner_wa = str(settings.get("whatsapp") or "").strip()
        phone = re.sub(r"[^0-9]", "", owner_wa)
        if phone:
            import urllib.parse
            wa_text = (
                f"Hi {result.get('customer_name') or 'there'}! Your order "
                f"{result.get('order_number')} at "
                f"{settings.get('store_name') or req.client} is confirmed. "
                f"Total: {settings.get('currency') or '₹'}{result.get('total')}. "
                f"We'll update you soon."
            )
            result["whatsapp_link"] = f"https://wa.me/{phone}?text={urllib.parse.quote(wa_text)}"
    except Exception:  # noqa: BLE001
        pass
    return result


@router.get("/orders")
async def store_orders(
    payload: dict | None = Depends(_auth_optional),
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """List orders for this store (owner or agency)."""
    _require_store()
    _enforce_client_scope(payload, workspace, client)
    return store_store.list_orders(workspace, client)


@router.patch("/orders/{oid}")
async def update_order_status(
    oid: str,
    req: OrderStatusPATCH,
    workspace: str = Query("Default"),
    client: str = Query("Client"),
    payload: dict | None = Depends(_auth_optional),
):
    """Owner updates an order's status + optional dispatch info."""
    _require_store()
    _enforce_client_scope(payload, workspace, client)
    extra = {
        "tracking_number": req.tracking_number,
        "carrier": req.carrier,
        "dispatch_note": req.dispatch_note,
    }
    result = store_store.update_order_status(workspace, client, oid, req.status, extra=extra)
    if result is None:
        raise HTTPException(status_code=503, detail="Store backend not available")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    # Dispatch notification (customer email) when an order ships.
    if (result.get("status") == "shipped" and result.get("tracking_number")):
        try:
            await _notify_order_shipped(workspace, client, result)
        except Exception:  # noqa: BLE001
            logger.exception("store: shipped notification failed")
    return result


@router.get("/track")
async def track_order_public(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
    order_number: str = Query(""),
    email: str = Query(""),
):
    """Public order tracking — order_number + email must match. No auth."""
    _require_store()
    if not order_number.strip() or not email.strip():
        raise HTTPException(status_code=400, detail="Order number + email dono required hain")
    result = store_store.track_order(workspace, client, order_number.strip(), email.strip())
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Storefront views (public analytics) ────────────────────────────────────


@router.post("/views")
async def record_store_view(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """Record one pageview of the client's public storefront. No auth."""
    _require_store()
    ok = store_store.record_view(workspace, client)
    return {"success": ok, "workspace": workspace, "client": client}


@router.get("/views")
async def store_views(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """Total pageviews of the client's public storefront."""
    _require_store()
    return {"views": store_store.view_count(workspace, client)}


# ── Sync to live website ─────────────────────────────────────────────────────


@router.post("/sync")
async def sync_store_site(req: SyncRequest, auth: dict | None = Depends(_auth_optional)):
    """Rebuild the client's live site from current store products + settings.

    Delegates to the Website Agent's store-aware builder and optionally
    redeploys to Vercel.
    """
    _require_store()
    _enforce_client_scope(auth, req.workspace, req.client)
    try:
        from admin.tools import website_tools
        result = website_tools.build_site_from_store(
            workspace=req.workspace,
            client=req.client,
            deploy=req.deploy,
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("store sync failed")
        raise HTTPException(status_code=500, detail=f"Sync failed: {e}") from e
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Coupons (owner creates, customers apply at checkout) ─────────────────────


@router.get("/coupons")
async def list_coupons(
    payload: dict | None = Depends(_auth_optional),
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """List all discount coupons for this store (token required, no public leak)."""
    _require_store()
    if not payload:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(payload, workspace, client)
    return store_store.list_coupons(workspace, client)


@router.post("/coupons")
async def create_coupon(req: CouponRequest, auth: dict | None = Depends(_auth_optional)):
    _require_store()
    if not auth:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(auth, req.workspace, req.client)
    coupon = req.coupon or req.data or {}
    created = store_store.create_coupon(req.workspace, req.client, coupon)
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create coupon")
    if "error" in created:
        raise HTTPException(status_code=400, detail=created["error"])
    return created


@router.post("/coupons/validate")
async def validate_coupon(req: CouponValidateRequest):
    """Public: check a coupon code against a subtotal (no auth)."""
    _require_store()
    result = store_store.validate_coupon(req.workspace, req.client, req.code, req.subtotal)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.patch("/coupons/{cid}")
async def update_coupon(cid: str, req: CouponRequest, auth: dict | None = Depends(_auth_optional)):
    _require_store()
    if not auth:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(auth, req.workspace, req.client)
    updated = store_store.update_coupon(req.workspace, req.client, cid, req.coupon or req.data or {})
    if not updated:
        raise HTTPException(status_code=404, detail="Coupon not found or update failed")
    return updated


@router.delete("/coupons/{cid}")
async def delete_coupon(cid: str, workspace: str = Query("Default"), client: str = Query("Client"),
                        auth: dict | None = Depends(_auth_optional)):
    _require_store()
    if not auth:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(auth, workspace, client)
    ok = store_store.delete_coupon(workspace, client, cid)
    if not ok:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return {"success": True, "deleted": cid}


# ── Reviews (customers rate products; owner moderates) ───────────────────────


@router.get("/reviews")
async def list_reviews(
    payload: dict | None = Depends(_auth_optional),
    workspace: str = Query("Default"),
    client: str = Query("Client"),
    product_id: str = Query(""),
):
    """List reviews for this store.

    Owner/agency (with token) get all reviews incl. pending; public callers
    only see approved ones so unpublished reviews never leak.
    """
    _require_store()
    _enforce_client_scope(payload, workspace, client)
    approved_only = payload is None
    return store_store.list_reviews(workspace, client, product_id=product_id, approved_only=approved_only)


@router.get("/reviews/stats")
async def reviews_stats(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """Aggregated rating stats (public, approved reviews only)."""
    _require_store()
    return store_store.review_stats(workspace, client)


@router.post("/reviews")
async def create_review(req: ReviewRequest):
    """Public: customer submits a review for a product (no auth, pending approval)."""
    _require_store()
    review = req.review or req.data or {}
    created = store_store.create_review(req.workspace, req.client, review)
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create review")
    return created


@router.patch("/reviews/{rid}")
async def update_review(rid: str, req: ReviewPatchRequest, auth: dict | None = Depends(_auth_optional)):
    """Owner approves/rejects or edits a review (token required)."""
    _require_store()
    if not auth:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(auth, req.workspace, req.client)
    updated = store_store.update_review(req.workspace, req.client, rid, req.data)
    if not updated:
        raise HTTPException(status_code=404, detail="Review not found or update failed")
    return updated


@router.delete("/reviews/{rid}")
async def delete_review(rid: str, workspace: str = Query("Default"), client: str = Query("Client"),
                        auth: dict | None = Depends(_auth_optional)):
    _require_store()
    if not auth:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(auth, workspace, client)
    ok = store_store.delete_review(workspace, client, rid)
    if not ok:
        raise HTTPException(status_code=404, detail="Review not found")
    return {"success": True, "deleted": rid}


# ── Booking settings (SBA meeting system — no Google Calendar) ────────────────


@router.get("/booking/settings")
async def get_booking_settings(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """Return the store's meeting-booking configuration (public-readable)."""
    _require_store()
    return store_store.get_booking_settings(workspace, client)


@router.patch("/booking/settings")
async def patch_booking_settings(
    req: BookingSettingsRequest,
    auth: dict | None = Depends(_auth_optional),
):
    """Update the store's meeting-booking configuration (owner login required)."""
    _require_store()
    if not auth:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(auth, req.workspace, req.client)
    updated = store_store.update_booking_settings(req.workspace, req.client, req.data)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update booking settings")
    return updated


# ── Meetings (SBA agent booking, stored in owner's store) ─────────────────────


@router.get("/meetings")
async def list_meetings(
    workspace: str = Query("Default"),
    client: str = Query("Client"),
    status: str = Query(""),
    token: str = Query(""),
):
    """List meeting requests for a store (owner token) or a single booking link."""
    _require_store()
    # A booking link token scopes the caller to one meeting without full auth.
    if token:
        m = store_store.find_meeting_by_token(workspace, client, token)
        if not m:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return {"success": True, "data": {"meeting": m}}
    auth = _auth_optional()
    _enforce_client_scope(auth, workspace, client)
    meetings = store_store.list_meeting_requests(workspace, client, status=status or None)
    return {"success": True, "data": {"meetings": meetings}}


@router.post("/meetings")
async def create_meeting(
    req: MeetingRequest,
    auth: dict | None = Depends(_auth_optional),
):
    """Create a meeting request (SBA agent books here after a lead says yes)."""
    _require_store()
    if not auth:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(auth, req.workspace, req.client)
    meeting = store_store.create_meeting_request(
        req.workspace,
        req.client,
        {
            "lead_id": req.lead_id,
            "lead_name": req.lead_name,
            "lead_email": req.lead_email,
            "lead_phone": req.lead_phone,
            "title": f"Meeting with {req.lead_name or 'Lead'} — TAGS Agency",
            "purpose": req.notes,
            "date": "",
            "time": "",
            "duration_minutes": req.duration_minutes,
            "status": "requested",
            "notes": req.notes,
            "source": "sba_autopilot",
            "owner_link_base": os.environ.get("STORE_BASE_URL", ""),
        },
    )
    if not meeting:
        raise HTTPException(status_code=500, detail="Failed to create meeting request")
    return {"success": True, "data": {"meeting": meeting}}


@router.get("/meetings/{mid}")
async def get_meeting(
    mid: str,
    workspace: str = Query("Default"),
    client: str = Query("Client"),
):
    """Get a single meeting request (owner token or booking link token)."""
    _require_store()
    m = store_store.get_meeting_request(workspace, client, mid)
    if not m:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {"success": True, "data": {"meeting": m}}


@router.patch("/meetings/{mid}")
async def patch_meeting(
    mid: str,
    req: MeetingStatusRequest,
    auth: dict | None = Depends(_auth_optional),
):
    """Update a meeting's status/notes (owner login required)."""
    _require_store()
    if not auth:
        raise HTTPException(status_code=401, detail="Store owner login required")
    _enforce_client_scope(auth, req.workspace, req.client)
    updated = store_store.update_meeting_request(
        req.workspace, req.client, mid, {"status": req.status, "notes": req.notes}
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Meeting not found or update failed")
    return {"success": True, "data": {"meeting": updated}}


# ── Public booking widget (no auth; reached from the link the SBA sends) ──────


@router.get("/book/{token}")
async def public_book_info(token: str, workspace: str = Query("Default"),
                           client: str = Query("Client")):
    """Public booking page data for a single meeting request.

    Returns the meeting details + available slots so a lead can confirm a time
    without logging in. The token is scoped to exactly one meeting row.
    """
    _require_store()
    m = store_store.find_meeting_by_token(workspace, client, token)
    if not m:
        raise HTTPException(status_code=404, detail="Booking link expired or invalid")
    settings = store_store.get_booking_settings(workspace, client)
    return {
        "success": True,
        "data": {
            "meeting": m,
            "booking": settings,
        },
    }


@router.post("/book/{token}")
async def public_book_confirm(token: str, body: dict[str, Any] | None = None,
                               workspace: str = Query("Default"),
                               client: str = Query("Client")):
    """Lead confirms a slot for this meeting request (no auth).

    Body may include ``confirmed_time`` (ISO) and optional ``lead_phone``. Sets
    status to ``confirmed`` and notifies the owner.
    """
    _require_store()
    body = body or {}
    m = store_store.find_meeting_by_token(workspace, client, token)
    if not m:
        raise HTTPException(status_code=404, detail="Booking link expired or invalid")
    updates: dict[str, Any] = {"status": "confirmed"}
    if body.get("confirmed_time"):
        updates["date"] = str(body["confirmed_time"])[:10]
        updates["time"] = str(body["confirmed_time"])[11:16]
    if body.get("lead_phone"):
        updates["lead_phone"] = body["lead_phone"]
    if body.get("notes"):
        updates["notes"] = body["notes"]
    updated = store_store.update_meeting_request(workspace, client, m.get("id"), updates)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to confirm booking")

    # Notify the owner that the lead picked a slot.
    try:
        from admin.tools.sba_meeting import SBAMeetingManager

        mgr = SBAMeetingManager(workspace=workspace, client=client,
                                store_base_url=os.environ.get("STORE_BASE_URL", ""))
        await mgr._notify_owner(updated, updated.get("lead_name", ""), str(body.get("confirmed_time", "")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Owner confirm-notification failed for %s: %s", token, exc)

    return {"success": True, "data": {"meeting": updated}}


# ── Client-facing Agent Auto-Delivery ──────────────────────────────────────
# A client (or the store frontend) can ask a question and get the answer back
# from the right specialist agent WITHOUT a human in the loop. The agent runs
# itself (L2/L3 agentic delivery) and returns the result.
#
# Safety: only READ/ANALYSIS agents are exposed. SBA / email / meeting agents
# are intentionally excluded — those stay owned by the autopilot + owner gates.
_CLIENT_AGENT_ALLOWLIST = {
    "seo", "content", "website", "ads", "social", "analytics", "analyzing", "memory",
}


class ClientAgentRequest(BaseModel):
    message: str
    agent: str | None = None  # optional; auto-routed from message if omitted
    workspace_id: str | None = None  # optional override


@router.post("/agent")
async def client_agent_ask(
    body: ClientAgentRequest,
    workspace: str = Query(...),
    client: str = Query(...),
) -> dict[str, Any]:
    """Client asks an agent a question; the agent runs itself and returns the answer.

    No human in the loop, no external sends. The agent is scoped to this store's
    workspace so it only reasons over this client's data.
    """
    _require_store()
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    # Resolve target agent: explicit -> keyword auto-route -> analyzing default.
    agent_type = body.agent
    if agent_type and agent_type not in _CLIENT_AGENT_ALLOWLIST:
        raise HTTPException(status_code=400, detail=f"agent '{agent_type}' not available to clients")
    if not agent_type:
        lowered = message.lower()
        if any(k in lowered for k in ("seo", "rank", "keyword", "search")):
            agent_type = "seo"
        elif any(k in lowered for k in ("ad", "campaign", "roas", "meta", "google ad")):
            agent_type = "ads"
        elif any(k in lowered for k in ("post", "social", "instagram", "linkedin", "tiktok")):
            agent_type = "social"
        elif any(k in lowered for k in ("analytics", "report", "insight", "trend", "forecast")):
            agent_type = "analyzing"
        elif any(k in lowered for k in ("website", "site", "page", "deploy", "performance")):
            agent_type = "website"
        elif any(k in lowered for k in ("content", "image", "caption", "creative", "design")):
            agent_type = "content"
        else:
            agent_type = "analyzing"

    from admin.workspace.manager import route_to_agent

    # Scope to this store's workspace when possible.
    target_ws = body.workspace_id or workspace
    try:
        resp = await route_to_agent(
            workspace_id=target_ws,
            agent_type=agent_type,
            message=message,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("client agent ask failed (ws=%s, agent=%s): %s", target_ws, agent_type, exc)
        return {
            "success": False,
            "error": f"{agent_type} agent failed: {exc}",
            "data": {"response": f"❌ Agent unavailable: {exc}", "agent": agent_type},
        }

    return {
        "success": True,
        "data": {"response": resp, "agent": agent_type, "workspace": target_ws},
    }
