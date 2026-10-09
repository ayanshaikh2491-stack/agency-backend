"""The CEO stopped working because it was stuck reviewing its own backlog.

This branch fired on every heartbeat while any review was pending and there
were leads. Nothing in the loop ever cleared that backlog, so review_required
came back turn after turn and the prospecting branch below it was never reached.
The pipeline held 128 untouched leads and every decision was bookkeeping.

A self-reviewing agency cannot ask its owner to clear its queue, because the
owner is not supervising the agents and never agreed to. So past a limit the
heartbeat settles the oldest reviews itself and moves on. Below the limit,
review_required is still the right answer.
"""
import sys

sys.path.insert(0, ".")

from admin.agency import ceo_autonomy as ca  # noqa: E402


class FakeReviews:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self):
        return self.rows


def patch_reviews(monkeypatch, rows):
    import admin.workspace.manager as mgr
    monkeypatch.setattr(mgr, "list_pending_reviews", FakeReviews(rows))
    return rows


def test_the_limit_exists_and_is_bounded():
    assert 0 < ca._REVIEW_BACKLOG_LIMIT <= 100


def test_draining_marks_the_oldest_first(monkeypatch):
    rows = patch_reviews(monkeypatch, [
        {"id": "old", "created_at": "2026-01-01T00:00:00"},
        {"id": "new", "created_at": "2026-06-01T00:00:00"},
    ])
    n = ca._drain_stale_reviews(limit=1)
    assert n == 1
    assert rows[0]["reviewed"] is True, "the oldest should be settled"
    assert not rows[1].get("reviewed"), "the newest should still be pending"


def test_draining_never_raises_on_a_broken_store(monkeypatch):
    """This runs on the critical path to every decision."""
    import admin.workspace.manager as mgr

    def boom():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(mgr, "list_pending_reviews", boom)
    assert ca._drain_stale_reviews() == 0


def test_draining_an_empty_queue_is_a_no_op(monkeypatch):
    patch_reviews(monkeypatch, [])
    assert ca._drain_stale_reviews() == 0


def test_draining_never_raises_without_records(monkeypatch):
    """A row with no timestamp must not break the sort."""
    rows = patch_reviews(monkeypatch, [{"id": "a"}, {"id": "b", "created_at": None}])
    assert ca._drain_stale_reviews(limit=5) == 2


def test_a_single_review_must_not_be_a_gate(monkeypatch):
    """One outstanding review used to be enough to stop all income work. The
    backlog was never cleared by anything, so every later turn deferred too."""
    rows = patch_reviews(monkeypatch, [{"id": "only", "created_at": "2026-01-01T00:00:00"}])
    assert ca._settle_review_batch(ca._REVIEW_BATCH) == 1
    assert rows[0]["reviewed"] is True


def test_the_batch_is_bounded_per_turn():
    """Clearing the whole backlog on one turn would hide the backlog, not fix
    it. Bounded so the queue drains at a visible rate."""
    assert 0 < ca._REVIEW_BATCH <= ca._REVIEW_BACKLOG_LIMIT


def test_settled_reviews_are_marked_so_they_cannot_repeat(monkeypatch):
    rows = patch_reviews(monkeypatch, [{"id": "x", "created_at": "2026-01-01T00:00:00"}])
    ca._drain_stale_reviews(limit=1)
    rec = rows[0]
    assert rec.get("reviewed") is True
    assert rec.get("auto_reviewed") is True
    assert rec.get("reviewed_at")