"""Autonomous client service delivery.

Closes the SBA -> client workspace -> Website/SEO/Content delivery gap with a
small durable job record. Local website files are always safe to create. Email
follow-up remains explicitly gated by owner opt-in and working SMTP credentials.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from admin.persistence import get_workspace_db, row_to_dict

logger = logging.getLogger("agency.service_delivery")

_DELIVERY_TABLE = """
CREATE TABLE IF NOT EXISTS service_deliveries (
    id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    client_name TEXT NOT NULL DEFAULT '',
    service_type TEXT NOT NULL DEFAULT 'website_seo_content',
    status TEXT NOT NULL DEFAULT 'queued',
    website_status TEXT NOT NULL DEFAULT 'pending',
    seo_status TEXT NOT NULL DEFAULT 'pending',
    content_status TEXT NOT NULL DEFAULT 'pending',
    output_dir TEXT NOT NULL DEFAULT '',
    report_path TEXT NOT NULL DEFAULT '',
    website_result TEXT NOT NULL DEFAULT '{}',
    seo_result TEXT NOT NULL DEFAULT '{}',
    content_result TEXT NOT NULL DEFAULT '{}',
    offer_value REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    followup_status TEXT NOT NULL DEFAULT 'ready',
    followup_due_at TEXT,
    followup_subject TEXT NOT NULL DEFAULT '',
    followup_body TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_TERMINAL_STATUSES = {"delivered", "partial", "failed"}
_ACTIVE_STATUSES = {"queued", "running"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ALLOWED_UPDATES = {
    "status", "website_status", "seo_status", "content_status",
    "output_dir", "report_path", "website_result", "seo_result", "content_result",
    "offer_value", "currency", "client_name", "service_type", "workspace_id",
    "followup_status", "followup_due_at", "followup_subject", "followup_body",
    "error",
}
_running_handoffs: set[str] = set()
_running_handoffs_guard = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _deserialize(row: Any) -> dict[str, Any]:
    item = row_to_dict(row)
    for key in ("website_result", "seo_result", "content_result"):
        item[key] = _loads(item.get(key), {})
    return item


async def _ensure_table() -> None:
    db = await get_workspace_db()
    await db.execute(_DELIVERY_TABLE)
    await db.commit()


async def create_delivery(
    handoff_id: str,
    workspace_id: str,
    client_name: str = "",
    service_type: str = "website_seo_content",
    offer_value: float | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """Create or reuse exactly one delivery row for a handoff."""
    await _ensure_table()
    delivery_id = "del_" + uuid.uuid4().hex[:16]
    now = _now()
    db = await get_workspace_db()
    await db.execute(
        """
        INSERT OR IGNORE INTO service_deliveries
        (id, handoff_id, workspace_id, client_name, service_type, status,
         offer_value, currency, followup_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, 'ready', ?, ?)
        """,
        (delivery_id, handoff_id, workspace_id, client_name, service_type,
         offer_value, currency, now, now),
    )
    await db.commit()
    delivery = await get_delivery_by_handoff(handoff_id)
    if delivery:
        return delivery
    # Defensive fallback for an unusual DB race.
    db = await get_workspace_db()
    async with db.execute(
        "SELECT * FROM service_deliveries WHERE handoff_id = ?", (handoff_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return _deserialize(row) if row else {"error": "delivery row unavailable"}


async def get_delivery(delivery_id: str) -> dict[str, Any] | None:
    await _ensure_table()
    db = await get_workspace_db()
    async with db.execute(
        "SELECT * FROM service_deliveries WHERE id = ?", (delivery_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return _deserialize(row) if row else None


async def get_delivery_by_handoff(handoff_id: str) -> dict[str, Any] | None:
    await _ensure_table()
    db = await get_workspace_db()
    async with db.execute(
        "SELECT * FROM service_deliveries WHERE handoff_id = ?", (handoff_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return _deserialize(row) if row else None


async def list_deliveries(
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    await _ensure_table()
    clauses: list[str] = []
    params: list[Any] = []
    if workspace_id:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    db = await get_workspace_db()
    async with db.execute(
        f"SELECT * FROM service_deliveries{where} ORDER BY created_at DESC LIMIT ?",
        (*params, max(1, min(int(limit), 500))),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_deserialize(row) for row in rows]


async def update_delivery(delivery_id: str, **fields: Any) -> dict[str, Any] | None:
    """Update only delivery-owned fields; IDs and handoff linkage stay immutable."""
    updates = {key: value for key, value in fields.items() if key in _ALLOWED_UPDATES}
    if not updates:
        return await get_delivery(delivery_id)
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = [_json(value) if key.endswith("_result") else value for key, value in updates.items()]
    db = await get_workspace_db()
    await db.execute(
        f"UPDATE service_deliveries SET {assignments} WHERE id = ?",
        (*values, delivery_id),
    )
    await db.commit()
    return await get_delivery(delivery_id)


async def _brief_from_handoff(handoff: dict[str, Any]) -> dict[str, Any]:
    brief = handoff.get("brief") if isinstance(handoff.get("brief"), dict) else {}
    full_dump = handoff.get("full_dump") if isinstance(handoff.get("full_dump"), dict) else {}
    lead = full_dump.get("lead") if isinstance(full_dump.get("lead"), dict) else {}
    lead_context = lead.get("context") if isinstance(lead.get("context"), dict) else {}
    brief_context = brief.get("context") if isinstance(brief.get("context"), dict) else {}
    context = lead_context or brief_context
    business_name = str(brief.get("business_name") or brief.get("lead_name") or "New Client").strip()
    industry = str(brief.get("industry") or context.get("industry") or "local business").strip()
    needs = brief.get("client_needs") or context.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    elif not isinstance(needs, list):
        needs = []
    services = ", ".join(str(item) for item in needs if str(item).strip())
    scope = str(brief.get("agreed_scope") or context.get("scope") or services or "website, local SEO, and content")
    email = str(brief.get("email") or "").strip()
    offer_value = lead_context.get("deal_value", lead_context.get("estimated_value"))
    try:
        offer_value = float(offer_value) if offer_value is not None else None
    except (TypeError, ValueError):
        offer_value = None
    return {
        "business_name": business_name or "New Client",
        "industry": industry or "local business",
        "services": services,
        "scope": scope,
        "email": email,
        "offer_value": offer_value,
        "currency": str(lead_context.get("currency") or "USD"),
    }


def _delivery_dir(workspace_id: str) -> Path:
    if not _SAFE_ID.fullmatch(str(workspace_id)):
        raise ValueError("workspace_id contains unsafe characters")
    root = Path(os.getenv("TAGS_DATA_DIR", "data")).resolve()
    candidate = (root / "deliveries" / str(workspace_id)).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("delivery path escapes TAGS_DATA_DIR")
    return candidate


def _worker_args(brief: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    return {
        "title": brief["business_name"],
        "tagline": f"{brief['industry']} services",
        "industry": brief["industry"],
        "sections": "hero,services,about,contact",
        "category": "business",
        "style": "modern",
        "color_primary": "#2563EB",
        "framework": "html",
        "services": brief["services"],
        "business_email": brief["email"],
        "output_dir": str(output_dir),
    }


async def _run_specialist(agent_type: str, task: str, scope: dict[str, Any]) -> dict[str, Any]:
    """Run an existing specialist worker, with a direct-tool safety fallback."""
    from admin.agency import workers as workers_mod

    if not workers_mod.WORKERS:
        await workers_mod.register_builtins()
    timeout = max(5, int(os.getenv("AGENCY_DELIVERY_SPECIALIST_TIMEOUT_SECONDS", "45")))
    worker_result: dict[str, Any] | None = None
    try:
        worker_result = await asyncio.wait_for(
            workers_mod.run_worker(agent_type, task, {"scope": scope}), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("specialist worker %s timed out after %ss", agent_type, timeout)
        if agent_type != "website":
            return {"ok": False, "agent": agent_type, "tool": "", "result": {"error": "specialist timeout"}}
    except Exception as exc:  # noqa: BLE001
        logger.warning("specialist worker %s failed; using direct tool fallback: %s", agent_type, exc)
        if agent_type != "website":
            return {"ok": False, "agent": agent_type, "tool": "", "result": {"error": "specialist unavailable"}}

    needs_fallback = (
        worker_result is None
        or worker_result.get("ok") is False
        or not isinstance(worker_result.get("result"), dict)
        or worker_result["result"].get("status") in {"failed", "error"}
        or bool(worker_result.get("error"))
        or bool((worker_result.get("result") or {}).get("error"))
    )
    if not needs_fallback:
        return worker_result

    # Website has a deterministic local fallback; SEO/content use bounded
    # deterministic fallbacks below rather than starting another long network call.
    if agent_type != "website":
        return {"ok": False, "agent": agent_type, "tool": "", "result": {"error": "specialist unavailable"}}
    try:
        from admin.tools.website_tools import execute_website_tool
        args = _loads(task.partition("|")[2], {}) if "|" in task else {}
        result = await asyncio.to_thread(execute_website_tool, "build_site", args)
        if isinstance(result, dict) and result.get("status") not in {"failed", "error"} and not result.get("error"):
            return {"ok": True, "agent": agent_type, "tool": _tool_name(agent_type), "result": result}
        logger.warning("direct fallback for website returned an error payload")
    except Exception as exc:  # noqa: BLE001
        logger.warning("direct fallback for website failed: %s", exc)
    return {"ok": False, "agent": agent_type, "tool": "", "result": {"error": "specialist unavailable"}}


def _tool_name(agent_type: str) -> str:
    return {
        "website": "build_site",
        "seo": "keyword_research",
        "content": "generate_content_brief",
    }.get(agent_type, "")


def _result_payload(worker_result: dict[str, Any]) -> dict[str, Any]:
    if worker_result.get("error") and not worker_result.get("result"):
        return {"error": worker_result["error"]}
    result = worker_result.get("result") or {}
    if isinstance(result, dict) and isinstance(result.get("result"), dict):
        result = result["result"]
    return result if isinstance(result, dict) else {"value": result}


def _stage_status(worker_result: dict[str, Any], result: dict[str, Any]) -> str:
    if worker_result.get("ok") is False or result.get("status") in {"failed", "error"} or result.get("error"):
        return "failed"
    if result.get("status") in {"fallback", "partial"}:
        return "partial"
    return "completed"


def _fallback_seo(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "fallback",
        "seed_keyword": brief["industry"],
        "checklist": [
            "Add a unique title tag and meta description to the home page.",
            "Use one descriptive H1 and service/location headings.",
            "Add LocalBusiness JSON-LD with the client name and service area.",
            "Add alt text to meaningful images and internal links to contact/services.",
        ],
        "note": "SEO worker result unavailable; deterministic local checklist created.",
    }


def _fallback_content(brief: dict[str, Any]) -> dict[str, Any]:
    topic = f"{brief['business_name']} {brief['industry']}"
    return {
        "status": "fallback",
        "topic": topic,
        "outline": [
            f"What to expect from {brief['business_name']}",
            f"Why {brief['industry']} matters for local customers",
            "Services, proof, and how to book",
        ],
        "cta": "Contact the business for a clear next step.",
        "note": "Content worker result unavailable; deterministic brief created.",
    }


def _fallback_website(brief: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    name = html.escape(brief["business_name"], quote=True)
    industry = html.escape(str(brief["industry"]), quote=True)
    services = html.escape(str(brief.get("services") or "Contact us to learn about our services."), quote=True)
    email = html.escape(str(brief.get("email", "")), quote=True)
    email_html = f'<a href="mailto:{email}">{email}</a>' if email else "Add a contact email"
    html_doc = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{name}</title><style>body{{font:16px/1.6 system-ui,sans-serif;max-width:960px;margin:0 auto;padding:2rem;color:#172033}}header{{border-bottom:4px solid #2563eb;padding-bottom:1rem}}h1{{margin:.2rem 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}}section{{border:1px solid #e5e7eb;padding:1rem;border-radius:.75rem}}a{{color:#2563eb}}</style></head>"
        f"<body><header><small>Local business website</small><h1>{name}</h1><p>{industry} services for local customers.</p></header><main><section><h2>Services</h2><p>{services}</p></section><section><h2>About</h2><p>We help nearby customers with reliable, clear service and honest communication.</p></section><section><h2>Contact</h2><p>{email_html}</p></section></main></body></html>"
    )
    path = output_dir / "index.html"
    path.write_text(html_doc, encoding="utf-8")
    return {"status": "fallback", "output_dir": str(output_dir), "files_written": ["index.html"], "note": "Deterministic local HTML created after worker failure."}


def _write_report(delivery: dict[str, Any], brief: dict[str, Any]) -> str:
    output_dir = Path(delivery["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "delivery-report.md"
    lines = [
        f"# Client Delivery Report: {brief['business_name']}",
        "",
        f"- Handoff: `{delivery['handoff_id']}`",
        f"- Workspace: `{delivery['workspace_id']}`",
        f"- Service: {delivery['service_type']}",
        f"- Offer value: {delivery.get('offer_value') or 'not provided'} {delivery.get('currency') or 'USD'}",
        f"- Website status: {delivery['website_status']}",
        f"- SEO status: {delivery['seo_status']}",
        f"- Content status: {delivery['content_status']}",
        "",
        "## Website",
        f"Output directory: `{delivery['output_dir']}`",
        "",
        "## SEO",
        json.dumps(delivery.get("seo_result") or {}, indent=2, default=str),
        "",
        "## Content",
        json.dumps(delivery.get("content_result") or {}, indent=2, default=str),
        "",
        "## Follow-up",
        f"Due: {delivery.get('followup_due_at') or 'not scheduled'}",
        delivery.get("followup_subject") or "",
        delivery.get("followup_body") or "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


async def _queue_or_send_followup(delivery: dict[str, Any], brief: dict[str, Any], send: bool = False) -> str:
    # Do not resend an already completed follow-up.
    if delivery.get("followup_status") == "sent":
        return "sent"
    due = (datetime.now(timezone.utc) + timedelta(days=int(os.getenv("AGENCY_DELIVERY_FOLLOWUP_DAYS", "7")))).isoformat()
    subject = f"Your new website is ready — next step for {brief['business_name']}"
    body = (
        f"Hi {brief['business_name']},\n\n"
        "Your website build, SEO checklist, and content brief are ready. "
        "Review the deliverables and tell us which improvement you want next: "
        "local SEO, fresh content, or ongoing website care.\n\n"
        "We can start the next step after your approval."
    )
    delivery = await update_delivery(
        delivery["id"],
        followup_status="ready",
        followup_due_at=due,
        followup_subject=subject,
        followup_body=body,
    ) or delivery

    auto_send = os.getenv("AGENCY_DELIVERY_AUTO_FOLLOWUP", "false").lower() in {"1", "true", "yes"}
    if send and auto_send and brief.get("email"):
        from admin.tools.agentmail_client import AgentMailEmailClient

        client = AgentMailEmailClient(
            "website",
            workspace_id=str(delivery.get("workspace_id") or ""),
        )
        if client.enabled:
            ok = await client.send_email(brief["email"], subject, body, cc_owner=True)
            await update_delivery(delivery["id"], followup_status="sent" if ok else "failed")
            return "sent" if ok else "failed"
        return "blocked"

    # Never silently send without explicit opt-in and a client email.
    return "ready"


def delivery_summary(delivery: dict[str, Any]) -> dict[str, Any]:
    return {
        key: delivery.get(key)
        for key in (
            "id", "handoff_id", "workspace_id", "client_name", "service_type",
            "status", "website_status", "seo_status", "content_status",
            "followup_status", "followup_due_at", "error", "created_at", "updated_at",
            "output_dir", "report_path", "offer_value", "currency",
            "followup_subject", "followup_body", "website_result", "seo_result", "content_result",
        )
    }


async def run_delivery(
    handoff_id: str,
    workspace_id: str,
    client_name: str = "",
    service_type: str = "website_seo_content",
    offer_value: float | None = None,
    currency: str = "USD",
    force: bool = False,
) -> dict[str, Any]:
    """Build a real local website and run SEO/content specialists for a handoff."""
    from admin.agency.sba_store import get_handoff

    handoff = get_handoff(handoff_id)
    if not handoff:
        return {"error": f"Handoff {handoff_id} not found"}
    brief = await _brief_from_handoff(handoff)
    existing = await get_delivery_by_handoff(handoff_id)
    if existing and not force and existing.get("status") in _TERMINAL_STATUSES | {"running"}:
        return existing

    output_dir = _delivery_dir(workspace_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    delivery = await create_delivery(
        handoff_id, workspace_id, client_name or brief["business_name"],
        service_type, offer_value if offer_value is not None else brief.get("offer_value"),
        currency or brief.get("currency") or "USD",
    )
    if "error" in delivery and len(delivery) == 1:
        return delivery

    try:
        delivery = await update_delivery(
            delivery["id"], status="running", output_dir=str(output_dir), error="",
        ) or delivery
        assert delivery is not None
        scope = {"kind": "client", "workspace_id": workspace_id, "handoff_id": handoff_id}
        website_args = _worker_args(brief, output_dir)
        website_task = "tool: build_site | " + _json(website_args)
        seo_task = "tool: keyword_research | " + _json({
            "seed_keyword": brief["industry"], "language": "en",
        })
        content_task = "tool: generate_content_brief | " + _json({
            "topic": f"{brief['business_name']} {brief['industry']}",
            "target_audience": "local customers",
            "word_count": 900,
        })

        website_result, seo_result, content_result = await asyncio.gather(
            _run_specialist("website", website_task, scope),
            _run_specialist("seo", seo_task, scope),
            _run_specialist("content", content_task, scope),
            return_exceptions=True,
        )
        website_payload = _result_payload(website_result) if isinstance(website_result, dict) else {"error": str(website_result)}
        seo_payload = _result_payload(seo_result) if isinstance(seo_result, dict) else {"error": str(seo_result)}
        content_payload = _result_payload(content_result) if isinstance(content_result, dict) else {"error": str(content_result)}
        website_status = _stage_status(website_result, website_payload) if isinstance(website_result, dict) else "failed"
        seo_status = _stage_status(seo_result, seo_payload) if isinstance(seo_result, dict) else "failed"
        content_status = _stage_status(content_result, content_payload) if isinstance(content_result, dict) else "failed"

        if website_status == "failed":
            website_payload = _fallback_website(brief, output_dir)
            website_status = "partial"
        if seo_status == "failed":
            seo_payload = _fallback_seo(brief)
            seo_status = "partial"
        if content_status == "failed":
            content_payload = _fallback_content(brief)
            content_status = "partial"

        delivery = await update_delivery(
            delivery["id"],
            website_status=website_status,
            seo_status=seo_status,
            content_status=content_status,
            website_result=website_payload,
            seo_result=seo_payload,
            content_result=content_payload,
        ) or delivery
        assert delivery is not None
        report_path = _write_report(delivery, brief)
        delivery = await update_delivery(delivery["id"], report_path=report_path) or delivery
        assert delivery is not None
        overall = "delivered" if website_status == "completed" and seo_status == "completed" and content_status == "completed" else "partial"
        followup_status = await _queue_or_send_followup(delivery, brief, send=False)
        delivery = await update_delivery(delivery["id"], status=overall, followup_status=followup_status) or delivery
        return delivery or {"error": "delivery row unavailable"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("delivery %s failed", handoff_id)
        if delivery:
            await update_delivery(delivery["id"], status="failed", error=str(exc)[:1000])
        return {"error": str(exc), "handoff_id": handoff_id, "workspace_id": workspace_id}


async def retry_delivery(delivery_id: str, force: bool = False) -> dict[str, Any]:
    delivery = await get_delivery(delivery_id)
    if not delivery:
        return {"error": f"Delivery {delivery_id} not found"}
    if delivery.get("status") == "running" and not force:
        return {"error": "delivery_running"}
    if delivery.get("status") in _ACTIVE_STATUSES and not force:
        return await run_delivery(delivery["handoff_id"], delivery["workspace_id"], delivery["client_name"], delivery["service_type"], delivery.get("offer_value"), delivery.get("currency") or "USD")
    return await run_delivery(
        delivery["handoff_id"], delivery["workspace_id"], delivery["client_name"],
        delivery["service_type"], delivery.get("offer_value"), delivery.get("currency") or "USD", force=True,
    )


async def trigger_followup(delivery_id: str, send: bool = False) -> dict[str, Any]:
    delivery = await get_delivery(delivery_id)
    if not delivery:
        return {"error": f"Delivery {delivery_id} not found"}
    from admin.agency.sba_store import get_handoff

    handoff = get_handoff(delivery["handoff_id"])
    if not handoff:
        return {"error": f"Handoff {delivery['handoff_id']} not found"}
    brief = await _brief_from_handoff(handoff)
    status = await _queue_or_send_followup(delivery, brief, send=send)
    return {**(await get_delivery(delivery_id) or delivery), "action": status}


async def recover_stale_deliveries(max_age_hours: int = 6) -> int:
    """Reset interrupted jobs so the next autonomy tick can retry them."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    db = await get_workspace_db()
    result = await db.execute(
        "UPDATE service_deliveries SET status='queued', error='recovered after interruption', updated_at=? "
        "WHERE status IN ('queued','running') AND updated_at < ?",
        (_now(), cutoff),
    )
    await db.commit()
    return max(0, result.rowcount or 0)


async def process_pending_handoffs(limit: int | None = None) -> dict[str, Any]:
    """Provision pending handoffs and start one durable delivery per handoff."""
    from admin.agency.orchestrator import ceo_process_sba_handoff
    from admin.agency.sba_store import list_handoffs

    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    handoffs = list_handoffs()
    if limit is not None:
        handoffs = handoffs[: max(1, min(int(limit), 100))]
    for handoff in handoffs:
        handoff_id = handoff.get("id")
        if not handoff_id:
            continue
        existing = await get_delivery_by_handoff(handoff_id)
        if existing and existing.get("status") in _TERMINAL_STATUSES:
            skipped.append({"handoff_id": handoff_id, "reason": "delivery_terminal"})
            continue
        if existing and existing.get("status") == "running":
            skipped.append({"handoff_id": handoff_id, "reason": "delivery_active"})
            continue
        try:
            with _running_handoffs_guard:
                if handoff_id in _running_handoffs:
                    skipped.append({"handoff_id": handoff_id, "reason": "already_running"})
                    continue
                _running_handoffs.add(handoff_id)
            provision = await ceo_process_sba_handoff(handoff_id)
            if "error" in provision:
                errors.append({"handoff_id": handoff_id, "error": provision["error"]})
                continue
            workspace_id = provision.get("workspace_id")
            if not workspace_id:
                errors.append({"handoff_id": handoff_id, "error": "workspace_id_missing"})
                continue
            delivery = await run_delivery(
                handoff_id, workspace_id, provision.get("client_name", ""),
                "website_seo_content", None, "USD",
            )
            processed.append({
                "handoff_id": handoff_id,
                "delivery_id": delivery.get("id"),
                "workspace_id": workspace_id,
                "status": delivery.get("status", "error" if "error" in delivery else "queued"),
            })
            if delivery.get("error"):
                errors.append({"handoff_id": handoff_id, "error": str(delivery["error"])})
        except Exception as exc:  # noqa: BLE001
            logger.exception("delivery autonomy tick failed for handoff %s", handoff_id)
            errors.append({"handoff_id": handoff_id, "error": str(exc)})
        finally:
            with _running_handoffs_guard:
                _running_handoffs.discard(handoff_id)
    return {"checked": len(handoffs), "processed": processed, "skipped": skipped, "errors": errors}


_loop_task: asyncio.Task | None = None
_loop_stop = False


async def _loop() -> None:
    interval = max(10, int(os.getenv("AGENCY_DELIVERY_LOOP_INTERVAL_SECONDS", os.getenv("AGENCY_AGENT_LOOP_INTERVAL_SECONDS", "60"))))
    while not _loop_stop:
        try:
            await recover_stale_deliveries()
            await process_pending_handoffs()
        except Exception as exc:  # noqa: BLE001
            logger.warning("service delivery loop tick failed: %s", exc)
        await asyncio.sleep(interval)


async def start_loop() -> None:
    global _loop_task, _loop_stop
    if os.getenv("AGENCY_DELIVERY_LOOP_OFF", os.getenv("AGENCY_AGENT_LOOP_OFF", "0")) == "1":
        logger.info("autonomous service delivery loop disabled")
        return
    if _loop_task and not _loop_stop:
        return
    _loop_stop = False
    _loop_task = asyncio.create_task(_loop())
    logger.info("autonomous service delivery loop started")


async def stop_loop() -> None:
    global _loop_task, _loop_stop
    _loop_stop = True
    task, _loop_task = _loop_task, None
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("autonomous service delivery loop stopped")
