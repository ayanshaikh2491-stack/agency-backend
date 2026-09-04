# web-lead-discovery

Discover leads on the open web using a browser: Google Maps, directories, Yelp,
Crunchbase, industry listings, and competitor backlink spying.

## When to use
Triggered by: google maps, scrape leads, find businesses, directory, listings,
"companies in <city>", competitor backlinks, local SEO leads, cold list building.

## Workflow
1. **Pick a source** by intent:
   - Local service businesses → Google Maps / Yelp / Justdial for "<service> in <city>".
   - B2B / SaaS → Crunchbase, G2, Capterra, industry directories.
   - Competitor clients → pull who ranks for target keywords, or who links to a rival.
2. **Extract** structured rows: name, website, phone, email (if public), owner name,
   location, category. One row per business.
3. **Score** each: has website? (no → easy win) | ranks in AI answers? (no → pain
   point) | reviews <50? (no social proof → upsell).
4. **Hand off** the list to SBA (outreach) or SEO (audit) via the workspace. CEO sees
   the count + top 3 opportunities.

## Guardrails
- Only PUBLIC data. No scraping behind logins, no personal data resale.
- Respect robots.txt on automated pulls; manual browse is fine.
- Log source + date so the list isn't duplicated next cycle.
