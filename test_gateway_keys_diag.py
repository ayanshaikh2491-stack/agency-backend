"""The gateway is the one component nobody could see into.

It runs on its own port with its own database inside the same container, and
every agent fails with the same opaque 503: no candidate model has a configured,
usable provider key. start.sh seeds it with curl -f, which throws away the HTTP
status of a failed seed, so there was no way to tell a key that was never seeded
from one that was seeded and is not usable. That gap cost hours: the whole
agency looked broken while the only missing thing was one env var.

These tests cover the diagnostic itself. It reports the difference between what
was intended and what the gateway holds, and it never raises, because it runs
from a health report that other systems depend on.
"""
import json
import sys

sys.path.insert(0, ".")

from admin.agency.runtime_fix import check_gateway_keys  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


def install_fake(mp, keys_payload=None, token="tok", fail_auth=False):
    mp.setenv("PROVIDER_KEYS_JSON", json.dumps(
        [{"platform": "groq", "key": "gsk_x", "label": "primary"}]))
    mp.setenv("FREEAPI_ADMIN_EMAIL", "ops@test.local")
    mp.setenv("FREEAPI_ADMIN_PASSWORD", "pw")

    class FakeClient:
        def post(self, url, json=None, timeout=None):
            if fail_auth:
                return FakeResponse({"error": "nope"}, 401)
            return FakeResponse({"token": token})

        def get(self, url, headers=None, timeout=None):
            return FakeResponse(keys_payload if keys_payload is not None else [])

    import httpx
    fake = FakeClient()
    mp.setattr(httpx, "post", fake.post)
    mp.setattr(httpx, "get", fake.get)
    return fake


def base_env(mp, base="http://127.0.0.1:3001/v1"):
    mp.setenv("WORKSPACE_API_BASE", base)
    mp.setenv("WORKSPACE_API_KEY", "u")
    return base


def test_it_reports_a_key_that_reached_the_gateway(monkeypatch):
    mp = monkeypatch
    base_env(mp)
    install_fake(mp, keys_payload=[{"platform": "groq", "label": "primary"}])
    out = check_gateway_keys()
    assert out["ok"] is True
    assert out["missing"] == []
    assert out["seeded_platforms"] == ["groq"]


def test_it_reports_a_key_that_did_not(monkeypatch):
    mp = monkeypatch
    """The exact failure: intended but never seeded."""
    base_env(mp)
    install_fake(mp, keys_payload=[])
    out = check_gateway_keys()
    assert out["ok"] is False
    assert out["missing"] == ["groq"], "the whole point is to name what is missing"


def test_it_says_when_the_gateway_will_not_issue_a_token(monkeypatch):
    mp = monkeypatch
    base_env(mp)
    install_fake(mp, fail_auth=True)
    out = check_gateway_keys()
    assert out["ok"] is False
    assert "token" in out.get("error", "")


def test_it_reports_missing_admin_credentials(monkeypatch):
    mp = monkeypatch
    base_env(mp)
    for k in ("PROVIDER_KEYS_JSON", "FREEAPI_ADMIN_EMAIL", "FREEAPI_ADMIN_PASSWORD"):
        mp.delenv(k, raising=False)
    out = check_gateway_keys()
    assert out["ok"] is False
    assert out["admin_configured"] is False


def test_it_strips_the_openai_suffix_to_reach_the_gateway(monkeypatch):
    mp = monkeypatch
    """The router is one path up from the openai-compatible base."""
    base_env(mp, base="http://127.0.0.1:3001/v1")
    install_fake(mp, keys_payload=[{"platform": "groq"}])
    out = check_gateway_keys()
    assert out["gateway_root"] == "http://127.0.0.1:3001"


def test_malformed_provider_json_does_not_raise(monkeypatch):
    mp = monkeypatch
    base_env(mp)
    install_fake(mp)
    mp.setenv("PROVIDER_KEYS_JSON", "{not json")
    out = check_gateway_keys()
    assert out["intended_platforms"] == []


def test_it_never_raises_into_the_health_report(monkeypatch):
    mp = monkeypatch
    base_env(mp)
    install_fake(mp)
    import httpx

    def boom(*a, **k):
        raise RuntimeError("network down")

    mp.setattr(httpx, "get", boom)
    out = check_gateway_keys()
    assert isinstance(out, dict)
    assert "error" in out
