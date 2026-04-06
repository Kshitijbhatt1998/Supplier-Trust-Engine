"""
ImportYeti scraper using Playwright.

Why Playwright (not Scrapy):
- ImportYeti is JavaScript-rendered (React)
- Requires login after 25 page views
- Dynamic content: charts, lazy-loaded shipment tables

Flow:
1. Login once, persist session cookies
2. Search textile HS codes to discover supplier URLs
3. For each supplier page: extract all structured data
4. Write to DuckDB
"""

import asyncio
import os
import re
import json
import random
import hashlib
from datetime import datetime
from typing import Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from playwright.async_api import async_playwright, Page, BrowserContext
from pipeline.storage.db import init_db
from pipeline.entity_resolution import resolve_and_upsert


# Textile-relevant HS code prefixes to seed discovery
TEXTILE_HS_CODES = [
    "5201",  # Cotton, not carded or combed
    "5208",  # Woven fabrics of cotton
    "5407",  # Woven fabrics of synthetic filament yarn
    "5512",  # Woven fabrics of synthetic staple fibres
    "6109",  # T-shirts, singlets, tank tops (knitted)
    "6203",  # Men's suits, jackets, trousers (not knitted)
    "6302",  # Bed linen, table linen, toilet linen
    "6006",  # Other knitted or crocheted fabrics
]


def slugify(name: str) -> str:
    """Create a stable ID from a company name."""
    clean = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return clean[:80]


def random_delay(min_s: float = 2.0, max_s: float = 5.0) -> float:
    return random.uniform(
        float(os.getenv("REQUEST_DELAY_MIN", min_s)),
        float(os.getenv("REQUEST_DELAY_MAX", max_s)),
    )


