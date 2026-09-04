"""Agency Orchestrator — workspace management, agent lifecycle, reporting chain.

Each workspace gets its own SEO agent with isolated memory + data.

Reporting Chain:
  1. Client Workspace SEO Agent → Client Workspace CEO (primary report)
  2. Client Workspace SEO Agent → Agency SEO Agent (parallel - quality monitoring)
  3. Client Workspace CEO → Agency CEO (workspace summary)
  4. Agency SEO Agent → monitors all clients, catches mistakes
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSPACE STORE
# ═══════════════════════════════════════════════════════════════════════════════

_workspaces: dict[str, dict[str, Any]] = {}
# workspace_id -> {
#   id, name, client_name, type (client/agency),
#   agents: {agent_type: {config, status, last_run}},
#   settings: {...},
#   created_at, updated_at
# }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


# ═══════════════════════════════════════════════════════════════
# WORKSPACE CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def create_workspace(
    name: str,
    client_name: str = "",
    workspace_type: str = "client",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new workspace (client or agency)."""
    wid = _new_id()
    ws = {
        "id": wid,
        "name": name,
        "client_name": client_name or name,
        "type": workspace_type,  # "client" or "agency"
        "agents": {},
        "settings": settings or {},
        "created_at": _now(),
        "updated_at": _now(),
    }
    _workspaces[wid] = ws
    logger.info("Created workspace: %s (%s) type=%s", name, wid, workspace_type)
    return ws


def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    return _workspaces.get(workspace_id)


def list_workspaces(workspace_type: str | None = None) -> list[dict[str, Any]]:
    ws_list = list(_workspaces.values())
    if workspace_type:
        ws_list = [w for w in ws_list if w["type"] == workspace_type]
    return sorted(ws_list, key=lambda w: w["created_at"], reverse=True)


