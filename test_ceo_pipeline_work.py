"""The agency had 128 leads and did nothing with them.

Prospecting only fired when the pipeline was empty, so once leads existed the
heartbeat fell through to observe and reported no urgent work. Finding leads is
not the job of this agency; selling to them is. This is the branch that does the
work, and these tests cover what it hands to the SBA.

Two properties matter most. It must name real leads with their real contact
details, because the SBA sees only the task text and cannot read the database.
And it must refuse to invent a contact, because a fabricated address bounces and
costs the sending domain.
"""
import sys

sys.path.insert(0, ".")

from admin.agency import ceo_autonomy as ca  # noqa: E402


def patch_leads(monkeypatch, rows):
    import admin.agency.sba_store as store
    monkeypatch.setattr(store, "list_leads", lambda: rows)


def lead(name, **kw):
    base = {"business_name": name, "city": "Pune", "status": "new", "score": 70,
            "email": "", "phone": "", "website": ""}
    base.update(kw)
    return base


def test_it_names_the_leads_and_their_contact(monkeypatch):
    patch_leads(monkeypatch, [lead("Thaper Dental Clinic", phone="+91 141 274 3788")])
    brief = ca._pipeline_brief()
    assert brief and "Thaper Dental Clinic" in brief["task"]
    assert "+91 141 274 3788" in brief["task"]


def test_a_lead_with_no_contact_says_so_rather_than_guessing(monkeypatch):
    patch_leads(monkeypatch, [lead("Jain Eye Clinic")])
    brief = ca._pipeline_brief()
    assert "NONE ON FILE" in brief["task"]
    # The instruction must forbid inventing one, not just label it.
    assert "never invent" in brief["task"].lower()


def test_contactable_leads_are_chosen_first(monkeypatch):
    patch_leads(monkeypatch, [
        lead("A Clinic", email="a@clinic.test", score=60),
        lead("B Clinic", score=90),
    ])
    brief = ca._pipeline_brief(limit=1)
    assert "A Clinic" in brief["task"], "the lead we can actually contact should win"


def test_closed_and_lost_leads_are_not_worked(monkeypatch):
    patch_leads(monkeypatch, [
        lead("Dead One", status="closed"),
        lead("Lost One", status="lost"),
    ])
    assert ca._pipeline_brief() is None


def test_nothing_to_work_returns_none(monkeypatch):
    patch_leads(monkeypatch, [])
    assert ca._pipeline_brief() is None


def test_a_broken_store_never_raises_into_the_heartbeat(monkeypatch):
    import admin.agency.sba_store as store

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "list_leads", boom)
    assert ca._pipeline_brief() is None


def test_the_task_tells_the_agent_to_act_not_ask(monkeypatch):
    """The SBA used to reply 'want me to qualify these' and do nothing."""
    patch_leads(monkeypatch, [lead("A Clinic", phone="+919999999999")])
    task = ca._pipeline_brief()["task"].lower()
    assert "decide and act" in task
    assert "do not ask" in task


def test_the_brief_is_bounded(monkeypatch):
    patch_leads(monkeypatch, [lead(f"Clinic {i}") for i in range(40)])
    brief = ca._pipeline_brief(limit=5)
    assert brief["chosen"] == 5
    assert brief["total"] == 40


def test_live_prompts_are_bounded(monkeypatch):
    """A 200 lead list would blow the task budget and lose the delegation."""
    patch_leads(monkeypatch, [lead(f"Clinic {i}", phone=f"+91{i:010d}") for i in range(60)])
    assert len(ca._pipeline_brief(limit=8)["task"]) < 4000