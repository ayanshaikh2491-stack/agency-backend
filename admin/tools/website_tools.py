"""Website Tools — Real tools for Website Agent.

15 tools (NO SEO — SEO Agent ka kaam hai):

Analysis (10):
1. analyze_website — Crawl site, detect tech stack, structure
2. check_performance — Page speed, load time, resources
3. check_links — Find broken links
4. security_check — Security headers check
5. tech_stack_advisor — Recommend tech stack
6. design_planner — Plan site architecture, navigation
7. check_accessibility — Basic a11y checks
8. competitor_sites — Scan competitor websites
9. responsive_check — Mobile responsiveness
10. check_ssl — SSL certificate status

Action (5):
11. generate_code — Generate Next.js/HTML/CSS code for a page
12. deploy_vercel — Deploy frontend+backend to Vercel
13. check_domain — Domain availability + DNS records
14. screenshot_site — Take a screenshot of a website
15. check_uptime — Monitor site uptime, response time, status
"""
from __future__ import annotations

import inspect
import os
import re
import ssl
import json
import shutil
import socket
import logging
import subprocess
import time
import dns.resolver
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_get(url: str, timeout: int = 15) -> requests.Response | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception as e:
        logger.warning("GET %s failed: %s", url, e)
        return None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSITE PROJECT BUILDER (shared by build_site + generate_code)
# ═══════════════════════════════════════════════════════════════════════════════

_WEBSITE_PALETTES = {
    "modern": {"primary": "#2563EB", "secondary": "#1E293B", "accent": "#F59E0B", "bg": "#FFFFFF", "text": "#1E293B"},
    "minimal": {"primary": "#000000", "secondary": "#666666", "accent": "#2563EB", "bg": "#FFFFFF", "text": "#333333"},
    "bold": {"primary": "#DC2626", "secondary": "#1E293B", "accent": "#F59E0B", "bg": "#FFFFFF", "text": "#1E293B"},
    "warm": {"primary": "#D97706", "secondary": "#92400E", "accent": "#059669", "bg": "#FFFBEB", "text": "#451A03"},
    "tech": {"primary": "#7C3AED", "secondary": "#1E1B4B", "accent": "#06B6D4", "bg": "#FFFFFF", "text": "#1E1B4B"},
}

_DEFAULT_SERVICES = ["Fast Delivery", "Secure Builds", "Scalable Design"]


def _mix_hex(hex_color: str, target: str = "ffffff", amt: float = 0.0) -> str:
    """Blend hex_color toward target hex by amt (0..1). Compatibility-safe (no color-mix())."""
    h = (hex_color or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r, g, b = 0, 0, 0
    t = target.lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    tr, tg, tb = int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    nr = round(r + (tr - r) * amt)
    ng = round(g + (tg - g) * amt)
    nb = round(b + (tb - b) * amt)
    return f"#{nr:02x}{ng:02x}{nb:02x}"


def _normalize_services(services) -> list[tuple[str, str, str]]:
    """Coerce services into render-ready (name, description, price) tuples.

    Accepts plain strings ("Fast Delivery"), store rows
    ({"name", "description", "price"}), or tuples, and returns
    (name, description, price) triples. The HTML cards renderer and the
    Next.js Services component both understand this shape.
    """
    out: list[tuple[str, str, str]] = []
    for s in (services or []):
        if isinstance(s, str):
            name = s.strip()
            if name:
                out.append((name, "", ""))
        elif isinstance(s, dict):
            name = str(s.get("name") or "").strip()
            if not name:
                continue
            desc = str(s.get("description") or "").strip()
            price = str(s.get("price") or "").strip()
            out.append((name, desc, price))
        elif isinstance(s, (tuple, list)) and s:
            name = str(s[0] or "").strip()
            if not name:
                continue
            desc = str(s[1] or "").strip() if len(s) > 1 else ""
            price = str(s[2] or "").strip() if len(s) > 2 else ""
            out.append((name, desc, price))
    # Dedupe preserving order.
    seen: set[tuple[str, str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _services_display(services) -> list[str]:
    """Human-readable one-liners for APIs/READMEs: 'Name — Description'."""
    out = []
    for name, desc, price in _normalize_services(services):
        if price:
            out.append(f"{name} — {desc}" if desc else f"{name} ({price})")
        else:
            out.append(f"{name} — {desc}" if desc else name)
    return out

# ═══════════════════════════════════════════════════════════════════════════════
# WEBSITE CATEGORIES — har type ke liye alag pages + sections
# ═══════════════════════════════════════════════════════════════════════════════

# Category -> page map. Key = route (index = home), value = nav label + sections.
# Section names must exist in the HTML/Next.js section renderers below.
WEBSITE_CATEGORIES: dict[str, dict[str, Any]] = {
    "business": {
        "label": "Business / Corporate",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "services", "about", "stats", "testimonials", "contact", "footer"]},
            "services": {"nav": "Services", "sections": ["hero_small", "services", "process", "faq", "cta", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "team", "stats", "cta", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "hours", "footer"]},
        },
    },
    "portfolio": {
        "label": "Portfolio / Creative",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "projects", "services", "testimonials", "contact", "footer"]},
            "work": {"nav": "Work", "sections": ["hero_small", "projects", "gallery", "cta", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "team", "stats", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "footer"]},
        },
    },
    "restaurant": {
        "label": "Restaurant / Cafe",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "menu", "gallery", "testimonials", "booking", "hours", "footer"]},
            "menu": {"nav": "Menu", "sections": ["hero_small", "menu", "hours", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "gallery", "testimonials", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "booking", "hours", "footer"]},
        },
    },
    "ecommerce": {
        "label": "E-commerce / Shop",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "products", "services", "features", "testimonials", "cta", "footer"]},
            "shop": {"nav": "Shop", "sections": ["hero_small", "products", "cta", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "team", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "footer"]},
        },
    },
    "saas": {
        "label": "SaaS / Tech Product",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "features", "pricing", "testimonials", "cta", "footer"]},
            "features": {"nav": "Features", "sections": ["hero_small", "features", "process", "faq", "footer"]},
            "pricing": {"nav": "Pricing", "sections": ["hero_small", "pricing", "faq", "cta", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "footer"]},
        },
    },
    "agency": {
        "label": "Agency / Studio",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "services", "projects", "process", "testimonials", "contact", "footer"]},
            "services": {"nav": "Services", "sections": ["hero_small", "services", "process", "faq", "cta", "footer"]},
            "work": {"nav": "Work", "sections": ["hero_small", "projects", "gallery", "cta", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "footer"]},
        },
    },
    "realestate": {
        "label": "Real Estate",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "listings", "features", "testimonials", "contact", "footer"]},
            "listings": {"nav": "Listings", "sections": ["hero_small", "listings", "cta", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "team", "stats", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "hours", "footer"]},
        },
    },
    "blog": {
        "label": "Blog / News",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "posts", "features", "newsletter", "footer"]},
            "posts": {"nav": "Posts", "sections": ["hero_small", "posts", "newsletter", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "stats", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "footer"]},
        },
    },
    "education": {
        "label": "Education / Coaching",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "features", "courses", "testimonials", "contact", "footer"]},
            "courses": {"nav": "Courses", "sections": ["hero_small", "courses", "pricing", "faq", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "team", "stats", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "footer"]},
        },
    },
    "health": {
        "label": "Health / Clinic / Fitness",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "services", "features", "testimonials", "booking", "contact", "footer"]},
            "services": {"nav": "Services", "sections": ["hero_small", "services", "process", "faq", "footer"]},
            "team": {"nav": "Team", "sections": ["hero_small", "team", "about", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "booking", "hours", "footer"]},
        },
    },
    "event": {
        "label": "Event / Wedding",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "gallery", "features", "testimonials", "booking", "contact", "footer"]},
            "gallery": {"nav": "Gallery", "sections": ["hero_small", "gallery", "cta", "footer"]},
            "details": {"nav": "Details", "sections": ["hero_small", "about", "hours", "faq", "footer"]},
            "rsvp": {"nav": "RSVP", "sections": ["hero_small", "booking", "contact", "footer"]},
        },
    },
    "hotel": {
        "label": "Hotel / Travel",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "features", "rooms", "gallery", "testimonials", "booking", "footer"]},
            "rooms": {"nav": "Rooms", "sections": ["hero_small", "rooms", "pricing", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "gallery", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "booking", "hours", "footer"]},
        },
    },
    "construction": {
        "label": "Construction / Trades",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "services", "projects", "stats", "testimonials", "contact", "footer"]},
            "services": {"nav": "Services", "sections": ["hero_small", "services", "process", "faq", "footer"]},
            "projects": {"nav": "Projects", "sections": ["hero_small", "projects", "gallery", "cta", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "hours", "footer"]},
        },
    },
    "nonprofit": {
        "label": "Nonprofit / Charity",
        "pages": {
            "index": {"nav": "Home", "sections": ["hero", "about", "projects", "stats", "donate", "contact", "footer"]},
            "about": {"nav": "About", "sections": ["hero_small", "about", "team", "stats", "footer"]},
            "projects": {"nav": "Projects", "sections": ["hero_small", "projects", "donate", "footer"]},
            "contact": {"nav": "Contact", "sections": ["hero_small", "contact", "footer"]},
        },
    },
}

# Category -> sample content used by sections (products, menu, projects, etc.)
_CATEGORY_CONTENT: dict[str, dict[str, Any]] = {
    "business": {"hero_copy": "We build modern, fast, and secure websites that help your business grow.", "about_copy": "is a team of passionate builders creating impactful digital experiences.", "team": [("Aisha", "CEO / Founder"), ("Rahul", "Head of Strategy"), ("Neha", "Design Lead")], "stats": [("250+", "Clients served"), ("8+", "Years experience"), ("99%", "Client satisfaction")], "process": [("Discover", "We learn your goals and audience"), ("Design", "We craft the look and feel"), ("Build", "We ship fast and iterate")], "faq": [("What do you charge?", "Every project is scoped individually. Tell us your goals and we will send a quote within 48 hours."), ("How long does a project take?", "Most sites go live in 2-4 weeks, depending on scope.")]},
    "portfolio": {"hero_copy": "Design and code that tells your story.", "about_copy": "is a creative studio crafting brands, websites, and visuals that people remember.", "projects": [("Brand Identity", "Logo & visual system for a fintech startup"), ("E-commerce Site", "Conversion-focused storefront built in 3 weeks"), ("Mobile App UI", "Product design for a health app")], "team": [("Arjun", "Designer"), ("Meera", "Developer"), ("Kabir", "Photographer")], "stats": [("120+", "Projects shipped"), ("40+", "Happy clients"), ("12", "Design awards")]},
    "restaurant": {"hero_copy": "Fresh ingredients, bold flavors, made with love.", "about_copy": "is a family-run kitchen serving honest food made from locally sourced ingredients.", "menu_items": [("Margherita Pizza", "Fresh mozzarella, basil, tomato", "₹349"), ("Grilled Paneer Bowl", "Charred paneer, quinoa, herbs", "₹429"), ("Classic Burger", "Smash patty, cheddar, house sauce", "₹299"), ("Masala Chai", "Spiced tea, served hot", "₹99"), ("Tiramisu", "Espresso-soaked ladyfingers", "₹249")], "stats": [("4.8★", "Average rating"), ("50k+", "Happy customers"), ("15", "Years of service")], "hours": "Mon-Sun 11:00 AM - 11:00 PM", "location": "12 Park Street, Mumbai"},
    "ecommerce": {"hero_copy": "Shop the best products at unbeatable prices.", "about_copy": "is an online store curating quality products with fast, reliable delivery.", "products": [("Wireless Headphones", "Noise-cancelling, 30h battery", "₹4,999"), ("Smart Watch", "Fitness tracking, AMOLED", "₹3,499"), ("Backpack 30L", "Waterproof, laptop sleeve", "₹2,299"), ("Sneakers", "Lightweight everyday wear", "₹3,999"), ("Desk Lamp", "LED, dimmable, USB-C", "₹1,499")], "stats": [("10k+", "Orders delivered"), ("4.9★", "Rating"), ("24h", "Delivery")]},
    "saas": {"hero_copy": "The all-in-one platform your team will love.", "about_copy": "builds software that helps teams work smarter and ship faster.", "features_custom": [("Analytics", "Real-time dashboards and reports"), ("Automation", "Save hours with no-code workflows"), ("Security", "Bank-grade encryption, SOC 2")], "stats": [("50k+", "Active users"), ("99.99%", "Uptime"), ("4.8★", "G2 rating")], "faq": [("Is there a free trial?", "Yes, 14 days free with no credit card required."), ("Can I cancel anytime?", "Absolutely, plans are month-to-month.")]},
    "agency": {"hero_copy": "We turn bold ideas into results that move the needle.", "about_copy": "is a full-service agency across brand, web, and growth.", "projects": [("Launch Campaign", "Product launch for a D2C brand"), ("Brand Refresh", "Rebrand for a logistics company"), ("SEO Growth", "3x organic traffic in 6 months")], "team": [("Rohan", "Founder / Creative Director"), ("Sana", "Growth Lead"), ("Vikram", "Tech Lead")], "stats": [("200+", "Campaigns run"), ("$5M+", "Client revenue driven"), ("30+", "Team members")]},
    "realestate": {"hero_copy": "Find the perfect property for your next chapter.", "about_copy": "is a real estate firm helping families and investors buy, sell, and rent with confidence.", "listings": [("3 BHK Skyline Apartment", "Andheri West, Mumbai - 1,450 sq.ft", "₹2.4 Cr"), ("Modern Studio", "Koramangala, Bengaluru - 420 sq.ft", "₹68 L"), ("Villa with Garden", "Pune - 4 BHK, private lawn", "₹3.9 Cr")], "team": [("Priya", "Founder / Agent"), ("Dev", "Sales Partner"), ("Anjali", "Property Advisor")], "stats": [("500+", "Properties sold"), ("15+", "Years in market"), ("4.9★", "Client rating")]},
    "blog": {"hero_copy": "Ideas, stories, and insights from our team.", "about_copy": "is a publication covering tech, business, and design.", "posts": [("How we scaled to 1M users", "A deep dive into our infrastructure journey", "5 min read"), ("Design trends for this year", "What is working in product design", "8 min read"), ("Building remote culture", "Lessons from a fully distributed team", "6 min read")], "stats": [("1M+", "Monthly readers"), ("500+", "Articles published"), ("40k", "Subscribers")]},
    "education": {"hero_copy": "Learn skills that change your career.", "about_copy": "is an academy offering practical, mentor-led courses.", "courses": [("Web Development Bootcamp", "HTML, CSS, JavaScript, React", "12 weeks"), ("Data Analytics", "SQL, Python, dashboards", "8 weeks"), ("UI/UX Design", "Figma to portfolio", "10 weeks")], "team": [("Dr. Sharma", "Curriculum Head"), ("Kavya", "Lead Instructor"), ("Imran", "Mentor")], "stats": [("10k+", "Students graduated"), ("92%", "Placement rate"), ("4.8★", "Course rating")]},
    "health": {"hero_copy": "Your health, our priority. Care you can trust.", "about_copy": "is a wellness center offering expert care with a personal touch.", "services_custom": [("General Checkup", "Comprehensive health screening"), ("Physiotherapy", "Recovery and rehab programs"), ("Fitness Coaching", "Personalized training plans")], "team": [("Dr. Mehta", "General Physician"), ("Dr. Rao", "Physiotherapist"), ("Coach Tanvi", "Fitness Lead")], "stats": [("30k+", "Patients treated"), ("4.9★", "Patient rating"), ("15+", "Specialists")], "hours": "Mon-Sat 9:00 AM - 8:00 PM", "location": "22 MG Road, Pune"},
    "event": {"hero_copy": "Celebrate your special day with us.", "about_copy": "is an events studio crafting weddings and celebrations that feel unforgettable.", "gallery_items": [("Wedding Stage", "Floral mandap design"), ("Mehndi Night", "Vibrant decor and music"), ("Reception Hall", "Elegant table settings")], "stats": [("300+", "Events hosted"), ("4.9★", "Couple rating"), ("10+", "Years experience")], "hours": "By appointment", "location": "Convention Center, Delhi"},
    "hotel": {"hero_copy": "Stay where comfort meets style.", "about_copy": "is a boutique hotel offering curated stays with warm hospitality.", "rooms": [("Deluxe Room", "King bed, city view, 320 sq.ft", "₹6,499/night"), ("Suite", "Living area, bathtub, 650 sq.ft", "₹11,999/night"), ("Family Room", "Two queens, 500 sq.ft", "₹8,499/night")], "stats": [("4.8★", "Guest rating"), ("1.2k", "Reviews"), ("45", "Rooms")], "hours": "Check-in 2 PM, Check-out 12 PM", "location": "Beach Road, Goa"},
    "construction": {"hero_copy": "Building your vision, brick by brick.", "about_copy": "is a construction firm delivering quality homes and commercial spaces on time.", "projects": [("Skyline Tower", "24-story residential complex"), ("Green Office Park", "LEED-certified offices"), ("Lakeside Villa", "Custom luxury home")], "team": [("Raj", "Project Director"), ("Suresh", "Site Engineer"), ("Lakshmi", "Interior Head")], "stats": [("180+", "Projects delivered"), ("25+", "Years in business"), ("98%", "On-time delivery")]},
    "nonprofit": {"hero_copy": "Together, we can change lives.", "about_copy": "is a non-profit working to provide education, food, and dignity to those who need it most.", "projects": [("School Meals Program", "Feeding 2,000 children daily"), ("Clean Water Initiative", "Wells for 40 villages"), ("Digital Literacy", "Computer labs in 25 schools")], "team": [("Asha", "Executive Director"), ("Farhan", "Programs Lead"), ("Geeta", "Volunteer Coordinator")], "stats": [("50k+", "Lives impacted"), ("120", "Volunteers"), ("15", "Programs running")]},
}

