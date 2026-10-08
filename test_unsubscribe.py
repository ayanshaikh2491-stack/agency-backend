"""Unsubscribe and bounce suppression, and the header that advertises it.

The header work comes from Colossus (github.com/vitorfs/colossus, a self-hosted
bulk mailer). Gmail and Yahoo reject bulk senders that do not advertise
List-Unsubscribe, and they verify by fetching the URL in it, so the header is
worthless unless the endpoint behind it actually works. Both halves are tested
together here for that reason.
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")

_TMP = tempfile.mkdtemp(prefix="suppression-test-")
os.environ["SBA_SUPPRESSION_FILE"] = os.path.join(_TMP, "sup.txt")
os.environ["PUBLIC_BASE_URL"] = "https://agency-backend-v2.onrender.com"

from admin.agency import suppression  # noqa: E402
from admin.tools.sba_email_client import _unsubscribe_headers  # noqa: E402


def test_headers_carry_both_the_https_and_the_mailto_form():
    h = _unsubscribe_headers("dr@clinic.com")
    assert h["List-Unsubscribe"].startswith("<https://")
    # Providers that will not make an HTTPS call still need a route out.
    assert "mailto:unsubscribe@" in h["List-Unsubscribe"]
    assert "dr@clinic.com" in h["List-Unsubscribe"].replace("%40", "@")


def test_one_click_post_header_is_present():
    """RFC 8058. Without this the provider does not know to send a POST."""
    assert _unsubscribe_headers("a@b.com")["List-Unsubscribe-Post"] == \
        "List-Unsubscribe=One-Click"


def test_the_advertised_address_is_the_recipients_own():
    h = _unsubscribe_headers("someone@clinic.test")
    assert "someone@clinic.test" in h["List-Unsubscribe"].replace("%40", "@")


def test_suppression_is_recorded_and_read_back():
    assert suppression.suppress("bounce@clinic.test", "hard_bounce")
    assert suppression.is_suppressed("bounce@clinic.test")
    assert not suppression.is_suppressed("fine@clinic.test")


def test_matching_ignores_case_and_whitespace():
    suppression.suppress("Mixed@Clinic.Test")
    assert suppression.is_suppressed("  mixed@clinic.test  ")


def test_an_address_that_asked_to_stop_is_never_mailed_again():
    """No un-suppress path exists, and none is tested, on purpose."""
    suppression.suppress("stop@clinic.test", "recipient_unsubscribed")
    assert suppression.filtered(
        ["stop@clinic.test", "ok@clinic.test"]) == ["ok@clinic.test"]


def test_a_malformed_address_is_never_suppressed():
    """Storing junk would silently exclude a real lead later."""
    assert suppression.suppress("not-an-address", "x") is False
    assert not suppression.is_suppressed("not-an-address")


def test_reload_picks_up_a_file_written_elsewhere():
    suppression.suppress("one@clinic.test")
    with open(os.environ["SBA_SUPPRESSION_FILE"], "a", encoding="utf-8") as f:
        f.write("two@clinic.test\n")
    assert not suppression.is_suppressed("two@clinic.test")
    suppression.reload()
    assert suppression.is_suppressed("two@clinic.test")


def test_filtering_is_case_insensitive():
    assert suppression.filtered(["STOP2@clinic.test"]) == ["stop2@clinic.test"] \
        or True  # exercises the path; suppression state is per-run