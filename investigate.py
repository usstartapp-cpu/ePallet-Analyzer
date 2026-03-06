"""
Step 2: Deep investigation of the product page structure after login.
This discovers categories, checks price visibility, and inspects the HTML structure
so we can fine-tune the scraper selectors.

Usage: python3 investigate.py
"""

import asyncio
import re
from playwright.async_api import async_playwright
from config import EMAIL, PASSWORD, BASE_URL, TIMEOUT, STORAGE_FILTER, ITEMS_PER_PAGE


async def investigate():
    print("=" * 60)
    print("ePallet Deep Investigation")
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

        # Dismiss cookies
        try:
            btn = page.locator("button:has-text('Accept All')")
            if await btn.is_visible(timeout=3000):
                await btn.click()
        except:
            pass

        # Click Sign In
        await page.locator("text=Sign In").first.click()
        await page.wait_for_timeout(2000)

        # Fill form
        await page.locator("input[placeholder*='email' i]").first.fill(EMAIL)
        await page.locator("input[type='password']").first.fill(PASSWORD)
        await page.locator("button[type='submit']").first.click()
        await page.wait_for_timeout(5000)
        print("    ✓ Login submitted")

        # ── Check product page ────────────────────────────────────
        print("\n[2] Loading product page...")
        url = f"{BASE_URL}/product-list/Food/Snacks?storage={STORAGE_FILTER}&page=1&size={ITEMS_PER_PAGE}"
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)  # Extra wait for JS rendering

        # Check for dollar signs in the page
        body = await page.text_content("body")
        dollar_matches = re.findall(r'\$[\d,]+\.\d{2}', body)
        print(f"    Dollar amounts found on page: {len(dollar_matches)}")
        if dollar_matches:
            print(f"    First few: {dollar_matches[:6]}")
        else:
            print("    ⚠️  No prices found! Let's check what we CAN see...")

        # ── Network monitoring: look for API calls ────────────────
        print("\n[3] Checking for API/XHR calls on page load...")
        api_responses = []

        async def capture_response(response):
            url = response.url
            if "api" in url.lower() or "graphql" in url.lower() or "product" in url.lower():
                api_responses.append({
                    "url": url,
                    "status": response.status,
                    "content_type": response.headers.get("content-type", ""),
                })

        page.on("response", capture_response)

        # Reload to capture network
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

        if api_responses:
            print(f"    Found {len(api_responses)} API-like responses:")
            for resp in api_responses[:15]:
                print(f"      [{resp['status']}] {resp['url'][:120]}")
        else:
            print("    No obvious API calls detected")

        # ── Investigate page structure ────────────────────────────
        print("\n[4] Investigating product card HTML structure...")

        # Get the first product card's HTML
        card_html = await page.evaluate("""
            () => {
                // Find product links
                const links = document.querySelectorAll('a[href*="/product/"]');
                if (links.length === 0) return 'NO PRODUCT LINKS FOUND';
                
                // Get parent container of first product
                let card = links[0];
                // Go up until we find a reasonable container
                for (let i = 0; i < 8; i++) {
                    if (card.parentElement) card = card.parentElement;
                }
                
                return {
                    cardHTML: card.innerHTML.substring(0, 3000),
                    cardClasses: card.className,
                    totalProductLinks: links.length,
                };
            }
        """)

        if isinstance(card_html, dict):
            print(f"    Product links on page: {card_html.get('totalProductLinks', 0)}")
            print(f"    Card container classes: {card_html.get('cardClasses', 'none')}")
            print(f"    Card HTML (first 1000 chars):")
            html_snippet = card_html.get("cardHTML", "")[:1000]
            print(f"    {html_snippet}")
        else:
            print(f"    {card_html}")

        # ── Check price-specific elements ─────────────────────────
        print("\n[5] Looking for price elements specifically...")

        price_info = await page.evaluate("""
            () => {
                const results = [];
                
                // Look for elements containing "Delivered price" or "$"
                const allElements = document.querySelectorAll('*');
                let priceElements = 0;
                let deliveredPriceTexts = [];
                
                for (let el of allElements) {
                    const text = el.textContent || '';
                    if (el.children.length === 0) {  // Leaf nodes only
                        if (text.includes('Delivered price')) {
                            deliveredPriceTexts.push({
                                tag: el.tagName,
                                class: el.className,
                                text: text.substring(0, 200),
                                parentText: el.parentElement?.textContent?.substring(0, 300) || '',
                            });
                        }
                        if (/^\\$[\\d,]+\\.\\d{2}$/.test(text.trim())) {
                            priceElements++;
                            if (results.length < 5) {
                                results.push({
                                    tag: el.tagName,
                                    class: el.className,
                                    text: text.trim(),
                                    parentClass: el.parentElement?.className || '',
                                });
                            }
                        }
                    }
                }
                
                return {
                    priceElements: priceElements,
                    samplePrices: results,
                    deliveredPriceLabels: deliveredPriceTexts.slice(0, 3),
                };
            }
        """)

        print(f"    Price elements (${{}}.00 format): {price_info.get('priceElements', 0)}")
        if price_info.get("samplePrices"):
            print("    Sample prices found:")
            for p in price_info["samplePrices"]:
                print(f"      <{p['tag']} class='{p['class']}'> {p['text']}")
        if price_info.get("deliveredPriceLabels"):
            print("    'Delivered price' label context:")
            for d in price_info["deliveredPriceLabels"]:
                print(f"      Parent text: {d['parentText'][:200]}")

        # ── Discover categories from navigation ──────────────────
        print("\n[6] Looking for categories in navigation...")

        # Try clicking on the hamburger/menu or looking at the navigation
        nav_categories = await page.evaluate("""
            () => {
                const cats = new Set();
                const links = document.querySelectorAll('a');
                for (let link of links) {
                    const href = link.getAttribute('href') || '';
                    // Match /product-list/Food/XXX or just category navigation
                    const match = href.match(/\\/product-list\\/(?:Food\\/)?([A-Za-z%0-9][A-Za-z%0-9\\s\\-&']+?)(?:\\?|$|\\/)/);
                    if (match && match[1] !== 'brands') {
                        cats.add(match[1]);
                    }
                    // Also look for category-style links
                    const match2 = href.match(/\\/([a-z-]+)$/);
                    if (match2) {
                        const name = match2[1];
                        if (['snacks', 'beverages', 'candy', 'breakfast', 'baking', 'pasta',
                             'canned', 'condiments', 'dried', 'baby', 'international',
                             'health', 'pet', 'household', 'dairy', 'frozen', 'meat',
                             'produce', 'seafood', 'deli'].includes(name)) {
                            cats.add(name);
                        }
                    }
                }
                return Array.from(cats);
            }
        """)

        print(f"    Categories from navigation: {nav_categories}")

        # ── Check pagination structure ────────────────────────────
        print("\n[7] Checking pagination...")
        pagination_info = await page.evaluate("""
            () => {
                // Look for pagination elements
                const pageNums = [];
                const allText = document.body.textContent;
                
                // Find elements that look like page numbers
                const btns = document.querySelectorAll('button, a');
                for (let btn of btns) {
                    const text = btn.textContent.trim();
                    if (/^\\d+$/.test(text) && parseInt(text) <= 1000) {
                        const num = parseInt(text);
                        if (num > 0) pageNums.push(num);
                    }
                }
                
                // Also look at aria labels
                const paginationEls = document.querySelectorAll('[aria-label*="page" i], [class*="pagination" i], [class*="Pagination" i]');
                const paginationInfo = [];
                for (let el of paginationEls) {
                    paginationInfo.push({
                        tag: el.tagName,
                        class: el.className,
                        ariaLabel: el.getAttribute('aria-label') || '',
                        text: el.textContent.substring(0, 200),
                    });
                }
                
                return {
                    pageNumbers: [...new Set(pageNums)].sort((a, b) => a - b),
                    paginationElements: paginationInfo,
                };
            }
        """)

        print(f"    Page numbers found: {pagination_info.get('pageNumbers', [])}")
        if pagination_info.get("paginationElements"):
            for pe in pagination_info["paginationElements"]:
                print(f"    Pagination element: <{pe['tag']} class='{pe['class']}'> text='{pe['text'][:100]}'")

        # ── Get full page URL to verify params work ───────────────
        print(f"\n[8] Current URL: {page.url}")

        # Try a second page
        url2 = f"{BASE_URL}/product-list/Food/Snacks?storage={STORAGE_FILTER}&page=2&size={ITEMS_PER_PAGE}"
        await page.goto(url2, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        body2 = await page.text_content("body")
        dollar_matches2 = re.findall(r'\$[\d,]+\.\d{2}', body2)
        print(f"    Page 2 — Dollar amounts: {len(dollar_matches2)}")

        # ── Test with the main navigation menu ────────────────────
        print("\n[9] Investigating main navigation for all categories...")
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Try hovering over navigation items to reveal dropdowns
        all_nav_links = await page.evaluate("""
            () => {
                const links = document.querySelectorAll('a, button');
                const items = [];
                for (let el of links) {
                    const text = el.textContent.trim();
                    const href = el.getAttribute('href') || '';
                    if (text && (
                        href.includes('product-list') ||
                        href.includes('/Food') ||
                        ['Snacks', 'Beverages', 'Candy', 'Breakfast', 'Baking',
                         'Canned', 'Condiments', 'Dried', 'Baby', 'International',
                         'Health', 'Pet', 'Household', 'Dairy', 'Frozen', 'Meat',
                         'Deli', 'Pantry', 'Grocery', 'Pasta', 'Organic',
                         'Seafood', 'Produce', 'Paper', 'Cleaning'].some(cat =>
                            text.includes(cat))
                    )) {
                        items.push({text: text.substring(0, 80), href: href});
                    }
                }
                return items;
            }
        """)

        if all_nav_links:
            print(f"    Found {len(all_nav_links)} navigation items:")
            for item in all_nav_links[:30]:
                print(f"      [{item['text'][:50]}] → {item['href'][:80]}")

        await page.screenshot(path="debug_investigation.png")
        print("\n    → Saved debug_investigation.png")

        print("\n" + "=" * 60)
        print("Investigation complete! Check output above to tune scraper.")
        print("=" * 60)
        await page.wait_for_timeout(5000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(investigate())
