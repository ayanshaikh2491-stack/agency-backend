"""Focused tests for the Telegram chat authorization guard.

Covers the three required cases (authorised chat passes, unauthorised chat is
rejected, unset chat id fails closed) plus the edge cases the guard documents:
multiple chat ids, blank/invalid config entries, group chats where the sender is
not the chat, and channel posts / anonymous admins that carry no `from` field.

Run from the backend-deploy directory:
    python -m pytest test_telegram_auth.py -v
"""
import ast
import asyncio
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.comm import telegram

OWNER_CHAT = 5412605117
OTHER_CHAT = -1001234567890
OWNER_USER = 700001
OTHER_USER = 700002


def make_message(chat_id, text="/status", user_id=OWNER_USER):
    message = {"message_id": 1, "chat": {"id": chat_id, "type": "private"}, "text": text}
    if user_id is not None:
        message["from"] = {"id": user_id, "is_bot": False, "first_name": "Someone"}
    return {"update_id": 1, "message": message}


@pytest.fixture
def dispatched(monkeypatch):
    """Replace the three side-effecting entry points and record calls."""
    calls = {"command": [], "chat": [], "sent": []}

    async def fake_command(command, args):
        calls["command"].append((command, args))
        return "ok"

    async def fake_chat(text, chat_id):
        calls["chat"].append((text, chat_id))

    async def fake_send(text, chat_id=None, **kwargs):
        calls["sent"].append((text, chat_id))
        return {"success": True}

    monkeypatch.setattr(telegram, "handle_ceo_command", fake_command)
    monkeypatch.setattr(telegram, "handle_ceo_chat", fake_chat)
    monkeypatch.setattr(telegram, "send_telegram_message", fake_send)
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "test-token", raising=False)
    return calls


def configure(monkeypatch, chat_ids, user_ids=None):
    """Set the chat allowlist (and optional sender allowlist) for the guard."""
    monkeypatch.setattr(telegram.settings, "TELEGRAM_CHAT_ID", chat_ids)
    monkeypatch.setattr(telegram, "TELEGRAM_CHAT_ID", chat_ids)
    monkeypatch.setattr(
        telegram.settings, "TELEGRAM_ALLOWED_USER_IDS", user_ids or "", raising=False
    )
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)


@pytest.fixture
def client(dispatched):
    app = FastAPI()
    app.include_router(telegram.router)
    return TestClient(app)


def post(client, body):
    return client.post("/telegram/webhook", json=body)


# --------------------------------------------------------------------------
# 1. Authorised chat passes
# --------------------------------------------------------------------------
def test_authorised_chat_command_runs(monkeypatch, client, dispatched):
    configure(monkeypatch, str(OWNER_CHAT))
    resp = post(client, make_message(OWNER_CHAT, "/approve abc123"))
    assert resp.status_code == 200
    assert resp.json()["authorized"] is True
    assert dispatched["command"] == [("/approve", ["abc123"])]
    assert dispatched["sent"] == [("ok", str(OWNER_CHAT))]


def test_authorised_chat_free_text_reaches_ceo(monkeypatch, client, dispatched):
    configure(monkeypatch, str(OWNER_CHAT))
    resp = post(client, make_message(OWNER_CHAT, "how are leads"))
    assert resp.json()["authorized"] is True
    assert dispatched["chat"] == [("how are leads", str(OWNER_CHAT))]
    assert dispatched["command"] == []


# --------------------------------------------------------------------------
# 2. Unauthorised chat is rejected
# --------------------------------------------------------------------------
def test_unauthorised_chat_is_rejected(monkeypatch, client, dispatched):
    configure(monkeypatch, str(OWNER_CHAT))
    resp = post(client, make_message(OTHER_CHAT, "/approve abc123", user_id=OWNER_USER))
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorized"] is False
    assert body["handled"] is False
    # Nothing executed, nothing sent back into the unauthorised chat.
    assert dispatched == {"command": [], "chat": [], "sent": []}


def test_rejection_response_is_non_leaky(monkeypatch, client, dispatched):
    configure(monkeypatch, str(OWNER_CHAT))
    raw = post(client, make_message(OTHER_CHAT, "/finance")).text
    # No configured chat id, no token, no internals echoed to the caller.
    assert str(OWNER_CHAT) not in raw
    assert "test-token" not in raw
    assert "Traceback" not in raw
    assert "unauthorised" not in raw.lower() and "denied" not in raw.lower()


