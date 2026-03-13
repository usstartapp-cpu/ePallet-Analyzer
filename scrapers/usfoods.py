"""
US Foods Scraper — OTP Login Required
═══════════════════════════════════════════════════════════════════
US Foods uses username + OTP (sent to phone).
This scraper pauses for manual OTP entry, then proceeds with scraping.

Usage:
    python3 -m scrapers.usfoods
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class USFoodsScraper(BaseScraper):
    VENDOR_SLUG = "us-foods"
    VENDOR_NAME = "US Foods"
    BASE_URL = "https://www.usfoods.com"

    def __init__(self):
        super().__init__()
        # Force headless=False for OTP login
        self.headless = False

    async def login(self, page) -> bool:
        """
        Log in to US Foods — requires manual OTP step.
        The browser will pause and wait for you to enter the OTP code.
        """
        username = self.get_env("USFOODS_USERNAME")

        await page.goto(f"{self.BASE_URL}/", wait_until="domcontentloaded")
        await self.delay(page, 3)

        # Navigate to login
        try:
            await page.locator("a:has-text('Sign In'), a:has-text('Log In')").first.click(timeout=5000)
            await self.delay(page, 3)
        except Exception:
            await page.goto(f"{self.BASE_URL}/login", wait_until="domcontentloaded")
            await self.delay(page, 3)

        # Enter username
        self.log(f"  → Entering username: {username}")
        try:
            await page.locator("input[name='username'], input#username, input[type='text']").first.fill(username, timeout=5000)
        except Exception as e:
            self.log(f"  ✗ Could not find username field: {e}")
            await page.screenshot(path="debug_usfoods_login.png")
            return False

        # Submit username (some sites have multi-step login)
        try:
            await page.locator("button[type='submit'], button:has-text('Next'), button:has-text('Continue')").first.click(timeout=5000)
            await self.delay(page, 3)
        except Exception:
            pass

        # ── OTP STEP ────────────────────────────────────────────
        self.log("")
        self.log("  ╔══════════════════════════════════════════════════╗")
        self.log("  ║  ⚠️  OTP REQUIRED — CHECK YOUR PHONE            ║")
        self.log("  ║  Enter the verification code in the browser.    ║")
        self.log("  ║  The scraper will wait up to 2 minutes...       ║")
        self.log("  ╚══════════════════════════════════════════════════╝")
        self.log("")

        # Wait for user to complete OTP (up to 2 minutes)
        for i in range(24):  # 24 × 5 sec = 2 minutes
            await self.delay(page, 5)

            # Check if we've made it past login
            if "login" not in page.url.lower() and "auth" not in page.url.lower():
                self.log("  ✓ OTP accepted — logged in!")
                return True

            # Check for dashboard/home indicators
            try:
                if await page.locator(
                    "a:has-text('My Account'), .user-name, .welcome-message"
                ).first.is_visible(timeout=1000):
                    return True
            except Exception:
                pass

            if i % 6 == 5:
                self.log(f"  ... still waiting for OTP ({(i+1)*5}s)")

        self.log("  ✗ Timed out waiting for OTP")
        await page.screenshot(path="debug_usfoods_otp_timeout.png")
        return False

    async def scrape(self, page) -> None:
        """Scrape US Foods product catalog."""
        # US Foods typically has category-based browsing
        categories = [
            ("/products/category/grocery", "Grocery"),
            ("/products/category/frozen", "Frozen"),
            ("/products/category/beverages", "Beverages"),
            ("/products/category/dairy", "Dairy"),
            ("/products/category/produce", "Produce"),
            ("/products/category/bakery", "Bakery"),
            ("/products/category/meat-seafood", "Meat & Seafood"),
        ]

        for path, name in categories:
            self.log(f"\n  📂 Category: {name}")
            try:
                url = f"{self.BASE_URL}{path}"
                await page.goto(url, wait_until="domcontentloaded")
                await self.delay(page, 3)

                await self._scrape_listing(page, name)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"category": name, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    async def _scrape_listing(self, page, category: str):
        """Scrape products from the current listing page."""
        products = await page.locator(
            ".product-card, .product-tile, .product-item, [data-product-id]"
        ).all()

        if not products:
            self.log(f"      ⚠ No products found on this page")
            return

        self.log(f"      Found {len(products)} items")

        for prod_el in products:
            try:
                name = ""
                price = None
                sku = ""
                url = ""

                # Name
                for sel in ["h3", "h2", ".product-name", ".product-title"]:
                    try:
                        name = await prod_el.locator(sel).first.inner_text(timeout=1500)
                        if name.strip():
                            break
                    except Exception:
                        continue

                # Price
                try:
                    price_text = await prod_el.locator(
                        ".price, .product-price, [data-price]"
                    ).first.inner_text(timeout=1500)
                    match = re.search(r"\$?([\d,]+\.?\d*)", price_text)
                    if match:
                        price = float(match.group(1).replace(",", ""))
                except Exception:
                    pass

                # SKU / product ID
                try:
                    sku = await prod_el.get_attribute("data-product-id", timeout=1000) or ""
                except Exception:
                    pass

                # URL
                try:
                    href = await prod_el.locator("a").first.get_attribute("href", timeout=1500)
                    if href:
                        url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                        if not sku:
                            m = re.search(r"/(\d{4,})", href)
                            if m:
                                sku = m.group(1)
                except Exception:
                    pass

                if not sku and name:
                    sku = f"usf-{abs(hash(name)) % 10**8}"

                if name.strip():
                    self.save_product({
                        "sku": sku,
                        "product_name": name.strip(),
                        "category": category,
                        "unit_price": price,
                        "product_url": url,
                        "in_stock": True,
                    })

            except Exception as e:
                self.stats["errors"] += 1


async def main():
    scraper = USFoodsScraper()
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
