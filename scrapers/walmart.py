"""
Walmart Business Scraper
═══════════════════════════════════════════════════════════════════
Scrapes product data from business.walmart.com — public catalog.
Search-based scraping across food categories.

Product tile structure (React/Next.js):
  div[data-item-id]                          — tile container
  span[data-automation-id="product-title"]   — product name
  div[data-automation-id="product-price"]    — price container
  a[link-identifier]                         — product link (/ip/name/ID)
  img[data-testid="productTileImage"]        — product image

Usage:
    python3 -m scrapers.walmart
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class WalmartScraper(BaseScraper):
    VENDOR_SLUG = "walmart"
    VENDOR_NAME = "Walmart Business"
    BASE_URL = "https://business.walmart.com"

    # Search terms to cover food & grocery categories
    SEARCH_TERMS = [
        "canned goods", "pasta", "rice", "snacks",
        "soup", "condiments", "baking", "beverages",
        "cereal", "coffee", "sauce", "cooking oil",
    ]

    async def login(self, page) -> bool:
        """Navigate to Walmart Business — public catalog, no login needed."""
        await page.goto(self.BASE_URL, wait_until="domcontentloaded")
        await self.delay(page, 3)

        # Dismiss any popups/banners
        for sel in [
            "button:has-text('Accept')",
            "button:has-text('Got it')",
            "button[aria-label='close']",
            "button:has-text('Close')",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await self.delay(page, 1)
            except Exception:
                pass

        self.log("  ✅ Walmart Business catalog ready (public)")
        return True

    async def scrape(self, page) -> None:
        """Scrape Walmart Business via search queries."""
        seen_ids = set()

        for term in self.SEARCH_TERMS:
            if self.has_reached_limit():
                break
            self.log(f"\n  🔍 Searching: {term}")
            try:
                await self._scrape_search(page, term, seen_ids)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"search": term, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    async def _scrape_search(self, page, term: str, seen_ids: set):
        """Scrape a Walmart Business search results page."""
        url = f"{self.BASE_URL}/search?q={term.replace(' ', '+')}"
        await page.goto(url, wait_until="domcontentloaded")
        await self.delay(page, 4)

        # Wait for product tiles to render
        try:
            await page.wait_for_selector(
                "div[data-item-id]", state="attached", timeout=15000
            )
        except Exception:
            self.log(f"      ⚠ No products found for '{term}'")
            await page.screenshot(path=f"debug_walmart_{term.replace(' ', '_')}.png")
            return

        # Scroll down to load more items (lazy loading)
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.delay(page, 1.5)

        tiles = await page.locator("div[data-item-id]").all()
        self.log(f"      Found {len(tiles)} product tiles")

        for tile in tiles:
            if self.has_reached_limit():
                break
            try:
                data = await self._extract_product(tile, term.title())
                if not data or not data.get("sku"):
                    continue
                # Dedupe across searches
                if data["sku"] in seen_ids:
                    continue
                seen_ids.add(data["sku"])
                self.save_product(data)
            except Exception:
                self.stats["errors"] += 1

    async def _extract_product(self, tile, category: str) -> dict:
        """Extract product data from a Walmart Business tile."""

        # ── SKU (item ID from data attribute) ───────────────────
        item_id = ""
        try:
            item_id = await tile.get_attribute("data-item-id") or ""
        except Exception:
            pass

        # Also try data-dca-id (numeric Walmart item number)
        dca_id = ""
        try:
            dca_id = await tile.get_attribute("data-dca-id") or ""
        except Exception:
            pass

        # ── Name ────────────────────────────────────────────────
        name = await self.safe_text(
            tile.locator('span[data-automation-id="product-title"]')
        )
        if not name:
            # Fallback: get from the link's accessible text
            name = await self.safe_text(tile.locator("a span.ld_FS"))
        if not name or len(name) < 3:
            return {}

        # Clean "current price $X.XX" from name if it leaked in
        name = re.sub(r'current price \$[\d,.]+', '', name).strip()

        # ── Price ───────────────────────────────────────────────
        price = None
        # Primary: the bold price div
        price_text = await self.safe_text(
            tile.locator('div[data-automation-id="product-price"] div.b.black')
        )
        if not price_text:
            price_text = await self.safe_text(
                tile.locator('div[data-automation-id="product-price"]')
            )
        price = self.parse_price(price_text)

        # ── URL ─────────────────────────────────────────────────
        link = await self.safe_attr(
            tile.locator("a[link-identifier], a[href*='/ip/']"),
            "href",
        )
        product_url = ""
        sku = item_id or dca_id
        if link:
            # Clean &amp; entities
            link = link.replace("&amp;", "&")
            product_url = link if link.startswith("http") else f"{self.BASE_URL}{link}"
            # Extract numeric item ID from URL: /ip/name/130874598
            m = re.search(r'/ip/[^/]+/(\d+)', link)
            if m and not sku:
                sku = m.group(1)

        if not sku:
            sku = f"wm-{abs(hash(name)) % 10**8}"

        # ── Image ───────────────────────────────────────────────
        image_url = await self.safe_attr(tile.locator("img"), "src")
        if not image_url:
            # Try srcset
            srcset = await self.safe_attr(tile.locator("img"), "srcset")
            if srcset:
                image_url = srcset.split(" ")[0].split(",")[0]

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
    scraper = WalmartScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
