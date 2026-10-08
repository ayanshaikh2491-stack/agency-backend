"""Lead capture must survive the output the model actually produced.

The two fixtures below are verbatim SBA output from the live service on
2026-10-08, not invented samples. A parser written against a guessed format
passes every synthetic test and matches nothing in production, which is exactly
what happened before this existed: nineteen prospecting runs, zero leads.

The failure this guards against is not a crash. It is a parser that quietly
returns an empty list, because that is indistinguishable, from the outside, from
"there were no targets in the output".
"""
import asyncio
import sys

sys.path.insert(0, ".")

from admin.agency.lead_capture import capture_leads_from_sba, parse_sba_targets  # noqa: E402

# Verbatim from task ceo_orch_d4374c50b4524036.
LIST_FORMAT = """Niche: Dentists drowning in new-patient lead leakage and after-hours admin.

5 high-fit target accounts:

1. Bright Smile Dental – Austin, TX – Pain: 40 % of new-patient calls/texts unanswered after 6 pm; scheduling bottlenecks. Hook: “Your phone rings at 9 pm but the front desk is closed—let an AI agent capture those leads while you finish procedures.”

2. Peak Physio Clinic – Denver, CO – Pain: 30 % no-show rate bleeding $18k/year; manual reminder follow-ups. Hook: “Recover empty appointment slots with automated confirmations and same-day rescheduling.”

3. Flow Yoga Studio – Portland, OR – Pain: instructor scheduling chaos + member churn from slow reply times. Hook: “Stop managing spreadsheets; let AI handle class waitlists and retention DMs.”

4. NovaSaaS Solutions – Austin, TX – Pain: 14-day onboarding stalls on repetitive QA questions. Hook: “Slash onboarding to 48 hours with an AI co-pilot that answers stack questions 24/7.”

5. Urban Dental Care – Seattle, WA – Pain: Google review gap hurting local SEO; staff too busy to ask. Hook: “Fill your 4.2 → 4.8 star gap while you treat patients—AI sends review requests at checkout.”

Tags: starving-crowd, local-B2B, dentists, physiotherapists, yoga, SaaS, AI-agents, warm-outreach."""

# Verbatim from task ceo_orch_ef7acedf35164a89.
TABLE_FORMAT = """**[SBA] — 5 High-Fit Target Accounts for TAGS Agency**

| # | Business | City | Pain-Point AI Agents Solve | Warm Outreach Hook |
|---|----------|------|---------------------------|-------------------|
| 1 | **Bright Smile Dental** | Austin, TX | New-patient lead flow inconsistent; reviews scattered across Google/Yelp | *"Dr. Lee, noticed your Yelp has 4.8★ but 37 reviews vs. competitors' 200+ — your patients love you, they just aren't telling the story at scale."* |
| 2 | **Revive Physio Studio** | Denver, CO | Referral leakage to PT chains; appointment gaps mid-week | *"Mark — 3 nearby PTs are swallowing your referrals because your website doesn't answer 'do I need a doctor's note?' in 8 seconds."* |
| 3 | **Flow State Yoga** | Portland, OR | Class capacity not filling; Instagram silent for 3 weeks | *"Your last pop-up class sold out in 2 hours — imagine that energy automated on reels while you teach."* |

**Merging note for CEO:** All 5 are local service/SaaS businesses with clear pain points. Ready for CONTENT and WEBSITE merges."""


def test_list_format_real_output():
    leads = parse_sba_targets(LIST_FORMAT)
    assert len(leads) == 5, f"expected 5, got {[l['business_name'] for l in leads]}"
    first = leads[0]
    assert first["business_name"] == "Bright Smile Dental"
    assert first["city"] == "Austin, TX"
    assert "40 %" in first["pain_point"]
    assert first["hook"].startswith("Your phone rings at 9 pm")
    assert "**" not in first["business_name"]


def test_table_format_real_output():
    leads = parse_sba_targets(TABLE_FORMAT)
    assert len(leads) == 3, f"expected 3, got {[l['business_name'] for l in leads]}"
    first = leads[0]
    assert first["business_name"] == "Bright Smile Dental"
    assert first["city"] == "Austin, TX"
    assert "**" not in first["business_name"]
    assert first["hook"].startswith("Dr. Lee")


def test_duplicate_business_names_collapse():
    """The same business legitimately appears across two runs. Creating it twice
    would inflate the pipeline and stop the heartbeat from ever firing again."""
    text = "1. Bright Smile Dental – Austin, TX – Pain: a.\n2. bright   smile dental – Austin, TX – Pain: b."
    leads = parse_sba_targets(text)
    assert len(leads) == 1


def test_empty_text_returns_empty_not_error():
    assert parse_sba_targets("") == []
    assert parse_sba_targets("no targets here at all") == []


