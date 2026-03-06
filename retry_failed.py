"""
Retry failed/incomplete categories from the main scrape.
- Pantry & Baking Supplies: failed entirely (JSON parse error)
- Snacks: got 1,000 of 1,224 (page 6 failed)

This will retry with smaller page sizes and append to the existing data.
"""

import asyncio
import json
import math
import os
import re
import time
from datetime import datetime

import pandas as pd
from playwright.async_api import async_playwright

from config import EMAIL, PASSWORD, BASE_URL, TIMEOUT, HEADLESS, CHECKPOINT_CSV


POST_ZIP = "77477"


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

    status = await page.evaluate("""
        async () => {
            const resp = await fetch('/api/ERP/customer/status');
            return await resp.json();
        }
    """)
    print(f"  Login: {'✅' if status.get('is_authenticated') else '❌'}")
    return status.get("is_authenticated", False)


async def fetch_page(page, sub_category, is_food, page_num, page_size):
    """Fetch a single page from the API with retry."""
    for attempt in range(3):
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
                    if (!resp.ok) return { error: `HTTP ${resp.status}` };
                    const text = await resp.text();
                    try {
                        const data = JSON.parse(text);
                        return {
                            status: resp.status,
                            count: data.count,
                            results: data.results || [],
                            next: data.next,
                        };
                    } catch(e) {
                        return { error: `JSON parse error (${text.length} bytes): ${e.message}` };
                    }
                } catch (err) {
                    return { error: err.message };
                }
            }
            """,
            [sub_category, is_food, page_num, page_size, POST_ZIP]
        )
        
        if "error" not in result:
            return result
        
        print(f"      Attempt {attempt + 1} failed: {result['error']}")
        await page.wait_for_timeout(3000)
    
    return {"error": "All retries failed", "results": [], "count": 0}


async def retry_categories():
    print("=" * 60)
    print("🔄 RETRYING FAILED CATEGORIES")
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

        retry_items = []

        # ── Retry: Pantry & Baking Supplies ───────────────────────
        print("\n📦 Retrying: Pantry & Baking Supplies")
        # Use smaller page size (100 instead of 200)
        pantry_products = []
        page_num = 1
        total = None
        
        while True:
            result = await fetch_page(page, "Pantry & Baking Supplies", True, page_num, 100)
            
            if "error" in result and not result.get("results"):
                print(f"      ❌ Page {page_num} failed: {result['error']}")
                break
            
            products = result.get("results", [])
            if total is None:
                total = result.get("count", 0)
            
            if not products:
                break
            
            pantry_products.extend(products)
            total_pages = math.ceil(total / 100) if total else 1
            print(f"      Page {page_num}/{total_pages} — {len(products)} items (total: {len(pantry_products)}/{total})")
            
            if not result.get("next") or len(pantry_products) >= total:
                break
            
            page_num += 1
            await page.wait_for_timeout(1500)

        print(f"      ✅ Pantry & Baking Supplies: {len(pantry_products)} products")
        retry_items.extend(pantry_products)

        # ── Retry: Snacks (pages 6-7, items 1001-1224) ────────────
        print("\n📦 Retrying: Snacks (remaining pages)")
        snacks_missing = []

        # We got items 1-1000 (pages 1-5 at 200/page). Need pages 6-7.
        for page_num in [6, 7]:
            result = await fetch_page(page, "Snacks", True, page_num, 200)
            
            if "error" in result and not result.get("results"):
                # Try smaller page size
                print(f"      Page {page_num} failed at size 200, trying 100...")
                # Remap: page 6@200 = pages 11-12@100
                for sub_page in range((page_num - 1) * 2 + 1, page_num * 2 + 1):
                    result2 = await fetch_page(page, "Snacks", True, sub_page, 100)
                    if result2.get("results"):
                        snacks_missing.extend(result2["results"])
                        print(f"      Sub-page {sub_page}@100 — {len(result2['results'])} items")
                    await page.wait_for_timeout(1500)
            else:
                products = result.get("results", [])
                snacks_missing.extend(products)
                print(f"      Page {page_num} — {len(products)} items")
            
            await page.wait_for_timeout(1500)

        print(f"      ✅ Snacks remaining: {len(snacks_missing)} products")
        retry_items.extend(snacks_missing)

        await browser.close()

    # ── Merge with existing data ──────────────────────────────────
    print(f"\n📊 Merging {len(retry_items)} new items with existing data...")
    
    # Load existing checkpoint
    if os.path.exists(CHECKPOINT_CSV):
        existing_df = pd.read_csv(CHECKPOINT_CSV)
        print(f"  Existing: {len(existing_df)} products")
    else:
        existing_df = pd.DataFrame()
        print("  No existing data found")

    # Process retry items
    new_rows = []
    for prod in retry_items:
        brand = prod.get("brand_name", "")
        brand = re.sub(r"-EP$", "", brand).strip()
        
        category = "Pantry & Baking Supplies" if prod.get("sub_category", "").find("Pantry") >= 0 or any(
            "Pantry" in (prod.get("sub_category", "") or "")
            for _ in [1]
        ) else "Snacks"
        # Actually determine from the fetch context
        # For simplicity, check which sub_categories contain the item
        sub_cats = prod.get("sub_category", "")
        
        new_rows.append({
            "category": sub_cats.split(",")[0].strip() if sub_cats else "Unknown",
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
            "sub_category": sub_cats,
            "product_id": prod.get("id", ""),
            "product_url": f"https://epallet.com/product/{prod.get('slug', '')}",
        })

    new_df = pd.DataFrame(new_rows)
    
    # Combine and deduplicate by product_id
    if not existing_df.empty:
        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["product_id"], keep="last")
    else:
        combined = new_df

    # Save updated checkpoint
    combined.to_csv(CHECKPOINT_CSV, index=False)
    print(f"  Combined: {len(combined)} products (deduplicated)")
    print(f"  Saved to: {CHECKPOINT_CSV}")
    print(f"\n  To regenerate Excel, run: python3 rebuild_excel.py")


if __name__ == "__main__":
    asyncio.run(retry_categories())
