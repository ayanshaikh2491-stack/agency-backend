"""Runnable no-network self-check for the CEO autonomy persistence surface."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def self_check() -> None:
    import admin.persistence as persistence
    from admin.agency.ceo_autonomy import (
        CEOAutonomy,
        decide_approval,
        emit_event,
        list_approvals,
        list_events,
        request_approval,
    )

    with tempfile.TemporaryDirectory(prefix="ceo-autonomy-selfcheck-") as temp_dir:
        old_db_path = persistence.DB_PATH
        try:
            persistence.DB_PATH = Path(temp_dir) / "selfcheck.db"
            await persistence.init_persistence()

            event = await emit_event(
                "heartbeat",
                source="selfcheck",
                payload={"reason": "smoke", "api_key": "must-not-leak"},
            )
            assert event and event["payload"]["api_key"] == "[REDACTED]"
            assert (await list_events(limit=10))[0]["event_type"] == "heartbeat"

            approval = await request_approval(
                "email",
                "Self-check approval gate",
                payload={"to": "operator@example.com"},
            )
            assert approval and approval["status"] == "pending"
            assert (await list_approvals(limit=10))[0]["action_type"] == "email"
            decided = await decide_approval(approval["id"], "rejected", reason="self-check")
            assert decided and decided["status"] == "rejected"

            control_plane = CEOAutonomy()
            control_plane._overview = lambda: {  # noqa: PLC2801 - self-check seam
                "summary": {"pending_handoffs": 0, "pending_reviews": 0},
                "workspaces": [],
            }
            result = await control_plane.tick()
            assert result["claimed"] >= 1
            assert result["succeeded"] == result["claimed"]
        finally:
            persistence.DB_PATH = old_db_path
            await persistence.close_persistence()

    print("ceo autonomy self-check: ok")


if __name__ == "__main__":
    asyncio.run(self_check())
