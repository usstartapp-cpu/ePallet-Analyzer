"""
Faire Wholesale Scraper
═══════════════════════════════════════════════════════════════════
Scrapes wholesale product data from faire.com (public catalog).
Faire's login page 404s, so we scrape the public search results.
Prices are hidden ("Unlock wholesale price") — we capture
product names, brands, images, and ratings.

Uses data-test-id selectors discovered via DOM inspection:
  • [data-test-id="product-tile"]             — tile container
  • [data-test-id="product-tile-product-information"] — name link
  • [data-test-id="product-tile-image"]       — image link
  • img inside tile                            — product image
  • inline-block <a> without data-test-id     — brand name

Usage:
    python3 -m scrapers.faire
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class FaireScraper(BaseScraper):
    VENDOR_SLUG = "faire"
    VENDOR_NAME = "Faire"
    BASE_URL = "https://www.faire.com"

    async def login(self, page) -> bool:
        """
        Faire's login page currently redirects to 404.
        We scrape the public catalog instead (prices hidden).
        """
        self.log("  ℹ Faire login unavailable — scraping public catalog")
        self.log("  ℹ Prices hidden ('Unlock wholesale price')")
        await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
        await self.delay(page, 3)
        return True  # No auth needed for public search

    async def scrape(self, page) -> None:
        """Scrape Faire via search across food & grocery categories."""
        search_terms = [
            ("canned food", "Canned Food"),
            ("pasta wholesale", "Pasta"),
            ("snacks wholesale", "Snacks"),
            ("condiments", "Condiments"),
            ("sauce wholesale", "Sauces"),
            ("chocolate wholesale", "Chocolate"),
            ("coffee wholesale", "Coffee"),
            ("tea wholesale", "Tea"),
            ("honey wholesale", "Honey"),
            ("spices wholesale", "Spices"),
            ("pickles", "Pickles"),
            ("jam wholesale", "Jams & Preserves"),
            ("popcorn wholesale", "Popcorn"),
            ("candy wholesale", "Candy"),
            ("cookies wholesale", "Cookies"),
            ("chips wholesale", "Chips"),
            ("granola wholesale", "Granola"),
            ("olive oil wholesale", "Olive Oil"),
            ("hot sauce", "Hot Sauce"),
            ("dried fruit wholesale", "Dried Fruit"),
        ]

        seen_ids = set()

        for query, category in search_terms:
            if self.has_reached_limit():
                break
            self.log(f"\n  📂 Searching: {query}")
            try:
                new = await self._scrape_search(page, query, category, seen_ids)
                self.log(f"      → {new} new products (total: {self.stats['products_found']})")
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"search": query, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    async def _scrape_search(self, page, query: str, category: str, seen_ids: set) -> int:
        """Scrape a single search results page."""
        url = (
            f"{self.BASE_URL}/search"
            f"?q={query.replace(' ', '+')}"
            f"&category=food-drink"
        )
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await self.delay(page, 4)

        # Scroll to trigger lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 800)")
            await self.delay(page, 1)

        # Extract products via JS for reliability (Faire uses React)
        products = await page.evaluate(r'''() => {
            const tiles = document.querySelectorAll('[data-test-id="product-tile"]');
            const results = [];
            for (const tile of tiles) {
                // Product name from product-information anchor
                const nameEl = tile.querySelector('[data-test-id="product-tile-product-information"]');
                const name = nameEl ? nameEl.textContent.trim() : '';

                // Brand name from inline-block anchor without data-test-id
                const allAnchors = tile.querySelectorAll('a');
                let brand = '';
                for (const a of allAnchors) {
                    if (a.className.includes('inline-block') && !a.getAttribute('data-test-id')) {
                        brand = a.textContent.trim();
                        break;
                    }
                }

                // Product ID and brand ID from href
                let productId = '';
                let brandId = '';
                let productUrl = '';
                for (const a of allAnchors) {
                    const href = a.href || '';
                    if (!productId) {
                        const pm = href.match(/product=(p_[a-z0-9]+)/);
                        if (pm) {
                            productId = pm[1];
                            productUrl = href;
                        }
                    }
                    if (!brandId) {
                        const bm = href.match(/brand=(b_[a-z0-9]+)/);
                        if (bm) brandId = bm[1];
                    }
                }

                // Image URL
                const img = tile.querySelector('img');
                const imgSrc = img ? (img.src || img.getAttribute('data-src') || '') : '';

                // Rating
                const ratingMatch = tile.textContent.match(/(\d+\.\d+)\s*\(/);
                const rating = ratingMatch ? ratingMatch[1] : '';

                if (name && productId) {
                    results.push({
                        productId,
                        brandId,
                        name,
                        brand,
                        imgSrc,
                        rating,
                        productUrl,
                    });
                }
            }
            return results;
        }''')

        self.log(f"      Found {len(products)} tiles")

        new_count = 0
        for prod in products:
            if self.has_reached_limit():
                break

            pid = prod["productId"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            img_url = prod["imgSrc"]

            product_data = {
                "sku": pid,
                "product_name": prod["name"],
                "brand": prod["brand"],
                "category": category,
                "unit_price": None,  # Prices hidden on public Faire
                "image_url": img_url,
                "product_url": prod["productUrl"],
                "in_stock": True,
            }

            self.save_product(product_data)
            new_count += 1

        return new_count


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = FaireScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