# Extra sections that only some categories use, with default content.
_SECTION_FALLBACK_CONTENT = {
    "menu": [("House Special", "Chef's signature dish", "₹299"), ("Garden Salad", "Fresh seasonal vegetables", "₹199")],
    "products": [("Product One", "Short description", "₹999"), ("Product Two", "Short description", "₹1,499")],
    "projects": [("Project Alpha", "Short description"), ("Project Beta", "Short description")],
    "listings": [("2 BHK Apartment", "Central location - 900 sq.ft", "₹95 L"), ("Studio Office", "Prime area - 500 sq.ft", "₹55 L")],
    "posts": [("Post Title One", "Short excerpt", "5 min read"), ("Post Title Two", "Short excerpt", "7 min read")],
    "courses": [("Course One", "Beginner friendly", "8 weeks"), ("Course Two", "Intermediate", "10 weeks")],
    "rooms": [("Standard Room", "Queen bed, city view", "₹4,999/night"), ("Premium Room", "King bed, balcony", "₹7,999/night")],
    "team": [("Team Member", "Role / Title"), ("Team Member 2", "Role / Title")],
    "gallery": [("Gallery Item 1", "Description"), ("Gallery Item 2", "Description")],
    "faq": [("How can I get started?", "Reach out via the contact form and we will guide you.")],
    "stats": [("100+", "Happy customers"), ("10+", "Years experience")],
    "process": [("Step 1", "We understand your needs"), ("Step 2", "We deliver high quality")],
    "features": [("Quality", "Built to the highest standard"), ("Support", "We are here when you need us"), ("Value", "Fair pricing, no surprises")],
    "hours": "Mon-Fri 9:00 AM - 6:00 PM",
    "location": "Your City, Your Country",
}


def _category_data(category: str) -> dict[str, Any]:
    """Return content dict for a category, with safe fallbacks for missing keys."""
    cat = (category or "business").strip().lower()
    base = dict(_CATEGORY_CONTENT.get(cat, _CATEGORY_CONTENT["business"]))
    # Fill any section that a category's pages use but has no content for.
    pages = WEBSITE_CATEGORIES.get(cat, WEBSITE_CATEGORIES["business"])["pages"]
    used = {s for page in pages.values() for s in page["sections"]}
    for sec in used:
        key = {"menu": "menu_items", "courses": "courses", "rooms": "rooms", "features": "features_custom"}.get(sec, sec)
        if key not in base and sec in _SECTION_FALLBACK_CONTENT:
            base.setdefault(key, _SECTION_FALLBACK_CONTENT[sec])
    base.setdefault("hero_copy", _CATEGORY_CONTENT["business"]["hero_copy"])
    base.setdefault("about_copy", _CATEGORY_CONTENT["business"]["about_copy"])
    return base


def _slugify(name: str) -> str:
    """Convert a name into a safe directory slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return slug or "website"


def _product_to_tuple(p: dict) -> tuple:
    """Convert a store product dict to the (name, description, price, image) card tuple."""
    price = str(p.get("price") or "").strip()
    return (
        str(p.get("name") or "Untitled Product"),
        str(p.get("description") or ""),
        price,
        str(p.get("image_url") or ""),
    )


def _escape_html(text: Any) -> str:
    """Escape text for safe HTML embedding (XSS-safe)."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _html_section(sec: str, ctx: dict) -> str:
    esc = _escape_html
    title = esc(ctx["title"])
    tagline = esc(ctx["tagline"])
    email = esc(ctx["business_email"])
    services = ctx["services"]
    data = ctx.get("data", {})
    c = ctx["colors"]

    def cards(items, *, sub_index=1, price_index=None, extra_class=""):
        out = []
        for it in items:
            head = esc(it[0])
            sub = esc(it[sub_index]) if len(it) > sub_index else ""
            price = esc(it[price_index]) if price_index is not None and len(it) > price_index else ""
            price_html = f'<span class="price">{price}</span>' if price else ""
            out.append(
                f'<div class="card{extra_class}"><h3>{head}</h3>'
                f'<p>{sub}</p>{price_html}</div>'
            )
        return "".join(out)

    svc_cards = cards(services)

    def product_cards(items, raw_items=None):
        """Image-aware product cards for the shop section (HTML framework)."""
        raw_by_name = {str(r.get("name") or ""): r for r in (raw_items or [])}
        out = []
        for it in items:
            head = esc(it[0])
            sub = esc(it[1]) if len(it) > 1 else ""
            price = esc(it[2]) if len(it) > 2 else ""
            raw = raw_by_name.get(it[0]) or {}
            img = esc(raw.get("image_url") or "")
            img_html = (
                f'<img class="product-img" src="{img}" alt="{head}" loading="lazy"/>'
                if img
                else f'<div class="product-ph" style="background:{c["primary"]}22">{head[:1]}</div>'
            )
            price_html = f'<span class="price">{price}</span>' if price else ""
            try:
                stock = int(raw.get("stock") or 0)
            except (TypeError, ValueError):
                stock = 0
            if stock > 0:
                stock_html = '<span class="stock">In stock</span>'
            elif raw.get("stock") is not None and str(raw.get("stock")) != "":
                stock_html = '<span class="stock out">Out of stock</span>'
            else:
                stock_html = ""
            out.append(
                f'<div class="card product-card">{img_html}<h3>{head}</h3>'
                f"<p>{sub}</p>{price_html}{stock_html}</div>"
            )
        return "".join(out)

    products_raw = data.get("products_raw") or []
    if sec == "hero":
        subtitle = f'<p class="sub">{tagline}</p>' if tagline else ""
        hero_copy = esc(data.get("hero_copy", "We build modern, fast, and secure websites that help your business grow."))
        return (
            f'<section class="hero"><h1>{title}</h1>{subtitle}'
            f"<p>{hero_copy}</p>"
            '<a href="#contact" class="btn">Get Started</a></section>'
        )
    if sec == "hero_small":
        subtitle = f'<p class="sub">{tagline}</p>' if tagline else ""
        return f'<section class="hero hero-small"><h1>{title}</h1>{subtitle}</section>'
    if sec == "services":
        return f'<section class="services reveal" id="services"><h2>Our Services</h2><div class="grid">{svc_cards}</div></section>'
    if sec == "about":
        return f'<section class="about reveal" id="about"><h2>About Us</h2><p>{title} {esc(data.get("about_copy", "is a team of passionate builders creating impactful digital experiences."))}</p></section>'
    if sec == "testimonials":
        return (
            '<section class="testimonials reveal" id="testimonials"><h2>What Clients Say</h2>'
            '<blockquote>"Professional, fast, and creative. Highly recommended!" — Happy Client</blockquote></section>'
        )
    if sec == "contact":
        contact_line = f'<p>Email us at <a href="mailto:{email}">{email}</a></p>' if email else ""
        return (
            f'<section class="contact" id="contact"><h2>Contact Us</h2>{contact_line}'
            '<form><input type="text" placeholder="Name" required><input type="email" placeholder="Email" required>'
            '<textarea placeholder="Message" required></textarea><button type="submit">Send</button></form></section>'
        )
    if sec == "footer":
        return f"<footer><p>&copy; 2026 {title}. All rights reserved.</p></footer>"
    if sec == "cta":
        return (
            '<section class="cta reveal"><h2>Ready to Get Started?</h2>'
            '<p>Contact us today and let\'s build something amazing together.</p>'
            '<a href="#contact" class="btn">Contact Us</a></section>'
        )
    if sec == "features":
        items = data.get("features_custom") or data.get("features") or _SECTION_FALLBACK_CONTENT["features"]
        return f'<section class="features reveal" id="features"><h2>Features</h2><div class="grid">{cards(items)}</div></section>'
    if sec == "pricing":
        return (
            '<section class="pricing reveal" id="pricing"><h2>Pricing</h2><div class="grid">'
            '<div class="card"><h3>Starter</h3><p>For individuals</p><span class="price">$29/mo</span></div>'
            '<div class="card featured"><h3>Pro</h3><p>For growing teams</p><span class="price">$79/mo</span></div>'
            '<div class="card"><h3>Enterprise</h3><p>Custom solutions</p><span class="price">$199/mo</span></div>'
            '</div></section>'
        )
    # ── Category-specific sections ──
    if sec == "menu":
        items = data.get("menu_items") or _SECTION_FALLBACK_CONTENT["menu"]
        return f'<section class="menu reveal" id="menu"><h2>Our Menu</h2><div class="grid">{cards(items, price_index=2)}</div></section>'
    if sec == "products":
        items = data.get("products") or _SECTION_FALLBACK_CONTENT["products"]
        body = product_cards(items, products_raw) if products_raw else cards(items, price_index=2)
        return (
            f'<section class="products reveal" id="products"><h2>Shop</h2><div class="grid">{body}</div>'
            '<p class="hint">Online checkout coming soon. Call or email to order.</p></section>'
        )
    if sec == "projects":
        items = data.get("projects") or _SECTION_FALLBACK_CONTENT["projects"]
        return f'<section class="projects reveal" id="projects"><h2>Our Work</h2><div class="grid">{cards(items)}</div></section>'
    if sec == "listings":
        items = data.get("listings") or _SECTION_FALLBACK_CONTENT["listings"]
        return f'<section class="listings reveal" id="listings"><h2>Featured Listings</h2><div class="grid">{cards(items, price_index=2)}</div></section>'
    if sec == "posts":
        items = data.get("posts") or _SECTION_FALLBACK_CONTENT["posts"]
        return f'<section class="posts reveal" id="posts"><h2>Latest Posts</h2><div class="grid">{cards(items)}</div></section>'
    if sec == "courses":
        items = data.get("courses") or _SECTION_FALLBACK_CONTENT["courses"]
        return f'<section class="courses reveal" id="courses"><h2>Our Courses</h2><div class="grid">{cards(items)}</div></section>'
    if sec == "rooms":
        items = data.get("rooms") or _SECTION_FALLBACK_CONTENT["rooms"]
        return f'<section class="rooms reveal" id="rooms"><h2>Rooms & Stays</h2><div class="grid">{cards(items, price_index=2)}</div></section>'
    if sec == "gallery":
        items = data.get("gallery_items") or data.get("gallery") or _SECTION_FALLBACK_CONTENT["gallery"]
        tiles = "".join(
            f'<div class="tile" style="background:{c["primary"]}22"><h3>{esc(it[0])}</h3><p>{esc(it[1]) if len(it) > 1 else ""}</p></div>'
            for it in items
        )
        return f'<section class="gallery reveal" id="gallery"><h2>Gallery</h2><div class="grid tiles">{tiles}</div></section>'
    if sec == "team":
        items = data.get("team") or _SECTION_FALLBACK_CONTENT["team"]
        avatars = "".join(
            f'<div class="card team-card"><div class="avatar">{esc(it[0][:2].upper())}</div><h3>{esc(it[0])}</h3><p>{esc(it[1]) if len(it) > 1 else ""}</p></div>'
            for it in items
        )
        return f'<section class="team reveal" id="team"><h2>Meet the Team</h2><div class="grid">{avatars}</div></section>'
    if sec == "stats":
        items = data.get("stats") or _SECTION_FALLBACK_CONTENT["stats"]
        stats = "".join(
            f'<div class="stat"><span class="num">{esc(it[0])}</span><p>{esc(it[1]) if len(it) > 1 else ""}</p></div>'
            for it in items
        )
        return f'<section class="stats reveal" id="stats"><div class="stats-row">{stats}</div></section>'
    if sec == "process":
        items = data.get("process") or _SECTION_FALLBACK_CONTENT["process"]
        return f'<section class="process reveal" id="process"><h2>How We Work</h2><div class="grid">{cards(items)}</div></section>'
    if sec == "faq":
        items = data.get("faq") or _SECTION_FALLBACK_CONTENT["faq"]
        faqs = "".join(
            f'<details class="faq-item"><summary>{esc(it[0])}</summary><p>{esc(it[1]) if len(it) > 1 else ""}</p></details>'
            for it in items
        )
        return f'<section class="faq reveal" id="faq"><h2>FAQ</h2>{faqs}</section>'
    if sec == "booking":
        return (
            '<section class="booking reveal" id="booking"><h2>Book Now</h2>'
            '<form class="booking-form"><input type="text" placeholder="Your Name" required>'
            '<input type="tel" placeholder="Phone" required>'
            '<input type="date" required><input type="time" required>'
            '<button type="submit">Request Booking</button></form></section>'
        )
    if sec == "hours":
        hours = esc(data.get("hours") or _SECTION_FALLBACK_CONTENT["hours"])
        location = esc(data.get("location") or _SECTION_FALLBACK_CONTENT["location"])
        return (
            f'<section class="hours reveal" id="hours"><h2>Hours & Location</h2>'
            f'<p><strong>Hours:</strong> {hours}</p><p><strong>Location:</strong> {location}</p></section>'
        )
    if sec == "newsletter":
        return (
            '<section class="newsletter reveal" id="newsletter"><h2>Stay Updated</h2>'
            '<form class="newsletter-form"><input type="email" placeholder="Your email" required>'
            '<button type="submit">Subscribe</button></form></section>'
        )
    if sec == "donate":
        return (
            '<section class="donate reveal" id="donate"><h2>Support Our Cause</h2>'
            '<p>Every contribution makes a real difference.</p>'
            '<a href="#contact" class="btn">Donate Now</a></section>'
        )
    return f'<section class="{sec} reveal" id="{sec}"><h2>{sec.title()}</h2><p>Content for the {sec} section.</p></section>'


def _html_nav(pages: list[tuple[str, str]], active: str = "index", title: str = "", logo_url: str = "") -> str:
    links = []
    for route, label in pages:
        href = "index.html" if route == "index" else f"{route}.html"
        active_cls = ' class="active"' if route == active else ""
        links.append(f'<a href="{href}"{active_cls}>{_escape_html(label)}</a>')
    if logo_url:
        brand = f'<img class="brand-logo" src="{_escape_html(logo_url)}" alt="{_escape_html(title)}"/>'
    else:
        brand = f'<span class="brand">{_escape_html(title)}</span>' if title else ""
    return f"<nav class=\"nav\">{brand}{''.join(links)}</nav>"


def _html_page(ctx: dict, body: str, nav: str = "") -> str:
    title = _escape_html(ctx["title"])
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "  <meta charset=\"UTF-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"  <title>{title}</title>\n"
        '  <link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        "  <link rel=\"stylesheet\" href=\"style.css\">\n"
        f'  <meta name="description" content="{title} — official website.">\n'
        "</head>\n<body>\n"
        f"{nav}\n{body}\n"
        "  <script>\n"
        "    document.querySelectorAll('section:not(.hero)').forEach(function(el){el.classList.add('reveal');});\n"
        "    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {\n"
        "      var io = new IntersectionObserver(function(entries){\n"
        "        entries.forEach(function(e){ if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });\n"
        "      }, { threshold: 0.12 });\n"
        "      document.querySelectorAll('.reveal').forEach(function(el){ io.observe(el); });\n"
        "    }\n"
        "  </script>\n"
        "</body>\n</html>"
    )


