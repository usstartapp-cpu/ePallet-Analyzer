"""
Johnson Bros. Bakery Supply Scraper — Shopify Store
═══════════════════════════════════════════════════════════════════
Uses the shared ShopifyBaseScraper with Johnson Bros config.

Usage:
    python3 -m scrapers.johnson_bros
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.shopify_base import ShopifyBaseScraper


class JohnsonBrosScraper(ShopifyBaseScraper):
    VENDOR_SLUG = "johnson-bros"
    VENDOR_NAME = "Johnson Bros. Bakery Supply"
    STORE_URL = "https://jbrosbakerysupply.com"
    ENV_PREFIX = "JOHNSONBROS"
    BRAND_NAME = "Johnson Bros"


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = JohnsonBrosScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
