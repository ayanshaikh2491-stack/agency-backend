"""The backup tag could not detect a wrong key.

`_encrypt` stored sha256(ciphertext) as the tag. That proves the ciphertext was
not altered in transit and says nothing about whether the key is right, so
decrypting under the wrong key produced plausible-looking noise: correct magic,
sensible length, random bytes. Restoring one of those would overwrite a good
database with garbage.

That exact failure cost a full day on 2026-10-07 before it turned out the real
cause was a truncated key in the verifier. The tag should have caught it
instantly. These tests pin that it now does.
"""
import importlib.util
import os
import sqlite3
import tempfile

import pytest

GOOD_KEY = "a" * 64
OTHER_KEY = "b" * 64


@pytest.fixture
def dbb(monkeypatch):
    """Load db_backup with a known key."""
    monkeypatch.setenv("ENCRYPTION_KEY", GOOD_KEY)
    monkeypatch.setenv("GH_BACKUP_TOKEN", "token")
    spec = importlib.util.spec_from_file_location(
        "db_backup_under_test", "admin/db_backup.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _sqlite_file(rows=3):
    path = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE leads(id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(rows):
        con.execute("INSERT INTO leads(name) VALUES (?)", (f"clinic {i}",))
    con.commit()
    con.close()
    with open(path, "rb") as f:
        raw = f.read()
    os.remove(path)
    return raw


def test_roundtrip_is_exact(dbb):
    raw = _sqlite_file()
    assert dbb._decrypt(dbb._encrypt(raw)) == raw


def test_roundtrip_survives_a_large_database(dbb):
    """The freeapi.db snapshot is over 1 MB. Small inputs hiding a bug is how
    the old cipher passed every local test while failing in production."""
    raw = _sqlite_file(rows=60000)
    assert len(raw) > 1_000_000
    assert dbb._decrypt(dbb._encrypt(raw)) == raw


def test_wrong_key_raises_instead_of_returning_noise(dbb, monkeypatch):
    """This is the whole point of the change."""
    raw = _sqlite_file()
    blob = dbb._encrypt(raw)

    monkeypatch.setenv("ENCRYPTION_KEY", OTHER_KEY)
    with pytest.raises(ValueError):
        plain = dbb._decrypt(blob)  # noqa: F841

    # And the failure must be loud, not a plausible-looking wrong answer.
    monkeypatch.setenv("ENCRYPTION_KEY", OTHER_KEY)
    try:
        got = dbb._decrypt(blob)
    except ValueError:
        got = None
    assert got is None, "a wrong key must never return plaintext"


def test_corrupted_body_is_detected(dbb):
    raw = _sqlite_file()
    blob = bytearray(dbb._encrypt(raw))
    blob[100] ^= 0xFF
    with pytest.raises(ValueError):
        dbb._decrypt(bytes(blob))


def test_truncated_blob_is_detected(dbb):
    raw = _sqlite_file()
    blob = dbb._encrypt(raw)
    with pytest.raises(ValueError):
        dbb._decrypt(blob[:len(blob) // 2])


def test_current_format_marker_is_versioned(dbb):
    assert dbb._MAGIC == b"AGDB2"
    assert dbb._encrypt(b"x").startswith(dbb._MAGIC)


def test_agdb1_snapshot_is_refused_not_guessed(dbb):
    """An old snapshot must be rejected explicitly, never half-restored."""
    raw = _sqlite_file()
    import hashlib

    legacy = (dbb._MAGIC_V1 + bytes(range(16))
              + hashlib.sha256(dbb._encrypt(raw)[37:]).digest()[:16]
              + dbb._encrypt(raw)[37:])
    with pytest.raises(dbb.LegacySnapshot):
        dbb._decrypt(legacy)


def test_legacy_is_a_valueerror_so_existing_handlers_still_work(dbb):
    """restore() catches ValueError around _decrypt. A new exception class that
    did not inherit from it would turn a clean skip into an unhandled crash."""
    assert issubclass(dbb.LegacySnapshot, ValueError)


def test_plaintext_blob_still_passes_through(dbb):
    """A snapshot from before encryption existed has no magic header."""
    assert dbb._decrypt(b"SQLite format 3\x00raw") is None


def test_same_plaintext_encrypts_differently_each_time(dbb):
    """A fresh nonce per write, so identical databases do not look identical on
    the branch and a snapshot cannot be recognised by its ciphertext."""
    raw = _sqlite_file()
    assert dbb._encrypt(raw) != dbb._encrypt(raw)