def test_rejection_is_logged_with_context(monkeypatch, client, dispatched, caplog):
    configure(monkeypatch, str(OWNER_CHAT))
    with caplog.at_level("WARNING", logger="admin.comm.telegram"):
        post(client, make_message(OTHER_CHAT, "/finance", user_id=OTHER_USER))
    text = caplog.text
    assert str(OTHER_CHAT) in text and str(OTHER_USER) in text
    assert "DENIED" in text
    assert "/finance" not in text  # message contents stay out of the log


def test_missing_chat_field_is_rejected(monkeypatch, client, dispatched):
    configure(monkeypatch, str(OWNER_CHAT))
    body = {"update_id": 1, "message": {"text": "/approve abc"}}
    assert post(client, body).json()["authorized"] is False
    assert dispatched["command"] == []


# --------------------------------------------------------------------------
# 3. Unset / blank chat id fails closed, loudly, without killing the route
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["", "   ", ",,,"])
def test_unset_chat_id_fails_closed(monkeypatch, client, dispatched, value):
    configure(monkeypatch, value)
    resp = post(client, make_message(OWNER_CHAT, "/approve abc123"))
    # Route still answers, Telegram does not retry, but nothing runs.
    assert resp.status_code == 200
    assert resp.json()["authorized"] is False
    assert dispatched["command"] == []
    assert dispatched["chat"] == []
    assert dispatched["sent"] == []


def test_unset_chat_id_is_logged_at_error(monkeypatch, client, dispatched, caplog):
    configure(monkeypatch, "")
    with caplog.at_level("ERROR", logger="admin.comm.telegram"):
        post(client, make_message(OWNER_CHAT, "/approve abc123"))
    assert "TELEGRAM_CHAT_ID is not configured" in caplog.text
    assert "failing closed" in caplog.text


# --------------------------------------------------------------------------
# 4. Multiple chat ids
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["111,222", "111, 222", "111;222", "111\n222", " 111 , 222 "])
def test_multiple_chat_ids_all_authorised(monkeypatch, client, dispatched, value):
    configure(monkeypatch, value)
    assert post(client, make_message(222, "/status")).json()["authorized"] is True
    assert post(client, make_message(111, "/status")).json()["authorized"] is True
    assert len(dispatched["command"]) == 2
    assert post(client, make_message(333, "/status")).json()["authorized"] is False
    assert len(dispatched["command"]) == 2


def test_chat_id_as_list(monkeypatch, client, dispatched):
    configure(monkeypatch, [111, 222])
    assert post(client, make_message(222, "/status")).json()["authorized"] is True
    assert post(client, make_message(333, "/status")).json()["authorized"] is False


def test_invalid_entry_is_ignored_and_logged(monkeypatch, client, dispatched, caplog):
    configure(monkeypatch, "111, @somechannel, 222")
    with caplog.at_level("ERROR", logger="admin.comm.telegram"):
        assert post(client, make_message(222, "/status")).json()["authorized"] is True
    assert "ignoring 1 non numeric allowlist entry" in caplog.text
    assert post(client, make_message(333, "/status")).json()["authorized"] is False


# --------------------------------------------------------------------------
# 5. Group / supergroup: chat id is the gate, sender is the optional extra
# --------------------------------------------------------------------------
def test_group_chat_matches_on_chat_id(monkeypatch, client, dispatched):
    configure(monkeypatch, str(OTHER_CHAT))
    body = make_message(OTHER_CHAT, "/status", user_id=OWNER_USER)
    body["message"]["chat"]["type"] = "supergroup"
    assert post(client, body).json()["authorized"] is True
    assert dispatched["command"] == [("/status", [])]


def test_sender_allowlist_rejects_other_group_member(monkeypatch, client, dispatched):
    configure(monkeypatch, str(OTHER_CHAT), user_ids=str(OWNER_USER))
    body = make_message(OTHER_CHAT, "/approve abc", user_id=OTHER_USER)
    body["message"]["chat"]["type"] = "supergroup"
    assert post(client, body).json()["authorized"] is False
    assert dispatched["command"] == []

    ok = make_message(OTHER_CHAT, "/approve abc", user_id=OWNER_USER)
    ok["message"]["chat"]["type"] = "supergroup"
    assert post(client, ok).json()["authorized"] is True
    assert dispatched["command"] == [("/approve", ["abc"])]


