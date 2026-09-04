"""Google Maps local lead scraper for the SBA agency.

Finds US local businesses WITHOUT a website (ideal SEO/website prospects):

  Mode A (card)  — home-service categories (plumber, roofer, auto repair, ...).
                   Google Maps shows the "Website" button directly on the card,
                   so cards lacking it are high-confidence no-website candidates.
  Mode B (all)   — booking categories (dentist, restaurant, salon, mechanic, ...).
                   Maps hides the Website button here even when a site exists, so
                   EVERY card is collected and verified on its place page.

Verification always opens the Google Maps place page and checks for a real
website link (data-value="Open website"), ignoring booking aggregators
(OpenTable, Flexbook, Modento, ServiceTitan, etc.).

Saves leads to data/sba_maps_leads.csv and prints a summary.

Usage: python admin/tools/maps_lead_scraper.py [max_combos] [max_candidates]
"""
import asyncio
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from admin.tools.chrome_tool import ChromeTool  # noqa: E402

# (category, city, state, mode). Mode "card" trusts the card-level Website
# button (home services). Mode "all" verifies every card on the place page
# (booking categories where Maps hides the Website button).
DEFAULT_COMBOS = [
    # ── Mode A: home services (card-level Website button is reliable) ──
    ("plumber", "Houston", "TX", "card"),
    ("roofer", "Austin", "TX", "card"),
    ("hvac", "Phoenix", "AZ", "card"),
    ("electrician", "San Antonio", "TX", "card"),
    ("auto repair", "Dallas", "TX", "card"),
    ("auto repair", "Phoenix", "AZ", "card"),
    ("auto repair", "Columbus", "OH", "card"),
    ("landscaper", "Denver", "CO", "card"),
    ("cleaning service", "Charlotte", "NC", "card"),
    ("handyman", "Nashville", "TN", "card"),
    ("handyman", "Tampa", "FL", "card"),
    ("locksmith", "Tampa", "FL", "card"),
    ("locksmith", "Kansas City", "MO", "card"),
    ("junk removal", "Orlando", "FL", "card"),
    ("junk removal", "Houston", "TX", "card"),
    ("garage door repair", "Las Vegas", "NV", "card"),
    ("window cleaning", "Columbus", "OH", "card"),
    ("carpet cleaning", "Indianapolis", "IN", "card"),
    ("pest control", "Kansas City", "MO", "card"),
    ("appliance repair", "Memphis", "TN", "card"),
    ("fencing contractor", "Louisville", "KY", "card"),
    ("moving company", "Oklahoma City", "OK", "card"),
    ("dumpster rental", "Albuquerque", "NM", "card"),
    ("tree service", "Tampa", "FL", "card"),
    ("power washing", "Charlotte", "NC", "card"),
    # ── Mode B: booking categories (verify every card on place page) ──
    ("dentist", "Columbus", "OH", "all"),
    ("dentist", "Nashville", "TN", "all"),
    ("auto body shop", "Houston", "TX", "all"),
    ("mechanic", "San Antonio", "TX", "all"),
    ("hair salon", "Denver", "CO", "all"),
    ("barber shop", "Indianapolis", "IN", "all"),
    ("restaurant", "Louisville", "KY", "all"),
    ("dog groomer", "Las Vegas", "NV", "all"),
    ("veterinarian", "Memphis", "TN", "all"),
]

SCROLL_ROUNDS = 25
MAX_CANDIDATES = int(os.environ.get("MAPS_MAX_CANDIDATES", "500"))

EXTRACT_JS = r"""
() => {
  const cards = Array.from(document.querySelectorAll('div[role="article"]'));
  const out = [];
  const seen = new Set();
  for (const card of cards) {
    const a = card.querySelector('a[href*="/maps/place/"]');
    if (!a) continue;
    const name = (a.getAttribute('aria-label') || a.title || '').trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    const hasWeb = !!card.querySelector('a[data-value="Website"]');
    const tel = card.querySelector('a[href^="tel:"]');
    out.push({
      name,
      has_website: hasWeb,
      href: a.href,
      phone: tel ? tel.getAttribute('href').replace('tel:', '').trim() : '',
      text: (card.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 300),
    });
  }
  return { count: out.length, data: out };
}
"""

SCROLL_JS = r"""
() => {
  const feed = document.querySelector('[role="feed"]');
  if (!feed) return false;
  const before = feed.scrollTop;
  feed.scrollTop = feed.scrollHeight;
  return feed.scrollTop > before;
}
"""

# Returns the real website (data-value="Open website") on a place page,
# ignoring booking/aggregator links.
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


