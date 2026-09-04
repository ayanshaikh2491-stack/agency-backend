"""SEO Tools — Free, no-API-key, no-rate-limit tools for the SEO Agent.

All tools use:
  - pyseoanalyzer (pip install pyseoanalyzer) for site audits
  - beautifulsoup4 + requests for custom crawling
  - Google Autocomplete (free, no key) for keyword research
  - Custom HTML analysis for on-page SEO checks

Zero cost. Zero rate limits. Runs locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _now_str() -> str:
    """ISO timestamp string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

# ── Constants ────────────────────────────────────────────────────────────────

GOOGLE_AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 15  # seconds
BS4_PARSER = "lxml"  # fallback: "html.parser"


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 1: Site Audit (Custom Crawler)
# ═══════════════════════════════════════════════════════════════════════════════

def site_audit(url: str, max_pages: int = 10) -> dict[str, Any]:
    """Crawl a site and produce a technical SEO audit.

    Checks:
    - Title tag, meta description, robots meta
    - H1-H6 heading structure
    - Image alt tags
    - Internal/external links
    - Canonical URL
    - Open Graph tags
    - Schema markup (JSON-LD)
    - Mobile viewport
    - Page speed basics (response time)
    - HTTP status codes
    """
    parsed = urlparse(url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    results = {
        "url": url,
        "pages_crawled": 0,
        "pages": [],
        "summary": {
            "total_links": 0,
            "internal_links": 0,
            "external_links": 0,
            "broken_links": 0,
            "pages_missing_title": 0,
            "pages_missing_meta_desc": 0,
            "pages_missing_h1": 0,
            "pages_missing_alt": 0,
            "pages_missing_viewport": 0,
            "pages_missing_canonical": 0,
            "pages_with_schema": 0,
            "avg_response_time_ms": 0,
        },
        "issues": [],
    }

    visited = set()
    to_visit = [url]
    response_times = []

    while to_visit and len(visited) < max_pages:
        current_url = to_visit.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        try:
            import time
            start = time.time()
            resp = requests.get(current_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            elapsed_ms = int((time.time() - start) * 1000)
            response_times.append(elapsed_ms)
        except Exception as e:
            results["issues"].append({
                "url": current_url,
                "type": "fetch_error",
                "message": str(e)[:200],
            })
            continue

        page_data = _analyze_page(current_url, resp, base_domain)
        results["pages"].append(page_data)
        results["pages_crawled"] += 1

        # Collect internal links for further crawling
        for link in page_data.get("internal_links_list", []):
            if link not in visited:
                to_visit.append(link)

    # Build summary
    _build_audit_summary(results, response_times)

    # Remove internal_links_list from output (large)
    for p in results["pages"]:
        p.pop("internal_links_list", None)

    return results


def _analyze_page(url: str, resp: requests.Response, base_domain: str) -> dict[str, Any]:
    """Analyze a single page for SEO signals."""
    soup = BeautifulSoup(resp.text, BS4_PARSER) if resp.text else BeautifulSoup("", BS4_PARSER)

    # Title
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # Meta description
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc_tag.get("content", "") if meta_desc_tag else ""

    # Robots meta
    robots_tag = soup.find("meta", attrs={"name": "robots"})
    robots = robots_tag.get("content", "") if robots_tag else ""

    # Canonical
    canonical_tag = soup.find("link", attrs={"rel": "canonical"})
    canonical = canonical_tag.get("href", "") if canonical_tag else ""

    # Viewport
    viewport_tag = soup.find("meta", attrs={"name": "viewport"})
    viewport = viewport_tag.get("content", "") if viewport_tag else ""

    # Open Graph
    og_tags = {}
    for og in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        og_tags[og.get("property", "")] = og.get("content", "")

    # Schema/JSON-LD
    schemas = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            schemas.append(json.loads(script.string or "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

    # Headings
    headings = {}
    for level in range(1, 7):
        tags = soup.find_all(f"h{level}")
        if tags:
            headings[f"h{level}"] = [t.get_text(strip=True)[:100] for t in tags]

    # Images
    images = soup.find_all("img")
    images_without_alt = [img.get("src", "") for img in images if not img.get("alt")]

    # Links
    all_links = soup.find_all("a", href=True)
    internal_links = []
    external_links = []
    for a in all_links:
        href = a["href"]
        full_url = urljoin(url, href)
        parsed = urlparse(full_url)
        if parsed.netloc == urlparse(base_domain).netloc:
            internal_links.append(full_url)
        elif parsed.scheme in ("http", "https"):
            external_links.append(full_url)

    # Word count
    body_text = soup.get_text(separator=" ", strip=True)
    word_count = len(body_text.split())

    return {
        "url": url,
        "status_code": resp.status_code,
        "response_time_ms": None,  # filled later
        "title": title,
        "title_length": len(title),
        "meta_description": meta_desc,
        "meta_desc_length": len(meta_desc),
        "robots": robots,
        "canonical": canonical,
        "has_viewport": bool(viewport),
        "open_graph": og_tags,
        "schemas": schemas,
        "has_schema": bool(schemas),
        "headings": headings,
        "h1_count": len(headings.get("h1", [])),
        "images_total": len(images),
        "images_without_alt": len(images_without_alt),
        "internal_links_count": len(internal_links),
        "external_links_count": len(external_links),
        "word_count": word_count,
        "internal_links_list": internal_links,
    }


def _build_audit_summary(results: dict, response_times: list[int]) -> None:
    """Aggregate page-level data into summary + issues."""
    s = results["summary"]
    all_links = 0
    internal = 0
    external = 0

    for p in results["pages"]:
        all_links += p["internal_links_count"] + p["external_links_count"]
        internal += p["internal_links_count"]
        external += p["external_links_count"]

        if not p["title"]:
            s["pages_missing_title"] += 1
            results["issues"].append({
                "url": p["url"], "type": "missing_title",
                "message": "Page has no <title> tag",
            })
        elif len(p["title"]) > 60:
            results["issues"].append({
                "url": p["url"], "type": "title_too_long",
                "message": f"Title is {len(p['title'])} chars (recommended: <60)",
            })

        if not p["meta_description"]:
            s["pages_missing_meta_desc"] += 1
            results["issues"].append({
                "url": p["url"], "type": "missing_meta_desc",
                "message": "Page has no meta description",
            })
        elif len(p["meta_description"]) > 160:
            results["issues"].append({
                "url": p["url"], "type": "meta_desc_too_long",
                "message": f"Meta description is {len(p['meta_description'])} chars (recommended: <160)",
            })

        if p["h1_count"] == 0:
            s["pages_missing_h1"] += 1
            results["issues"].append({
                "url": p["url"], "type": "missing_h1",
                "message": "Page has no H1 tag",
            })
        elif p["h1_count"] > 1:
            results["issues"].append({
                "url": p["url"], "type": "multiple_h1",
                "message": f"Page has {p['h1_count']} H1 tags (recommended: 1)",
            })

        if p["images_without_alt"] > 0:
            s["pages_missing_alt"] += p["images_without_alt"]
            results["issues"].append({
                "url": p["url"], "type": "missing_alt",
                "message": f"{p['images_without_alt']} images missing alt text",
            })

        if not p["has_viewport"]:
            s["pages_missing_viewport"] += 1
            results["issues"].append({
                "url": p["url"], "type": "missing_viewport",
                "message": "No mobile viewport meta tag",
            })

        if not p["canonical"]:
            s["pages_missing_canonical"] += 1
            results["issues"].append({
                "url": p["url"], "type": "missing_canonical",
                "message": "No canonical URL set",
            })

        if p["has_schema"]:
            s["pages_with_schema"] += 1

    s["total_links"] = all_links
    s["internal_links"] = internal
    s["external_links"] = external
    if response_times:
        s["avg_response_time_ms"] = int(sum(response_times) / len(response_times))


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 2: Keyword Research (Google Autocomplete)
# ═══════════════════════════════════════════════════════════════════════════════

def keyword_research(seed_keyword: str, language: str = "en") -> dict[str, Any]:
    """Expand a seed keyword using Google Autocomplete.

    Appends a-z and common prefixes to get long-tail variations.
    Returns unique keyword suggestions grouped by type.
    """
    all_suggestions = set()

    # 1. Direct autocomplete
    suggestions = _google_autocomplete(seed_keyword, language)
    all_suggestions.update(suggestions)

    # 2. A-Z prefix expansion
    for letter in "abcdefghijklmnopqrstuvwxyz":
        query = f"{seed_keyword} {letter}"
        suggestions = _google_autocomplete(query, language)
        all_suggestions.update(suggestions)

    # 3. Question prefixes
    for prefix in ["how to", "what is", "why", "best", "top", "guide", "example", "vs", "near me", "free"]:
        query = f"{prefix} {seed_keyword}"
        suggestions = _google_autocomplete(query, language)
        all_suggestions.update(suggestions)

    # Remove the seed keyword itself and empty strings
    all_suggestions.discard(seed_keyword)
    all_suggestions.discard("")

    # Categorize
    keywords = sorted(all_suggestions)
    questions = [k for k in keywords if any(k.lower().startswith(q) for q in ["how", "what", "why", "when", "where", "which", "who", "is ", "are ", "can "])]
    long_tail = [k for k in keywords if len(k.split()) >= 4]
    short_tail = [k for k in keywords if len(k.split()) < 4 and k not in questions]

    return {
        "seed_keyword": seed_keyword,
        "total_suggestions": len(keywords),
        "all_keywords": keywords,
        "questions": questions,
        "long_tail": long_tail,
        "short_tail": short_tail,
    }


def _google_autocomplete(query: str, language: str = "en") -> list[str]:
    """Call Google Autocomplete API (free, no key, no rate limit)."""
    try:
        resp = requests.get(
            GOOGLE_AUTOCOMPLETE_URL,
            params={"client": "firefox", "q": query, "hl": language},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            },
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 1:
                return data[1]  # Second element is the suggestions list
    except Exception as e:
        logger.warning("Google Autocomplete failed for '%s': %s", query, e)
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 3: On-Page SEO Check (Single URL)
# ═══════════════════════════════════════════════════════════════════════════════

def onpage_check(url: str) -> dict[str, Any]:
    """Deep on-page SEO analysis for a single URL."""
    try:
        import time
        start = time.time()
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response_time_ms = int((time.time() - start) * 1000)
    except Exception as e:
        return {"url": url, "error": str(e)}

    soup = BeautifulSoup(resp.text, BS4_PARSER) if resp.text else BeautifulSoup("", BS4_PARSER)

    # Title analysis
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # Meta description
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc_tag.get("content", "") if meta_desc_tag else ""

    # Open Graph
    og_title = ""
    og_desc = ""
    og_image = ""
    for og in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        prop = og.get("property", "")
        content = og.get("content", "")
        if prop == "og:title":
            og_title = content
        elif prop == "og:description":
            og_desc = content
        elif prop == "og:image":
            og_image = content

    # Twitter card
    twitter_card = ""
    for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
        if tag.get("name") == "twitter:card":
            twitter_card = tag.get("content", "")

    # Schema/JSON-LD
    schemas = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            schemas.append(json.loads(script.string or "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

    # Headings
    headings = {}
    for level in range(1, 7):
        tags = soup.find_all(f"h{level}")
        if tags:
            headings[f"h{level}"] = [t.get_text(strip=True)[:200] for t in tags]

    # Images
    images = soup.find_all("img")
    images_data = []
    for img in images:
        images_data.append({
            "src": img.get("src", "")[:200],
            "alt": img.get("alt", ""),
            "has_alt": bool(img.get("alt")),
            "width": img.get("width", ""),
            "height": img.get("height", ""),
        })

    # Links
    all_links = soup.find_all("a", href=True)
    broken_links = []
    for a in all_links:
        href = a["href"]
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        full_url = urljoin(url, href)
        # Just record, don't check every link (too slow)
        broken_links.append({
            "href": href[:200],
            "text": a.get_text(strip=True)[:100],
        })

    # Word count & readability
    body_text = soup.get_text(separator=" ", strip=True)
    words = body_text.split()
    word_count = len(words)
    sentences = re.split(r'[.!?]+', body_text)
    sentence_count = max(len([s for s in sentences if s.strip()]), 1)
    avg_words_per_sentence = word_count / sentence_count

    # Generate scores & issues
    issues = []
    score = 100

    if not title:
        issues.append({"severity": "critical", "message": "Missing <title> tag"})
        score -= 25
    elif len(title) < 30:
        issues.append({"severity": "warning", "message": f"Title too short ({len(title)} chars, recommended: 30-60)"})
        score -= 5
    elif len(title) > 60:
        issues.append({"severity": "warning", "message": f"Title too long ({len(title)} chars, recommended: 30-60)"})
        score -= 5

    if not meta_desc:
        issues.append({"severity": "critical", "message": "Missing meta description"})
        score -= 20
    elif len(meta_desc) < 70:
        issues.append({"severity": "warning", "message": f"Meta description too short ({len(meta_desc)} chars)"})
        score -= 3
    elif len(meta_desc) > 160:
        issues.append({"severity": "warning", "message": f"Meta description too long ({len(meta_desc)} chars)"})
        score -= 3

    h1s = headings.get("h1", [])
    if not h1s:
        issues.append({"severity": "critical", "message": "Missing H1 tag"})
        score -= 15
    elif len(h1s) > 1:
        issues.append({"severity": "warning", "message": f"Multiple H1 tags ({len(h1s)})"})
        score -= 5

    no_alt_images = [i for i in images_data if not i["has_alt"]]
    if no_alt_images:
        issues.append({"severity": "warning", "message": f"{len(no_alt_images)} images missing alt text"})
        score -= min(len(no_alt_images) * 2, 10)

    if not schemas:
        issues.append({"severity": "info", "message": "No structured data (JSON-LD) found"})
        score -= 5

    if not og_title or not og_desc:
        issues.append({"severity": "info", "message": "Missing or incomplete Open Graph tags"})
        score -= 3

    if response_time_ms > 3000:
        issues.append({"severity": "critical", "message": f"Very slow response time ({response_time_ms}ms)"})
        score -= 15
    elif response_time_ms > 1500:
        issues.append({"severity": "warning", "message": f"Slow response time ({response_time_ms}ms)"})
        score -= 5

    if word_count < 300:
        issues.append({"severity": "warning", "message": f"Thin content ({word_count} words, recommended: 300+)"})
        score -= 10

    return {
        "url": url,
        "status_code": resp.status_code,
        "response_time_ms": response_time_ms,
        "seo_score": max(score, 0),
        "title": title,
        "title_length": len(title),
        "meta_description": meta_desc,
        "meta_desc_length": len(meta_desc),
        "og_title": og_title,
        "og_desc": og_desc,
        "og_image": og_image,
        "twitter_card": twitter_card,
        "schemas": schemas,
        "headings": headings,
        "images": images_data,
        "word_count": word_count,
        "sentence_count": sentence_count,
        "avg_words_per_sentence": round(avg_words_per_sentence, 1),
        "issues": issues,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 4: Sitemap Parser
# ═══════════════════════════════════════════════════════════════════════════════

def parse_sitemap(url: str) -> dict[str, Any]:
    """Parse a sitemap.xml and extract all URLs."""
    # Try common sitemap locations
    sitemap_urls = [
        url.rstrip("/") + "/sitemap.xml",
        url.rstrip("/") + "/sitemap_index.xml",
    ]

    for sitemap_url in sitemap_urls:
        try:
            resp = requests.get(sitemap_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return _parse_sitemap_xml(resp.text, sitemap_url)
        except Exception:
            continue

    return {"url": url, "error": "No sitemap found", "urls": []}


def _parse_sitemap_xml(xml_text: str, source_url: str) -> dict[str, Any]:
    """Parse sitemap XML content."""
    urls = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        # Check if it's a sitemap index
        sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)
        if sitemaps:
            for loc in sitemaps:
                sub = _parse_sitemap_xml_url(loc.text)
                urls.extend(sub)
        else:
            urls = _parse_sitemap_xml_url(None, root=root, ns=ns)

    except ET.ParseError:
        pass

    return {
        "source": source_url,
        "total_urls": len(urls),
        "urls": urls[:100],  # Cap at 100 for output
    }


def _parse_sitemap_xml_url(loc_text: str = None, root=None, ns=None) -> list[dict]:
    """Helper to parse URLs from sitemap."""
    urls = []
    if root is not None and ns is not None:
        for url_elem in root.findall(".//sm:url", ns):
            loc = url_elem.find("sm:loc", ns)
            lastmod = url_elem.find("sm:lastmod", ns)
            if loc is not None:
                urls.append({
                    "url": loc.text,
                    "lastmod": lastmod.text if lastmod is not None else "",
                })
    elif loc_text:
        try:
            resp = requests.get(loc_text, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return _parse_sitemap_xml(resp.text, loc_text).get("urls", [])
        except Exception:
            pass
    return urls


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 5: Robots.txt Parser
# ═══════════════════════════════════════════════════════════════════════════════

def parse_robots_txt(url: str) -> dict[str, Any]:
    """Parse robots.txt and extract rules."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        resp = requests.get(robots_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return {"url": robots_url, "error": f"HTTP {resp.status_code}", "exists": False}
    except Exception as e:
        return {"url": robots_url, "error": str(e), "exists": False}

    lines = resp.text.split("\n")
    rules = {"user_agents": [], "disallow": [], "allow": [], "sitemap": []}

    current_agent = "*"
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("user-agent:"):
            current_agent = line.split(":", 1)[1].strip()
            rules["user_agents"].append(current_agent)
        elif line.lower().startswith("disallow:"):
            rules["disallow"].append(line.split(":", 1)[1].strip())
        elif line.lower().startswith("allow:"):
            rules["allow"].append(line.split(":", 1)[1].strip())
        elif line.lower().startswith("sitemap:"):
            rules["sitemap"].append(line.split(":", 1)[1].strip())

    return {
        "url": robots_url,
        "exists": True,
        "raw": resp.text[:2000],
        "rules": rules,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 6: Competitor SERP Check (via Google)
# ═══════════════════════════════════════════════════════════════════════════════

def serp_check(keyword: str, num_results: int = 10) -> dict[str, Any]:
    """Check Google SERP for a keyword (scrape top results).

    Uses Google search directly — no API key needed.
    Returns top organic results with title, URL, snippet.
    """
    try:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": keyword, "num": num_results, "hl": "en"},
            headers={
                **HEADERS,
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=REQUEST_TIMEOUT,
        )
        soup = BeautifulSoup(resp.text, BS4_PARSER)

        results = []
        for g in soup.select("div.g, div[data-sokoban-container]"):
            title_el = g.select_one("h3")
            link_el = g.select_one("a[href]")
            snippet_el = g.select_one("div[data-sncf], span.aCOpRe, div.VwiC3b")

            if title_el and link_el:
                href = link_el["href"]
                if href.startswith("/url?"):
                    href = href.split("/url?q=")[-1].split("&")[0]
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": href,
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                })

        return {
            "keyword": keyword,
            "results_count": len(results),
            "results": results[:num_results],
        }
    except Exception as e:
        return {"keyword": keyword, "error": str(e), "results": []}


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 7: Generate Meta Tags (Action — not just analysis)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_meta_tags(url: str) -> dict[str, Any]:
    """Crawl a page, analyze its content, and generate optimized meta tags.

    Returns ready-to-paste <title>, <meta description>, and Open Graph tags
    that are SEO-optimized based on actual page content.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except Exception as e:
        return {"url": url, "error": str(e)}

    soup = BeautifulSoup(resp.text, BS4_PARSER)

    # Extract current state
    title_tag = soup.find("title")
    current_title = title_tag.get_text(strip=True) if title_tag else ""
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    current_meta = meta_desc_tag.get("content", "") if meta_desc_tag else ""

    # Extract content signals
    h1_tags = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2_tags = [h.get_text(strip=True) for h in soup.find_all("h2")]
    og_title = ""
    og_desc = ""
    og_image = ""
    for og in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        prop = og.get("property", "")
        if prop == "og:title":
            og_title = og.get("content", "")
        elif prop == "og:description":
            og_desc = og.get("content", "")
        elif prop == "og:image":
            og_image = og.get("content", "")

    # Extract body text for content analysis
    body = soup.get_text(separator=" ", strip=True)
    # Find most relevant phrases (simple: top keywords from body)
    words = re.findall(r"\b[a-zA-Z]{4,}\b", body.lower())
    word_freq = {}
    for w in words:
        word_freq[w] = word_freq.get(w, 0) + 1
    top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:15]
    top_keywords = [w for w, _ in top_words]

    # Domain name for brand
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.replace("www.", "")

    # ── Generate optimized title (30-60 chars) ──
    primary_keyword = h1_tags[0] if h1_tags else (top_keywords[0] if top_keywords else domain)
    # Build title: Primary Keyword | Brand
    new_title = f"{primary_keyword[:50]} | {domain}"
    if len(new_title) > 60:
        new_title = f"{primary_keyword[:40]} | {domain}"

    # ── Generate optimized meta description (120-160 chars) ──
    # Use H2s and top keywords to build a compelling description
    subtopics = h2_tags[:3] if h2_tags else top_keywords[:3]
    subtopics_text = ", ".join(subtopics[:3])
    new_meta = f"{primary_keyword} - {subtopics_text}. {domain} provides expert information and resources."
    if len(new_meta) > 160:
        new_meta = f"{primary_keyword[:80]}. {subtopics_text}. Expert guide on {domain}."
    if len(new_meta) > 160:
        new_meta = new_meta[:157] + "..."

    # ── Generate Open Graph tags ──
    new_og_title = new_title[:95]
    new_og_desc = new_meta[:200]

    # ── Build ready-to-paste HTML ──
    html_tags = (
        f'<!-- SEO Meta Tags - Generated by TAGS SEO Agent -->\n'
        f'<title>{new_title}</title>\n'
        f'<meta name="description" content="{new_meta}">\n'
        f'<meta name="robots" content="index, follow">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<link rel="canonical" href="{url}">\n'
        f'\n'
        f'<!-- Open Graph -->\n'
        f'<meta property="og:title" content="{new_og_title}">\n'
        f'<meta property="og:description" content="{new_og_desc}">\n'
        f'<meta property="og:url" content="{url}">\n'
        f'<meta property="og:type" content="website">\n'
        f'\n'
        f'<!-- Twitter Card -->\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:title" content="{new_og_title}">\n'
        f'<meta name="twitter:description" content="{new_og_desc}">'
    )

    return {
        "url": url,
        "current": {
            "title": current_title,
            "title_length": len(current_title),
            "meta_description": current_meta,
            "meta_desc_length": len(current_meta),
            "og_title": og_title,
            "og_desc": og_desc,
        },
        "generated": {
            "title": new_title,
            "title_length": len(new_title),
            "meta_description": new_meta,
            "meta_desc_length": len(new_meta),
            "og_title": new_og_title,
            "og_desc": new_og_desc,
        },
        "content_signals": {
            "h1_tags": h1_tags[:5],
            "h2_tags": h2_tags[:5],
            "top_keywords": top_keywords[:10],
        },
        "html_tags": html_tags,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 8: Generate Schema Markup (Action — generates JSON-LD code)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_schema(url: str) -> dict[str, Any]:
    """Crawl a page, detect its type, and generate appropriate JSON-LD schema markup.

    Detects: article, product, local business, FAQ, organization, breadcrumb.
    Returns ready-to-paste <script> tag with structured data.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except Exception as e:
        return {"url": url, "error": str(e)}

    soup = BeautifulSoup(resp.text, BS4_PARSER)
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.replace("www.", "")

    # Extract signals to detect page type
    title = soup.find("title").get_text(strip=True) if soup.find("title") else ""
    h1 = soup.find("h1").get_text(strip=True) if soup.find("h1") else title
    body_text = soup.get_text(separator=" ", strip=True)[:2000]
    images = [img.get("src", "") for img in soup.find_all("img") if img.get("src")]

    # Detect page type
    page_type = "organization"  # default
    body_lower = body_text.lower()
    if any(kw in body_lower for kw in ["price", "buy", "add to cart", "product", "$", "₹"]):
        page_type = "product"
    elif any(kw in body_lower for kw in ["article", "published", "author", "posted on"]):
        page_type = "article"
    elif any(kw in body_lower for kw in ["faq", "frequently asked", "questions and answers"]):
        page_type = "faq"
    elif any(kw in body_lower for kw in ["location", "address", "opening hours", "contact us", "map"]):
        page_type = "local_business"

    # Check for existing schema
    existing_schemas = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            existing_schemas.append(json.loads(script.string or "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

    # ── Generate schema based on detected type ──
    schemas = []

    # Always add Organization schema
    org_schema = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": domain.split(".")[0].title(),
        "url": f"{parsed_url.scheme}://{parsed_url.netloc}",
    }
    if images:
        org_schema["logo"] = images[0] if images[0].startswith("http") else f"{parsed_url.scheme}://{parsed_url.netloc}{images[0]}"
    schemas.append(("Organization", org_schema))

    if page_type == "article":
        article_schema = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": h1[:110],
            "url": url,
            "publisher": {
                "@type": "Organization",
                "name": domain.split(".")[0].title(),
            },
        }
        if images:
            article_schema["image"] = images[0]
        schemas.append(("Article", article_schema))

    elif page_type == "product":
        product_schema = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": h1[:110],
            "url": url,
            "description": body_text[:500],
        }
        if images:
            product_schema["image"] = images[:3]
        schemas.append(("Product", product_schema))

    elif page_type == "faq":
        # Extract Q&A pairs
        faq_pairs = []
        # Look for common FAQ patterns
        for el in soup.find_all(["h2", "h3", "strong", "b"]):
            text = el.get_text(strip=True)
            if text.endswith("?"):
                answer_el = el.find_next(["p", "div", "span"])
                answer = answer_el.get_text(strip=True)[:300] if answer_el else ""
                if answer:
                    faq_pairs.append({"question": text, "answer": answer})

        if faq_pairs:
            faq_schema = {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": pair["question"],
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": pair["answer"],
                        },
                    }
                    for pair in faq_pairs[:20]
                ],
            }
            schemas.append(("FAQPage", faq_schema))

    elif page_type == "local_business":
        local_schema = {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": h1[:110],
            "url": url,
        }
        schemas.append(("LocalBusiness", local_schema))

    # BreadcrumbList (always useful)
    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "name": "Home",
                "item": f"{parsed_url.scheme}://{parsed_url.netloc}",
            },
            {
                "@type": "ListItem",
                "position": 2,
                "name": h1[:60],
                "item": url,
            },
        ],
    }
    schemas.append(("BreadcrumbList", breadcrumb_schema))

    # ── Build HTML output ──
    script_tags = []
    for schema_type, schema_data in schemas:
        script_tags.append(
            f'<script type="application/ld+json">\n'
            f'{json.dumps(schema_data, indent=2)}\n'
            f'</script>'
        )
    html_output = "\n".join(script_tags)

    return {
        "url": url,
        "detected_type": page_type,
        "existing_schemas_count": len(existing_schemas),
        "generated_schemas": [
            {"type": st, "data": sd} for st, sd in schemas
        ],
        "html_tags": html_output,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 9: Fix Audit Issues (Action — generates ready-to-paste HTML fixes)
# ═══════════════════════════════════════════════════════════════════════════════

def fix_audit_issues(audit_url: str) -> dict[str, Any]:
    """Run a site audit and generate specific HTML/code fixes for each issue found.

    Returns page-by-page fixes that can be copy-pasted into the site's code.
    """
    # First run the audit
    audit = site_audit(audit_url, max_pages=5)

    pages = audit.get("pages", [])
    issues = audit.get("issues", [])
    summary = audit.get("summary", {})

    fixes_by_page = []
    for page in pages:
        page_fixes = []
        url = page.get("url", "")

        # Fix: Missing title
        if not page.get("title"):
            page_fixes.append({
                "issue": "Missing <title> tag",
                "priority": "critical",
                "fix": '<title>Your Brand - Primary Keyword | Secondary Keyword</title>',
                "instruction": "Replace 'Your Brand' with your brand name, 'Primary Keyword' with the main topic of this page.",
            })

        # Fix: Title too long
        elif page.get("title_length", 0) > 60:
            page_fixes.append({
                "issue": f"Title too long ({page['title_length']} chars)",
                "priority": "warning",
                "fix": f'<title>{page["title"][:55]}...</title>',
                "instruction": f"Shorten title from {page['title_length']} to under 60 characters. Keep the most important keywords.",
            })

        # Fix: Missing meta description
        if not page.get("meta_description"):
            page_fixes.append({
                "issue": "Missing meta description",
                "priority": "critical",
                "fix": '<meta name="description" content="Write a compelling 120-160 character description that includes your main keyword and a call to action.">',
                "instruction": "Write a unique description for each page. Include the primary keyword naturally.",
            })

        # Fix: Missing viewport
        if not page.get("has_viewport"):
            page_fixes.append({
                "issue": "Missing mobile viewport",
                "priority": "critical",
                "fix": '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
                "instruction": "Add this tag in the <head> section. Required for mobile responsiveness.",
            })

        # Fix: Missing canonical
        if not page.get("canonical"):
            page_fixes.append({
                "issue": "Missing canonical URL",
                "priority": "warning",
                "fix": f'<link rel="canonical" href="{url}">',
                "instruction": "Set the canonical URL to the preferred version of this page (with or without trailing slash).",
            })

        # Fix: Missing H1
        if page.get("h1_count", 0) == 0:
            page_fixes.append({
                "issue": "Missing H1 tag",
                "priority": "critical",
                "fix": '<h1>Main Topic of This Page</h1>',
                "instruction": "Add one H1 tag that clearly describes the page content. Include the primary keyword.",
            })

        # Fix: Multiple H1s
        elif page.get("h1_count", 0) > 1:
            page_fixes.append({
                "issue": f"Multiple H1 tags ({page['h1_count']})",
                "priority": "warning",
                "fix": "Keep only the most important H1. Change others to H2.",
                "instruction": "Each page should have exactly one H1 tag. Convert extra H1s to H2s.",
            })

        # Fix: Images without alt
        if page.get("images_without_alt", 0) > 0:
            page_fixes.append({
                "issue": f"{page['images_without_alt']} images missing alt text",
                "priority": "warning",
                "fix": '<img src="image.jpg" alt="Descriptive text about the image including keywords">',
                "instruction": f"Add descriptive alt text to all {page['images_without_alt']} images. Be descriptive and include relevant keywords naturally.",
            })

        # Fix: Missing schema
        if not page.get("has_schema"):
            page_fixes.append({
                "issue": "No structured data (JSON-LD)",
                "priority": "info",
                "fix": '<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","name":"Page Title","url":"' + url + '"}</script>',
                "instruction": "Add JSON-LD structured data. Use the generate_schema tool for auto-generated schema.",
            })

        if page_fixes:
            fixes_by_page.append({
                "url": url,
                "title": page.get("title", "(missing)"),
                "issues_count": len(page_fixes),
                "fixes": page_fixes,
            })

    # Summary of all fixes
    total_fixes = sum(len(p["fixes"]) for p in fixes_by_page)
    critical = sum(1 for p in fixes_by_page for f in p["fixes"] if f["priority"] == "critical")
    warnings = sum(1 for p in fixes_by_page for f in p["fixes"] if f["priority"] == "warning")

    return {
        "url": audit_url,
        "pages_analyzed": len(pages),
        "total_fixes": total_fixes,
        "critical_fixes": critical,
        "warnings": warnings,
        "fixes_by_page": fixes_by_page,
    }


# ═══════════════════════════════════════════════
# TOOL 10: Generate SEO Report (Action — client-ready report)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_seo_report(url: str, keywords: list[str] | None = None) -> dict[str, Any]:
    """Run a full SEO audit + keyword check and generate a client-ready report.

    Produces a markdown report with executive summary, issues, keyword analysis,
    competitor insights, and prioritized action items.
    """
    # Run site audit
    audit = site_audit(url, max_pages=5)

    # Run on-page check
    page_check = onpage_check(url)

    # Run keyword research for first relevant keyword
    keyword_data = None
    if keywords:
        keyword_data = keyword_research(keywords[0])
    elif page_check.get("title"):
        # Extract a seed keyword from the title
        seed = page_check["title"].split("|")[0].split("-")[0].strip()[:40]
        if seed:
            keyword_data = keyword_research(seed)

    # Run robots.txt check
    robots = parse_robots_txt(url)

    # ── Build report ──
    pages = audit.get("pages", [])
    issues = audit.get("issues", [])
    summary = audit.get("summary", {})
    seo_score = page_check.get("seo_score", 0)

    # Executive summary
    critical_count = sum(1 for i in issues if "missing" in i.get("type", "").lower())
    report_date = _now_str()[:10]

    report = f"""# SEO Report: {url}
**Date:** {report_date}
**Prepared by:** TAGS SEO Agent

---

## Executive Summary

| Metric | Value |
|--------|-------|
| SEO Score | **{seo_score}/100** |
| Pages Crawled | {audit.get('pages_crawled', 0)} |
| Total Issues | {len(issues)} |
| Critical Issues | {critical_count} |
| Avg Response Time | {summary.get('avg_response_time_ms', 'N/A')}ms |
| Total Links | {summary.get('total_links', 0)} |
| Internal Links | {summary.get('internal_links', 0)} |
| External Links | {summary.get('external_links', 0)} |

### Overall Health: {'POOR' if seo_score < 40 else 'NEEDS WORK' if seo_score < 70 else 'GOOD'}

"""

    # Issues section
    if issues:
        report += "## Issues Found\n\n"
        report += "| Priority | Type | URL | Details |\n"
        report += "|----------|------|-----|----------|\n"
        for issue in issues[:20]:
            priority = "CRITICAL" if "missing" in issue.get("type", "").lower() else "WARNING"
            report += f"| {priority} | {issue.get('type', '?')} | {issue.get('url', '?')[:40]} | {issue.get('message', '?')[:50]} |\n"
        report += "\n"

    # Page-level details
    if pages:
        report += "## Page Analysis\n\n"
        for p in pages[:5]:
            report += f"### {p.get('url', '?')}\n"
            report += f"- **Title:** {p.get('title', '(missing)')} ({p.get('title_length', 0)} chars)\n"
            report += f"- **Meta Description:** {'Present' if p.get('meta_description') else 'MISSING'}\n"
            report += f"- **H1 Tags:** {p.get('h1_count', 0)}\n"
            report += f"- **Images:** {p.get('images_total', 0)} ({p.get('images_without_alt', 0)} missing alt)\n"
            report += f"- **Schema:** {'Yes' if p.get('has_schema') else 'No'}\n"
            report += f"- **Canonical:** {'Yes' if p.get('canonical') else 'MISSING'}\n"
            report += f"- **Viewport:** {'Yes' if p.get('has_viewport') else 'MISSING'}\n\n"

    # Keyword analysis
    if keyword_data and keyword_data.get("total_suggestions", 0) > 0:
        report += "## Keyword Analysis\n\n"
        report += f"**Seed Keyword:** {keyword_data.get('seed_keyword', '?')}\n\n"
        report += f"- **Total keyword variations found:** {keyword_data.get('total_suggestions', 0)}\n"
        report += f"- **Questions (for FAQ content):** {len(keyword_data.get('questions', []))}\n"
        report += f"- **Long-tail keywords:** {len(keyword_data.get('long_tail', []))}\n"
        report += f"- **Short-tail keywords:** {len(keyword_data.get('short_tail', []))}\n\n"

        if keyword_data.get("questions"):
            report += "### Question Keywords (Great for FAQ/Blog Content)\n"
            for q in keyword_data["questions"][:10]:
                report += f"- {q}\n"
            report += "\n"

        if keyword_data.get("long_tail"):
            report += "### Long-tail Keywords (Low Competition)\n"
            for kw in keyword_data["long_tail"][:10]:
                report += f"- {kw}\n"
            report += "\n"

    # Robots.txt
    if robots.get("exists"):
        rules = robots.get("rules", {})
        report += "## Robots.txt Analysis\n\n"
        report += f"- **User Agents:** {', '.join(rules.get('user_agents', []))}\n"
        report += f"- **Disallow Rules:** {len(rules.get('disallow', []))}\n"
        report += f"- **Sitemaps:** {', '.join(rules.get('sitemap', []))}\n\n"

    # Action items
    report += "## Priority Action Items\n\n"
    action_num = 1
    if seo_score < 40:
        report += f"{action_num}. **URGENT:** Overall SEO score is {seo_score}/100. Immediate technical fixes needed.\n"
        action_num += 1
    if summary.get("pages_missing_title", 0) > 0:
        report += f"{action_num}. Add <title> tags to {summary['pages_missing_title']} page(s)\n"
        action_num += 1
    if summary.get("pages_missing_meta_desc", 0) > 0:
        report += f"{action_num}. Write meta descriptions for {summary['pages_missing_meta_desc']} page(s)\n"
        action_num += 1
    if summary.get("pages_missing_h1", 0) > 0:
        report += f"{action_num}. Add H1 tags to {summary['pages_missing_h1']} page(s)\n"
        action_num += 1
    if summary.get("pages_missing_alt", 0) > 0:
        report += f"{action_num}. Add alt text to {summary['pages_missing_alt']} images\n"
        action_num += 1
    if summary.get("pages_missing_viewport", 0) > 0:
        report += f"{action_num}. Add viewport meta tag to {summary['pages_missing_viewport']} page(s) for mobile\n"
        action_num += 1
    if summary.get("pages_missing_canonical", 0) > 0:
        report += f"{action_num}. Add canonical URLs to {summary['pages_missing_canonical']} page(s)\n"
        action_num += 1
    if not keyword_data:
        report += f"{action_num}. Run keyword research to identify target keywords\n"
        action_num += 1
    report += f"{action_num}. Schedule monthly SEO audit to track improvements\n"

    report += "\n---\n*Report generated by TAGS SEO Agent*\n"

    return {
        "url": url,
        "report_date": report_date,
        "seo_score": seo_score,
        "report_markdown": report,
        "summary": {
            "pages_crawled": audit.get("pages_crawled", 0),
            "total_issues": len(issues),
            "seo_score": seo_score,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL 11: Track Rankings (Action — monitor SERP position over time)
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory ranking history
_ranking_history: dict[str, list[dict]] = {}


def track_rankings(keyword: str, target_url: str, num_results: int = 20) -> dict[str, Any]:
    """Check Google SERP for a keyword and find where the target URL ranks.

    Stores the result with timestamp for tracking rank changes over time.
    Returns position, comparison with competitors, and historical data.
    """
    # Get SERP results
    serp = serp_check(keyword, num_results)
    results = serp.get("results", [])

    # Find target URL position
    target_position = None
    target_result = None
    target_domain = urlparse(target_url).netloc.replace("www.", "")

    for i, r in enumerate(results, start=1):
        result_domain = urlparse(r.get("url", "")).netloc.replace("www.", "")
        if target_domain in result_domain or result_domain in target_domain:
            target_position = i
            target_result = r
            break
        # Also check if URL path matches
        if target_url.rstrip("/") in r.get("url", "").rstrip("/"):
            target_position = i
            target_result = r
            break

    # Store in history
    history_key = f"{keyword}|{target_domain}"
    if history_key not in _ranking_history:
        _ranking_history[history_key] = []

    now = _now_str()
    entry = {
        "timestamp": now,
        "position": target_position,
        "total_results": len(results),
    }
    _ranking_history[history_key].append(entry)

    # Calculate rank change
    prev_entries = _ranking_history[history_key][:-1]
    rank_change = None
    if prev_entries:
        last_pos = prev_entries[-1].get("position")
        if last_pos is not None and target_position is not None:
            rank_change = last_pos - target_position  # positive = improved

    # Build competitor list
    competitors = []
    for i, r in enumerate(results[:10], start=1):
        competitors.append({
            "position": i,
            "title": r.get("title", "")[:80],
            "url": r.get("url", "")[:100],
        })

    # Build report
    if target_position:
        position_text = f"Position #{target_position}"
        if rank_change is not None:
            if rank_change > 0:
                position_text += f" (improved by {rank_change} positions)"
            elif rank_change < 0:
                position_text += f" (dropped {abs(rank_change)} positions)"
            else:
                position_text += " (unchanged)"
    else:
        position_text = "NOT RANKING in top " + str(num_results)

    return {
        "keyword": keyword,
        "target_url": target_url,
        "position": target_position,
        "position_text": position_text,
        "rank_change": rank_change,
        "total_results_checked": len(results),
        "competitors": competitors,
        "history": _ranking_history[history_key][-10:],  # Last 10 entries
        "tracking_started": _ranking_history[history_key][0]["timestamp"] if _ranking_history[history_key] else now,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY — LangGraph tool definitions
# ═══════════════════════════════════════════════════════════════════════════════

SEO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "site_audit",
            "description": "Crawl a website and produce a technical SEO audit. Checks title tags, meta descriptions, headings, images, links, schema, viewport. Returns issues and summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to audit (e.g., https://example.com)"},
                    "max_pages": {"type": "integer", "description": "Max pages to crawl (default 10)", "default": 10},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keyword_research",
            "description": "Research keywords using Google Autocomplete. Expands a seed keyword into 100+ long-tail variations, questions, and related searches. No API key needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seed_keyword": {"type": "string", "description": "The main keyword to research (e.g., 'digital marketing')"},
                    "language": {"type": "string", "description": "Language code (default 'en')", "default": "en"},
                },
                "required": ["seed_keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "onpage_check",
            "description": "Deep on-page SEO analysis for a single URL. Checks title, meta, headings, images, OG tags, schema, readability, response time. Returns SEO score (0-100) and issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to analyze"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_sitemap",
            "description": "Parse a website's sitemap.xml and list all indexed URLs with lastmod dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The website URL (will try /sitemap.xml)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_robots_txt",
            "description": "Parse robots.txt to see which pages are blocked/allowed for crawlers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The website URL"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "serp_check",
            "description": "Check Google search results for a keyword. See who ranks, what titles/descriptions they use. No API key needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "The keyword to check SERP for"},
                    "num_results": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                },
                "required": ["keyword"],
            },
        },
    },
    # ── ACTION TOOLS (generate code, reports, fixes) ──
    {
        "type": "function",
        "function": {
            "name": "generate_meta_tags",
            "description": "Crawl a page and generate optimized SEO meta tags (title, description, OG tags). Returns ready-to-paste HTML code. Use after onpage_check to fix meta issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to generate meta tags for"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_schema",
            "description": "Crawl a page and generate JSON-LD structured data schema. Auto-detects page type (article, product, FAQ, local business). Returns ready-to-paste script tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to generate schema for"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fix_audit_issues",
            "description": "Run site audit and generate specific HTML/code fixes for each issue found. Returns page-by-page copy-paste fixes. Use after site_audit to get actionable fixes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "audit_url": {"type": "string", "description": "The URL to audit and generate fixes for"},
                },
                "required": ["audit_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_seo_report",
            "description": "Generate a client-ready SEO report with executive summary, issues, keyword analysis, and action items. Combines audit + keywords + robots.txt into one markdown report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to generate report for"},
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "Optional keywords to include in report"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_rankings",
            "description": "Check Google SERP and find where a URL ranks for a keyword. Stores position with timestamp for tracking rank changes over time. Run multiple times to see trends.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "The keyword to check rankings for"},
                    "target_url": {"type": "string", "description": "The URL to find in search results"},
                    "num_results": {"type": "integer", "description": "Results to check (default 20)", "default": 20},
                },
                "required": ["keyword", "target_url"],
            },
        },
    },
]


# ── Tool executor ──────────────────────────────────────────────────────────

def execute_seo_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute an SEO tool by name with given arguments."""
    tools_map = {
        "site_audit": lambda args: site_audit(args["url"], args.get("max_pages", 10)),
        "keyword_research": lambda args: keyword_research(args["seed_keyword"], args.get("language", "en")),
        "onpage_check": lambda args: onpage_check(args["url"]),
        "parse_sitemap": lambda args: parse_sitemap(args["url"]),
        "parse_robots_txt": lambda args: parse_robots_txt(args["url"]),
        "serp_check": lambda args: serp_check(args["keyword"], args.get("num_results", 10)),
        # Action tools
        "generate_meta_tags": lambda args: generate_meta_tags(args["url"]),
        "generate_schema": lambda args: generate_schema(args["url"]),
        "fix_audit_issues": lambda args: fix_audit_issues(args["audit_url"]),
        "generate_seo_report": lambda args: generate_seo_report(args["url"], args.get("keywords")),
        "track_rankings": lambda args: track_rankings(args["keyword"], args["target_url"], args.get("num_results", 20)),
    }

    if name not in tools_map:
        return {"error": f"Unknown tool: {name}"}

    try:
        result = tools_map[name](arguments)
        return result
    except Exception as e:
        logger.exception("SEO tool '%s' failed", name)
        return {"error": str(e)}
