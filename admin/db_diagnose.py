"""Print exactly what the backup code sees on this machine.

Run inside the container:  python -m admin.db_diagnose

The branch snapshots are not SQLite files, but the encryption round-trips
exactly, which means the file being read is already not a database. This dumps
the ground truth so the question stops being a theory: does the path exist, is it
a file, what is its header, and what does sqlite think of it.

The output goes to stdout, so it is visible in the Render container log next to
the [db_backup] lines.
"""
import glob
import os
import sqlite3

from admin import db_backup


def _head(path: str, n: int = 32) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except Exception as e:
        return f"<unreadable: {e}>".encode()[:n]


def main() -> None:
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
