"""
Step 1: Test login to ePallet.com
Run this first to verify authentication works and see how the site behaves.
Usage: python3 test_login.py
"""

import asyncio
from playwright.async_api import async_playwright
from config import EMAIL, PASSWORD, BASE_URL, TIMEOUT


async def test_login():
    print("=" * 60)
    print("ePallet Login Test")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        page.set_default_timeout(TIMEOUT)

        # ── Step 1: Go to homepage ─────────────────────────────────
        print("\n[1] Navigating to ePallet homepage...")
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Dismiss cookie banner if present
        try:
            cookie_btn = page.locator("button:has-text('Accept All')")
            if await cookie_btn.is_visible(timeout=3000):
                await cookie_btn.click()
                print("    ✓ Cookie banner dismissed")
        except:
            print("    ○ No cookie banner found")

        # ── Step 2: Click Sign In button ──────────────────────────
        print("\n[2] Looking for Sign In button...")
        try:
            # Try multiple selectors for the sign-in button
            sign_in_selectors = [
                "text=Sign In",
                "button:has-text('Sign In')",
                "a:has-text('Sign In')",
                "[data-testid='sign-in']",
                ".sign-in-btn",
            ]
            clicked = False
            for selector in sign_in_selectors:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        print(f"    ✓ Clicked sign-in using: {selector}")
                        clicked = True
                        break
                except:
                    continue

            if not clicked:
                print("    ✗ Could not find Sign In button")
                # Take screenshot for debugging
                await page.screenshot(path="debug_homepage.png")
                print("    → Saved debug_homepage.png")
                await browser.close()
                return

        except Exception as e:
            print(f"    ✗ Error: {e}")
            await page.screenshot(path="debug_homepage.png")
            await browser.close()
            return

        await page.wait_for_timeout(2000)

        # ── Step 3: Fill login form ──────────────────────────────
        print("\n[3] Looking for login form...")
        await page.screenshot(path="debug_login_form.png")
        print("    → Saved debug_login_form.png (check what the form looks like)")

        # Try to find email/password fields
        try:
            # Common selectors for email field
            email_selectors = [
                "input[type='email']",
                "input[name='email']",
                "input[name='username']",
                "input[placeholder*='email' i]",
                "input[placeholder*='Email' i]",
                "#email",
                "#username",
            ]
            email_field = None
            for selector in email_selectors:
                try:
                    field = page.locator(selector).first
                    if await field.is_visible(timeout=1500):
                        email_field = field
                        print(f"    ✓ Found email field: {selector}")
                        break
                except:
                    continue

            if not email_field:
                # Maybe the Sign In opened a new page/modal — look at all inputs
                all_inputs = page.locator("input")
                count = await all_inputs.count()
                print(f"    → Found {count} input fields on page:")
                for i in range(count):
                    inp = all_inputs.nth(i)
                    inp_type = await inp.get_attribute("type") or "?"
                    inp_name = await inp.get_attribute("name") or "?"
                    inp_placeholder = await inp.get_attribute("placeholder") or "?"
                    inp_id = await inp.get_attribute("id") or "?"
                    print(f"      [{i}] type={inp_type}, name={inp_name}, placeholder={inp_placeholder}, id={inp_id}")

                await page.screenshot(path="debug_no_email_field.png")
                print("    → Saved debug_no_email_field.png")
                # Try the first text/email input
                if count > 0:
                    email_field = all_inputs.first
                    print("    → Using first input field as email")

            if email_field:
                await email_field.fill(EMAIL)
                print(f"    ✓ Entered email: {EMAIL}")

            # Password field
            pw_selectors = [
                "input[type='password']",
                "input[name='password']",
                "#password",
            ]
            pw_field = None
            for selector in pw_selectors:
                try:
                    field = page.locator(selector).first
                    if await field.is_visible(timeout=1500):
                        pw_field = field
                        print(f"    ✓ Found password field: {selector}")
                        break
                except:
                    continue

            if pw_field:
                await pw_field.fill(PASSWORD)
                print("    ✓ Entered password")

            await page.screenshot(path="debug_filled_form.png")

        except Exception as e:
            print(f"    ✗ Error filling form: {e}")
            await page.screenshot(path="debug_form_error.png")
            await browser.close()
            return

        # ── Step 4: Submit login ─────────────────────────────────
        print("\n[4] Submitting login...")
        try:
            submit_selectors = [
                "button[type='submit']",
                "button:has-text('Sign In')",
                "button:has-text('Log In')",
                "button:has-text('Login')",
                "input[type='submit']",
            ]
            for selector in submit_selectors:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        print(f"    ✓ Clicked submit: {selector}")
                        break
                except:
                    continue
        except Exception as e:
            print(f"    ✗ Error submitting: {e}")

        # Wait for navigation/login to complete
        await page.wait_for_timeout(5000)
        await page.screenshot(path="debug_after_login.png")
        print("    → Saved debug_after_login.png")

        # ── Step 5: Verify login ─────────────────────────────────
        print("\n[5] Verifying login status...")
        current_url = page.url
        print(f"    Current URL: {current_url}")

        # Check if we're logged in by looking for account-related elements
        page_text = await page.text_content("body")
        if "Sign Out" in page_text or "My Account" in page_text or "Log Out" in page_text:
            print("    ✓ LOGIN SUCCESSFUL!")
        elif "Sign In" in page_text:
            print("    ✗ Still seeing 'Sign In' — login may have failed")
            print("    → Check debug_after_login.png for error messages")
        else:
            print("    ? Uncertain login status — check screenshots")

        # ── Step 6: Test product page with prices ────────────────
        print("\n[6] Testing product page to check if prices are visible...")
        test_url = f"{BASE_URL}/product-list/Food/Snacks?storage=DRY&page=1&size=24"
        await page.goto(test_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await page.screenshot(path="debug_product_page.png")
        print("    → Saved debug_product_page.png")

        # Check if prices are visible
        body_text = await page.text_content("body")
        if "$" in body_text and "Delivered price" in body_text:
            print("    ✓ PRICES ARE VISIBLE! Authentication working for product pages.")
        else:
            print("    ? Prices may not be visible — check debug_product_page.png")

        # ── Step 7: Discover categories ──────────────────────────
        print("\n[7] Discovering available categories...")
        # Look at the navigation/sidebar for category links
        category_links = await page.locator("a[href*='/product-list/Food/']").all()
        categories = set()
        for link in category_links:
            href = await link.get_attribute("href")
            if href and "/product-list/Food/" in href:
                # Extract category name from URL
                parts = href.split("/product-list/Food/")
                if len(parts) > 1:
                    cat = parts[1].split("?")[0].split("/")[0]
                    if cat and cat != "Food":
                        categories.add(cat)

        if categories:
            print(f"    ✓ Found {len(categories)} categories:")
            for cat in sorted(categories):
                print(f"      • {cat}")
        else:
            print("    → No categories found on product page, will try navigation menu")

        # Keep browser open for 10s so user can see the result
        print("\n" + "=" * 60)
        print("Browser will stay open for 10 seconds so you can inspect...")
        print("Check the debug_*.png screenshots in the project folder.")
        print("=" * 60)
        await page.wait_for_timeout(10000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(test_login())
