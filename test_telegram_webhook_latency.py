"""Why the webhook must not await the CEO.

Telegram gives a webhook about 10 seconds. A CEO reply is an LLM round trip and
routinely takes longer, so awaiting it inline made Telegram treat every message
as failed and redeliver it. These tests pin the ordering so the regression
cannot come back quietly.
"""
import asyncio
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.comm import telegram


@pytest.fixture
def client(monkeypatch):
    async def slow_process(body):
        # Stand-in for a real CEO round trip: far longer than Telegram waits.
        await asyncio.sleep(1.5)
        return {"ok": True, "authorized": True, "handled": True}

    monkeypatch.setattr(telegram, "process_telegram_update", slow_process)
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "test-token", raising=False)

    app = FastAPI()
    app.include_router(telegram.router)
    return TestClient(app)


def _send(client):
    return client.post("/telegram/webhook", json={
        "update_id": 1,
        "message": {"message_id": 2, "chat": {"id": 5412605117},
                    "from": {"id": 5412605117}, "text": "hello"},
    })


def test_webhook_returns_before_the_ceo_thinks(client):
    """The response must arrive while the handler is still working.

    If this ever fails again the fix was reverted somewhere. The 1.5 s inside
    slow_process is far below a real reply but far above Telegram's patience.
    """
    loop = asyncio.new_event_loop()
    try:
        started = loop.time()
        client.portal = None
        # Run the route on the loop the TestClient will share.
        result = _run_on_loop(client, loop)
        elapsed = loop.time() - started
    finally:
        loop.close()

    assert result.status_code == 200
    # Authorization is answered synchronously (it is pure id comparison), the
    # CEO work is not. `handled` is False because the work has not run yet.
    assert result.json() == {"ok": True, "authorized": True, "handled": False}
    assert elapsed < 1.0, (
        f"webhook took {elapsed:.2f}s; it awaited the CEO instead of "
        "acknowledging immediately"
    )


def _run_on_loop(client, loop):
    """Issue the request and let the background task finish afterwards."""
    import threading

    box = {}

    def worker():
        asyncio.set_event_loop(loop)
        box["response"] = _send(client)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=20)
    # Give the detached task a moment to complete so it cannot leak into the
    # next test, but never block on it.
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)
    return box["response"]


def test_background_task_is_held_by_a_strong_reference(client):
    """A fire-and-forget task with no reference can be collected mid-flight.

    That failure is silent: the reply just never arrives. Pin the reference set.
    """
    src = inspect.getsource(telegram._dispatch_in_background)
    assert "_PENDING.add(task)" in src, "in-flight tasks must be strongly referenced"
    assert "_PENDING.discard" in src, "references must be released when done"


def test_detached_errors_still_reach_the_user(monkeypatch):
    """Detached work has no route-level try/except any more.

    process_telegram_update failing must still send the user something, rather
    than vanishing into the event loop's exception handler. Driven directly
    rather than through the client, because a fire-and-forget task races the
    assertion otherwise.
    """
    async def boom(body):
        raise RuntimeError("gateway timeout")

    sent = []

    async def fake_send(text, chat_id=None):
        sent.append((text, chat_id))
        return {"ok": True}

    monkeypatch.setattr(telegram, "process_telegram_update", boom)
    monkeypatch.setattr(telegram, "send_telegram_message", fake_send)

    asyncio.run(telegram._guarded_process({
        "message": {"chat": {"id": 777}, "text": "hi"}}))

    assert sent, "an error reply must be delivered, not dropped"
    assert "gateway timeout" in sent[0][0]
    assert sent[0][1] == "777"


def test_error_without_a_chat_id_does_not_raise(monkeypatch):
    """A malformed update must not turn into a second, louder crash."""
    async def boom(body):
        raise RuntimeError("gateway timeout")

    async def fake_send(text, chat_id=None):
        raise AssertionError("should not try to send without a chat id")

    monkeypatch.setattr(telegram, "process_telegram_update", boom)
    monkeypatch.setattr(telegram, "send_telegram_message", fake_send)

    asyncio.run(telegram._guarded_process({"message": {"chat": {}}}))
