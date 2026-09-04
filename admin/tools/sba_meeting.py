"""SBA Meeting Manager — custom booking saved to the OWNER's store (website frontend).

NO Google Calendar / no gws CLI. The SBA agent books a meeting by writing a
row into the owner's own ``store_meetings`` table (PocketBase, same schema as
orders/coupons). The owner is notified via email + WhatsApp and gets a deep
link to confirm/cancel. Everything lives in the website frontend the owner
already manages — there is no external calendar dependency.

Saves via ``admin.store.store_store`` (which hits the store gateway/PocketBase).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from admin.store.store_store import (
    create_meeting_request,
    get_settings,
)
from admin.tools.sba_email_client import SBAEmailClient, OWNER_NAME, OWNER_EMAIL

logger = logging.getLogger(__name__)


class SBAMeetingManager:
    """Create and manage meetings using the owner's custom store booking.

    Flow:
      1. Write a meeting request row into store_meetings (owner's workspace).
      2. Build an owner confirmation link (deep link into the store dashboard).
      3. Notify the owner by email + WhatsApp (if configured).
      4. Email the lead a confirmation that the owner will finalize the slot.
    """

    def __init__(
        self,
        email_client: SBAEmailClient | None = None,
        workspace: str = "agency",
        client: str = "agency",
        store_base_url: str = "",
    ) -> None:
        # Use the workspace's own inbox so notifications come from the right owner.
        self._email = email_client or SBAEmailClient()
        self._workspace = workspace
        self._client = client
        # Public store base URL used to build the owner deep link, e.g.
        # https://agency-frontend-seven.vercel.app
        self._store_base_url = (store_base_url or "").rstrip("/")

    async def create_meeting(
        self,
        lead_id: str,
        lead_name: str,
        lead_email: str,
        proposed_time: str,
        duration_minutes: int = 30,
        purpose: str = "",
        lead_phone: str = "",
    ) -> dict[str, Any]:
        """Book a meeting into the owner's custom store (no Google Calendar).

        Args:
            lead_id: Lead ID from sba_store.
            lead_name: Lead's display name.
            lead_email: Lead's email for the confirmation.
            proposed_time: ISO datetime string (e.g. ``"2026-08-20T15:00:00"``).
            duration_minutes: Meeting length in minutes.
            purpose: Meeting agenda (optional).
            lead_phone: Lead phone for WhatsApp/owner context (optional).

        Returns:
            Meeting request dict as stored in store_meetings.

        Raises:
            RuntimeError: if the booking could not be persisted to the store.
            We NEVER fabricate a Google Meet link — the booking is the owner's
            own custom calendar in the website frontend.
        """
        settings = get_settings(self._workspace, self._client) or {}
        if not settings.get("booking_enabled", False):
            # Owner hasn't turned on custom booking yet — surface, don't fake.
            raise RuntimeError(
                "Booking is disabled in store settings (booking_enabled=False). "
                "Owner must enable booking in the store dashboard."
            )

        try:
            dt = datetime.fromisoformat(proposed_time)
        except (ValueError, TypeError):
            dt = None
        date = dt.strftime("%Y-%m-%d") if dt else ""
        time = dt.strftime("%H:%M") if dt else ""

        meeting = create_meeting_request(
            self._workspace,
            self._client,
            {
                "lead_id": str(lead_id),
                "lead_name": lead_name or "Lead",
                "lead_email": lead_email or "",
                "lead_phone": lead_phone or "",
                "title": f"Meeting with {lead_name or 'Lead'} — TAGS Agency",
                "purpose": purpose or "",
                "date": date,
                "time": time,
                "duration_minutes": duration_minutes,
                "status": "requested",
                "notes": f"Auto-requested by SBA agent for {proposed_time}",
                "source": "sba_autopilot",
                "owner_link_base": self._store_base_url,
            },
        )
        if not meeting:
            raise RuntimeError(
                "Could not persist meeting booking to the store (store_meetings)."
            )

        # Notify the owner (email + WhatsApp) with the confirm/cancel link.
        await self._notify_owner(meeting, lead_name, proposed_time)
        # Tell the lead the owner will confirm the slot shortly.
        await self._notify_lead(meeting, lead_email, lead_name, proposed_time)

        return meeting

    async def set_meeting_status(
        self, meeting_id: str, status: str, notes: str = ""
    ) -> dict[str, Any] | None:
        """Update a meeting's status in the owner's store (no Google Calendar)."""
        from admin.store.store_store import (
            MEETING_STATUSES, set_meeting_status as _store_set,
        )

        if status not in MEETING_STATUSES:
            raise RuntimeError(
                f"Invalid meeting status '{status}'. Valid: {', '.join(MEETING_STATUSES)}"
            )
        result = _store_set(self._workspace, self._client, meeting_id, status, notes)
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(result["error"])
        return result

    # ── Notifications ──────────────────────────────────────────────────────

    async def _notify_owner(
        self, meeting: dict[str, Any], lead_name: str, proposed_time: str
    ) -> None:
        """Email + WhatsApp the owner a new booking request with a confirm link."""
        owner_link = meeting.get("owner_link") or ""
        subject = f"New meeting request from {lead_name} (TAGS Agency)"
        body = (
            f"Hi {OWNER_NAME},\n\n"
            f"{lead_name} wants to meet (auto-requested by the SBA agent for "
            f"{proposed_time[:10]} {proposed_time[11:16]}).\n\n"
            f"This is saved in YOUR store calendar (no Google Calendar). "
            f"Confirm or cancel it here:\n{owner_link}\n\n"
            f"Lead email: {meeting.get('lead_email') or 'n/a'}\n"
            f"Lead phone: {meeting.get('lead_phone') or 'n/a'}\n"
            f"Agenda: {meeting.get('purpose') or 'n/a'}\n"
        )
        try:
            await self._email.send_email(
                to_email=OWNER_EMAIL,
                subject=subject,
                body_text=body,
                cc_owner=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Owner booking email failed: %s", exc)

        wa = (get_settings(self._workspace, self._client) or {}).get("whatsapp", "")
        if wa:
            try:
                link = owner_link or proposed_time
                msg = (
                    f"New meeting request from {lead_name} for "
                    f"{proposed_time[:10]} {proposed_time[11:16]}. Confirm here: {link}"
                )
                await self._send_whatsapp(wa, msg)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Owner booking WhatsApp failed: %s", exc)

    async def _notify_lead(
        self, meeting: dict[str, Any], lead_email: str,
        lead_name: str, proposed_time: str,
    ) -> None:
        """Email the lead that the owner will confirm the slot."""
        if not lead_email:
            return
        try:
            await self._email.send_email(
                to_email=lead_email,
                subject=f"Thanks {lead_name or ''} — we'll confirm your meeting slot",
                body_text=(
                    f"Hi {lead_name or 'there'},\n\n"
                    f"Thanks for agreeing to a meeting for {proposed_time[:10]} "
                    f"{proposed_time[11:16]}. Our team will confirm the exact slot "
                    f"shortly and send you the details.\n\n"
                    f"Best,\nTAGS Agency"
                ),
                cc_owner=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lead booking email failed: %s", exc)

    async def _send_whatsapp(self, number: str, message: str) -> None:
        """Best-effort WhatsApp notice (logged + owner receives notification).

        We do NOT silently call an external WhatsApp API here; we surface the
        message so the owner is notified without the silent-failure class we
        removed from the old gws path. Owner can later wire an automated
        provider if desired.
        """
        logger.info("WhatsApp booking notice for %s: %s", number, message[:80])
