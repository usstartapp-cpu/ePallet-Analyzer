"""
Scraper 4000 — 100-Product Test Runner (All Vendors)
═══════════════════════════════════════════════════════════════════
Thin CLI runner that imports and runs each vendor's modular scraper.
Each scraper is in its own file under scrapers/ and uses BaseScraper.

Usage:
    python3 test_100.py costco              # Test one vendor
    python3 test_100.py webstaurant         # Test one vendor
    python3 test_100.py --all               # Test all vendors (skip ePallet + OTP)
    python3 test_100.py --list              # Show vendor status
"""

import asyncio
import argparse
import os
import sys
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from scrapers import (
    SCRAPER_REGISTRY,
    SKIP_VENDORS,
    get_scraper,
)
from db.supabase_client import supabase, get_vendor_by_slug

TARGET = 100  # products per vendor


# ═══════════════════════════════════════════════════════════════════
# RUN A SINGLE VENDOR
# ═══════════════════════════════════════════════════════════════════

async def run_vendor(slug: str) -> tuple[str, int, int, int]:
    """
    Run the modular scraper for a single vendor.
    Returns (slug, products_found, products_new, products_updated).
    """
    if slug in SKIP_VENDORS:
        print(f"[{slug}] ⏭  Skipping: {SKIP_VENDORS[slug]}")
        return slug, 0, 0, 0

    if slug not in SCRAPER_REGISTRY:
        print(f"[{slug}] ❌ No scraper found for '{slug}'")
        print(f"         Available: {', '.join(sorted(SCRAPER_REGISTRY.keys()))}")
        return slug, 0, 0, 0

    try:
        scraper = get_scraper(slug, max_products=TARGET)
        stats = await scraper.run(triggered_by="test_100")

        found = stats.get("products_found", 0)
        new   = stats.get("products_new", 0)
        updated = stats.get("products_updated", 0)
        return slug, found, new, updated

    except Exception as e:
        print(f"[{slug}] ❌ Fatal error: {e}")
        traceback.print_exc()
        return slug, 0, 0, 0


# ═══════════════════════════════════════════════════════════════════
# RUN ALL VENDORS
# ═══════════════════════════════════════════════════════════════════

async def run_all():
    """Run all vendor scrapers sequentially (skipping ePallet & OTP vendors)."""
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║     SCRAPER 4000 — 100-Product Test (All Vendors)          ║")
    print(f"║     Target: {TARGET} products per vendor                       ║")
    print(f"║     Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                        ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    results = []
    slugs_to_run = [s for s in SCRAPER_REGISTRY if s not in SKIP_VENDORS]

    for slug in slugs_to_run:
        try:
            result = await run_vendor(slug)
            results.append(result)
        except Exception as e:
            print(f"[{slug}] ❌ Fatal error: {e}")
            results.append((slug, 0, 0, 0))
        print()

    # ── Summary ──────────────────────────────────────────────────
    print("\n" + "═" * 70)
    print("📊  FINAL RESULTS")
    print("═" * 70)
    print(f"  {'Vendor':<25} {'Found':>10} {'New':>10} {'Updated':>10}")
    print("  " + "─" * 65)

    total_found = total_new = total_updated = 0
    for slug, found, new, updated in results:
        total_found += found
        total_new += new
        total_updated += updated
        icon = "✅" if found > 0 else "⚠️"
        print(f"  {icon} {slug:<23} {found:>10} {new:>10} {updated:>10}")

    for slug, reason in SKIP_VENDORS.items():
        print(f"  ⏭  {slug:<23} {'—':>10} {'—':>10} {'—':>10}  ({reason})")

    print("  " + "─" * 65)
    print(f"     {'TOTAL':<23} {total_found:>10} {total_new:>10} {total_updated:>10}")
    print("═" * 70)


# ═══════════════════════════════════════════════════════════════════
# STATUS LISTING
# ═══════════════════════════════════════════════════════════════════

def show_status():
    """Show current vendor status from Supabase."""
    print("\n  SCRAPER 4000 — Vendor Status")
    print("  " + "─" * 60 + "\n")

    vendors = supabase.table("vendors").select("id, name, slug").order("name").execute().data

    for v in vendors:
        cnt = supabase.table("products").select(
            "id", count="exact"
        ).eq("vendor_id", v["id"]).execute()
        count = cnt.count or 0

        if v["slug"] in SKIP_VENDORS:
            icon = "⏭"
            note = SKIP_VENDORS[v["slug"]]
        elif v["slug"] in SCRAPER_REGISTRY:
            icon = "✅" if count > 0 else "🔧"
            note = f"{count} products" if count > 0 else "Ready to scrape"
        else:
            icon = "❓"
            note = "No scraper registered"

        print(f"  {icon} {v['name']:<35} {count:>6} products  {note}")

    print()


# ═══════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Scraper 4000 — 100-Product Test Runner",
        epilog="Examples:\n"
               "  python3 test_100.py costco\n"
               "  python3 test_100.py --all\n"
               "  python3 test_100.py --list\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("vendor", nargs="?", help="Vendor slug to test")
    parser.add_argument("--all", action="store_true", help="Test all vendors")
    parser.add_argument("--list", action="store_true", help="Show vendor status")
    parser.add_argument(
        "--limit", type=int, default=100,
        help="Max products per vendor (default: 100)",
    )
    args = parser.parse_args()

    global TARGET
    TARGET = args.limit

    if args.list:
        show_status()
        return

    if args.all:
        asyncio.run(run_all())
        return

    if args.vendor:
        result = asyncio.run(run_vendor(args.vendor))
        slug, found, new, updated = result
        print(f"\n✅ Done: {slug} — {found} found, {new} new, {updated} updated")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
