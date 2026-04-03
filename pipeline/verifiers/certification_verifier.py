"""
Certification verifiers for OEKO-TEX and GOTS.

These run AFTER the ImportYeti scraper has populated the suppliers table.
For each supplier, we attempt to find and verify their certification status
by querying the official issuing body portals directly.

Async with semaphore-controlled concurrency (max 5 parallel requests)
to avoid hammering certification portals.
"""

import asyncio
import os
import re
from datetime import datetime
from typing import Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from playwright.async_api import async_playwright, Page
from pipeline.storage.db import init_db, upsert_certification


MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_REQUESTS", 5))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"


# ------------------------------------------------------------------ #
# OEKO-TEX Verifier                                                     #
# ------------------------------------------------------------------ #

async def verify_oekotex(
    page: Page,
    supplier_name: str,
    supplier_id: str,
    license_id: Optional[str] = None,
) -> dict:
    """
    Check OEKO-TEX Label Check portal.

    Can search by:
    - License ID (precise, if you have it from the supplier's website)
    - Company name (fuzzy, slower)

    URL: https://www.oeko-tex.com/en/label-check
    """
    result = {
        "supplier_id": supplier_id,
        "source": "oekotex",
        "license_id": license_id,
        "status": "not_found",
        "valid_until": None,
        "certificate_name": None,
    }

    try:
        await page.goto(
            "https://www.oeko-tex.com/en/label-check",
            wait_until="networkidle",
            timeout=20000
        )
        await asyncio.sleep(2)

        # Use license_id if available, otherwise search by name
        search_term = license_id or supplier_name

        # TODO: Confirm selector after inspecting oeko-tex.com/en/label-check
        search_input = await page.query_selector("input[type='text'], input[placeholder*='search' i], input[placeholder*='label' i]")
        if not search_input:
            logger.warning(f"OEKO-TEX: Could not find search input for {supplier_name}")
            return result

        await search_input.fill(search_term)
        await asyncio.sleep(1)

        # Submit — try button click first, then Enter
        submit_btn = await page.query_selector("button[type='submit'], button.search-btn")
        if submit_btn:
            await submit_btn.click()
        else:
            await search_input.press("Enter")

        await page.wait_for_load_state("networkidle", timeout=10000)
        await asyncio.sleep(2)

        # Parse result
        # TODO: Adjust selectors after inspecting live result DOM
        status_el = await page.query_selector(".certification-status, .label-status, [data-status]")
        date_el = await page.query_selector(".valid-until, .expiry-date, [data-expiry]")
        name_el = await page.query_selector(".certificate-name, .company-name-result")

        if status_el:
            raw_status = (await status_el.inner_text()).lower()
            result["status"] = "valid" if "valid" in raw_status else "expired"

        if date_el:
            raw_date = await date_el.inner_text()
            result["valid_until"] = _parse_date(raw_date)

        if name_el:
            result["certificate_name"] = (await name_el.inner_text()).strip()

        logger.info(f"OEKO-TEX [{supplier_name}]: {result['status']}")

    except Exception as e:
        logger.warning(f"OEKO-TEX verification failed for {supplier_name}: {e}")
        result["status"] = "error"

    return result


# ------------------------------------------------------------------ #
# GOTS Verifier (Global Organic Textile Standard)                       #
# ------------------------------------------------------------------ #

async def verify_gots(
    page: Page,
    supplier_name: str,
    supplier_id: str,
    license_id: Optional[str] = None,
) -> dict:
    """
    Check GOTS Public Database.

    URL: https://global-standard.org/find-certified-companies-and-products/certified-facilities.html
    Note: GOTS uses a search portal powered by their certification bodies.
    """
    result = {
        "supplier_id": supplier_id,
        "source": "gots",
        "license_id": license_id,
        "status": "not_found",
        "valid_until": None,
        "certificate_name": None,
    }

    try:
        await page.goto(
            "https://global-standard.org/find-certified-companies-and-products/certified-facilities.html",
            wait_until="networkidle",
            timeout=20000
        )
        await asyncio.sleep(2)

        # TODO: Confirm selector after inspecting global-standard.org search form
        name_input = await page.query_selector("input[name*='company' i], input[placeholder*='company' i], input[type='text']")
        if not name_input:
            logger.warning(f"GOTS: Could not find search input for {supplier_name}")
            return result

        await name_input.fill(supplier_name)

        submit = await page.query_selector("button[type='submit'], input[type='submit']")
        if submit:
            await submit.click()
        else:
            await name_input.press("Enter")

        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)

        # Check for results table
        # TODO: Adjust selectors to match GOTS result table structure
        rows = await page.query_selector_all("table tbody tr, .result-row, .facility-row")

        for row in rows:
            row_text = (await row.inner_text()).lower()
            if supplier_name.lower()[:10] in row_text:
                result["status"] = "valid"
                result["certificate_name"] = supplier_name

                # Try to find expiry date in the row
                date_match = re.search(r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}", row_text)
                if date_match:
                    result["valid_until"] = _parse_date(date_match.group())
                break

        logger.info(f"GOTS [{supplier_name}]: {result['status']}")

    except Exception as e:
        logger.warning(f"GOTS verification failed for {supplier_name}: {e}")
        result["status"] = "error"

    return result


# ------------------------------------------------------------------ #
# Orchestrator: verify all suppliers in DuckDB                          #
# ------------------------------------------------------------------ #

async def verify_all_suppliers(limit: int = 100) -> None:
    """
    Pull all suppliers from DuckDB, verify their certifications,
    and write results back.
    """
    con = init_db()
    suppliers = con.execute(
        f"SELECT id, name FROM suppliers LIMIT {limit}"
    ).fetchall()

    logger.info(f"Verifying certifications for {len(suppliers)} suppliers...")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        async def verify_one(supplier_id: str, supplier_name: str):
            async with semaphore:
                page = await context.new_page()
                try:
                    # Run both verifiers per supplier
                    oekotex_result = await verify_oekotex(page, supplier_name, supplier_id)
                    upsert_certification(con, oekotex_result)

                    await asyncio.sleep(2)  # Pause between portals

                    gots_result = await verify_gots(page, supplier_name, supplier_id)
                    upsert_certification(con, gots_result)

                finally:
                    await page.close()

        tasks = [verify_one(row[0], row[1]) for row in suppliers]
        await asyncio.gather(*tasks)

        await browser.close()

    logger.info("Certification verification complete.")


# ------------------------------------------------------------------ #
# Helpers                                                               #
# ------------------------------------------------------------------ #

def _parse_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(verify_all_suppliers(limit=50))
