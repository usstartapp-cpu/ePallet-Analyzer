"""
ePallet Scraper — Migrated to Scraper 4000 modular architecture
═══════════════════════════════════════════════════════════════════
Uses the internal product search API for maximum speed.
Logs in via browser, then makes direct API calls per category.

Usage:
    python3 -m scrapers.epallet
"""

import asyncio
import math
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


# ── Category lists from ePallet's API ──────────────────────────

FOOD_CATEGORIES = [
    "Pantry & Baking Supplies",
    "Canned Goods",
    "Ingredients",
    "Meal Kits",
    "Condiments, Dressings, Spreads",
    "Baby Food",
    "Meat & Seafood",
    "Natural Foods & Plant-Based",
    "Fruits & Vegetables",
    "Frozen",
    "Eggs & Dairy",
    "Snacks",
    "Ready to Eat Meals",
    "Industrial Ingredients",
    "Beverages & Beverage Mixes",
    "Prepared & Ethnic Foods",
    "Baked Goods & Desserts",
    "Spices, Seasonings, Sweeteners",
    "Bakery, Tortillas",
    "Grains & Cereals",
    "Legumes, Nuts & Seeds",
]

NONFOOD_CATEGORIES = [
    "Hand Sanitizers, Disinfectants & Wipes",
    "Disposable Serviceware",
    "Household",
    "Pet Products",
    "Medical Supplies",
    "Health & Beauty",
    "Baby Products",
    "Cleaning Supplies & Paper",
    "Janitorial Supplies",
    "PPE",
    "Pet Food",
    "Paper & Serviceware",
]

API_PAGE_SIZE = 200
POST_ZIP = "77477"


