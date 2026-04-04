import asyncio
import os
import datetime
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from dotenv import load_dotenv
from database import SessionLocal, Supplier, Relationship, Certification

load_dotenv()

class ImportYetiScraper:
    def __init__(self, headless=True):
        self.headless = headless
        self.email = os.getenv("IMPORTYETI_EMAIL")
        self.password = os.getenv("IMPORTYETI_PASSWORD")
        self.base_url = "https://www.importyeti.com"

    async def run(self, supplier_urls):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await stealth_async(page)

            # Login if credentials provided
            if self.email and self.password:
                await self.login(page)

            for url in supplier_urls:
                try:
                    await self.scrape_supplier(page, url)
                except Exception as e:
                    print(f"Error scraping {url}: {e}")
                
                # Random delay to avoid detection
                await asyncio.sleep(5)

            await browser.close()

    async def login(self, page):
        print("Attempting to login to ImportYeti...")
        try:
            await page.goto(f"{self.base_url}/login")
            await page.fill('input[type="email"]', self.email)
            await page.fill('input[type="password"]', self.password)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")
            print("Login successful (probably).")
        except Exception as e:
            print(f"Login failed: {e}")

    async def scrape_supplier(self, page, url):
        print(f"Scraping supplier: {url}")
        await page.goto(url)
        await page.wait_for_load_state("networkidle")

        # Wait for potential Cloudflare challenge
        if "Just a moment" in await page.title():
            print("Cloudflare challenge detected. Waiting...")
            await asyncio.sleep(10)

        # Selectors (based on research/likely structure)
        # TODO: Confirm selectors in real environment if they fail
        name_selector = "h1" 
        country_selector = ".location-text" # Based on subagent research
        shipment_count_selector = "//div[contains(text(), 'Total Shipments')]/following-sibling::div"

        try:
            name = await page.inner_text(name_selector)
            name = name.strip()
        except:
            name = "Unknown"

        try:
            # Country might be in a specific div or text
            country = await page.inner_text(country_selector)
        except:
            country = "Unknown"

        try:
            # Shipment count is usually a number
            shipment_text = await page.inner_text(shipment_count_selector)
            shipment_count = int(''.join(filter(str.isdigit, shipment_text)))
        except:
            shipment_count = 0

        print(f"Found: {name}, Country: {country}, Shipments: {shipment_count}")

        # Save to DB
        db = SessionLocal()
        supplier = db.query(Supplier).filter(Supplier.importyeti_url == url).first()
        if not supplier:
            supplier = Supplier(importyeti_url=url)
            db.add(supplier)
        
        supplier.name = name
        supplier.country = country
        supplier.shipment_count = shipment_count
        supplier.last_scraped_at = datetime.datetime.utcnow()
        
        db.commit()
        db.close()

if __name__ == "__main__":
    import sys
    urls = sys.argv[1:] if len(sys.argv) > 1 else ["https://www.importyeti.com/supplier/aim-textiles"]
    scraper = ImportYetiScraper(headless=False) # Default to false for initial tests as requested
    asyncio.run(scraper.run(urls))
