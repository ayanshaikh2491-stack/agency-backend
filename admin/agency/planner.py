"""Agent Planner — agents auto-plan and execute SEO tasks.

The planner gives each SEO agent the ability to:
  1. Assess current state (what needs doing)
  2. Create a prioritized task list
  3. Execute tasks using SEO tools
  4. Report results up the chain

This makes agents "autonomous" — they decide what to do and do it.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# TASK TYPES
# ═══════════════════════════════════════════════════════════════════════════════

TASK_TYPES = {
    "site_audit": {
        "description": "Crawl site and find technical SEO issues",
        "frequency": "weekly",
        "priority": 1,
    },
    "onpage_check": {
        "description": "Check on-page SEO score for target URL",
        "frequency": "daily",
        "priority": 2,
    },
    "keyword_research": {
        "description": "Research keywords and find new opportunities",
        "frequency": "weekly",
        "priority": 3,
    },
    "track_rankings": {
        "description": "Check SERP positions for tracked keywords",
        "frequency": "daily",
        "priority": 2,
    },
    "generate_report": {
        "description": "Generate client-ready SEO report",
        "frequency": "weekly",
        "priority": 4,
    },
    "fix_issues": {
        "description": "Generate fixes for critical issues found",
        "frequency": "on发现问题后",
        "priority": 1,
    },
    "generate_meta_tags": {
        "description": "Generate optimized meta tags",
        "frequency": "on发现问题后",
        "priority": 2,
    },
    "generate_schema": {
        "description": "Generate JSON-LD schema markup",
        "frequency": "monthly",
        "priority": 3,
    },
}

# In-memory task store
_tasks: dict[str, list[dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex[:10]


# ═══════════════════════════════════════════════════════════════════════════════
# PLAN CREATION
# ═══════════════════════════════════════════════════════════════════════════════

def create_seo_plan(workspace_id: str, target_url: str, keywords: list[str] | None = None) -> dict[str, Any]:
    """Create an SEO task plan for a workspace.

    The planner looks at what needs to be done and creates prioritized tasks.
    """
    tasks = []
    now = _now()

    # Always: site audit
    tasks.append({
        "id": _new_id(),
        "type": "site_audit",
        "priority": 1,
        "status": "pending",
        "params": {"url": target_url, "max_pages": 5},
        "created_at": now,
        "depends_on": [],
    })

    # Always: on-page check
    tasks.append({
        "id": _new_id(),
        "type": "onpage_check",
        "priority": 2,
        "status": "pending",
        "params": {"url": target_url},
        "created_at": now,
        "depends_on": [],
    })

    # If keywords provided: keyword research + rank tracking
    if keywords:
        tasks.append({
            "id": _new_id(),
            "type": "keyword_research",
            "priority": 3,
            "status": "pending",
            "params": {"seed_keyword": keywords[0]},
            "created_at": now,
            "depends_on": [],
        })
        for kw in keywords[:3]:
            tasks.append({
                "id": _new_id(),
                "type": "track_rankings",
                "priority": 2,
                "status": "pending",
                "params": {"keyword": kw, "target_url": target_url},
                "created_at": now,
                "depends_on": [],
            })

    # Always: meta tags + schema
    tasks.append({
        "id": _new_id(),
        "type": "generate_meta_tags",
        "priority": 3,
        "status": "pending",
        "params": {"url": target_url},
        "created_at": now,
        "depends_on": [],
    })

    tasks.append({
        "id": _new_id(),
        "type": "generate_schema",
        "priority": 4,
        "status": "pending",
        "params": {"url": target_url},
        "created_at": now,
        "depends_on": [],
    })

    # Report generation depends on audit
    audit_task_id = tasks[0]["id"]
    tasks.append({
        "id": _new_id(),
        "type": "generate_report",
        "priority": 5,
        "status": "pending",
        "params": {"url": target_url, "keywords": keywords},
        "created_at": now,
        "depends_on": [audit_task_id],
    })

    plan = {
        "id": _new_id(),
        "workspace_id": workspace_id,
        "target_url": target_url,
        "keywords": keywords or [],
        "tasks": tasks,
        "status": "pending",  # pending, running, completed, failed
        "created_at": now,
        "started_at": None,
        "completed_at": None,
    }

    _tasks[workspace_id] = _tasks.get(workspace_id, [])
    _tasks[workspace_id].append(plan)

    return plan


def get_plans(workspace_id: str) -> list[dict[str, Any]]:
    return _tasks.get(workspace_id, [])


def get_latest_plan(workspace_id: str) -> dict[str, Any] | None:
    plans = _tasks.get(workspace_id, [])
    return plans[-1] if plans else None


# ═══════════════════════════════════════════════════════════════════════════════
# PLAN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

def execute_plan(workspace_id: str) -> dict[str, Any]:
    """Execute all pending tasks in a plan.

    Runs each task using the appropriate SEO tool.
    Returns aggregated results.
    """
    plan = get_latest_plan(workspace_id)
    if not plan:
        return {"error": "No plan found"}

    plan["status"] = "running"
    plan["started_at"] = _now()

    from admin.tools.seo_tools import execute_seo_tool

    results = {}
    errors = []

    for task in plan["tasks"]:
        if task["status"] != "pending":
            continue

        task_type = task["type"]
        params = task["params"]

        # Check dependencies
        deps_met = all(
            any(t["id"] == dep_id and t["status"] == "completed" for t in plan["tasks"])
            for dep_id in task.get("depends_on", [])
        )
        if not deps_met:
            continue

        task["status"] = "running"
        logger.info("Executing task: %s(%s)", task_type, params)

        try:
            result = execute_seo_tool(task_type, params)
            task["status"] = "completed"
            task["result"] = result
            task["completed_at"] = _now()
            results[task_type] = result
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
            task["completed_at"] = _now()
            errors.append({"task": task_type, "error": str(e)})
            logger.exception("Task failed: %s", task_type)

    plan["status"] = "completed" if not errors else "partial"
    plan["completed_at"] = _now()

    return {
        "plan_id": plan["id"],
        "status": plan["status"],
        "tasks_total": len(plan["tasks"]),
        "tasks_completed": sum(1 for t in plan["tasks"] if t["status"] == "completed"),
        "tasks_failed": sum(1 for t in plan["tasks"] if t["status"] == "failed"),
        "results": results,
        "errors": errors,
    }


def execute_plan_and_report(workspace_id: str) -> dict[str, Any]:
    """Execute plan and submit results up the reporting chain.

    This is the main "agent does its job" function.
    """
    from admin.agency.orchestrator import run_seo_agent_for_workspace, aggregate_client_reports

    # Execute the plan
    plan_result = execute_plan(workspace_id)

    # Submit report up the chain
    report_result = run_seo_agent_for_workspace(workspace_id)

    return {
        "plan": plan_result,
        "report": report_result,
    }
