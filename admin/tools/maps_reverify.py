"""Re-verify suspicious candidates (those without phone from the parallel run).

The parallel run marked some candidates as leads when their place page had not
finished loading (no phone/address found, and no website link yet). This script
re-verifies those with a polling loop: it waits up to POLL_SECONDS for the page
to expose either a website link OR a phone/address, and only then decides.

Rules:
  - Page loaded + website link present  -> NOT a lead (has website)
  - Page loaded + no website link       -> LEAD (confirmed no-site, phone+addr)
  - Page never loads phone/addr/website -> SKIP (no guess; stays out of CSV)

Usage: python admin/tools/maps_reverify.py [workers] [poll_seconds]
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from admin.tools.chrome_tool import ChromeTool  # noqa: E402

VERIFY_JS = r"""
() => {
  const siteLinks = Array.from(document.querySelectorAll('a[data-value="Open website"]'));
  const tel = document.querySelector('a[href^="tel:"]');
  const addr = document.querySelector('button[data-value*="address"], div[aria-label*="Address"]');
  return {
    has_website: siteLinks.length > 0,
    website: siteLinks.length ? (siteLinks[0].getAttribute('href') || '') : '',
    phone: tel ? tel.getAttribute('href').replace('tel:', '').trim() : '',
    address: addr ? (addr.getAttribute('aria-label') || '').replace(/^Address\s*:?\s*/i, '') : '',
  };
}
"""

GOOD_JSONL = "data/sba_maps_leads_good.jsonl"
BAD_JSONL = "data/sba_maps_leads_rejected.jsonl"


def _parse_address(text: str) -> str:
    m = re.search(r"·\s+(.{3,}?)\s+(?:Open|Closed|24 hours)", text)
    if m:
        return m.group(1).strip()
    return ""


def _append(path: str, lead: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(lead, ensure_ascii=False) + "\n")


async def verify_one(chrome: ChromeTool, lead: dict, poll_sec: int) -> str:
    """Returns 'lead', 'reject', or 'skip'."""
    href = lead.get("href")
    if not href:
        return "skip"
    try:
        await chrome.goto(href)
    except Exception as exc:
        print(f"[maps] goto failed {lead['name']}: {exc}", flush=True)
        return "skip"
    # Poll until the page exposes phone/address/website or timeout.
    loaded = False
    info = {}
    for _ in range(poll_sec // 2):
        await asyncio.sleep(2)
        try:
            info = await chrome.eval_json(VERIFY_JS) or {}
        except Exception:
            continue
        if info.get("has_website") or info.get("phone") or info.get("address"):
            loaded = True
            break
    if not loaded:
        return "skip"
    if info.get("has_website"):
        return "reject"
    lead["website_status"] = "verified_none"
    if info.get("phone"):
        lead["phone"] = info["phone"]
    if info.get("address"):
        lead["address"] = info["address"]
    if not lead.get("address"):
        lead["address"] = _parse_address(lead.get("text", ""))
    return "lead"


async def worker(worker_id: int, slice_of: list[dict], stats: dict, poll_sec: int) -> None:
    chrome = ChromeTool(
        browser_name="sba",
        workspace=f"maps_rw{worker_id}",
        profile_dir=os.path.expanduser(f"~/.sba-chrome-reverify-{worker_id}"),
    )
    try:
        await chrome.status()
        for cand in slice_of:
            verdict = await verify_one(chrome, cand, poll_sec)
            if verdict == "lead":
                stats["lead"] += 1
                _append(GOOD_JSONL, cand)
            elif verdict == "reject":
                stats["reject"] += 1
                _append(BAD_JSONL, cand)
            else:
                stats["skip"] += 1
            stats["done"] += 1
            if stats["done"] % 5 == 0:
                print(
                    f"[maps] PROGRESS {stats['done']}/{stats['total']} "
                    f"| leads {stats['lead']} | has-site {stats['reject']} | skipped {stats['skip']}",
                    flush=True,
                )
    finally:
        await chrome.close()


async def main_async(workers_n: int, poll_sec: int) -> None:
    # Load original candidates, take only the ones that ended up "no phone"
    with open("data/sba_maps_candidates.json", encoding="utf-8") as f:
        candidates = json.load(f)
    no_phone_keys = set()
    if os.path.exists("data/sba_maps_leads_live.jsonl"):
        with open("data/sba_maps_leads_live.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if not d.get("phone"):
                    no_phone_keys.add(d.get("href", ""))

    subset = [c for c in candidates if c.get("href") in no_phone_keys]
    total = len(subset)
    print(f"[maps] Re-verifying {total} suspicious candidates with {workers_n} workers (poll {poll_sec}s)", flush=True)

    for p in (GOOD_JSONL, BAD_JSONL):
        if os.path.exists(p):
            os.remove(p)

    slices: list[list[dict]] = [[] for _ in range(workers_n)]
    for idx, cand in enumerate(subset):
        slices[idx % workers_n].append(cand)

    stats = {"done": 0, "lead": 0, "reject": 0, "skip": 0, "total": total}
    await asyncio.gather(*[worker(i, slices[i], stats, poll_sec) for i in range(workers_n)])
    print(
        f"[maps] DONE: {stats['lead']} confirmed, {stats['reject']} have websites, {stats['skip']} skipped (never loaded)",
        flush=True,
    )


def main() -> int:
    workers_n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    poll_sec = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    asyncio.run(main_async(workers_n, poll_sec))

    # Sync updated good/rejected status to self-hosted Supabase
    try:
        import subprocess as _sp
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        _sp.run([sys.executable, os.path.join(root, "supabase_sync.py")], cwd=root)
    except Exception as e:
        print(f"[warn] Supabase sync skip: {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
