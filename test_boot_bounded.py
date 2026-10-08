"""Boot must not be blocked by backup restore.

Every deploy was failing with update_failed. The image built, then the container
started and never bound its port, so Render's health check on /api/health gave
up. The step that runs before the port opens is the restore from the backup
branch, and it makes several GitHub API calls in sequence, each with its own
120 second timeout. When GitHub is slow or rate-limiting the service IP, that
adds up to minutes inside a step that runs before the API is reachable at all.

An empty database is recoverable. A container that never binds its port is not:
no Telegram, no agents, no health check. So each pre-boot network step is now
bounded by `timeout` in start.sh and the boot continues regardless.

These read start.sh because the bound lives there, not in Python. A test that
asserted on a Python function would pass while the shell still blocked forever.
"""
import os
import re
import sys

sys.path.insert(0, ".")

START_SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start.sh")


def text() -> str:
    with open(START_SH, encoding="utf-8") as f:
        return f.read()


def test_restore_is_time_bounded():
    src = text()
    assert re.search(r"timeout\s+\"?\$\{DB_RESTORE_TIMEOUT", src), \
        "db restore runs unbounded before the port opens"


def test_diagnose_is_time_bounded():
    src = text()
    assert re.search(r"timeout\s+\"?\$\{DB_DIAGNOSE_TIMEOUT", src)


def test_kv_restore_is_time_bounded():
    src = text()
    assert re.search(r"timeout\s+\"?\$\{KV_RESTORE_TIMEOUT", src)


def test_a_timeout_does_not_abort_the_boot():
    """The point of the bound: boot continues, not that restore succeeds."""
    src = text()
    for var in ("DB_RESTORE_TIMEOUT", "DB_DIAGNOSE_TIMEOUT", "KV_RESTORE_TIMEOUT"):
        # Every bounded step must be followed by a fallback that keeps going.
        idx = src.find(var)
        assert idx != -1, var
        tail = src[idx:idx + 400]
        assert "boot continues" in tail, f"{var} has no continue-on-failure path"


def test_boot_still_opens_the_port():
    """A bound that stopped the script would be worse than no bound."""
    src = text()
    assert "python app.py" in src
    assert "/api/health" in src


def test_the_bound_is_configurable_and_has_a_default():
    src = text()
    assert "DB_RESTORE_TIMEOUT:-" in src, "restore bound needs a default"
    assert "DB_RESTORE_TIMEOUT" in src and "120" in src