"""
Capture the actual product search API response to understand the data format.
"""

import asyncio
import json
from playwright.async_api import async_playwright
from config import EMAIL, PASSWORD, BASE_URL, STORAGE_FILTER, ITEMS_PER_PAGE


async def capture_api():
    print("=" * 60)
    print("Capture Product Search API Response")
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

        # Capture product search response
        print("\n[2] Capturing productSearch API response...")

        product_api_response = None

        async def capture_product_response(response):
            nonlocal product_api_response
            if "productSearch" in response.url:
                try:
                    body = await response.text()
                    product_api_response = body
                    print(f"    ✓ Captured productSearch response ({len(body)} bytes)")
                except Exception as e:
                    print(f"    ✗ Error capturing: {e}")

        page.on("response", capture_product_response)

        # Load product page to trigger API call
        url = f"{BASE_URL}/product-list/Food/Snacks?storage={STORAGE_FILTER}&page=1&size={ITEMS_PER_PAGE}"
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(8000)

        if product_api_response:
            data = json.loads(product_api_response)
            
            # Save full response
            with open("api_response_sample.json", "w") as f:
                json.dump(data, f, indent=2)
            print(f"    → Full response saved to api_response_sample.json")

            # Analyze structure
            print(f"\n    Top-level keys: {list(data.keys())}")
            
            if "count" in data:
                print(f"    Total count: {data['count']}")
            if "page" in data:
                print(f"    Current page: {data['page']}")
            if "page_size" in data:
                print(f"    Page size: {data['page_size']}")
            
            # Find the products array
            for key in ["results", "products", "items", "data"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    print(f"\n    Products key: '{key}' ({len(items)} items)")
                    if items:
                        print(f"    First product keys: {list(items[0].keys())}")
                        print(f"\n    First product (full):")
                        print(json.dumps(items[0], indent=2))
                        if len(items) > 1:
                            print(f"\n    Second product (full):")
                            print(json.dumps(items[1], indent=2))
                    break

        else:
            print("    ✗ No productSearch response captured")

        # Also get the categories API
        print("\n\n[3] Fetching categories API directly...")
        categories_response = await page.evaluate("""
            async () => {
                const resp = await fetch('/api/ERP/common/categories/common-categories');
                return await resp.json();
            }
        """)
        
        with open("categories_response.json", "w") as f:
            json.dump(categories_response, f, indent=2)
        print(f"    → Saved to categories_response.json")
        print(f"    Keys: {list(categories_response.keys())}")
        
        for biz_type in categories_response:
            items = categories_response[biz_type]
            print(f"\n    {biz_type}:")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        print(f"      {item.get('name', '?')}: {item.get('child', [])}")
                    else:
                        print(f"      {item}")

        # Get cookies for potential direct API usage
        print("\n\n[4] Session info for direct API calls...")
        cookies = await context.cookies()
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        print(f"    Cookie header length: {len(cookie_header)} chars")
        
        # Key cookies
        for c in cookies:
            if c['name'] in ['sessionid', 'csrftoken', '__Host-next-auth.csrf-token']:
                print(f"    {c['name']}: {c['value'][:50]}...")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(capture_api())
