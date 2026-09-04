"""Test Chrome anti-block measures — check if automation is detectable."""
import asyncio
import json
import sys

sys.path.insert(0, "..")
from admin.tools.chrome_tool import ChromeTool


async def test_stealth():
    chrome = ChromeTool(workspace="test_stealth")
    p = chrome._page
    if not await chrome._ensure_page():
        print("❌ Chrome daemon not running. Start it first:")
        print("   python -m admin.tools.browser_daemon --workspace test_stealth")
        return

    p = chrome._page
    print("🔍 Testing stealth measures...\n")

    # Test 1: navigator.webdriver
    wd = await p.evaluate("navigator.webdriver")
    print(f"  navigator.webdriver = {wd}  {'✅' if wd == False else '❌ BLOCKED'}")

    # Test 2: navigator.plugins
    plugins = await p.evaluate("navigator.plugins.length")
    print(f"  navigator.plugins.length = {plugins}  {'✅' if plugins > 0 else '❌ BLOCKED'}")

    # Test 3: chrome.runtime
    has_chrome = await p.evaluate("!!window.chrome && !!window.chrome.runtime")
    print(f"  window.chrome.runtime = {has_chrome}  {'✅' if has_chrome else '❌ BLOCKED'}")

    # Test 4: WebGL vendor spoof
    webgl_vendor = await p.evaluate("""
        (() => {
            try {
                const c = document.createElement('canvas');
                const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
                if (!gl) return 'no-webgl';
                return gl.getParameter(gl.getExtension('WEBGL_debug_renderer_info')
                    ? 37445 : 37445);
            } catch(e) { return e.message; }
        })()
    """)
    print(f"  WebGL vendor = {webgl_vendor}")

    # Test 5: navigator.languages
    langs = await p.evaluate("navigator.languages")
    print(f"  navigator.languages = {langs}  {'✅' if 'en-US' in langs else '⚠️'}")

    # Test 6: navigator.webdriver on new page
    await p.goto("about:blank")
    wd2 = await p.evaluate("navigator.webdriver")
    print(f"  [new page] navigator.webdriver = {wd2}  {'✅' if wd2 == False else '❌ BLOCKED'}")

    # Test 7: User-Agent
    ua = await p.evaluate("navigator.userAgent")
    print(f"  User-Agent: {ua[:70]}...")

    # Test 8: Check bot detection score (cloudflare-style)
    print(f"\n  🌐 Browser fingerprint summary:")
    fp = await p.evaluate("""
        (() => ({
            userAgent: navigator.userAgent.slice(0, 80),
            platform: navigator.platform,
            hardwareConcurrency: navigator.hardwareConcurrency,
            deviceMemory: navigator.deviceMemory,
            maxTouchPoints: navigator.maxTouchPoints,
            cookieEnabled: navigator.cookieEnabled,
            doNotTrack: navigator.doNotTrack,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        }))()
    """)
    for k, v in fp.items():
        print(f"    {k}: {v}")

    print("\n📋 Summary:")
    checks = [
        ("webdriver=false", wd == False),
        ("plugins>0", plugins > 0),
        ("chrome.runtime", has_chrome),
        ("languages=en-US", 'en-US' in langs),
    ]
    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")

    print("\nNext: Login karo LinkedIn manually, phir chrome_save_cookies call karo.")
    print("Next login pe chrome_load_cookies se session restore ho jayega.")


if __name__ == "__main__":
    asyncio.run(test_stealth())
