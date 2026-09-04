"""SBA Email Templates — 4 professional templates for lead outreach flow.

All templates in Hinglish (owner ke liye) or professional English (lead ke liye).
Templates use {placeholder} for dynamic values.
"""

from __future__ import annotations

from typing import Any

# ── Template 1: First Contact to Lead ───────────────────────────────────

TEMPLATE_FIRST_CONTACT = """Hi {lead_name},

I came across {platform} and saw you were looking for help with {project_type}.

We're TAGS Agency — we specialize in {service_description}. We've helped
businesses like yours achieve {sample_result}.

Would you be available for a quick 15-minute call this week to discuss
how we can help?

Looking forward to hearing from you!

Best,
{owner_name}
TAGS Agency
{owner_email}
"""

# ── Template 2: Notify Owner about Lead Reply ──────────────────────────

TEMPLATE_OWNER_NOTIFICATION = """Boss! {lead_name} ne reply kiya hai! 🎯

Company: {business_name}
Score: {score}/100
Timeline: {timeline}

Unhone kaha:
"{reply_summary}"

Suggested Time: {suggested_time}

👉 Kya main meeting confirm karun?
Reply: "Haan" → Meeting setup + lead ko confirmation
Reply: "Nahi, [day] [time]" → Change time
Reply: "Nahi" → Lead ko polite "not right now" email
"""

# ── Template 3: Meeting Confirmation to Lead ───────────────────────────

TEMPLATE_MEETING_CONFIRM = """Hi {lead_name},

Perfect! Our team is looking forward to connecting with you.

Meeting Details:
📅 Date: {meeting_date}
⏰ Time: {meeting_time}
🔗 Link: {meeting_link}
⏱ Duration: 30 minutes

Please let me know if this time still works for you, or if you need to
reschedule.

See you there!

Best,
{owner_name}
TAGS Agency
"""

# ── Template 4: Post-Meeting Follow-up ─────────────────────────────────

TEMPLATE_FOLLOWUP = """Hi {lead_name},

Great meeting today! Here's a quick summary of what we discussed:

{summary_bullets}

Next Steps:
{next_steps}

If you have any questions, feel free to reply to this email.

Looking forward to working together!

Best,
{owner_name}
TAGS Agency
"""

# ── Template 5: Rejected / Not Right Now ───────────────────────────────

TEMPLATE_REJECTED = """Hi {lead_name},

No worries at all! Totally understand if now isn't the right time.

If your situation changes or you'd like to revisit this in the future,
feel free to reach out anytime.

Wishing you all the best!

{owner_name}
TAGS Agency
"""


def format_template(name: str, **kwargs: Any) -> str:
    """Format an email template by name with given kwargs.

    Args:
        name: Template name (e.g. 'first_contact', 'owner_notification').
        **kwargs: Values to fill placeholders.

    Returns: Filled template string.

    Raises: ValueError if template not found.
    """
    templates = {
        "first_contact": TEMPLATE_FIRST_CONTACT,
        "owner_notification": TEMPLATE_OWNER_NOTIFICATION,
        "meeting_confirm": TEMPLATE_MEETING_CONFIRM,
        "followup": TEMPLATE_FOLLOWUP,
        "rejected": TEMPLATE_REJECTED,
    }
    tpl = templates.get(name)
    if not tpl:
        raise ValueError(f"Unknown template: {name}. Available: {list(templates.keys())}")
    return tpl.format(**kwargs)
