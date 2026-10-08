"""The CEO dispatches work to an agent that cannot see anything.

The thinker prompt listed counts: `{"total_leads": 5}`. From that the CEO would
conclude the highest-value move was to score the five leads, and would dispatch
that sentence to the `analyzing` agent. The agent receives the task text as its
only context, so it replied, correctly and repeatedly:

    I don't have access to your CRM, lead list, or any internal data, so I
    can't score the 5 leads without risking hallucination.

Twelve consecutive delegations in that shape on 2026-10-08, every one reported
`done`, none of them doing the work. Nothing was broken in the agents. The CEO
was asking for work it had not given enough information to perform.

`_lead_brief` puts the leads themselves into the prompt, and says so explicitly
when a lead has no contact details, so the CEO does not plan outreach that
cannot happen.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from admin.agency import ceo_autonomy  # noqa: E402

LEADS = [
    {"business_name": "SmileCraft Dental Clinic", "city": "Pune", "status": "new",
     "score": 60, "email": "", "phone": "",
     "context": {"pain_point": "120+ reviews a month sitting unanswered",
                 "outreach_hook": "Your Google pack fell 3 spots last month"}},
    {"business_name": "PhysioCare Clinic", "city": "Indore", "status": "new",
     "score": 60, "email": "front@physiocare.test", "phone": "",
     "context": {"pain_point": "Manual response takes 4 hrs/week",
                 "outreach_hook": "Your last 3 one-star reviews got no reply"}},
    {"business_name": "Closed Client", "city": "Pune", "status": "closed",
     "score": 60, "context": {}},
]


def _install(monkeypatch, leads):
    import admin.agency.sba_store as store
    monkeypatch.setattr(store, "list_leads", lambda: leads)


def test_brief_names_the_leads_not_just_the_count(monkeypatch):
    _install(monkeypatch, LEADS)
    brief = asyncio.run(ceo_autonomy._lead_brief())
    assert "SmileCraft Dental Clinic (Pune)" in brief
    assert "PhysioCare Clinic (Indore)" in brief
    assert "120+ reviews a month sitting unanswered" in brief
    assert "Your Google pack fell 3 spots last month" in brief


def test_missing_contact_is_stated_explicitly(monkeypatch):
    """The CEO should not plan outreach it cannot execute."""
    _install(monkeypatch, LEADS)
    brief = asyncio.run(ceo_autonomy._lead_brief())
    assert "contact: NONE ON FILE" in brief
    assert "contact: front@physiocare.test" in brief


def test_closed_and_lost_leads_are_excluded(monkeypatch):
    """Working a closed deal is not the highest-value internal action."""
    _install(monkeypatch, LEADS)
    brief = asyncio.run(ceo_autonomy._lead_brief())
    assert "Closed Client" not in brief


def test_empty_pipeline_says_so(monkeypatch):
    _install(monkeypatch, [])
    assert asyncio.run(ceo_autonomy._lead_brief()) == "(no open leads)"


def test_brief_is_bounded(monkeypatch):
    """Sent to a small free model every think tick, so it must not grow with
    the pipeline."""
    many = [{"business_name": f"Clinic {i}", "city": "Pune", "status": "new",
             "score": 60, "context": {}} for i in range(50)]
    _install(monkeypatch, many)
    brief = asyncio.run(ceo_autonomy._lead_brief())
    assert "Clinic 7" in brief
    assert "Clinic 8" not in brief
    assert "and 42 more open lead(s)" in brief


def test_a_broken_store_does_not_break_the_thinker(monkeypatch):
    import admin.agency.sba_store as store

    def _boom():
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "list_leads", _boom)
    assert asyncio.run(ceo_autonomy._lead_brief()) == "(lead list unavailable)"
