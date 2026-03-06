"""
Test calling the product search API directly from the browser context.
"""

import asyncio
import json
from playwright.async_api import async_playwright
from config import EMAIL, PASSWORD, BASE_URL, STORAGE_FILTER, ITEMS_PER_PAGE


async def test_api():
    print("=" * 60)
    print("Direct API Call Test")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        page.set_default_timeout(60000)

        # Login
        print("\n[1] Logging in...")
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
        print("    ✓ Logged in")

        # Try direct API call from browser context
        print("\n[2] Calling productSearch API directly...")
        
        result = await page.evaluate("""
            async () => {
                try {
                    const resp = await fetch('/api/ERP/search/productSearch?page=1&page_size=24&post_zip=77477&ordering=relevance', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({
                            category: null,
                            sub_category: "Snacks",
                            brand: null,
                            only_haggle_products: null,
                            food: true,
                            filters: {
                                storage: ["DRY"],
                            }
                        }),
                    });
                    const data = await resp.json();
                    return { status: resp.status, data: data };
                } catch (err) {
                    return { error: err.message };
                }
            }
        """)

        if "error" in result:
            print(f"    ✗ Error: {result['error']}")
        else:
            print(f"    ✓ Status: {result['status']}")
            data = result["data"]
            
            with open("product_api_response.json", "w") as f:
                json.dump(data, f, indent=2)
            print(f"    → Full response saved to product_api_response.json")
            
            print(f"    Top-level keys: {list(data.keys())}")
            
            if "count" in data:
                print(f"    Total products: {data['count']}")
            if "page" in data:
                print(f"    Current page: {data['page']}")
            if "page_size" in data:
                print(f"    Page size: {data['page_size']}")
            
            # Check for products
            for key in ["results", "products", "items", "data"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    print(f"\n    Products in '{key}': {len(items)} items")
                    if items:
                        print(f"    Product keys: {list(items[0].keys())}")
                        print(f"\n    === FIRST PRODUCT ===")
                        print(json.dumps(items[0], indent=2))
                    break

        # Now try a bigger page size to speed up scraping
        print("\n\n[3] Testing larger page sizes...")
        for size in [48, 96, 100, 200]:
            result = await page.evaluate(f"""
                async () => {{
                    try {{
                        const resp = await fetch('/api/ERP/search/productSearch?page=1&page_size={size}&post_zip=77477&ordering=relevance', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{
                                category: null,
                                sub_category: "Snacks",
                                brand: null,
                                only_haggle_products: null,
                                food: true,
                                filters: {{ storage: ["DRY"] }}
                            }}),
                        }});
                        const data = await resp.json();
                        const results = data.results || data.products || data.items || [];
                        return {{ status: resp.status, count: data.count, returned: results.length, page_size: {size} }};
                    }} catch (err) {{
                        return {{ error: err.message, page_size: {size} }};
                    }}
                }}
            """)
            print(f"    page_size={size}: returned={result.get('returned', '?')}, total={result.get('count', '?')}, status={result.get('status', '?')}")

        # Test getting ALL categories at once (no sub_category filter)
        print("\n\n[4] Testing search without category filter (all products)...")
        result = await page.evaluate("""
            async () => {
                try {
                    const resp = await fetch('/api/ERP/search/productSearch?page=1&page_size=24&post_zip=77477&ordering=relevance', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            category: null,
                            sub_category: null,
                            brand: null,
                            only_haggle_products: null,
                            food: true,
                            filters: { storage: ["DRY"] }
                        }),
                    });
                    const data = await resp.json();
                    return { status: resp.status, count: data.count, returned: (data.results || []).length };
                } catch (err) {
                    return { error: err.message };
                }
            }
        """)
        print(f"    All DRY food products: total={result.get('count', '?')}, returned={result.get('returned', '?')}")

        # Also try without food filter
        result2 = await page.evaluate("""
            async () => {
                try {
                    const resp = await fetch('/api/ERP/search/productSearch?page=1&page_size=24&post_zip=77477&ordering=relevance', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            category: null,
                            sub_category: null,
                            brand: null,
                            only_haggle_products: null,
                            food: null,
                            filters: { storage: ["DRY"] }
                        }),
                    });
                    const data = await resp.json();
                    return { status: resp.status, count: data.count, returned: (data.results || []).length };
                } catch (err) {
                    return { error: err.message };
                }
            }
        """)
        print(f"    All DRY products (any): total={result2.get('count', '?')}, returned={result2.get('returned', '?')}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(test_api())
