"""
Ghirardelli Scraper
═══════════════════════════════════════════════════════════════════
Scrapes product data from ghirardelli.com (Magento 2).
Product tiles use standard Magento classes:
  li.item.product.product-item
  a.product-item-link  (name)
  span[data-price-amount]  (price)
  img.product-image-photo  (image)
  div.product-item-info[class*='product-id-']  (internal ID)

All products are on /chocolate/all-chocolate (144 items, no pagination needed).

Usage:
    python3 -m scrapers.ghirardelli
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class GhirardelliScraper(BaseScraper):
    VENDOR_SLUG = "ghirardelli"
    VENDOR_NAME = "Ghirardelli"
    BASE_URL = "https://www.ghirardelli.com"

    # Categories discovered from site nav
    CATEGORIES = [
        ("/chocolate/all-chocolate", "All Chocolate"),
        ("/baking-and-more/baking", "Baking"),
        ("/baking-and-more/powders-and-sauces/hot-cocoa", "Hot Cocoa"),
        ("/gifts/all-gifts", "Gifts"),
    ]

    async def login(self, page) -> bool:
        """Navigate to Ghirardelli — public catalog, login optional."""
        email, password = self.get_credentials("GHIRARDELLI")

        await page.goto(
            f"{self.BASE_URL}/chocolate/all-chocolate",
            wait_until="domcontentloaded",
        )
        await self.delay(page, 3)

        # Dismiss cookie banner
        try:
            btn = page.locator(
                "button#onetrust-accept-btn-handler, "
                "button:has-text('Accept All')"
            )
            if await btn.first.is_visible(timeout=3000):
                await btn.first.click()
                await self.delay(page, 1)
        except Exception:
            pass

        # Optional login (prices may differ for logged-in users)
        if email and password:
            try:
                await page.goto(
                    f"{self.BASE_URL}/customer/account/login/",
                    wait_until="domcontentloaded",
                )
                await self.delay(page, 2)
                await page.locator("input#email").first.fill(email, timeout=5000)
                await page.locator("input#pass").first.fill(password, timeout=5000)
                await page.locator("button#send2").first.click(timeout=5000)
                await self.delay(page, 4)
                if "login" not in page.url.lower():
                    self.log("  ✅ Logged in")
            except Exception as e:
                self.log(f"  ⚠ Login skipped: {e}")

        self.log("  ✅ Proceeding with public catalog")
        return True

    async def scrape(self, page) -> None:
        """Scrape Ghirardelli product catalog by category."""
        seen_urls = set()

        for path, cat_name in self.CATEGORIES:
            if self.has_reached_limit():
                break
            self.log(f"\n  📂 Category: {cat_name}")
            try:
                await self._scrape_category(page, path, cat_name, seen_urls)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"category": cat_name, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    async def _scrape_category(self, page, path: str, category: str, seen_urls: set):
        """Scrape all products on a Ghirardelli category page."""
        url = f"{self.BASE_URL}{path}"
        await page.goto(url, wait_until="domcontentloaded")
        await self.delay(page, 3)

        # Wait for product grid to appear
        try:
            await page.wait_for_selector(
                "li.product-item", state="attached", timeout=10000
            )
        except Exception:
            self.log(f"      ⚠ No product grid found")
            await page.screenshot(path=f"debug_ghir_{category.replace(' ', '_')}.png")
            return

        tiles = await page.locator("li.product-item").all()
        self.log(f"      Found {len(tiles)} product tiles")

        for tile in tiles:
            if self.has_reached_limit():
                break
            try:
                data = await self._extract_product(tile, category)
                if not data or not data.get("sku"):
                    continue
                # Dedupe across categories
                if data.get("product_url") in seen_urls:
                    continue
                seen_urls.add(data.get("product_url", ""))
                self.save_product(data)
            except Exception as e:
                self.stats["errors"] += 1

    async def _extract_product(self, tile, category: str) -> dict:
        """Extract product data from a Magento 2 product tile."""

        # ── Name ────────────────────────────────────────────────
        name = await self.safe_text(
            tile.locator("a.product-item-link, strong.product-item-name")
        )
        if not name or len(name) < 3:
            return {}

        # ── Price ───────────────────────────────────────────────
        price = None
        # Try data-price-amount attribute first (most reliable)
        price_el = tile.locator("span[data-price-amount]")
        try:
            price_str = await price_el.first.get_attribute("data-price-amount", timeout=2000)
            if price_str:
                price = float(price_str)
        except Exception:
            pass
        if not price:
            price_text = await self.safe_text(tile.locator("span.price"))
            price = self.parse_price(price_text)

        # ── URL ─────────────────────────────────────────────────
        link = await self.safe_attr(
            tile.locator("a.product-item-link, a.product-item-photo__link"),
            "href",
        )
        product_url = ""
        if link:
            product_url = link if link.startswith("http") else f"{self.BASE_URL}{link}"

        # ── SKU (from URL slug) ─────────────────────────────────
        sku = ""
        if link:
            # URL pattern: /product-name-SKUCODE  e.g. /ab-custom-mix-box-100-pc-85432
            slug = link.rstrip("/").split("/")[-1]
            # Extract trailing numeric/alphanumeric SKU code
            m = re.search(r'-(\d{3,}[a-z]*(?:cs)?)$', slug, re.IGNORECASE)
            if m:
                sku = m.group(1)
            else:
                # Use full slug as SKU
                sku = slug

        if not sku:
            sku = f"ghir-{abs(hash(name)) % 10**8}"

        # ── Image ───────────────────────────────────────────────
        image_url = await self.safe_attr(
            tile.locator("img.product-image-photo"), "src"
        )

        # ── In stock ────────────────────────────────────────────
        in_stock = True
        try:
            classes = await tile.get_attribute("class") or ""
            if "unavailable" in classes.lower():
                in_stock = False
        except Exception:
            pass

        return {
            "sku": sku,
            "product_name": name.strip(),
            "brand": "Ghirardelli",
            "category": category,
            "unit_price": price,
            "product_url": product_url,
            "image_url": image_url,
            "in_stock": in_stock,
        }


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = GhirardelliScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
