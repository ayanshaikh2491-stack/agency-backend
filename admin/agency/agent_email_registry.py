"""Per-agent / per-workspace inbox registry (Mail.tm, free, no domain/server).

Each logical actor (sba, ceo, seo, or a client workspace) gets its own
persisted Mail.tm inbox so the agent can read replies + send internally
without buying a domain or running a mail server. Cold emails to real leads
still go through the existing reputed SMTP sender (sba_email_client).

Config lives in config/agent_emails.json (gitignored secrets: addresses +
passwords + tokens).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from admin.tools.mailtm_client import MailTMAccount

logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get(
    "AGENT_EMAILS_CONFIG",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "config", "agent_emails.json"),
)


def _load() -> dict[str, Any]:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_inbox(actor: str, create: bool = True) -> MailTMAccount | None:
    """Return the persisted Mail.tm inbox for an actor, creating if missing.

    actor: "sba", "ceo", "seo", or a workspace name like "workspace_alpha".
    """
    data = _load()
    entry = data.get(actor)
    if isinstance(entry, dict) and entry.get("address") and entry.get("token"):
        acct = MailTMAccount(
            address=entry["address"], password=entry.get("password", ""),
            token=entry["token"],
        )
        # Token may expire; refresh silently if needed on first use elsewhere.
        return acct
    if not create:
        return None
    # Mint a brand-new free inbox for this actor.
    label = actor.replace(" ", "_").lower()[:20]
    acct = MailTMAccount.create(label=label)
    data[actor] = acct.to_dict()
    _save(data)
    logger.info("agent inbox created for %r -> %s", actor, acct.address)
    return acct


def list_actors() -> list[str]:
    return list(_load().keys())


def register_inbox(actor: str, address: str, password: str, token: str) -> None:
    """Persist an externally-created inbox for an actor."""
    data = _load()
    data[actor] = {"address": address, "password": password, "token": token}
    _save(data)