def update_workspace(workspace_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    ws = _workspaces.get(workspace_id)
    if not ws:
        return None
    ws.update(updates)
    ws["updated_at"] = _now()
    return ws


def delete_workspace(workspace_id: str) -> bool:
    return _workspaces.pop(workspace_id, None) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

def register_agent(
    workspace_id: str,
    agent_type: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register an agent in a workspace."""
    ws = _workspaces.get(workspace_id)
    if not ws:
        raise ValueError(f"Workspace {workspace_id} not found")

    agent_info = {
        "type": agent_type,
        "workspace_id": workspace_id,
        "status": "idle",
        "config": config or {},
        "last_run": None,
        "total_runs": 0,
        "last_report": None,
        "created_at": _now(),
    }
    ws["agents"][agent_type] = agent_info
    logger.info("Registered %s agent in workspace %s", agent_type, ws["name"])
    return agent_info


def get_agent(workspace_id: str, agent_type: str) -> dict[str, Any] | None:
    ws = _workspaces.get(workspace_id)
    if not ws:
        return None
    return ws["agents"].get(agent_type)


def update_agent_status(workspace_id: str, agent_type: str, status: str) -> None:
    ws = _workspaces.get(workspace_id)
    if ws and agent_type in ws["agents"]:
        ws["agents"][agent_type]["status"] = status
        ws["agents"][agent_type]["updated_at"] = _now()


def increment_agent_runs(workspace_id: str, agent_type: str) -> None:
    ws = _workspaces.get(workspace_id)
    if ws and agent_type in ws["agents"]:
        agent = ws["agents"][agent_type]
        agent["total_runs"] = agent.get("total_runs", 0) + 1
        agent["last_run"] = _now()


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTING CHAIN
# ═══════════════════════════════════════════════════════════════════════════════
#
#  Client SEO Agent ──→ Client Workspace CEO ──→ Agency CEO
#       │
#       └────────────────→ Agency SEO Agent (quality monitor)
#
#  Each workspace = own SEO agent + own memory + own data
#  Agency SEO = quality controller
#

# In-memory report chain
_reports: list[dict[str, Any]] = []

# Per-workspace agent memory (isolated data per workspace)
_workspace_memory: dict[str, dict[str, Any]] = {}
# workspace_id -> {
#   seo_data: {audit_history, onpage_history, keyword_data, rankings},
#   reports_sent: [],
#   reports_received: [],
#   issues_found: [],
#   issues_fixed: [],
# }


def get_workspace_memory(workspace_id: str) -> dict[str, Any]:
    """Get or create isolated memory for a workspace."""
    if workspace_id not in _workspace_memory:
        _workspace_memory[workspace_id] = {
            "seo_data": {
                "audit_history": [],
                "onpage_history": [],
                "keyword_data": {},
                "rankings_history": [],
            },
            "reports_sent": [],
            "reports_received": [],
            "issues_found": [],
            "issues_fixed": [],
        }
    return _workspace_memory[workspace_id]


def save_to_workspace_memory(workspace_id: str, category: str, data: Any) -> None:
    """Save data to a workspace's isolated memory."""
    mem = get_workspace_memory(workspace_id)
    if category in mem.get("seo_data", {}):
        if isinstance(mem["seo_data"][category], list):
            mem["seo_data"][category].append({"data": data, "at": _now()})
        else:
            mem["seo_data"][category] = data
    logger.info("Saved to workspace %s memory: %s", workspace_id, category)


def get_workspace_seo_history(workspace_id: str) -> dict[str, Any]:
    """Get SEO history for a specific workspace."""
    mem = get_workspace_memory(workspace_id)
    return mem.get("seo_data", {})


def submit_report(
    from_workspace_id: str,
    from_agent_type: str,
    to_agent_type: str,
    report_type: str,
    title: str,
    content: dict[str, Any],
    summary: str = "",
) -> dict[str, Any]:
    """Submit a report from one agent to another.

    This uses the reporting chain:
    - Client workspace agents report to agency agents
    - Agency agents report to SBA
    - SBA reports to CEO
    """
    report_id = _new_id()
    now = _now()

    from_ws = get_workspace(from_workspace_id)
    from_ws_name = from_ws["name"] if from_ws else "Unknown"

    report = {
        "id": report_id,
        "from_workspace_id": from_workspace_id,
        "from_workspace_name": from_ws_name,
        "from_agent_type": from_agent_type,
        "to_agent_type": to_agent_type,
        "report_type": report_type,
        "title": title,
        "summary": summary,
        "content": content,
        "status": "pending",  # pending, read, acknowledged, actioned
        "created_at": now,
        "read_at": None,
        "acknowledged_at": None,
    }
    _reports.append(report)

    logger.info(
        "Report: %s/%s → %s [%s] %s",
        from_ws_name, from_agent_type, to_agent_type, report_type, title,
    )

    # Also send via agent bus if available
    try:
        from admin.workspace.agent_bus import send_message
        send_message(
            from_agent=f"{from_ws_name}/{from_agent_type}",
            to_agent=to_agent_type,
            workspace_id=from_workspace_id,
            subject=title,
            content=summary or str(content)[:500],
            message_type="report",
            metadata={"report_id": report_id, "report_type": report_type},
        )
    except Exception as e:
        logger.warning("Failed to send via agent bus: %s", e)

    return report


def get_reports(
    to_agent_type: str | None = None,
    from_workspace_id: str | None = None,
    report_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get reports, optionally filtered."""
    filtered = _reports
    if to_agent_type:
        filtered = [r for r in filtered if r["to_agent_type"] == to_agent_type]
    if from_workspace_id:
        filtered = [r for r in filtered if r["from_workspace_id"] == from_workspace_id]
    if report_type:
        filtered = [r for r in filtered if r["report_type"] == report_type]
    return sorted(filtered, key=lambda r: r["created_at"], reverse=True)[:limit]


def mark_report_read(report_id: str) -> bool:
    for r in _reports:
        if r["id"] == report_id:
            r["status"] = "read"
            r["read_at"] = _now()
            return True
    return False


def mark_report_acknowledged(report_id: str) -> bool:
    for r in _reports:
        if r["id"] == report_id:
            r["status"] = "acknowledged"
            r["acknowledged_at"] = _now()
            return True
    return False


def get_pending_reports(to_agent_type: str) -> list[dict[str, Any]]:
    return [r for r in _reports if r["to_agent_type"] == to_agent_type and r["status"] == "pending"]


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-REPORT: Agent runs tools → generates report → submits up the chain
# ═══════════════════════════════════════════════════════════════════════════════

def run_seo_agent_for_workspace(workspace_id: str) -> dict[str, Any]:
    """Run SEO agent for a client workspace and report results up the chain.

    Flow:
    1. Get workspace settings (target URL, keywords)
    2. Run SEO tools (audit, keywords, onpage)
    3. Generate report
    4. Submit report to Client Workspace CEO (primary)
    5. Submit report to Agency SEO Agent (parallel - quality monitoring)
    """
    ws = get_workspace(workspace_id)
    if not ws:
        return {"error": "Workspace not found"}

    target_url = ws.get("settings", {}).get("target_url", "")
    keywords = ws.get("settings", {}).get("keywords", [])
    workspace_name = ws["name"]

    if not target_url:
        return {"error": "No target_url set in workspace settings"}

    from admin.tools.seo_tools import (
        site_audit,
        keyword_research,
        onpage_check,
        generate_seo_report,
        track_rankings,
    )

    update_agent_status(workspace_id, "seo", "running")
    results = {"workspace": workspace_name, "url": target_url}

    # 1. Run site audit
    try:
        audit = site_audit(target_url, max_pages=5)
        results["audit"] = {
            "pages_crawled": audit.get("pages_crawled", 0),
            "issues_count": len(audit.get("issues", [])),
            "summary": audit.get("summary", {}),
        }
    except Exception as e:
        results["audit"] = {"error": str(e)}

    # 2. On-page check
    try:
        onpage = onpage_check(target_url)
        results["onpage"] = {
            "seo_score": onpage.get("seo_score", 0),
            "issues_count": len(onpage.get("issues", [])),
        }
    except Exception as e:
        results["onpage"] = {"error": str(e)}

    # 3. Keyword research
    if keywords:
        try:
            kw_data = keyword_research(keywords[0])
            results["keywords"] = {
                "seed": keywords[0],
                "total": kw_data.get("total_suggestions", 0),
                "questions": len(kw_data.get("questions", [])),
            }
        except Exception as e:
            results["keywords"] = {"error": str(e)}

    # 4. Track rankings
    if keywords:
        for kw in keywords[:3]:
            try:
                rank = track_rankings(kw, target_url)
                results.setdefault("rankings", []).append({
                    "keyword": kw,
                    "position": rank.get("position"),
                    "text": rank.get("position_text", ""),
                })
            except Exception:
                pass

    # 5. Generate report
    try:
        report = generate_seo_report(target_url, keywords)
        results["report"] = {
            "seo_score": report.get("seo_score", 0),
            "total_issues": report.get("summary", {}).get("total_issues", 0),
            "report_length": len(report.get("report_markdown", "")),
        }
    except Exception as e:
        results["report"] = {"error": str(e)}

    # 6. Save to workspace memory (isolated per workspace)
    mem = get_workspace_memory(workspace_id)
    if "audit" in results and "error" not in results["audit"]:
        mem["seo_data"]["audit_history"].append({
            "at": _now(),
            "pages_crawled": results["audit"]["pages_crawled"],
            "issues_count": results["audit"]["issues_count"],
        })
    if "onpage" in results and "error" not in results["onpage"]:
        mem["seo_data"]["onpage_history"].append({
            "at": _now(),
            "seo_score": results["onpage"]["seo_score"],
            "issues_count": results["onpage"]["issues_count"],
        })
    if "keywords" in results:
        mem["seo_data"]["keyword_data"][results["keywords"].get("seed", "")] = results["keywords"]
    if "rankings" in results:
        mem["seo_data"]["rankings_history"].append({
            "at": _now(),
            "rankings": results["rankings"],
        })

    # 7. Build summary
    seo_score = results.get("onpage", {}).get("seo_score", 0)
    issues = results.get("audit", {}).get("issues_count", 0)
    summary_text = (
        f"SEO scan complete for {workspace_name} ({target_url}). "
        f"Score: {seo_score}/100. Issues: {issues}. "
    )
    if results.get("rankings"):
        for r in results["rankings"]:
            summary_text += f"{r['keyword']}: {r['text']}. "

    # 7. PRIMARY: Submit to Client Workspace CEO
    ws_ceo_report = submit_report(
        from_workspace_id=workspace_id,
        from_agent_type="seo",
        to_agent_type="workspace_ceo",
        report_type="seo_scan",
        title=f"SEO Report: {workspace_name} - Score {seo_score}/100",
        content=results,
        summary=summary_text,
    )

    # 8. PARALLEL: Submit to Agency SEO Agent (quality monitoring)
    agency_seo_report = submit_report(
        from_workspace_id=workspace_id,
        from_agent_type="seo",
        to_agent_type="agency_seo",
        report_type="seo_scan",
        title=f"SEO Report: {workspace_name} - Score {seo_score}/100",
        content=results,
        summary=summary_text,
    )

    increment_agent_runs(workspace_id, "seo")
    update_agent_status(workspace_id, "seo", "idle")

    return {
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
        "results": results,
        "ws_ceo_report_id": ws_ceo_report["id"],
        "agency_seo_report_id": agency_seo_report["id"],
        "submitted_to": ["workspace_ceo", "agency_seo"],
    }


def agency_seo_monitor() -> dict[str, Any]:
    """Agency SEO Agent: review all client reports, quality check, catch mistakes.

    This is the quality controller. It:
    1. Reviews all pending client SEO reports
    2. Checks for issues, mistakes, low scores
    3. Flags problems that need attention
    4. Saves monitoring data for history
    """
    client_reports = get_reports(to_agent_type="agency_seo", report_type="seo_scan")

    if not client_reports:
        return {"message": "No client reports to review", "status": "clean"}

    total_clients = len(set(r["from_workspace_id"] for r in client_reports))
    scores = []
    all_issues = 0
    flagged = []
    client_summaries = []

    for r in client_reports:
        content = r.get("content", {})
        score = content.get("onpage", {}).get("seo_score", 0)
        issues = content.get("audit", {}).get("issues_count", 0)
        scores.append(score)
        all_issues += issues

        # Flag problems
        flags = []
        if score < 50:
            flags.append(f"LOW SCORE: {score}/100")
        if issues > 10:
            flags.append(f"HIGH ISSUES: {issues} found")
        if score == 0 and issues == 0:
            flags.append("SCAN MAY HAVE FAILED - no data")

        client_info = {
            "client": r.get("from_workspace_name", "?"),
            "workspace_id": r.get("from_workspace_id"),
            "score": score,
            "issues": issues,
            "flags": flags,
            "needs_attention": len(flags) > 0,
        }
        client_summaries.append(client_info)

        if flags:
            flagged.append(client_info)

    avg_score = sum(scores) // len(scores) if scores else 0

    # Mark reports as reviewed
    for r in client_reports:
        mark_report_acknowledged(r["id"])

    return {
        "status": "reviewed",
        "total_clients": total_clients,
        "avg_score": avg_score,
        "total_issues": all_issues,
        "flagged_count": len(flagged),
        "flagged": flagged,
        "client_summaries": client_summaries,
    }


def workspace_ceo_to_agency_ceo(workspace_id: str) -> dict[str, Any]:
    """Client Workspace CEO: forward workspace summary to Agency CEO.

    Collects all reports received by workspace CEO and submits to Agency CEO.
    """
    ws_reports = get_reports(to_agent_type="workspace_ceo", from_workspace_id=workspace_id)

    if not ws_reports:
        return {"message": "No reports for workspace CEO to forward"}

    ws = get_workspace(workspace_id)
    ws_name = ws["name"] if ws else "Unknown"

    report_lines = []
    for r in ws_reports[:10]:
        report_lines.append(f"- [{r['report_type']}] {r['title']}")

    summary_text = (
        f"Workspace CEO Report: {ws_name}. "
        f"{len(ws_reports)} reports from workspace agents."
    )

    report = submit_report(
        from_workspace_id=workspace_id,
        from_agent_type="workspace_ceo",
        to_agent_type="agency_ceo",
        report_type="workspace_ceo_report",
        title=f"Workspace CEO: {ws_name} - {len(ws_reports)} reports",
        content={
            "workspace_id": workspace_id,
            "workspace_name": ws_name,
            "total_reports": len(ws_reports),
            "reports": report_lines,
            "raw_summaries": [r["summary"] for r in ws_reports[:5]],
        },
        summary=summary_text,
    )

    return {
        "report_id": report["id"],
        "workspace_name": ws_name,
        "reports_forwarded": len(ws_reports),
        "submitted_to": "agency_ceo",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SBA PIPELINE SCAN
# ═══════════════════════════════════════════════════════════════════════════════


def sba_pipeline_scan(workspace_id: str) -> dict[str, Any]:
    """SBA pipeline scan — check pipeline health and report to CEO.

    Runs on schedule to:
      1. Check for stale leads needing follow-up
      2. Check for hot leads ready for CEO handoff
      3. Report pipeline summary to CEO
    """
    from admin.agency.sba_store import list_handoffs, list_leads

    leads = list_leads()
    handoffs = [h for h in list_handoffs() if not h.get("workspace_id")]

    pipeline_counts: dict[str, int] = {}
    for s in ["new", "contacted", "meeting", "proposal", "negotiation", "closed", "lost"]:
        pipeline_counts[s] = len([l for l in leads if l["status"] == s])

    hot_leads = [l for l in leads if l.get("score", 0) >= 80 and l["status"] not in ("closed", "lost")]

    summary_parts = [
        f"SBA Pipeline Scan: {len(leads)} total leads.",
        f"Pipeline: {pipeline_counts}.",
    ]
    if hot_leads:
        summary_parts.append(f"{len(hot_leads)} hot leads ready.")
    if handoffs:
        summary_parts.append(f"{len(handoffs)} pending CEO handoffs.")

    report = submit_report(
        from_workspace_id=workspace_id,
        from_agent_type="sba",
        to_agent_type="agency_ceo",
        report_type="sba_pipeline",
        title=f"SBA Pipeline: {len(leads)} leads, {len(hot_leads)} hot",
        content={
            "workspace_id": workspace_id,
            "total_leads": len(leads),
            "pipeline": pipeline_counts,
            "hot_leads": [
                {"id": l["id"], "name": l["name"], "score": l["score"]}
                for l in hot_leads[:10]
            ],
            "pending_handoffs": len(handoffs),
        },
        summary=" ".join(summary_parts),
    )

    return {
        "status": "scanned",
        "total_leads": len(leads),
        "pipeline": pipeline_counts,
        "hot_leads": len(hot_leads),
        "pending_handoffs": len(handoffs),
        "report_id": report["id"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SBA EMAIL LEAD AUTO-CREATION
# ═══════════════════════════════════════════════════════════════════════════════


async def sba_check_email_leads() -> dict[str, Any]:
    """SBA checks email inbox for new lead inquiries and auto-creates leads.

    Returns summary of what was found and created.
    """
    from admin.tools.email_service import EmailLeadService

    service = EmailLeadService()
    if not service.enabled:
        return {"status": "disabled", "message": "Email service not configured"}

    created = await service.process_and_create_leads(auto_qualify=True)
    return {
        "status": "checked",
        "emails_processed": len(created),
        "leads_created": [
            {"id": l["id"], "name": l["name"], "score": l.get("score", 0)}
            for l in created
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CEO-SBA INTEGRATION: Auto workspace creation on hand══════════════════════════════════════════════════════════════


async def ceo_process_sba_handoff(handoff_id: str) -> dict[str, Any]:
    """CEO processes an SBA handoff: creates workspace + registers agents.

    This wires SBA --> CEO flow:
      1. Take the handoff
      2. Auto-create a client workspace
      3. Register all agents
      4. Set up default schedules
      5. Return workspace info
    """
    from admin.agency.sba_store import get_handoff, mark_handoff_workspace_created

    handoff = get_handoff(handoff_id)
    if not handoff:
        return {"error": f"Handoff {handoff_id} not found"}

    brief = handoff.get("brief", {})
    lead_name = brief.get("lead_name", "New Client")
    business_name = brief.get("business_name", lead_name)

    # Create workspace
    ws = create_workspace(
        name=f"{business_name} Workspace",
        client_name=business_name,
        workspace_type="client",
        settings={
            "lead_name": lead_name,
            "handoff_id": handoff_id,
            "source": brief.get("source", "sba_handoff"),
        },
    )
    ws_id = ws["id"]

    # Register all agents
    for agent_type in ["sba", "seo", "content", "website", "social", "ads", "analytics"]:
        register_agent(ws_id, agent_type, config={
            "lead_name": lead_name,
            "business_name": business_name,
            "handoff_source": "sba",
        })

    # Set up default schedules
    from admin.agency.scheduler import setup_default_schedules
    setup_default_schedules(ws_id)

    # Mark handoff as processed
    await mark_handoff_workspace_created(handoff_id, ws_id)

    # Submit report to CEO
    report = submit_report(
        from_workspace_id=ws_id,
        from_agent_type="sba",
        to_agent_type="agency_ceo",
        report_type="workspace_created",
        title=f"Workspace created: {business_name}",
        content={
            "workspace_id": ws_id,
            "workspace_name": ws["name"],
            "client_name": business_name,
            "handoff_id": handoff_id,
            "agents_registered": ["sba", "seo", "content", "website", "social", "ads", "analytics"],
        },
        summary=f"SBA handoff processed. Workspace '{business_name}' created with all agents.",
    )

    return {
        "status": "workspace_created",
        "workspace_id": ws_id,
        "workspace_name": ws["name"],
        "client_name": business_name,
        "agents_registered": 7,
        "report_id": report["id"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-SETUP: Create agency workspace + seed client══════════════════════════════════════════════════════════════════════════

def setup_agency() -> dict[str, Any]:
    """Create the agency workspace with all agent slots."""
    agency = create_workspace("Agency HQ", "TAGS Agency", workspace_type="agency")
    agency_id = agency["id"]

    # Agency SEO + Agency CEO
    for agent_type in ["seo", "content", "website", "social", "ads", "analytics"]:
        register_agent(agency_id, agent_type)
    register_agent(agency_id, "agency_ceo")

    return agency


def setup_client_workspace(
    client_name: str,
    target_url: str,
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Create a client workspace with SEO agent + workspace CEO configured."""
    ws = create_workspace(
        name=f"{client_name} Workspace",
        client_name=client_name,
        workspace_type="client",
        settings={
            "target_url": target_url,
            "keywords": keywords or [],
        },
    )
    # Client workspace agents
    register_agent(ws["id"], "seo")
    register_agent(ws["id"], "workspace_ceo")
    return ws