def test_header_and_divider_rows_are_not_leads():
    lines = "\n".join([
        "| # | Business | City | Pain | Hook |",
        "|---|----------|------|------|------|",
        "| 1 | Real Clinic | Pune, MH | slow replies | call them |",
    ])
    leads = parse_sba_targets(lines)
    assert len(leads) == 1
    assert leads[0]["business_name"] == "Real Clinic"


def test_city_is_not_repeated_into_the_pain_point():
    """Observed live 2026-10-08: the first ten captured leads stored the city as
    their pain point, e.g. `pain: Jaipur` for a Jaipur clinic. The city was
    consumed into `city` and then joined into the tail anyway."""
    text = "\n".join([
        "1. Sunrise Yoga Studio – Jaipur – Pain: slow replies on Instagram DMs.",
        "2. CityCare Dental – Nagpur – Pain: no-shows at 18 % each week.",
    ])
    leads = parse_sba_targets(text)
    assert len(leads) == 2
    assert leads[0]["city"] == "Jaipur"
    assert leads[0]["pain_point"].rstrip(".") == "slow replies on Instagram DMs"
    assert leads[1]["city"] == "Nagpur"
    assert "Jaipur" not in leads[0]["pain_point"]


def test_city_glued_onto_the_business_name_is_recovered():
    """Observed live: `BrightSmile Dental Nagpur (multi-location) - ...`."""
    text = ("1. BrightSmile Dental Nagpur (multi-location) – Pain: 3 branches, "
            "reception overwhelmed. Hook: \"Let AI answer every branch at once.\"")
    leads = parse_sba_targets(text)
    assert len(leads) == 1
    lead = leads[0]
    assert lead["business_name"] == "BrightSmile Dental"
    assert lead["city"] == "Nagpur"
    assert lead["pain_point"].startswith("3 branches")
    assert lead["hook"].startswith("Let AI answer")


def test_business_name_ending_in_a_qualifier_word_is_not_split():
    """The embedded-city rule must not eat the last word of a plain name."""
    text = "1. Peak Physio Clinic – Denver, CO – Pain: no-shows."
    leads = parse_sba_targets(text)
    assert leads[0]["business_name"] == "Peak Physio Clinic"
    assert leads[0]["city"] == "Denver, CO"


def test_live_string_where_city_shares_the_field_with_prose():
    """Verbatim shape from the first live capture round, where every lead came
    back with city=(none) and pain starting "ProHealth Dental Nagpur.".

    The city sits at the head of the field but the sentence continues after it,
    so _CITY_RE rejected the whole field and the business name and city were
    both swallowed into the pain point."""
    text = ("1. ProHealth Dental – Nagpur. Pain-point: owner responds only on "
            "weekends; leads book competitors. Hook: \"You're losing ~8 leads/week "
            "to the clinic 2 streets away that replies same-day.\"")
    leads = parse_sba_targets(text)
    assert len(leads) == 1
    lead = leads[0]
    assert lead["business_name"] == "ProHealth Dental"
    assert lead["city"] == "Nagpur"
    assert not lead["pain_point"].startswith("-point")
    assert lead["pain_point"].startswith("owner responds only on weekends")
    assert lead["hook"].startswith("You're losing ~8 leads/week")


def test_a_field_label_is_not_mistaken_for_a_city():
    """No dash separator at all, so the second field is the pain text itself."""
    text = "1. Bright Smile Dental Pain: replies only after 6 pm, calls roll over."
    lead = parse_sba_targets(text)[0]
    assert lead["city"] == ""
    assert lead["pain_point"].startswith("replies only after 6 pm")


def test_capture_persists_and_dedupes(monkeypatch):
    created = []

    async def fake_create_lead(data):
        created.append(data)

    class FakeStore:
        @staticmethod
        def list_leads():
            return []

    import admin.agency.sba_store as store
    monkeypatch.setattr(store, "create_lead", fake_create_lead)
    monkeypatch.setattr(store, "list_leads", FakeStore.list_leads)

    rep = asyncio.run(capture_leads_from_sba(LIST_FORMAT, workspace_id="ws1"))
    assert rep["parsed"] == 5
    assert rep["created"] == 5
    assert len(created) == 5
    ctx = created[0]["context"]
    assert ctx["outreach_hook"].startswith("Your phone rings")
    assert ctx["workspace_id"] == "ws1"
    assert created[0]["status"] == "new"


def test_capture_never_raises_on_broken_store(monkeypatch):
    """A lead-capture failure must not turn a good orchestration into an error."""
    import admin.agency.sba_store as store

    async def boom(_data):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "create_lead", boom)
    monkeypatch.setattr(store, "list_leads", lambda: [])

    rep = asyncio.run(capture_leads_from_sba(LIST_FORMAT))
    assert rep["error"] is not None and "db down" in rep["error"]
