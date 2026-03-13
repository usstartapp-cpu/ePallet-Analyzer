"""
Generic E-Commerce Scraper — Template for simple vendor sites
═══════════════════════════════════════════════════════════════════
Many vendors share a similar login flow and product page structure.
This provides a configurable scraper for standard e-commerce sites.

Covers: Faire, Walmart, McLane, Hershey's, Ghirardelli, Barilla,
        Alessi, Vigo, Del Monte, Johnson Bros, Every Day Supply

Usage:
    python3 -m scrapers.generic --vendor faire
    python3 -m scrapers.generic --vendor hersheys
"""

import asyncio
import re
import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


# ── Vendor configurations ──────────────────────────────────────────

VENDOR_CONFIGS = {
    "faire": {
        "name": "Faire",
        "env_prefix": "FAIRE",
        "base_url": "https://www.faire.com",
        "login_url": "https://www.faire.com/login",
        "login_selectors": {
            "email": "input[name='email'], input[type='email']",
            "password": "input[name='password'], input[type='password']",
            "submit": "button[type='submit'], button:has-text('Log In')",
        },
        "product_categories": [
            ("/search?q=food&category=food-beverage", "Food & Beverage"),
            ("/search?q=snacks&category=food-beverage", "Snacks"),
            ("/search?q=pantry&category=food-beverage", "Pantry"),
        ],
    },
    "walmart": {
        "name": "Walmart Business",
        "env_prefix": "WALMART",
        "base_url": "https://business.walmart.com",
        "login_url": "https://business.walmart.com/account/login",
        "login_selectors": {
            "email": "input[name='email'], input#email",
            "password": "input[name='password'], input#password",
            "submit": "button[type='submit'], button:has-text('Sign in')",
        },
        "product_categories": [
            ("/browse/food", "Food"),
            ("/browse/beverages", "Beverages"),
            ("/browse/snacks", "Snacks"),
            ("/browse/cleaning", "Cleaning"),
        ],
    },
    "mclane": {
        "name": "McLane Xpress",
        "env_prefix": "MCLANE",
        "base_url": "https://mclanexpress.com",
        "login_url": "https://mclanexpress.com/login",
        "login_selectors": {
            "email": "input[name='email'], input[type='email'], input#username",
            "password": "input[name='password'], input[type='password']",
            "submit": "button[type='submit'], button:has-text('Login'), button:has-text('Sign In')",
        },
        "product_categories": [
            ("/products/grocery", "Grocery"),
            ("/products/snacks", "Snacks"),
            ("/products/beverages", "Beverages"),
            ("/products/candy", "Candy"),
        ],
    },
    "hersheys": {
        "name": "Hershey's",
        "env_prefix": "HERSHEYS",
        "base_url": "https://shop.hersheys.com",
        "login_url": "https://shop.hersheys.com/login",
        "login_selectors": {
            "email": "input[name='email'], input#email",
            "password": "input[name='password'], input#password",
            "submit": "button[type='submit'], button:has-text('Sign In')",
        },
        "product_categories": [
            ("/all-candy", "All Candy"),
            ("/chocolate", "Chocolate"),
            ("/baking", "Baking"),
        ],
    },
    "ghirardelli": {
        "name": "Ghirardelli",
        "env_prefix": "GHIRARDELLI",
        "base_url": "https://www.ghirardelli.com",
        "login_url": "https://www.ghirardelli.com/customer/account/login/",
        "login_selectors": {
            "email": "input#email, input[name='login[username]']",
            "password": "input[name='login[password]'], input#pass",
            "submit": "button[type='submit'], button:has-text('Sign In')",
        },
        "product_categories": [
            ("/chocolate", "Chocolate"),
            ("/baking-chocolate", "Baking"),
            ("/all-products", "All Products"),
        ],
    },
    "barilla": {
        "name": "Barilla",
        "env_prefix": "BARILLA",
        "base_url": "https://www.barilla.com",
        "login_url": "https://www.barilla.com/en-us/login",
        "login_selectors": {
            "email": "input[name='email'], input#email",
            "password": "input[name='password'], input#password",
            "submit": "button[type='submit']",
        },
        "product_categories": [
            ("/en-us/products/pasta", "Pasta"),
            ("/en-us/products/sauce", "Sauce"),
            ("/en-us/products/ready-meals", "Ready Meals"),
        ],
    },
    "alessi": {
        "name": "Alessi Foods",
        "env_prefix": "ALESSI",
        "base_url": "https://alessifoods.com",
        "login_url": "https://alessifoods.com/account/login",
        "login_selectors": {
            "email": "input#customer_email, input[name='customer[email]']",
            "password": "input#customer_password, input[name='customer[password]']",
            "submit": "button[type='submit'], input[type='submit']",
        },
        "product_categories": [
            ("/collections/all", "All Products"),
        ],
    },
    "vigo": {
        "name": "Vigo Foods",
        "env_prefix": "VIGO",
        "base_url": "https://vigofoods.com",
        "login_url": "https://vigofoods.com/account/login",
        "login_selectors": {
            "email": "input#customer_email, input[name='customer[email]']",
            "password": "input#customer_password, input[name='customer[password]']",
            "submit": "button[type='submit'], input[type='submit']",
        },
        "product_categories": [
            ("/collections/all", "All Products"),
        ],
    },
    "delmonte": {
        "name": "Del Monte Cash Back",
        "env_prefix": "DELMONTE",
        "base_url": "https://delmontefscashback.com",
        "login_url": "https://delmontefscashback.com/dashboard/",
        "login_selectors": {
            "email": "input[name='email'], input#email, input[type='email']",
            "password": "input[name='password'], input[type='password']",
            "submit": "button[type='submit'], button:has-text('Login'), button:has-text('Sign In')",
        },
        "product_categories": [
            ("/dashboard/products", "Products"),
        ],
    },
    "johnson-bros": {
        "name": "Johnson Bros. Bakery Supply",
        "env_prefix": "JOHNSONBROS",
        "base_url": "https://jbrosbakerysupply.com",
        "login_url": "https://jbrosbakerysupply.com/account/login",
        "login_selectors": {
            "email": "input#customer_email, input[name='customer[email]']",
            "password": "input#customer_password, input[name='customer[password]']",
            "submit": "button[type='submit'], input[type='submit']",
        },
        "product_categories": [
            ("/collections/all", "All Products"),
        ],
    },
    "everyday-supply": {
        "name": "Every Day Supply Co",
        "env_prefix": "EVERYDAYSUPPLY",
        "base_url": "https://everydaysupplyco.com",
        "login_url": "https://everydaysupplyco.com/account/login",
        "login_selectors": {
            "email": "input#customer_email, input[name='customer[email]']",
            "password": "input#customer_password, input[name='customer[password]']",
            "submit": "button[type='submit'], input[type='submit']",
        },
        "product_categories": [
            ("/collections/all", "All Products"),
        ],
    },
}


