"""
Del Monte Cash Back (Food Fundz / Incent) Scraper
═══════════════════════════════════════════════════════════════════
Scrapes product data from delmontefscashback.com.
JS SPA (Incent platform) — requires Playwright for rendering.
Login required to access the product/rebate catalog.

Usage:
    python3 -m scrapers.delmonte
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class DelmonteScraper(BaseScraper):
    VENDOR_SLUG = "delmonte"
    VENDOR_NAME = "Del Monte Cash Back"
    BASE_URL = "https://delmontefscashback.com"

    async def login(self, page) -> bool:
        """Log in to Del Monte Cash Back (Incent/Food Fundz SPA)."""
        email, password = self.get_credentials("DELMONTE")

        if not email or not password:
            self.log("  ⚠ No DELMONTE_EMAIL / DELMONTE_PASSWORD in .env")
            return False

        await page.goto(f"{self.BASE_URL}", wait_until="domcontentloaded")
        await self.delay(page, 5)  # JS SPA needs time to render

        self.log(f"  → Waiting for app to render...")

        # Wait for the SPA to boot
        try:
            await page.wait_for_selector(
                "input, form, [class*='login'], #app > div:not(.pre-render)",
                state="attached",
                timeout=15000,
            )
        except Exception:
            self.log("  ⚠ App did not fully render")

        await page.screenshot(path="debug_delmonte_landing.png")
        self.log(f"  → Current URL: {page.url}")

        # Find and fill login form
        self.log(f"  → Entering credentials ({email})...")
        try:
            email_input = page.locator(
                "input[type='email'], input[name='email'], "
                "input[placeholder*='mail'], input[placeholder*='user'], "
                "input[type='text']"
            )
            if await email_input.first.is_visible(timeout=5000):
                await email_input.first.fill(email, timeout=5000)

            pwd_input = page.locator(
                "input[type='password'], input[name='password']"
            )
            if await pwd_input.first.is_visible(timeout=3000):
                await pwd_input.first.fill(password, timeout=5000)

            submit = page.locator(
                "button[type='submit'], button:has-text('Login'), "
                "button:has-text('Sign In'), button:has-text('Log In'), "
                "input[type='submit']"
            )
            await submit.first.click(timeout=5000)
            self.log("  → Submitted login form")
            await self.delay(page, 5)
        except Exception as e:
            self.log(f"  ⚠ Login issue: {e}")
            await page.screenshot(path="debug_delmonte_login_error.png")

        # Verify
        await page.screenshot(path="debug_delmonte_post_login.png")
        self.log(f"  → Post-login URL: {page.url}")

        # Check for dashboard/product indicators
        try:
            has_content = await page.locator(
                "[class*='dashboard'], [class*='product'], [class*='catalog'], "
                "[class*='rebate'], nav, [class*='menu'], [class*='sidebar']"
            ).first.is_visible(timeout=5000)
            if has_content:
                return True
        except Exception:
            pass

        if "login" not in page.url.lower():
            return True

        self.log("  ⚠ Login may have failed — continuing anyway")
        return True

    async def scrape(self, page) -> None:
        """Scrape Del Monte products from the dashboard/catalog."""
        # Try to navigate to products/catalog
        product_urls = [
            f"{self.BASE_URL}/#/products",
            f"{self.BASE_URL}/#/catalog",
            f"{self.BASE_URL}/#/rebates",
            f"{self.BASE_URL}/#/dashboard",
        ]

        for url in product_urls:
            if self.has_reached_limit():
                break
            self.log(f"\n  📂 Trying: {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await self.delay(page, 4)

                # Check if products loaded
                product_elements = await page.locator(
                    "[class*='product'], [class*='item'], [class*='card'], "
                    "[class*='rebate'], table tbody tr, [class*='list-item']"
                ).all()

                if product_elements:
                    self.log(f"      Found {len(product_elements)} elements")
                    await self._extract_products(page, product_elements)
                    if self.stats["products_found"] > 0:
                        break  # Found products, stop trying other URLs
            except Exception as e:
                self.stats["errors"] += 1
                self.log(f"      ❌ Error: {e}")

        if self.stats["products_found"] == 0:
            self.log("  ⚠ No products found — trying search approach")
            await self._try_search(page)

        if self.stats["products_found"] == 0:
            self.log("  ⚠ No products found on any page — taking debug screenshot")
            await page.screenshot(path="debug_delmonte_no_products.png")
            # Dump page content for debugging
            content = await page.content()
            with open("debug_delmonte_page.html", "w") as f:
                f.write(content)
            self.log("  → Saved page HTML to debug_delmonte_page.html")

    async def _extract_products(self, page, elements):
        """Extract product data from found elements."""
        for el in elements:
            if self.has_reached_limit():
                break
            try:
                data = await self._extract_product(el)
                if data and data.get("sku"):
                    self.save_product(data)
            except Exception:
                self.stats["errors"] += 1

    async def _try_search(self, page):
        """Try using search functionality to find products."""
        try:
            search = page.locator(
                "input[type='search'], input[placeholder*='earch'], "
                "input[class*='search']"
            )
            if await search.first.is_visible(timeout=5000):
                for term in ["Del Monte", "canned", "fruit", "vegetables"]:
                    if self.has_reached_limit():
                        break
                    await search.first.fill(term, timeout=3000)
                    await page.keyboard.press("Enter")
                    await self.delay(page, 3)

                    results = await page.locator(
                        "[class*='product'], [class*='item'], [class*='result'], "
                        "table tbody tr"
                    ).all()
                    self.log(f"      Search '{term}': {len(results)} results")
                    await self._extract_products(page, results)
        except Exception:
            pass

    async def _extract_product(self, element) -> dict:
        """Extract product from a Del Monte card/row."""
        name = ""
        for sel in ["h3", "h2", "h4", "[class*='name']", "[class*='title']",
                     "a", "td:first-child", "span", "p"]:
            name = await self.safe_text(element.locator(sel))
            if name and len(name) > 2:
                break

        if not name or len(name) < 3:
            return {}

        price_text = await self.safe_text(
            element.locator(
                "[class*='price'], [class*='amount'], [class*='cost'], "
                "[class*='value'], td:nth-child(2)"
            )
        )
        price = self.parse_price(price_text)

        sku_text = await self.safe_text(
            element.locator(
                "[class*='sku'], [class*='upc'], [class*='code'], td:nth-child(3)"
            )
        )
        sku = ""
        if sku_text:
            m = re.search(r'(\d{4,})', sku_text)
            if m:
                sku = m.group(1)

        if not sku:
            sku = f"dm-{abs(hash(name)) % 10**8}"

        image_url = await self.safe_attr(element.locator("img"), "src")
        if image_url and not image_url.startswith("http"):
            image_url = f"{self.BASE_URL}{image_url}"

        return {
            "sku": sku,
            "product_name": name.strip(),
            "brand": "Del Monte",
            "category": "Grocery",
            "unit_price": price,
            "image_url": image_url,
            "in_stock": True,
        }


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = DelmonteScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
