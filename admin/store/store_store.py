"""Store data store — products + settings per workspace, PocketBase-backed.

Uses the same REST pattern as `admin.agency.website_supabase` (urllib only).
Each workspace's rows live in its own schema (`{schema}__store_products`,
`{schema}__store_settings`) and are scoped to a client via `client_name`.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
from collections import Counter
from typing import Any

from admin.agency.website_supabase import _api, get_config
from admin.agency.workspace_provision import schema_for

logger = logging.getLogger(__name__)

PRODUCTS_TABLE = "store_products"
SERVICES_TABLE = "store_services"
SETTINGS_TABLE = "store_settings"
ORDERS_TABLE = "store_orders"
COUPONS_TABLE = "store_coupons"
REVIEWS_TABLE = "store_reviews"
MEETINGS_TABLE = "store_meetings"

# Meeting / booking status lifecycle (custom SBA booking system — no Google Calendar)
MEETING_STATUSES = ["requested", "confirmed", "completed", "cancelled"]
MEETING_STATUS_LABELS = {
    "requested": "Requested",
    "confirmed": "Confirmed",
    "completed": "Completed",
    "cancelled": "Cancelled",
}

# Order status lifecycle (Shopify-like)
ORDER_STATUSES = ["placed", "processing", "shipped", "delivered", "cancelled", "returned", "refunded"]
ORDER_STATUS_LABELS = {
    "placed": "Placed",
    "processing": "Processing",
    "shipped": "Shipped",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "returned": "Returned",
    "refunded": "Refunded",
}

# ── Customer location parsing (kaha se order aaya) ──────────────────────────
# Pincode prefix (first 3 digits) → (city, state) for common Indian cities.
# Deterministic + testable; fallback logic below handles the rest.
PINCODE_CITY_MAP = {
    "110": ("Delhi", "Delhi"),
    "121": ("Faridabad", "Haryana"),
    "122": ("Gurugram", "Haryana"),
    "400": ("Mumbai", "Maharashtra"),
    "401": ("Thane", "Maharashtra"),
    "411": ("Pune", "Maharashtra"),
    "500": ("Hyderabad", "Telangana"),
    "560": ("Bengaluru", "Karnataka"),
    "600": ("Chennai", "Tamil Nadu"),
    "641": ("Coimbatore", "Tamil Nadu"),
    "682": ("Kochi", "Kerala"),
    "695": ("Thiruvananthapuram", "Kerala"),
    "700": ("Kolkata", "West Bengal"),
    "380": ("Ahmedabad", "Gujarat"),
    "395": ("Surat", "Gujarat"),
    "302": ("Jaipur", "Rajasthan"),
    "226": ("Lucknow", "Uttar Pradesh"),
    "201": ("Ghaziabad", "Uttar Pradesh"),
    "452": ("Indore", "Madhya Pradesh"),
    "462": ("Bhopal", "Madhya Pradesh"),
}

STATES = [
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "delhi", "jammu and kashmir", "ladakh", "puducherry", "chandigarh",
    "andaman and nicobar islands", "dadra and nagar haveli and daman and diu",
]
STATE_ABBR = {
    "ka": "karnataka", "mh": "maharashtra", "dl": "delhi", "tn": "tamil nadu",
    "ap": "andhra pradesh", "ts": "telangana", "gj": "gujarat", "rj": "rajasthan",
    "up": "uttar pradesh", "wb": "west bengal", "kl": "kerala", "pb": "punjab",
    "hr": "haryana", "mp": "madhya pradesh", "br": "bihar", "od": "odisha",
    "ga": "goa", "uk": "uttarakhand", "cg": "chhattisgarh", "jh": "jharkhand",
    "as": "assam", "sk": "sikkim", "mz": "mizoram", "mn": "manipur",
    "ml": "meghalaya", "nl": "nagaland", "tr": "tripura", "ar": "arunachal pradesh",
    "la": "ladakh", "jk": "jammu and kashmir", "py": "puducherry", "ch": "chandigarh",
}


def parse_location(address: str | None) -> dict[str, str]:
    """Extract city/state/pincode from a free-text delivery address.

    Pincode (6 digits) is the strongest signal → city/state via prefix map.
    Falls back to scanning for known state names/abbreviations, then uses the
    last comma segment of the address as the city.
    """
    addr = (address or "").strip()
    out = {"customer_city": "", "customer_state": "", "customer_pincode": ""}
    if not addr:
        return out
    low = addr.lower()

    m = re.search(r"\b(\d{6})\b", addr)
    if m:
        pincode = m.group(1)
        out["customer_pincode"] = pincode
        city_state = PINCODE_CITY_MAP.get(pincode[:3])
        if city_state:
            out["customer_city"], out["customer_state"] = city_state

    state = None
    for st in STATES:
        if st in low:
            state = st.title()
            break
    if not state:
        for abbr, full in STATE_ABBR.items():
            if re.search(r"\b" + re.escape(abbr) + r"\b", low):
                state = full.title()
                break
    if state and not out["customer_state"]:
        out["customer_state"] = state

    if not out["customer_city"]:
        known = {s.lower() for s in STATES} | set(STATE_ABBR)
        for seg in reversed([s.strip() for s in addr.split(",") if s.strip()]):
            seg_low = seg.lower()
            if not seg or re.search(r"\d", seg) or seg_low in known or seg_low in STATE_ABBR:
                continue
            out["customer_city"] = seg.split()[0][:40]
            break
    return out


def _norm_city_state(order: dict[str, Any]) -> tuple[str, str]:
    """Best-effort location for an order (explicit fields > parse address)."""
    city = str(order.get("customer_city") or "").strip()
    state = str(order.get("customer_state") or "").strip()
    if city or state:
        return city, state
    loc = parse_location(str(order.get("customer_address") or ""))
    return loc["customer_city"], loc["customer_state"]

PRODUCT_FIELDS = {
    "name": "",
    "description": "",
    "price": "",
    "compare_at": "",
    "image_url": "",
    "category": "",
    "sku": "",
    "stock": 0,
    "active": True,
    "featured": False,
    "sort_order": 0,
}

SERVICE_FIELDS = {
    "name": "",
    "description": "",
    "price": "",
    "image_url": "",
    "active": True,
    "sort_order": 0,
}


def store_available() -> bool:
    """True when the gateway/backend is configured (SUPABASE_URL + key set)."""
    return get_config() is not None


def _client_q(client: str) -> str:
    import urllib.parse
    return "client_name=eq." + urllib.parse.quote(client)


def _norm_product(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce PocketBase json fields into stable types for consumers."""
    def s(v: Any) -> str:
        return "" if v is None else str(v)
    out = dict(row)
    out["name"] = s(out.get("name"))
    out["description"] = s(out.get("description"))
    out["price"] = s(out.get("price"))
    out["compare_at"] = s(out.get("compare_at"))
    out["image_url"] = s(out.get("image_url"))
    out["category"] = s(out.get("category"))
    out["sku"] = s(out.get("sku"))
    try:
        out["stock"] = int(out.get("stock") or 0)
    except (TypeError, ValueError):
        out["stock"] = 0
    out["active"] = bool(out.get("active", True))
    out["featured"] = bool(out.get("featured", False))
    try:
        out["sort_order"] = int(out.get("sort_order") or 0)
    except (TypeError, ValueError):
        out["sort_order"] = 0
    return out


