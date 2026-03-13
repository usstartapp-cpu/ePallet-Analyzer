"""
Scraper 4000 — Multi-Vendor Scrape Runner
══════════════════════════════════════════════════════════════
Run any vendor scraper from the command line.

Usage:
    python3 run_scraper.py epallet              # Run ePallet scraper
    python3 run_scraper.py costco               # Run Costco scraper
    python3 run_scraper.py faire                # Run Faire (via generic)
    python3 run_scraper.py --all                # Run all enabled scrapers
    python3 run_scraper.py --list               # List all vendors
"""

import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db.supabase_client import get_all_vendors, get_vendor_stats

# ── Vendor → Scraper mapping ──────────────────────────────────────

# Vendors with dedicated scrapers
DEDICATED_SCRAPERS = {
    "epallet": "scrapers.epallet",
    "costco": "scrapers.costco",
    "webstaurant": "scrapers.webstaurant",
    "us-foods": "scrapers.usfoods",
}

# Vendors handled by the generic scraper
GENERIC_VENDORS = [
    "faire", "walmart", "mclane", "hersheys", "ghirardelli",
    "barilla", "alessi", "vigo", "delmonte", "johnson-bros",
    "everyday-supply",
]

# Manual-only vendors (no scraper)
MANUAL_VENDORS = ["ben-e-keith", "dawn-foods", "dot-foods"]


async def run_vendor(slug: str) -> dict:
    """Run the appropriate scraper for a vendor slug."""
    print(f"\n{'─' * 60}")
    print(f"  Starting scraper for: {slug}")
    print(f"{'─' * 60}\n")

    if slug in DEDICATED_SCRAPERS:
        module = __import__(DEDICATED_SCRAPERS[slug], fromlist=["main"])
        # Each dedicated scraper has a specific class
        if slug == "epallet":
            from scrapers.epallet import EPalletScraper
            scraper = EPalletScraper()
        elif slug == "costco":
            from scrapers.costco import CostcoScraper
            scraper = CostcoScraper()
        elif slug == "webstaurant":
            from scrapers.webstaurant import WebstaurantScraper
            scraper = WebstaurantScraper()
        elif slug == "us-foods":
            from scrapers.usfoods import USFoodsScraper
            scraper = USFoodsScraper()
        return await scraper.run()

    elif slug in GENERIC_VENDORS:
        from scrapers.generic import GenericScraper
        scraper = GenericScraper(slug)
        return await scraper.run()

    elif slug in MANUAL_VENDORS:
        print(f"  ⚠ {slug} is a manual-only vendor (no web portal).")
        print(f"    Data must be entered manually or via CSV upload.")
        return {"skipped": True}

    else:
        print(f"  ❌ Unknown vendor: {slug}")
        return {"error": f"Unknown vendor: {slug}"}


async def run_all():
    """Run all enabled vendor scrapers sequentially."""
    vendors = get_all_vendors(enabled_only=True)
    results = {}

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║           SCRAPER 4000 — Full Multi-Vendor Run             ║")
    print(f"║           {len(vendors)} vendors enabled                            ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    for v in vendors:
        slug = v["slug"]
        if slug in MANUAL_VENDORS:
            print(f"\n  ⏭ Skipping {v['name']} (manual only)")
            continue

        try:
            stats = await run_vendor(slug)
            results[slug] = stats
        except Exception as e:
            print(f"\n  ❌ {v['name']} failed: {e}")
            results[slug] = {"error": str(e)}

    # Summary
    print("\n" + "═" * 60)
    print("📊 SCRAPE SUMMARY")
    print("═" * 60)
    for slug, stats in results.items():
        if stats.get("skipped"):
            print(f"  ⏭ {slug}: skipped (manual)")
        elif stats.get("error"):
            print(f"  ❌ {slug}: ERROR — {stats['error']}")
        else:
            found = stats.get("products_found", 0)
            new = stats.get("products_new", 0)
            changes = stats.get("price_changes", 0)
            errors = stats.get("errors", 0)
            print(f"  ✅ {slug}: {found} found, {new} new, {changes} price changes, {errors} errors")
    print("═" * 60)

    return results


def list_vendors():
    """Print all vendors and their scraper status."""
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║           SCRAPER 4000 — Vendor Registry                   ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    try:
        stats = get_vendor_stats()
    except Exception:
        stats = []

    if stats:
        for s in stats:
            v = s["vendor"]
            slug = v["slug"]
            count = s["product_count"]
            last = s["last_run"]

            # Determine scraper type
            if slug in DEDICATED_SCRAPERS:
                scraper_type = "🔧 Dedicated"
            elif slug in GENERIC_VENDORS:
                scraper_type = "🔄 Generic"
            elif slug in MANUAL_VENDORS:
                scraper_type = "📋 Manual"
            else:
                scraper_type = "❓ Unknown"

            enabled = "✅" if v["scrape_enabled"] else "❌"
            last_scrape = ""
            if last:
                last_scrape = f" | Last: {last['started_at'][:16]} ({last['status']})"

            print(f"  {enabled} {v['name']:<30} {scraper_type:<15} "
                  f"| {count:>5} products{last_scrape}")
    else:
        # Fallback if DB not reachable
        vendors = get_all_vendors()
        for v in vendors:
            print(f"  {'✅' if v['scrape_enabled'] else '❌'} {v['name']:<30} [{v['slug']}]")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Scraper 4000 — Multi-Vendor Scrape Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 run_scraper.py epallet          Run ePallet scraper
  python3 run_scraper.py costco           Run Costco scraper
  python3 run_scraper.py faire            Run Faire (generic scraper)
  python3 run_scraper.py --all            Run all enabled vendor scrapers
  python3 run_scraper.py --list           List all vendors and status
        """
    )
    parser.add_argument("vendor", nargs="?", help="Vendor slug to scrape")
    parser.add_argument("--all", action="store_true", help="Run all enabled scrapers")
    parser.add_argument("--list", action="store_true", help="List all vendors")

    args = parser.parse_args()

    if args.list:
        list_vendors()
        return

    if args.all:
        asyncio.run(run_all())
        return

    if args.vendor:
        asyncio.run(run_vendor(args.vendor))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
