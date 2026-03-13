"""
Costco Business Delivery Scraper
═══════════════════════════════════════════════════════════════════
Scrapes product data from costcobusinessdelivery.com.
Logs in, then searches grocery categories for products.

Costco has aggressive bot detection (Akamai). We use:
  • Non-headless mode forced (headless is always blocked)
  • HTTP/1.1 fallback via Chromium flags
  • Stealth user-agent and viewport
  • Extra delays between requests

Usage:
    python3 -m scrapers.costco
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper
from playwright.async_api import async_playwright


class CostcoScraper(BaseScraper):
    VENDOR_SLUG = "costco"
    VENDOR_NAME = "Costco Business"
    BASE_URL = "https://www.costcobusinessdelivery.com"

    async def run(self, triggered_by: str = "manual") -> dict:
        """
        Override run() to use custom browser launch args that bypass
        Costco's Akamai bot detection (HTTP/2 fingerprinting).
        """
        from datetime import datetime
        from db.supabase_client import start_scrape_run, complete_scrape_run, fail_scrape_run
        import traceback

        self.log("=" * 60)
        self.log(f"🚀 SCRAPER 4000 — {self.VENDOR_NAME}")
        self.log(f"   Slug: {self.VENDOR_SLUG}")
        self.log(f"   Method: {self.SCRAPE_METHOD}")
        self.log(f"   Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log("=" * 60)

        self.run_id = start_scrape_run(self.vendor_id, self.SCRAPE_METHOD, triggered_by)
        self.log(f"📝 Scrape run: {self.run_id}")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=False,  # Costco blocks headless entirely
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-http2",           # Force HTTP/1.1
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--no-sandbox",
                    ],
                )
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    timezone_id="America/Los_Angeles",
                )

                # Mask webdriver flag
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                """)

                page = await context.new_page()
                page.set_default_timeout(self.timeout)

                self.log("\n🔐 Logging in...")
                login_ok = await self.login(page)
                if not login_ok:
                    raise RuntimeError("Login failed")
                self.log("✅ Login successful\n")

                self.log("📦 Starting scrape...")
                await self.scrape(page)
                await browser.close()

            complete_scrape_run(self.run_id, self.stats)
            self.log("\n" + "=" * 60)
            self.log(f"✅ SCRAPE COMPLETE — {self.VENDOR_NAME}")
            self.log(f"   Products found:   {self.stats['products_found']}")
            self.log(f"   New products:     {self.stats['products_new']}")
            self.log(f"   Updated products: {self.stats['products_updated']}")
            self.log(f"   Price changes:    {self.stats['price_changes']}")
            self.log(f"   Errors:           {self.stats['errors']}")
            self.log("=" * 60)
            return self.stats

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            self.log(f"\n❌ SCRAPE FAILED: {e}")
            fail_scrape_run(self.run_id, error_msg)
            self.stats["errors"] += 1
            return self.stats

    async def login(self, page) -> bool:
        """Log in to Costco Business Delivery."""
        email, password = self.get_credentials("COSTCO")

        self.log(f"  → Navigating to {self.BASE_URL} ...")
        await page.goto(f"{self.BASE_URL}/", wait_until="domcontentloaded", timeout=45000)
        await self.delay(page, 4)

        # Take a debug screenshot to see what we got
        await page.screenshot(path="debug_costco_landing.png")
        title = await page.title()
        self.log(f"  → Page title: {title}")

        # If we hit a challenge page, wait longer
        body_text = await page.inner_text("body")
        if "checking your browser" in body_text.lower() or "access denied" in body_text.lower():
            self.log("  ⚠ Bot challenge detected, waiting...")
            await self.delay(page, 10)
            await page.screenshot(path="debug_costco_challenge.png")

        # Click sign in
        try:
            await page.locator(
                "a#header_sign_in, a[href*='LogonForm'], a:has-text('Sign In')"
            ).first.click(timeout=8000)
            await self.delay(page, 4)
        except Exception:
            self.log("  → Direct nav to login page...")
            await page.goto(f"{self.BASE_URL}/LogonForm", wait_until="domcontentloaded", timeout=45000)
            await self.delay(page, 4)

        await page.screenshot(path="debug_costco_login_page.png")

        # Fill credentials
        self.log(f"  → Entering credentials ({email})...")
        try:
            await page.locator("input#logonId").first.fill(email, timeout=8000)
            await page.locator("input#logonPassword").first.fill(password, timeout=8000)

            self.log("  → Submitting...")
            await page.locator(
                "button[type='submit'], input[value='Sign In'], button:has-text('Sign In')"
            ).first.click(timeout=8000)
            await self.delay(page, 6)
        except Exception as e:
            self.log(f"  ⚠ Login form issue: {e}")
            await page.screenshot(path="debug_costco_login_error.png")

        await page.screenshot(path="debug_costco_post_login.png")

        # Check login
        if "LogonForm" in page.url:
            self.log("  ⚠ May still be on login page")

        # Verify login
        try:
            logged_in = await page.locator(
                "a:has-text('My Account'), a:has-text('Sign Out'), .account-icon"
            ).first.is_visible(timeout=5000)
            if logged_in:
                return True
        except Exception:
            pass

        if "LogonForm" not in page.url and "sign-in" not in page.url:
            self.log("  → Login appears successful (redirected away)")
            return True

        self.log("  ⚠ Could not verify login, continuing anyway")
        return True  # Try to scrape anyway

    async def scrape(self, page) -> None:
        """Scrape Costco Business via search across food categories."""
        search_terms = [
            "canned food", "pasta", "rice", "snacks",
            "beverages", "soup", "condiments",
        ]

        for term in search_terms:
            if self.has_reached_limit():
                break
            self.log(f"\n  📂 Searching: {term}")
            try:
                await self._scrape_search(page, term)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"search": term, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    async def _scrape_search(self, page, term: str):
        """Scrape a search results page."""
        url = f"{self.BASE_URL}/s?dept=All&keyword={term.replace(' ', '+')}"
        await page.goto(url, wait_until="domcontentloaded")
        await self.delay(page, 3)

        # Product tiles
        tiles = await page.locator(
            "div.product-tile-set div.col-xs-6, div.product, "
            ".product-list div[class*='col'], a.product-image-url, "
            "div[data-testid='ProductTile']"
        ).all()
        self.log(f"      Found {len(tiles)} tiles")

        for tile in tiles:
            if self.has_reached_limit():
                break
            try:
                data = await self._extract_product(tile, term.title())
                if data and data.get("sku"):
                    self.save_product(data)
            except Exception:
                self.stats["errors"] += 1

        await self.delay(page, 1)

    async def _extract_product(self, element, category: str) -> dict:
        """Extract product data from a Costco product tile."""
        name = await self.safe_text(
            element.locator(
                "span.description, a.description, p.description, h3, "
                "a[class*='desc'], span[class*='desc']"
            )
        )
        price_text = await self.safe_text(
            element.locator("div.price, span.price, div[class*='price']")
        )
        price = self.parse_price(price_text)

        link = await self.safe_attr(element.locator("a"), "href")
        sku = ""
        url = ""
        if link:
            url = link if link.startswith("http") else f"{self.BASE_URL}{link}"
            m = re.search(r"\.product\.(\d+)\.html", link)
            if not m:
                m = re.search(r"/(\d{4,})\.html", link)
            if m:
                sku = m.group(1)

        if not sku and name:
            sku = f"costco-{abs(hash(name)) % 10**8}"

        if not name or len(name) < 3:
            return {}

        return {
            "sku": sku,
            "product_name": name.strip(),
            "category": category,
            "unit_price": price,
            "product_url": url,
            "in_stock": True,
        }


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = CostcoScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
