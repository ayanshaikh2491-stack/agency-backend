#!/usr/bin/env python3
"""Standalone Cloudflare KV backup/restore for the workspace SQLite database.

Designed to be called from start.sh:
  - 'restore' — download latest backup from KV before the app starts
  - 'backup'  — upload current DB to KV (called on shutdown via trap)

Uses only Python stdlib (urllib, base64) to avoid any import issues in the
Docker image. Never raises — failures are logged and skipped.
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

KV_NAMESPACE_ID = "5b16c98175e44680be0cf35f1be65e8f"
KV_ACCOUNT_ID = "44f94d3a0d718f3192a26fe49401bdd9"
KV_KEY = "workspace_backup.db"
# Default path matches what persistence.py uses in the Docker container
DB_PATH = os.getenv("WORKSPACE_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tags_agency_workspace.db"))


def _headers():
    token = os.getenv("CF_KV_TOKEN")
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _url():
    return (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{KV_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{KV_KEY}"
    )


def restore():
    """Download DB from KV to local file. Returns True on success."""
    headers = _headers()
    if not headers:
        print("[kv_backup] CF_KV_TOKEN not set — skipping restore")
        return False
    if not os.path.exists(DB_PATH):
        print("[kv_backup] WARNING: DB file does not exist on restore target")
    try:
        req = urllib.request.Request(_url(), headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status == 200:
                d = json.loads(r.read().decode())
                b64 = d.get("value") or d.get("result", {}).get("value")
                if b64:
                    data = base64.b64decode(b64)
                    # Write only if data is valid (> 100 bytes — a real SQLite file)
                    if len(data) > 100:
                        with open(DB_PATH, "wb") as f:
                            f.write(data)
                        print(f"[kv_backup] Restored DB from KV ({len(data)} bytes)")
                        return True
                    else:
                        print(f"[kv_backup] KV value too small ({len(data)} bytes), skipping")
                        return False
                else:
                    print("[kv_backup] No value in KV response, skipping restore")
                    return False
            elif r.status == 404:
                print("[kv_backup] No backup in KV yet, starting fresh")
                return False
            else:
                print(f"[kv_backup] KV GET returned {r.status}, skipping restore")
                return False
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[kv_backup] No backup in KV yet, starting fresh")
            return False
        print(f"[kv_backup] KV download failed: HTTP {e.code} — {e.read().decode()[:200]}")
        return False
    except Exception as exc:
        print(f"[kv_backup] Restore error (non-fatal): {exc}")
        return False


def backup():
    """Upload local DB to KV. Returns True on success."""
    headers = _headers()
    if not headers:
        print("[kv_backup] CF_KV_TOKEN not set — skipping backup")
        return False
    if not os.path.exists(DB_PATH):
        print("[kv_backup] DB file does not exist — skipping backup")
        return False
    try:
        # Checkpoint the WAL first. Without this the main .db file on disk is
        # missing every write since the last checkpoint, so the snapshot would
        # silently lag behind the live database by minutes. This is the same
        # reason a bare "cp *.db" backup is not a backup.
        try:
            import sqlite3

            conn = sqlite3.connect(DB_PATH, timeout=10)
            try:
                conn.execute("PRAGMA wal_checkpoint(FULL)")
            finally:
                conn.close()
        except Exception as exc:
            print(f"[kv_backup] WAL checkpoint skipped: {exc}")

        data = open(DB_PATH, "rb").read()
        if len(data) < 100:
            print(f"[kv_backup] DB file too small ({len(data)} bytes), skipping backup")
            return False
        b64 = base64.b64encode(data).decode()
        body = json.dumps({"value": b64, "metadata": {"size": len(data)}}).encode()
        req = urllib.request.Request(_url(), data=body, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status == 200:
                print(f"[kv_backup] Uploaded DB to KV ({len(data)} bytes)")
                return True
            else:
                print(f"[kv_backup] KV upload returned {r.status}")
                return False
    except Exception as exc:
        print(f"[kv_backup] Backup error (non-fatal): {exc}")
        return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backup"
    if cmd == "restore":
        sys.exit(0 if restore() else 0)  # always exit 0 — non-fatal
    elif cmd == "backup":
        sys.exit(0 if backup() else 0)
    else:
        print(f"[kv_backup] Unknown command: {cmd}. Use 'restore' or 'backup'.")
        sys.exit(0)
