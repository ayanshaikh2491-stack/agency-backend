"""Brevo email sender for agents."""
import os
import httpx
from typing import Optional

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

async def send_email(
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    from_email: str = "sba@tagsagency.ai",
    from_name: str = "TAGS Agency SBA"
) -> dict:
    """Send email via Brevo API."""
    if not BREVO_API_KEY:
        return {"success": False, "error": "BREVO_API_KEY not configured"}

    payload = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
        "textContent": text or html.replace("<br>", "\n").replace("<p>", "").replace("</p>", "\n"),
    }

    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(BREVO_API_URL, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                return {"success": True, "message_id": resp.json().get("messageId")}
            return {"success": False, "error": f"{resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def send_bulk_email(
    recipients: list[str],
    subject: str,
    html: str,
    text: Optional[str] = None,
    from_email: str = "sba@tagsagency.ai",
    from_name: str = "TAGS Agency"
) -> dict:
    """Send same email to multiple recipients (max 50 per call for free tier)."""
    results = []
    for i in range(0, len(recipients), 50):
        batch = recipients[i:i+50]
        for to in batch:
            res = await send_email(to, subject, html, text, from_email, from_name)
            results.append({"to": to, **res})
    return {"success": True, "results": results}