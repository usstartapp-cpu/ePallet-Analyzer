"""
Shopify Base Scraper — Shared logic for all Shopify-based vendors
═══════════════════════════════════════════════════════════════════
Many vendors (Alessi, Vigo, Johnson Bros, Every Day Supply) run on
Shopify.  They all share the same login flow and products.json API.

Subclasses only need to set class-level constants.

Usage:
    class AlessiScraper(ShopifyBaseScraper):
        VENDOR_SLUG = "alessi"
        STORE_URL = "https://alessifoods.com"
        ENV_PREFIX = "ALESSI"
        BRAND_NAME = "Alessi"
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class ShopifyBaseScraper(BaseScraper):
    """Base class for Shopify-powered vendor stores."""

    STORE_URL: str = ""        # Override: "https://alessifoods.com"
    ENV_PREFIX: str = ""       # Override: "ALESSI"
    BRAND_NAME: str = ""       # Override: "Alessi"
    SCRAPE_METHOD = "shopify_api"

    async def login(self, page) -> bool:
        """Log in via Shopify's standard /account/login form."""
        email, password = self.get_credentials(self.ENV_PREFIX)

        if not email or not password:
            self.log(f"  ⚠ No credentials for {self.ENV_PREFIX}, proceeding unauthenticated")
            await page.goto(self.STORE_URL, wait_until="domcontentloaded")
            return True

        login_url = f"{self.STORE_URL}/account/login"
        await page.goto(login_url, wait_until="domcontentloaded")
        await self.delay(page, 2)

        self.log(f"  → Entering credentials ({email})...")
        try:
            await page.locator(
                "input#customer_email, input[name='customer[email]'], input[type='email']"
            ).first.fill(email, timeout=5000)
            await page.locator(
                "input#customer_password, input[name='customer[password]'], input[type='password']"
            ).first.fill(password, timeout=5000)

            self.log("  → Submitting...")
            await page.locator(
                "button[type='submit'], input[type='submit']"
            ).first.click(timeout=5000)
            await self.delay(page, 4)
        except Exception as e:
            self.log(f"  ⚠ Login form issue: {e}")
            await page.screenshot(path=f"debug_{self.VENDOR_SLUG}_login.png")

        # Verify
        if "/account" in page.url and "login" not in page.url:
            return True

        self.log("  ⚠ Login unclear, continuing...")
        await page.screenshot(path=f"debug_{self.VENDOR_SLUG}_login.png")
        return True  # Most Shopify stores work without auth

    async def scrape(self, page) -> None:
        """Scrape via Shopify's products.json API, fallback to HTML."""
        products_found = await self._scrape_via_api(page)

        if not products_found:
            self.log("  📂 API returned nothing — falling back to HTML scraping")
            await self._scrape_via_html(page)

    async def _scrape_via_api(self, page) -> bool:
        """Use Shopify's /products.json endpoint."""
        page_num = 1
        found_any = False

        while not self.has_reached_limit():
            api_url = f"{self.STORE_URL}/products.json?limit=250&page={page_num}"
            self.log(f"  📦 Fetching products.json page {page_num}...")

            resp = await page.evaluate("""
                async (url) => {
                    try {
                        const r = await fetch(url);
                        if (!r.ok) return { error: r.status };
                        return await r.json();
                    } catch(e) {
                        return { error: e.message };
                    }
                }
            """, api_url)

            if isinstance(resp, dict) and "error" in resp:
                self.log(f"      ✗ API error: {resp['error']}")
                break

            api_products = resp.get("products", [])
            if not api_products:
                self.log(f"      No more products on page {page_num}")
                break

            found_any = True
            self.log(f"      Got {len(api_products)} products from API")

            for p in api_products:
                if self.has_reached_limit():
                    break
                self.save_product(self._normalize_api(p))

            if len(api_products) < 250:
                break
            page_num += 1
            await self.delay(page, 1)

        return found_any

    async def _scrape_via_html(self, page) -> None:
        """Fallback: scrape /collections/all via HTML."""
        await page.goto(f"{self.STORE_URL}/collections/all", wait_until="domcontentloaded")
        await self.delay(page, 3)

        tiles = await page.locator(
            "div.product-card, div.grid-product, a[href*='/products/'], "
            "div.product-item, div.product"
        ).all()
        self.log(f"      Found {len(tiles)} product tiles")

        for tile in tiles:
            if self.has_reached_limit():
                break
            try:
                data = await self._extract_html_product(tile)
                if data and data.get("sku"):
                    self.save_product(data)
            except Exception:
                self.stats["errors"] += 1

    def _normalize_api(self, raw: dict) -> dict:
        """Normalize a Shopify products.json item into our schema."""
        variants = raw.get("variants", [])
        price = None
        variant_sku = ""
        in_stock = True

        if variants:
            price_str = variants[0].get("price")
            if price_str:
                try:
                    price = float(price_str)
                except (ValueError, TypeError):
                    pass
            variant_sku = variants[0].get("sku", "")
            in_stock = variants[0].get("available", True)

        sku = variant_sku or str(raw.get("id", ""))
        images = raw.get("images", [])
        image_url = images[0].get("src", "") if images else ""

        description = raw.get("body_html", "") or ""
        # Strip HTML tags from description
        description = re.sub(r"<[^>]+>", " ", description).strip()[:500]

        return {
            "sku": sku,
            "product_name": raw.get("title", ""),
            "brand": raw.get("vendor", self.BRAND_NAME),
            "description": description,
            "category": raw.get("product_type", ""),
            "unit_price": price,
            "product_url": f"{self.STORE_URL}/products/{raw.get('handle', '')}",
            "image_url": image_url,
            "in_stock": in_stock,
        }

    async def _extract_html_product(self, element) -> dict:
        """Extract product from an HTML tile element."""
        name = ""
        price = None
        sku = ""
        url = ""

        for sel in ["h3", "h2", "p[class*='title']", "span[class*='title']",
                     "a[class*='title']"]:
            try:
                name = await element.locator(sel).first.inner_text(timeout=1500)
                if name.strip():
                    break
            except Exception:
                continue

        try:
            price_text = await element.locator(
                "span[class*='price'], span.money, div.price"
            ).first.inner_text(timeout=1500)
            price = self.parse_price(price_text)
        except Exception:
            pass

        try:
            link = await element.locator("a[href*='/products/']").first.get_attribute("href", timeout=1500)
            if not link:
                link = await element.get_attribute("href", timeout=1500)
            if link:
                url = link if link.startswith("http") else f"{self.STORE_URL}{link}"
                m = re.search(r"/products/([^/?]+)", link)
                if m:
                    sku = m.group(1)
        except Exception:
            pass

        if not sku and name:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())[:50]
            sku = f"{self.VENDOR_SLUG}-{slug}"

        if not name.strip():
            return {}

        return {
            "sku": sku,
            "product_name": name.strip(),
            "brand": self.BRAND_NAME,
            "category": "All Products",
            "unit_price": price,
            "product_url": url,
            "in_stock": True,
        }
