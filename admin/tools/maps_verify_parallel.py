"""Parallel place-page verifier for Google Maps candidates.

Splits data/sba_maps_candidates.json across N Chrome instances (one daemon
per worker, separate profile dir + CDP port), verifies each candidate's
place page for a real website link, and appends confirmed leads to a live
JSONL file immediately so results stream as they are verified.

Final merged CSV: data/sba_maps_leads.csv
Live stream:      data/sba_maps_leads_live.jsonl

Usage: python admin/tools/maps_verify_parallel.py [workers]
"""
import asyncio
import csv
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

LIVE_JSONL = "data/sba_maps_leads_live.jsonl"


def _parse_address(text: str) -> str:
    m = re.search(r"·\s+(.{3,}?)\s+(?:Open|Closed|24 hours)", text)
    if m:
        return m.group(1).strip()
    return ""


def _append_live(lead: dict) -> None:
    """Append one confirmed lead to the live JSONL stream."""
    os.makedirs("data", exist_ok=True)
    with open(LIVE_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(lead, ensure_ascii=False) + "\n")


async def verify_one(chrome: ChromeTool, lead: dict) -> dict | None:
    href = lead.get("href")
    if not href:
        return lead
    try:
        await chrome.goto(href)
    except Exception as exc:
        print(f"[maps] verify goto failed for {lead['name']}: {exc}", flush=True)
        return lead
    try:
        await chrome.wait("network-idle", timeout=15000)
    except Exception:
        pass
    await asyncio.sleep(1.0)
    try:
        info = await chrome.eval_json(VERIFY_JS) or {}
    except Exception:
        info = {}
    if info.get("has_website"):
        return None
    lead["website_status"] = "verified_none"
    if info.get("phone"):
        lead["phone"] = info["phone"]
    if info.get("address"):
        lead["address"] = info["address"]
    if not lead.get("address"):
        lead["address"] = _parse_address(lead.get("text", ""))
    return lead


async def worker(worker_id: int, slice_of: list[dict], stats: dict) -> None:
    chrome = ChromeTool(
        browser_name="sba",
        workspace=f"maps_w{worker_id}",
        profile_dir=os.path.expanduser(f"~/.sba-chrome-profile-{worker_id}"),
    )
    try:
        await chrome.status()
        print(f"[maps] worker {worker_id}: {len(slice_of)} candidates", flush=True)
        for i, cand in enumerate(slice_of, 1):
            verified = await verify_one(chrome, cand)
            if verified:
                stats["confirmed"] += 1
                _append_live(verified)
            else:
                stats["has_site"] += 1
            stats["done"] += 1
            if stats["done"] % 5 == 0:
                print(
                    f"[maps] PROGRESS {stats['done']}/{stats['total']} "
                    f"-> {stats['confirmed']} leads ({stats['has_site']} have sites)",
                    flush=True,
                )
    finally:
        await chrome.close()


async def main_async(workers_n: int) -> list[dict]:
    with open("data/sba_maps_candidates.json", encoding="utf-8") as f:
        candidates = json.load(f)
    total = len(candidates)
    print(f"[maps] {total} candidates, {workers_n} parallel workers", flush=True)

    # Fresh live stream for this run
    os.makedirs("data", exist_ok=True)
    if os.path.exists(LIVE_JSONL):
        os.remove(LIVE_JSONL)

    slices: list[list[dict]] = [[] for _ in range(workers_n)]
    for idx, cand in enumerate(candidates):
        slices[idx % workers_n].append(cand)

    stats = {"done": 0, "confirmed": 0, "has_site": 0, "total": total}
    await asyncio.gather(*[worker(i, slices[i], stats) for i in range(workers_n)])
    print(f"[maps] DONE: {stats['confirmed']} confirmed, {stats['has_site']} have sites", flush=True)

    leads = []
    if os.path.exists(LIVE_JSONL):
        with open(LIVE_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    leads.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return leads


def main() -> int:
    workers_n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    leads = asyncio.run(main_async(workers_n))

    csv_path = "data/sba_maps_leads.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "category", "city_state", "address", "phone", "website_status", "text", "href"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(leads)

    print("\n" + "=" * 60, flush=True)
    print(f"VERIFIED LEADS: {len(leads)} -> {csv_path}", flush=True)
    for lead in leads[:25]:
        print(
            f"  - {lead['name']} | {lead['category']} | {lead['city_state']} | "
            f"{lead.get('address') or 'no-addr'} | {lead.get('phone') or 'no-phone'}",
            flush=True,
        )
    if len(leads) > 25:
        print(f"  ... aur {len(leads) - 25} leads (CSV mein)", flush=True)

    # Sync to self-hosted Supabase so every run backs up to EC2
    try:
        import subprocess as _sp
        import sys as _sys
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        _sp.run([_sys.executable, os.path.join(root, "supabase_sync.py")], cwd=root)
    except Exception as e:
        print(f"[warn] Supabase sync skip: {e}", flush=True)

    return 0 if leads else 1


if __name__ == "__main__":
    sys.exit(main())
