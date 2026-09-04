"""Mail.tm client — free API-based inbox for agents (no domain purchase, no server).

Lets an agent create its own email address on a free Mail.tm domain, read the
inbox as JSON, and reply — entirely via the public REST API. Used as the
agent's OWN automation inbox (signups, OTPs, receiving lead replies).

For SENDING cold emails to real leads, pair this with a reputed sender
(Gmail/SES); this inbox is for the agent's own receive/reply + automation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import string
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.mail.tm"

_RNG = random.SystemRandom()


def _rand_suffix(n: int = 8) -> str:
    return "".join(_RNG.choice(string.ascii_lowercase + string.digits) for _ in range(n))


class MailTMAccount:
    """One agent/workspace inbox on Mail.tm. Create once, then read/reply."""

    def __init__(self, address: str, password: str, token: str = "") -> None:
        self.address = address
        self.password = password
        self.token = token

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # ── REST helpers ────────────────────────────────────────────────
    @staticmethod
    def _post(path: str, json_body: dict | None = None, headers: dict | None = None,
              timeout: float = 20.0) -> dict:
        with httpx.Client(base_url=API_BASE, timeout=timeout, follow_redirects=True) as c:
            r = c.post(path, json=json_body, headers=headers or {})
            try:
                return {"ok": r.status_code in (200, 201), "status": r.status_code,
                        "data": r.json() if r.content else {}}
            except Exception:
                return {"ok": r.status_code in (200, 201), "status": r.status_code,
                        "data": {"raw": r.text[:500]}}

    @staticmethod
    def _get(path: str, headers: dict | None = None, timeout: float = 20.0) -> dict:
        with httpx.Client(base_url=API_BASE, timeout=timeout, follow_redirects=True) as c:
            r = c.get(path, headers=headers or {})
            try:
                return r.json()
            except Exception:
                return {"raw": r.text[:500]}

    # ── Account lifecycle ───────────────────────────────────────────
    @classmethod
    def create(cls, domain: str | None = None, label: str = "agent") -> "MailTMAccount":
        """Create a fresh free inbox. Returns the account (token fetched)."""
        if not domain:
            domains = cls._get("/domains")
            items = domains.get("hydra:member") or domains.get("items") or domains.get("member") or []
            if not items:
                raise RuntimeError("mail.tm: no domains available")
            domain = (items[0].get("domain") or "").strip()
        if not domain:
            raise RuntimeError("mail.tm: empty domain")
        address = f"{label}_{_rand_suffix()}@{domain}"
        password = _rand_suffix(16) + "Aa1!"
        res = cls._post("/accounts", json_body={"address": address, "password": password})
        if not res["ok"]:
            raise RuntimeError(f"mail.tm account create failed: {res}")
        # Fetch token
        tok = cls._post("/token", json_body={"address": address, "password": password})
        token = (tok.get("data") or {}).get("token", "")
        acct = cls(address=address, password=password, token=token)
        logger.info("mail.tm inbox created: %s", address)
        return acct

    def login(self) -> None:
        tok = self._post("/token", json_body={"address": self.address, "password": self.password})
        self.token = (tok.get("data") or {}).get("token", "")
        if not self.token:
            raise RuntimeError(f"mail.tm login failed for {self.address}")

    # ── Inbox ───────────────────────────────────────────────────────
    def list_messages(self, page: int = 1) -> list[dict]:
        data = self._get(f"/messages?page={page}", headers=self.auth_headers)
        items = data.get("hydra:member") or data.get("items") or data.get("member") or []
        return items

    def get_message(self, msg_id: str) -> dict:
        return self._get(f"/messages/{msg_id}", headers=self.auth_headers)

    def wait_for_message(self, timeout_seconds: int = 60, poll: int = 5,
                         predicate=None) -> dict | None:
        """Poll inbox until a message arrives (optionally matching predicate)."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            msgs = self.list_messages()
            for m in msgs:
                if predicate is None or predicate(m):
                    return self.get_message(m.get("id", ""))
            time.sleep(poll)
        return None

    def extract_otp(self, msg: dict) -> list[str]:
        """Pull OTP / verification codes out of a message body."""
        text = (msg.get("text") or msg.get("intro") or "")
        return re.findall(r"\b\d{4,8}\b", text)

    # ── Reply ──────────────────────────────────────────────────────
    def reply(self, to_address: str, subject: str, body: str) -> dict:
        return self._post("/messages", json_body={
            "from": self.address,
            "to": [to_address] if isinstance(to_address, str) else to_address,
            "subject": subject,
            "body": body,
        }, headers=self.auth_headers)

    def to_dict(self) -> dict:
        return {"address": self.address, "password": self.password, "token": self.token}


async def demo() -> None:
    acct = MailTMAccount.create(label="sba")
    print("Created:", acct.address)
    print("Inbox:", acct.list_messages()[:1])
    print("Self dict (persist this):", json.dumps(acct.to_dict()))


if __name__ == "__main__":
    asyncio.run(demo())
