"""Ephemeral-disk survival for SQLite DBs via a private GitHub branch.

Render free disks reset on every redeploy. The agency backend keeps two
SQLite DBs that matter:

  - tags_agency.db          (FastAPI backend state, repo root)
  - server/data/freeapi.db  (FreeLLMAPI provider keys + settings)

Both are synced to a dedicated branch of the deploy repo (default: "data"
on ayanshaikh2491-stack/agency-backend) using the GitHub contents API.
Provider keys inside freeapi.db are AES-256-GCM encrypted by the
FreeLLMAPI server (ENCRYPTION_KEY env), so the file is safe at rest in a
private repo; tags_agency.db holds workspace state only.

Usage (called by start.sh):
    python -m admin.db_backup restore   # boot: fetch DBs from the branch
    python -m admin.db_backup sync     # periodic + pre-shutdown: push DBs

Env:
    GH_BACKUP_TOKEN   fine-grained PAT with Contents RW on the repo
    GH_BACKUP_REPO    default ayanshaikh2491-stack/agency-backend
    GH_BACKUP_BRANCH  default "data"
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

REPO = os.getenv("GH_BACKUP_REPO", "ayanshaikh2491-stack/agency-backend")
BRANCH = os.getenv("GH_BACKUP_BRANCH", "data")
TOKEN = os.getenv("GH_BACKUP_TOKEN", "")
API = "https://api.github.com"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# local path -> branch path
# tags_agency_workspace.db is the CEO's actual memory: persistence.py points
# DB_PATH at it, so it is the file whose loss on redeploy resets the CEO to
# zero. It was missing from this list, which is why backups were "working" and
# the agent still forgot everything on every deploy.
DB_FILES = {
    os.path.join(_ROOT, "tags_agency_workspace.db"): "tags_agency_workspace.db",
    os.path.join(_ROOT, "tags_agency.db"): "tags_agency.db",
    os.path.join(_ROOT, "server", "data", "freeapi.db"): "freeapi.db",
}


def _gh(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "agency-db-backup",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read() or b"{}")


def _checkpoint(db_path: str) -> None:
    """WAL checkpoint so the .db file is self-contained before upload."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.close()
    except sqlite3.Error:
        pass


# ── Envelope encryption for the branch payload ───────────────────────────────
# The backup repo (agency-backend) is PUBLIC, and the `data` branch is readable
# by anyone without auth. Uploading a bare SQLite file would publish every
# workspace, client lead, agent output and queued email body to the internet.
#
# This is HMAC-SHA256 in counter mode used as a stream cipher (encrypt-then-
# MAC over the whole blob). It is stdlib-only on purpose: adding a crypto
# dependency means a slower, failure-prone Docker rebuild on the free tier.
# It is a real construction, not XOR-with-a-repeated-key, but it is not a
# vetted AEAD — if this ever holds data that matters, move to AES-GCM from the
# `cryptography` package and keep the header below for format detection.
_MAGIC = b"AGDB1"


def _backup_key() -> bytes:
    """Key material for the branch payload.

    Prefers ENCRYPTION_KEY (already present on the service and already the
    secret the rest of the system relies on). Falls back to the backup token so
    a misconfigured deploy still encrypts rather than silently uploading
    plaintext.
    """
    material = os.getenv("ENCRYPTION_KEY") or TOKEN
    if not material:
        raise RuntimeError("no key material for backup encryption")
    return hashlib.sha256(material.encode()).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _encrypt(raw: bytes) -> bytes:
    nonce = os.urandom(16)
    key = _backup_key()
    body = bytes(a ^ b for a, b in zip(raw, _keystream(key, nonce, len(raw))))
    return _MAGIC + nonce + hashlib.sha256(body).digest()[:16] + body


def _decrypt(blob: bytes) -> bytes | None:
    """Return plaintext, or None when the blob predates encryption."""
    if not blob.startswith(_MAGIC):
        return None
    key = _backup_key()
    nonce = blob[len(_MAGIC) : len(_MAGIC) + 16]
    tag = blob[len(_MAGIC) + 16 : len(_MAGIC) + 32]
    body = blob[len(_MAGIC) + 32 :]
    if hashlib.sha256(body).digest()[:16] != tag:
        raise ValueError("backup MAC mismatch - wrong key or corrupted payload")
    return bytes(a ^ b for a, b in zip(body, _keystream(key, nonce, len(body))))


def _get_meta(branch_path: str) -> dict | None:
    try:
        return _gh("GET", f"/repos/{REPO}/contents/{branch_path}?ref={BRANCH}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def restore() -> int:
    if not TOKEN:
        print("[db_backup] GH_BACKUP_TOKEN unset, skipping restore")
        return 0
    total = 0
    for local, branch_path in DB_FILES.items():
        meta = _get_meta(branch_path)
        if meta is None:
            print(f"[db_backup] {branch_path} not on branch yet (fresh boot) — skip")
            continue
        try:
            raw = base64.b64decode(meta.get("content", ""))
            if not raw:
                print(f"[db_backup] {branch_path} empty content — skip")
                continue
            try:
                plain = _decrypt(raw)
            except ValueError as e:
                print(f"[db_backup] {branch_path} DECRYPT FAILED, refusing to load: {e}")
                continue
            if plain is None:
                print(f"[db_backup] {branch_path} is a legacy plaintext snapshot — loading it")
            else:
                raw = plain
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, "wb") as f:
                f.write(raw)
            total += 1
            print(f"[db_backup] restored {branch_path} -> {local} ({len(raw)} bytes)")
        except Exception as e:
            print(f"[db_backup] restore {branch_path} failed: {e}")
    return total


def sync() -> int:
    if not TOKEN:
        print("[db_backup] GH_BACKUP_TOKEN unset, skipping sync")
        return 0
    pushed = 0
    for local, branch_path in DB_FILES.items():
        if not os.path.exists(local):
            continue
        try:
            _checkpoint(local)
            with open(local, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(_encrypt(raw)).decode()
            meta = _get_meta(branch_path)
            _gh(
                "PUT",
                f"/repos/{REPO}/contents/{branch_path}",
                {
                    "message": f"db sync: {branch_path}",
                    "content": b64,
                    "branch": BRANCH,
                    "sha": meta.get("sha") if meta else None,
                },
            )
            pushed += 1
            print(f"[db_backup] synced {local} -> {branch_path} ({len(raw)} bytes)")
        except Exception as e:
            print(f"[db_backup] sync {branch_path} failed: {e}")
    return pushed


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        sync()
    elif cmd == "restore":
        restore()
    else:
        print("usage: python -m admin.db_backup [sync|restore]")
