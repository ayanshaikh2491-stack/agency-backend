# seo-technical

Technical SEO deep-dive: make the site crawlable, fast, and indexable before any
content work. Rankings break here first.

## When to use
Triggered by: technical seo, crawl, canonical, hreflang, render, site architecture,
xml sitemap, robots.txt, core web vitals, javascript seo, structured data errors.

## Audit checklist (run in order)
1. **Crawl** the site (Screaming Frog / site: search / `webapp-testing`). Find:
   orphan pages, 404s, redirect chains, duplicate titles.
2. **Indexability**: robots.txt not blocking key paths; canonical tags self-referencing;
   no `noindex` on money pages; XML sitemap submitted + clean.
3. **Render**: if JS-heavy, confirm Google can render (view-source vs rendered DOM).
4. **Core Web Vitals**: LCP <2.5s, CLS <0.1, INP <200ms. Flag the worst 5 templates.
5. **Architecture**: flat (home → category → page, <=3 clicks), logical internal linking.
6. **Structured data**: validate with Rich Results Test; fix Product/LocalBusiness/
   FAQ schemas so AI engines can parse.

## Output
A prioritized fix list: 🔴 blocking (fix now) → 🟡 soon → 🟢 ok. Hand the 🔴 list to
Website agent; report the rest to CEO.

## Guardrails
- Don't rewrite content here — that's the SEO content pass. This is plumbing.
- Every fix = measurable (crawl errors down, LCP up), not vibes.
