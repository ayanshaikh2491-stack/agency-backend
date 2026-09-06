"""OSM Overpass lead source — free, keyless, geo-accurate, no Chrome.

Replaces the Bing-HTML lightweight finder: Overpass serves clean JSON of
real local businesses inside the requested city boundary, with phone /
website / email tags. Deep-crawl fallback (site contact page) still runs
for businesses missing an email, using the existing httpx+selectolax
helpers in this module.
"""

from __future__ import annotations

import logging
import re
import urllib.parse

logger = logging.getLogger(__name__)

# Free public Overpass endpoints (failover on 429/503)
_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# category -> OSM tag filters tried in order (first with results wins)
# Home-service trades (agency's ICP) map to craft/shop/office tags.
_CATEGORY_TAGS: dict[str, list[str]] = {
    "dentist": ['["amenity"="dentist"]'],
    "doctor": ['["amenity"="doctors"]'],
    "clinic": ['["amenity"="clinic"]'],
    "lawyer": ['["amenity"="lawyer"]', '["office"="lawyer"]'],
    "accountant": ['["office"="accountant"]'],
    "roofing": ['["craft"="roofer"]', '["shop"="roofing"]'],
    "roofer": ['["craft"="roofer"]', '["shop"="roofing"]'],
    "plumber": ['["craft"="plumber"]', '["shop"="plumber"]'],
    "electrician": ['["craft"="electrician"]'],
    "hvac": ['["craft"="hvac"]', '["shop"="hvac"]'],
    "landscaping": ['["craft"="gardener"]', '["shop"="garden_centre"]'],
    "salon": ['["shop"="hairdresser"]'],
    "hairdresser": ['["shop"="hairdresser"]'],
    "barber": ['["shop"="hairdresser"]'],
    "gym": ['["leisure"="fitness_centre"]'],
    "fitness": ['["leisure"="fitness_centre"]'],
    "restaurant": ['["amenity"="restaurant"]'],
    "cafe": ['["amenity"="cafe"]'],
    "bakery": ['["shop"="bakery"]'],
    "car_repair": ['["shop"="car_repair"]'],
    "auto repair": ['["shop"="car_repair"]'],
    "real estate": ['["office"="estate_agent"]'],
    "real estate agent": ['["office"="estate_agent"]'],
    "insurance": ['["office"="insurance"]'],
    "pharmacy": ['["amenity"="pharmacy"]'],
    "veterinary": ['["shop"="pet"]', '["amenity"="veterinary"]'],
    "vet": ['["amenity"="veterinary"]'],
    "spa": ['["leisure"="spa"]', '["shop"="beauty"]'],
    "beauty": ['["shop"="beauty"]'],
    "florist": ['["shop"="florist"]'],
    "tattoo": ['["shop"="tattoo"]'],
    "butcher": ['["shop"="butcher"]'],
    "cleaning": ['["shop"="dry_cleaning"]', '["craft"="cleaning"]'],
    "dry cleaner": ['["shop"="dry_cleaning"]'],
    "travel agency": ['["shop"="travel_agency"]'],
    "school": ['["amenity"="school"]', '["amenity"="kindergarten"]'],
    "photographer": ['["craft"="photographer"]'],
    "bakery shop": ['["shop"="bakery"]'],
    "furniture": ['["shop"="furniture"]'],
    "clothing": ['["shop"="clothes"]'],
    "pet store": ['["shop"="pet"]'],
}

# Generic fallback: try amenity=, shop=, craft=, office= with the category word
_GENERIC_TAG_KEYS = ("amenity", "shop", "craft", "office", "leisure")

_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _geo_area_filter(city: str, state: str) -> str:
    """Overpass area selector for a named city (admin_level 8 = US city).

    Kept deliberately light: name + level + boundary matches fast; heavier
    state disambiguation regex made Overpass time out. State is carried in
    the lead metadata instead of the query.
    """
    city_esc = city.replace('"', "")
    return f'area["name"="{city_esc}"]["admin_level"="8"]["boundary"="administrative"]->.a;'