def _html_css(ctx: dict) -> str:
    c = ctx["colors"]
    p = c["primary"]
    s = c["secondary"]
    a = c["accent"]
    tint = _mix_hex(p, "ffffff", 0.90)
    tint2 = _mix_hex(p, "ffffff", 0.96)
    font_url = "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Sora:wght@600;700;800&display=swap"
    return f"""/* Premium design system — Generated by Website Agent (TAGS) */
@import url('{font_url}');
:root {{
  --p: {p}; --s: {s}; --a: {a}; --bg: {c['bg']}; --text: {c['text']};
  --tint: {tint}; --tint2: {tint2};
  --radius: 18px; --shadow: 0 20px 45px -20px rgba(0,0,0,0.25);
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{ font-family: 'Plus Jakarta Sans', system-ui, sans-serif; color: var(--text); background: var(--bg); line-height: 1.6; -webkit-font-smoothing: antialiased; }}
h1,h2,h3 {{ font-family: 'Sora', 'Plus Jakarta Sans', sans-serif; letter-spacing: -0.02em; }}
nav {{ position: sticky; top: 0; z-index: 50; display: flex; align-items: center; gap: 1.6rem; padding: 0.9rem 2rem;
  background: rgba(255,255,255,0.72); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid rgba(0,0,0,0.06); box-shadow: 0 8px 30px -18px rgba(0,0,0,0.4); flex-wrap: wrap; }}
nav .brand {{ font-family: 'Sora', sans-serif; font-weight: 800; font-size: 1.2rem; letter-spacing: -0.02em; margin-right: auto; color: var(--s); }}
nav .brand-logo {{ height: 38px; width: auto; border-radius: 10px; margin-right: auto; }}
nav a {{ color: var(--text); text-decoration: none; font-weight: 600; font-size: 0.95rem; opacity: 0.8; transition: color 0.2s; }}
nav a:hover, nav a.active {{ color: var(--p); opacity: 1; }}
.btn {{ display: inline-block; padding: 0.95rem 2.4rem; background: linear-gradient(135deg, var(--p), {_mix_hex(p,'ffffff',-0.25)});
  color: #fff; text-decoration: none; border-radius: 999px; font-weight: 700; font-size: 1.02rem; letter-spacing: -0.01em;
  box-shadow: 0 14px 30px -12px var(--p); transition: transform 0.2s, box-shadow 0.2s; }}
.btn:hover {{ transform: translateY(-3px); box-shadow: 0 22px 44px -14px var(--p); }}
.hero {{ position: relative; min-height: 88vh; display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; padding: 6rem 2rem; overflow: hidden;
  background:
    radial-gradient(900px 500px at 15% -10%, {tint} 0%, transparent 60%),
    radial-gradient(800px 600px at 110% 10%, {_mix_hex(a,'ffffff',0.88)} 0%, transparent 55%),
    linear-gradient(180deg, var(--bg), var(--tint2)); }}
.hero::after {{ content: ""; position: absolute; inset: 0; background-image: radial-gradient(rgba(0,0,0,0.05) 1px, transparent 1px);
  background-size: 22px 22px; -webkit-mask-image: radial-gradient(circle at 50% 35%, #000, transparent 70%);
  mask-image: radial-gradient(circle at 50% 35%, #000, transparent 70%); opacity: 0.6; pointer-events: none; }}
.hero h1 {{ position: relative; font-size: clamp(2.6rem, 6vw, 4.6rem); font-weight: 800; line-height: 1.05;
  background: linear-gradient(120deg, var(--s), var(--p)); -webkit-background-clip: text; background-clip: text; color: transparent; max-width: 16ch; }}
.hero .sub {{ position: relative; font-size: clamp(1.1rem, 2.2vw, 1.5rem); margin: 1.1rem 0; font-weight: 500; opacity: 0.85; }}
.hero p {{ position: relative; font-size: 1.15rem; margin-bottom: 2rem; max-width: 38ch; opacity: 0.8; }}
.hero .btn {{ position: relative; }}
.hero-small {{ min-height: 46vh; padding: 4rem 2rem; text-align: center;
  background: radial-gradient(700px 400px at 50% -20%, var(--tint) 0%, transparent 60%); }}
.hero-small h1 {{ font-size: clamp(2rem, 4vw, 3.2rem); font-weight: 800;
  background: linear-gradient(120deg, var(--s), var(--p)); -webkit-background-clip: text; background-clip: text; color: transparent; }}
.hero-small .sub {{ font-size: 1.2rem; opacity: 0.8; margin-top: 0.6rem; }}
.services, .about, .testimonials, .pricing, .features, .contact, .menu, .products, .projects, .listings, .posts, .courses, .rooms, .gallery, .team, .process, .faq, .booking, .hours, .newsletter, .donate {{ padding: 6rem 2rem; text-align: center; }}
.services h2, .about h2, .testimonials h2, .pricing h2, .features h2, .contact h2, .menu h2, .products h2, .projects h2, .listings h2, .posts h2, .courses h2, .rooms h2, .gallery h2, .team h2, .process h2, .faq h2, .booking h2, .hours h2, .newsletter h2, .donate h2 {{ font-size: clamp(2rem, 4vw, 2.8rem); font-weight: 800; margin-bottom: 2.6rem; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 1.8rem; max-width: 1120px; margin: 0 auto; }}
.card {{ background: #fff; border-radius: var(--radius); padding: 2rem; box-shadow: var(--shadow); border: 1px solid rgba(0,0,0,0.04);
  transition: transform 0.25s, box-shadow 0.25s; }}
.card:hover {{ transform: translateY(-6px); box-shadow: 0 30px 60px -22px rgba(0,0,0,0.3); }}
.card.featured {{ border: 2px solid var(--p); background: linear-gradient(180deg, #fff, var(--tint2)); }}
.product-card {{ padding: 0; overflow: hidden; text-align: left; }}
.product-img {{ width: 100%; height: 210px; object-fit: cover; }}
.product-ph {{ width: 100%; height: 210px; display: flex; align-items: center; justify-content: center; font-size: 3.4rem;
  font-weight: 800; font-family: 'Sora', sans-serif; color: var(--p); background: linear-gradient(135deg, var(--tint), var(--tint2)); }}
.product-card h3, .product-card p {{ padding: 0 1.2rem; }}
.product-card h3 {{ padding-top: 1.1rem; }}
.product-card .price {{ padding: 0 1.2rem 1.2rem; }}
.stock {{ display: inline-block; margin-left: 0.5rem; font-size: 0.78rem; font-weight: 700; color: #16a34a; }}
.stock.out {{ color: #dc2626; }}
.card h3 {{ color: var(--s); margin-bottom: 0.5rem; font-size: 1.25rem; }}
.price {{ display: inline-block; margin-top: 0.5rem; font-weight: 800; color: var(--p); font-size: 1.1rem; }}
.hint {{ margin-top: 1.5rem; color: #64748b; }}
.tiles {{ grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }}
.tile {{ border-radius: var(--radius); padding: 3rem 1.5rem; background: linear-gradient(135deg, var(--tint), #fff); }}
.tile h3 {{ color: var(--s); }}
.team-card .avatar {{ width: 64px; height: 64px; border-radius: 50%; background: linear-gradient(135deg, var(--p), var(--a)); color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 800; margin: 0 auto 1rem; font-size: 1.2rem; }}
.stats-row {{ display: flex; justify-content: center; gap: 3rem; flex-wrap: wrap; max-width: 1120px; margin: 0 auto; }}
.stat {{ text-align: center; padding: 1.5rem 2.2rem; border-radius: var(--radius); background: var(--tint2); box-shadow: var(--shadow); }}
.stat .num {{ display: block; font-size: 2.6rem; font-weight: 800; font-family: 'Sora', sans-serif; color: var(--p); }}
.stat p {{ color: #475569; font-weight: 500; }}
.faq-item {{ max-width: 720px; margin: 0.6rem auto; text-align: left; background: #fff; border-radius: 14px; padding: 1.1rem 1.6rem;
  box-shadow: var(--shadow); border: 1px solid rgba(0,0,0,0.04); }}
.faq-item summary {{ font-weight: 700; cursor: pointer; }}
.faq-item p {{ margin-top: 0.5rem; color: #475569; }}
.cta {{ background: linear-gradient(135deg, var(--s), {_mix_hex(s,'000000',0.25)}); color: #fff; padding: 6rem 2rem; text-align: center; }}
.cta h2 {{ font-size: clamp(2rem, 4vw, 2.8rem); font-weight: 800; }}
.cta p {{ font-size: 1.15rem; margin-bottom: 2rem; opacity: 0.9; }}
.cta .btn {{ background: #fff; color: var(--s); box-shadow: 0 14px 30px -12px rgba(0,0,0,0.5); }}
blockquote {{ font-size: 1.25rem; font-style: italic; max-width: 640px; margin: 0 auto; padding: 2.2rem; border-left: 4px solid var(--p);
  background: var(--tint2); border-radius: 0 14px 14px 0; text-align: left; }}
footer {{ background: var(--s); color: #fff; text-align: center; padding: 2.4rem; }}
footer p {{ opacity: 0.85; }}
form {{ display: flex; flex-direction: column; gap: 1rem; max-width: 500px; margin: 0 auto; }}
input, textarea {{ padding: 0.85rem 1rem; border: 1px solid #d8dee9; border-radius: 12px; font-size: 1rem; font-family: inherit; }}
input:focus, textarea:focus {{ outline: 2px solid var(--p); border-color: transparent; }}
button {{ padding: 0.9rem 1.4rem; background: linear-gradient(135deg, var(--p), {_mix_hex(p,'ffffff',-0.2)}); color: #fff; border: none;
  border-radius: 12px; font-size: 1rem; font-weight: 700; cursor: pointer; font-family: inherit; }}
@media (max-width: 768px) {{ .hero h1 {{ font-size: 2.4rem; }} nav {{ gap: 1rem; padding: 0.8rem 1.2rem; }} }}
@media (prefers-reduced-motion: no-preference) {{
  .reveal {{ opacity: 0; transform: translateY(28px); transition: opacity 0.7s ease, transform 0.7s ease; }}
  .reveal.in {{ opacity: 1; transform: none; }}
}}
"""


def _nextjs_cards(items: list, ctx: dict, *, price_index=None) -> str:
    """Return a JSX grid of cards built from a list of tuples."""
    c = ctx["colors"]
    rows = []
    for it in items:
        head = it[0]
        sub = it[1] if len(it) > 1 else ""
        price = it[price_index] if price_index is not None and len(it) > price_index else ""
        price_el = f'<span className="inline-block mt-2 font-bold" style={{{{color: "{c["accent"]}"}}}}>{price}</span>' if price else ""
        rows.append(
            f'<div className="bg-white rounded-xl p-8 shadow-lg">'
            f'<h3 className="text-lg font-bold mb-2" style={{{{color: "{c["primary"]}"}}}}>{head}</h3>'
            f'<p className="text-gray-600">{sub}</p>{price_el}</div>'
        )
    return "\n        ".join(rows)


def _nextjs_product_cards(items: list, raw_items: list, ctx: dict) -> str:
    """JSX product cards with images + stock badges (shop section)."""
    c = ctx["colors"]
    raw_by_name = {str(r.get("name") or ""): r for r in raw_items}
    rows = []
    for it in items:
        head = it[0]
        sub = it[1] if len(it) > 1 else ""
        price = it[2] if len(it) > 2 else ""
        raw = raw_by_name.get(it[0]) or {}
        img = raw.get("image_url") or ""
        img_el = (
            f'<img src="{img}" alt="{head}" loading="lazy" className="w-full h-52 object-cover rounded-lg mb-4"/>'
            if img
            else f'<div className="w-full h-52 rounded-lg mb-4 flex items-center justify-center text-4xl font-extrabold" style={{{{backgroundColor: "{c["primary"]}22", color: "{c["primary"]}"}}}}>{head[:1]}</div>'
        )
        price_el = f'<span className="inline-block mt-2 font-bold" style={{{{color: "{c["accent"]}"}}}}>{price}</span>' if price else ""
        try:
            stock = int(raw.get("stock") or 0)
        except (TypeError, ValueError):
            stock = 0
        if stock > 0:
            stock_el = '<span className="ml-2 text-sm font-semibold text-green-600">In stock</span>'
        elif raw.get("stock") is not None and str(raw.get("stock")) != "":
            stock_el = '<span className="ml-2 text-sm font-semibold text-red-600">Out of stock</span>'
        else:
            stock_el = ""
        rows.append(
            f'<div className="bg-white rounded-xl p-4 shadow-lg">'
            f'{img_el}'
            f'<h3 className="text-lg font-bold mb-2" style={{{{color: "{c["primary"]}"}}}}>{head}</h3>'
            f'<p className="text-gray-600 text-sm">{sub}</p>{price_el}{stock_el}</div>'
        )
    return "\n        ".join(rows)


def _nextjs_component(sec: str, ctx: dict) -> str:
    c = ctx["colors"]
    title = ctx["title"]
    tagline = ctx["tagline"]
    email = ctx["business_email"]
    services = ctx["services"]
    data = ctx.get("data", {})
    hero_copy = data.get("hero_copy", "We build modern, fast, and secure websites that help your business grow.")
    about_copy = data.get("about_copy", "is a team of passionate builders creating impactful digital experiences.")

    if sec == "hero":
        return f"""export default function Hero() {{
  const title = {json.dumps(title, ensure_ascii=False)};
  const tagline = {json.dumps(tagline, ensure_ascii=False)};
  return (
    <section className="relative min-h-[88vh] flex flex-col items-center justify-center text-center px-8 overflow-hidden"
      style={{{{ background: 'radial-gradient(900px 500px at 15% -10%, var(--tint) 0%, transparent 60%), radial-gradient(800px 600px at 110% 10%, {_mix_hex(c['accent'],'ffffff',0.88)} 0%, transparent 55%), linear-gradient(180deg, var(--color-bg), var(--tint2))' }}}}>
      <h1 className="text-5xl md:text-7xl font-extrabold mb-4 max-w-[16ch] leading-[1.05]"
        style={{{{ background: 'linear-gradient(120deg, var(--color-secondary), var(--color-primary))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent', fontFamily: 'Sora, sans-serif' }}}}>{{title}}</h1>
      {{tagline && <p className="text-xl md:text-2xl mb-4 font-medium opacity-85">{{tagline}}</p>}}
      <p className="text-lg md:text-xl mb-8 max-w-[38ch] opacity-80">{hero_copy}</p>
      <a href="#contact" className="bg-gradient-to-br from-[var(--color-primary)] to-[{_mix_hex(c['primary'],'ffffff',-0.25)}] text-white px-8 py-3 rounded-full font-bold shadow-[0_14px_30px_-12px_var(--color-primary)] hover:-translate-y-1 transition-transform">Get Started</a>
    </section>
  );
}}"""
    if sec == "hero_small":
        return f"""export default function HeroSmall() {{
  const title = {json.dumps(title, ensure_ascii=False)};
  const tagline = {json.dumps(tagline, ensure_ascii=False)};
  return (
    <section className="min-h-[46vh] flex flex-col items-center justify-center text-center px-8"
      style={{{{ background: 'radial-gradient(700px 400px at 50% -20%, var(--tint) 0%, transparent 60%)' }}}}>
      <h1 className="text-4xl md:text-5xl font-extrabold"
        style={{{{ background: 'linear-gradient(120deg, var(--color-secondary), var(--color-primary))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent', fontFamily: 'Sora, sans-serif' }}}}>{{title}}</h1>
      {{tagline && <p className="text-xl opacity-80 mt-2">{{tagline}}</p>}}
    </section>
  );
}}"""
    if sec == "services":
        return f"""export default function Services() {{
  const services = {json.dumps(services, ensure_ascii=False)};
  return (
    <section className="py-24 px-8 text-center reveal" id="services">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Our Services</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-7 max-w-5xl mx-auto">
        {{services.map((s, i) => (
          <div key={{i}} className="bg-white rounded-2xl p-8 shadow-[0_20px_45px_-20px_rgba(0,0,0,0.25)] border border-black/5 hover:-translate-y-1.5 transition-transform">
            <h3 className="text-lg font-bold mb-2" style={{{{color: 'var(--color-secondary)'}}}}>{{{{s[0]}}}}</h3>
            {{s[1] && <p className="text-gray-600">{{{{s[1]}}}}</p>}}
            {{s[2] && <p className="mt-2 text-sm font-semibold" style={{{{color: 'var(--color-primary)'}}}}>{{{{s[2]}}}}</p>}}
          </div>
        ))}}
      </div>
    </section>
  );
}}"""
    if sec == "about":
        return f"""export default function About() {{
  const title = {json.dumps(title, ensure_ascii=False)};
  return (
    <section className="py-24 px-8 text-center reveal" id="about">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>About Us</h2>
      <p className="text-gray-600 max-w-2xl mx-auto text-lg">{{title}} {about_copy}</p>
    </section>
  );
}}"""
    if sec == "testimonials":
        return """export default function Testimonials() {
  return (
    <section className="py-24 px-8 text-center reveal" id="testimonials">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{fontFamily: 'Sora, sans-serif'}}>What Clients Say</h2>
      <blockquote className="text-xl italic max-w-2xl mx-auto pl-8 border-l-4 border-[var(--color-primary)] text-left bg-[var(--tint2)] rounded-r-2xl py-6">
        "Professional, fast, and creative. Highly recommended!" — Happy Client
      </blockquote>
    </section>
  );
}"""
    if sec == "contact":
        email_block = ""
        if email:
            email_block = (f'<p className="text-lg mb-4">Email us at <a href="mailto:{email}" className="underline" style={{{{color: "var(--color-primary)"}}}}>{email}</a></p>')
        return f"""export default function Contact() {{
  const email = {json.dumps(email, ensure_ascii=False)};
  return (
    <section className="py-24 px-8 text-center reveal" id="contact">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Contact Us</h2>
      {email_block}
      <form className="flex flex-col gap-4 max-w-md mx-auto" onSubmit={{e => e.preventDefault()}}>
        <input className="p-3 border border-gray-300 rounded-xl" placeholder="Name" required />
        <input className="p-3 border border-gray-300 rounded-xl" placeholder="Email" required />
        <textarea className="p-3 border border-gray-300 rounded-xl" placeholder="Message" required />
        <button className="p-3 bg-gradient-to-br from-[var(--color-primary)] to-[{_mix_hex(c['primary'],'ffffff',-0.2)}] text-white rounded-xl cursor-pointer font-bold">Send</button>
      </form>
    </section>
  );
}}"""
    if sec == "footer":
        owner_link = ""
        vt = ctx.get("view_tracking")
        dash_url = (vt or {}).get("dashboard_url") if vt else None
        if dash_url:
            owner_link = (
                f'<a href="{_escape_html(dash_url)}" '
                f'className="inline-block mt-3 text-sm text-gray-400 hover:text-white underline underline-offset-4">'
                f'Store Owner Login</a>'
            )
        return f"""export default function Footer() {{
  const title = {json.dumps(title, ensure_ascii=False)};
  return (
    <footer className="bg-slate-800 text-white text-center py-6">
      <p>&copy; 2026 {{title}}. All rights reserved.</p>
      <p className="text-xs text-gray-400 mt-1">Powered by TAGS</p>
      {owner_link}
    </footer>
  );
}}"""
    if sec == "features":
        items = data.get("features_custom") or data.get("features") or _SECTION_FALLBACK_CONTENT["features"]
        cards = _nextjs_cards(items, ctx)
        return f"""export default function Features() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="features">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Features</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl mx-auto">
        {cards}
      </div>
    </section>
  );
}}"""
    if sec == "cta":
        return """export default function CTA() {
  return (
    <section className="py-24 px-8 text-center text-white reveal"
      style={{background: 'linear-gradient(135deg, var(--color-secondary), #0b1020)'}}>
      <h2 className="text-4xl md:text-5xl font-extrabold mb-4" style={{fontFamily: 'Sora, sans-serif'}}>Ready to Get Started?</h2>
      <p className="text-lg mb-8 opacity-90">Contact us today.</p>
      <a href="#contact" className="bg-white text-slate-800 px-8 py-3 rounded-full font-bold hover:-translate-y-1 transition-transform inline-block">Contact Us</a>
    </section>
  );
}"""
    if sec == "pricing":
        return """export default function Pricing() {
  return (
    <section className="py-24 px-8 text-center reveal" id="pricing">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{fontFamily: 'Sora, sans-serif'}}>Pricing</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl mx-auto">
        <div className="bg-white rounded-2xl p-8 shadow-[0_20px_45px_-20px_rgba(0,0,0,0.25)]"><h3 className="text-lg font-bold mb-2">Starter</h3><p>For individuals</p><span className="inline-block mt-2 font-bold text-amber-500">$29/mo</span></div>
        <div className="bg-white rounded-2xl p-8 shadow-[0_20px_45px_-20px_rgba(0,0,0,0.25)] border-2 border-blue-600"><h3 className="text-lg font-bold mb-2">Pro</h3><p>For growing teams</p><span className="inline-block mt-2 font-bold text-amber-500">$79/mo</span></div>
        <div className="bg-white rounded-2xl p-8 shadow-[0_20px_45px_-20px_rgba(0,0,0,0.25)]"><h3 className="text-lg font-bold mb-2">Enterprise</h3><p>Custom solutions</p><span className="inline-block mt-2 font-bold text-amber-500">$199/mo</span></div>
      </div>
    </section>
  );
}"""
    # ── Category-specific sections ──
    grid = 'className="grid grid-cols-1 md:grid-cols-3 gap-7 max-w-5xl mx-auto"'
    if sec == "menu":
        items = data.get("menu_items") or _SECTION_FALLBACK_CONTENT["menu"]
        return f"""export default function Menu() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="menu">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Our Menu</h2>
      <div {grid}>
        {_nextjs_cards(items, ctx, price_index=2)}
      </div>
    </section>
  );
}}"""
    if sec == "products":
        items = data.get("products") or _SECTION_FALLBACK_CONTENT["products"]
        raw_items = data.get("products_raw") or []
        body = _nextjs_product_cards(items, raw_items, ctx) if raw_items else _nextjs_cards(items, ctx, price_index=2)
        return f"""export default function Products() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="products">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Shop</h2>
      <div {grid}>
        {body}
      </div>
      <p className="mt-6 text-gray-500">Online checkout coming soon. Call or email to order.</p>
    </section>
  );
}}"""
    if sec == "projects":
        items = data.get("projects") or _SECTION_FALLBACK_CONTENT["projects"]
        return f"""export default function Projects() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="projects">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Our Work</h2>
      <div {grid}>
        {_nextjs_cards(items, ctx)}
      </div>
    </section>
  );
}}"""
    if sec == "listings":
        items = data.get("listings") or _SECTION_FALLBACK_CONTENT["listings"]
        return f"""export default function Listings() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="listings">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Featured Listings</h2>
      <div {grid}>
        {_nextjs_cards(items, ctx, price_index=2)}
      </div>
    </section>
  );
}}"""
    if sec == "posts":
        items = data.get("posts") or _SECTION_FALLBACK_CONTENT["posts"]
        return f"""export default function Posts() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="posts">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Latest Posts</h2>
      <div {grid}>
        {_nextjs_cards(items, ctx)}
      </div>
    </section>
  );
}}"""
    if sec == "courses":
        items = data.get("courses") or _SECTION_FALLBACK_CONTENT["courses"]
        return f"""export default function Courses() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="courses">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Our Courses</h2>
      <div {grid}>
        {_nextjs_cards(items, ctx)}
      </div>
    </section>
  );
}}"""
    if sec == "rooms":
        items = data.get("rooms") or _SECTION_FALLBACK_CONTENT["rooms"]
        return f"""export default function Rooms() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="rooms">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Rooms & Stays</h2>
      <div {grid}>
        {_nextjs_cards(items, ctx, price_index=2)}
      </div>
    </section>
  );
}}"""
    if sec == "gallery":
        items = data.get("gallery_items") or data.get("gallery") or _SECTION_FALLBACK_CONTENT["gallery"]
        tiles = "".join(
            f'<div className="rounded-xl p-12 text-center" style={{{{backgroundColor: "{c["primary"]}22"}}}}>'
            f'<h3 className="text-lg font-bold mb-2" style={{{{color: "{c["primary"]}"}}}}>{it[0]}</h3>'
            f'<p className="text-gray-600">{it[1] if len(it) > 1 else ""}</p></div>'
            for it in items
        )
        return f"""export default function Gallery() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="gallery">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Gallery</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-7 max-w-5xl mx-auto">
        {tiles}
      </div>
    </section>
  );
}}"""
    if sec == "team":
        items = data.get("team") or _SECTION_FALLBACK_CONTENT["team"]
        avatars = "".join(
            f'<div className="bg-white rounded-xl p-8 shadow-lg text-center">'
            f'<div className="w-16 h-16 rounded-full mx-auto mb-4 flex items-center justify-center font-bold text-white text-lg" style={{{{backgroundColor: "{c["primary"]}"}}}}>{it[0][:2].upper()}</div>'
            f'<h3 className="text-lg font-bold mb-2">{it[0]}</h3><p className="text-gray-600">{it[1] if len(it) > 1 else ""}</p></div>'
            for it in items
        )
        return f"""export default function Team() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="team">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Meet the Team</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-7 max-w-5xl mx-auto">
        {avatars}
      </div>
    </section>
  );
}}"""
    if sec == "stats":
        items = data.get("stats") or _SECTION_FALLBACK_CONTENT["stats"]
        stats = "".join(
            f'<div className="text-center"><span className="block text-4xl font-extrabold" style={{{{color: "{c["primary"]}"}}}}>{it[0]}</span><p className="text-gray-500">{it[1] if len(it) > 1 else ""}</p></div>'
            for it in items
        )
        return f"""export default function Stats() {{
  return (
    <section className="py-20 px-8 text-center reveal" id="stats">
      <div className="flex justify-center gap-12 flex-wrap max-w-5xl mx-auto">
        {stats}
      </div>
    </section>
  );
}}"""
    if sec == "process":
        items = data.get("process") or _SECTION_FALLBACK_CONTENT["process"]
        return f"""export default function Process() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="process">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>How We Work</h2>
      <div {grid}>
        {_nextjs_cards(items, ctx)}
      </div>
    </section>
  );
}}"""
    if sec == "faq":
        items = data.get("faq") or _SECTION_FALLBACK_CONTENT["faq"]
        faqs = "".join(
            f'<details className="max-w-2xl mx-auto mb-3 bg-white rounded-lg p-4 shadow-sm text-left">'
            f'<summary className="font-semibold cursor-pointer">{it[0]}</summary>'
            f'<p className="mt-2 text-gray-600">{it[1] if len(it) > 1 else ""}</p></details>'
            for it in items
        )
        return f"""export default function FAQ() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="faq">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>FAQ</h2>
      {faqs}
    </section>
  );
}}"""
    if sec == "booking":
        return """export default function Booking() {
  return (
    <section className="py-24 px-8 text-center reveal" id="booking">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{fontFamily: 'Sora, sans-serif'}}>Book Now</h2>
      <form className="flex flex-col gap-4 max-w-md mx-auto" onSubmit={e => e.preventDefault()}>
        <input className="p-3 border border-gray-300 rounded-xl" placeholder="Your Name" required />
        <input className="p-3 border border-gray-300 rounded-xl" placeholder="Phone" required />
        <div className="flex gap-4">
          <input type="date" className="p-3 border border-gray-300 rounded-xl flex-1" required />
          <input type="time" className="p-3 border border-gray-300 rounded-xl flex-1" required />
        </div>
        <button className="p-3 bg-gradient-to-br from-[var(--color-primary)] to-[#1d4ed8] text-white rounded-xl cursor-pointer font-bold">Request Booking</button>
      </form>
    </section>
  );
}"""
    if sec == "hours":
        hours = data.get("hours") or _SECTION_FALLBACK_CONTENT["hours"]
        location = data.get("location") or _SECTION_FALLBACK_CONTENT["location"]
        return f"""export default function Hours() {{
  const hours = {json.dumps(hours, ensure_ascii=False)};
  const location = {json.dumps(location, ensure_ascii=False)};
  return (
    <section className="py-24 px-8 text-center reveal" id="hours">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Hours & Location</h2>
      <p className="text-gray-600 mb-2"><strong>Hours:</strong> {{hours}}</p>
      <p className="text-gray-600"><strong>Location:</strong> {{location}}</p>
    </section>
  );
}}"""
    if sec == "newsletter":
        return """export default function Newsletter() {
  return (
    <section className="py-24 px-8 text-center reveal" id="newsletter">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-14" style={{fontFamily: 'Sora, sans-serif'}}>Stay Updated</h2>
      <form className="flex gap-4 max-w-md mx-auto" onSubmit={e => e.preventDefault()}>
        <input className="p-3 border border-gray-300 rounded-xl flex-1" placeholder="Your email" required />
        <button className="p-3 bg-gradient-to-br from-[var(--color-primary)] to-[#1d4ed8] text-white rounded-xl cursor-pointer font-bold">Subscribe</button>
      </form>
    </section>
  );
}"""
    if sec == "donate":
        return f"""export default function Donate() {{
  return (
    <section className="py-24 px-8 text-center text-white reveal" style={{{{background: 'linear-gradient(135deg, var(--color-secondary), #0b1020)'}}}} id="donate">
      <h2 className="text-4xl md:text-5xl font-extrabold mb-4" style={{{{fontFamily: 'Sora, sans-serif'}}}}>Support Our Cause</h2>
      <p className="text-lg mb-8 opacity-90">Every contribution makes a real difference.</p>
      <a href="#contact" className="bg-white text-slate-800 px-8 py-3 rounded-full font-bold hover:-translate-y-1 transition-transform inline-block">Donate Now</a>
    </section>
  );
}}"""
    name = sec.title().replace(" ", "")
    return f"""export default function {name}() {{
  return (
    <section className="py-24 px-8 text-center reveal" id="{sec}">
      <h2 className="text-4xl font-bold mb-4" style={{{{fontFamily: 'Sora, sans-serif'}}}}>{sec.title()}</h2>
      <p className="text-gray-600">Content for the {sec} section.</p>
    </section>
  );
}}"""