async def scrape_combo(chrome: ChromeTool, category: str, city: str, state: str, mode: str) -> list[dict]:
    url = f"https://www.google.com/maps/search/{category.replace(' ', '+')}+{city.replace(' ', '+')}+{state}/"
    print(f"[maps] Searching [{mode}]: {category} {city}, {state}", flush=True)
    try:
        await chrome.goto(url)
    except Exception as exc:
        print(f"[maps] goto failed: {exc}", flush=True)
        return []
    try:
        await chrome.wait("network-idle", timeout=15000)
    except Exception:
        pass
    # Scroll the results FEED panel (page scroll doesn't load more cards).
    for i in range(SCROLL_ROUNDS):
        try:
            moved = await chrome.eval_json(SCROLL_JS)
            if not moved and i > 3:
                break
            await asyncio.sleep(0.5)
        except Exception:
            break
    try:
        res = await chrome.eval_json(EXTRACT_JS)
    except Exception as exc:
        print(f"[maps] eval failed: {exc}", flush=True)
        return []
    data = (res or {}).get("data") or []
    for item in data:
        item["category"] = category
        item["city_state"] = f"{city}, {state}"
        item["mode"] = mode
    no_site = sum(1 for d in data if not d["has_website"])
    print(f"[maps] {len(data)} cards, {no_site} without website (mode={mode})", flush=True)
    if mode == "card":
        return [d for d in data if not d["has_website"]]
    return data


async def verify_lead(chrome: ChromeTool, lead: dict) -> dict | None:
    """Open the place page and confirm the business really has no website."""
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
        return None  # has a real website -> not a lead
    lead["website_status"] = "verified_none"
    if info.get("phone"):
        lead["phone"] = info["phone"]
    if info.get("address"):
        lead["address"] = info["address"]
    if not lead.get("address"):
        lead["address"] = _parse_address(lead.get("text", ""))
    return lead


def _parse_address(text: str) -> str:
    m = re.search(r"·\s+(.{3,}?)\s+(?:Open|Closed|24 hours)", text)
    if m:
        return m.group(1).strip()
    return ""


def main() -> int:
    max_combos = int(sys.argv[1]) if len(sys.argv) > 1 else len(DEFAULT_COMBOS)
    combos = DEFAULT_COMBOS[:max_combos]

    async def run():
        chrome = ChromeTool(browser_name="sba", workspace="agency")
        try:
            status = await chrome.status()
            print("[maps] Chrome status:", status, flush=True)

            candidate_path = "data/sba_maps_candidates.json"
            candidates: list[dict] = []
            if os.path.exists(candidate_path):
                with open(candidate_path, encoding="utf-8") as f:
                    candidates = json.load(f)
                print(f"[maps] Resumed {len(candidates)} candidates from {candidate_path}", flush=True)
            else:
                seen_names: set[str] = set()
                for category, city, state, mode in combos:
                    if len(candidates) >= MAX_CANDIDATES:
                        print(f"[maps] Reached MAX_CANDIDATES={MAX_CANDIDATES}, stopping collection", flush=True)
                        break
                    cards = await scrape_combo(chrome, category, city, state, mode)
                    for card in cards:
                        key = card["name"].lower()
                        if key in seen_names:
                            continue
                        seen_names.add(key)
                        candidates.append(card)
                    print(f"[maps] running candidates: {len(candidates)}", flush=True)
                    await asyncio.sleep(1.2)
                os.makedirs("data", exist_ok=True)
                with open(candidate_path, "w", encoding="utf-8") as f:
                    json.dump(candidates, f, ensure_ascii=False, indent=1)
                print(f"[maps] Saved {len(candidates)} candidates to {candidate_path}", flush=True)

            print(f"\n[maps] Verifying {len(candidates)} candidates on place pages...", flush=True)
            leads: list[dict] = []
            skipped = 0
            for i, cand in enumerate(candidates, 1):
                verified = await verify_lead(chrome, cand)
                if verified:
                    leads.append(verified)
                else:
                    skipped += 1
                if i % 10 == 0:
                    print(f"[maps] verified {i}/{len(candidates)} -> {len(leads)} confirmed leads ({skipped} have sites)", flush=True)
            print(f"[maps] DONE verifying: {len(leads)} leads, {skipped} with websites", flush=True)
            return leads
        finally:
            await chrome.close()

    leads = asyncio.run(run())

    os.makedirs("data", exist_ok=True)
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
    for lead in leads[:20]:
        print(f"  - {lead['name']} | {lead['category']} | {lead['city_state']} | {lead.get('address') or 'no-addr'} | {lead.get('phone') or 'no-phone'}", flush=True)
    if len(leads) > 20:
        print(f"  ... aur {len(leads) - 20} leads (CSV mein)", flush=True)
    return 0 if leads else 1


if __name__ == "__main__":
    sys.exit(main())
