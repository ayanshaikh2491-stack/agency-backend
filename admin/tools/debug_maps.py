"""Debug: goto full place href and check for Website link."""
import asyncio, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from admin.tools.chrome_tool import ChromeTool

GET_HREF_JS = r"""
() => {
  const a = document.querySelector('div[role="article"] a[href*="/maps/place/"]');
  return a ? a.href : null;
}
"""

PAGE_JS = r"""
() => {
  const links = Array.from(document.querySelectorAll('a')).map(x => ({
    text: (x.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30),
    href: (x.getAttribute('href') || '').slice(0, 130),
    data_value: x.getAttribute('data-value') || '',
  }));
  const unique = [];
  const seen = new Set();
  for (const l of links) {
    const k = l.text + '|' + l.href.slice(0, 60);
    if (seen.has(k)) continue;
    seen.add(k);
    unique.push(l);
  }
  return { title: document.title, url: location.href, links: unique.slice(0, 30) };
}
"""


async def main():
    chrome = ChromeTool(browser_name="sba", workspace="agency")
    try:
        print(await chrome.goto("https://www.google.com/maps/search/dentist+Columbus+OH/"), flush=True)
        try:
            await chrome.wait("network-idle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(2)
        href = await chrome.eval_json(GET_HREF_JS)
        print("HREF:", href, flush=True)
        print(await chrome.goto(href), flush=True)
        try:
            await chrome.wait("network-idle", timeout=15000)
        except Exception as exc:
            print("wait exc:", exc, flush=True)
        await asyncio.sleep(4)
        print("PAGE:", flush=True)
        print(await chrome.eval_json(PAGE_JS), flush=True)
    finally:
        await chrome.close()


asyncio.run(main())
