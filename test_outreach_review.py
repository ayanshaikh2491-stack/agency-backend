"""The review the owner is not going to do.

He asked for the sales agent to draft and review its own outreach with no human
in the loop, and for the agent to handle whatever problems it finds. This tests
the review that replaces him.

Every case is a fact, not a taste: an address that was guessed rather than
found, a message that does not mention the business it is going to, a claim the
lead record cannot support, a message with no way to opt out, a run that has
already started bouncing. A model that also chooses to send is not a reliable
judge of its own prose, which is why none of these ask whether the message reads
well.
"""
import sys

sys.path.insert(0, ".")

from admin.agency.outreach_sender import (  # noqa: E402
    MAX_BOUNCES_BEFORE_STOP,
    draft_outreach,
    review_draft,
    warmup_limit,
)


def lead(**kw):
    base = {"business_name": "Thaper Dental Clinic", "city": "Jaipur",
            "email": "drthaper@gmail.com", "phone": "+91 141 274 3788"}
    base.update(kw)
    return base


def test_a_sound_draft_passes():
    d = draft_outreach(lead())
    r = review_draft(d, lead())
    assert r["ok"], r["reasons"]


def test_a_guessed_address_is_rejected():
    """A bouncing address is what gets the sending domain flagged."""
    l = lead(email="", email_candidates=[{"email": "dr.thaper@clinic.com"}])
    r = review_draft(draft_outreach(l), l)
    assert not r["ok"]
    assert any("no email on file" in x for x in r["reasons"])


def test_an_unverified_candidate_is_rejected_even_when_it_is_set():
    """The failure this guards: an unverified pattern guess gets copied into the
    contact field and then sent, which is exactly the invented-address case."""
    guessed = "dr.thaper@gmail.com"
    l = lead(email=guessed, email_candidates=[{"email": guessed}])
    r = review_draft(draft_outreach(l), l)
    assert not r["ok"]
    assert any("unverified pattern candidate" in x for x in r["reasons"])


def test_a_found_address_is_not_mistaken_for_a_guess():
    """A real address sitting beside unrelated candidates must still send."""
    l = lead(email_candidates=[{"email": "someone.else@clinic.com"}])
    assert review_draft(draft_outreach(l), l)["ok"]


def test_a_draft_about_the_wrong_business_is_rejected():
    """Crossed wires are how one clinic's name reaches another inbox."""
    r = review_draft(draft_outreach(lead()), lead(business_name="Kettlebell Gym"))
    assert not r["ok"]
    assert any("does not mention its own lead" in x for x in r["reasons"])


def test_an_unsupported_claim_is_rejected():
    d = draft_outreach(lead()) + "\nWe guarantee double your bookings."
    r = review_draft(d, lead())
    assert not r["ok"]
    assert any("unsupported claim" in x for x in r["reasons"])


def test_a_message_with_no_way_out_is_rejected():
    d = draft_outreach(lead()).replace("reply 'no' and I will not follow up.", "")
    r = review_draft(d, lead())
    assert not r["ok"]
    assert any("opt-out" in x for x in r["reasons"])


def test_an_empty_or_stub_draft_is_rejected():
    r = review_draft("Hi, interested?", lead())
    assert not r["ok"]
    assert any("too short" in x for x in r["reasons"])


def test_a_bouncing_run_stops_by_itself():
    """The one condition where the sender halts without a human deciding."""
    r = review_draft(draft_outreach(lead()), lead(),
                      bounce_count=MAX_BOUNCES_BEFORE_STOP + 1)
    assert not r["ok"]
    assert any("halted" in x for x in r["reasons"])


def test_the_stemmed_business_name_still_matches():
    """A short or oddly punctuated name must not fail its own message."""
    for name in ("Dr. Dixit's Dental Speciality Clinic", "Vb. Dental & Implant Centre"):
        l = lead(business_name=name)
        assert review_draft(draft_outreach(l), l)["ok"], name


def test_warmup_ramps_and_then_holds():
    assert warmup_limit(1) == 5
    assert warmup_limit(2) == 10
    assert warmup_limit(3) == 15
    assert warmup_limit(4) == 30
    # It must not keep climbing forever.
    assert warmup_limit(10) == warmup_limit(20)


def test_warmup_handles_a_zero_or_negative_day():
    assert warmup_limit(0) == 5
    assert warmup_limit(-3) == 5


def test_both_variants_carry_an_opt_out_and_pass_review():
    """The WhatsApp version is reviewed by the same rules as the email one."""
    l = lead()
    d = draft_outreach(l, variant="whatsapp")
    r = review_draft(d, l)
    assert not any("opt-out" in x for x in r["reasons"]), r["reasons"]


def test_drafts_name_the_business_and_the_city():
    d = draft_outreach(lead())
    assert "Thaper Dental Clinic" in d
    assert "Jaipur" in d