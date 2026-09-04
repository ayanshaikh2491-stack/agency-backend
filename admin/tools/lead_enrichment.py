"""Lead email enrichment for SBA — powered by Bing RSS search + domain trust.

The SBA agent calls find_lead_email(name, city, category, website) via tool
dispatch, and the autopilot uses it to auto-fill candidate leads.

Why "domain trust": naive enrichment crawls whatever domains Bing returns,
which produced junk like bd@grubhub.com (GrubHub listing page), stories@wikihow.com
(a how-to article), info@midtownatl.com (a visitor site). This module only
trusts a result domain after (1) it is not a known platform/aggregator/media
domain, (2) its homepage actually mentions the lead's business name, and
(3) the email itself passes strict validity rules. If the caller supplies the
lead's own scraped website, that domain is crawled first and is the only one
that can override a search result.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Hard ceiling for one enrichment call. The autopilot wraps find_lead_email in
# asyncio.wait_for(ENRICH_TIMEOUT_SECONDS=45s), but a thread can't be cancelled:
# the crawl would keep burning CPU/bandwidth after the wrapper times out. This
# budget is the *internal* deadline: every Bing query, homepage check and page
# crawl shrinks its own request timeout as the budget drains, and aborts early.
# Kept below the wrapper so enrichment returns "" (no email) instead of the
# pass logging a timeout for a domain that just drip-feeds bytes.
ENRICH_BUDGET_SECONDS = float(os.environ.get("SBA_ENRICH_BUDGET_SECONDS", "38"))
# Connect timeout is capped hard (3.05s) so a dead/unroutable host can't eat
# the budget; the read timeout shrinks with the remaining budget.
_CONNECT_TIMEOUT = 3.05


# Requests applies a single timeout to *each* read chunk, not the whole
# request, so a slow site can hang for minutes. The deadline helpers below
# make every call self-terminate within the overall budget.
def _now() -> float:
    return time.monotonic()


def _expired(deadline: float | None) -> bool:
    return bool(deadline) and _now() >= deadline


def _remaining(deadline: float | None, cap: float) -> float:
    """Per-request timeout = min(cap, budget left), 0 once exhausted."""
    if not deadline:
        return cap
    left = deadline - _now()
    if left <= 0.2:
        return 0.0
    return min(cap, left - 0.1)


def _sleep(seconds: float, deadline: float | None) -> None:
    """Sleep without overshooting the enrichment deadline."""
    if seconds <= 0 or _expired(deadline):
        return
    time.sleep(min(seconds, _remaining(deadline, seconds)))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# ── Strict validity (mirrors sba_autopilot._is_valid_lead_email) ─────────
_JUNK_TLDS = {
    "js", "css", "png", "jpg", "jpeg", "gif", "svg", "webp", "html", "htm",
    "json", "xml", "php", "local", "internal", "invalid", "test", "example",
    "localhost", "donotuse", "company", "home", "lan", "intranet",
}
_GOV_EDU_TLDS = ("gov", "edu", "mil")
_BAD_EMAIL_PAT = re.compile(
    r"(example|sentry|wixpress|yourdomain|email\.com|@2x|\.\.|donotreply|"
    r"no-reply|noreply|@sentry|@wix|@godaddy|@cloudflare|@googleusercontent|"
    r"u003e|u003c|%3e|%3c|&gt;|&lt;)",
    re.I,
)
# Prefixes that NEVER name a human decision maker — rejected even when the
# address was found on the business's own verified page (noreply@/unsubscribe@/
# careers@ can never be a cold-email target).
_HARD_JUNK_PREFIXES = (
    "press@", "media@", "pr@", "careers@", "jobs@", "hr@", "noreply@",
    "no-reply@", "donotreply@", "unsubscribe@", "newsletter@", "mailer@",
    "bounce@", "postmaster@", "webmaster@", "abuse@", "automated@",
    "editor@", "tips@", "newsroom@", "submissions@", "stories@",
    "advertise@", "partners@", "founders@", "team@", "privacy@", "legal@",
    "addressadmissions@", "recreationdepartment@", "parkingservices@",
    "mychartsupport@", "subscriptionsupport@", "guest@", "stop@", "care@",
    "billing@", "name@", "feedback@", "hi@", "user@",
)
# Generic front-desk prefixes. For a local small business info@/contact@/hello@
# IS the owner's inbox, so these are allowed ONLY when the caller proved the
# address came from the business's own verified page (allow_consumer=True). In
# an unverified context (a scraper listing's info@) they stay junk.
_GENERIC_PREFIXES = (
    "support@", "info@", "contact@", "admin@", "hello@", "help@",
    "sales@", "service@", "office@", "dispatch@", "bookings@",
    "enquiries@", "inquiries@", "reservations@", "scheduling@", "mail@",
)
# Local parts that scream "automated/aggregator", not a human decision maker
# (ad-alerts@, notifications@, alert@, ...).
_JUNK_LOCAL_PAT = re.compile(
    r"(alert|notif|noreply|no-?reply|donotreply|automated|mailer|bounce|"
    r"postmaster|webmaster|abuse|marketing@|promo@|deals@|offers@)",
    re.I,
)
# A school/university/campus domain is not a small-business decision maker.
_SCHOOL_DOMAIN_MARKERS = (
    "school", "academy", "k12", "college", "univ", "campus", "faculty",
    "alumni", "edu.",
)
# Platform / listing / directory / media domains. A result on one of these is
# a listing page for the business, NOT the business website. Crawling it is
# exactly how bd@grubhub.com and stories@wikihow.com got saved.
SKIP_DOMAINS = {
    # Directories & listings
    "google.com", "google.co.in", "maps.google.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "linkedin.com", "yelp.com",
    "yellowpages.com", "yellowpages.ca", "bing.com", "duckduckgo.com",
    "youtube.com", "pinterest.com", "tripadvisor.com", "angieslist.com",
    "bbb.org", "houzz.com", "thumbtack.com", "nextdoor.com", "foursquare.com",
    "manta.com", "superpages.com", "whitepages.com", "redfin.com", "zillow.com",
    "realtor.com", "groupon.com", "birdeye.com", "local.com", "hotfrog.com",
    "cylex.us.com", "merchantcircle.com", "citysearch.com", "mapquest.com",
    "chamberofcommerce.com", "bizjournals.com", "zmenu.com", "remax.com",
    "houseofnames.com", "find-us-here.com", "merchantcircle.com", "n49.com",
    "brownbook.net", "tupalo.com", "cybo.com", "opendi.com", "yellow.place",
    "nicelocal.com", "yably.com", "infobel.com", "kompass.com", "europages.com",
    "theknot.com", "weddingwire.com", "thumbtack.com", "porch.com",
    "homeadvisor.com", "angie.com", "buildzoom.com", "cnet.com", "cityfos.com",
    # Food delivery / reservation platforms
    "grubhub.com", "doordash.com", "ubereats.com", "opentable.com",
    "resy.com", "eatstreet.com", "postmates.com", "seamless.com",
    "chownow.com", "toasttab.com", "squareup.com", "square.site",
    "flexbook.com", "booksy.com", "schedulicity.com", "vagaro.com",
    "mindbodyonline.com", "acuityscheduling.com", "calendly.com",
    "styleseat.com", "opencare.com", "zocdoc.com", "healthgrades.com",
    "webmd.com", "vitals.com", "medicare.gov", "care.com",
    # Consumer / media / editorial sites
    "wikihow.com", "zhihu.com", "biblegateway.com", "salon.com",
    "indianexpress.com", "rent.com", "joinbelle.com", "repeallouisville.com",
    "salemwebnetwork.com", "the-uptown.com", "52pojie.cn", "roamartists.com",
    "sa-comms.com", "whichiscorrect.com", "central.com", "volarerevere.com",
    "tnvacation.com", "midtownatl.com", "lenoxtools.com", "icstucson.org",
    "wiltondentalassoc.com", "districtgov.org", "wikipedia.org", "quora.com",
    "gravatar.com", "vimeo.com", "yelp.com", "forbes.com", "usatoday.com",
    "newsweek.com", "patch.com", "nextdoor.com", "onlyinyourstate.com",
    # Big brands / parents / placeholder hosts
    "company.com", "yourdomain.com", "sentry.io", "wixpress.com",
    "godaddy.com", "domainsbyproxy.com", "googleusercontent.com",
    "wix.com", "squarespace.com", "godaddysites.com", "webs.com",
    "weebly.com", "wordpress.com", "blogspot.com", "tumblr.com",
    "starbucks.com", "mcdonalds.com", "homedepot.com", "lowes.com",
    "walmart.com", "costco.com", "target.com", "amazon.com", "ebay.com",
    "etsy.com", "craigslist.org", "yellowbot.com", "cylex.com", "yellowee.com",
    "spoke.com", "zoominfo.com", "dnb.com", "linkedin.com", "glassdoor.com",
    "indeed.com", "monster.com", "careerbuilder.com",
    # Consumer mailboxes: never a business decision maker's cold-email target.
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "proton.me",
    "protonmail.com", "zoho.com", "qq.com", "163.com", "126.com",
    "tutanota.com", "gmx.com", "gmx.net", "mail.com", "yandex.com",
    "yandex.ru", "fastmail.com", "hey.com", "pm.me", "mail.ru",
    "rediffmail.com", "bol.com.br", "uol.com.br", "web.de", "orange.fr",
    "wanadoo.fr", "libero.it", "virgilio.it", "t-online.de", "btinternet.com",
    "sky.com", "virginmedia.com", "cox.net", "verizon.net", "att.net",
    "sbcglobal.net", "comcast.net", "charter.net", "earthlink.net",
    "frontiernet.net", "roadrunner.com", "optimum.net", "suddenlink.net",
    # Big media/brand sites. These can match short name tokens by accident
    # (e.g. "Paw Wow" vs Paramount's "Paw Patrol") and should never be crawled
    # as a lead's business website.
    "pluto.tv", "paramountplus.com", "paramount.com", "netflix.com",
    "hulu.com", "disneyplus.com", "hbomax.com", "max.com", "peacocktv.com",
    "apple.com", "spotify.com", "pandora.com",
    # Big-bank/aggregator domains that are never a local small business.
    "wellsfargo.com", "wellsfargoadvisors.com",
}
# Consumer / free mailboxes. These are the *business's own* mailbox for many
# local small businesses (a roofer with no domain runs on
# triangleroofingnola@gmail.com). They are only accepted when the address was
# found on the business's own verified page (own website or a homepage that
# mentions the business name). Unverified consumer addresses are still junk.
_CONSUMER_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "proton.me",
    "protonmail.com", "zoho.com", "qq.com", "163.com", "126.com",
    "tutanota.com", "gmx.com", "gmx.net", "mail.com", "yandex.com",
    "yandex.ru", "fastmail.com", "hey.com", "pm.me", "mail.ru",
    "rediffmail.com", "bol.com.br", "uol.com.br", "web.de", "orange.fr",
    "wanadoo.fr", "libero.it", "virgilio.it", "t-online.de", "btinternet.com",
    "sky.com", "virginmedia.com", "cox.net", "verizon.net", "att.net",
    "sbcglobal.net", "comcast.net", "charter.net", "earthlink.net",
    "frontiernet.net", "roadrunner.com", "optimum.net", "suddenlink.net",
}
# Common business-name filler words — not distinctive enough to match a
# homepage against (e.g. "plumbing" matches every plumber's site).
_BUSINESS_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "at", "for", "llc", "inc", "co",
    "company", "corporation", "corp", "group", "services", "service", "solutions",
    "plumbing", "hvac", "heating", "cooling", "air", "electric", "electrical",
    "roofing", "roofer", "landscaping", "landscape", "painting", "painter",
    "cleaning", "handyman", "repair", "remodeling", "construction", "contractors",
    "contractor", "salon", "spa", "dental", "dentistry", "dentist", "clinic",
    "medical", "auto", "cars", "automotive", "mechanic", "barber", "barbing",
    "studio", "design", "designs", "boutique", "shop", "store", "market",
    "kitchen", "bath", "restaurant", "cafe", "coffee", "pizza", "bar", "grill",
    "locksmith", "moving", "movers", "pest", "control", "exterminator",
    "lawn", "care", "tree", "towing", "garage", "insurance", "financial",
    "accounting", "tax", "legal", "law", "attorney", "realty", "realtor",
    "homes", "properties", "property", "estates", "travel", "tours", "hotel",
    "motel", "inn", "lodging", "pet", "grooming", "veterinary", "vet", "fitness",
    "gym", "yoga", "beauty", "nails", "lashes", "massage", "transportation",
    "logistics", "shipping", "supplies", "materials", "products", "systems",
}


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    for p in (
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"),
        "/home/ubuntu/sba-backend/.env",
    ):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(key + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return default


def _supabase_config() -> tuple[str, str] | None:
    url = _env("SUPABASE_URL", "http://18.213.66.136:8050").rstrip("/")
    key = _env("SUPABASE_SERVICE_KEY", "")
    if not key:
        return None
    return url, key


def _is_valid_email(email: str, allow_consumer: bool = False) -> bool:
    e = (email or "").strip().lower()
    if not e or not EMAIL_RE.fullmatch(e):
        return False
    if _BAD_EMAIL_PAT.search(e):
        return False
    if "example.com" in e:
        return False
    local, domain = e.split("@", 1)[0], e.split("@", 1)[1]
    tld = domain.rsplit(".", 1)[-1]
    if tld in _JUNK_TLDS:
        return False
    if domain.endswith(_GOV_EDU_TLDS):
        return False
    if any(m in domain for m in _SCHOOL_DOMAIN_MARKERS):
        return False
    if domain in SKIP_DOMAINS:
        if allow_consumer and domain in _CONSUMER_DOMAINS:
            pass
        else:
            return False
    for prefix in _HARD_JUNK_PREFIXES:
        if e.startswith(prefix):
            return False
    # Generic front-desk prefixes are fine on the business's OWN verified page
    # (allow_consumer=True), but are junk in an unverified scrape. On consumer
    # mail domains (gmail/yahoo/...) a role account like info@ can never be
    # proven to belong to this specific business, so it stays junk even when
    # the caller says consumer mail is otherwise acceptable.
    if not allow_consumer or domain in _CONSUMER_DOMAINS:
        for prefix in _GENERIC_PREFIXES:
            if e.startswith(prefix):
                return False
    if _JUNK_LOCAL_PAT.search(local):
        return False
    return True


def _clean_domain(url: str) -> str:
    try:
        dom = urllib.parse.urlparse(url).netloc.lower()
        return dom[4:] if dom.startswith("www.") else dom
    except Exception:
        return ""


def _name_tokens(name: str) -> list[str]:
    """Distinctive searchable tokens from a business name (lowercase)."""
    out = []
    for tok in re.findall(r"[a-z0-9]+", (name or "").lower()):
        if len(tok) >= 4 and tok not in _BUSINESS_STOPWORDS:
            out.append(tok)
    return out


# Words that add no identity to the full-name phrase (legal suffixes and pure
# filler). Unlike _BUSINESS_STOPWORDS these are dropped from the PHRASE too, so
# "Cooper Plumbing & Air LLC" matches a site titled "Cooper Plumbing & Air".
_PHRASE_FILLERS = {
    "a", "an", "the", "and", "of", "for", "at", "or",
    "llc", "inc", "co", "corp", "ltd", "company", "dba",
}


def _name_phrase(name: str) -> str:
    """Normalized contiguous business-name phrase, e.g. 'J & E Express Auto'
    -> 'j e express auto'. Used when distinctive tokens are too weak to match
    against (short names like 'Paw Wow' would otherwise let ANY page pass)."""
    return " ".join(
        w for w in re.findall(r"[a-z0-9]+", (name or "").lower())
        if len(w) >= 2 and w not in _PHRASE_FILLERS
    )


def _text_matches_tokens(text: str, tokens: list[str], phrase: str = "") -> bool:
    """Require strong evidence the page IS the business.

    With 2+ distinctive name tokens (e.g. "Midtown Smiles"), ALL must appear
    so a same-name other business ("Midtown Comics") can't pass. With 0-1
    distinctive tokens (short names like "Paw Wow", "Ace Plumbing"), substring
    matching is far too loose: a media page can mention "paw"/"wow" separately
    (Paramount's "Paw Patrol") or a generic word alone. Those names only pass
    when the FULL contiguous name phrase appears on the page.
    """
    t = (text or "").lower()
    if len(tokens) >= 2:
        return all(tok in t for tok in tokens)
    # 0 or 1 distinctive token -> the contiguous full-name phrase must appear.
    # Normalize the text the same way as the phrase so '&', '-', '/' etc. don't
    # break "cooper plumbing air" vs "Cooper Plumbing & Air".
    if not phrase:
        return False
    nt = " ".join(re.findall(r"[a-z0-9]+", t))
    return phrase in nt


def bing_search(query: str, count: int = 10, deadline: float | None = None) -> list[dict[str, str]]:
    """Bing RSS search. Returns [{url, title, desc}]. Self-terminates within
    the enrichment budget (each attempt shrinks its request timeout)."""
    for attempt in range(3):
        t = _remaining(deadline, 15)
        if t <= 0:
            break
        try:
            r = SESSION.get(
                "https://www.bing.com/search",
                params={"format": "rss", "q": query, "count": count},
                timeout=(min(_CONNECT_TIMEOUT, t), t),
            )
            if r.status_code != 200:
                _sleep(2, deadline)
                continue
            soup = BeautifulSoup(r.text, "xml")
            out = []
            for item in soup.find_all("item"):
                title = item.title.get_text() if item.title else ""
                link = item.link.get_text() if item.link else ""
                desc = item.description.get_text() if item.description else ""
                out.append({"url": link, "title": title, "desc": desc})
            return out
        except requests.RequestException:
            _sleep(2, deadline)
    return []


def _extract_emails_from_text(text: str | None, allow_consumer: bool = False) -> set[str]:
    if not text:
        return set()
    found = set()
    for m in EMAIL_RE.findall(text):
        if _is_valid_email(m, allow_consumer=allow_consumer):
            found.add(m.lower())
    return found


def _homepage_check(domain: str, tokens: list[str], name: str = "", timeout: int = 10,
                    deadline: float | None = None) -> bool:
    """True if the domain's homepage mentions the business name.

    This is the anti-junk gate: grubhub.com/wikihow.com/midtownatl.com never
    mention "Cooper Plumbing" (or whatever the lead is), so they're rejected
    as crawl targets and their emails are never collected. Short names with no
    distinctive tokens ("Paw Wow") only pass when their FULL name phrase is on
    the page, so a media site that coincidentally mentions "paw"/"wow"
    (Paramount's "Paw Patrol") can't slip through. Pages that look like a
    school, article, or portal (non-business) are also rejected even when a
    single name token coincidentally appears (Carroll Family Dental vs
    carrollschool.org).
    """
    _NON_BUSINESS_MARKERS = (
        "school", "academy", "university", "college", "campus", "alumni",
        "wikipedia", "help center", "help centre", "frequently asked",
        "recipes", "how to", "news article", "blog post", "faq",
        "county", "government", "municipal", "town of", "city of",
        "foundation", "nonprofit", "non-profit", "chamber of commerce",
        "association", "ministry", "church", "congregation", "parish",
    )
    if not tokens and not name:
        return False  # nothing to match against -> never trust a random domain
    phrase = _name_phrase(name)
    for scheme in ("https", "http"):
        t = _remaining(deadline, timeout)
        if t <= 0:
            return False
        try:
            r = SESSION.get(f"{scheme}://{domain}/", timeout=(min(_CONNECT_TIMEOUT, t), t),
                            allow_redirects=True)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.get_text() if soup.title else ""
            desc = ""
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                desc = meta["content"]
            h1 = soup.find("h1")
            h1t = h1.get_text() if h1 else ""
            page_text = title + " " + desc + " " + h1t
            low = page_text.lower()
            if any(m in low for m in _NON_BUSINESS_MARKERS):
                return False
            if _text_matches_tokens(page_text, tokens, phrase):
                return True
            return False
        except requests.RequestException:
            continue
        _sleep(0.3, deadline)
    return False


def _crawl_domain(domain: str, timeout: int = 12, allow_consumer: bool = False,
                  deadline: float | None = None) -> set[str]:
    """Fetch homepage + contact/about pages and extract emails. Stops at the
    first page that yields a valid email and at the enrichment deadline."""
    found = set()
    for p in ("/", "/contact", "/contact-us", "/about", "/about-us"):
        if _expired(deadline):
            break
        for scheme in ("https", "http"):
            t = _remaining(deadline, timeout)
            if t <= 0:
                break
            u = f"{scheme}://{domain}{p}"
            try:
                r = SESSION.get(u, timeout=(min(_CONNECT_TIMEOUT, t), t), allow_redirects=True)
                if r.status_code != 200:
                    continue
                found |= _extract_emails_from_text(r.text, allow_consumer=allow_consumer)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.select('a[href^="mailto:"]'):
                    m = a["href"][7:].split("?")[0]
                    if _is_valid_email(m, allow_consumer=allow_consumer):
                        found.add(m.lower())
                if found:
                    return found
                break
            except requests.RequestException:
                continue
        _sleep(0.4, deadline)
    return found


def find_lead_email(
    name: str,
    city: str = "",
    category: str = "",
    website: str = "",
    patch_supabase: bool = True,
    supabase_id: Any = None,
) -> dict[str, Any]:
    """Main entry: find a *verified* email for a lead.

    Order of trust:
      1. The lead's own website (from the scraper card), if given.
      2. Bing result domains whose homepage mentions the lead's name.
    Returns dict with email + sources. Junk domains are never crawled.
    """
    name = (name or "").strip().strip('"')
    city = (city or "").strip()
    category = (category or "").strip()

    if not name:
        return {"email": "", "domains": [], "all_emails": [],
                "sources": [], "patched": False}

    tokens = _name_tokens(name)

    # One internal deadline for the whole enrichment. Every nested request
    # (Bing, homepage check, page crawl) shrinks its own timeout from this,
    # so a slow domain can never eat the entire autopilot pass.
    deadline = _now() + ENRICH_BUDGET_SECONDS

    emails: set[str] = set()
    crawled: list[str] = []
    search_sources: list[str] = []

    # 1) Own website — highest trust, crawl first. Consumer mailboxes (gmail,
    #    yahoo, ...) are the real business mailbox for many local businesses
    #    with no domain, so they are accepted here and on any page whose
    #    homepage is verified to mention the business name below.
    own_domain = _clean_domain(website or "")
    if own_domain and own_domain not in SKIP_DOMAINS:
        found = _crawl_domain(own_domain, allow_consumer=True, deadline=deadline)
        if found:
            emails |= found
            crawled.append(own_domain)
        search_sources.append(website)

    # 2) Bing search with homepage-name verification.
    if not emails and not _expired(deadline):
        queries = []
        if city and category:
            queries.append(f'"{name}" {city} {category}')
        if city:
            queries.append(f'"{name}" {city}')
        queries.append(f'"{name}" email')
        for q in queries[:3]:
            if _expired(deadline):
                break
            results = bing_search(q, deadline=deadline)
            _sleep(1.0, deadline)
            for res in results:
                if _expired(deadline):
                    break
                url = res.get("url", "")
                dom = _clean_domain(url)
                if not dom or dom in SKIP_DOMAINS or dom in crawled:
                    continue
                # Anti-junk gate: only crawl domains that plausibly ARE the
                # business (homepage mentions the name). This is what kills
                # grubhub.com, wikihow.com, midtownatl.com, pluto.tv, etc.
                if not _homepage_check(dom, tokens, name, deadline=deadline):
                    continue
                found = _crawl_domain(dom, allow_consumer=True, deadline=deadline)
                if found:
                    emails |= found
                    crawled.append(dom)
                    search_sources.append(url)
                    break
            if emails:
                break

    best = ""
    provenance = ""
    if emails:
        best = max(emails, key=lambda e: _score_email(e, own_domain))
        edom = best.split("@", 1)[1].lower()
        if own_domain and (edom == own_domain or edom.endswith("." + own_domain)):
            provenance = "own_domain"
        elif edom in _CONSUMER_DOMAINS:
            provenance = "consumer"
        else:
            provenance = "homepage"

    patched = False
    if best and patch_supabase and supabase_id is not None:
        patched = patch_email(supabase_id, best, provenance)

    return {
        "email": best,
        "provenance": provenance,
        "domains": crawled[:3],
        "all_emails": sorted(emails),
        "sources": search_sources[:3],
        "patched": patched,
    }


def _score_email(email: str, own_domain: str) -> int:
    """Rank candidates: own-domain and person-like local parts win.

    Generic prefixes (info@/contact@) are blocked by validity rules anyway, so
    never prefer them; a plausible name (john@, service@diy) is a better target.
    """
    local = email.split("@", 1)[0].lower()
    edom = email.split("@", 1)[1].lower()
    score = 0
    if own_domain and edom == own_domain:
        score += 5
    elif own_domain and edom.endswith("." + own_domain):
        score += 4
    if any(k in local for k in ("info", "contact", "hello", "enquiry", "office", "admin", "mail")):
        score += 1  # valid but weak (rare: these are usually filtered earlier)
    elif re.search(r"[a-z]", local) and local not in ("info", "contact", "sales", "support", "service"):
        score += 2  # person-like or business-specific local part
    if len(local) >= 5:
        score += 1
    return score


def patch_email(lead_id: Any, email: str, provenance: str = "") -> bool:
    cfg = _supabase_config()
    if not cfg:
        return False
    url, key = cfg
    payload: dict[str, Any] = {"email": email}
    if provenance:
        payload["email_provenance"] = provenance
    try:
        r = SESSION.patch(
            f"{url}/rest/v1/leads?id=eq.{lead_id}",
            json=payload,
            headers={
                "apikey": key,
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            timeout=15,
        )
        return r.status_code in (200, 201, 204)
    except requests.RequestException:
        return False


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    n = args[0] if args else input("Lead name: ")
    c = args[1] if len(args) > 1 else ""
    cat = args[2] if len(args) > 2 else ""
    site = args[3] if len(args) > 3 else ""
    res = find_lead_email(n, c, cat, website=site, patch_supabase=False)
    print(json.dumps(res, indent=2, default=str))