def _nextjs_navbar(pages: list[tuple[str, str]], ctx: dict) -> str:
    title = ctx["title"]
    links = "".join(
        f'<Link href="/{"" if route == "index" else route}">{_escape_html(label)}</Link>'
        for route, label in pages
    )
    owner_link = ""
    vt = ctx.get("view_tracking")
    dash_url = (vt or {}).get("dashboard_url") if vt else None
    if dash_url:
        owner_link = (
            f'<a href="{_escape_html(dash_url)}" '
            f'className="ml-auto px-3 py-1.5 rounded-lg text-xs font-semibold border border-amber-400/70 text-amber-300 hover:bg-amber-400/10 transition-colors shrink-0">'
            f'Owner Login</a>'
        )
    return f"""import Link from "next/link";

export default function Navbar() {{
  return (
    <nav className="sticky top-0 z-50 bg-white/70 backdrop-blur-xl border-b border-black/5 shadow-[0_8px_30px_-18px_rgba(0,0,0,0.4)] px-8 py-3 flex items-center gap-6 flex-wrap">
      <span className="font-bold text-lg text-slate-800" style={{{{fontFamily: "Sora, sans-serif"}}}}>{_escape_html(title)}</span>
      {links}
      {owner_link}
    </nav>
  );
}}"""


def _nextjs_footer(ctx: dict) -> str:
    return _nextjs_component("footer", ctx)


def _nextjs_page(page_route: str, section_list: list[str], ctx: dict, pages: list[tuple[str, str]]) -> str:
    imports = "\n".join(
        f"import {s.title().replace(' ', '')} from '@/components/{s.title().replace(' ', '')}';"
        for s in section_list
    )
    calls = "\n      ".join(f"<{s.title().replace(' ', '')} />" for s in section_list)
    name = "HomePage" if page_route == "index" else f"{page_route.title().replace('-', '')}Page"
    subdir = "" if page_route == "index" else f"{page_route}/"
    return f"""// app/{subdir}page.tsx — Generated by Website Agent
{imports}

export default function {name}() {{
  return (
    <main>
      {calls}
    </main>
  );
}}
"""


def _nextjs_view_beacon(view_tracking: dict | None) -> str:
    """Tiny client component that records one pageview per session.

    Rendered by the layout when the site is a client storefront. Uses
    useEffect (no raw <script> in a server component) and posts once per
    browser session to the backend's /api/store/views endpoint.
    """
    if not view_tracking:
        return ""
    ws = json.dumps(view_tracking.get("workspace", ""), ensure_ascii=False)
    cl = json.dumps(view_tracking.get("client", ""), ensure_ascii=False)
    base = json.dumps((view_tracking.get("url") or "").rstrip("/"), ensure_ascii=False)
    return f'''"use client";
import {{ useEffect }} from "react";

const WS = {ws};
const CL = {cl};
const BASE = {base};

export default function ViewBeacon() {{
  useEffect(() => {{
    try {{
      const key = "tags_view_" + WS;
      if (window.localStorage.getItem(key)) return;
      fetch(BASE + "/api/store/views?workspace=" + encodeURIComponent(WS) + "&client=" + encodeURIComponent(CL), {{
        method: "POST",
        keepalive: true,
      }})
        .then(function (r) {{ return r.json(); }})
        .then(function () {{ try {{ window.localStorage.setItem(key, "1"); }} catch (e) {{}} }})
        .catch(function () {{}});
    }} catch (e) {{}}
  }}, []);
  return null;
}}
'''


def _nextjs_layout(ctx: dict, pages: list[tuple[str, str]], view_tracking: dict | None = None) -> str:
    title = _escape_html(ctx["title"])
    beacon = "        <ViewBeacon />\n" if view_tracking else ""
    return f"""// app/layout.tsx — Generated by Website Agent
import type {{ Metadata }} from "next";
import Navbar from "../components/Navbar";
import Footer from "../components/Footer";
import ViewBeacon from "../components/ViewBeacon";
import "./globals.css";

export const metadata: Metadata = {{
  title: "{title}",
  description: "Official website of {title}.",
}};

export default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{
  return (
    <html lang="en">
      <body className="font-['Plus_Jakarta_Sans',system-ui,sans-serif]">
        {beacon}        <Navbar />
        {{children}}
        <Footer />
      </body>
    </html>
  );
}}
"""


def _nextjs_globals_css(ctx: dict) -> str:
    c = ctx["colors"]
    tint = _mix_hex(c["primary"], "ffffff", 0.90)
    tint2 = _mix_hex(c["primary"], "ffffff", 0.96)
    return f"""@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Sora:wght@600;700;800&display=swap');
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {{
  --color-primary: {c['primary']};
  --color-secondary: {c['secondary']};
  --color-accent: {c['accent']};
  --color-bg: {c['bg']};
  --color-text: {c['text']};
  --tint: {tint};
  --tint2: {tint2};
}}

html {{ scroll-behavior: smooth; }}
body {{
  color: var(--color-text);
  background: var(--color-bg);
  font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
h1, h2, h3 {{ font-family: 'Sora', 'Plus Jakarta Sans', sans-serif; letter-spacing: -0.02em; }}

@media (prefers-reduced-motion: no-preference) {{
  .reveal {{ opacity: 0; transform: translateY(28px); transition: opacity 0.7s ease, transform 0.7s ease; }}
  .reveal.in {{ opacity: 1; transform: none; }}
}}
"""


def _nextjs_package_json(title: str) -> str:
    return json.dumps({
        "name": _slugify(title),
        "version": "0.1.0",
        "private": True,
        "scripts": {"dev": "next dev", "build": "next build", "start": "next start", "lint": "next lint"},
        "dependencies": {"next": "^14.2.0", "react": "^18.3.1", "react-dom": "^18.3.1"},
        "devDependencies": {
            "@types/node": "^20", "@types/react": "^18", "@types/react-dom": "^18",
            "autoprefixer": "^10", "postcss": "^8", "tailwindcss": "^3.4.0", "typescript": "^5",
        },
    }, indent=2)


