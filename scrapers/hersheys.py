"""
Hershey's Scraper
═══════════════════════════════════════════════════════════════════
Scrapes product catalog from hersheyland.com.
NOTE: hersheys.com redirects to hersheyland.com which is a brand showcase
site, NOT an e-commerce store. No prices or SKUs available.
We extract product names, images, and URLs from the Algolia search index
and rendered product listing page.

Usage:
    python3 -m scrapers.hersheys
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class HersheysScraper(BaseScraper):
    VENDOR_SLUG = "hersheys"
    VENDOR_NAME = "Hershey's"
    BASE_URL = "https://www.hersheyland.com"

    # Brand pages to scrape for product info
    BRAND_PAGES = [
        ("/products", "All Products"),
        ("/reeses", "Reese's"),
        ("/kit-kat", "Kit Kat"),
        ("/hersheys-brand", "Hershey's"),
        ("/jolly-rancher", "Jolly Rancher"),
        ("/twizzlers", "Twizzlers"),
        ("/ice-breakers", "Ice Breakers"),
    ]

    async def login(self, page) -> bool:
        """Navigate to Hershey's — public brand site, no login needed."""
        await page.goto(
            f"{self.BASE_URL}/products",
            wait_until="domcontentloaded",
        )
        await self.delay(page, 3)

        # Dismiss cookie banner
        try:
            btn = page.locator(
                "button#onetrust-accept-btn-handler, "
                "button:has-text('Accept All Cookies')"
            )
            if await btn.first.is_visible(timeout=3000):
                await btn.first.click()
                await self.delay(page, 1)
        except Exception:
            pass

        self.log("  ✅ Proceeding with brand catalog (no prices available)")
        return True

    async def scrape(self, page) -> None:
        """Scrape Hershey's product listings from rendered pages."""
        seen = set()

        # The products page is an Algolia-powered JS SPA — wait for it to render
        await page.goto(
            f"{self.BASE_URL}/products",
            wait_until="domcontentloaded",
        )
        await self.delay(page, 5)

        # Wait for product cards to render
        try:
            await page.wait_for_selector(
                ".product-card, .ais-Hits-item, [class*='product'], a[href*='/products/']",
                state="attached",
                timeout=15000,
            )
        except Exception:
            self.log("  ⚠ Product cards did not render, trying alternative approach...")

        # Try to find product links on the page
        product_links = await page.locator(
            "a[href*='/products/']"
        ).all()

        self.log(f"  📦 Found {len(product_links)} product links")

        if not product_links:
            # Fallback: scrape brand pages for product references
            self.log("  ⚠ No product links on /products — scraping brand pages")
            await self._scrape_brand_pages(page, seen)
            return

        for link in product_links:
            if self.has_reached_limit():
                break
            try:
                href = await link.get_attribute("href", timeout=2000) or ""
                if not href or href in seen:
                    continue
                # Skip non-product links
                if "/products.html" in href or href.count("/") < 2:
                    continue
                seen.add(href)

                name = ""
                try:
                    name = (await link.inner_text(timeout=2000)).strip()
                except Exception:
                    pass

                if not name or len(name) < 3:
                    continue

                url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                slug = href.rstrip("/").split("/")[-1].replace(".html", "")
                sku = f"hersh-{slug}" if slug else f"hersh-{abs(hash(name)) % 10**8}"

                # Try to get image from parent/sibling
                image_url = ""
                try:
                    parent = link.locator("xpath=ancestor::div[1]")
                    img = parent.locator("img")
                    image_url = await img.first.get_attribute("src", timeout=2000) or ""
                    if image_url and not image_url.startswith("http"):
                        image_url = f"{self.BASE_URL}{image_url}"
                except Exception:
                    pass

                self.save_product({
                    "sku": sku,
                    "product_name": name,
                    "brand": "Hershey's",
                    "category": "Candy & Chocolate",
                    "unit_price": None,  # Brand site — no prices
                    "product_url": url,
                    "image_url": image_url,
                    "in_stock": True,
                })
            except Exception:
                self.stats["errors"] += 1

    async def _scrape_brand_pages(self, page, seen: set):
        """Fallback: scrape individual brand pages for product references."""
        for path, brand in self.BRAND_PAGES:
            if self.has_reached_limit():
                break
            try:
                await page.goto(
                    f"{self.BASE_URL}{path}",
                    wait_until="domcontentloaded",
                )
                await self.delay(page, 4)

                # Look for product cards/links
                links = await page.locator(
                    "a[href*='/products/'], a[href*='/candy/'], "
                    "a.product-card, div[class*='product'] a"
                ).all()
                self.log(f"  📂 {brand}: {len(links)} product links")

                for link in links:
                    if self.has_reached_limit():
                        break
                    try:
                        href = await link.get_attribute("href", timeout=2000) or ""
                        if not href or href in seen:
                            continue
                        seen.add(href)

                        name = ""
                        try:
                            name = (await link.inner_text(timeout=2000)).strip()
                        except Exception:
                            pass
                        if not name or len(name) < 3:
                            continue

                        url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                        slug = href.rstrip("/").split("/")[-1].replace(".html", "")
                        sku = f"hersh-{slug}" if slug else f"hersh-{abs(hash(name)) % 10**8}"

                        self.save_product({
                            "sku": sku,
                            "product_name": name,
                            "brand": brand,
                            "category": "Candy & Chocolate",
                            "unit_price": None,
                            "product_url": url,
                            "in_stock": True,
                        })
                    except Exception:
                        self.stats["errors"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                self.log(f"      ❌ {brand}: {e}")


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = HersheysScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