def _clean_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known product fields (drops id/created noise)."""
    payload = {}
    for key, default in PRODUCT_FIELDS.items():
        if key in data and data[key] is not None:
            payload[key] = data[key]
    return payload


# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCTS
# ═══════════════════════════════════════════════════════════════════════════════


def list_products(workspace: str, client: str, active_only: bool = False) -> list[dict[str, Any]]:
    """List all products for (workspace, client), newest first."""
    cfg = get_config()
    if not cfg:
        return []
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + PRODUCTS_TABLE + "?select=*&" + _client_q(client) + "&order=created_at.desc",
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: list_products failed: %s", e)
        return []
    products = [_norm_product(r) for r in rows]
    if active_only:
        products = [p for p in products if p["active"]]
    return products


def get_product(workspace: str, client: str, pid: str) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + PRODUCTS_TABLE + "?select=*&id=eq." + pid,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: get_product failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_product(rows[0]) if rows else None


def create_product(workspace: str, client: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Create a product for (workspace, client)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = {"client_name": client, **(_clean_payload(data) or {"name": "Untitled Product"})}
    try:
        rows = _api(
            "POST", url, key,
            "/rest/v1/" + PRODUCTS_TABLE,
            payload,
            profile=schema_for(workspace),
        )
        return _norm_product(rows[0]) if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("store: create_product failed: %s", e)
        return None


def update_product(workspace: str, client: str, pid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Update a product by its PocketBase id."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = _clean_payload(data)
    if not payload:
        return get_product(workspace, client, pid)
    try:
        rows = _api(
            "PATCH", url, key,
            "/rest/v1/" + PRODUCTS_TABLE + "?id=eq." + pid,
            payload,
            profile=schema_for(workspace),
        )
        rows = [r for r in rows if r.get("client_name") == client]
        return _norm_product(rows[0]) if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("store: update_product failed: %s", e)
        return None


def delete_product(workspace: str, client: str, pid: str) -> bool:
    cfg = get_config()
    if not cfg:
        return False
    url, key = cfg
    try:
        _api(
            "DELETE", url, key,
            "/rest/v1/" + PRODUCTS_TABLE + "?id=eq." + pid,
            profile=schema_for(workspace),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("store: delete_product failed: %s", e)
        return False


def product_stats(workspace: str, client: str) -> dict[str, Any]:
    """Aggregate counts for the client dashboard."""
    products = list_products(workspace, client)
    active = [p for p in products if p["active"]]
    return {
        "total": len(products),
        "active": len(active),
        "inactive": len(products) - len(active),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SERVICES
# ═══════════════════════════════════════════════════════════════════════════════


def _norm_service(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a store_services row into stable types."""
    def s(v: Any) -> str:
        return "" if v is None else str(v)
    out = dict(row)
    out["name"] = s(out.get("name"))
    out["description"] = s(out.get("description"))
    out["price"] = s(out.get("price"))
    out["image_url"] = s(out.get("image_url"))
    try:
        out["active"] = bool(out.get("active", True))
    except (TypeError, ValueError):
        out["active"] = True
    try:
        out["sort_order"] = int(out.get("sort_order") or 0)
    except (TypeError, ValueError):
        out["sort_order"] = 0
    return out


def _clean_service_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known service fields."""
    payload = {}
    for key in SERVICE_FIELDS:
        if key in data and data[key] is not None:
            payload[key] = data[key]
    return payload


def list_services(workspace: str, client: str, active_only: bool = False) -> list[dict[str, Any]]:
    """List all services for (workspace, client), lowest sort_order first."""
    cfg = get_config()
    if not cfg:
        return []
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + SERVICES_TABLE + "?select=*&" + _client_q(client) + "&order=sort_order.asc",
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: list_services failed: %s", e)
        return []
    services = [_norm_service(r) for r in rows]
    if active_only:
        services = [s for s in services if s["active"]]
    return services


def create_service(workspace: str, client: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Create a service for (workspace, client)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = {"client_name": client, **(_clean_service_payload(data) or {"name": "Untitled Service"})}
    try:
        rows = _api(
            "POST", url, key,
            "/rest/v1/" + SERVICES_TABLE,
            payload,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: create_service failed: %s", e)
        return None
    if not rows:
        return None
    row = rows[0] if isinstance(rows, list) else rows
    if row.get("client_name") != client:
        logger.warning("store: service insert scoped to wrong client")
        return None
    return _norm_service(row)


def update_service(workspace: str, client: str, sid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Update a service (scoped to client)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = _clean_service_payload(data)
    if not payload:
        return get_service(workspace, client, sid)
    try:
        rows = _api(
            "PATCH", url, key,
            "/rest/v1/" + SERVICES_TABLE + "?id=eq." + sid,
            payload,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: update_service failed: %s", e)
        return None
    # Verify the updated row still belongs to this client.
    found = get_service(workspace, client, sid)
    return found


def delete_service(workspace: str, client: str, sid: str) -> bool:
    """Delete a service (scoped to client)."""
    cfg = get_config()
    if not cfg:
        return False
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + SERVICES_TABLE + "?select=*&id=eq." + sid,
            profile=schema_for(workspace),
        )
        if not rows or rows[0].get("client_name") != client:
            return False
        _api(
            "DELETE", url, key,
            "/rest/v1/" + SERVICES_TABLE + "?id=eq." + sid,
            profile=schema_for(workspace),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("store: delete_service failed: %s", e)
        return False


def get_service(workspace: str, client: str, sid: str) -> dict[str, Any] | None:
    """Fetch one service, scoped to client."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + SERVICES_TABLE + "?select=*&id=eq." + sid,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: get_service failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_service(rows[0]) if rows else None


