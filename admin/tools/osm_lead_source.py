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

# Free public Overpass endpoints, tried in order (failover on 429/503/504).
# Two was not enough: during live testing Jaipur returned 429 from both and
# Lucknow returned 504 from both, while the first endpoint served Pune seconds
# earlier. Public mirrors rate-limit independently, so more endpoints means
# more chances that at least one is healthy.
# overpass.osm.jp was tried here and removed: its certificate does not match
# the hostname, so it can only ever fail the whole lookup on a TLS error.
_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
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
    # ── TAGS Agency's actual ICP, Indian market ─────────────────────────────
    # The list above is US-centric and covers trades that barely exist in the
    # tier-1/2 Indian cities this agency sells into. These are the categories
    # from FOUNDER_CONTEXT.md, and each was returning zero leads until the tag
    # was spelled the way OpenStreetMap actually spells it. Note the two
    # different conventions: healthcare uses `healthcare:speciality=`,
    # physios are tagged as a healthcare kind rather than an amenity.
    "physiotherapist": [
        '["healthcare"="physiotherapist"]',
        '["amenity"="physiotherapist"]',
        '["healthcare:speciality"="physiotherapy"]',
        '["leisure"="sports_centre"]["healthcare"]',
    ],
    "physio": [
        '["healthcare"="physiotherapist"]',
        '["healthcare:speciality"="physiotherapy"]',
    ],
    "dermatologist": [
        '["healthcare:speciality"="dermatology"]',
        '["amenity"="dermatologist"]',
    ],
    "skin clinic": [
        '["healthcare:speciality"="dermatology"]',
        '["amenity"="dermatologist"]',
    ],
    "derm": ['["healthcare:speciality"="dermatology"]'],
    "orthopaedic": [
        '["healthcare:speciality"="orthopaedics"]',
        '["amenity"="orthopaedic"]',
    ],
    "ortho": ['["healthcare:speciality"="orthopaedics"]'],
    "orthopedic": ['["healthcare:speciality"="orthopaedics"]'],
    "yoga": [
        '["leisure"="sports_centre"]["name"~"[Yy]oga",i]',
        '["leisure"="sports_centre"]["sport"~"[Yy]oga",i]',
    ],
    "yoga studio": [
        '["leisure"="sports_centre"]["name"~"[Yy]oga",i]',
        '["leisure"="sports_centre"]["sport"~"[Yy]oga",i]',
    ],
    "ayurveda": ['["healthcare"="ayurveda"]', '["amenity"="ayurveda"]'],
    "homeopathy": ['["healthcare"="homeopathy"]'],
    "optician": ['["healthcare"="optician"]', '["shop"="optician"]'],
    "hospital": ['["amenity"="hospital"]', '["healthcare"="hospital"]'],
    "diagnostic centre": [
        '["amenity"="doctors"]["healthcare:speciality"="diagnostic"]',
        '["healthcare"="diagnostic"]',
    ],
    "diagnostic center": ['["healthcare"="diagnostic"]'],
    "cafe": ['["amenity"="cafe"]'],
}

# Generic fallback: try amenity=, shop=, craft=, office= with the category word
_GENERIC_TAG_KEYS = ("amenity", "shop", "craft", "office", "leisure")

_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _geo_area_filter(city: str, state: str) -> str:
    """Overpass area selector for a named city.

    admin_level is deliberately NOT constrained. It was pinned to "8" on the
    assumption that cities are admin_level 8, which is true in the US and false
    almost everywhere else. In OpenStreetMap's own data:

        Pune     admin_level=5     (5 elements at level 8, 0 at 5 or 6 or 7)
        Indore   admin_level=5
        Nagpur   admin_level=5
        Surat    admin_level=8     (the exception that hid the bug)

    So pinning to 8 returned zero areas for every Indian city except the rare
    one that happened to match, and the lead finder reported an empty pipeline
    rather than an error. Matching on name + boundary works in both countries
    and does not need a country-specific table.

    Kept deliberately light: name + boundary matches fast; a state
    disambiguation regex made Overpass time out. State is carried in the lead
    metadata instead of the query.
    """
    city_esc = city.replace('"', "")
    return f'area["name"="{city_esc}"]["boundary"="administrative"]->.a;'


def _overpass_query(category: str, city: str, state: str, limit: int) -> str:
    tagsets = _CATEGORY_TAGS.get(category.strip().lower())
    if not tagsets:
        word = category.strip().lower().replace('"', "")
        tagsets = [f'["{k}"="{word}"]' for k in _GENERIC_TAG_KEYS]
    body = _geo_area_filter(city, state)
    # One union block, newlines, no trailing semicolon on the closing paren.
    # This is the only shape Overpass QL accepts for OR: emitting each tagset as
    # its own standalone "(nwr[..](area.a););" statement silently discarded them
    # all, so every category with more than one tagset returned zero leads.
    # Dentist worked purely because it happened to have exactly one.
    union = "\n  ".join(f"nwr{ts}(area.a);" for ts in tagsets)
    return (f"[out:json][timeout:25];\n{body}\n(\n  {union}\n);\n"
            f"out center tags {int(limit)};")


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


# Dialing prefixes for a bare 10-digit local number. The agency sells into
# Indian tier-1/2 cities, and "+1" is the United States country code, so a
# Pune clinic's 9425715707 was being stored as "+1-9425715707" - a number that
# cannot be dialled anywhere. Numbers that already carry a "+" or a leading 0
# are left alone.
_COUNTRY_PREFIX = {
    "in": "+91", "india": "+91",
    "us": "+1", "usa": "+1",
}


def _country_prefix(country: str) -> str:
    return _COUNTRY_PREFIX.get((country or "").strip().lower(), "+91")


def _normalise_phone(phone: str, country: str) -> str:
    """Put a bare local number into international form.

    A bare 10-digit number is country-dependent, so the prefix has to come from
    the country rather than being hardcoded to the US.
    """
    phone = (phone or "").strip()
    if not phone or phone.startswith("+"):
        return phone
    digits = phone.replace("-", "").replace(" ", "")
    if re.match(r"^\d{10}$", digits):
        return f"{_country_prefix(country)}-{phone}"
    return phone


def _osm_to_lead(el: dict, category: str, city: str, state: str,
                 country: str = "in") -> dict | None:
    """Convert an OSM element into the shared normalized lead shape."""
    t = el.get("tags") or {}
    name = (t.get("name") or "").strip()
    if not name:
        return None
    phone = (t.get("phone") or t.get("contact:phone") or t.get("phone:contact") or "").strip()
    phone = _normalise_phone(phone, country)
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
    country: str = "in",
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
        lead = _osm_to_lead(el, category, city, state, country)
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