def _overpass_query(category: str, city: str, state: str, limit: int) -> str:
    tagsets = _CATEGORY_TAGS.get(category.strip().lower())
    if not tagsets:
        word = category.strip().lower().replace('"', "")
        tagsets = [f'["{k}"="{word}"]' for k in _GENERIC_TAG_KEYS]
    body = _geo_area_filter(city, state)
    unions = "".join(f"(nwr{ts}(area.a););" for ts in tagsets)
    return f"[out:json][timeout:25];{body}{unions}out center tags {int(limit)};"


def _overpass_fetch(query: str) -> list[dict]:
    """POST the query to Overpass with endpoint failover. Returns elements."""
    import httpx

    last_err = ""
    for ep in _OVERPASS_ENDPOINTS:
        try:
            r = httpx.post(
                ep,
                data={"data": query},
                timeout=30,
                headers={"User-Agent": "TAGS-Agency-OS/1.0 (lead-gen)"},
            )
            if r.status_code == 200:
                return r.json().get("elements", [])
            last_err = f"{ep} -> HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_err = f"{ep} -> {exc}"
        logger.info("overpass failover: %s", last_err)
    logger.warning("all overpass endpoints failed: %s", last_err)
    return []


def _osm_to_lead(el: dict, category: str, city: str, state: str) -> dict | None:
    """Convert an OSM element into the shared normalized lead shape."""
    t = el.get("tags") or {}
    name = (t.get("name") or "").strip()
    if not name:
        return None
    phone = (t.get("phone") or t.get("contact:phone") or t.get("phone:contact") or "").strip()
    if phone and not phone.startswith("+") and re.match(r"^\d{10}$", phone.replace("-", "").replace(" ", "")):
        phone = "+1-" + phone
    website = (t.get("website") or t.get("contact:website") or t.get("url") or "").strip()
    if website and not website.startswith("http"):
        website = "https://" + website
    if website:
        # strip tracking cruft, keep bare host+path
        parsed = urllib.parse.urlparse(website)
        if parsed.netloc:
            website = f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path}"
    email = (t.get("email") or t.get("contact:email") or "").strip().lower()
    addr_parts = [
        (t.get("addr:housenumber") or "").strip(),
        (t.get("addr:street") or "").strip(),
    ]
    address = " ".join(p for p in addr_parts if p)
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    lead = {
        "name": name,
        "business_name": name,
        "phone": phone,
        "email": email,
        "website": website,
        "address": address,
        "city": city,
        "state": state,
        "category": category,
        "source": "osm_overpass",
        "rating": None,
        "verified": bool(phone or website or email),
        "lat": lat,
        "lon": lon,
    }
    return lead


def find_leads_osm(
    category: str,
    city: str,
    state: str,
    max_results: int = 8,
) -> list[dict]:
    """Find local-business leads via OSM Overpass (no Chrome, no API key).

    Returns up to max_results normalized leads sorted so contactable ones
    (phone/website/email present) come first. Missing emails are deep-filled
    from the business's own website via _light_fetch + _extract_contact.
    """
    query = _overpass_query(category, city, state, max_results * 3)
    elements = _overpass_fetch(query)
    if not elements:
        return []

    leads: list[dict] = []
    seen_names: set[str] = set()
    for el in elements:
        lead = _osm_to_lead(el, category, city, state)
        if not lead:
            continue
        key = lead["name"].lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        leads.append(lead)
        if len(leads) >= max_results * 2:
            break

    # Deep-fill email from the business's own site when missing.
    for lead in leads:
        if lead.get("email") or not lead.get("website"):
            continue
        try:
            from urllib.parse import urlparse

            base_domain = (urlparse(lead["website"]).hostname or "").replace("www.", "").lower()
            site_html = _light_fetch(lead["website"], timeout=8.0)
            if site_html:
                e, p = _extract_contact(site_html, base_domain)
                if e:
                    lead["email"] = e
                if p and not lead.get("phone"):
                    lead["phone"] = p
        except Exception:  # noqa: BLE001
            pass

    # Contactable leads first, then by completeness of contact info.
    leads.sort(key=lambda l: (bool(l.get("phone")) + bool(l.get("website")) + bool(l.get("email"))), reverse=True)
    return leads[:max_results]
