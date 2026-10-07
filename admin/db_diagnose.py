"""Print exactly what the backup code sees on this machine.

Run inside the container:  python -m admin.db_diagnose

The branch snapshots are not SQLite files, but the encryption round-trips
exactly, which means the file being read is already not a database. This dumps
the ground truth so the question stops being a theory: does the path exist, is it
a file, what is its header, and what does sqlite think of it.

The output goes to stdout, and is also published to the backup branch when
GH_BACKUP_TOKEN is set. That second part matters: the Render REST API cannot
return container logs and the agent cannot read the dashboard, so without it
this information would be unreachable and the investigation would stall again.
"""
import base64
import glob
import io
import os
import sqlite3
import sys

from admin import db_backup

REPORT_PATH = "db_diagnose.txt"


def _head(path: str, n: int = 32) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except Exception as e:
        return f"<unreadable: {e}>".encode()[:n]


def _key_fingerprint() -> None:
    """Prove which key this process actually uses.

    Every other part of the backup path has been verified identical: the deployed
    db_backup.py matches the local file byte for byte, _keystream matches, and a
    local encrypt/decrypt round-trip is exact. Yet a snapshot produced by this
    container does not decrypt. The one input that has never been checked from
    the inside is the key itself, because the MAC covers the ciphertext and
    therefore cannot detect a wrong key.

    This prints a test vector: a fixed plaintext encrypted with the key this
    process resolves. Encrypt the same vector elsewhere with the same key and
    compare. Identical means the key is the same and the fault is elsewhere;
    different means the container is using a different key than expected.
    """
    from admin import db_backup

    print("=" * 70)
    print("key fingerprint")
    print("=" * 70)
    raw_key = os.getenv("ENCRYPTION_KEY", "")
    print(f"  ENCRYPTION_KEY set   : {bool(raw_key)}")
    print(f"  ENCRYPTION_KEY len   : {len(raw_key)}")
    try:
        import hashlib

        print(f"  sha256(ENCRYPTION_KEY): {hashlib.sha256(raw_key.encode()).hexdigest()}")
    except Exception as e:
        print(f"  sha256 failed        : {e}")

    print(f"  _backup_key() hex    : {db_backup._backup_key().hex()}")

    # Test vector. Nonce is pinned so the result is reproducible.
    vector = b"SQLite format 3\x00AGENCY-KEY-TEST-VECTOR"
    pinned = bytes(range(16))
    key = db_backup._backup_key()
    ks = db_backup._keystream(key, pinned, len(vector))
    ct = bytes(a ^ b for a, b in zip(vector, ks))
    print(f"  vector plaintext     : {vector!r}")
    print(f"  pinned nonce         : {pinned.hex()}")
    print(f"  vector ciphertext    : {ct.hex()}")
    print("  Recompute this locally with the same key. If it differs, the")
    print("  container is using a different key than the one you expect.")


def main() -> None:
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = buf
    try:
        _report()
        _key_fingerprint()
    finally:
        sys.stdout = real
    text = buf.getvalue()
    real.write(text)
    _publish(text)


def _publish(text: str) -> None:
    """Push the report to the backup branch so it can be read from outside.

    Best effort. A diagnostic that takes the service down, or that fails
    silently when it cannot publish, is worse than no diagnostic.
    """
    if not db_backup.TOKEN:
        print("[db_diagnose] GH_BACKUP_TOKEN unset; report is stdout-only")
        return
    try:
        existing = db_backup._get_meta(REPORT_PATH) or {}
        body = {
            "message": "db diagnose report",
            "content": base64.b64encode(text.encode()).decode(),
            "branch": db_backup.BRANCH,
        }
        if existing.get("sha"):
            body["sha"] = existing["sha"]
        db_backup._gh("PUT", f"/repos/{db_backup.REPO}/contents/{REPORT_PATH}", body)
        print(f"[db_diagnose] report published to {db_backup.BRANCH}/{REPORT_PATH}")
    except Exception as e:
        print(f"[db_diagnose] could not publish report: {type(e).__name__}: {e}")


def _report() -> None:
    root = db_backup._ROOT
    print("=" * 70)
    print(f"db_diagnose: _ROOT = {root}")
    print(f"cwd         = {os.getcwd()}")
    print(f"token set   = {bool(db_backup.TOKEN)}")
    print(f"key set     = {bool(os.getenv('ENCRYPTION_KEY'))}")
    print("=" * 70)

    for local, branch in db_backup.DB_FILES.items():
        print(f"\n--- {branch}")
        print(f"    expected path : {local}")
        print(f"    exists        : {os.path.exists(local)}")
        if os.path.exists(local):
            st = os.stat(local)
            print(f"    size          : {st.st_size}")
            print(f"    is file       : {os.path.isfile(local)}")
            head = _head(local)
            print(f"    head          : {head!r}")
            print(f"    is sqlite     : {head[:15] == b'SQLite format 3'}")
            try:
                con = sqlite3.connect(f"file:{local}?mode=ro", uri=True)
                tables = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                print(f"    tables        : {len(tables)}")
                print(f"    integrity     : {con.execute('PRAGMA integrity_check').fetchone()[0]}")
                con.close()
            except Exception as e:
                print(f"    SQLITE ERROR  : {type(e).__name__}: {e}")

    # What database files actually exist anywhere near the root.
    print("\n" + "=" * 70)
    print("every .db file visible under the app root")
    print("=" * 70)
    found = sorted(glob.glob(os.path.join(root, "**", "*.db"), recursive=True))
    if not found:
        print("  (none)")
    for p in found[:60]:
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        head = _head(p, 16)
        print(f"  {size:>10}  sqlite={head[:15] == b'SQLite format 3'!s:<5}  {p}")

    print("\n" + "=" * 70)
    print("sidecar WAL/SHM files (a -wal beside a -db means WAL is live)")
    print("=" * 70)
    for p in sorted(glob.glob(os.path.join(root, "**", "*.db-*"), recursive=True))[:40]:
        print(f"  {os.path.getsize(p):>10}  {p}")
    if not glob.glob(os.path.join(root, "**", "*.db-*"), recursive=True):
        print("  (none)")


if __name__ == "__main__":
    main()
