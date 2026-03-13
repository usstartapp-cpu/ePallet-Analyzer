"""
Amazon Price Comparison Scraper
═══════════════════════════════════════════════════════════════════
Searches Amazon for products already in our database (from other vendors)
and records the Amazon price. This gives us a price comparison baseline:
every product we track has an Amazon price to compare against.

Strategy:
  1. Pull existing product names from Supabase (all non-Amazon vendors)
  2. Search each product on Amazon
  3. Extract the best matching result: price, ASIN, title, image, URL
  4. Save as vendor "amazon"

Amazon search pages use these selectors:
  div[data-component-type="s-search-result"]   — each result card
  span.a-price > span.a-offscreen              — price text
  h2 a.a-link-normal                           — title link
  img.s-image                                  — product image
  data-asin attribute                          — ASIN (Amazon SKU)

Usage:
    python3 -m scrapers.amazon
    python3 -m scrapers.amazon --limit 100
"""

import asyncio
import re
import sys
import os
import urllib.parse
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper
from db.supabase_client import supabase


class AmazonScraper(BaseScraper):
    VENDOR_SLUG = "amazon"
    VENDOR_NAME = "Amazon"
    BASE_URL = "https://www.amazon.com"

    def __init__(self):
        super().__init__()
        self.search_terms: list[dict] = []  # [{product_name, sku, brand, ...}]

    def load_search_terms(self, limit: int = 100):
        """
        Pull distinct product names from Supabase (non-Amazon vendors).
        We'll search Amazon for each of these.
        Prioritize products that have a brand + specific name.
        """
        self.log("📋 Loading products from Supabase to search on Amazon...")

        # Get Amazon vendor_id so we can exclude it
        amazon_vendor = supabase.table("vendors").select("id").eq("slug", "amazon").execute()
        amazon_vid = amazon_vendor.data[0]["id"] if amazon_vendor.data else None

        # Pull products from all other vendors
        query = supabase.table("products").select(
            "sku, product_name, brand, category, unit_price, vendor_id"
        )

        if amazon_vid:
            query = query.neq("vendor_id", amazon_vid)

        # Order by product_name to get variety
        resp = query.order("product_name").limit(2000).execute()

        if not resp.data:
            self.log("  ⚠ No products found in database!")
            return

        # Deduplicate by product_name (keep first occurrence)
        seen_names = set()
        unique_products = []
        for p in resp.data:
            name_key = p["product_name"].strip().lower()
            if name_key not in seen_names and len(name_key) > 5:
                seen_names.add(name_key)
                unique_products.append(p)

        # Shuffle for variety, then take up to `limit`
        random.shuffle(unique_products)
        self.search_terms = unique_products[:limit]

        self.log(f"  ✅ Loaded {len(self.search_terms)} unique products to search")

    async def login(self, page) -> bool:
        """
        Navigate to Amazon — no login needed for public search.
        Just load the homepage to set cookies.
        """
        await page.goto(self.BASE_URL, wait_until="domcontentloaded")
        await self.delay(page, 3)

        # Dismiss any popups / location prompts
        try:
            dismiss = page.locator("input#GLUXConfirmClose, button:has-text('Stay on Amazon.com')")
            if await dismiss.first.is_visible(timeout=3000):
                await dismiss.first.click()
                await self.delay(page, 1)
        except Exception:
            pass

        self.log("  ✅ Amazon loaded (public search)")
        return True

    async def scrape(self, page) -> None:
        """Search Amazon for each product in our database."""
        if not self.search_terms:
            self.load_search_terms(limit=self.MAX_PRODUCTS or 100)

        if not self.search_terms:
            self.log("  ⚠ No products to search!")
            return

        total = len(self.search_terms)
        for idx, product in enumerate(self.search_terms):
            if self.has_reached_limit():
                break

            product_name = product["product_name"]
            brand = product.get("brand", "")
            original_sku = product.get("sku", "")

            # Build search query — use brand + product name for better results
            search_query = self._build_search_query(product_name, brand, original_sku)

            self.log(f"\n  🔍 [{idx+1}/{total}] Searching: {search_query[:70]}...")

            try:
                result = await self._search_amazon(page, search_query, product)
                if result:
                    self.save_product(result)
                    self.log(f"      ✅ ${result.get('unit_price', 0):.2f} — {result['product_name'][:50]}")
                else:
                    self.log(f"      ⚠ No match found")
                    self.stats["errors"] += 1
            except Exception as e:
                self.log(f"      ❌ Error: {e}")
                self.stats["errors"] += 1
                self.stats["error_log"].append({
                    "search": search_query[:80],
                    "error": str(e)
                })

            # Polite delay to avoid throttling (randomized)
            delay = random.uniform(2.0, 5.0)
            await self.delay(page, delay)

    def _build_search_query(self, product_name: str, brand: str, sku: str) -> str:
        """
        Build an effective Amazon search query from product data.
        Cleans up vendor-specific formatting.
        """
        # Clean up the product name
        query = product_name.strip()

        # Remove leading size info like "10 oz" if brand is available
        # Remove vendor-specific prefixes
        query = re.sub(r'^\d+(\.\d+)?\s*(oz|lb|lbs|gal|ct|pk|pc|count)\s+', '', query, flags=re.IGNORECASE)

        # If brand isn't already in the name, prepend it
        if brand and brand.lower() not in query.lower():
            query = f"{brand} {query}"

        # Remove excessive whitespace
        query = re.sub(r'\s+', ' ', query).strip()

        # Cap length for Amazon search (too long = bad results)
        if len(query) > 120:
            query = query[:120]

        return query

    async def _search_amazon(self, page, search_query: str, original_product: dict) -> dict | None:
        """
        Search Amazon for a product and extract the best result.
        Returns product dict or None.
        """
        # URL-encode the search query
        encoded = urllib.parse.quote_plus(search_query)
        search_url = f"{self.BASE_URL}/s?k={encoded}"

        await page.goto(search_url, wait_until="domcontentloaded")
        await self.delay(page, 2)

        # Check for CAPTCHA
        captcha = page.locator("form[action='/errors/validateCaptcha']")
        try:
            if await captcha.first.is_visible(timeout=1500):
                self.log("      ⚠ CAPTCHA detected — waiting 30s then retrying...")
                await self.delay(page, 30)
                await page.goto(search_url, wait_until="domcontentloaded")
                await self.delay(page, 3)
                if await captcha.first.is_visible(timeout=1500):
                    self.log("      ❌ Still CAPTCHA — skipping")
                    return None
        except Exception:
            pass

        # Wait for search results
        try:
            await page.wait_for_selector(
                "div[data-component-type='s-search-result']",
                state="attached",
                timeout=10000
            )
        except Exception:
            # Maybe no results or different page structure
            self.log("      ⚠ No search results container found")
            return None

        # Get all result cards (take first few for best match)
        results = await page.locator(
            "div[data-component-type='s-search-result']"
        ).all()

        if not results:
            return None

        # Try first 3 results to find best match
        for result in results[:3]:
            try:
                data = await self._extract_result(result, original_product)
                if data and data.get("unit_price"):
                    return data
            except Exception:
                continue

        # If none had prices, try first result anyway
        if results:
            try:
                data = await self._extract_result(results[0], original_product)
                if data:
                    return data
            except Exception:
                pass

        return None

    async def _extract_result(self, result, original_product: dict) -> dict | None:
        """Extract product data from an Amazon search result card."""

        # ── ASIN (Amazon's SKU) ─────────────────────────────────
        asin = ""
        try:
            asin = await result.get_attribute("data-asin") or ""
        except Exception:
            pass
        if not asin:
            return None

        # ── Title ───────────────────────────────────────────────
        title = ""
        # Best selector: data-cy="title-recipe" contains the full product title
        title_selectors = [
            '[data-cy="title-recipe"] a span',
            '.s-title-instructions-style a span',
            'a.a-link-normal span.a-text-normal',
            'h2 a.a-link-normal span',
        ]
        for sel in title_selectors:
            try:
                loc = result.locator(sel)
                if await loc.count() > 0:
                    title = (await loc.first.inner_text(timeout=2000)).strip()
                    if title and len(title) > 10:
                        break
            except Exception:
                continue

        if not title or len(title) < 5:
            return None

        # Skip sponsored/ad results with no real product data
        if "sponsored" in title.lower() and len(title) < 20:
            return None

        # ── Price ───────────────────────────────────────────────
        price = None

        # Method 1: a-offscreen (hidden accessible price text)
        price_el = result.locator("span.a-price span.a-offscreen")
        try:
            price_text = await price_el.first.inner_text(timeout=2000)
            price = self.parse_price(price_text)
        except Exception:
            pass

        # Method 2: a-price-whole + a-price-fraction
        if not price:
            try:
                whole = await self.safe_text(result.locator("span.a-price-whole"))
                fraction = await self.safe_text(result.locator("span.a-price-fraction"))
                if whole:
                    whole = whole.replace(",", "").rstrip(".")
                    fraction = fraction or "00"
                    price = float(f"{whole}.{fraction}")
            except Exception:
                pass

        # ── URL ─────────────────────────────────────────────────
        product_url = ""
        link_selectors = [
            '[data-cy="title-recipe"] a.a-link-normal',
            '.s-title-instructions-style a.a-link-normal',
            'h2 a.a-link-normal',
            'a.a-link-normal[href*="/dp/"]',
        ]
        for sel in link_selectors:
            try:
                loc = result.locator(sel)
                if await loc.count() > 0:
                    href = await loc.first.get_attribute("href", timeout=2000)
                    if href:
                        if href.startswith("/"):
                            product_url = f"{self.BASE_URL}{href}"
                        elif href.startswith("http"):
                            product_url = href
                        else:
                            product_url = f"{self.BASE_URL}/{href}"
                        # Clean tracking params but keep /dp/ASIN
                        if "/dp/" in product_url:
                            match = re.search(r'(/dp/[A-Z0-9]{10})', product_url)
                            if match:
                                product_url = f"{self.BASE_URL}{match.group(1)}"
                        break
            except Exception:
                continue

        if not product_url and asin:
            product_url = f"{self.BASE_URL}/dp/{asin}"

        # ── Image ───────────────────────────────────────────────
        image_url = ""
        img_el = result.locator("img.s-image")
        try:
            image_url = await img_el.first.get_attribute("src", timeout=2000) or ""
        except Exception:
            pass

        # ── Rating (bonus data) ─────────────────────────────────
        rating = None
        try:
            rating_text = await self.safe_text(result.locator("span.a-icon-alt"))
            if rating_text:
                m = re.search(r'([\d.]+)\s+out', rating_text)
                if m:
                    rating = float(m.group(1))
        except Exception:
            pass

        # ── Build product dict ──────────────────────────────────
        # Use original product's category and brand for cross-referencing
        return {
            "sku": asin,  # Amazon ASIN as the SKU
            "product_name": title,
            "brand": self._extract_brand(title, original_product.get("brand", "")),
            "category": original_product.get("category", ""),
            "unit_price": price,
            "product_url": product_url,
            "image_url": image_url,
            "in_stock": True,
            # Store the original product name in description for cross-referencing
            "description": f"Amazon match for: {original_product.get('product_name', '')[:200]}",
        }

    def _extract_brand(self, title: str, original_brand: str) -> str:
        """Extract brand from Amazon title or use original product's brand."""
        if original_brand:
            return original_brand
        # Amazon titles usually start with brand name
        parts = title.split()
        if len(parts) >= 2:
            return parts[0]
        return ""


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Amazon Price Comparison Scraper")
    parser.add_argument("--limit", type=int, default=100,
                        help="Number of products to search (default: 100)")
    args = parser.parse_args()

    scraper = AmazonScraper()
    scraper.MAX_PRODUCTS = args.limit
    scraper.load_search_terms(limit=args.limit)
    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
