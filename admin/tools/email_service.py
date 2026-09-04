"""Email Service — lead discovery from email inbox.

SBA uses this to:
  1. Check for incoming lead inquiries
  2. Extract lead info from emails
  3. Auto-create leads in SBA store
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class EmailLeadService:
    """Check email inbox for lead inquiries and auto-create leads.

    Supports:
      - IMAP email checking (configurable via env vars)
      - Auto-extraction of lead name, business, contact info
      - AI-based enrichment using SBA's LLM
      - Auto-creation of leads in SBA store
    """

    def __init__(self) -> None:
        self._last_check: str | None = None
        self._enabled = bool(os.environ.get("SBA_EMAIL_ENABLED", ""))

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def check_inbox(self, mark_read: bool = True) -> list[dict[str, Any]]:
        """Check email inbox for new lead inquiries.

        Returns list of potential leads found.
        Uses IMAP if configured, otherwise returns empty.
        """
        if not self._enabled:
            logger.debug("Email lead service disabled (set SBA_EMAIL_ENABLED=1)")
            return []

        leads_found: list[dict[str, Any]] = []

        try:
            import imaplib
            import email as email_lib
            from email.header import decode_header

            host = os.environ.get("SBA_EMAIL_IMAP_HOST", "")
            port = int(os.environ.get("SBA_EMAIL_IMAP_PORT", "993"))
            user = os.environ.get("SBA_EMAIL_USER", "")
            password = os.environ.get("SBA_EMAIL_PASS", "")

            if not all([host, user, password]):
                logger.warning("Email IMAP not configured (set SBA_EMAIL_IMAP_HOST/USER/PASS)")
                return []

            # Connect to IMAP
            mail = imaplib.IMAP4_SSL(host, port)
            mail.login(user, password)
            mail.select("INBOX")

            # Search for unseen emails
            status, messages = mail.search(None, "UNSEEN")
            if status != "OK":
                return []

            for num in messages[0].split():
                try:
                    status, msg_data = mail.fetch(num, "(RFC822)")
                    if status != "OK":
                        continue

                    raw_email = msg_data[0][1]
                    msg = email_lib.message_from_bytes(raw_email)

                    # Extract basic info
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding or "utf-8", errors="replace")

                    from_addr = msg.get("From", "")
                    body_text = self._get_email_body(msg)

                    # Skip auto-replies and newsletters
                    if self._is_auto_reply(subject, from_addr):
                        continue

                    # Enrich with LLM
                    enriched = await self._enrich_lead(from_addr, subject, body_text)

                    leads_found.append({
                        "source": "email",
                        "from": from_addr,
                        "subject": subject,
                        "body_preview": body_text[:200],
                        "enriched": enriched,
                    })

                    if mark_read:
                        mail.store(num, "+FLAGS", "\\Seen")

                except Exception as e:
                    logger.warning("Error processing email: %s", e)
                    continue

            mail.logout()

        except ImportError:
            logger.debug("imaplib not available")
        except Exception as e:
            logger.error("Email check failed: %s", e)

        return leads_found

    async def process_and_create_leads(
        self,
        auto_qualify: bool = True,
    ) -> list[dict[str, Any]]:
        """Check inbox and auto-create leads from found inquiries.

        Returns list of created lead records.
        """
        emails = await self.check_inbox()
        created_leads: list[dict[str, Any]] = []

        from admin.agency.sba_store import create_lead

        for email_data in emails:
            enriched = email_data.get("enriched", {})
            if not enriched or enriched.get("is_lead") is False:
                continue

            try:
                lead = await create_lead({
                    "name": enriched.get("name", email_data["from"]),
                    "business_name": enriched.get("business", ""),
                    "email": self._extract_email(email_data["from"]),
                    "source": "email_inquiry",
                    "score": enriched.get("score", 50),
                    "context": {
                        "subject": email_data["subject"],
                        "body_preview": email_data["body_preview"],
                        "needs": enriched.get("needs", []),
                        "source": "email",
                    },
                    "notes": [{
                        "text": f"Email inquiry: {email_data['subject']}\n{email_data['body_preview']}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "source": "email_auto",
                    }],
                })
                created_leads.append(lead)
                logger.info("Auto-created lead from email: %s", lead.get("name"))
            except Exception as e:
                logger.error("Failed to create lead from email: %s", e)

        self._last_check = datetime.now(timezone.utc).isoformat()
        return created_leads

    # ── Private helpers ────────────────────────────────────────────────

    def _get_email_body(self, msg: Any) -> str:
        """Extract text body from email message."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body += payload.decode("utf-8", errors="replace")
                    except Exception:
                        pass
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
            except Exception:
                pass
        return body[:5000]  # Limit body size

    def _is_auto_reply(self, subject: str, from_addr: str) -> bool:
        """Detect auto-replies, bounces, newsletters."""
        auto_indicators = [
            "out of office", "auto-reply", "autoreply", "auto reply",
            "returned mail", "undeliverable", "bounce", "mail delivery failed",
            "unsubscribe", "newsletter", "do not reply", "noreply",
        ]
        subject_lower = subject.lower()
        from_lower = from_addr.lower()
        for indicator in auto_indicators:
            if indicator in subject_lower or indicator in from_lower:
                return True
        return False

    def _extract_email(self, from_addr: str) -> str:
        """Extract email address from 'Name <email>' format."""
        import re
        match = re.search(r'<([^>]+)>', from_addr)
        if match:
            return match.group(1)
        return from_addr.strip()

    async def _enrich_lead(
        self,
        from_addr: str,
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        """Use LLM to enrich email into a lead record.

        Returns dict with: name, business, needs, score, is_lead
        """
        try:
            import openai
            from admin.config import settings

            client = openai.AsyncOpenAI(
                api_key=settings.WORKSPACE_API_KEY or None,
                base_url=settings.WORKSPACE_API_BASE or None,
            )

            resp = await client.chat.completions.create(
                model=settings.WORKSPACE_AGENT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are SBA's email parsing specialist. Extract lead info from this email. "
                            "Respond in JSON only with keys: "
                            "is_lead (bool), name, business, needs (list), score (0-100), reason"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"From: {from_addr}\nSubject: {subject}\n\n{body[:2000]}"
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"},
            )

            text = resp.choices[0].message.content or "{}"
            return json.loads(text)
        except Exception:
            return {"is_lead": False, "name": "", "business": "", "needs": [], "score": 50}
