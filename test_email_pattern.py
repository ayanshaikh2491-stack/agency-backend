"""Email addresses must never be invented.

Scout (github.com/kiryano/Scout) was given to this agency to extract email
addresses for the SBA agent, and the pattern-detection half of it is ported into
lead_enrichment. Its SMTP verification half is not usable here: it needs
outbound port 25, which is blocked, so a generated address cannot be confirmed
to exist.

That makes pattern candidates guesses, and the rules below are what keep a guess
from becoming a stored contact. An address that bounces marks the sending domain
as spam and teaches the recipient to ignore the agency; an empty field costs one
phone call.
"""
import sys

sys.path.insert(0, ".")

from admin.tools.lead_enrichment import (  # noqa: E402
    _apply_email_pattern,
    _detect_email_pattern,
    _pattern_candidates,
)


def test_detects_the_common_naming_conventions():
    assert _detect_email_pattern("dr.sharma") == "first.last"
    assert _detect_email_pattern("a.sharma") == "f.last"
    assert _detect_email_pattern("contact") == "first"
    # Dotted local parts with three parts are not a convention we can apply.
    assert _detect_email_pattern("dr.vikram.sharma") == ""
    assert _detect_email_pattern("") == ""


def test_applies_the_detected_convention():
    assert _apply_email_pattern("first.last", "dr", "sharma", "x.com") == "dr.sharma@x.com"
    assert _apply_email_pattern("f.last", "a", "sharma", "x.com") == "a.sharma@x.com"
    assert _apply_email_pattern("first", "a", "sharma", "x.com") == "a@x.com"
    assert _apply_email_pattern("bogus", "a", "s", "x.com") == ""


def test_an_f_initial_is_never_empty():
    """f.last on a one-letter first name would produce '.last@domain'."""
    assert _apply_email_pattern("f.last", "", "sharma", "x.com") == ""


def test_candidates_are_never_the_confirmed_address():
    """Re-returning the address we already found adds nothing."""
    out = _pattern_candidates("dr.sharma@clinic.com", {"clinic.com"})
    assert all(c["email"].lower() != "dr.sharma@clinic.com" for c in out)


def test_candidates_are_labelled_unverified():
    """The whole point. A guess must never be able to masquerade as a contact."""
    out = _pattern_candidates("dr.sharma@clinic.com", {"clinic.com"})
    assert out, "expected a candidate from a first.last address"
    for c in out:
        assert c["verified"] == "false"
        assert "not confirmed" in c["reason"]


def test_no_address_means_no_candidates():
    assert _pattern_candidates("", {"clinic.com"}) == []
    assert _pattern_candidates("", set()) == []


def test_an_unlearnable_address_yields_no_candidates():
    """info@clinic.com teaches nothing: you cannot derive a person from it."""
    out = _pattern_candidates("info", set())
    assert out == []


def test_the_returned_payload_keeps_candidates_out_of_email():
    """Even if a caller passes an address, the candidate path is separate."""
    found = "reception@clinic.com"
    candidates = _pattern_candidates(found, {"clinic.com"})
    for c in candidates:
        assert c["email"] != found


def test_a_role_address_is_never_used_to_learn_a_pattern():
    """The bug this caught: "info" looked like a first name and produced the
    candidate "info@", an address with no domain at all."""
    for role in ("info", "contact", "admin", "enquiry", "hello", "support"):
        assert _pattern_candidates(f"{role}@clinic.com", {"clinic.com"}) == []


def test_a_candidate_always_carries_a_domain():
    for sample, doms in [("dr.sharma@clinic.com", set()),
                         ("dr.sharma@clinic.com", {"clinic.com"}),
                         ("a.sharma@clinic.com", {"clinic.com"})]:
        for c in _pattern_candidates(sample, doms):
            _, _, domain = c["email"].partition("@")
            assert domain, f"candidate with no domain: {c['email']}"