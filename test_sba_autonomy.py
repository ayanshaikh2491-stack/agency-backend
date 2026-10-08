"""The SBA agent asks the owner for permission instead of doing its job.

Asked to name its best leads, it replied "Want me to qualify any of these with
BANT or start outreach?" The owner is running a business, not supervising a
salesperson's desk. Qualifying a lead and drafting outreach are decisions
inside the sales role, so they are the agent's to make.

These tests read the deployed prompt rather than a copy of it, so the rule
cannot drift back into offering instead of acting.
"""
import os
import sys

sys.path.insert(0, ".")

from admin.workspace.agents.sba import SBA_SYSTEM_PROMPT  # noqa: E402


def test_prompt_states_autonomy_explicitly():
    prompt = SBA_SYSTEM_PROMPT.lower()
    assert "autonomously" in prompt
    assert "do not ask permission" in prompt


def test_prompt_forbids_ending_with_a_question():
    """The exact failure. Offering a next step reads as deferring to the owner."""
    assert "want me to" in SBA_SYSTEM_PROMPT.lower()
    assert "should i start outreach" in SBA_SYSTEM_PROMPT.lower()


def test_prompt_separates_sales_execution_from_ceo_strategy():
    """Escalation has to be defined, or autonomy either stalls or runs away."""
    low = SBA_SYSTEM_PROMPT.lower()
    assert "escalate to the ceo" in low
    for topic in ("niche", "spending money", "budget"):
        assert topic in low, f"CEO escalation does not mention {topic}"


def test_prompt_forbids_listing_without_doing():
    """A turn that only produces a list is the same failure in another form."""
    assert "do not stop at listing them" in SBA_SYSTEM_PROMPT.lower()


def test_prompt_still_forbids_inventing_contact_details():
    """Autonomy must not extend to making up an email address. This stays."""
    assert "never invent one" in SBA_SYSTEM_PROMPT.lower()
    assert "none on file" in SBA_SYSTEM_PROMPT.lower()


def test_prompt_still_forbids_fabricated_leads():
    assert "don't just make up lead lists" in SBA_SYSTEM_PROMPT.lower()


def test_the_agent_still_has_the_tools_to_act():
    """Prompting for autonomy is pointless unless the tools are registered."""
    import admin.workspace.agents.sba as sba_mod

    names = {t["function"]["name"] for t in sba_mod.SBA_SYSTEM_PROMPT and getattr(sba_mod, "SBA_TOOLS", [])}
    assert "find_leads_http" in names
    assert "save_lead_record" in names
    assert "qualify_lead" in names


def test_prompt_file_is_the_one_being_edited():
    """Guards against testing a stale copy."""
    path = os.path.join(os.path.dirname(sys.modules["admin.workspace.agents.sba"].__file__), "sba.py")
    text = open(path, encoding="utf-8").read()
    assert "DO NOT ASK PERMISSION" in text