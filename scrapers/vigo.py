"""
Vigo Foods Scraper — SabraMedia CMS Store
═══════════════════════════════════════════════════════════════════
Uses the shared SabraMediaBaseScraper with Vigo-specific config.
NOT a Shopify store — uses custom SabraMedia CMS with /catalog/ URLs.
(Same platform as Alessi Foods — sister brands.)

Usage:
    python3 -m scrapers.vigo
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.sabramedia_base import SabraMediaBaseScraper


class VigoScraper(SabraMediaBaseScraper):
    VENDOR_SLUG = "vigo"
    VENDOR_NAME = "Vigo Foods"
    STORE_URL = "https://vigofoods.com"
    ENV_PREFIX = "VIGO"
    BRAND_NAME = "Vigo"


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = VigoScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
