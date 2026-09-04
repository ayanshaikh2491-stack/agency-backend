"""HF Spaces ephemeral storage survival — multi-provider S3 sync.

Providers (S3-compatible, sab boto3 se):
  Provider 1 (critical data):  R2_SYNC_BUCKET, R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY
  Provider 2..N (artifacts):  SYNC_EXTRA_PROVIDERS="b2,e2" + <NAME>_BUCKET,
                               <NAME>_ENDPOINT, <NAME>_ACCESS_KEY, <NAME>_SECRET_KEY

Routing:
  pb_data + chhote JSON stores -> provider 1 (R2, critical)
  bade dirs (outputs, generated_sites, published_posts, store) -> sab
  providers pe round-robin (hash % len) — ~55GB tak free stack.

Usage:
    python -m admin.hf_sync sync     # pb_data + JSON stores -> providers
    python -m admin.hf_sync restore  # startup pe sab wapas lao
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

PREFIX = os.getenv("HF_SYNC_PREFIX", "agency-os-backup")
PB_DIR = Path(os.getenv("PB_DATA_DIR", "/app/pb_data"))

# JSON file stores jo PB ke sath mirror hote hain (workspace data).
# HF container mein /app/admin/data, local dev mein admin/data — dono chalein.
_CANDIDATE_BASES = [Path("/app"), Path(__file__).resolve().parent.parent]
_EXTRA_DIRS: list[Path] = []
for _base in _CANDIDATE_BASES:
    for _d in ("admin/data", "data"):
        _p = _base / _d
        if _p.is_dir() and _p not in _EXTRA_DIRS:
            _EXTRA_DIRS.append(_p)

# Ye dirs bade artifacts hote hain — multi-provider round-robin routing
_BIG_DIRS = {
    "generated_sites", "outputs", "published_posts", "store",
    "p100_cpu_test", "live_test_p100",
}


# ── Providers ─────────────────────────────────────────────────────────────

def _mk_client(endpoint: str, access_key: str, secret_key: str):
    import boto3  # lazy import: sync ke liye hi chahiye

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _load_providers() -> list[dict]:
    """Env se providers build karo. Provider 1 = R2 (existing vars, critical data)."""
    providers: list[dict] = []

    r2_bucket = os.getenv("R2_SYNC_BUCKET", "")
    if r2_bucket:
        providers.append({
            "name": "r2",
            "bucket": r2_bucket,
            "client": _mk_client(
                os.getenv("R2_ENDPOINT", ""),
                os.getenv("R2_ACCESS_KEY", ""),
                os.getenv("R2_SECRET_KEY", ""),
            ),
        })

    # Extra providers: SYNC_EXTRA_PROVIDERS="b2,e2,storj" (upper-case env vars)
    for name in filter(None, (n.strip() for n in os.getenv("SYNC_EXTRA_PROVIDERS", "").split(","))):
        env = name.upper()
        bucket = os.getenv(f"{env}_BUCKET", "")
        if not bucket:
            continue
        providers.append({
            "name": name,
            "bucket": bucket,
            "client": _mk_client(
                os.getenv(f"{env}_ENDPOINT", ""),
                os.getenv(f"{env}_ACCESS_KEY", ""),
                os.getenv(f"{env}_SECRET_KEY", ""),
            ),
        })

    return providers


def _route(group: str, rel: str, providers: list[dict]) -> dict:
    """Critical data -> provider[0] (R2). Artifacts -> hash-based round-robin."""
    if group == "critical" or len(providers) == 1:
        return providers[0]
    # ponytail: hash routing self-balancing hai; weight-aware jab provider
    # limits track karne padenge (>40GB pe upgrade)
    idx = int(hashlib.sha1(rel.encode()).hexdigest(), 16) % len(providers)
    return providers[idx]


# ── SQLite safety ─────────────────────────────────────────────────────────

def _checkpoint(pb_dir: Path) -> None:
    """SQLite WAL checkpoint — .db file self-contained ban jata hai upload se pehle."""
    for db in pb_dir.glob("*.db"):
        try:
            conn = sqlite3.connect(db)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.close()
        except sqlite3.Error:
            pass


# ── Sync / Restore ────────────────────────────────────────────────────────

def sync() -> int:
    providers = _load_providers()
    if not providers:
        print("[hf_sync] koi provider configured nahi (R2_SYNC_BUCKET unset) — skipping sync")
        return 0

    PB_DIR.mkdir(parents=True, exist_ok=True)
    _checkpoint(PB_DIR)

    counts: dict[str, int] = {p["name"]: 0 for p in providers}

    def upload(provider: dict, local: Path, key: str) -> None:
        provider["client"].upload_file(str(local), provider["bucket"], key)
        counts[provider["name"]] += 1

    # pb_data = critical, hamesha provider 1 (R2)
    for path in PB_DIR.rglob("*"):
        if path.is_file():
            rel = path.relative_to(PB_DIR).as_posix()
            upload(providers[0], path, f"{PREFIX}/pb_data/{rel}")

    # JSON stores: critical chhote + round-robin bade
    for extra in _EXTRA_DIRS:
        if not extra.exists():
            continue
        for path in extra.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(extra).as_posix()
            top = rel.split("/", 1)[0]
            group = "big" if top in _BIG_DIRS else "critical"
            prov = _route(group, rel, providers)
            # key: files/<admin|data>/<rel> — admin/data vs data distinguish
            stem = "admin/data" if extra.parent.name == "admin" else extra.name
            upload(prov, path, f"{PREFIX}/files/{stem}/{rel}")

    print(f"[hf_sync] uploaded: " + ", ".join(f"{n}={c}" for n, c in counts.items()))
    return sum(counts.values())


def restore() -> int:
    providers = _load_providers()
    if not providers:
        print("[hf_sync] koi provider configured nahi — skipping restore")
        return 0

    PB_DIR.mkdir(parents=True, exist_ok=True)
    total = 0

    for prov in providers:
        paginator = prov["client"].get_paginator("list_objects_v2")
        # ponytail: full restore at boot; >1GB pe lazy per-key fetch karna
        # hoga (startup time ceiling ~2-3 min @ 10GB)
        for page in paginator.paginate(Bucket=prov["bucket"], Prefix=f"{PREFIX}/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key.removeprefix(f"{PREFIX}/")

                if rel.startswith("pb_data/"):
                    target = PB_DIR / rel.removeprefix("pb_data/")
                elif rel.startswith("files/"):
                    parts = rel.removeprefix("files/").split("/", 1)
                    if len(parts) != 2:
                        continue
                    # files/admin/data/... ya files/data/... — base dir detect karo
                    sub = parts[1]
                    if parts[0] == "admin" and sub.startswith("data/"):
                        target = Path("/app") / "admin" / sub
                    elif parts[0] == "data":
                        target = Path("/app") / "data" / sub.removeprefix("data/")
                    else:
                        target = Path("/app") / parts[0] / sub
                else:
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    prov["client"].download_fileobj(prov["bucket"], key, tmp)
                    os.replace(tmp.name, target)
                total += 1

    print(f"[hf_sync] restored {total} files from {len(providers)} providers")
    return total


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        sync()
    elif cmd == "restore":
        restore()
    else:
        print("usage: python -m admin.hf_sync [sync|restore]")
