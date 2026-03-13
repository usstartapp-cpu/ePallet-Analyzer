"""
Barilla Scraper
═══════════════════════════════════════════════════════════════════
Scrapes product data from barilla.com/en-us public catalog.

Strategy:
  1. Use the search page (/en-us/products/search) to discover ALL product URLs
     — scroll to lazy-load all 88 product cards.
  2. Visit each product detail page to extract:
     • Product name, description, pack size, image  (from JSON-LD)
     • GTIN / UPC  (from nutrition iframe URL)
     • Category    (from URL path, e.g. classic-blue-box → Classic Blue Box)

Note: barilla.com is a *brand awareness* site — no retail prices.
      unit_price is left as None.

Usage:
    python3 -m scrapers.barilla
"""

import asyncio
import json
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class BarillaScraper(BaseScraper):
    VENDOR_SLUG = "barilla"
    VENDOR_NAME = "Barilla"
    BASE_URL = "https://www.barilla.com"

    # ── login ───────────────────────────────────────────────────

    async def login(self, page) -> bool:
        """Navigate to Barilla — public catalog, no login needed."""
        await page.goto(f"{self.BASE_URL}/en-us", wait_until="domcontentloaded")
        await self.delay(page, 3)

        # Dismiss cookie banner
        try:
            await page.locator("button#onetrust-accept-btn-handler").click(timeout=5000)
            await self.delay(page, 1)
        except Exception:
            pass

        self.log("  ✅ Proceeding with public catalog")
        return True

    # ── scrape ──────────────────────────────────────────────────

    async def scrape(self, page) -> None:
        """
        Phase 1: Discover all product URLs from category listing pages.
        Phase 2: Visit each product page and extract structured data.
        """
        product_urls = await self._discover_products(page)
        self.log(f"\n  📋 Discovered {len(product_urls)} unique products")

        for idx, (href, category) in enumerate(product_urls, 1):
            if self.has_reached_limit():
                break
            full_url = f"{self.BASE_URL}{href}" if not href.startswith("http") else href
            self.log(f"  [{idx}/{len(product_urls)}] {href.split('/')[-1]}")
            try:
                data = await self._scrape_product_page(page, full_url, category)
                if data and data.get("sku"):
                    self.save_product(data)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"url": full_url, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    # ── Phase 1: discover product URLs ──────────────────────────

    async def _discover_products(self, page) -> list[tuple[str, str]]:
        """
        Visit each category listing page to collect product detail URLs.
        Returns list of (href, category_name) tuples, deduplicated.
        """
        categories = [
            ("/en-us/products/pasta/classic-blue-box", "Classic Blue Box"),
            ("/en-us/products/pasta/al-bronzo", "Al Bronzo"),
            ("/en-us/products/pasta/legume", "Legume Pasta"),
            ("/en-us/products/pasta/gluten-free", "Gluten Free"),
            ("/en-us/products/pasta/protein-plus", "Protein+"),
            ("/en-us/products/pasta/ready-pasta", "Ready Pasta"),
            ("/en-us/products/pasta/whole-grain-pasta", "Whole Grain"),
            ("/en-us/products/pesto/pesto-pasta-sauce", "Pesto"),
            ("/en-us/products/sauce/premium-sauce", "Premium Sauces"),
        ]

        seen: set[str] = set()
        results: list[tuple[str, str]] = []

        for path, cat_name in categories:
            url = f"{self.BASE_URL}{path}"
            self.log(f"\n  📂 Category: {cat_name}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self.delay(page, 3)

                cards = await page.locator("div.cardBase.productCell").all()
                self.log(f"      Found {len(cards)} product cards")

                for card in cards:
                    a = card.locator("a")
                    href = await self.safe_attr(a, "href")
                    if href and href not in seen:
                        seen.add(href)
                        results.append((href, cat_name))

            except Exception as e:
                self.log(f"      ❌ Category error: {e}")
                self.stats["errors"] += 1

        return results

    # ── Phase 2: scrape individual product pages ────────────────

    async def _scrape_product_page(self, page, url: str, category: str) -> dict:
        """
        Visit a product detail page and extract:
          - JSON-LD structured data  → name, description, image, pack size, etc.
          - GTIN from nutrition iframe URL
          - SKU derived from URL slug
        """
        # Barilla's site can be slow — retry with longer timeout
        for attempt in range(2):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                break
            except Exception as e:
                if attempt == 0:
                    self.log(f"      ⏳ Retrying {url.split('/')[-1]}...")
                    await self.delay(page, 2)
                else:
                    raise
        await self.delay(page, 1.5)

        # ── Extract JSON-LD ─────────────────────────────────────
        ld_data = {}
        try:
            scripts = await page.locator('script[type="application/ld+json"]').all()
            for s in scripts:
                raw = await s.inner_text(timeout=3000)
                parsed = json.loads(raw)
                if parsed.get("@type") == "Product":
                    ld_data = parsed
                    break
        except Exception:
            pass

        name = ld_data.get("name", "").strip()
        if not name:
            # Fallback: try h1
            name = await self.safe_text(page.locator("h1"))

        if not name or len(name) < 2:
            return {}

        description = ld_data.get("description", "").strip()

        # Pack size from JSON-LD
        pack_sizes = ld_data.get("packSizes", [])
        pack_size = ""
        if pack_sizes and isinstance(pack_sizes[0], list) and pack_sizes[0]:
            pack_size = pack_sizes[0][0]
        elif pack_sizes and isinstance(pack_sizes[0], str):
            pack_size = pack_sizes[0]

        # Image URL
        images = ld_data.get("image", [])
        image_url = images[0] if isinstance(images, list) and images else ""

        # Product range from JSON-LD (e.g. "Classic Blue Box")
        product_range = ld_data.get("productRange", "")

        # Rating
        rating_data = ld_data.get("aggregateRating", {})
        rating = rating_data.get("ratingValue")

        # ── Extract GTIN / UPC from nutrition iframe ────────────
        gtin = ""
        try:
            iframe = page.locator("iframe.externalIframeNutrition")
            src = await iframe.get_attribute("src", timeout=3000)
            if src:
                m = re.search(r"GTIN=(\d+)", src)
                if m:
                    gtin = m.group(1).lstrip("0") or m.group(1)
        except Exception:
            pass

        # ── Derive SKU from URL slug ────────────────────────────
        slug = url.rstrip("/").split("/")[-1]
        sku = gtin if gtin else f"barilla-{slug}"

        # ── Ingredients from JSON-LD ────────────────────────────
        ingredients = ld_data.get("ingredients", [])
        ingredients_str = "; ".join(ingredients) if ingredients else ""

        # Build description: include pack size if available
        desc_parts = []
        if pack_size:
            desc_parts.append(f"Pack size: {pack_size}")
        if description:
            desc_parts.append(description[:450])
        full_desc = " | ".join(desc_parts) if desc_parts else None

        return {
            "sku": sku,
            "upc": gtin if gtin else None,
            "product_name": f"Barilla {name}",
            "brand": "Barilla",
            "category": category,
            "description": full_desc,
            "unit_price": None,  # Brand site — no retail pricing
            "product_url": url,
            "image_url": image_url if image_url else None,
            "in_stock": True,
        }


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = BarillaScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