class ImportYetiScraper:
    BASE_URL = "https://www.importyeti.com"
    SESSION_FILE = "data/.importyeti_session.json"

    def __init__(self):
        self.email = os.getenv("IMPORTYETI_EMAIL")
        self.password = os.getenv("IMPORTYETI_PASSWORD")
        self.headless = os.getenv("HEADLESS", "true").lower() == "true"
        self.con = init_db()

    # ------------------------------------------------------------------ #
    # Session management                                                    #
    # ------------------------------------------------------------------ #

    async def _save_session(self, context: BrowserContext) -> None:
        cookies = await context.cookies()
        os.makedirs("data", exist_ok=True)
        with open(self.SESSION_FILE, "w") as f:
            json.dump(cookies, f)
        logger.info("Session cookies saved.")

    async def _load_session(self, context: BrowserContext) -> bool:
        if not os.path.exists(self.SESSION_FILE):
            return False
        with open(self.SESSION_FILE) as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        logger.info("Loaded existing session cookies.")
        return True

    async def _login(self, page: Page) -> None:
        """Log in to ImportYeti and persist the session."""
        logger.info("Logging in to ImportYeti...")
        await page.goto(f"{self.BASE_URL}/login", wait_until="networkidle")
        await asyncio.sleep(random_delay(1, 2))

        # Fill credentials — inspect live DOM to confirm selectors
        await page.fill("input[type='email']", self.email)
        await page.fill("input[type='password']", self.password)
        await page.click("button[type='submit']")

        await page.wait_for_url(f"{self.BASE_URL}/**", timeout=15000)
        logger.info("Login successful.")

    # ------------------------------------------------------------------ #
    # Discovery: find supplier URLs from HS code searches                   #
    # ------------------------------------------------------------------ #

    async def _discover_suppliers(self, page: Page, hs_code: str) -> list[str]:
        """
        Search ImportYeti by HS code and collect supplier page URLs.
        Returns a list of relative paths like '/company/xyz-textiles'.

        NOTE: Selectors marked # TODO — confirm against live DOM.
        """
        search_url = f"{self.BASE_URL}/search?q={hs_code}&type=supplier"
        logger.info(f"Discovering suppliers for HS {hs_code}: {search_url}")
        await page.goto(search_url, wait_until="networkidle")
        await asyncio.sleep(random_delay())

        # TODO: Confirm this selector by inspecting importyeti.com search results
        supplier_links = await page.eval_on_selector_all(
            "a[href^='/company/']",
            "els => els.map(el => el.getAttribute('href'))"
        )

        unique = list(set(supplier_links))
        logger.info(f"  Found {len(unique)} supplier links for HS {hs_code}")
        return unique

    # ------------------------------------------------------------------ #
    # Extraction: parse a single supplier page                              #
    # ------------------------------------------------------------------ #

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _scrape_supplier(self, page: Page, path: str) -> Optional[dict]:
        """
        Scrape a single ImportYeti supplier page.
        Returns a structured dict or None if the page is inaccessible.

        Selectors are best-guess from DOM patterns documented in ImportYeti
        reviews — you WILL need to adjust these after inspecting the live page.
        Open DevTools on any /company/ page and verify each selector.
        """
        url = f"{self.BASE_URL}{path}"
        logger.info(f"Scraping: {url}")

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(random_delay())
        except Exception as e:
            logger.warning(f"Failed to load {url}: {e}")
            return None

        # --- Core identity fields ---
        # TODO: Open any /company/ page, right-click → Inspect each field
        name = await self._safe_text(page, "h1")
        country = await self._safe_text(page, "[data-testid='supplier-country']")
        address = await self._safe_text(page, "[data-testid='supplier-address']")

        # --- Shipment stats ---
        # These are typically rendered in stat cards near the top
        shipment_count_raw = await self._safe_text(page, "[data-testid='total-shipments']")
        shipment_count = self._parse_int(shipment_count_raw)

        avg_monthly_raw = await self._safe_text(page, "[data-testid='avg-monthly-shipments']")
        avg_monthly = self._parse_float(avg_monthly_raw)

        # --- HS codes ---
        hs_codes = await page.eval_on_selector_all(
            "[data-testid='hs-code-tag'], .hs-code-badge",
            "els => els.map(el => el.innerText.trim())"
        )

        # --- Buyer list (customer concentration) ---
        buyers = await page.eval_on_selector_all(
            "[data-testid='buyer-name'], .buyer-row .company-name",
            "els => els.map(el => el.innerText.trim())"
        )

        # --- Shipment dates ---
        first_date_raw = await self._safe_text(page, "[data-testid='first-shipment-date']")
        last_date_raw = await self._safe_text(page, "[data-testid='last-shipment-date']")

        if not name:
            logger.warning(f"No company name found at {url} — selector may be wrong")
            return None

        supplier_id = slugify(name)

        return {
            "id": supplier_id,
            "name": name,
            "country": country,
            "address": address,
            "shipment_count": shipment_count,
            "avg_monthly_shipments": avg_monthly,
            "total_buyers": len(buyers),
            "hs_codes": [h.strip() for h in hs_codes if h.strip()],
            "top_buyers": buyers[:10],  # Store top 10 for concentration calc
            "first_shipment_date": self._parse_date(first_date_raw),
            "last_shipment_date": self._parse_date(last_date_raw),
            "source": "importyeti",
            "raw_url": url,
        }

    # ------------------------------------------------------------------ #
    # Helpers                                                               #
    # ------------------------------------------------------------------ #

    async def _safe_text(self, page: Page, selector: str) -> Optional[str]:
        try:
            el = await page.query_selector(selector)
            if el:
                return (await el.inner_text()).strip()
        except Exception:
            pass
        return None

    def _parse_int(self, raw: Optional[str]) -> Optional[int]:
        if not raw:
            return None
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else None

    def _parse_float(self, raw: Optional[str]) -> Optional[float]:
        if not raw:
            return None
        match = re.search(r"[\d.]+", raw)
        return float(match.group()) if match else None

    def _parse_date(self, raw: Optional[str]) -> Optional[str]:
        """Try common date formats ImportYeti uses."""
        if not raw:
            return None
        for fmt in ("%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d %b %Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------------ #
    # Main orchestration                                                    #
    # ------------------------------------------------------------------ #

    async def run(self, hs_codes: list[str] = None, max_per_code: int = 20) -> None:
        """
        Full scraping run:
        1. Login (or reuse session)
        2. Discover supplier URLs across HS codes
        3. Scrape each supplier page
        4. Write to DuckDB
        """
        codes = hs_codes or TEXTILE_HS_CODES

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )

            page = await context.new_page()

            # Login or reuse session
            session_loaded = await self._load_session(context)
            if not session_loaded:
                await self._login(page)
                await self._save_session(context)

            # Discover all supplier paths
            all_paths: set[str] = set()
            for hs in codes:
                paths = await self._discover_suppliers(page, hs)
                all_paths.update(paths[:max_per_code])
                await asyncio.sleep(random_delay(3, 6))

            logger.info(f"Total unique suppliers to scrape: {len(all_paths)}")

            # Scrape each supplier
            scraped = 0
            failed = 0
            for path in all_paths:
                data = await self._scrape_supplier(page, path)
                if data:
                    result = resolve_and_upsert(self.con, data)
                    scraped += 1
                    tag = "new" if result.is_new else f"→ {result.canonical_id}"
                    logger.success(f"  ✓ {data['name']} ({data['country']}) [{tag}] — {data['shipment_count']} shipments")
                else:
                    failed += 1

                await asyncio.sleep(random_delay())

            await browser.close()
            logger.info(f"Done. Scraped: {scraped}, Failed: {failed}")


# ------------------------------------------------------------------ #
# Entry point                                                           #
# ------------------------------------------------------------------ #

async def main():
    from dotenv import load_dotenv
    load_dotenv()
    scraper = ImportYetiScraper()
    await scraper.run(max_per_code=25)


if __name__ == "__main__":
    asyncio.run(main())
