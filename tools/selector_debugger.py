"""
Selector Debugger for ImportYeti
=================================
Run this BEFORE the main scraper to verify that every CSS selector
actually finds data on the live ImportYeti DOM.

Usage:
    python tools/selector_debugger.py --company "welspun india"
    python tools/selector_debugger.py --company "arvind limited"
    python tools/selector_debugger.py --url https://www.importyeti.com/company/welspun-india

Output:
    A table showing each field, the selector used, and what was actually found.
    Green = data found. Red = selector returned nothing (needs fixing).

When a selector fails:
    1. The browser stays open (headless=False)
    2. Open DevTools → Elements → Ctrl+F to search for the text you expect
    3. Right-click the element → Copy → Copy selector
    4. Paste the new selector into importyeti_scraper.py
"""

import asyncio
import os
import sys
import argparse
import re
from typing import Optional

from playwright.async_api import async_playwright, Page
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------ #
# All selectors from importyeti_scraper.py in one place for easy fixing #
# ------------------------------------------------------------------ #

SELECTORS = {
    "company_name":         "h1",
    "country":              "[data-testid='supplier-country']",
    "address":              "[data-testid='supplier-address']",
    "shipment_count":       "[data-testid='total-shipments']",
    "avg_monthly":          "[data-testid='avg-monthly-shipments']",
    "hs_codes":             "[data-testid='hs-code-tag'], .hs-code-badge",
    "buyers":               "[data-testid='buyer-name'], .buyer-row .company-name",
    "first_shipment_date":  "[data-testid='first-shipment-date']",
    "last_shipment_date":   "[data-testid='last-shipment-date']",
    "supplier_links":       "a[href^='/company/']",
}

GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD  = "\033[1m"


async def login(page: Page) -> None:
    email    = os.getenv("IMPORTYETI_EMAIL")
    password = os.getenv("IMPORTYETI_PASSWORD")
    if not email or not password:
        print(f"{YELLOW}⚠ No credentials in .env — skipping login (may hit page limit){RESET}")
        return
    print("Logging in...")
    await page.goto("https://www.importyeti.com/login", wait_until="networkidle")
    await asyncio.sleep(1.5)
    await page.fill("input[type='email']", email)
    await page.fill("input[type='password']", password)
    await page.click("button[type='submit']")
    await page.wait_for_url("https://www.importyeti.com/**", timeout=15000)
    print(f"{GREEN}✓ Login successful{RESET}\n")


async def debug_page(page: Page, url: str) -> dict[str, str]:
    """
    Navigate to URL and test every selector.
    Returns a dict of {field: found_value_or_None}.
    """
    print(f"Navigating to: {url}\n")
    await page.goto(url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)

    results = {}

    print(f"{'Field':<25} {'Selector':<55} {'Result'}")
    print("-" * 110)

    for field, selector in SELECTORS.items():
        try:
            # For multi-value selectors (hs_codes, buyers, supplier_links)
            if field in ("hs_codes", "buyers", "supplier_links"):
                elements = await page.query_selector_all(selector.split(",")[0].strip())
                # Try second selector if first returns nothing
                if not elements and "," in selector:
                    elements = await page.query_selector_all(selector.split(",")[1].strip())

                if elements:
                    texts = []
                    for el in elements[:3]:  # Preview first 3
                        t = await el.inner_text()
                        texts.append(t.strip()[:30])
                    value = f"[{len(elements)} items] {texts}"
                    color = GREEN
                    symbol = "✓"
                else:
                    value = "NOTHING FOUND"
                    color = RED
                    symbol = "✗"
            else:
                el = await page.query_selector(selector)
                if el:
                    text = await el.inner_text()
                    value = text.strip()[:60]
                    color = GREEN
                    symbol = "✓"
                else:
                    value = "NOTHING FOUND"
                    color = RED
                    symbol = "✗"

            results[field] = value if "NOTHING" not in value else None
            short_selector = selector[:53] + ".." if len(selector) > 55 else selector
            print(f"  {symbol} {field:<23} {short_selector:<55} {color}{value}{RESET}")

        except Exception as e:
            print(f"  {RED}✗ {field:<23} ERROR: {e}{RESET}")
            results[field] = None

    return results


