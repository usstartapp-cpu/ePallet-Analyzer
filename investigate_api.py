"""
Investigate the ePallet API to see if we can scrape directly via API calls
instead of browser automation (much faster).
"""

import asyncio
import json
from playwright.async_api import async_playwright
from config import EMAIL, PASSWORD, BASE_URL, STORAGE_FILTER, ITEMS_PER_PAGE


async def investigate_api():
    print("=" * 60)
    print("ePallet API Investigation")
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

        # ── Login ─────────────────────────────────────────────────
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

        # ── Capture ALL API calls when loading a product page ─────
        print("\n[2] Capturing all API/fetch calls on product page load...")
        
        api_calls = []

        async def capture_request(request):
            url = request.url
            if any(x in url for x in ['api/', 'graphql', 'product', 'search', 'catalog', 'erp', 'ERP']):
                api_calls.append({
                    "method": request.method,
                    "url": url,
                    "headers": dict(request.headers),
                    "post_data": request.post_data,
                })

        api_responses_data = []

        async def capture_response(response):
            url = response.url
            if any(x in url.lower() for x in ['api/', 'graphql', 'product', 'search', 'catalog', 'erp']):
                try:
                    body = await response.text()
                    api_responses_data.append({
                        "url": url,
                        "status": response.status,
                        "content_type": response.headers.get("content-type", ""),
                        "body_preview": body[:2000] if body else "",
                    })
                except:
                    pass

        page.on("request", capture_request)
        page.on("response", capture_response)

        url = f"{BASE_URL}/product-list/Food/Snacks?storage={STORAGE_FILTER}&page=1&size={ITEMS_PER_PAGE}"
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(8000)

        print(f"\n    Captured {len(api_calls)} API requests:")
        for call in api_calls:
            print(f"\n    [{call['method']}] {call['url'][:150]}")
            if call['post_data']:
                print(f"      POST data: {call['post_data'][:500]}")
            # Check for auth headers
            for h in ['authorization', 'cookie', 'x-api-key', 'token']:
                if h in call['headers']:
                    val = call['headers'][h]
                    print(f"      {h}: {val[:100]}...")

        print(f"\n    Captured {len(api_responses_data)} API responses:")
        for resp in api_responses_data:
            print(f"\n    [{resp['status']}] {resp['url'][:150]}")
            print(f"      Content-Type: {resp['content_type']}")
            # Try to parse as JSON
            try:
                data = json.loads(resp['body_preview'])
                if isinstance(data, dict):
                    print(f"      JSON keys: {list(data.keys())[:10]}")
                    # Look for product data
                    for key in ['products', 'items', 'data', 'results', 'records']:
                        if key in data:
                            items = data[key]
                            if isinstance(items, list) and len(items) > 0:
                                print(f"      {key}: {len(items)} items")
                                print(f"      First item keys: {list(items[0].keys()) if isinstance(items[0], dict) else type(items[0])}")
                                # Print first item
                                print(f"      First item: {json.dumps(items[0], indent=2)[:800]}")
                            break
                    # Check for pagination info
                    for key in ['total', 'totalCount', 'count', 'pagination', 'meta', 'page', 'pages']:
                        if key in data:
                            print(f"      {key}: {data[key]}")
                elif isinstance(data, list):
                    print(f"      JSON array: {len(data)} items")
                    if data:
                        print(f"      First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else type(data[0])}")
            except:
                print(f"      Body: {resp['body_preview'][:300]}")

        # ── Also check what cookies/tokens we have ────────────────
        print("\n\n[3] Cookies and storage...")
        cookies = await context.cookies()
        for cookie in cookies:
            if any(x in cookie['name'].lower() for x in ['token', 'session', 'auth', 'jwt', 'sid']):
                print(f"    Cookie: {cookie['name']} = {cookie['value'][:80]}...")

        # Check localStorage
        local_storage = await page.evaluate("() => JSON.stringify(localStorage)")
        ls_data = json.loads(local_storage)
        print(f"\n    LocalStorage keys: {list(ls_data.keys())[:20]}")
        for key in ls_data:
            if any(x in key.lower() for x in ['token', 'auth', 'user', 'session']):
                val = ls_data[key]
                print(f"    LS [{key}]: {val[:200]}...")

        # ── Look for categories in the API/navigation ─────────────
        print("\n\n[4] Finding all categories via site navigation...")
        
        # Try to open the navigation menu and find all category links
        categories_data = await page.evaluate("""
            () => {
                const cats = [];
                const links = document.querySelectorAll('a[href*="/product-list/Food/"]');
                const seen = new Set();
                for (let link of links) {
                    const href = link.getAttribute('href');
                    if (href && !seen.has(href) && !href.includes('brands')) {
                        seen.add(href);
                        cats.push({
                            text: link.textContent.trim(),
                            href: href,
                        });
                    }
                }
                return cats;
            }
        """)
        
        print(f"    Category links found: {len(categories_data)}")
        for cat in categories_data:
            print(f"      [{cat['text'][:40]}] → {cat['href']}")

        # ── Try to find a menu/dropdown that shows all categories ─
        print("\n[5] Looking for navigation menu with all categories...")
        
        # Look for menu trigger
        menu_triggers = await page.locator("button:has-text('Categories'), button:has-text('Shop'), button:has-text('Menu'), [class*='menu'], [class*='nav'] button").all()
        print(f"    Found {len(menu_triggers)} potential menu triggers")
        
        for trigger in menu_triggers[:5]:
            text = await trigger.text_content()
            if text:
                print(f"      Menu item: '{text.strip()[:50]}'")
                try:
                    await trigger.click()
                    await page.wait_for_timeout(2000)
                    
                    # Check for new category links
                    new_cats = await page.evaluate("""
                        () => {
                            const cats = [];
                            const links = document.querySelectorAll('a[href*="/product-list/Food/"]');
                            const seen = new Set();
                            for (let link of links) {
                                const href = link.getAttribute('href');
                                if (href && !seen.has(href) && !href.includes('brands')) {
                                    seen.add(href);
                                    cats.push({
                                        text: link.textContent.trim(),
                                        href: href,
                                    });
                                }
                            }
                            return cats;
                        }
                    """)
                    
                    if len(new_cats) > len(categories_data):
                        print(f"      → Found {len(new_cats)} categories after click!")
                        for cat in new_cats:
                            print(f"        [{cat['text'][:40]}] → {cat['href']}")
                        categories_data = new_cats
                except:
                    pass

        print("\n" + "=" * 60)
        print("API Investigation complete!")
        print("=" * 60)
        await page.wait_for_timeout(5000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(investigate_api())
