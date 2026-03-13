"""
Alessi Foods Scraper — SabraMedia CMS Store
═══════════════════════════════════════════════════════════════════
Uses the shared SabraMediaBaseScraper with Alessi-specific config.
NOT a Shopify store — uses custom SabraMedia CMS with /catalog/ URLs.

Usage:
    python3 -m scrapers.alessi
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.sabramedia_base import SabraMediaBaseScraper


class AlessiScraper(SabraMediaBaseScraper):
    VENDOR_SLUG = "alessi"
    VENDOR_NAME = "Alessi Foods"
    STORE_URL = "https://alessifoods.com"
    ENV_PREFIX = "ALESSI"
    BRAND_NAME = "Alessi"


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

async def main():
    scraper = AlessiScraper()
    scraper.MAX_PRODUCTS = 100
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