def service_stats(workspace: str, client: str) -> dict[str, Any]:
    """Aggregate service counts for the client dashboard."""
    services = list_services(workspace, client)
    active = [s for s in services if s["active"]]
    return {
        "total": len(services),
        "active": len(active),
        "inactive": len(services) - len(active),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_SETTINGS: dict[str, Any] = {
    "store_name": "",
    "tagline": "",
    "category": "ecommerce",
    "style": "modern",
    "color_primary": "#2563EB",
    "framework": "nextjs",
    "currency": "₹",
    "show_stock": True,
    "contact_email": "",
    "contact_phone": "",
    "contact_address": "",
    "logo_url": "",
    "whatsapp": "",
    "delivery_charge": "",
    "free_delivery_min": "",
    "payments": {"cod": True, "upi": True, "card": False},
    "banners": [],
    "domain": "",
    # ── Custom meeting/booking (SBA agent books here, owner's own, no Google) ──
    "booking_enabled": False,      # owner toggles; SBA books only when True
    "booking_slot_minutes": 30,   # default meeting length
    "booking_working_hours": "09:00-18:00",  # IST window owner is free
    "booking_timezone": "Asia/Kolkata",
    "booking_advance_hours": 1,    # min hours before a slot
    "booking_slots": [],          # optional fixed slots ["2026-08-20T15:00", ...]
}


def get_settings(workspace: str, client: str) -> dict[str, Any]:
    """Get store settings for (workspace, client), merged over defaults."""
    cfg = get_config()
    row: dict[str, Any] = {}
    if cfg:
        url, key = cfg
        try:
            rows = _api(
                "GET", url, key,
                "/rest/v1/" + SETTINGS_TABLE + "?select=*&" + _client_q(client),
                profile=schema_for(workspace),
            )
            if rows:
                row = dict(rows[0])
        except Exception as e:  # noqa: BLE001
            logger.warning("store: get_settings failed: %s", e)
    out = dict(DEFAULT_SETTINGS)
    for k, v in row.items():
        if k in out and v is not None:
            if k in ("payments", "banners"):
                if isinstance(v, str):
                    try:
                        out[k] = json.loads(v)
                    except (TypeError, ValueError):
                        out[k] = dict(DEFAULT_SETTINGS["payments"]) if k == "payments" else []
                else:
                    out[k] = v
            elif k in ("booking_enabled", "show_stock"):
                # Booleans must stay bool, never stringified to "True"/"False".
                out[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            else:
                out[k] = str(v)
    if not isinstance(out.get("banners"), list):
        out["banners"] = []
    if not isinstance(out.get("payments"), dict):
        out["payments"] = dict(DEFAULT_SETTINGS["payments"])
    # Booking fields (persisted as text/int, normalize to correct types)
    out["booking_enabled"] = out.get("booking_enabled") in (True, "true", "1", 1)
    try:
        out["booking_slot_minutes"] = int(out.get("booking_slot_minutes") or 30)
    except (TypeError, ValueError):
        out["booking_slot_minutes"] = 30
    try:
        out["booking_advance_hours"] = int(out.get("booking_advance_hours") or 1)
    except (TypeError, ValueError):
        out["booking_advance_hours"] = 1
    out.setdefault("booking_working_hours", "09:00-18:00")
    out.setdefault("booking_timezone", "Asia/Kolkata")
    if not isinstance(out.get("booking_slots"), list):
        out["booking_slots"] = []
    return out


def upsert_settings(workspace: str, client: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Upsert store settings (single row per client)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = {"client_name": client}
    for k, v in data.items():
        if k in DEFAULT_SETTINGS and v is not None:
            payload[k] = v
    try:
        rows = _api(
            "POST", url, key,
            "/rest/v1/" + SETTINGS_TABLE,
            payload,
            on_conflict="client_name",
            profile=schema_for(workspace),
        )
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("store: upsert_settings failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# ORDERS + SALES (real store revenue — NOT SBA lead stats)
# ═══════════════════════════════════════════════════════════════════════════════


def _norm_order(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce an order row into stable shapes for consumers."""
    def s(v: Any) -> str:
        return "" if v is None else str(v)
    out = dict(row)
    out["client_name"] = s(out.get("client_name"))
    out["order_number"] = s(out.get("order_number") or out.get("id") or "")
    out["customer_name"] = s(out.get("customer_name"))
    out["customer_email"] = s(out.get("customer_email"))
    out["customer_phone"] = s(out.get("customer_phone"))
    out["customer_address"] = s(out.get("customer_address"))
    for k in ("customer_city", "customer_state", "customer_pincode", "source",
              "tracking_number", "carrier", "dispatch_note", "shipped_at",
              "payment_method", "notes", "coupon_code"):
        out[k] = s(out.get(k))
    if not out.get("source"):
        out["source"] = "Direct"
    city, state = _norm_city_state(out)
    if city and not out.get("customer_city"):
        out["customer_city"] = city
    if state and not out.get("customer_state"):
        out["customer_state"] = state
    out["status"] = s(out.get("status") or "placed")
    try:
        out["total"] = float(out.get("total") or 0)
    except (TypeError, ValueError):
        out["total"] = 0.0
    try:
        out["discount"] = float(out.get("discount") or 0)
    except (TypeError, ValueError):
        out["discount"] = 0.0
    try:
        out["subtotal"] = float(out.get("subtotal") or 0)
    except (TypeError, ValueError):
        out["subtotal"] = 0.0
    items = out.get("items")
    if isinstance(items, str):
        try:
            import json
            items = json.loads(items)
        except Exception:  # noqa: BLE001
            items = []
    if not isinstance(items, list):
        items = []
    out["items"] = items
    return out


def _parse_price(product: dict[str, Any]) -> float:
    """Parse a price value that may include ₹ / commas into a float."""
    try:
        return float(str(product.get("price") or "0").replace("₹", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def place_order(workspace: str, client: str, product_id: str | None = None,
                quantity: int = 1, customer: dict[str, Any] | None = None, *,
                items: list[dict[str, Any]] | None = None,
                payment_method: str = "", notes: str = "",
                coupon_code: str = "") -> dict[str, Any] | None:
    """Place an order (public checkout).

    Accepts either a single ``product_id``/``quantity`` (backwards compatible)
    or a list of ``items`` ``[{"product_id": ..., "quantity": ...}]`` for cart
    checkout. Validates every product exists, is active and in stock, decrements
    each product's stock, and records one order row. Returns the created order.
    """
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    customer = customer or {}

    # Normalize requested items into [(product, qty)]
    if not items:
        items = [{"product_id": product_id, "quantity": quantity}]
    requested: list[dict[str, Any]] = []
    for it in items:
        pid = (it or {}).get("product_id")
        q = max(1, int((it or {}).get("quantity") or 1))
        requested.append({"product_id": pid, "quantity": q})

    # Validate + price every line
    resolved: list[dict[str, Any]] = []
    subtotal = 0.0
    for it in requested:
        product = get_product(workspace, client, it["product_id"])
        if not product:
            return {"error": "Product not found"}
        if not product.get("active", True):
            return {"error": f"'{product.get('name', '')}' is not available"}
        try:
            stock = int(product.get("stock") or 0)
        except (TypeError, ValueError):
            stock = 0
        if stock < it["quantity"]:
            return {"error": f"Not enough stock for '{product.get('name', '')}' (only {stock} left)"}
        price = _parse_price(product)
        resolved.append({
            "product": product,
            "quantity": it["quantity"],
            "price": price,
        })
        subtotal += round(price * it["quantity"], 2)
    if not resolved:
        return {"error": "Order has no items"}

    # Optional coupon discount
    discount = 0.0
    coupon = ""
    if coupon_code and coupon_code.strip():
        coupon = coupon_code.strip().upper()
        valid = validate_coupon(workspace, client, coupon, subtotal)
        if "error" in valid:
            return {"error": valid["error"]}
        discount = round(float(valid.get("discount") or 0), 2)
        if discount > subtotal:
            discount = subtotal
    total = round(subtotal - discount, 2)

    order_number = "ORD-" + str(int(__import__("time").time() * 1000))[-8:]

    loc = parse_location(str(customer.get("address") or ""))
    source = str(customer.get("source") or "").strip() or "Direct"

    payload = {
        "client_name": client,
        "order_number": order_number,
        "product_id": resolved[0]["product"]["id"],
        "product_name": resolved[0]["product"].get("name", ""),
        "quantity": resolved[0]["quantity"],
        "unit_price": resolved[0]["price"],
        "subtotal": subtotal,
        "discount": discount,
        "total": total,
        "coupon_code": coupon,
        "items": [{
            "product_id": r["product"]["id"],
            "name": r["product"].get("name", ""),
            "price": r["price"],
            "quantity": r["quantity"],
        } for r in resolved],
        "customer_name": str(customer.get("name") or "").strip(),
        "customer_email": str(customer.get("email") or "").strip(),
        "customer_phone": str(customer.get("phone") or "").strip(),
        "customer_address": str(customer.get("address") or "").strip(),
        "customer_city": loc["customer_city"],
        "customer_state": loc["customer_state"],
        "customer_pincode": loc["customer_pincode"],
        "source": source,
        "payment_method": str(payment_method or "").strip() or "COD",
        "notes": str(notes or "").strip(),
        "status": "placed",
    }
    try:
        rows = _api(
            "POST", url, key,
            "/rest/v1/" + ORDERS_TABLE,
            payload,
            profile=schema_for(workspace),
        )
        if not rows:
            return {"error": "Order create failed"}
        # Decrement stock for every line item
        for r in resolved:
            try:
                prod = r["product"]
                cur = int(prod.get("stock") or 0)
                update_product(workspace, client, prod["id"], {"stock": max(0, cur - r["quantity"])})
            except Exception:  # noqa: BLE001
                logger.warning("store: stock decrement failed for %s", r["product"]["id"])
        # Count this coupon usage
        if coupon:
            bump_coupon_usage(workspace, client, coupon)
        return _norm_order(rows[0])
    except Exception as e:  # noqa: BLE001
        logger.warning("store: place_order failed: %s", e)
        return {"error": f"Order failed: {e}"}


def get_order(workspace: str, client: str, oid: str) -> dict[str, Any] | None:
    """Fetch one order by id, scoped to (workspace, client)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + ORDERS_TABLE + "?select=*&id=eq." + oid,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: get_order failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_order(rows[0]) if rows else None


def list_orders(workspace: str, client: str, limit: int = 200) -> list[dict[str, Any]]:
    """List orders for (workspace, client), newest first."""
    cfg = get_config()
    if not cfg:
        return []
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + ORDERS_TABLE + "?select=*&" + _client_q(client) + "&order=created_at.desc",
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: list_orders failed: %s", e)
        return []
    return [_norm_order(r) for r in rows][:limit]


def find_order_by_number(workspace: str, client: str, order_number: str) -> dict[str, Any] | None:
    """Fetch one order by its human order number (ORD-xxxx), client-scoped."""
    cfg = get_config()
    if not cfg:
        return None
    import urllib.parse
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + ORDERS_TABLE + "?select=*&order_number=eq." + urllib.parse.quote(str(order_number)),
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: find_order_by_number failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_order(rows[0]) if rows else None


def track_order(workspace: str, client: str, order_number: str, email: str) -> dict[str, Any]:
    """Public order tracking: order_number + email must match.

    Returns a safe summary (status, items, total, dispatch/tracking info)
    with no full personal data beyond what ties the order to its buyer.
    """
    order = find_order_by_number(workspace, client, order_number)
    expected = (order.get("customer_email") or "").strip().lower() if order else ""
    if not order or (email or "").strip().lower() != expected:
        return {"error": "Order nahi mila. Order number + email check karke dobara try karo."}
    return {
        "order_number": order["order_number"],
        "status": order["status"],
        "customer_name": order["customer_name"],
        "total": order["total"],
        "items": order["items"],
        "tracking_number": order.get("tracking_number"),
        "carrier": order.get("carrier"),
        "dispatch_note": order.get("dispatch_note"),
        "payment_method": order.get("payment_method") or "",
        "created_at": order.get("created_at"),
        "shipped_at": order.get("shipped_at"),
    }


def update_order_status(workspace: str, client: str, oid: str, status: str,
                        extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Update an order's status (owner action).

    Validates the status against the known lifecycle and scopes the update
    to (workspace, client). Optional `extra` may carry dispatch fields
    (tracking_number, carrier, dispatch_note); moving to "shipped" stamps
    shipped_at. Returns the updated order or an error dict.
    """
    status = (status or "").strip().lower()
    if status not in ORDER_STATUSES:
        return {"error": f"Invalid status '{status}'. Valid: {', '.join(ORDER_STATUSES)}"}
    cfg = get_config()
    if not cfg:
        return None
    existing = get_order(workspace, client, oid)
    if not existing:
        return {"error": "Order not found"}
    url, key = cfg
    payload: dict[str, Any] = {"status": status}
    if extra:
        for k in ("tracking_number", "carrier", "dispatch_note"):
            # Only overwrite when the caller actually provides a value.
            # Empty strings would wipe previously recorded dispatch info
            # (e.g. advancing shipped -> delivered without re-sending tracking).
            if k in extra and str(extra[k] or "").strip():
                payload[k] = str(extra[k]).strip()
    if status == "shipped" and not existing.get("shipped_at"):
        payload["shipped_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        rows = _api(
            "PATCH", url, key,
            "/rest/v1/" + ORDERS_TABLE + "?id=eq." + oid,
            payload,
            profile=schema_for(workspace),
        )
        rows = [r for r in rows if r.get("client_name") == client]
        if rows:
            return _norm_order(rows[0])
        # Gateway may return empty on PATCH; fall back to re-read.
        updated = get_order(workspace, client, oid)
        return updated if updated else existing
    except Exception as e:  # noqa: BLE001
        logger.warning("store: update_order_status failed: %s", e)
        return {"error": f"Status update failed: {e}"}


def sales_stats(workspace: str, client: str) -> dict[str, Any]:
    """Real store sales: revenue, order count, units sold, top product.

    This is what the client store dashboard shows — actual orders placed
    through the storefront, NOT SBA lead pipeline counts.
    """
    orders = list_orders(workspace, client)
    revenue = round(sum(float(o.get("total") or 0) for o in orders), 2)
    units = 0
    by_product: dict[str, dict[str, Any]] = {}
    for o in orders:
        for item in o.get("items") or []:
            q = int(item.get("quantity") or 0)
            units += q
            pid = str(item.get("product_id") or o.get("product_id") or "?")
            entry = by_product.setdefault(pid, {"name": item.get("name") or o.get("product_name") or "Product", "units": 0, "revenue": 0.0})
            entry["units"] += q
            entry["revenue"] = round(entry["revenue"] + float(item.get("price") or 0) * q, 2)
    top = None
    top_products: list[dict[str, Any]] = []
    if by_product:
        ranked = sorted(by_product.values(), key=lambda e: (e["units"], e["revenue"]), reverse=True)
        top = ranked[0]
        top_products = ranked[:5]
    cities = Counter((_norm_city_state(o)[0] or "Unknown") for o in orders)
    states = Counter((_norm_city_state(o)[1] or "Unknown") for o in orders)
    sources = Counter((o.get("source") or "Direct") for o in orders)
    return {
        "revenue": revenue,
        "orders": len(orders),
        "units": units,
        "avg_order": round(revenue / len(orders), 2) if orders else 0.0,
        "top_product": top,
        "top_products": top_products,
        "status_breakdown": {s: sum(1 for o in orders if (o.get("status") or "placed") == s) for s in {o.get("status") or "placed" for o in orders}},
        "cities": [{"city": c, "orders": n} for c, n in cities.most_common(5)],
        "states": [{"state": s, "orders": n} for s, n in states.most_common(5)],
        "sources": [{"source": s, "orders": n} for s, n in sources.most_common(5)],
        "views": view_count(workspace, client),
        "source": "orders",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STORE VIEWS (public storefront pageviews)
# ═══════════════════════════════════════════════════════════════════════════════


def record_view(workspace: str, client: str) -> bool:
    """Record one pageview of the client's public storefront.

    Uses the existing website_build_log table with event_type='page_view'
    so no new table is needed. Returns True when recorded.
    """
    cfg = get_config()
    if not cfg:
        return False
    url, key = cfg
    try:
        _api(
            "POST", url, key,
            "/rest/v1/website_build_log",
            {"client_name": client, "event_type": "page_view", "message": "storefront view", "actor": "storefront"},
            profile=schema_for(workspace),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("store: record_view failed: %s", e)
        return False


def view_count(workspace: str, client: str) -> int:
    """Count pageviews of the client's public storefront."""
    cfg = get_config()
    if not cfg:
        return 0
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/website_build_log?select=id&event_type=eq.page_view&"
            + _client_q(client) + "&limit=100000",
            profile=schema_for(workspace),
        )
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("store: view_count failed: %s", e)
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# COUPONS / DISCOUNT CODES (owner creates, customers apply at checkout)
# ═══════════════════════════════════════════════════════════════════════════════

COUPON_FIELDS = {
    "code": "",
    "discount_type": "percent",  # 'percent' | 'flat'
    "discount_value": 0,
    "min_order": 0,
    "max_uses": 0,               # 0 = unlimited
    "used_count": 0,
    "active": True,
    "expires_at": "",
}


def _norm_coupon(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a store_coupons row into stable types."""
    def s(v: Any) -> str:
        return "" if v is None else str(v)
    out = dict(row)
    out["code"] = s(out.get("code")).strip().upper()
    out["discount_type"] = s(out.get("discount_type") or "percent")
    try:
        out["discount_value"] = float(out.get("discount_value") or 0)
    except (TypeError, ValueError):
        out["discount_value"] = 0.0
    try:
        out["min_order"] = float(out.get("min_order") or 0)
    except (TypeError, ValueError):
        out["min_order"] = 0.0
    try:
        out["max_uses"] = int(out.get("max_uses") or 0)
    except (TypeError, ValueError):
        out["max_uses"] = 0
    try:
        out["used_count"] = int(out.get("used_count") or 0)
    except (TypeError, ValueError):
        out["used_count"] = 0
    out["active"] = bool(out.get("active", True))
    out["expires_at"] = s(out.get("expires_at"))
    return out


def _clean_coupon_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = {}
    for key in COUPON_FIELDS:
        if key in data and data[key] is not None:
            payload[key] = data[key]
    if "code" in payload and payload["code"] is not None:
        payload["code"] = str(payload["code"]).strip().upper()
    return payload


def list_coupons(workspace: str, client: str) -> list[dict[str, Any]]:
    cfg = get_config()
    if not cfg:
        return []
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + COUPONS_TABLE + "?select=*&" + _client_q(client) + "&order=created_at.desc",
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: list_coupons failed: %s", e)
        return []
    return [_norm_coupon(r) for r in rows]


def get_coupon(workspace: str, client: str, cid: str) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + COUPONS_TABLE + "?select=*&id=eq." + cid,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: get_coupon failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_coupon(rows[0]) if rows else None


def find_coupon(workspace: str, client: str, code: str) -> dict[str, Any] | None:
    """Find a coupon by its (case-insensitive) code, client-scoped."""
    cfg = get_config()
    if not cfg:
        return None
    import urllib.parse
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + COUPONS_TABLE + "?select=*&code=eq." + urllib.parse.quote(str(code).strip().upper()),
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: find_coupon failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_coupon(rows[0]) if rows else None


def create_coupon(workspace: str, client: str, data: dict[str, Any]) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = {"client_name": client, **(_clean_coupon_payload(data) or {"code": "SAVE10"})}
    if not str(payload.get("code") or "").strip():
        return None
    if find_coupon(workspace, client, str(payload["code"])):
        return {"error": "Is code ka coupon pehle se hai"}
    try:
        rows = _api(
            "POST", url, key,
            "/rest/v1/" + COUPONS_TABLE,
            payload,
            profile=schema_for(workspace),
        )
        return _norm_coupon(rows[0]) if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("store: create_coupon failed: %s", e)
        return None


def update_coupon(workspace: str, client: str, cid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = _clean_coupon_payload(data)
    if not payload:
        return get_coupon(workspace, client, cid)
    try:
        rows = _api(
            "PATCH", url, key,
            "/rest/v1/" + COUPONS_TABLE + "?id=eq." + cid,
            payload,
            profile=schema_for(workspace),
        )
        rows = [r for r in rows if r.get("client_name") == client]
        if rows:
            return _norm_coupon(rows[0])
        return get_coupon(workspace, client, cid)
    except Exception as e:  # noqa: BLE001
        logger.warning("store: update_coupon failed: %s", e)
        return None


def delete_coupon(workspace: str, client: str, cid: str) -> bool:
    cfg = get_config()
    if not cfg:
        return False
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + COUPONS_TABLE + "?select=*&id=eq." + cid,
            profile=schema_for(workspace),
        )
        if not rows or rows[0].get("client_name") != client:
            return False
        _api(
            "DELETE", url, key,
            "/rest/v1/" + COUPONS_TABLE + "?id=eq." + cid,
            profile=schema_for(workspace),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("store: delete_coupon failed: %s", e)
        return False


def validate_coupon(workspace: str, client: str, code: str, subtotal: float) -> dict[str, Any]:
    """Validate a coupon code against an order subtotal.

    Returns {"discount": float} on success or {"error": str} when invalid.
    """
    coupon = find_coupon(workspace, client, code)
    if not coupon:
        return {"error": f"Coupon '{code}' nahi mila"}
    if not coupon.get("active", True):
        return {"error": "Ye coupon abhi active nahi hai"}
    now = datetime.datetime.now(datetime.timezone.utc)
    expires = coupon.get("expires_at") or ""
    if expires:
        try:
            exp = datetime.datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=datetime.timezone.utc)
            if now > exp:
                return {"error": "Ye coupon expire ho chuka hai"}
        except (TypeError, ValueError):
            pass
    try:
        min_order = float(coupon.get("min_order") or 0)
    except (TypeError, ValueError):
        min_order = 0.0
    if subtotal < min_order:
        return {"error": f"Ye coupon ₹{min_order:g} ya usse upar ke order pe lagta hai"}
    try:
        max_uses = int(coupon.get("max_uses") or 0)
        used = int(coupon.get("used_count") or 0)
    except (TypeError, ValueError):
        max_uses, used = 0, 0
    if max_uses and used >= max_uses:
        return {"error": "Ye coupon apni limit tak use ho chuka hai"}
    dtype = str(coupon.get("discount_type") or "percent")
    value = float(coupon.get("discount_value") or 0)
    if dtype == "flat":
        discount = min(value, subtotal)
    else:
        discount = round(subtotal * min(value, 100) / 100, 2)
    return {"discount": discount, "coupon": coupon.get("code", "").upper()}


def bump_coupon_usage(workspace: str, client: str, code: str) -> None:
    """Increment used_count after a successful order with a coupon."""
    coupon = find_coupon(workspace, client, code)
    if not coupon:
        return
    cfg = get_config()
    if not cfg:
        return
    url, key = cfg
    try:
        used = int(coupon.get("used_count") or 0) + 1
        _api(
            "PATCH", url, key,
            "/rest/v1/" + COUPONS_TABLE + "?id=eq." + str(coupon.get("id")),
            {"used_count": used},
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: bump_coupon_usage failed: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# REVIEWS (customers rate products; owner moderates)
# ═══════════════════════════════════════════════════════════════════════════════

REVIEW_FIELDS = {
    "product_id": "",
    "product_name": "",
    "rating": 5,
    "reviewer_name": "",
    "reviewer_email": "",
    "comment": "",
    "approved": False,
}


def _norm_review(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a store_reviews row into stable types."""
    def s(v: Any) -> str:
        return "" if v is None else str(v)
    out = dict(row)
    out["product_id"] = s(out.get("product_id"))
    out["product_name"] = s(out.get("product_name"))
    out["reviewer_name"] = s(out.get("reviewer_name"))
    out["reviewer_email"] = s(out.get("reviewer_email"))
    out["comment"] = s(out.get("comment"))
    try:
        raw_rating = out.get("rating")
        rating = int(raw_rating) if raw_rating not in (None, "") else 5
        out["rating"] = max(1, min(5, rating))
    except (TypeError, ValueError):
        out["rating"] = 5
    out["approved"] = bool(out.get("approved", False))
    return out


def _clean_review_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = {}
    for key in REVIEW_FIELDS:
        if key in data and data[key] is not None:
            payload[key] = data[key]
    return payload


def list_reviews(workspace: str, client: str, product_id: str = "",
                 approved_only: bool = False) -> list[dict[str, Any]]:
    cfg = get_config()
    if not cfg:
        return []
    import urllib.parse
    url, key = cfg
    q = "/rest/v1/" + REVIEWS_TABLE + "?select=*&" + _client_q(client)
    if product_id:
        q += "&product_id=eq." + urllib.parse.quote(product_id)
    q += "&order=created_at.desc"
    try:
        rows = _api("GET", url, key, q, profile=schema_for(workspace))
    except Exception as e:  # noqa: BLE001
        logger.warning("store: list_reviews failed: %s", e)
        return []
    reviews = [_norm_review(r) for r in rows]
    if approved_only:
        reviews = [r for r in reviews if r["approved"]]
    return reviews


def create_review(workspace: str, client: str, data: dict[str, Any]) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = {"client_name": client, **(_clean_review_payload(data) or {})}
    if not str(payload.get("product_id") or "").strip():
        return None
    # One review per (email, product): update instead of duplicate
    email = str(payload.get("reviewer_email") or "").strip().lower()
    if email:
        existing = list_reviews(workspace, client, product_id=str(payload["product_id"]))
        for r in existing:
            if str(r.get("reviewer_email") or "").strip().lower() == email:
                cid = str(r.get("id") or "")
                if cid:
                    return update_review(workspace, client, cid, payload)
    try:
        rows = _api(
            "POST", url, key,
            "/rest/v1/" + REVIEWS_TABLE,
            payload,
            profile=schema_for(workspace),
        )
        return _norm_review(rows[0]) if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("store: create_review failed: %s", e)
        return None


def update_review(workspace: str, client: str, rid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = _clean_review_payload(data)
    if not payload:
        return get_review(workspace, client, rid)
    try:
        rows = _api(
            "PATCH", url, key,
            "/rest/v1/" + REVIEWS_TABLE + "?id=eq." + rid,
            payload,
            profile=schema_for(workspace),
        )
        rows = [r for r in rows if r.get("client_name") == client]
        if rows:
            return _norm_review(rows[0])
        return get_review(workspace, client, rid)
    except Exception as e:  # noqa: BLE001
        logger.warning("store: update_review failed: %s", e)
        return None


def get_review(workspace: str, client: str, rid: str) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + REVIEWS_TABLE + "?select=*&id=eq." + rid,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: get_review failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_review(rows[0]) if rows else None


def delete_review(workspace: str, client: str, rid: str) -> bool:
    cfg = get_config()
    if not cfg:
        return False
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + REVIEWS_TABLE + "?select=*&id=eq." + rid,
            profile=schema_for(workspace),
        )
        if not rows or rows[0].get("client_name") != client:
            return False
        _api(
            "DELETE", url, key,
            "/rest/v1/" + REVIEWS_TABLE + "?id=eq." + rid,
            profile=schema_for(workspace),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("store: delete_review failed: %s", e)
        return False


def review_stats(workspace: str, client: str) -> dict[str, Any]:
    """Aggregate ratings per product (approved reviews only) for the UI."""
    reviews = list_reviews(workspace, client, approved_only=True)
    by_product: dict[str, dict[str, Any]] = {}
    for r in reviews:
        pid = str(r.get("product_id") or "?")
        entry = by_product.setdefault(pid, {"count": 0, "total": 0, "avg": 0.0})
        entry["count"] += 1
        entry["total"] += int(r.get("rating") or 0)
    for e in by_product.values():
        e["avg"] = round(e["total"] / e["count"], 1) if e["count"] else 0.0
        e.pop("total", None)
    all_ratings = [int(r.get("rating") or 0) for r in reviews]
    return {
        "total": len(reviews),
        "avg": round(sum(all_ratings) / len(all_ratings), 1) if all_ratings else 0.0,
        "by_product": by_product,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MEETINGS / BOOKING (custom SBA booking — owner's OWN calendar, NO Google)
# ═══════════════════════════════════════════════════════════════════════════════

MEETING_FIELDS = {
    "lead_id": "",
    "lead_name": "",
    "lead_email": "",
    "lead_phone": "",
    "title": "",
    "purpose": "",
    "date": "",
    "time": "",            # "HH:MM" (owner local / booking_timezone)
    "duration_minutes": 30,
    "status": "requested",  # requested -> confirmed -> completed / cancelled
    "owner_link": "",       # deep link owner uses to confirm/cancel
    "booking_token": "",    # opaque id used in the owner link
    "notes": "",
    "calendar_event": False,  # always False now — no external Google Calendar
    "source": "sba_autopilot",
}

import time as _time  # noqa: E402  (placed late to keep table defs above)


def _norm_meeting(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a store_meetings row into stable types for consumers."""
    def s(v: Any) -> str:
        return "" if v is None else str(v)
    out = dict(row)
    out["lead_id"] = s(out.get("lead_id"))
    out["lead_name"] = s(out.get("lead_name"))
    out["lead_email"] = s(out.get("lead_email"))
    out["lead_phone"] = s(out.get("lead_phone"))
    out["title"] = s(out.get("title"))
    out["purpose"] = s(out.get("purpose"))
    out["date"] = s(out.get("date"))
    out["time"] = s(out.get("time"))
    out["owner_link"] = s(out.get("owner_link"))
    out["booking_token"] = s(out.get("booking_token"))
    out["notes"] = s(out.get("notes"))
    try:
        out["duration_minutes"] = int(out.get("duration_minutes") or 30)
    except (TypeError, ValueError):
        out["duration_minutes"] = 30
    out["status"] = s(out.get("status") or "requested")
    out["calendar_event"] = False  # custom booking: never Google
    out["source"] = s(out.get("source") or "sba_autopilot")
    return out


def _clean_meeting_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known meeting fields; defaults applied."""
    payload: dict[str, Any] = {"client_name": data.get("client_name", "")}
    for key, default in MEETING_FIELDS.items():
        if key in data and data[key] is not None:
            payload[key] = data[key]
    return payload


def create_meeting_request(workspace: str, client: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Create a meeting/booking request (SBA agent calls this).

    Saves entirely in the owner's store (store_meetings), NOT Google Calendar.
    Returns the created meeting row (with owner_link + booking_token) or None.
    """
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    token = "bk_" + str(int(_time.time() * 1000))[-10:] + _new_bk_suffix()
    base = (data.get("owner_link_base") or "").strip()
    owner_link = f"{base.rstrip('/')}/store/{_slug_for(workspace)}?owner=1&booking={token}" if base else f"?booking={token}"
    payload = _clean_meeting_payload({
        "client_name": client,
        "lead_id": data.get("lead_id", ""),
        "lead_name": data.get("lead_name", ""),
        "lead_email": data.get("lead_email", ""),
        "lead_phone": data.get("lead_phone", ""),
        "title": data.get("title", "Meeting with TAGS Agency"),
        "purpose": data.get("purpose", ""),
        "date": data.get("date", ""),
        "time": data.get("time", ""),
        "duration_minutes": data.get("duration_minutes", 30),
        "status": "requested",
        "owner_link": owner_link,
        "booking_token": token,
        "notes": data.get("notes", ""),
        "source": data.get("source", "sba_autopilot"),
    })
    try:
        rows = _api(
            "POST", url, key,
            "/rest/v1/" + MEETINGS_TABLE,
            payload,
            profile=schema_for(workspace),
        )
        return _norm_meeting(rows[0]) if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("store: create_meeting_request failed: %s", e)
        return None


def _new_bk_suffix() -> str:
    import random
    import string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _slug_for(workspace: str) -> str:
    """Best-effort store slug for the owner link (matches store route)."""
    try:
        from admin.agency.workspace_provision import slug_for as _slug
        return _slug(workspace)
    except Exception:  # noqa: BLE001
        return workspace


def get_meeting_request(workspace: str, client: str, mid: str) -> dict[str, Any] | None:
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + MEETINGS_TABLE + "?select=*&id=eq." + mid,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: get_meeting_request failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_meeting(rows[0]) if rows else None


def find_meeting_by_token(workspace: str, client: str, token: str) -> dict[str, Any] | None:
    """Look up a meeting by its opaque booking_token (used by owner link)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    try:
        rows = _api(
            "GET", url, key,
            "/rest/v1/" + MEETINGS_TABLE + "?select=*&booking_token=eq." + token,
            profile=schema_for(workspace),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("store: find_meeting_by_token failed: %s", e)
        return None
    rows = [r for r in rows if r.get("client_name") == client]
    return _norm_meeting(rows[0]) if rows else None


def list_meeting_requests(workspace: str, client: str, status: str = "") -> list[dict[str, Any]]:
    cfg = get_config()
    if not cfg:
        return []
    url, key = cfg
    q = "/rest/v1/" + MEETINGS_TABLE + "?select=*&" + _client_q(client) + "&order=created_at.desc"
    if status:
        q += "&status=eq." + status
    try:
        rows = _api("GET", url, key, q, profile=schema_for(workspace))
    except Exception as e:  # noqa: BLE001
        logger.warning("store: list_meeting_requests failed: %s", e)
        return []
    return [_norm_meeting(r) for r in rows]


def update_meeting_request(workspace: str, client: str, mid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Update a meeting (owner confirms/cancels, or autopilot edits time)."""
    cfg = get_config()
    if not cfg:
        return None
    url, key = cfg
    payload = {k: v for k, v in data.items() if k in MEETING_FIELDS and v is not None}
    if not payload:
        return get_meeting_request(workspace, client, mid)
    try:
        rows = _api(
            "PATCH", url, key,
            "/rest/v1/" + MEETINGS_TABLE + "?id=eq." + mid,
            payload,
            profile=schema_for(workspace),
        )
        rows = [r for r in rows if r.get("client_name") == client]
        if rows:
            return _norm_meeting(rows[0])
        return get_meeting_request(workspace, client, mid)
    except Exception as e:  # noqa: BLE001
        logger.warning("store: update_meeting_request failed: %s", e)
        return None


def set_meeting_status(workspace: str, client: str, mid: str, status: str,
                       notes: str = "") -> dict[str, Any] | None:
    if status not in MEETING_STATUSES:
        return {"error": f"Invalid status '{status}'. Valid: {', '.join(MEETING_STATUSES)}"}
    data: dict[str, Any] = {"status": status}
    if notes:
        data["notes"] = notes
    return update_meeting_request(workspace, client, mid, data)


def meeting_link_for(meeting: dict[str, Any]) -> str:
    """Public-facing confirmation link (owner clicks to confirm/cancel)."""
    return str(meeting.get("owner_link") or "")


# ── Booking settings (read/write the booking_* fields on the settings row) ─────


def get_booking_settings(workspace: str, client: str) -> dict[str, Any]:
    """Return the booking configuration for a store (defaults when unset)."""
    settings = get_settings(workspace, client)
    raw_enabled = settings.get("booking_enabled", False)
    if isinstance(raw_enabled, str):
        raw_enabled = raw_enabled.strip().lower() in ("1", "true", "yes", "on")
    return {
        "booking_enabled": bool(raw_enabled),
        "booking_slot_minutes": int(settings.get("booking_slot_minutes") or 30),
        "booking_working_hours": settings.get("booking_working_hours") or "09:00-18:00",
        "booking_timezone": settings.get("booking_timezone") or "Asia/Kolkata",
        "booking_advance_hours": int(settings.get("booking_advance_hours") or 1),
        "booking_slots": settings.get("booking_slots") or [],
    }


def update_booking_settings(workspace: str, client: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Upsert the booking_* fields on the store settings row."""
    allowed = {
        "booking_enabled", "booking_slot_minutes", "booking_working_hours",
        "booking_timezone", "booking_advance_hours", "booking_slots",
    }
    payload = {k: v for k, v in (data or {}).items() if k in allowed}
    if not payload:
        return get_settings(workspace, client)
    if "booking_enabled" in payload:
        payload["booking_enabled"] = bool(payload["booking_enabled"])
    for int_key in ("booking_slot_minutes", "booking_advance_hours"):
        if int_key in payload:
            try:
                payload[int_key] = int(payload[int_key])
            except (TypeError, ValueError):
                payload.pop(int_key, None)
    updated = upsert_settings(workspace, client, payload)
    if updated is None:
        return None
    return get_booking_settings(workspace, client)