def _nextjs_tsconfig() -> str:
    """Standard Next.js 14 TypeScript config so `next build` resolves .tsx modules."""
    return json.dumps({
        "compilerOptions": {
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
            "paths": {"@/*": ["./*"]},
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
        "exclude": ["node_modules"],
    }, indent=2)


def _nextjs_env_dts() -> str:
    """next-env.d.ts — required by Next.js for TypeScript support."""
    return (
        '/// <reference types="next" />\n'
        '/// <reference types="next/image-types/global" />\n\n'
        "// NOTE: This file should not be edited\n"
        "// see https://nextjs.org/docs/basic-features/typescript for more information.\n"
    )


def _nextjs_next_config() -> str:
    """next.config.mjs — allow remote images and skip strict type/lint gates."""
    return (
        "/** @type {import('next').NextConfig} */\n"
        "const nextConfig = {\n"
        "  images: {\n"
        "    remotePatterns: [\n"
        "      { protocol: 'https', hostname: '**' },\n"
        "      { protocol: 'http', hostname: '**' },\n"
        "    ],\n"
        "  },\n"
        "  typescript: { ignoreBuildErrors: true },\n"
        "  eslint: { ignoreDuringBuilds: true },\n"
        "};\n"
        "export default nextConfig;\n"
    )


def _nextjs_postcss_config() -> str:
    """postcss.config.mjs — Tailwind pipeline for Next.js."""
    return (
        "const config = {\n"
        "  plugins: {\n"
        "    tailwindcss: {},\n"
        "    autoprefixer: {},\n"
        "  },\n"
        "};\n"
        "export default config;\n"
    )


def _nextjs_tailwind_config(ctx: dict) -> str:
    c = ctx["colors"]
    return f"""import type {{ Config }} from "tailwindcss";

const config: Config = {{
  content: [
    "./app/**/*.{{js,ts,jsx,tsx,mdx}}",
    "./components/**/*.{{js,ts,jsx,tsx,mdx}}",
  ],
  theme: {{
    extend: {{
      colors: {{
        primary: "{c['primary']}",
        secondary: "{c['secondary']}",
        accent: "{c['accent']}",
      }},
    }},
  }},
  plugins: [],
}};

export default config;
"""


def _build_readme(ctx: dict, framework: str) -> str:
    category = ctx.get("category", "business")
    pages = WEBSITE_CATEGORIES.get(category, WEBSITE_CATEGORIES["business"])["pages"]
    page_list = ", ".join(f"`{p['nav']}` ({r})" for r, p in pages.items())
    if framework == "html":
        return f"""# {ctx['title']}

Generated by the Website Agent (category: {category}).

## Pages
{page_list}

## Run locally
Open `index.html` in a browser (or run `python -m http.server`).

## Customize
Edit `style.css` for colors and fonts. Sections live in each `*.html` page.
"""
    return f"""# {ctx['title']}

Generated by the Website Agent (category: {category}).

## Pages
{page_list}

## Run locally
```bash
npm install
npm run dev
```

## Build & deploy
```bash
npm run build
npx vercel deploy --prod
```

## Structure
- `app/page.tsx` — home page
- `app/<route>/page.tsx` — other pages (about, services, contact, ...)
- `app/layout.tsx` — root layout + metadata (Navbar + Footer)
- `app/globals.css` — Tailwind + design tokens
- `components/*.tsx` — section components (Hero, Services, Menu, Products, ...)
- `components/Navbar.tsx` — shared navigation
- `components/Footer.tsx` — shared footer
"""


def _build_instructions(framework: str, title: str) -> str:
    if framework == "html":
        return "Save files next to each other and open index.html in a browser. Ready to deploy to any static host (Vercel, Netlify, GitHub Pages)."
    return "1. npm install\n2. npm run dev — preview locally\n3. npm run build && npx vercel deploy --prod — deploy to Vercel"


def _build_website_project(
    *,
    title: str = "My Website",
    tagline: str = "",
    industry: str = "",
    services: list[str] | None = None,
    business_email: str = "",
    sections: list[str] | None = None,
    category: str = "business",
    style: str = "modern",
    color_primary: str = "#2563EB",
    framework: str = "nextjs",
    skills: list[str] | None = None,
    products: list[dict] | None = None,
    view_tracking: dict | None = None,
    logo_url: str = "",
) -> dict[str, Any]:
    """Build a complete website project dict (rel_path -> content). Deterministic, no network, no LLM.

    - `category` picks a preset page map (business, portfolio, restaurant, ecommerce,
      saas, agency, realestate, blog, education, health, event, hotel, construction,
      nonprofit). Each category gets its own multi-page site.
    - `sections` (legacy) forces a single-page build with exactly those sections.
    - `products` (list of dicts) overrides the shop catalog with real client
      products: {name, description, price, image_url, stock, sku, category}.
    - `view_tracking` ({url, workspace, client, dashboard_url}) embeds a tiny
      pageview beacon plus an Owner Login link in the generated site.
    """
    skills = [s for s in (skills or []) if s]
    title = (title or "").strip() or "My Website"
    tagline = (tagline or "").strip()
    # Services may arrive as plain strings ("Fast Delivery") or store rows
    # ({"name": ..., "description": ..., "price": ...}). Normalize to dicts so
    # the Services section can render name + description (+ price when set).
    # Always store NORMALIZED (name, description, price) tuples in ctx so the
    # HTML/Next.js card renderers iterate tuples (not characters of a string).
    # A bare list of strings like ["Fast Delivery"] would otherwise render each
    # letter as its own card (head="F", sub="a").
    services = _normalize_services(services) or _normalize_services(list(_DEFAULT_SERVICES))
    category = (category or "business").strip().lower()
    if category not in WEBSITE_CATEGORIES:
        category = "business"

    colors = dict(_WEBSITE_PALETTES.get(style, _WEBSITE_PALETTES["modern"]))
    colors["primary"] = color_primary or colors["primary"]

    if "nextjs-developer" in skills or "react-expert" in skills:
        framework = framework or "nextjs"

    ctx = {
        "title": title,
        "tagline": tagline,
        "industry": industry,
        "services": services,
        "business_email": business_email or "",
        "colors": colors,
        "style": style,
        "category": category,
        "data": _category_data(category),
        "view_tracking": view_tracking,
        "logo_url": (logo_url or "").strip(),
    }

    # Real client products (from the store) override the canned catalog.
    if products:
        raw = []
        norm: list[tuple] = []
        for p in products:
            if isinstance(p, dict):
                raw.append(p)
                norm.append(_product_to_tuple(p))
        if norm:
            ctx["data"]["products"] = norm
            ctx["data"]["products_raw"] = raw

    # Page map: route -> {"nav": label, "sections": [...]}
    if isinstance(sections, str):
        sections = [s.strip() for s in sections.split(",") if s and s.strip()]
    if sections:
        page_map = {"index": {"nav": "Home", "sections": [s.strip().lower() for s in sections if s and s.strip()]}}
        if not page_map["index"]["sections"]:
            page_map["index"]["sections"] = ["hero", "services", "about", "testimonials", "contact", "footer"]
    else:
        page_map = dict(WEBSITE_CATEGORIES[category]["pages"])

    pages = [(route, spec["nav"]) for route, spec in page_map.items()]
    all_sections: list[str] = []
    for spec in page_map.values():
        for s in spec["sections"]:
            if s not in all_sections:
                all_sections.append(s)

    # The layout already renders <Footer /> globally, so drop any "footer"
    # section from page maps to avoid rendering it twice on every page.
    if framework == "nextjs":
        for spec in page_map.values():
            spec["sections"] = [s for s in spec["sections"] if s != "footer"]
        all_sections = [s for s in all_sections if s != "footer"]

    if framework == "html":
        files: dict[str, str] = {}
        for route, spec in page_map.items():
            body = "".join(_html_section(s, ctx) for s in spec["sections"])
            fname = "index.html" if route == "index" else f"{route}.html"
            files[fname] = _html_page(ctx, body, _html_nav(pages, route, title, logo_url))
        files["style.css"] = _html_css(ctx)
        page_code = files.get("index.html", "")
        components = {}
    else:
        components = {}
        for s in all_sections:
            name = s.title().replace(" ", "")
            # Components include event handlers (forms, etc.) so they must be
            # Client Components ("use client") to serialize in the App Router.
            components[f"components/{name}.tsx"] = '"use client";\n\n' + _nextjs_component(s, ctx)
        components["components/Navbar.tsx"] = '"use client";\n\n' + _nextjs_navbar(pages, ctx)
        components["components/Footer.tsx"] = '"use client";\n\n' + _nextjs_footer(ctx)
        if view_tracking and framework == "nextjs":
            beacon_component = _nextjs_view_beacon(view_tracking)
            if beacon_component:
                components["components/ViewBeacon.tsx"] = beacon_component
        files = {
            "package.json": _nextjs_package_json(title),
            "tsconfig.json": _nextjs_tsconfig(),
            "next-env.d.ts": _nextjs_env_dts(),
            "next.config.mjs": _nextjs_next_config(),
            "postcss.config.mjs": _nextjs_postcss_config(),
            "app/layout.tsx": _nextjs_layout(ctx, pages, view_tracking),
            "app/globals.css": _nextjs_globals_css(ctx),
            "tailwind.config.ts": _nextjs_tailwind_config(ctx),
            "README.md": _build_readme(ctx, framework),
        }
        for route, spec in page_map.items():
            subdir = "" if route == "index" else f"{route}/"
            files[f"app/{subdir}page.tsx"] = _nextjs_page(route, spec["sections"], ctx, pages)
        page_code = files.get("app/page.tsx", "")
        files.update(components)

    return {
        "framework": framework,
        "style": style,
        "category": category,
        "pages": [{"route": r, "nav": s["nav"], "sections": s["sections"]} for r, s in page_map.items()],
        "sections": all_sections,
        "colors": colors,
        "title": title,
        "tagline": tagline,
        "industry": industry,
        "services": _services_display(services),
        "business_email": business_email or "",
        "skills_applied": skills,
        "page_code": page_code,
        "files": files,
        "components": components,
        "file_count": len(files),
        "instructions": _build_instructions(framework, title),
        "generated_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ANALYZE WEBSITE
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_website(url: str) -> dict[str, Any]:
    """Crawl a website and analyze structure, tech stack, meta info, navigation."""
    resp = _safe_get(url)
    if not resp:
        return {"error": f"Cannot reach {url}", "status": "failed"}

    soup = _soup(resp.text)
    parsed = urlparse(url)

    # Tech stack detection
    tech_stack = []
    html_lower = resp.text.lower()
    headers = {k.lower(): v for k, v in resp.headers.items()}

    if "next" in html_lower or "__next" in html_lower:
        tech_stack.append("Next.js")
    if "react" in html_lower or "_react" in html_lower:
        tech_stack.append("React")
    if "vue" in html_lower or "v-if" in html_lower or "v-for" in html_lower:
        tech_stack.append("Vue.js")
    if "angular" in html_lower or "ng-app" in html_lower:
        tech_stack.append("Angular")
    if "wordpress" in html_lower or "wp-content" in html_lower:
        tech_stack.append("WordPress")
    if "shopify" in html_lower or "cdn.shopify.com" in html_lower:
        tech_stack.append("Shopify")
    if "webflow" in html_lower or "webflow.com" in html_lower:
        tech_stack.append("Webflow")
    if "squarespace" in html_lower:
        tech_stack.append("Squarespace")
    if "wix" in html_lower:
        tech_stack.append("Wix")
    if "tailwind" in html_lower or "tw-" in html_lower:
        tech_stack.append("Tailwind CSS")
    if "bootstrap" in html_lower:
        tech_stack.append("Bootstrap")
    if "jquery" in html_lower:
        tech_stack.append("jQuery")
    if headers.get("server") == "cloudflare" or "cf-ray" in headers:
        tech_stack.append("Cloudflare")
    if headers.get("server", "").lower().startswith("nginx"):
        tech_stack.append("Nginx")
    if headers.get("server", "").lower().startswith("apache"):
        tech_stack.append("Apache")
    if "vercel" in headers.get("server", "").lower() or "x-vercel" in headers:
        tech_stack.append("Vercel")

    # Meta info
    meta = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name", "") or tag.get("property", "")
        content = tag.get("content", "")
        if name and content:
            meta[name] = content

    # Navigation
    nav_links = []
    nav = soup.find("nav") or soup.find(class_=re.compile(r"nav|menu", re.I))
    if nav:
        for a in nav.find_all("a", href=True):
            text = a.get_text(strip=True)[:50]
            if text:
                nav_links.append(text)

    # Pages (internal links)
    internal_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/") or parsed.netloc in href:
            full = urljoin(url, href)
            if urlparse(full).netloc == parsed.netloc:
                internal_links.add(full.split("#")[0].split("?")[0])

    # Images
    images = soup.find_all("img")
    images_without_alt = sum(1 for img in images if not img.get("alt", "").strip())

    # Headings
    headings = {}
    for level in range(1, 7):
        h_tags = soup.find_all(f"h{level}")
        if h_tags:
            headings[f"h{level}"] = len(h_tags)

    return {
        "url": url,
        "analyzed_at": _now(),
        "title": soup.title.string.strip() if soup.title and soup.title.string else "",
        "meta": meta,
        "tech_stack": tech_stack,
        "navigation": nav_links[:15],
        "page_count": len(internal_links),
        "internal_links": list(internal_links)[:50],
        "images": {"total": len(images), "without_alt": images_without_alt},
        "headings": headings,
        "headers": {k: v[:100] for k, v in headers.items() if k in ["server", "content-type", "x-powered-by"]},
        "status_code": resp.status_code,
        "redirect_chain": [r.url for r in resp.history] if resp.history else [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CHECK PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════

def check_performance(url: str) -> dict[str, Any]:
    """Check page performance: load time, size, resources."""
    import time
    start = time.time()
    resp = _safe_get(url)
    load_time = round(time.time() - start, 2)

    if not resp:
        return {"error": f"Cannot reach {url}", "status": "failed"}

    soup = _soup(resp.text)
    html_size = len(resp.content)
    css_files = len(soup.find_all("link", rel="stylesheet"))
    js_files = len(soup.find_all("script", src=True))
    images = soup.find_all("img")

    large_images = 0
    for img in images:
        try:
            if int(img.get("width", 0)) > 1000 or int(img.get("height", 0)) > 1000:
                large_images += 1
        except (ValueError, TypeError):
            pass

    inline_styles = len(soup.find_all("style"))
    inline_scripts = len(soup.find_all("script", src=False))
    encoding = resp.headers.get("Content-Encoding", "none")
    cache_control = resp.headers.get("Cache-Control", "not set")

    score = 100
    issues = []
    if load_time > 3:
        score -= 30
        issues.append(f"Slow load: {load_time}s (target: <3s)")
    elif load_time > 1:
        score -= 10
        issues.append(f"Moderate load: {load_time}s")
    if html_size > 500_000:
        score -= 20
        issues.append(f"Large HTML: {html_size/1000:.0f}KB (target: <200KB)")
    if css_files > 10:
        score -= 10
        issues.append(f"Too many CSS files: {css_files}")
    if js_files > 15:
        score -= 15
        issues.append(f"Too many JS files: {js_files}")
    if encoding == "none":
        score -= 10
        issues.append("No compression (gzip/brotli)")
    if cache_control == "not set":
        score -= 5
        issues.append("No cache-control header")
    if large_images > 3:
        score -= 10
        issues.append(f"{large_images} large images detected")

    return {
        "url": url,
        "checked_at": _now(),
        "load_time_seconds": load_time,
        "html_size_bytes": html_size,
        "html_size_kb": round(html_size / 1024, 1),
        "resources": {"css_files": css_files, "js_files": js_files, "images": len(images), "large_images": large_images, "inline_styles": inline_styles, "inline_scripts": inline_scripts},
        "optimization": {"compression": encoding, "cache_control": cache_control},
        "score": max(0, score),
        "issues": issues,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CHECK LINKS
# ═══════════════════════════════════════════════════════════════════════════════

def check_links(url: str, max_links: int = 50) -> dict[str, Any]:
    """Find broken links on a page."""
    resp = _safe_get(url)
    if not resp:
        return {"error": f"Cannot reach {url}", "status": "failed"}

    soup = _soup(resp.text)
    links = []
    for a in soup.find_all("a", href=True)[:max_links * 2]:
        href = a["href"]
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        links.append(urljoin(url, href))
    links = list(set(links))[:max_links]

    broken = []
    working = 0
    for link in links:
        try:
            r = requests.head(link, headers=_HEADERS, timeout=10, allow_redirects=True)
            if r.status_code >= 400:
                broken.append({"url": link, "status": r.status_code})
            else:
                working += 1
        except Exception:
            broken.append({"url": link, "status": "timeout"})

    return {
        "url": url,
        "checked_at": _now(),
        "total_links_checked": len(links),
        "working": working,
        "broken": broken,
        "broken_count": len(broken),
        "health_score": round((working / max(len(links), 1)) * 100),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SECURITY CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def security_check(url: str) -> dict[str, Any]:
    """Check security headers."""
    resp = _safe_get(url)
    if not resp:
        return {"error": f"Cannot reach {url}", "status": "failed"}

    headers = {k.lower(): v for k, v in resp.headers.items()}
    issues = []
    score = 100

    security_headers = {
        "strict-transport-security": {"name": "HSTS", "critical": True},
        "content-security-policy": {"name": "Content Security Policy", "critical": True},
        "x-frame-options": {"name": "X-Frame-Options", "critical": False},
        "x-content-type-options": {"name": "X-Content-Type-Options", "critical": False},
        "x-xss-protection": {"name": "X-XSS-Protection", "critical": False},
        "referrer-policy": {"name": "Referrer Policy", "critical": False},
        "permissions-policy": {"name": "Permissions Policy", "critical": False},
    }

    present = {}
    missing = []
    for header, info in security_headers.items():
        if header in headers:
            present[header] = headers[header][:100]
        else:
            missing.append(info["name"])
            score -= 15 if info["critical"] else 5
            issues.append(f"Missing {info['name']}")

    is_https = url.startswith("https://")
    if not is_https:
        score -= 20
        issues.append("Not using HTTPS")

    x_powered = headers.get("x-powered-by", "")
    if x_powered:
        score -= 5
        issues.append(f"X-Powered-By exposed: {x_powered}")

    return {
        "url": url,
        "checked_at": _now(),
        "is_https": is_https,
        "score": max(0, score),
        "headers_present": present,
        "headers_missing": missing,
        "issues": issues,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TECH STACK ADVISOR
# ═══════════════════════════════════════════════════════════════════════════════

def tech_stack_advisor(
    site_type: str = "",
    needs_ecommerce: bool = False,
    needs_blog: bool = False,
    budget: str = "medium",
    client_preference: str = "",
) -> dict[str, Any]:
    """Recommend tech stack based on project needs."""
    recommendations = []

    if needs_ecommerce:
        if budget == "low":
            recommendations.append({"stack": "Shopify", "reason": "Quick setup, managed hosting, low maintenance"})
        else:
            recommendations.append({"stack": "Next.js + Stripe", "reason": "Full control, better performance, scalable"})

    if needs_blog:
        if budget == "low":
            recommendations.append({"stack": "WordPress", "reason": "Easy content management, huge plugin ecosystem"})
        else:
            recommendations.append({"stack": "Next.js + MDX", "reason": "Fast, SEO-friendly, developer-friendly"})

    if site_type in ("landing", "portfolio", "saas"):
        recommendations.append({"stack": "Next.js + Tailwind", "reason": "Fast, modern, great SEO, easy to deploy on Vercel"})
    if site_type in ("corporate", "enterprise"):
        recommendations.append({"stack": "Next.js + Headless CMS", "reason": "Scalable, secure, flexible content management"})
    if site_type in ("webapp", "dashboard"):
        recommendations.append({"stack": "Next.js + React + PostgreSQL", "reason": "Full-stack, type-safe, great DX"})

    if client_preference:
        recommendations.append({"stack": client_preference, "reason": "Client preference"})

    if not recommendations:
        recommendations.append({"stack": "Next.js + Tailwind + Vercel", "reason": "Default modern stack"})

    hosting = "Vercel" if any("Next.js" in r["stack"] for r in recommendations) else "AWS/Netlify"
    cms = "None (code-based)" if not needs_blog else ("Sanity/Contentful" if budget != "low" else "WordPress")

    return {
        "site_type": site_type,
        "needs_ecommerce": needs_ecommerce,
        "needs_blog": needs_blog,
        "budget": budget,
        "recommendations": recommendations,
        "hosting": hosting,
        "cms": cms,
        "design_tool": "Figma",
        "version_control": "Git + GitHub",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. DESIGN PLANNER
# ═══════════════════════════════════════════════════════════════════════════════

def design_planner(
    site_type: str = "landing",
    pages: str = "home, about, services, contact",
    style: str = "modern",
) -> dict[str, Any]:
    """Plan site architecture, navigation, page structure."""
    page_list = [p.strip() for p in pages.split(",") if p.strip()]

    page_structures = {
        "home": {"sections": ["hero", "features/benefits", "testimonials", "cta", "footer"], "purpose": "First impression, convert visitors"},
        "about": {"sections": ["hero", "story", "team", "values", "cta"], "purpose": "Build trust, show brand personality"},
        "services": {"sections": ["hero", "service_list", "process", "pricing", "cta"], "purpose": "Showcase offerings, drive inquiries"},
        "contact": {"sections": ["hero", "contact_form", "map", "details", "social_links"], "purpose": "Enable communication, capture leads"},
        "blog": {"sections": ["hero", "featured_posts", "post_grid", "pagination"], "purpose": "Content marketing, SEO"},
        "pricing": {"sections": ["hero", "pricing_cards", "faq", "cta"], "purpose": "Show plans, drive conversions"},
        "portfolio": {"sections": ["hero", "project_grid", "case_study", "cta"], "purpose": "Showcase work, build credibility"},
    }

    planned_pages = [{"name": p, **page_structures.get(p, {"sections": ["hero", "content", "cta"], "purpose": "Custom page"})} for p in page_list]
    nav = {"primary": page_list[:6], "footer": page_list}

    palettes = {
        "modern": {"primary": "#2563EB", "secondary": "#1E293B", "accent": "#F59E0B", "bg": "#FFFFFF"},
        "minimal": {"primary": "#000000", "secondary": "#666666", "accent": "#2563EB", "bg": "#FFFFFF"},
        "bold": {"primary": "#DC2626", "secondary": "#1E293B", "accent": "#F59E0B", "bg": "#FFFFFF"},
        "warm": {"primary": "#D97706", "secondary": "#92400E", "accent": "#059669", "bg": "#FFFBEB"},
        "tech": {"primary": "#7C3AED", "secondary": "#1E1B4B", "accent": "#06B6D4", "bg": "#FFFFFF"},
    }

    return {
        "site_type": site_type,
        "style": style,
        "pages": planned_pages,
        "navigation": nav,
        "color_palette": palettes.get(style, palettes["modern"]),
        "typography": {"heading": "Inter" if style == "modern" else "Poppins", "body": "Inter"},
        "responsive_breakpoints": {"mobile": "375px", "tablet": "768px", "desktop": "1024px", "wide": "1280px"},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CHECK ACCESSIBILITY
# ═══════════════════════════════════════════════════════════════════════════════

def check_accessibility(url: str) -> dict[str, Any]:
    """Basic accessibility checks: alt text, headings, ARIA, color contrast hints."""
    resp = _safe_get(url)
    if not resp:
        return {"error": f"Cannot reach {url}", "status": "failed"}

    soup = _soup(resp.text)
    issues = []
    score = 100

    images = soup.find_all("img")
    no_alt = [img.get("src", "")[:80] for img in images if not img.get("alt", "").strip()]
    if no_alt:
        score -= min(15, len(no_alt) * 3)
        issues.append(f"{len(no_alt)} images missing alt text")

    links = soup.find_all("a")
    empty_links = [a.get("href", "")[:80] for a in links if not a.get_text(strip=True) and not a.find("img") and not a.get("aria-label")]
    if empty_links:
        score -= min(10, len(empty_links) * 2)
        issues.append(f"{len(empty_links)} links without accessible text")

    inputs = soup.find_all(["input", "textarea", "select"])
    unlabeled = 0
    for inp in inputs:
        if inp.get("type") in ("hidden", "submit", "button"):
            continue
        inp_id = inp.get("id", "")
        has_label = bool(inp_id and soup.find("label", attrs={"for": inp_id}))
        has_aria = bool(inp.get("aria-label") or inp.get("aria-labelledby"))
        if not has_label and not has_aria:
            unlabeled += 1
    if unlabeled:
        score -= min(10, unlabeled * 2)
        issues.append(f"{unlabeled} form inputs without labels")

    headings = []
    for level in range(1, 7):
        for _ in soup.find_all(f"h{level}"):
            headings.append(level)
    skipped = 0
    for i in range(1, len(headings)):
        if headings[i] - headings[i - 1] > 1:
            skipped += 1
    if skipped:
        score -= min(10, skipped * 3)
        issues.append(f"Heading hierarchy skipped {skipped} levels")

    html_tag = soup.find("html")
    has_lang = bool(html_tag and html_tag.get("lang"))
    if not has_lang:
        score -= 5
        issues.append("Missing lang attribute on <html>")

    landmarks = soup.find_all(attrs={"role": True})
    if not landmarks and not soup.find("nav") and not soup.find("main"):
        score -= 5
        issues.append("No ARIA landmarks or semantic HTML")

    return {
        "url": url,
        "checked_at": _now(),
        "score": max(0, score),
        "images_total": len(images),
        "images_without_alt": len(no_alt),
        "empty_links": len(empty_links),
        "unlabeled_inputs": unlabeled,
        "heading_skips": skipped,
        "has_lang": has_lang,
        "issues": issues,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 8. COMPETITOR SITES
# ═══════════════════════════════════════════════════════════════════════════════

def competitor_sites(urls: list[str]) -> dict[str, Any]:
    """Scan multiple competitor websites for comparison."""
    results = []
    for url in urls[:5]:
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url
        try:
            analysis = analyze_website(url)
            perf = check_performance(url)
            results.append({
                "url": url,
                "title": analysis.get("title", ""),
                "tech_stack": analysis.get("tech_stack", []),
                "page_count": analysis.get("page_count", 0),
                "performance_score": perf.get("score", 0),
                "load_time": perf.get("load_time_seconds", 0),
            })
        except Exception as e:
            results.append({"url": url, "error": str(e)})

    return {"analyzed_at": _now(), "competitors": results, "count": len(results)}


# ═══════════════════════════════════════════════════════════════════════════════
# 9. RESPONSIVE CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def responsive_check(url: str) -> dict[str, Any]:
    """Check mobile responsiveness: viewport, media queries, mobile-friendly."""
    resp = _safe_get(url)
    if not resp:
        return {"error": f"Cannot reach {url}", "status": "failed"}

    soup = _soup(resp.text)
    issues = []
    score = 100

    viewport = soup.find("meta", attrs={"name": "viewport"})
    has_viewport = bool(viewport)
    viewport_content = viewport.get("content", "") if viewport else ""
    if not has_viewport:
        score -= 30
        issues.append("Missing viewport meta tag")
    elif "width=device-width" not in viewport_content:
        score -= 10
        issues.append("Viewport missing device-width")

    # CSS media queries
    media_queries = 0
    for tag in soup.find_all("style"):
        text = tag.string or ""
        media_queries += len(re.findall(r"@media", text))
    for tag in soup.find_all(style=True)[:20]:
        if "max-width" in tag.get("style", "") or "min-width" in tag.get("style", ""):
            media_queries += 1
    if media_queries == 0:
        score -= 15
        issues.append("No CSS media queries found (may not be responsive)")

    # Fixed widths
    fixed_widths = 0
    for tag in soup.find_all(style=True)[:30]:
        if re.search(r"width:\s*\d{4,}px", tag.get("style", "")):
            fixed_widths += 1
    if fixed_widths > 3:
        score -= 10
        issues.append(f"{fixed_widths} elements with fixed widths >1000px")

    # Large images
    images = soup.find_all("img")
    large_unconstrained = 0
    for img in images:
        try:
            if int(img.get("width", 0)) > 800:
                large_unconstrained += 1
        except (ValueError, TypeError):
            pass
    if large_unconstrained > 2:
        score -= 10
        issues.append(f"{large_unconstrained} large images may overflow on mobile")

    return {
        "url": url,
        "checked_at": _now(),
        "score": max(0, score),
        "has_viewport": has_viewport,
        "viewport_content": viewport_content,
        "media_queries_found": media_queries,
        "fixed_width_elements": fixed_widths,
        "images_total": len(images),
        "issues": issues,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 10. CHECK SSL
# ═══════════════════════════════════════════════════════════════════════════════

def check_ssl(url: str) -> dict[str, Any]:
    """Check SSL certificate status."""
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    hostname = parsed.hostname or ""
    port = parsed.port or 443

    if not hostname:
        return {"error": "Invalid URL", "status": "failed"}

    result: dict[str, Any] = {
        "url": url,
        "hostname": hostname,
        "checked_at": _now(),
        "is_https": url.startswith("https"),
        "ssl_valid": False,
        "issuer": "",
        "subject": "",
        "not_before": "",
        "not_after": "",
        "days_until_expiry": 0,
        "protocol": "",
        "issues": [],
    }

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                result["ssl_valid"] = True
                result["protocol"] = ssock.version() or ""

                issuer_dict = dict(x[0] for x in cert.get("issuer", []))
                result["issuer"] = issuer_dict.get("organizationName", issuer_dict.get("commonName", ""))

                subject_dict = dict(x[0] for x in cert.get("subject", []))
                result["subject"] = subject_dict.get("commonName", "")

                result["not_before"] = cert.get("notBefore", "")
                not_after = cert.get("notAfter", "")
                result["not_after"] = not_after

                if not_after:
                    try:
                        expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                        days = (expiry - datetime.utcnow()).days
                        result["days_until_expiry"] = days
                        if days < 30:
                            result["issues"].append(f"SSL expires in {days} days!")
                        elif days < 90:
                            result["issues"].append(f"SSL expires in {days} days — renew soon")
                    except ValueError:
                        pass

    except ssl.SSLCertVerificationError as e:
        result["issues"].append(f"SSL verification failed: {str(e)[:100]}")
    except socket.timeout:
        result["issues"].append("Connection timeout")
    except socket.gaierror:
        result["issues"].append(f"Cannot resolve hostname: {hostname}")
    except Exception as e:
        result["issues"].append(f"SSL check failed: {str(e)[:100]}")

    if not result["is_https"]:
        result["issues"].insert(0, "Site is not using HTTPS")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 11. GENERATE CODE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_code(
    page_type: str = "landing",
    framework: str = "nextjs",
    style: str = "modern",
    sections: str = "hero,features,cta,footer",
    color_primary: str = "#2563EB",
    title: str = "My Website",
    tagline: str = "",
    services: str = "",
    business_email: str = "",
    output_dir: str = "",
    skills: list[str] | None = None,
) -> dict[str, Any]:
    """Generate starter code for a website page (Next.js or HTML/CSS).

    Uses the shared project builder so output includes real files and
    business info (services, email, tagline). Pass output_dir to write
    the project to disk.
    """
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    project = _build_website_project(
        title=title,
        tagline=tagline,
        services=[s.strip() for s in services.split(",") if s.strip()],
        business_email=business_email,
        sections=[s.strip() for s in sections.split(",") if s.strip()],
        style=style,
        color_primary=color_primary,
        framework=framework,
        skills=skills or [],
    )
    if output_dir:
        written = []
        for rel_path, content in project["files"].items():
            full_path = os.path.join(output_dir, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(rel_path)
        project["status"] = "built"
        project["output_dir"] = output_dir
        project["files_written"] = written
    return project


def build_site(
    title: str = "My Website",
    tagline: str = "",
    industry: str = "",
    sections: str = "",
    category: str = "business",
    style: str = "modern",
    color_primary: str = "#2563EB",
    framework: str = "nextjs",
    services: str = "",
    business_email: str = "",
    output_dir: str = "",
    skills: list[str] | None = None,
    products: list[dict] | None = None,
    view_tracking: dict | None = None,
    logo_url: str = "",
) -> dict[str, Any]:
    """Build a complete website project on disk from business info.

    Writes real files (Next.js project or HTML) and returns the file list.
    Default is a multi-page site picked by `category` (business, portfolio,
    restaurant, ecommerce, saas, agency, realestate, blog, education, health,
    event, hotel, construction, nonprofit). Pass `sections` to force a
    single-page build with exactly those sections. Pass `products` (list of
    dicts) to seed the shop with a real client catalog. Pass `view_tracking`
    ({url, workspace, client, dashboard_url}) to embed a pageview beacon and
    an Owner Login link in the generated site.
    """
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    if isinstance(services, str):
        svc_list = [s.strip() for s in services.split(",") if s.strip()]
    elif isinstance(services, list):
        svc_list = services
    else:
        svc_list = []
    project = _build_website_project(
        title=title,
        tagline=tagline,
        industry=industry,
        services=svc_list,
        business_email=business_email,
        sections=[s.strip() for s in sections.split(",") if s.strip()] if sections else None,
        category=category,
        style=style,
        color_primary=color_primary,
        framework=framework,
        skills=skills or [],
        products=products,
        view_tracking=view_tracking,
        logo_url=logo_url,
    )
    if not output_dir:
        output_dir = os.path.join("generated_sites", _slugify(project["title"]))
    written = []
    for rel_path, content in project["files"].items():
        full_path = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(rel_path)
    preview = "open index.html in a browser" if project["framework"] == "html" else f"run `npm run dev` in {output_dir}"
    return {
        **project,
        "status": "built",
        "output_dir": output_dir,
        "files_written": written,
        "file_count": len(written),
        "preview_url_hint": preview,
    }


def build_site_from_store(
    workspace: str = "Default",
    client: str = "Client",
    deploy: bool = True,
) -> dict[str, Any]:
    """Build (and optionally deploy) a client's storefront from their store.

    Reads the client's products + settings from the store (PocketBase via the
    gateway) and regenerates their ecommerce site with the real catalog. When
    `deploy` is True, publishes to Vercel and returns the live URL. This is
    the Website Agent's store-aware path: client adds a product in the portal,
    the agent pushes it to the live site.
    """
    from admin.store import store_store

    products = store_store.list_products(workspace, client, active_only=True)
    services = store_store.list_services(workspace, client, active_only=True)
    settings = store_store.get_settings(workspace, client)

    title = (settings.get("store_name") or "").strip() or client
    # Embed a pageview beacon + Owner Login link in the live storefront.
    from admin.config.settings import EC2_BACKEND_URL, STORE_DASHBOARD_BASE_URL
    view_tracking = {
        "url": (os.getenv("EC2_BACKEND_URL") or EC2_BACKEND_URL).rstrip("/"),
        "workspace": workspace,
        "client": client,
        "dashboard_url": f"{STORE_DASHBOARD_BASE_URL}/store/{workspace}",
    }
    result = build_site(
        title=title,
        tagline=(settings.get("tagline") or "").strip(),
        category=(settings.get("category") or "ecommerce").strip() or "ecommerce",
        style=(settings.get("style") or "modern").strip(),
        color_primary=(settings.get("color_primary") or "#2563EB").strip(),
        framework=(settings.get("framework") or "nextjs").strip(),
        business_email=(settings.get("contact_email") or "").strip(),
        logo_url=(settings.get("logo_url") or "").strip(),
        output_dir=os.path.join("generated_sites", "store_" + _slugify(workspace) + "_" + _slugify(client)),
        products=products,
        services=services,
        view_tracking=view_tracking,
    )

    if not deploy:
        return {
            **result,
            "deployed": False,
            "product_count": len(products),
            "service_count": len(services),
            "sync": {"workspace": workspace, "client": client},
        }

    deployed = deploy_vercel(
        project_path=result["output_dir"],
        project_name=f"store-{_slugify(workspace)}",
    )
    site_url = deployed.get("url") or ""
    if deployed.get("status") == "deployed" and site_url:
        try:
            from admin.agency.website_supabase import upsert_website_build, log_website_event
            upsert_website_build(workspace, client, status="deployed", site_url=site_url,
                                 current_stage="store-sync", framework=result["framework"])
            log_website_event(workspace, client, "store_sync",
                              f"Store synced: {len(products)} products, {len(services)} services live at {site_url}")
        except Exception as e:  # noqa: BLE001
            logger.warning("build_site_from_store: persistence log failed: %s", e)
    return {
        **result,
        "deployed": deployed.get("status") == "deployed",
        "deploy": deployed,
        "product_count": len(products),
        "service_count": len(services),
        "site_url": site_url,
        "sync": {"workspace": workspace, "client": client},
    }


def update_store_site(
    workspace: str = "Default",
    client: str = "Client",
    deploy: bool = True,
) -> dict[str, Any]:
    """UPDATE a client's existing storefront in place (no full rebuild).

    Reads the client's current products + settings from the store and patches
    ONLY the already-generated site files (shop.html product grid + logo in the
    nav of every page). It does NOT regenerate the whole project, CSS, nav
    links, or framework scaffolding — so the client's existing site layout is
    preserved; only the catalog and logo change.

    This is the Website Agent's store-aware *update* path: the client edits a
    product or logo in their portal, the agent pushes just that change live.
    """
    from admin.store import store_store

    products = store_store.list_products(workspace, client, active_only=True)
    services = store_store.list_services(workspace, client, active_only=True)
    settings = store_store.get_settings(workspace, client)

    out_dir = os.path.join(
        "generated_sites", "store_" + _slugify(workspace) + "_" + _slugify(client)
    )
    if not os.path.isdir(out_dir):
        # No existing site yet — fall back to a full build.
        logger.info("update_store_site: no existing site at %s, building fresh", out_dir)
        return build_site_from_store(workspace=workspace, client=client, deploy=deploy)

    title = (settings.get("store_name") or "").strip() or client
    logo_url = (settings.get("logo_url") or "").strip()
    color_primary = (settings.get("color_primary") or "#2563EB").strip() or "#2563EB"

    # Mirror _build_website_project's product wiring so _html_section renders
    # the real store catalog (tuples for cards + raw dicts for image/stock).
    norm_products = [_product_to_tuple(p) for p in products if isinstance(p, dict)]
    ctx = {
        "title": title,
        "tagline": (settings.get("tagline") or "").strip(),
        "business_email": (settings.get("contact_email") or "").strip(),
        "services": [(s.get("name", ""), s.get("description", "")) for s in services],
        "colors": {"primary": color_primary, "accent": color_primary},
        "data": {
            "products": norm_products or list(_SECTION_FALLBACK_CONTENT["products"]),
            "products_raw": products,
        },
    }
    try:
        section = _html_section("products", ctx)
        new_grid = section.split('<div class="grid">', 1)[1].rsplit("</div>", 1)[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("update_store_site: grid render failed: %s", e)
        return build_site_from_store(workspace=workspace, client=client, deploy=deploy)

    updated_files = []
    for fname in ("shop.html", "index.html"):
        fpath = os.path.join(out_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            html = _read_text(fpath)
            html = _patch_products_grid(html, new_grid)
            html = _patch_logo(html, logo_url, title)
            _write_text(fpath, html)
            updated_files.append(fname)
        except Exception as e:  # noqa: BLE001
            logger.warning("update_store_site: failed to patch %s: %s", fname, e)

    try:
        from admin.agency.website_supabase import log_website_event
        log_website_event(workspace, client, "store_update",
                          f"Store updated in place: {len(products)} products, logo={'set' if logo_url else 'none'}")
    except Exception as e:  # noqa: BLE001
        logger.warning("update_store_site: persistence log failed: %s", e)

    result: dict[str, Any] = {
        "status": "updated",
        "mode": "in_place_update",
        "workspace": workspace,
        "client": client,
        "output_dir": out_dir,
        "files_updated": updated_files,
        "product_count": len(products),
        "logo_set": bool(logo_url),
        "rebuilt": False,
    }
    if not deploy:
        return result

    deployed = deploy_vercel(
        project_path=out_dir,
        project_name=f"store-{_slugify(workspace)}",
    )
    site_url = deployed.get("url") or ""
    result["deployed"] = deployed.get("status") == "deployed"
    result["deploy"] = deployed
    result["site_url"] = site_url
    return result


def _patch_products_grid(html: str, new_grid_inner: str) -> str:
    """Replace the inner content of the products section's <div class="grid">."""
    import re

    # The product grid is `<div class="grid">...</div>` whose closing tag is
    # immediately followed by `<p class="hint">` (or `</section>`). Match the
    # whole grid block and swap only its inner cards.
    pat = re.compile(r"<div class=\"grid\">.*?</div>(?=\s*<p class=\"hint\">|\s*</section>)", re.S)

    def repl(m: "re.Match[str]") -> str:
        return f'<div class="grid">{new_grid_inner}</div>'

    return pat.sub(repl, html, count=1)


def _patch_logo(html: str, logo_url: str, title: str) -> str:
    """Update the brand/nav logo. If a logo is set, render <img>; else text brand."""
    import re

    if logo_url:
        logo_html = f'<img class="brand-logo" src="{_escape_html(logo_url)}" alt="{_escape_html(title)}"/>'
    else:
        logo_html = f'<span class="brand">{_escape_html(title)}</span>'
    html = re.sub(r'<img class="brand-logo"[^>]*/>', logo_html, html)
    html = re.sub(r'<span class="brand">[^<]*</span>', logo_html, html)
    return html


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. DEPLOY TO VERCEL (build -> host -> domain, full pipeline)
# ═══════════════════════════════════════════════════════════════════════════════

_VERCEL_API = "https://api.vercel.com"


def _get_vercel_token() -> str | None:
    """Resolve a Vercel auth token: env VERCEL_TOKEN, then CLI auth files.

    Works on any host (EC2 included) without an interactive login.
    """
    tok = os.environ.get("VERCEL_TOKEN", "").strip()
    if tok:
        return tok
    # Fallback: read VERCEL_TOKEN from the backend .env (same pattern as
    # website_supabase._env) so publishing works on EC2 without systemd edits.
    for p in (
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"),
        "/home/ubuntu/sba-backend/.env",
    ):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("VERCEL_TOKEN="):
                        t = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if t:
                            return t
        except Exception:  # noqa: BLE001
            continue
    candidates = [
        os.path.join(os.environ.get("APPDATA", ""), "com.vercel.cli", "Data", "auth.json"),
        os.path.join(os.environ.get("APPDATA", ""), "com.vercel.cli", "auth.json"),
        os.path.join(os.environ.get("XDG_CONFIG_HOME", ""), "com.vercel.cli", "Data", "auth.json"),
        os.path.expanduser("~/.vercel/auth.json"),
        os.path.expanduser("~/.local/share/com.vercel.cli/auth.json"),
        os.path.expanduser("~/.config/com.vercel.cli/auth.json"),
    ]
    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            t = data.get("token") or (data.get("tokens", [{}])[0].get("token") if data.get("tokens") else "")
            if t:
                return t.strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("vercel auth read failed %s: %s", path, e)
    return None


def _vercel_headers(token: str | None = None) -> dict[str, str]:
    tok = token or _get_vercel_token()
    if not tok:
        return {}
    return {"Authorization": f"Bearer {tok}"}


def _extract_vercel_url(output: str) -> str:
    """Pull the real deployment URL from vercel CLI output.

    Prefers the stable production alias (the "Aliased https://..." line that
    `vercel --prod` prints) so site_url does not change on every publish,
    then falls back to the deployment-specific URL. Ignores version banners
    like "Vercel CLI 54.13.0 (Node.js 25.8.1)".
    """
    url_like = re.compile(r"https://[^\s,;]+")
    candidates: list[str] = []
    aliased: list[str] = []
    for line in output.split("\n"):
        line = line.strip()
        found = url_like.findall(line)
        if not found:
            continue
        for u in found:
            u = u.rstrip(".,;)")
            # Stop at the vercel domain so trailing banner text ("Vercel CLI ...")
            # that the CLI appends right after the URL is not glued onto it.
            m = re.match(r"(https://[^\s,;]*?\.vercel\.(?:app|com))", u, re.IGNORECASE)
            if m:
                u = m.group(1)
            low = u.lower()
            if "vercel.app" in low or "vercel.com" in low:
                candidates.append(u)
                if "aliased" in line.lower():
                    aliased.append(u)
    # The production alias (the "Aliased https://..." line from `vercel --prod`)
    # is stable across publishes, so prefer it over the deployment URL.
    for u in aliased:
        low = u.lower()
        if ".vercel.app" in low:
            return u
    # Prefer the public *.vercel.app URL over dashboard/inspect links.
    for u in candidates:
        low = u.lower()
        if ".vercel.app" in low:
            return u
    for u in candidates:
        if "vercel.com/" in u.lower():
            return u
    return candidates[0] if candidates else ""


def deploy_vercel(
    project_path: str = ".",
    project_name: str = "",
    prod: bool = True,
    env_vars: str = "",
    token: str = "",
) -> dict[str, Any]:
    """Deploy a project to Vercel (frontend+backend). Uses vercel CLI or npx.

    Works three ways:
    - No token + real CLI installed: uses the existing local `vercel login`
      session (CLI auth).
    - Token given (or VERCEL_TOKEN env / backend .env / CLI auth file): passes
      `--token`, so the same call works on EC2 and other servers without
      interactive login.
    - No vercel binary: falls back to `npx --yes vercel` (Node >= 16), which
      keeps EC2 deploys working without a global install. npx requires a token.
    """
    resolved_token = _explicit_vercel_token(token)
    cli_bin = _vercel_cli_bin()
    npx_bin = _npx_bin()
    cli_available = bool(cli_bin)
    npx_available = bool(npx_bin)

    if not resolved_token and not cli_available:
        # Server path (EC2): the token may live in the backend .env or an auth
        # file instead of the process environment. A real CLI is preferred with
        # its own logged-in session, so only fall back when no binary exists.
        resolved_token = _get_vercel_token()

    if not (cli_available or npx_available):
        return {
            "error": "No vercel CLI or npx available. Install Node (for npx) or run "
            "`npm i -g vercel`, and set VERCEL_TOKEN for token-based deploys.",
            "status": "failed",
            "hint": "export VERCEL_TOKEN=<token>  # https://vercel.com/account/tokens",
        }
    if npx_available and not cli_available and not resolved_token:
        return {
            "error": "VERCEL_TOKEN is required when deploying via npx "
            "(no local vercel login session exists).",
            "status": "failed",
            "hint": "Set VERCEL_TOKEN in the backend .env or as an env var.",
        }

    # Build the command: prefer a real CLI binary, else npx --yes vercel.
    if cli_available:
        cmd = [cli_bin, "--yes"]
    else:
        cmd = [npx_bin, "--yes", "vercel", "--yes"]
    if resolved_token:
        cmd.extend(["--token", resolved_token])
    if prod:
        cmd.append("--prod")
    if project_name:
        cmd.extend(["--name", project_name])

    # Parse env vars (KEY=VALUE,KEY2=VALUE2)
    env_list = []
    if env_vars:
        for pair in env_vars.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                env_list.append(f"{k.strip()}={v.strip()}")

    for env in env_list:
        cmd.extend(["--env", env])

    # Deploy
    try:
        deploy_start = time.time()
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=600,
            cwd=project_path if os.path.isdir(project_path) else ".",
        )
        deploy_time = round(time.time() - deploy_start, 1)

        output = result.stdout + result.stderr
        url = _extract_vercel_url(output)

        return {
            "status": "deployed" if result.returncode == 0 else "failed",
            "project_name": project_name or "auto",
            "project_path": project_path,
            "production": prod,
            "url": url,
            "deploy_time_seconds": deploy_time,
            "output": output[:3000],
            "deployed_at": _now(),
            "token_used": bool(resolved_token),
        }
    except subprocess.TimeoutExpired:
        return {"error": "Deploy timed out (600s limit)", "status": "failed"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "status": "failed"}


_VERCEL_CLI_PATH: str | None = None


def _vercel_cli_bin() -> str | None:
    """Resolve the vercel CLI executable (handles Windows .cmd shims)."""
    global _VERCEL_CLI_PATH
    if _VERCEL_CLI_PATH is None:
        try:
            _VERCEL_CLI_PATH = shutil.which("vercel")
        except Exception:  # noqa: BLE001
            _VERCEL_CLI_PATH = ""
    return _VERCEL_CLI_PATH or None


_NPX_PATH: str | None = None


def _npx_bin() -> str | None:
    """Resolve npx (Node package runner) for token-based deploys without a
    global vercel install (EC2). Returns the executable path or None."""
    global _NPX_PATH
    if _NPX_PATH is None:
        try:
            _NPX_PATH = shutil.which("npx") or shutil.which("npx.cmd") or ""
        except Exception:  # noqa: BLE001
            _NPX_PATH = ""
    return _NPX_PATH or None


def _explicit_vercel_token(token: str = "") -> str:
    """Only an explicitly supplied token counts (arg or VERCEL_TOKEN env).

    The CLI auth file is NOT read here, because on Windows the live session
    lives in the CLI's secret store while auth.json may hold a stale token.
    """
    t = token or os.environ.get("VERCEL_TOKEN", "").strip()
    return t.strip()


def _run_vercel_cli(args: list[str], cwd: str = ".", timeout: int = 120) -> tuple[int, str, str]:
    """Run the vercel CLI using its own logged-in session (no --token needed)."""
    exe = _vercel_cli_bin()
    if not exe:
        return 127, "", "vercel CLI not found. Install: npm i -g vercel"
    try:
        result = subprocess.run(
            [exe, *args],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd if os.path.isdir(cwd) else ".",
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"vercel CLI timed out after {timeout}s"


def connect_domain(project: str, domain: str, token: str = "") -> dict[str, Any]:
    """Attach a custom domain to a Vercel project and return the DNS records.

    Two modes:
    - Token available (VERCEL_TOKEN env / arg / CLI auth file): uses the REST
      API, so it works on EC2 and other servers without a CLI login.
    - No token: falls back to the local `vercel` CLI session
      (`vercel domains add`), which uses the existing login.
    Returns exact DNS records the client must add (A for apex, CNAME for sub).
    """
    domain = (domain or "").strip().lower().replace("http://", "").replace("https://", "")
    domain = domain.split("/")[0]
    if not project:
        return {"error": "project name is required (deploy first, then use its project name)", "status": "failed"}
    if not domain:
        return {"error": "domain is required (e.g. example.com or www.example.com)", "status": "failed"}

    is_apex = domain.count(".") == 1  # example.com -> apex; www.example.com -> subdomain
    dns_records = [
        {
            "type": "A" if is_apex else "CNAME",
            "name": "@" if is_apex else "www" if domain.startswith("www.") else domain.split(".", 1)[0],
            "value": "76.76.21.21" if is_apex else "cname.vercel-dns.com",
            "ttl": "600",
            "purpose": "Point domain to your Vercel deployment (hosting)",
        },
        {
            "type": "TXT",
            "name": "@",
            "value": "verification=vercel",
            "ttl": "600",
            "purpose": "Vercel domain verification (add this TXT record)",
        },
    ]

    explicit_token = _explicit_vercel_token(token)
    cli_available = bool(_vercel_cli_bin())
    if explicit_token:
        headers = _vercel_headers(explicit_token)
        headers["Content-Type"] = "application/json"
        try:
            resp = requests.post(
                f"{_VERCEL_API}/v9/projects/{project}/domains",
                headers=headers,
                json={"name": domain},
                timeout=30,
            )
            payload = resp.json() if resp.content else {}
            ok = resp.status_code in (200, 201)
            if not ok and payload.get("error", {}).get("code") == "domain_already_in_use":
                ok = True  # already attached is fine
            return {
                "status": "connected" if ok else "failed",
                "domain": domain,
                "project": project,
                "mode": "api",
                "api_status_code": resp.status_code,
                "vercel_response": payload,
                "dns_records": dns_records,
                "next_steps": [
                    "Add the DNS records above at your domain registrar or DNS provider (GoDaddy, Namecheap, Cloudflare, etc.)",
                    "Then call /api/website/domain/status to check when the domain verifies.",
                    "Vercel auto-provisions SSL once DNS propagates (usually 5 min - 48h).",
                ],
                "connected_at": _now(),
            }
        except Exception as e:  # noqa: BLE001
            return {"error": f"Domain connect API failed: {e}", "status": "failed"}

    # No explicit token: use local CLI session (vercel domains add).
    if not cli_available:
        # Last resort: auth file token via REST API (works on servers w/o CLI).
        file_token = _get_vercel_token()
        if file_token:
            try:
                headers = _vercel_headers(file_token)
                headers["Content-Type"] = "application/json"
                resp = requests.post(
                    f"{_VERCEL_API}/v9/projects/{project}/domains",
                    headers=headers,
                    json={"name": domain},
                    timeout=30,
                )
                payload = resp.json() if resp.content else {}
                ok = resp.status_code in (200, 201)
                if not ok and payload.get("error", {}).get("code") == "domain_already_in_use":
                    ok = True
                return {
                    "status": "connected" if ok else "failed",
                    "domain": domain,
                    "project": project,
                    "mode": "api-file-token",
                    "api_status_code": resp.status_code,
                    "vercel_response": payload,
                    "dns_records": dns_records,
                    "next_steps": [
                        "Add the DNS records above at your domain registrar or DNS provider.",
                        "Then call /api/website/domain/status to check when the domain verifies.",
                    ],
                    "connected_at": _now(),
                }
            except Exception as e:  # noqa: BLE001
                return {"error": f"Domain connect failed: {e}", "status": "failed"}
    code, out, err = _run_vercel_cli(["domains", "add", domain], timeout=180)
    output = out + err
    ok = code == 0
    if not ok and "already" in output.lower():
        ok = True  # domain already registered on this account
    return {
        "status": "connected" if ok else "failed",
        "domain": domain,
        "project": project,
        "mode": "cli",
        "cli_status_code": code,
        "vercel_response": output[:2000],
        "dns_records": dns_records,
        "next_steps": [
            "Add the DNS records above at your domain registrar or DNS provider (GoDaddy, Namecheap, Cloudflare, etc.)",
            "Then call /api/website/domain/status to check when the domain verifies.",
            "Vercel auto-provisions SSL once DNS propagates (usually 5 min - 48h).",
        ],
        "connected_at": _now(),
    }


def domain_status(project: str, domain: str, token: str = "") -> dict[str, Any]:
    """Check whether a custom domain has been verified on a Vercel project."""
    domain = (domain or "").strip().lower().replace("http://", "").replace("https://", "").split("/")[0]
    explicit_token = _explicit_vercel_token(token)
    cli_available = bool(_vercel_cli_bin())
    if explicit_token:
        headers = _vercel_headers(explicit_token)
        try:
            resp = requests.get(
                f"{_VERCEL_API}/v6/domains/{domain}/config?project={project}",
                headers=headers,
                timeout=30,
            )
            payload = resp.json() if resp.content else {}
        except Exception as e:  # noqa: BLE001
            return {"error": f"Domain status API failed: {e}", "status": "failed"}
        misconfigured = bool(payload.get("misconfigured", False))
        return {
            "status": "verified" if resp.status_code == 200 and not misconfigured else "pending",
            "domain": domain,
            "project": project,
            "mode": "api",
            "misconfigured": misconfigured,
            "api_status_code": resp.status_code,
            "config": payload,
            "checked_at": _now(),
        }

    # No explicit token: use local CLI session (vercel domains inspect).
    if not cli_available:
        file_token = _get_vercel_token()
        if file_token:
            try:
                headers = _vercel_headers(file_token)
                resp = requests.get(
                    f"{_VERCEL_API}/v6/domains/{domain}/config?project={project}",
                    headers=headers,
                    timeout=30,
                )
                payload = resp.json() if resp.content else {}
                misconfigured = bool(payload.get("misconfigured", False))
                return {
                    "status": "verified" if resp.status_code == 200 and not misconfigured else "pending",
                    "domain": domain,
                    "project": project,
                    "mode": "api-file-token",
                    "misconfigured": misconfigured,
                    "api_status_code": resp.status_code,
                    "config": payload,
                    "checked_at": _now(),
                }
            except Exception as e:  # noqa: BLE001
                return {"error": f"Domain status failed: {e}", "status": "failed"}
    code, out, err = _run_vercel_cli(["domains", "inspect", domain], timeout=60)
    output = out + err
    verified = code == 0 and "vercel-dns" in output.lower()
    return {
        "status": "verified" if verified else "pending",
        "domain": domain,
        "project": project,
        "mode": "cli",
        "cli_status_code": code,
        "misconfigured": not verified,
        "config": output[:2000],
        "checked_at": _now(),
    }


def publish_site(
    title: str = "My Website",
    tagline: str = "",
    industry: str = "",
    sections: str = "",
    category: str = "business",
    style: str = "modern",
    color_primary: str = "#2563EB",
    framework: str = "nextjs",
    services: str = "",
    business_email: str = "",
    project_name: str = "",
    output_dir: str = "",
    skills: list[str] | None = None,
    prod: bool = True,
    token: str = "",
) -> dict[str, Any]:
    """One-shot pipeline: build real site code -> deploy to Vercel -> live URL.

    Wraps build_site + deploy_vercel so the Website Agent can take a business
    brief and return a working public URL in a single call. `category` picks
    the multi-page site template (see build_site).
    """
    build = build_site(
        title=title,
        tagline=tagline,
        industry=industry,
        sections=sections,
        category=category,
        style=style,
        color_primary=color_primary,
        framework=framework,
        services=services,
        business_email=business_email,
        output_dir=output_dir,
        skills=skills,
    )
    if build.get("status") != "built":
        return {"status": "failed", "error": build.get("error") or "build failed", "build": build}

    resolved_token = _explicit_vercel_token(token)

    # Next.js needs dependencies installed before Vercel can build it.
    if framework == "nextjs":
        install = subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund"],
            capture_output=True, text=True, timeout=600,
            cwd=build["output_dir"],
        )
        if install.returncode != 0:
            return {
                **build,
                "status": "build_ok_install_failed",
                "install_output": (install.stdout + install.stderr)[:2000],
            }

    deploy = deploy_vercel(
        project_path=build["output_dir"],
        project_name=project_name or _slugify(build["title"]),
        prod=prod,
        token=resolved_token,
    )
    if deploy.get("status") != "deployed":
        return {
            **build,
            "status": "deploy_failed",
            "deploy": deploy,
        }

    return {
        **build,
        "status": "published",
        "deploy": deploy,
        "live_url": deploy.get("url", ""),
        "published_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 13. CHECK DOMAIN
# ═══════════════════════════════════════════════════════════════════════════════

def check_domain(domain: str) -> dict[str, Any]:
    """Check domain availability, DNS records, and registrar info."""
    # Clean domain
    domain = domain.strip().lower()
    if domain.startswith("http"):
        domain = urlparse(domain).hostname or domain
    domain = domain.replace("www.", "")

    result: dict[str, Any] = {
        "domain": domain,
        "checked_at": _now(),
        "dns_records": {},
        "has_website": False,
        "ssl_info": {},
        "issues": [],
    }

    # DNS records
    record_types = ["A", "AAAA", "CNAME", "MX", "TXT", "NS"]
    for rtype in record_types:
        try:
            answers = dns.resolver.resolve(domain, rtype)
            records = [str(r) for r in answers]
            result["dns_records"][rtype] = records
            if rtype == "A" or rtype == "AAAA":
                result["has_website"] = True
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            pass
        except Exception:
            pass

    # Check if site responds
    for scheme in ["https", "http"]:
        url = f"{scheme}://{domain}"
        resp = _safe_get(url, timeout=8)
        if resp:
            result["has_website"] = True
            result["status_code"] = resp.status_code
            result["final_url"] = resp.url
            result["title"] = ""
            soup = _soup(resp.text)
            if soup.title and soup.title.string:
                result["title"] = soup.title.string.strip()[:100]
            break

    # SSL check
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                issuer = dict(x[0] for x in cert.get("issuer", []))
                result["ssl_info"] = {
                    "valid": True,
                    "issuer": issuer.get("organizationName", issuer.get("commonName", "")),
                    "expires": cert.get("notAfter", ""),
                }
    except Exception:
        result["ssl_info"] = {"valid": False}

    # Suggestions
    if not result["has_website"]:
        result["issues"].append("Domain has no active website")
    if not result["dns_records"]:
        result["issues"].append("No DNS records found — domain may be available")
    if not result.get("ssl_info", {}).get("valid"):
        result["issues"].append("No valid SSL certificate")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 14. SCREENSHOT SITE
# ═══════════════════════════════════════════════════════════════════════════════

def screenshot_site(
    url: str,
    width: int = 1280,
    height: int = 800,
) -> dict[str, Any]:
    """Take a screenshot of a website using requests-based approach.

    For full browser screenshots, use the Chrome Agent (CDP).
    This tool captures page metadata + visual indicators.
    """
    resp = _safe_get(url, timeout=15)
    if not resp:
        return {"error": f"Cannot reach {url}", "status": "failed"}

    soup = _soup(resp.text)

    # Extract visual metadata
    og_image = ""
    for meta in soup.find_all("meta"):
        if meta.get("property") == "og:image":
            og_image = meta.get("content", "")
            break

    # Extract all images
    images = []
    for img in soup.find_all("img")[:20]:
        src = img.get("src", "")
        if src:
            src = urljoin(url, src)
        images.append({
            "src": src,
            "alt": img.get("alt", ""),
            "width": img.get("width", ""),
            "height": img.get("height", ""),
        })

    # Favicon
    favicon = ""
    link = soup.find("link", rel=lambda r: r and "icon" in r)
    if link:
        favicon = urljoin(url, link.get("href", ""))

    # Background colors
    bg_colors = set()
    for tag in soup.find_all(style=True)[:30]:
        style = tag.get("style", "")
        color_match = re.findall(r"background(?:-color)?:\s*(#[0-9a-fA-F]{3,8})", style)
        bg_colors.update(color_match)

    # Try to capture screenshot via CDP if available
    screenshot_path = ""
    try:
        from admin.tools.chrome_tool import ChromeTool
        chrome = ChromeTool()
        # This would need async context — skip for sync tool
        # Just report that CDP is available
    except Exception:
        pass

    return {
        "url": url,
        "captured_at": _now(),
        "viewport": {"width": width, "height": height},
        "title": soup.title.string.strip()[:100] if soup.title and soup.title.string else "",
        "og_image": og_image,
        "favicon": favicon,
        "images": images,
        "image_count": len(images),
        "bg_colors": list(bg_colors)[:10],
        "screenshot_available": bool(screenshot_path),
        "note": "For full browser screenshots, use Chrome Agent via CDP or Playwright",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 15. CHECK UPTIME
# ═══════════════════════════════════════════════════════════════════════
def check_uptime(
    url: str,
    checks: int = 3,
    interval: int = 2,
) -> dict[str, Any]:
    """Monitor site uptime: check response time, status code, SSL over multiple pings."""
    results = []
    status_counts: dict[str, int] = {}
    response_times = []

    for i in range(checks):
        start = time.time()
        try:
            resp = requests.get(
                url, headers=_HEADERS, timeout=15, allow_redirects=True
            )
            elapsed = round(time.time() - start, 3)
            status = resp.status_code

            results.append({
                "check": i + 1,
                "status": status,
                "response_time_ms": round(elapsed * 1000),
                "size_bytes": len(resp.content),
                "redirected": len(resp.history) > 0,
                "final_url": resp.url,
            })

            status_key = f"{status}"
            status_counts[status_key] = status_counts.get(status_key, 0) + 1
            response_times.append(elapsed)
        except requests.exceptions.Timeout:
            elapsed = round(time.time() - start, 3)
            results.append({"check": i + 1, "status": "timeout", "response_time_ms": round(elapsed * 1000)})
            status_counts["timeout"] = status_counts.get("timeout", 0) + 1
        except requests.exceptions.ConnectionError:
            results.append({"check": i + 1, "status": "connection_error"})
            status_counts["connection_error"] = status_counts.get("connection_error", 0) + 1
        except Exception as e:
            results.append({"check": i + 1, "status": "error", "error": str(e)[:100]})
            status_counts["error"] = status_counts.get("error", 0) + 1

        if i < checks - 1:
            time.sleep(interval)

    # Calculate stats
    avg_response = round(sum(response_times) / len(response_times) * 1000, 1) if response_times else 0
    min_response = round(min(response_times) * 1000, 1) if response_times else 0
    max_response = round(max(response_times) * 1000, 1) if response_times else 0

    success_count = sum(v for k, v in status_counts.items() if k.startswith("2"))
    uptime_percent = round((success_count / max(checks, 1)) * 100, 1)

    # SSL check
    ssl_valid = False
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        hostname = parsed.hostname
        if hostname:
            ctx = ssl.create_default_context()
            with socket.create_connection((hostname, 443), timeout=8) as sock:
                with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                    ssl_valid = True
    except Exception:
        pass

    # Health assessment
    if uptime_percent == 100 and avg_response < 2000:
        health = "healthy"
    elif uptime_percent >= 80:
        health = "degraded"
    else:
        health = "unhealthy"

    return {
        "url": url,
        "checked_at": _now(),
        "checks_performed": checks,
        "uptime_percent": uptime_percent,
        "health": health,
        "response_time": {
            "avg_ms": avg_response,
            "min_ms": min_response,
            "max_ms": max_response,
        },
        "status_distribution": status_counts,
        "ssl_valid": ssl_valid,
        "checks": results,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

WEBSITE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_website",
            "description": "Crawl a website and analyze structure, tech stack, meta info, navigation, images.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Website URL to analyze"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_performance",
            "description": "Check page performance: load time, HTML size, resources, compression, caching. Returns score.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to check"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_links",
            "description": "Find broken links on a page. Checks each link and reports broken ones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Page URL to check"},
                    "max_links": {"type": "integer", "description": "Max links to check (default 50)", "default": 50},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "security_check",
            "description": "Check security headers: HTTPS, HSTS, CSP, X-Frame-Options, etc.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to check"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tech_stack_advisor",
            "description": "Recommend tech stack based on project needs (site type, ecommerce, blog, budget).",
            "parameters": {
                "type": "object",
                "properties": {
                    "site_type": {"type": "string", "description": "landing, corporate, saas, webapp, portfolio"},
                    "needs_ecommerce": {"type": "boolean", "default": False},
                    "needs_blog": {"type": "boolean", "default": False},
                    "budget": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                    "client_preference": {"type": "string", "description": "Client's preferred tech"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "design_planner",
            "description": "Plan site architecture: pages, sections, navigation, color palette, typography.",
            "parameters": {
                "type": "object",
                "properties": {
                    "site_type": {"type": "string", "default": "landing"},
                    "pages": {"type": "string", "description": "Comma-separated page names", "default": "home, about, services, contact"},
                    "style": {"type": "string", "enum": ["modern", "minimal", "bold", "warm", "tech"], "default": "modern"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_accessibility",
            "description": "Basic a11y check: alt text, labels, heading hierarchy, ARIA landmarks, lang attribute.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to check"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "competitor_sites",
            "description": "Scan competitor websites: tech stack, performance, SEO score comparison.",
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {"type": "array", "items": {"type": "string"}, "description": "List of competitor URLs"},
                },
                "required": ["urls"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "responsive_check",
            "description": "Check mobile responsiveness: viewport meta, CSS media queries, fixed widths, large images.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to check"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_ssl",
            "description": "Check SSL certificate: valid, expiry date, issuer, protocol version.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to check"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_code",
            "description": "Generate starter code for a website page (Next.js components or HTML/CSS). Returns ready-to-use code with color palette and sections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_type": {"type": "string", "description": "landing, portfolio, saas, corporate", "default": "landing"},
                    "framework": {"type": "string", "enum": ["nextjs", "html"], "default": "nextjs"},
                    "style": {"type": "string", "enum": ["modern", "minimal", "bold", "warm", "tech"], "default": "modern"},
                    "sections": {"type": "string", "description": "Comma-separated sections: hero,features,cta,footer,about,contact,testimonials,pricing", "default": "hero,features,cta,footer"},
                    "color_primary": {"type": "string", "description": "Primary color hex code", "default": "#2563EB"},
                    "title": {"type": "string", "description": "Website/page title", "default": "My Website"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_site",
            "description": "Build a complete website project on disk (Next.js or HTML) from business info: title, tagline, services, email, category, colors. Category picks a multi-page site (business, portfolio, restaurant, ecommerce, saas, agency, realestate, blog, education, health, event, hotel, construction, nonprofit). Writes real files and returns the file list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Website/business name", "default": "My Website"},
                    "tagline": {"type": "string", "description": "One-line value proposition", "default": ""},
                    "industry": {"type": "string", "description": "Industry (tech, food, agency, etc.)", "default": ""},
                    "category": {"type": "string", "enum": ["business", "portfolio", "restaurant", "ecommerce", "saas", "agency", "realestate", "blog", "education", "health", "event", "hotel", "construction", "nonprofit"], "description": "Website type: picks pages + sections (e.g. restaurant -> Menu page, ecommerce -> Shop page). Leave default for a general business site.", "default": "business"},
                    "sections": {"type": "string", "description": "Optional: comma-separated sections to force a single-page build (hero,services,about,testimonials,contact,footer). Leave empty to use the category page map.", "default": ""},
                    "style": {"type": "string", "enum": ["modern", "minimal", "bold", "warm", "tech"], "default": "modern"},
                    "color_primary": {"type": "string", "description": "Primary color hex code", "default": "#2563EB"},
                    "framework": {"type": "string", "enum": ["nextjs", "html"], "default": "nextjs"},
                    "services": {"type": "string", "description": "Comma-separated service names (e.g. Web Design, SEO, Branding)", "default": ""},
                    "business_email": {"type": "string", "description": "Contact email shown in contact section + footer", "default": ""},
                    "output_dir": {"type": "string", "description": "Where to write the project (default generated_sites/<title-slug>)", "default": ""},
                    "skills": {"type": "array", "items": {"type": "string"}, "description": "Matched skill names to bias defaults", "default": []},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deploy_vercel",
            "description": "Deploy a project to Vercel (frontend+backend). Uses vercel CLI. Returns deploy URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Path to project directory", "default": "."},
                    "project_name": {"type": "string", "description": "Vercel project name"},
                    "prod": {"type": "boolean", "description": "Deploy to production", "default": True},
                    "env_vars": {"type": "string", "description": "Comma-separated env vars: KEY1=val1,KEY2=val2"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_domain",
            "description": "Check domain: DNS records (A, AAAA, CNAME, MX, TXT, NS), SSL, website status, availability hints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain name to check (e.g. example.com)"},
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot_site",
            "description": "Capture website visual metadata: title, OG image, favicon, images, background colors, layout info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to capture"},
                    "width": {"type": "integer", "description": "Viewport width", "default": 1280},
                    "height": {"type": "integer", "description": "Viewport height", "default": 800},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_uptime",
            "description": "Monitor site uptime: multiple health checks, response time stats, SSL validity, health assessment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to monitor"},
                    "checks": {"type": "integer", "description": "Number of checks to perform (default 3)", "default": 3},
                    "interval": {"type": "integer", "description": "Seconds between checks (default 2)", "default": 2},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_site",
            "description": "One-shot: build a real website from a business brief, install deps, deploy to Vercel, and return the live URL. Category picks the multi-page template (restaurant -> menu page, ecommerce -> shop, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Business/website name", "default": "My Website"},
                    "tagline": {"type": "string", "description": "One-line value proposition", "default": ""},
                    "industry": {"type": "string", "description": "Industry (tech, food, agency, etc.)", "default": ""},
                    "category": {"type": "string", "enum": ["business", "portfolio", "restaurant", "ecommerce", "saas", "agency", "realestate", "blog", "education", "health", "event", "hotel", "construction", "nonprofit"], "description": "Website type: picks pages + sections (e.g. restaurant -> Menu page, ecommerce -> Shop page).", "default": "business"},
                    "sections": {"type": "string", "description": "Optional: comma-separated sections to force a single-page build. Leave empty to use the category page map.", "default": ""},
                    "style": {"type": "string", "enum": ["modern", "minimal", "bold", "warm", "tech"], "default": "modern"},
                    "color_primary": {"type": "string", "description": "Primary color hex code", "default": "#2563EB"},
                    "framework": {"type": "string", "enum": ["nextjs", "html"], "default": "nextjs"},
                    "services": {"type": "string", "description": "Comma-separated service names", "default": ""},
                    "business_email": {"type": "string", "description": "Contact email", "default": ""},
                    "project_name": {"type": "string", "description": "Vercel project name (default: slug of title)", "default": ""},
                    "output_dir": {"type": "string", "description": "Where to write the project", "default": ""},
                    "skills": {"type": "array", "items": {"type": "string"}, "default": []},
                    "prod": {"type": "boolean", "description": "Deploy to production", "default": True},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "connect_domain",
            "description": "Attach a custom domain to a deployed Vercel project and return the exact DNS records to add (A/CNAME + TXT).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Vercel project name (from a deploy)"},
                    "domain": {"type": "string", "description": "Custom domain, e.g. example.com or www.example.com"},
                },
                "required": ["project", "domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "domain_status",
            "description": "Check whether a custom domain attached to a Vercel project is verified and not misconfigured.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Vercel project name"},
                    "domain": {"type": "string", "description": "Custom domain to check"},
                },
                "required": ["project", "domain"],
            },
        },
    },
    # ── Store-aware tools (Website Agent <-> Client Store Portal) ──────────────
    {
        "type": "function",
        "function": {
            "name": "update_store_site",
            "description": (
                "Update a client's existing storefront in place from their store portal "
                "(products + logo). Patches only the live site's product grid and logo — "
                "does NOT rebuild the whole site. Use when the client adds/edits a product "
                "or logo in their portal and wants the change pushed to their live website. "
                "This is the Website Agent's store path, NOT SBA."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace ID, e.g. ws_agency"},
                    "client": {"type": "string", "description": "Client name (default: Client)"},
                    "deploy": {"type": "boolean", "description": "Deploy to Vercel after update (default true)", "default": True},
                },
                "required": ["workspace_id"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL EXECUTION ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

def execute_website_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route tool call to actual function."""
    dispatch = {
        "analyze_website": lambda a: analyze_website(a["url"]),
        "check_performance": lambda a: check_performance(a["url"]),
        "check_links": lambda a: check_links(a["url"], a.get("max_links", 50)),
        "security_check": lambda a: security_check(a["url"]),
        "tech_stack_advisor": lambda a: tech_stack_advisor(
            site_type=a.get("site_type", ""),
            needs_ecommerce=a.get("needs_ecommerce", False),
            needs_blog=a.get("needs_blog", False),
            budget=a.get("budget", "medium"),
            client_preference=a.get("client_preference", ""),
        ),
        "design_planner": lambda a: design_planner(
            site_type=a.get("site_type", "landing"),
            pages=a.get("pages", "home, about, services, contact"),
            style=a.get("style", "modern"),
        ),
        "check_accessibility": lambda a: check_accessibility(a["url"]),
        "competitor_sites": lambda a: competitor_sites(a["urls"]),
        "responsive_check": lambda a: responsive_check(a["url"]),
        "check_ssl": lambda a: check_ssl(a["url"]),
        "generate_code": lambda a: generate_code(
            page_type=a.get("page_type", "landing"),
            framework=a.get("framework", "nextjs"),
            style=a.get("style", "modern"),
            sections=a.get("sections", "hero,features,cta,footer"),
            color_primary=a.get("color_primary", "#2563EB"),
            title=a.get("title", "My Website"),
        ),
        "build_site": lambda a: build_site(
            title=a.get("title", "My Website"),
            tagline=a.get("tagline", ""),
            industry=a.get("industry", ""),
            sections=a.get("sections", ""),
            category=a.get("category", "business"),
            style=a.get("style", "modern"),
            color_primary=a.get("color_primary", "#2563EB"),
            framework=a.get("framework", "nextjs"),
            services=a.get("services", ""),
            business_email=a.get("business_email", ""),
            output_dir=a.get("output_dir", ""),
            skills=a.get("skills", []),
        ),
        "deploy_vercel": lambda a: deploy_vercel(
            project_path=a.get("project_path", "."),
            project_name=a.get("project_name", ""),
            prod=a.get("prod", True),
            env_vars=a.get("env_vars", ""),
        ),
        "check_domain": lambda a: check_domain(a["domain"]),
        "screenshot_site": lambda a: screenshot_site(
            url=a["url"],
            width=a.get("width", 1280),
            height=a.get("height", 800),
        ),
        "check_uptime": lambda a: check_uptime(
            url=a["url"],
            checks=a.get("checks", 3),
            interval=a.get("interval", 2),
        ),
        "publish_site": lambda a: publish_site(
            title=a.get("title", "My Website"),
            tagline=a.get("tagline", ""),
            industry=a.get("industry", ""),
            sections=a.get("sections", ""),
            category=a.get("category", "business"),
            style=a.get("style", "modern"),
            color_primary=a.get("color_primary", "#2563EB"),
            framework=a.get("framework", "nextjs"),
            services=a.get("services", ""),
            business_email=a.get("business_email", ""),
            project_name=a.get("project_name", ""),
            output_dir=a.get("output_dir", ""),
            skills=a.get("skills", []),
            prod=a.get("prod", True),
        ),
        "connect_domain": lambda a: connect_domain(
            project=a["project"],
            domain=a["domain"],
        ),
        "domain_status": lambda a: domain_status(
            project=a["project"],
            domain=a["domain"],
        ),
        "update_store_site": lambda a: update_store_site(
            workspace=a.get("workspace_id", "Default"),
            client=a.get("client"),
            deploy=bool(a.get("deploy", True)),
        ),
    }
    fn = dispatch.get(name)
    if fn:
        try:
            try:
                sig = inspect.signature(fn)
                params = set(sig.parameters)
                filtered = {k: v for k, v in args.items()
                            if k in params or (len(params) == 1 and "a" in params)}
            except (ValueError, TypeError):
                filtered = args
            return fn(filtered)
        except Exception as e:
            logger.exception("Website tool failed: %s", name)
            return {"error": str(e), "status": "failed"}
    return {"error": f"Unknown tool: {name}", "status": "failed"}
