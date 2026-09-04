"""High-level AgentMail notify helpers for the agency.

Per user decision (2026-08-23):
  * sba@agentmail.to and tagsceo@agentmail.to are the ONLY agent inboxes.
  * They email the OWNER (Ayan) and the agency CLIENT directly.
  * Inbox-less agents (seo, etc.) relay through the CEO ("act as <agent>").

This module is the single place agents call to "send an email as me".
It DOES NOT touch cold-lead outreach (that stays on the owner's reputed
SMTP sender in sba_email_client). It is only for agent<->owner/client comms.
"""
from __future__ import annotations

import os
from typing import Optional

from admin.tools.agentmail_client import (
    ENABLED_INBOXES,
    PRIMARY_SENDER,
    get_inbox,
    send,
)

# Owner (Ayan) address — same env var the SMTP client uses, so it's one source.
OWNER_EMAIL = (
    os.environ.get("SBA_OWNER_EMAIL", "")
    or os.environ.get("TAGS_SMTP_EMAIL", "")
    or "ayanshaikh2491@gmail.com"  # fallback; overridden by env
)

# Optional agency-client address(es) to CC on agent emails. Comma-separated.
CLIENT_EMAILS = [
    e.strip() for e in (os.environ.get("AGENCY_CLIENT_EMAILS", "") or "").split(",") if e.strip()
]


def actor_address(actor: str) -> Optional[str]:
    """The AgentMail address an actor sends from (falls back to CEO)."""
    inbox = get_inbox(actor, create=False)
    return inbox["address"] if inbox else None


def notify_owner(actor: str, subject: str, text: str, html: Optional[str] = None) -> bool:
    """Send an email from an agent's inbox to the owner (Ayan).

    Inbox-less actors relay via the CEO inbox (from=CEO, signature notes the actor).
    """
    return _send_as(actor, to=OWNER_EMAIL, subject=subject, text=text, html=html)


def notify_client(actor: str, subject: str, text: str, html: Optional[str] = None,
                  client_email: Optional[str] = None) -> bool:
    """Send an email from an agent's inbox to the agency client."""
    targets = [client_email] if client_email else CLIENT_EMAILS
    if not targets:
        logger_w("notify_client: no client email configured; skipping")
        return False
    ok = True
    for tgt in targets:
        if not _send_as(actor, to=tgt, subject=subject, text=text, html=html):
            ok = False
    return ok


def _send_as(actor: str, to: str, subject: str, text: str,
             html: Optional[str] = None) -> bool:
    """Send from actor's inbox; if actor has no inbox, relay via CEO.

    Relayed mail keeps the actor's name in the signature so the reader knows
    who actually said it (CEO is just the transport).
    """
    from_agentmail = actor in ENABLED_INBOXES
    if from_agentmail:
        return send(actor, to=to, subject=subject, text=text, html=html)
    # Relay: CEO sends, actor named in body so it's not misleading.
    relay_text = f"[from {actor}, relayed by CEO]\n\n{text}"
    return send(PRIMARY_SENDER, to=to, subject=subject, text=relay_text, html=html)


def logger_w(msg: str) -> None:
    import logging
    logging.getLogger(__name__).warning(msg)
