"""
Webstaurant Store Scraper
═══════════════════════════════════════════════════════════════════
Scrapes product data from webstaurantstore.com.
Search pages return 60 products per page in .product-box-container tiles.

Rendered HTML selectors (Playwright):
  .product-box-container              → product tile
  a[data-testid="itemLink"]           → product URL (href)
  [data-testid="itemDescription"]     → product name (text)
  [data-testid="price"]               → price like "$39.99/Case"
  img                                 → product image

Usage:
    python3 -m scrapers.webstaurant
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class WebstaurantScraper(BaseScraper):
    VENDOR_SLUG = "webstaurant"
    VENDOR_NAME = "Webstaurant Store"
    BASE_URL = "https://www.webstaurantstore.com"

    SEARCH_TERMS = [
        ("food", "Food"),
        ("canned goods", "Canned Goods"),
        ("pasta", "Pasta & Sauce"),
        ("baking", "Baking Supplies"),
        ("beverages", "Beverages"),
        ("condiments", "Condiments"),
        ("snacks", "Snacks"),
    ]

    async def login(self, page) -> bool:
        """Navigate to Webstaurant — public catalog, no login needed."""
        await page.goto(self.BASE_URL, wait_until="domcontentloaded")
        await self.delay(page, 2)
        self.log("  ✅ Proceeding with public catalog")
        return True

    async def scrape(self, page) -> None:
        """Scrape Webstaurant Store via search pages."""
        seen_skus = set()

        for term, cat_name in self.SEARCH_TERMS:
            if self.has_reached_limit():
                break
            self.log(f"\n  📂 Search: {cat_name}")
            try:
                await self._scrape_search(page, term, cat_name, seen_skus)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"search": term, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    async def _scrape_search(self, page, term: str, category: str, seen_skus: set):
        """Scrape search results with pagination."""
        for page_num in range(1, 4):  # Max 3 pages per search
            if self.has_reached_limit():
                break

            url = f"{self.BASE_URL}/search/{term.replace(' ', '-')}.html"
            if page_num > 1:
                url += f"?page={page_num}"

            await page.goto(url, wait_until="domcontentloaded")
            await self.delay(page, 4)

            # Wait for product tiles to render
            try:
                await page.wait_for_selector(
                    ".product-box-container",
                    state="attached",
                    timeout=10000,
                )
            except Exception:
                if page_num == 1:
                    self.log(f"      ⚠ No results for '{term}'")
                break

            boxes = await page.locator(".product-box-container").all()
            if not boxes:
                break

            self.log(f"      Page {page_num} — {len(boxes)} items")

            for box in boxes:
                if self.has_reached_limit():
                    break
                try:
                    data = await self._extract_product(box, category)
                    if data and data.get("sku") and data["sku"] not in seen_skus:
                        seen_skus.add(data["sku"])
                        self.save_product(data)
                except Exception:
                    self.stats["errors"] += 1

            # Check for next page
            try:
                next_btn = page.locator(
                    "li.rc-pagination-next:not(.rc-pagination-disabled) a, "
                    "a[aria-label='next page']"
                )
                if not await next_btn.first.is_visible(timeout=2000):
                    break
            except Exception:
                break

    async def _extract_product(self, box, category: str) -> dict:
        """Extract product data from a .product-box-container tile."""

        # ── Name ────────────────────────────────────────────────
        name = await self.safe_text(box.locator("[data-testid='itemDescription']"))
        if not name or len(name) < 3:
            return {}

        # ── URL + SKU ───────────────────────────────────────────
        link = await self.safe_attr(
            box.locator("a[data-testid='itemLink']"), "href"
        )
        product_url = ""
        sku = ""
        if link:
            product_url = link if link.startswith("http") else f"{self.BASE_URL}{link}"
            # URL pattern: /product-name/SKUCODE.html
            m = re.search(r'/([^/]+)\.html$', link)
            if m:
                sku = m.group(1)

        if not sku:
            sku = f"ws-{abs(hash(name)) % 10**8}"

        # ── Price ───────────────────────────────────────────────
        price_text = await self.safe_text(box.locator("[data-testid='price']"))
        price = self.parse_price(price_text)

        # ── Image ───────────────────────────────────────────────
        image_url = await self.safe_attr(box.locator("img"), "src")
        if image_url and not image_url.startswith("http"):
            image_url = f"{self.BASE_URL}{image_url}"

        return {
            "sku": sku,
            "product_name": name.strip(),
            "category": category,
            "unit_price": price,
            "product_url": product_url,
            "image_url": image_url,
            "in_stock": True,
        }


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = WebstaurantScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