class GenericScraper(BaseScraper):
    """Configurable scraper for standard e-commerce vendor sites."""

    def __init__(self, vendor_slug: str):
        if vendor_slug not in VENDOR_CONFIGS:
            raise ValueError(
                f"No config for '{vendor_slug}'. "
                f"Available: {', '.join(VENDOR_CONFIGS.keys())}"
            )
        self.config = VENDOR_CONFIGS[vendor_slug]
        self.VENDOR_SLUG = vendor_slug
        self.VENDOR_NAME = self.config["name"]
        super().__init__()

    async def login(self, page) -> bool:
        """Generic login flow using configured selectors."""
        cfg = self.config
        sel = cfg["login_selectors"]
        email, password = self.get_credentials(cfg["env_prefix"])

        if not email or not password:
            self.log(f"  ⚠ No credentials for {cfg['env_prefix']}, skipping login")
            await page.goto(cfg["base_url"], wait_until="domcontentloaded")
            return True  # Some sites work without login

        await page.goto(cfg["login_url"], wait_until="domcontentloaded")
        await self.delay(page, 3)

        # Fill email
        self.log(f"  → Entering credentials ({email})...")
        try:
            await page.locator(sel["email"]).first.fill(email, timeout=5000)
        except Exception as e:
            self.log(f"  ✗ Could not find email field: {e}")
            await page.screenshot(path=f"debug_{self.VENDOR_SLUG}_login.png")
            return False

        # Fill password
        try:
            await page.locator(sel["password"]).first.fill(password, timeout=5000)
        except Exception as e:
            self.log(f"  ✗ Could not find password field: {e}")
            return False

        # Submit
        self.log("  → Submitting...")
        try:
            await page.locator(sel["submit"]).first.click(timeout=5000)
        except Exception:
            await page.keyboard.press("Enter")

        await self.delay(page, 5)

        # Verify — redirected away from login page
        if "login" not in page.url.lower() and "sign" not in page.url.lower():
            self.log("  ✓ Login appears successful")
            return True

        # Check for logged-in indicators
        try:
            indicators = "a:has-text('Account'), a:has-text('Sign Out'), "
            indicators += "a:has-text('Log Out'), .account-menu, .user-nav"
            if await page.locator(indicators).first.is_visible(timeout=3000):
                return True
        except Exception:
            pass

        self.log("  ⚠ Could not confirm login (proceeding anyway)")
        await page.screenshot(path=f"debug_{self.VENDOR_SLUG}_login.png")
        return True  # Try to proceed

    async def scrape(self, page) -> None:
        """Scrape products from configured category URLs."""
        cfg = self.config

        for path, category_name in cfg["product_categories"]:
            self.log(f"\n  📂 Category: {category_name}")
            try:
                url = f"{cfg['base_url']}{path}"
                await self._scrape_listing(page, url, category_name)
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"category": category_name, "error": str(e)})
                self.log(f"      ❌ Error: {e}")

    async def _scrape_listing(self, page, url: str, category: str):
        """Scrape a product listing page (with pagination)."""
        page_num = 1

        while page_num <= 15:  # Safety limit
            page_url = f"{url}{'&' if '?' in url else '?'}page={page_num}" if page_num > 1 else url
            await page.goto(page_url, wait_until="domcontentloaded")
            await self.delay(page, 3)

            # Try multiple common product selectors
            products = []
            selectors = [
                ".product-card",
                ".product-item",
                ".product-tile",
                ".grid-product",
                "[data-product-id]",
                ".product",
                ".collection-product",
                "article.product",
                ".product-grid-item",
            ]

            for sel in selectors:
                products = await page.locator(sel).all()
                if products:
                    break

            if not products:
                if page_num == 1:
                    self.log(f"      ⚠ No products found (may need selector tuning)")
                break

            self.log(f"      Page {page_num} — {len(products)} items")

            for prod_el in products:
                try:
                    data = await self._extract_product(prod_el, category)
                    if data and data.get("sku"):
                        self.save_product(data)
                except Exception:
                    self.stats["errors"] += 1

            # Check pagination
            try:
                next_link = page.locator(
                    "a:has-text('Next'), a[rel='next'], .pagination-next a, "
                    "a:has-text('›'), a[aria-label='Next']"
                )
                if not await next_link.first.is_visible(timeout=2000):
                    break
            except Exception:
                break

            page_num += 1
            await self.delay(page)

    async def _extract_product(self, element, category: str) -> dict:
        """Extract product data from a generic product card."""
        name = ""
        price = None
        sku = ""
        url = ""
        image = ""

        # Product name
        for sel in ["h2", "h3", "h4", ".product-title", ".product-name",
                     "a.product-title", ".product-card__title", "[data-product-title]"]:
            try:
                name = await element.locator(sel).first.inner_text(timeout=1500)
                if name.strip():
                    break
            except Exception:
                continue

        # Product link + SKU from URL
        try:
            link_el = element.locator("a").first
            href = await link_el.get_attribute("href", timeout=1500)
            if href:
                url = href if href.startswith("http") else f"{self.config['base_url']}{href}"
                # Try to extract product ID/SKU from URL
                for pattern in [r"/products?/([^/?#]+)", r"/(\d{4,})", r"id=(\d+)"]:
                    m = re.search(pattern, href)
                    if m:
                        sku = m.group(1)
                        break
        except Exception:
            pass

        # Price
        for sel in [".price", ".product-price", ".money", "[data-price]",
                     ".product-card__price", ".regular-price"]:
            try:
                price_text = await element.locator(sel).first.inner_text(timeout=1500)
                price = self._parse_price(price_text)
                if price:
                    break
            except Exception:
                continue

        # Image
        try:
            img = await element.locator("img").first.get_attribute("src", timeout=1500)
            if img:
                image = img if img.startswith("http") else f"{self.config['base_url']}{img}"
        except Exception:
            pass

        # Fallback SKU
        if not sku:
            try:
                sku = await element.get_attribute("data-product-id", timeout=1000) or ""
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
            "category": category,
            "unit_price": price,
            "product_url": url,
            "image_url": image,
            "in_stock": True,
        }

    @staticmethod
    def _parse_price(text: str):
        if not text:
            return None
        match = re.search(r"\$?([\d,]+\.?\d*)", text.replace("\n", " "))
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                return None
        return None


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="Generic vendor scraper")
    parser.add_argument("--vendor", "-v", required=True,
                        choices=list(VENDOR_CONFIGS.keys()),
                        help="Vendor slug to scrape")
    args = parser.parse_args()

    scraper = GenericScraper(args.vendor)
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
