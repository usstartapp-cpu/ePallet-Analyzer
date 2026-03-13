"""
Every Day Supply Co Scraper — Shopify Store
═══════════════════════════════════════════════════════════════════
Uses the shared ShopifyBaseScraper with Every Day Supply config.

Usage:
    python3 -m scrapers.everyday_supply
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.shopify_base import ShopifyBaseScraper


class EverydaySupplyScraper(ShopifyBaseScraper):
    VENDOR_SLUG = "everyday-supply"
    VENDOR_NAME = "Every Day Supply Co"
    STORE_URL = "https://everydaysupplyco.com"
    ENV_PREFIX = "EVERYDAYSUPPLY"
    BRAND_NAME = "Every Day Supply"


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = EverydaySupplyScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