async def suggest_fixes(page: Page, failed_fields: list[str]) -> None:
    """
    For fields that returned nothing, try to auto-discover a working selector
    by searching the page text for known patterns.
    """
    if not failed_fields:
        return

    print(f"\n{BOLD}=== Auto-discovery for failed fields ==={RESET}")
    print("Searching page DOM for likely elements...\n")

    # Get all text nodes from the page to find where data lives
    all_text = await page.evaluate("""
        () => {
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_ELEMENT,
                null,
                false
            );
            const elements = [];
            while (walker.nextNode()) {
                const el = walker.currentNode;
                const text = el.innerText?.trim();
                if (text && text.length > 0 && text.length < 100) {
                    elements.push({
                        tag: el.tagName,
                        id: el.id,
                        classes: el.className,
                        testid: el.getAttribute('data-testid'),
                        text: text
                    });
                }
            }
            return elements;
        }
    """)

    patterns = {
        "shipment_count":      r"^\d[\d,]+$",         # Pure numbers like "1,234"
        "avg_monthly":         r"^\d+\.?\d*\/mo",      # "12.5/mo" or similar
        "first_shipment_date": r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
        "last_shipment_date":  r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
        "country":             r"^[A-Z][a-z]+(,?\s+[A-Z][a-z]+)?$",
    }

    for field in failed_fields:
        if field not in patterns:
            continue
        pattern = patterns[field]
        matches = [
            el for el in all_text
            if re.search(pattern, el.get("text", ""), re.IGNORECASE)
        ]
        if matches:
            print(f"{YELLOW}  → {field}: Found {len(matches)} candidate(s):{RESET}")
            for m in matches[:3]:
                testid = f"[data-testid='{m['testid']}']" if m.get('testid') else ""
                id_sel = f"#{m['id']}" if m.get('id') else ""
                class_sel = f".{m['classes'].split()[0]}" if m.get('classes') else ""
                suggestion = testid or id_sel or class_sel or m['tag'].lower()
                print(f"     Text: '{m['text']}' → Try selector: {suggestion}")
        else:
            print(f"{RED}  → {field}: No auto-match found. Open DevTools and search for the value manually.{RESET}")


async def main():
    parser = argparse.ArgumentParser(description="Debug ImportYeti CSS selectors")
    parser.add_argument("--company", type=str, help="Company name to search (e.g. 'welspun india')")
    parser.add_argument("--url",     type=str, help="Direct URL to a /company/ page")
    parser.add_argument("--search",  type=str, help="Test search page selectors (e.g. HS code '5201')")
    args = parser.parse_args()

    if not any([args.company, args.url, args.search]):
        parser.print_help()
        print(f"\n{YELLOW}Example: python tools/selector_debugger.py --company 'welspun india'{RESET}")
        sys.exit(1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,  # Always visible for debugging
            args=["--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        await login(page)

        if args.url:
            target_url = args.url
        elif args.company:
            slug = args.company.lower().replace(" ", "-")
            target_url = f"https://www.importyeti.com/company/{slug}"
        elif args.search:
            target_url = f"https://www.importyeti.com/search?q={args.search}&type=supplier"

        results = await debug_page(page, target_url)

        failed = [f for f, v in results.items() if v is None]

        print(f"\n{BOLD}Summary:{RESET}")
        print(f"  Working selectors: {GREEN}{len(results) - len(failed)}/{len(results)}{RESET}")
        print(f"  Failed selectors:  {RED}{len(failed)}/{len(results)}{RESET}")

        if failed:
            print(f"\n  Failed fields: {', '.join(failed)}")
            await suggest_fixes(page, failed)
            print(f"\n{YELLOW}Browser is open — inspect these elements in DevTools.{RESET}")
            print("Press Enter to close the browser when done...")
            input()
        else:
            print(f"\n{GREEN}All selectors working! You can run the main scraper.{RESET}")
            await asyncio.sleep(3)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
