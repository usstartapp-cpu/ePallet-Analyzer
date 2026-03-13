"""
McLane Xpress Scraper
═══════════════════════════════════════════════════════════════════
Scrapes product data from mclanexpress.com (Angular SPA on Clixa platform).
Public catalog — no login needed. Uses search to discover products.

Product tiles:  app-item
  .item-name         → product name
  .item-price        → price like "$1.53"
  .item-category     → category like "Candy"
  .item-pack         → pack info like "1 Pack, 5 OZ"
  .item-image-container img → image

Search returns 20 items per query at /order/search.ng

Usage:
    python3 -m scrapers.mclane
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class McLaneScraper(BaseScraper):
    VENDOR_SLUG = "mclane"
    VENDOR_NAME = "McLane Xpress"
    BASE_URL = "https://mclanexpress.com"

    # Search terms to cover different product categories (20 items per search)
    SEARCH_TERMS = [
        "chips", "candy", "cookies", "crackers", "gum",
        "chocolate", "soda", "water", "juice", "energy drink",
        "jerky", "nuts", "granola", "protein bar", "trail mix",
        "coffee", "tea", "popcorn", "pretzels", "cheese",
    ]

    async def login(self, page) -> bool:
        """Navigate to McLane Xpress — public catalog."""
        await page.goto(
            f"{self.BASE_URL}/order/",
            wait_until="domcontentloaded",
        )
        await self.delay(page, 5)  # Angular app needs time to boot

        # Wait for app to render
        try:
            await page.wait_for_selector(
                "app-item, input[type='text']",
                state="attached",
                timeout=15000,
            )
        except Exception:
            self.log("  ⚠ App did not render fully")

        self.log(f"  → URL: {page.url}")
        self.log("  ✅ Proceeding with public catalog")
        return True

    async def scrape(self, page) -> None:
        """Scrape McLane Xpress via search queries."""
        seen_names = set()

        for term in self.SEARCH_TERMS:
            if self.has_reached_limit():
                break
            self.log(f"\n  🔍 Search: {term}")
            try:
                await self._scrape_search(page, term, seen_names)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"search": term, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    async def _scrape_search(self, page, term: str, seen_names: set):
        """Type a search term and extract results."""
        # Find and fill search input
        search_input = page.locator("input[type='text']").first
        try:
            await search_input.click(timeout=3000)
            await search_input.fill("", timeout=2000)  # Clear
            await search_input.fill(term, timeout=3000)
            await page.keyboard.press("Enter")
            await self.delay(page, 4)
        except Exception as e:
            self.log(f"      ⚠ Search input issue: {e}")
            return

        # Wait for results
        try:
            await page.wait_for_selector("app-item", state="attached", timeout=8000)
        except Exception:
            self.log(f"      ⚠ No results for '{term}'")
            return

        items = await page.locator("app-item").all()
        self.log(f"      Found {len(items)} items")

        for item in items:
            if self.has_reached_limit():
                break
            try:
                data = await self._extract_product(item)
                if not data or not data.get("sku"):
                    continue
                # Dedupe by name
                if data["product_name"] in seen_names:
                    continue
                seen_names.add(data["product_name"])
                self.save_product(data)
            except Exception:
                self.stats["errors"] += 1

    async def _extract_product(self, item) -> dict:
        """Extract product data from an app-item element."""

        # Name
        name = await self.safe_text(item.locator(".item-name"))
        if not name or len(name) < 3:
            return {}

        # Price
        price_text = await self.safe_text(item.locator(".item-price"))
        price = self.parse_price(price_text)

        # Category
        category = await self.safe_text(item.locator(".item-category"))
        if not category:
            category = "General"

        # Pack info (for description)
        pack = await self.safe_text(item.locator(".item-pack"))

        # Image
        image_url = await self.safe_attr(
            item.locator(".item-image-container img, img"), "src"
        )
        if image_url and not image_url.startswith("http"):
            image_url = f"{self.BASE_URL}{image_url}"

        # Generate SKU from name (McLane doesn't show SKU in tiles)
        sku = f"mcl-{abs(hash(name)) % 10**8}"

        full_name = name.strip()
        if pack:
            full_name = f"{name.strip()} ({pack.strip()})"

        return {
            "sku": sku,
            "product_name": full_name,
            "category": category.strip(),
            "unit_price": price,
            "image_url": image_url,
            "in_stock": True,
        }


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = McLaneScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