def test_sender_allowlist_from_env(monkeypatch, client, dispatched):
    configure(monkeypatch, str(OWNER_CHAT))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", f"{OWNER_USER}")
    assert post(client, make_message(OWNER_CHAT, "/status", user_id=OWNER_USER)).json()[
        "authorized"
    ] is True
    assert post(client, make_message(OWNER_CHAT, "/status", user_id=OTHER_USER)).json()[
        "authorized"
    ] is False


# --------------------------------------------------------------------------
# 6. Channel post / anonymous admin: no `from` field
# --------------------------------------------------------------------------
def test_missing_from_denied_when_sender_allowlist_configured(
    monkeypatch, client, dispatched
):
    configure(monkeypatch, str(OWNER_CHAT), user_ids=str(OWNER_USER))
    body = make_message(OWNER_CHAT, "/approve abc", user_id=None)
    body["message"]["sender_chat"] = {"id": OWNER_CHAT}
    resp = post(client, body)
    assert resp.json()["authorized"] is False
    assert dispatched["command"] == []


def test_missing_from_allowed_without_sender_allowlist(monkeypatch, client, dispatched):
    # The chat allowlist is the whole trust boundary, same as any other message.
    configure(monkeypatch, str(OWNER_CHAT))
    body = make_message(OWNER_CHAT, "/status", user_id=None)
    assert post(client, body).json()["authorized"] is True
    assert dispatched["command"] == [("/status", [])]


# --------------------------------------------------------------------------
# 7. Structural: one ingress route, one dispatcher, handlers only reachable
#    through the guarded funnel.
# --------------------------------------------------------------------------
def test_router_exposes_only_the_webhook_route():
    routes = [(r.path, sorted(getattr(r, "methods", []))) for r in telegram.router.routes]
    assert routes == [("/telegram/webhook", ["POST"])]


def test_webhook_route_delegates_to_the_funnel():
    assert "process_telegram_update" in inspect.getsource(telegram.telegram_webhook)


def test_handlers_are_only_called_from_the_guarded_funnel():
    """Walk real call sites (not text) so docstrings cannot fool the check."""
    tree = ast.parse(inspect.getsource(telegram))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for handler in ("handle_ceo_command", "handle_ceo_chat"):
        callers = set()
        for name, node in functions.items():
            if name == handler:
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == handler
                ):
                    callers.add(name)
        assert callers == {"process_telegram_update"}, (
            f"{handler} is called from {callers}, not only the guarded funnel"
        )


def test_funnel_authorizes_before_dispatching():
    src = inspect.getsource(telegram.process_telegram_update)
    assert src.index("authorize_update(") < src.index("handle_ceo_command(")
    assert src.index("authorize_update(") < src.index("handle_ceo_chat(")


# --------------------------------------------------------------------------
# 8. Guard is also usable directly, and never raises on junk input
# --------------------------------------------------------------------------
def test_guard_returns_decision_without_raising():
    telegram.settings.TELEGRAM_CHAT_ID = "123"
    try:
        for junk in ({}, {"chat": {}}, {"chat": {"id": None}}, {"chat": {"id": "abc"}}):
            decision = telegram.authorize_update(junk)
            assert decision.allowed is False
            assert decision.reason == telegram.AUTH_CHAT_NOT_ALLOWED
    finally:
        telegram.settings.TELEGRAM_CHAT_ID = ""


def test_bot_token_is_never_logged(monkeypatch, client, dispatched, caplog):
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "SUPERSECRETTOKEN", raising=False)
    monkeypatch.setattr(telegram, "TELEGRAM_API_URL", "https://api.telegram.org/botSUPERSECRETTOKEN")
    configure(monkeypatch, str(OWNER_CHAT))
    with caplog.at_level("INFO", logger="admin.comm.telegram"):
        post(client, make_message(OWNER_CHAT, "/approve secretpayload"))
    assert "SUPERSECRETTOKEN" not in caplog.text
    assert "secretpayload" not in caplog.text


def test_asyncio_marker_present():
    # Guards against a test silently becoming a no-op coroutine.
    assert asyncio.iscoroutinefunction(telegram.process_telegram_update)