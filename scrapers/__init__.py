"""
Scrapers Package — Modular vendor scraper system for Scraper 4000
═══════════════════════════════════════════════════════════════════
Each vendor has its own file.  This __init__ provides a central
registry so the runner can discover and instantiate any scraper by slug.

Usage:
    from scrapers import get_scraper, get_all_scrapers, SCRAPER_REGISTRY

    scraper = get_scraper("costco")
    scraper.MAX_PRODUCTS = 100
    asyncio.run(scraper.run())
"""

from scrapers.base import BaseScraper

# ── Import all scraper classes ──────────────────────────────────

from scrapers.amazon import AmazonScraper
from scrapers.epallet import EPalletScraper
from scrapers.costco import CostcoScraper
from scrapers.webstaurant import WebstaurantScraper
from scrapers.usfoods import USFoodsScraper
from scrapers.walmart import WalmartScraper
from scrapers.faire import FaireScraper
from scrapers.hersheys import HersheysScraper
from scrapers.ghirardelli import GhirardelliScraper
from scrapers.barilla import BarillaScraper
from scrapers.alessi import AlessiScraper
from scrapers.vigo import VigoScraper
from scrapers.everyday_supply import EverydaySupplyScraper
from scrapers.delmonte import DelmonteScraper
from scrapers.mclane import McLaneScraper


# ── Registry: slug → scraper class ─────────────────────────────

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "amazon":           AmazonScraper,
    "epallet":          EPalletScraper,
    "costco":           CostcoScraper,
    "webstaurant":      WebstaurantScraper,
    "us-foods":         USFoodsScraper,
    "walmart":          WalmartScraper,
    "faire":            FaireScraper,
    "hersheys":         HersheysScraper,
    "ghirardelli":      GhirardelliScraper,
    "barilla":          BarillaScraper,
    "alessi":           AlessiScraper,
    "vigo":             VigoScraper,
    "everyday-supply":  EverydaySupplyScraper,
    "delmonte":         DelmonteScraper,
    "mclane":           McLaneScraper,
}

# Vendors that need special handling / can't be auto-scraped
SKIP_VENDORS: dict[str, str] = {
    "epallet":       "Already scraped (1,401 products in DB)",
    "us-foods":      "Requires OTP — run manually: python3 -m scrapers.usfoods",
    "ben-e-keith":   "No web portal (manual data entry only)",
    "dawn-foods":    "No web portal (manual data entry only)",
    "dot-foods":     "No web portal (manual data entry only)",
    "johnson-bros":  "Brochure-only WordPress site — no SKU/price data",
    "delmonte":      "Rebate dashboard only — no product catalog with SKU/price",
    "hersheys":      "Brand showcase site (hersheyland.com) — no e-commerce",
    "costco":        "Bot detection (Akamai) — cannot automate reliably",
}


def get_scraper(slug: str, max_products: int = 0) -> BaseScraper:
    """
    Instantiate a scraper by vendor slug.
    Set max_products > 0 to cap how many products are scraped.
    """
    cls = SCRAPER_REGISTRY.get(slug)
    if not cls:
        raise ValueError(
            f"No scraper for '{slug}'.  Available: {', '.join(SCRAPER_REGISTRY.keys())}"
        )
    scraper = cls()
    if max_products > 0:
        scraper.MAX_PRODUCTS = max_products
    return scraper


def get_all_scrapers(max_products: int = 0,
                     skip: set[str] | None = None) -> list[BaseScraper]:
    """
    Return a list of all scraper instances (optionally skipping some slugs).
    """
    skip = skip or set()
    scrapers = []
    for slug, cls in SCRAPER_REGISTRY.items():
        if slug in skip:
            continue
        s = cls()
        if max_products > 0:
            s.MAX_PRODUCTS = max_products
        scrapers.append(s)
    return scrapers


__all__ = [
    "BaseScraper",
    "SCRAPER_REGISTRY",
    "SKIP_VENDORS",
    "get_scraper",
    "get_all_scrapers",
]
