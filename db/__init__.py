"""
Database package — Supabase client and operations for Scraper 4000
"""

from db.supabase_client import (
    get_client,
    get_vendor_by_slug,
    get_all_vendors,
    get_vendor_id,
    start_scrape_run,
    complete_scrape_run,
    fail_scrape_run,
    upsert_product,
    get_products,
    get_product_count,
    get_price_comparison,
    get_best_prices,
    get_vendor_stats,
    check_rate_limit,
)

__all__ = [
    "get_client",
    "get_vendor_by_slug",
    "get_all_vendors",
    "get_vendor_id",
    "start_scrape_run",
    "complete_scrape_run",
    "fail_scrape_run",
    "upsert_product",
    "get_products",
    "get_product_count",
    "get_price_comparison",
    "get_best_prices",
    "get_vendor_stats",
    "check_rate_limit",
]
