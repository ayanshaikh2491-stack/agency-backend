"""SBA Email Client — Send/Receive via owner's email (App Password).

Owner configures Gmail/Yahoo/Outlook app password. SBA uses SMTP to send,
IMAP to check replies, and LLM to enrich lead responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
import imaplib
import email as email_lib
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any

import openai
from admin.config import settings

logger = logging.getLogger(__name__)

# ── Email config from env ───────────────────────────────────────────────

# SBA_OWNER_EMAIL is the canonical name; fall back to TAGS_SMTP_EMAIL so
# .env files that predate the SBA pipeline (or use the older utility name)
# still work without re-typing the app password.
OWNER_EMAIL = (
    os.environ.get("SBA_OWNER_EMAIL", "")
    or os.environ.get("TAGS_SMTP_EMAIL", "")
)
OWNER_EMAIL_PASSWORD = (  # App Password
    os.environ.get("SBA_OWNER_EMAIL_PASSWORD", "")
    or os.environ.get("TAGS_SMTP_PASSWORD", "")
)
OWNER_NAME = os.environ.get("SBA_OWNER_NAME", "Ayan")
SMTP_HOST = os.environ.get("SBA_SMTP_HOST", os.environ.get("TAGS_SMTP_HOST", "smtp.gmail.com"))
SMTP_PORT = int(os.environ.get("SBA_SMTP_PORT", os.environ.get("TAGS_SMTP_PORT", "587")))
IMAP_HOST = os.environ.get("SBA_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("SBA_IMAP_PORT", "993"))


def build_workspace_email_client(workspace_name: str = "agency") -> SBAEmailClient:
    """Pick the right inbox for a workspace.

    - ``agency``: env creds (SBA_OWNER_EMAIL / SBA_OWNER_EMAIL_PASSWORD).
    - client workspace: its OWN ``smtp_email`` + ``smtp_password`` from the
      workspace config. If the client has no creds yet, email is DISABLED so
      we never accidentally send from the agency inbox on a client's behalf.
    """
    if not workspace_name or workspace_name == "agency":
        return SBAEmailClient()
    try:
        from admin.agency import sba_biztypes as biztypes
        cfg = biztypes.get_workspace_config(workspace_name)
    except Exception:  # noqa: BLE001
        logger.warning("workspace config missing for %r, email disabled", workspace_name)
        return SBAEmailClient(email="", password="")
    ws_email = (str(cfg.get("smtp_email") or "").strip()
                or str(cfg.get("owner_email") or "").strip())
    ws_pass = str(cfg.get("smtp_password") or "").strip()
    if not (ws_email and ws_pass):
        logger.warning(
            "Workspace %r has no smtp_email/smtp_password yet — SBA email disabled "
            "so it never uses the agency inbox.", workspace_name
        )
        return SBAEmailClient(email="", password="")
    return SBAEmailClient(
        email=ws_email,
        password=ws_pass,
        name=str(cfg.get("owner_email") or "").strip() or ws_email,
        smtp_host=str(cfg.get("smtp_host") or "").strip(),
        smtp_port=str(cfg.get("smtp_port") or "").strip(),
        imap_host=str(cfg.get("imap_host") or "").strip(),
        imap_port=str(cfg.get("imap_port") or "").strip(),
    )


class SBAEmailClient:
    """Send emails as owner, check replies, auto-enrich with LLM.

    Credentials can come from env (agency default) or per-workspace via
    constructor args (client workspaces use THEIR OWN email + app password,
    never the agency inbox). Pass ``email=""`` to force a disabled client.
    """

    def __init__(self, email: str | None = None, password: str | None = None,
                 name: str | None = None, smtp_host: str | None = None,
                 smtp_port: int | str | None = None, imap_host: str | None = None,
                 imap_port: int | str | None = None) -> None:
        # Instance creds win; otherwise fall back to env defaults (agency).
        # Note: explicit "" (not None) means "force disabled", never fall back.
        self.email = (OWNER_EMAIL if email is None else email or "").strip()
        self.password = OWNER_EMAIL_PASSWORD if password is None else password
        self.name = (name or OWNER_NAME or "").strip()
        self.smtp_host = (smtp_host or SMTP_HOST or "").strip()
        self.imap_host = (imap_host or IMAP_HOST or "").strip()
        try:
            self.smtp_port = int(smtp_port) if smtp_port else SMTP_PORT
        except (TypeError, ValueError):
            self.smtp_port = SMTP_PORT
        try:
            self.imap_port = int(imap_port) if imap_port else IMAP_PORT
        except (TypeError, ValueError):
            self.imap_port = IMAP_PORT
        self._enabled = bool(self.email and self.password)
        if not self._enabled:
            logger.warning(
                "SBA email disabled. Set SBA_OWNER_EMAIL and SBA_OWNER_EMAIL_PASSWORD (app password)."
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        cc_owner: bool = True,
    ) -> bool:
        """Send an email as the owner via SMTP.

        Args:
            to_email: Lead's email address.
            subject: Email subject line.
            body_text: Plain text body.
            cc_owner: If True, BCC a copy to owner (the parameter is named\n                cc_owner for brevity, but it sends a BCC, not a CC).

        Returns: True if sent successfully.
        """
        if not self._enabled:
            logger.warning("Email disabled — cannot send.")
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.name} <{self.email}>"
        msg["To"] = to_email
        msg["Subject"] = subject

        # Plain text part
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        try:
            loop = asyncio.get_running_loop()

            def _send() -> None:
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.email, self.password)
                    server.sendmail(self.email, [to_email], msg.as_string())
                    if cc_owner:
                        # BCC to owner
                        bcc_msg = MIMEText(
                            f"📧 SBA sent email to {to_email}\n\nSubject: {subject}\n\n{body_text[:500]}",
                            "plain",
                            "utf-8",
                        )
                        bcc_msg["From"] = f"SBA <{self.email}>"
                        bcc_msg["To"] = self.email
                        bcc_msg["Subject"] = f"[SBA] Sent to {to_email}: {subject}"
                        server.sendmail(self.email, [self.email], bcc_msg.as_string())

            await loop.run_in_executor(None, _send)
            logger.info("Email sent to %s: %s", to_email, subject)
            return True

        except Exception:
            logger.exception("Failed to send email to %s", to_email)
            return False

    async def check_replies(self, mark_read: bool = True) -> list[dict[str, Any]]:
        """Check inbox for replies to SBA-sent emails.

        Returns list of dicts with:
          - from_addr, subject, body_preview, enriched (from LLM)
        """
        if not self._enabled:
            return []

        replies: list[dict[str, Any]] = []

        try:
            loop = asyncio.get_running_loop()

            def _fetch() -> list[dict[str, Any]]:
                result: list[dict[str, Any]] = []
                mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
                mail.login(self.email, self.password)
                mail.select("INBOX")

                # Search for unseen emails (or recent replies)
                status, messages = mail.search(None, "UNSEEN")
                if status != "OK":
                    mail.logout()
                    return result

                for num in messages[0].split():
                    try:
                        _status, msg_data = mail.fetch(num, "(RFC822)")
                        if _status != "OK":
                            continue

                        raw_email = msg_data[0][1]
                        msg = email_lib.message_from_bytes(raw_email)

                        # subject and multi-part encoded subjects
                        raw_subject = msg["Subject"]
                        if raw_subject is None:
                            subject = ""
                        else:
                            parts = decode_header(raw_subject)
                            subject_parts: list[str] = []
                            for raw_part, encoding in parts:
                                if isinstance(raw_part, bytes):
                                    subject_parts.append(
                                        raw_part.decode(encoding or "utf-8", errors="replace")
                                    )
                                elif isinstance(raw_part, str):
                                    subject_parts.append(raw_part)
                            subject = "".join(subject_parts)
                        from_addr = msg.get("From", "")
                        body_text = self._get_body(msg)

                        # Skip auto-replies
                        if self._is_auto(subject, from_addr):
                            continue

                        result.append({
                            "from_addr": from_addr,
                            "subject": subject,
                            "body_preview": body_text[:300],
                            "body_full": body_text[:2000],
                        })

                        if mark_read:
                            mail.store(num, "+FLAGS", "\\Seen")
                    except Exception:
                        continue

                mail.logout()
                return result

            raw_replies = await loop.run_in_executor(None, _fetch)

            # Enrich each reply with LLM
            for reply in raw_replies:
                enriched = await self._enrich_reply(reply)
                reply["enriched"] = enriched
                replies.append(reply)

        except Exception:
            logger.exception("Email check failed")

        return replies

    def _get_body(self, msg: Any) -> str:
        """Extract plain text body from email."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
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
        return body[:5000]

    def _is_auto(self, subject: str, from_addr: str) -> bool:
        """Detect auto-replies, bounces, newsletters."""
        indicators = [
            "out of office", "auto-reply", "autoreply",
            "returned mail", "undeliverable", "mail delivery failed",
            "unsubscribe", "newsletter", "noreply",
        ]
        text = (subject + " " + from_addr).lower()
        return any(i in text for i in indicators)

    async def _enrich_reply(self, reply: dict[str, Any]) -> dict[str, Any]:
        """Use LLM to understand lead's reply.

        Returns:
          is_interested (bool): Lead interested in meeting?
          suggested_time (str): If they mentioned a time.
          sentiment (str): positive/neutral/negative
          score_change (int): How lead score should change.
          summary (str): One-line summary.
        """
        try:
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
                            "You are SBA's email reply analyzer. Analyze this lead reply. "
                            "Respond in JSON only with keys: "
                            "is_interested (bool), suggested_time (str or null), "
                            "sentiment (positive/neutral/negative), "
                            "score_change (-20 to 20), "
                            "summary (str), needs_followup (bool)"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"From: {reply['from_addr']}\n"
                            f"Subject: {reply['subject']}\n\n"
                            f"{reply['body_full']}"
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
            return {
                "is_interested": False,
                "suggested_time": None,
                "sentiment": "neutral",
                "score_change": 0,
                "summary": "Could not analyze reply.",
                "needs_followup": False,
                "uncertain": True,
                "reason": "LLM unavailable - could not analyze",
            }