class EPalletScraper(BaseScraper):
    VENDOR_SLUG = "epallet"
    VENDOR_NAME = "ePallet"
    SCRAPE_METHOD = "api"

    async def login(self, page) -> bool:
        """Log in to ePallet via browser to establish session cookies."""
        email, password = self.get_credentials("EPALLET")

        await page.goto("https://epallet.com", wait_until="domcontentloaded")
        await self.delay(page, 2)

        # Dismiss cookie banner
        try:
            btn = page.locator("button:has-text('Accept All')")
            if await btn.is_visible(timeout=3000):
                await btn.click()
                self.log("  ✓ Cookie banner dismissed")
        except Exception:
            pass

        # Click Sign In
        self.log("  → Clicking Sign In...")
        await page.locator("text=Sign In").first.click()
        await self.delay(page, 2)

        # Fill credentials
        self.log(f"  → Entering credentials ({email})...")
        await page.locator("input[placeholder*='email' i]").first.fill(email)
        await page.locator("input[type='password']").first.fill(password)

        # Submit
        self.log("  → Submitting...")
        await page.locator("button[type='submit']").first.click()
        await self.delay(page, 5)

        # Verify
        status = await page.evaluate("""
            async () => {
                try {
                    const resp = await fetch('/api/ERP/customer/status');
                    return await resp.json();
                } catch(e) {
                    return { error: e.message };
                }
            }
        """)

        if status.get("is_authenticated") or status.get("is_customer"):
            self.log(f"  ✓ Authenticated as {status.get('contact_name', 'N/A')}")
            return True
        else:
            self.log(f"  ✗ Login verification failed: {status}")
            await page.screenshot(path="debug_epallet_login.png")
            return False

    async def scrape(self, page) -> None:
        """Scrape all ePallet categories via their internal API."""
        all_categories = (
            [(cat, True) for cat in FOOD_CATEGORIES] +
            [(cat, False) for cat in NONFOOD_CATEGORIES]
        )

        for i, (category, is_food) in enumerate(all_categories, 1):
            food_label = "Food" if is_food else "Non-Food"
            self.log(f"\n  [{i}/{len(all_categories)}] {category} ({food_label})")

            try:
                products = await self._fetch_category(page, category, is_food)

                if not products:
                    self.log(f"      ○ No DRY products — skipping")
                    continue

                # Save each product
                for prod in products:
                    self.save_product(self._normalize(prod, category, food_label))

                self.log(f"      ✅ {len(products)} products saved")

            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({
                    "category": category,
                    "error": str(e),
                })
                self.log(f"      ❌ Error: {e}")

            await self.delay(page, 1)

    async def _fetch_category(self, page, sub_category: str,
                               is_food: bool) -> list[dict]:
        """Fetch ALL products for a category using ePallet's internal API."""
        all_products = []
        current_page = 1

        while True:
            result = await page.evaluate(
                """
                async ([subCategory, isFood, pageNum, pageSize, postZip]) => {
                    try {
                        const resp = await fetch(
                            `/api/ERP/search/productSearch?page=${pageNum}&page_size=${pageSize}&post_zip=${postZip}&ordering=relevance`,
                            {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    category: null,
                                    sub_category: subCategory,
                                    brand: null,
                                    only_haggle_products: null,
                                    food: isFood,
                                    filters: { storage: ["DRY"] }
                                }),
                            }
                        );
                        const data = await resp.json();
                        return {
                            status: resp.status,
                            count: data.count,
                            results: data.results || [],
                            next: data.next,
                        };
                    } catch (err) {
                        return { error: err.message };
                    }
                }
                """,
                [sub_category, is_food, current_page, API_PAGE_SIZE, POST_ZIP]
            )

            if "error" in result:
                self.log(f"      ✗ API error page {current_page}: {result['error']}")
                break

            if result["status"] != 200:
                self.log(f"      ✗ HTTP {result['status']} on page {current_page}")
                break

            products = result.get("results", [])
            total_count = result.get("count", 0)

            if not products:
                break

            all_products.extend(products)
            total_pages = math.ceil(total_count / API_PAGE_SIZE) if total_count else 1
            self.log(f"      Page {current_page}/{total_pages} — "
                     f"{len(all_products)}/{total_count}")

            if not result.get("next") or len(all_products) >= total_count:
                break

            current_page += 1
            await self.delay(page, 1)

        return all_products

    def _normalize(self, raw: dict, category: str, food_label: str) -> dict:
        """Normalize ePallet API response into our standard product schema."""
        brand = raw.get("brand_name", "")
        # Remove '-EP' suffix from brand names
        brand = re.sub(r"-EP$", "", brand).strip() if brand else ""

        return {
            "sku": str(raw.get("id", "")),
            "upc": raw.get("upc", ""),
            "product_name": raw.get("name", ""),
            "brand": brand,
            "description": raw.get("description_short", ""),
            "category": category,
            "sub_category": raw.get("sub_category", ""),
            "unit_price": self._safe_num(raw.get("delivered_price")),
            "case_price": self._safe_num(raw.get("delivered_case_price")),
            "price_per_oz": self._safe_num(raw.get("per_oz_delivered_price")),
            "bulk_price": self._safe_num(raw.get("per_unit_delivered_price")),
            "pack_size_raw": raw.get("pack_size", ""),
            "cases_per_pallet": self._safe_int(raw.get("case_per_pallet")),
            "min_order_qty": self._safe_int(raw.get("min_pallet_quantity")),
            "lead_time_days": self._safe_int(raw.get("lead_time_days")),
            "in_stock": bool(raw.get("is_available", True)),
            "mixed_pallet": bool(raw.get("for_mixed_pallet", False)),
            "has_promo": bool(raw.get("has_promo", False)),
            "product_url": f"https://epallet.com/product/{raw.get('slug', '')}",
            "image_url": raw.get("image_url", ""),
        }

    @staticmethod
    def _safe_num(val):
        """Convert to float or None."""
        if val is None or val == "":
            return None
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val):
        """Convert to int or None."""
        if val is None or val == "":
            return None
        try:
            return int(float(str(val)))
        except (ValueError, TypeError):
            return None


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = EPalletScraper()
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
