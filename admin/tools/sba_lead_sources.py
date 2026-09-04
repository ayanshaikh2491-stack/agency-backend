# admin/tools/sba_lead_sources.py
"""Multi-platform local lead source layer for the SBA agent.

Each platform is a small plugin using ChromeTool. All plugins return the
same normalized lead shape so the autopilot pipeline never cares which
source a lead came from. No-website filter + place-page verification live
here (booking aggregators like OpenTable/Flexbook are ignored).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

from admin.tools.chrome_tool import ChromeTool

logger = logging.getLogger(__name__)

NORMALIZED_FIELDS = [
    "name", "address", "city", "state", "phone", "website",
    "category", "source", "rating", "verified", "sources",
]

SOURCES = ["google_maps", "yelp", "yellowpages", "bing_maps", "facebook_pages"]

# Booking/aggregator domains that never count as a real business website
_AGGREGATORS = ("opentable", "flexbook", "modento", "servicetitan", "schedulicity",
                "booksy", "yelp.com", "yellowpages.com", "facebook.com", "instagram.com")


def _is_aggregator(url: str) -> bool:
    return any(agg in (url or "").lower() for agg in _AGGREGATORS)


# UI labels that the generic extractor picks up from aggregator pages
# (yellowpages search chrome, bing filter pills, etc.). Not real businesses.
_GENERIC_LABELS = {
    "use my location", "default", "distance", "open now", "sort",
    "filter", "filters", "website", "directions", "call", "reviews",
    "plumber", "electrician", "hvac", "roofer", "landscaper", "salon",
    "dentist", "painter", "handyman", "auto repair", "cleaning service",
    "name", "address", "phone", "more", "see all", "view all",
}


def _is_real_business(card: dict) -> bool:
    """True only for a plausible business card (name + phone)."""
    name = (card.get("name") or "").strip()
    phone = (card.get("phone") or "").strip()
    if not name or not phone:
        return False
    if name.lower() in _GENERIC_LABELS:
        return False
    return True


def normalize_lead(card: dict, source: str) -> dict:
    """Normalize a raw plugin card into the shared lead shape."""
    name = (card.get("name") or card.get("text") or "").strip()
    if not name:
        name = "Unknown"
    address = (card.get("address") or "").strip()
    city = (card.get("city") or "").strip()
    state = (card.get("state") or "").strip()
    phone = re.sub(r"[^0-9+]", "", (card.get("phone") or "")).strip()
    website = (card.get("website") or "").strip()
    return {
        "name": name,
        "address": address,
        "city": city,
        "state": state,
        "phone": phone,
        "href": (card.get("href") or "").strip(),
        "website": website if website and not _is_aggregator(website) else "",
        "category": (card.get("category") or "").strip(),
        "source": source,
        "rating": card.get("rating"),
        "verified": bool(card.get("verified")),
        "sources": [source],
    }


def dedupe_leads(leads: list[dict]) -> list[dict]:
    """Merge leads with the same name+phone, union their sources."""
    out: dict[tuple, dict] = {}
    for lead in leads:
        key = (lead.get("name", "").strip().lower(), lead.get("phone", "").strip())
        if not key[0]:
            continue
        if key in out:
            existing = out[key]
            for src in lead.get("sources", []):
                if src not in existing["sources"]:
                    existing["sources"].append(src)
            if not existing.get("website") and lead.get("website"):
                existing["website"] = lead["website"]
            if lead.get("verified"):
                existing["verified"] = True
        else:
            out[key] = dict(lead)
    return list(out.values())


# ── Lightweight finder (NO Chrome) ───────────────────────────────────────────
# Browser scraping (find_leads_all) is heavy and wedges on a dead CDP transport.
# This path uses httpx (async HTTP) + selectolax (fast HTML parse) so the agent
# finds leads WITHOUT a browser, then deep-crawls each business's own website to
# pull its real email + phone. Returns the same normalized lead shape.
_LIGHT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _light_fetch(url: str, timeout: float = 8.0) -> str:
    """Fetch a URL with httpx (light, no browser). Returns '' on failure."""
    try:
        import httpx

        with httpx.Client(
            headers={"User-Agent": _LIGHT_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            return resp.text or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("light fetch failed %s: %s", url, exc)
        return ""


def _extract_contact(html: str, base_domain: str) -> tuple[str, str]:
    """Pull first real email + phone from a page using selectolax."""
    email = ""
    phone = ""
    try:
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)
        text = tree.text(separator=" ", strip=True)
        # Email: prefer one whose domain matches the business site.
        for m in _EMAIL_RE.finditer(text):
            cand = m.group(0).lower()
            if base_domain and base_domain in cand:
                email = cand
                break
        if not email:
            m = _EMAIL_RE.search(text)
            email = m.group(0).lower() if m else ""
        pm = _PHONE_RE.search(text)
        phone = pm.group(0) if pm else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("contact extract failed: %s", exc)
    return email, phone


def find_leads_lightweight(
    category: str,
    city: str,
    state: str,
    max_per_source: int = 5,
) -> list[dict]:
    """Find leads via Bing organic HTML (httpx + selectolax, no Chrome).

    For each business we also fetch its own website and pull the real email +
    phone, so the agent gets contactable leads without a browser.
    """
    query = f"{category} in {city}, {state}"
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&count=20"
    html = _light_fetch(url, timeout=10.0)
    if not html:
        logger.warning("light find: no HTML for %r", query)
        return []

    leads: list[dict] = []
    try:
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)
        blocks = tree.css("li.b_algo") or tree.css("div.b_algo")
        for blk in blocks[: max(20, max_per_source * 3)]:
            h2 = blk.css_first("h2")
            name = (h2.text(strip=True) if h2 else "") or ""
            website = ""
            cite = blk.css_first("cite")
            if cite:
                website = cite.text(strip=True)
            # Bing cite text carries breadcrumb junk like "site.com › plumber".
            # Keep only the first whitespace/handle-delimited token as the host.
            if website:
                website = website.split()[0].split("›")[0].strip()
            if website and not website.startswith("http"):
                website = "https://" + website.lstrip("/")
            snippet = blk.text(separator=" ", strip=True)
            phone_m = _PHONE_RE.search(snippet)
            phone = phone_m.group(0) if phone_m else ""
            clean_name = name.strip().lower()
            if not clean_name or clean_name in _GENERIC_LABELS:
                continue
            # Deep step: pull real email/phone from the business's own site.
            email = ""
            base_domain = ""
            if website:
                try:
                    from urllib.parse import urlparse

                    base_domain = (urlparse(website).hostname or "").replace("www.", "").lower()
                except Exception:  # noqa: BLE001
                    pass
                site_html = _light_fetch(website, timeout=8.0)
                if site_html:
                    e, p = _extract_contact(site_html, base_domain)
                    email, phone = e or email, p or phone
            lead = normalize_lead({
                "name": name,
                "phone": phone,
                "email": email,
                "website": website,
                "address": "",
                "category": category,
                "city": city,
                "state": state,
                "text": snippet,
                "href": website,
                "source": "bing_light",
                "rating": None,
                "verified": False,
            }, "bing_light")
            if email:
                lead["email"] = email
            leads.append(lead)
            if len(leads) >= max_per_source:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("light find parse failed: %s", exc)

    logger.info("light find: %d leads for %r", len(leads), query)
    return leads


def _card_from_items(raw: dict) -> list[dict]:
    items = raw.get("items") or []
    # chrome_tool.extract() returns {"text": json.dumps([...])}; normalize that
    # shape too so scrapers work regardless of the extractor contract.
    if not items:
        try:
            txt = raw.get("text") or ""
            if txt and txt.startswith("["):
                import json as _json

                items = _json.loads(txt)
        except Exception:  # noqa: BLE001
            items = []
    cards = []
    for it in items:
        if isinstance(it, str):
            it = {"text": it}
        text = it.get("text") or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue
        # Phone: only the matched span (the whole line may contain "8 PM" junk).
        phone = ""
        for ln in lines:
            m = re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", ln)
            if m:
                phone = m.group(0)
                break
        # Address: from the first digit run to the end of its line.
        address = ""
        for ln in lines:
            m = re.search(r"\d+\s+\w+", ln)
            if m:
                first = re.search(r"\d", ln)
                if first:
                    address = ln[first.start():].strip()
                break
        cards.append({
            "name": it.get("name") or lines[0],
            "text": text,
            "href": it.get("href") or "",
            "address": address,
            "phone": phone,
            "website": it.get("website") or "",
        })
    return cards


def _maps_url(category: str, city: str, state: str) -> str:
    q = f"{category} {city} {state}".replace(" ", "+")
    return f"https://www.google.com/maps/search/{q}"


# Google Maps result cards in the current DOM: an <a> with an aria-label and a
# /maps/place href inside the results feed. Class names change often, so we key
# off the stable aria-label + href pattern and pull text from the card wrapper.
_MAPS_CARDS_JS = r"""
() => {
  const phoneRe = /\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}/;
  const cardText = (a) => {
    let el = a;
    for (let i = 0; i < 6; i++) {
      if (!el.parentElement) break;
      el = el.parentElement;
      const t = (el.innerText || '').trim();
      if (t && t.length < 800 && phoneRe.test(t)) return t;
    }
    const feed = a.closest('[role="feed"] > div');
    return feed ? (feed.innerText || '').trim() : (a.innerText || '').trim();
  };
  // Domains that are platforms/aggregators, never the business's own site.
  const BAD_HOSTS = ['google.com','google.co.in','google.co.uk','maps.google.com','facebook.com','instagram.com','twitter.com','x.com','linkedin.com','yelp.com','yellowpages.com','yellowpages.ca','bing.com','youtube.com','tripadvisor.com','angieslist.com','bbb.org','zillow.com','realtor.com','redfin.com','booking.com','opentable.com','grubhub.com','doordash.com','ubereats.com','homeadvisor.com','porch.com','thumbtack.com','maps.app.goo.gl','goo.gl','g.page','whitepages.com','superpages.com','manta.com','houzz.com','foursquare.com','yellowbot.com','nicelocal.com','cylex.us.com','merchantcircle.com','chamberofcommerce.com','cityfos.com'];
  // The card row = the direct child of the feed that contains this anchor.
  const rowOf = (a) => {
    const feed = a.closest('[role="feed"]');
    if (feed) {
      const kids = feed.children;
      for (let i = 0; i < kids.length; i++) if (kids[i].contains(a)) return kids[i];
    }
    let el = a;
    for (let i = 0; i < 5; i++) {
      if (!el.parentElement) break;
      el = el.parentElement;
      if (el.querySelectorAll && el.querySelectorAll('a[href*="/maps/place"]').length === 1) return el;
    }
    return null;
  };
  // The business's own website link inside the card row ("Visit X's
  // website" button or the first external http link that is not a platform).
  const websiteOf = (row) => {
    if (!row || !row.querySelectorAll) return '';
    const links = row.querySelectorAll('a[href^="http"]');
    for (const l of links) {
      const al = (l.getAttribute('aria-label') || '').toLowerCase();
      if (al.startsWith('visit') && al.includes('website')) return l.getAttribute('href') || '';
    }
    for (const l of links) {
      const h = (l.getAttribute('href') || '').trim();
      if (!h || h.includes('/maps/place') || h.includes('google.com')) continue;
      let host = '';
      try { host = new URL(h).hostname.replace(/^www\./, '').toLowerCase(); }
      catch (e) { continue; }
      if (host && !BAD_HOSTS.includes(host)) return h;
    }
    return '';
  };
  const seen = new Set();
  const out = [];
  const anchors = document.querySelectorAll('a[aria-label][href*="/maps/place"], a[aria-label][href*="google.com/maps"]');
  for (const a of anchors) {
    const label = (a.getAttribute('aria-label') || '').trim();
    if (!label || label.length < 3 || seen.has(label)) continue;
    seen.add(label);
    const row = rowOf(a);
    out.push({ name: label, href: a.href, text: cardText(a), website: websiteOf(row) });
  }
  return out.slice(0, 40);
}
"""


async def _scrape_maps_cards(chrome: ChromeTool) -> list[dict]:
    """Extract Google Maps result cards via JS (aria-label + place href)."""
    try:
        cards = await chrome.eval_json(_MAPS_CARDS_JS)
        if isinstance(cards, list) and cards:
            # Parse phone/address out of the card text into card fields.
            return _card_from_items({"items": cards})
    except Exception as exc:  # noqa: BLE001
        logger.warning("maps JS extraction failed: %s", exc)
    # Fallback: the generic extractor (kept for older layouts).
    raw = await chrome.extract(limit=40)
    return _card_from_items(raw)


def _yelp_url(category: str, city: str, state: str) -> str:
    return f"https://www.yelp.com/search?find_desc={category.replace(' ', '+')}&find_loc={city.replace(' ', '+')}%2C+{state}"


def _yellowpages_url(category: str, city: str, state: str) -> str:
    return f"https://www.yellowpages.com/search?search_terms={category.replace(' ', '+')}&geo_location_terms={city.replace(' ', '+')}%2C+{state}"


def _bing_url(category: str, city: str, state: str) -> str:
    return f"https://www.bing.com/maps?q={category.replace(' ', '+')}+{city.replace(' ', '+')}+{state}"


def _facebook_url(category: str, city: str, state: str) -> str:
    return f"https://www.facebook.com/search/pages/?q={category.replace(' ', '+')}+{city.replace(' ', '+')}+{state}"


# YellowPages search result cards: .result wrapper with h3.n-heading (name),
# .phones.phone.primary (phone), .street-address + .locality (address), and the
# website link (.track-visit-website). The generic extractor grabs sidebar/
# header noise, so we target cards explicitly.
_YELLOWPAGES_CARDS_JS = r"""
() => {
  const phoneRe = /\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}/;
  const out = [];
  const seen = new Set();
  const cards = document.querySelectorAll('.result, div[class*="result"]');
  for (const c of cards) {
    const nameEl = c.querySelector('h3.n-heading a, h2 a, a[href*="/yellowpages.com"] .n-business-name, a.business-name, h3 a');
    if (!nameEl) continue;
    const name = (nameEl.innerText || '').trim();
    if (!name || name.length < 2 || seen.has(name)) continue;
    let phone = '';
    const phoneEl = c.querySelector('.phones.phone.primary, .phone-primary, div.phone, .phones');
    if (phoneEl) {
      const m = (phoneEl.innerText || '').match(phoneRe);
      if (m) phone = m[0];
    }
    if (!phone) {
      const m = (c.innerText || '').match(phoneRe);
      if (m) phone = m[0];
    }
    const addrEl = c.querySelector('.street-address, .address, div[class*="address"]');
    const address = (addrEl ? (addrEl.innerText || '').trim() : '');
    const siteEl = c.querySelector('a[class*="website"], a.track-visit-website, a[href^="http"]');
    const website = siteEl ? (siteEl.getAttribute('href') || '') : '';
    seen.add(name);
    out.push({ name, phone, address, website, href: nameEl.getAttribute('href') || '' });
  }
  return out.slice(0, 40);
}
"""

# Yelp search result cards: li[data-testid="serp-result"] (or div fallback) with
# h3/h4 name and a phone in the card body. Yelp often bot-blocks with an empty
# body; the JS extractor still returns [] gracefully so other sources continue.
_YELP_CARDS_JS = r"""
() => {
  const phoneRe = /\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}/;
  const out = [];
  const seen = new Set();
  const cards = document.querySelectorAll('li[data-testid="serp-result"], div[data-testid="serp-result"], li[data-testid="result"]');
  for (const c of cards) {
    const nameEl = c.querySelector('h3 a, h4 a, h3 span, a[href*="/biz/"]');
    if (!nameEl) continue;
    const name = (nameEl.innerText || '').trim();
    if (!name || name.length < 2 || seen.has(name)) continue;
    const m = (c.innerText || '').match(phoneRe);
    if (!m) continue;  // Yelp cards without a visible phone are no-prospects
    const siteEl = c.querySelector('a[href^="http"][rel="noopener"], a[class*="website"]');
    const website = siteEl ? (siteEl.getAttribute('href') || '') : '';
    const addrEl = c.querySelector('div[class*="address"], address, div[class*="location"]');
    seen.add(name);
    out.push({
      name,
      phone: m[0],
      address: addrEl ? (addrEl.innerText || '').trim() : '',
      website,
      href: nameEl.getAttribute('href') || '',
    });
  }
  return out.slice(0, 40);
}
"""


async def _scrape_cards(chrome: ChromeTool, url: str) -> list[dict]:
    await chrome.goto(url)
    await chrome.wait("load")
    raw = await chrome.extract(limit=20)
    return _card_from_items(raw)


async def _scrape_yellowpages(chrome: ChromeTool, url: str) -> list[dict]:
    """YellowPages: JS card extractor with a generic fallback."""
    await chrome.goto(url)
    await chrome.wait("load")
    await asyncio.sleep(1.5)
    try:
        cards = await chrome.eval_json(_YELLOWPAGES_CARDS_JS)
        if isinstance(cards, list) and cards:
            return cards
    except Exception as exc:  # noqa: BLE001
        logger.warning("yellowpages JS extraction failed: %s", exc)
    raw = await chrome.extract(limit=20)
    return _card_from_items(raw)


async def _scrape_yelp(chrome: ChromeTool, url: str) -> list[dict]:
    """Yelp: JS card extractor; retry once on empty body (bot-block flake)."""
    await chrome.goto(url)
    await chrome.wait("load")
    await asyncio.sleep(2)
    cards: list[dict] = []
    try:
        cards = await chrome.eval_json(_YELP_CARDS_JS)
        if not (isinstance(cards, list) and cards):
            await asyncio.sleep(2)
            cards = await chrome.eval_json(_YELP_CARDS_JS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("yelp JS extraction failed: %s", exc)
    if isinstance(cards, list) and cards:
        return cards
    raw = await chrome.extract(limit=20)
    return _card_from_items(raw)


async def find_leads(source: str, category: str, city: str, state: str,
                     max_candidates: int = 10, chrome: ChromeTool | None = None) -> list[dict]:
    """Collect + verify leads from one platform."""
    own_chrome = chrome is None
    chrome = chrome or ChromeTool(browser_name="sba", workspace="agency")
    leads: list[dict] = []
    try:
        if source == "google_maps":
            url = _maps_url(category, city, state)
            await chrome.goto(url)
            await chrome.wait("load")
            # Let the result feed render (lazy-loaded cards AND their website
            # buttons appear over several seconds; too short = cards with no
            # website even though the business has one).
            await asyncio.sleep(int(os.environ.get("SBA_MAPS_SETTLE_SECONDS", "8")))
            cards = await _scrape_maps_cards(chrome)
            # Card-level: home-service categories show Website button on card;
            # cards without it are high-confidence no-website candidates.
            for card in cards[:max_candidates]:
                lead = normalize_lead(card, source)
                lead["city"], lead["state"] = city, state
                lead["category"] = category
                lead["verified"] = True  # maps card pattern; place-page verify optional
                leads.append(lead)
        elif source == "yelp":
            cards = await _scrape_yelp(chrome, _yelp_url(category, city, state))
            for card in cards[:max_candidates]:
                lead = normalize_lead(card, source)
                lead["city"], lead["state"] = city, state
                lead["category"] = category
                website = card.get("website") or (card.get("href") or "")
                if website and not _is_aggregator(website):
                    lead["website"] = website
                else:
                    lead["verified"] = True  # no real website -> prospect
                leads.append(lead)
        elif source == "yellowpages":
            cards = await _scrape_yellowpages(chrome, _yellowpages_url(category, city, state))
            for card in cards[:max_candidates]:
                lead = normalize_lead(card, source)
                lead["city"], lead["state"] = city, state
                lead["category"] = category
                lead["verified"] = True
                leads.append(lead)
        elif source == "bing_maps":
            cards = await _scrape_cards(chrome, _bing_url(category, city, state))
            for card in cards[:max_candidates]:
                lead = normalize_lead(card, source)
                lead["city"], lead["state"] = city, state
                lead["category"] = category
                lead["verified"] = True
                leads.append(lead)
        elif source == "facebook_pages":
            cards = await _scrape_cards(chrome, _facebook_url(category, city, state))
            for card in cards[:max_candidates]:
                lead = normalize_lead(card, source)
                lead["city"], lead["state"] = city, state
                lead["category"] = category
                lead["verified"] = False  # beta: phone/address rarely on card
                leads.append(lead)
        else:
            raise ValueError(f"Unknown source: {source}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("find_leads(%s) failed: %s", source, exc)
    finally:
        if own_chrome:
            # A wedged playwright transport can hang stop() forever; never
            # let a cleanup call freeze the pipeline (seen: 9h autopilot hang).
            try:
                await asyncio.wait_for(chrome.close(), timeout=10)
            except Exception as exc:  # noqa: BLE001
                logger.warning("chrome.close() after %s timed out: %s", source, exc)
    # Only real businesses: name + phone. Aggregator UI labels ('Use my
    # location') and phone-less rows are noise, not prospects.
    return [lead for lead in leads if _is_real_business(lead)]


async def find_leads_all(category: str, city: str, state: str,
                         max_per_source: int = 5, chrome: ChromeTool | None = None) -> list[dict]:
    """Collect leads from every platform, dedupe, return merged list."""
    own_chrome = chrome is None
    chrome = chrome or ChromeTool(browser_name="sba", workspace="agency")
    all_leads: list[dict] = []
    try:
        for source in SOURCES:
            try:
                found = await find_leads(source, category, city, state,
                                         max_candidates=max_per_source, chrome=chrome)
                all_leads.extend(found)
                logger.info("source %s -> %d leads", source, len(found))
            except Exception as exc:  # noqa: BLE001
                logger.warning("source %s skipped: %s", source, exc)
            await asyncio.sleep(1.0)
    finally:
        if own_chrome:
            try:
                await asyncio.wait_for(chrome.close(), timeout=10)
            except Exception as exc:  # noqa: BLE001
                logger.warning("chrome.close() after lead pass timed out: %s", exc)
    return dedupe_leads(all_leads)
