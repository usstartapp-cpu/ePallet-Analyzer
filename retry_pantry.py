"""
Retry Pantry & Baking Supplies with smaller page sizes to avoid 504 timeouts.
This is the largest category (2,473 items) and causes server timeouts at size 100+.
"""

import asyncio
import json
import math
import os
import re
import time

import pandas as pd
from playwright.async_api import async_playwright

from config import EMAIL, PASSWORD, BASE_URL, HEADLESS

POST_ZIP = "77477"
PAGE_SIZE = 50  # Small to avoid 504s


async def login(page):
    await page.goto(BASE_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    try:
        await page.locator("button:has-text('Accept All')").click(timeout=3000)
    except:
        pass
    await page.locator("text=Sign In").first.click()
    await page.wait_for_timeout(2000)
    await page.locator("input[placeholder*='email' i]").first.fill(EMAIL)
    await page.locator("input[type='password']").first.fill(PASSWORD)
    await page.locator("button[type='submit']").first.click()
    await page.wait_for_timeout(5000)
    status = await page.evaluate("async () => { const r = await fetch('/api/ERP/customer/status'); return await r.json(); }")
    print(f"  Login: {'✅' if status.get('is_authenticated') else '❌'}")
    return status.get("is_authenticated", False)


async def fetch_page(page, sub_category, page_num, page_size):
    for attempt in range(3):
        result = await page.evaluate(
            """
            async ([subCat, pageNum, pageSize, postZip]) => {
                try {
                    const resp = await fetch(
                        `/api/ERP/search/productSearch?page=${pageNum}&page_size=${pageSize}&post_zip=${postZip}&ordering=relevance`,
                        {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                category: null,
                                sub_category: subCat,
                                brand: null,
                                only_haggle_products: null,
                                food: true,
                                filters: { storage: ["DRY"] }
                            }),
                        }
                    );
                    const text = await resp.text();
                    if (!resp.ok) return { error: `HTTP ${resp.status}`, statusCode: resp.status };
                    try {
                        const data = JSON.parse(text);
                        return { count: data.count, results: data.results || [], next: data.next };
                    } catch(e) {
                        return { error: `JSON parse: ${e.message}` };
                    }
                } catch (err) {
                    return { error: err.message };
                }
            }
            """,
            [sub_category, page_num, page_size, POST_ZIP]
        )

        if "error" not in result:
            return result

        print(f"      ⚠️ Attempt {attempt + 1}: {result.get('error', '?')}")
        # If 504, wait longer
        wait = 5000 if result.get("statusCode") == 504 else 3000
        await page.wait_for_timeout(wait)

    return {"error": "All retries failed", "results": [], "count": 0}


async def scrape_pantry():
    print("=" * 60)
    print("🔄 SCRAPING: Pantry & Baking Supplies")
    print(f"   Page size: {PAGE_SIZE} (to avoid 504 timeouts)")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        page.set_default_timeout(60000)

        await login(page)

        all_products = []
        page_num = 1
        total = None
        consecutive_fails = 0

        while consecutive_fails < 5:
            result = await fetch_page(page, "Pantry & Baking Supplies", page_num, PAGE_SIZE)

            if "error" in result and not result.get("results"):
                print(f"      ❌ Page {page_num} failed: {result['error']}")
                consecutive_fails += 1
                
                # Try re-login if too many fails
                if consecutive_fails == 3:
                    print("      🔄 Re-authenticating...")
                    await login(page)
                
                page_num += 1  # Skip this page and try next
                await page.wait_for_timeout(5000)
                continue

            consecutive_fails = 0
            products = result.get("results", [])

            if total is None:
                total = result.get("count", 0)

            if not products:
                break

            all_products.extend(products)
            total_pages = math.ceil(total / PAGE_SIZE) if total else 1
            print(f"      Page {page_num}/{total_pages} — {len(products)} items ({len(all_products)}/{total})")

            if not result.get("next") or len(all_products) >= total:
                break

            page_num += 1
            await page.wait_for_timeout(2000)

        print(f"\n  ✅ Got {len(all_products)} / {total or '?'} Pantry & Baking products")

        await browser.close()

    # Save and merge
    if all_products:
        new_rows = []
        for prod in all_products:
            brand = re.sub(r"-EP$", "", prod.get("brand_name", "")).strip()
            new_rows.append({
                "category": "Pantry & Baking Supplies",
                "food_type": "Food",
                "manufacturer": brand,
                "product": prod.get("name", ""),
                "description": prod.get("description_short", ""),
                "upc": prod.get("upc", ""),
                "delivered_price": prod.get("delivered_price", ""),
                "delivered_case_price": prod.get("delivered_case_price", ""),
                "price_per_unit": prod.get("per_unit_delivered_price", ""),
                "price_per_oz": prod.get("per_oz_delivered_price", ""),
                "pack_size_raw": prod.get("pack_size", ""),
                "cases_per_pallet": prod.get("case_per_pallet", ""),
                "lead_time_days": prod.get("lead_time_days", ""),
                "min_pallet_qty": prod.get("min_pallet_quantity", ""),
                "mixed_pallet": "Yes" if prod.get("for_mixed_pallet") else "No",
                "available": "Yes" if prod.get("is_available") else "No",
                "has_promo": "Yes" if prod.get("has_promo") else "No",
                "main_category": prod.get("main_category", ""),
                "sub_category": prod.get("sub_category", ""),
                "product_id": prod.get("id", ""),
                "product_url": f"https://epallet.com/product/{prod.get('slug', '')}",
            })

        new_df = pd.DataFrame(new_rows)

        # Merge with existing
        if os.path.exists("epallet_checkpoint.csv"):
            existing = pd.read_csv("epallet_checkpoint.csv")
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["product_id"], keep="last")
        else:
            combined = new_df

        combined.to_csv("epallet_checkpoint.csv", index=False)
        print(f"  📄 Saved to epallet_checkpoint.csv ({len(combined)} total products)")


if __name__ == "__main__":
    asyncio.run(scrape_pantry())
