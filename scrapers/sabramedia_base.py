"""
SabraMedia Base Scraper — Shared logic for SabraMedia CMS vendor stores
═══════════════════════════════════════════════════════════════════
Alessi Foods and Vigo Foods both use the SabraMedia CMS with
JavaScript-rendered product arrays loaded via AJAX (voCnd jQuery plugin).

The catalog page at /catalog loads products dynamically via:
  POST /action/public/cmc/cnd  (with CSRF token from session)

Products render into  div.content.product-array > ul > li  elements,
each with data-product-id attributes. The conduit supports pagination
and category filtering.

This base class handles:
  • Logging in via the /login form
  • Navigating to /catalog and waiting for AJAX product rendering
  • Scraping products from the JS-rendered DOM
  • Clicking through categories via the sidebar nav
  • Handling pagination (perpetual scroll / "load more")

Subclasses only need to set class-level constants.
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scrapers.base import BaseScraper


class SabraMediaBaseScraper(BaseScraper):
    """Base class for SabraMedia CMS-powered vendor stores (Alessi, Vigo)."""

    STORE_URL: str = ""        # Override: "https://alessifoods.com"
    ENV_PREFIX: str = ""       # Override: "ALESSI"
    BRAND_NAME: str = ""       # Override: "Alessi"
    SCRAPE_METHOD = "playwright_sabramedia"

    async def login(self, page) -> bool:
        """Log in via the SabraMedia /login form."""
        email, password = self.get_credentials(self.ENV_PREFIX)

        if not email or not password:
            self.log(f"  ⚠ No credentials for {self.ENV_PREFIX}, proceeding unauthenticated")
            await page.goto(self.STORE_URL, wait_until="domcontentloaded")
            return True

        login_url = f"{self.STORE_URL}/login"
        self.log(f"  → Navigating to {login_url}")
        await page.goto(login_url, wait_until="domcontentloaded")
        await self.delay(page, 2)

        # Take a debug screenshot to see what we get
        await page.screenshot(path=f"debug_{self.VENDOR_SLUG}_login_page.png")

        self.log(f"  → Entering credentials ({email})...")
        try:
            # SabraMedia login forms — try various selectors
            email_input = page.locator(
                "input[name='email'], input#email, input[type='email'], "
                "input[name='login_email'], input[name='username']"
            ).first
            await email_input.fill(email, timeout=5000)

            pwd_input = page.locator(
                "input[name='password'], input#password, input[type='password'], "
                "input[name='login_password']"
            ).first
            await pwd_input.fill(password, timeout=5000)

            self.log("  → Submitting login form...")
            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Log In'), button:has-text('Login'), "
                "a.button:has-text('Login'), button:has-text('Sign In')"
            ).first
            await submit.click(timeout=5000)
            await self.delay(page, 4)
        except Exception as e:
            self.log(f"  ⚠ Login form issue: {e}")
            await page.screenshot(path=f"debug_{self.VENDOR_SLUG}_login_error.png")

        # Check login result
        current_url = page.url
        body_class = await page.evaluate("document.body.className") or ""
        self.log(f"  → Post-login URL: {current_url}")
        self.log(f"  → Body classes: {body_class[:100]}")

        if "logged-in" in body_class or "login" not in current_url:
            self.log("  ✅ Login appears successful")
            return True

        self.log("  ⚠ Login may not have worked, continuing anyway (catalog is public)")
        await page.screenshot(path=f"debug_{self.VENDOR_SLUG}_login_result.png")
        return True  # Catalog is publicly browsable

    async def scrape(self, page) -> None:
        """
        Scrape the SabraMedia catalog.

        Strategy: Navigate to /catalog, wait for the voCnd AJAX plugin to
        render products, then extract from the rendered DOM. Walk through
        categories via the sidebar nav to get all products.
        """
        # Step 1: Discover categories from the navigation menu
        categories = await self._discover_categories(page)

        if not categories:
            self.log("  ⚠ No categories found, scraping /catalog directly")
            categories = [(f"{self.STORE_URL}/catalog", "All Products")]

        self.log(f"  📋 Found {len(categories)} categories to scrape")
        for url, name in categories:
            self.log(f"      • {name}: {url}")

        # Step 2: Scrape each category
        seen_skus = set()
        for cat_url, cat_name in categories:
            if self.has_reached_limit():
                break
            self.log(f"\n  📂 Category: {cat_name}")
            try:
                count = await self._scrape_category(page, cat_url, cat_name, seen_skus)
                self.log(f"      ✅ Got {count} products from {cat_name}")
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["error_log"].append({"category": cat_name, "error": str(e)})
                self.log(f"      ❌ Error scraping {cat_name}: {e}")

    async def _discover_categories(self, page) -> list[tuple[str, str]]:
        """Discover category URLs from the catalog page navigation."""
        self.log("  🔍 Discovering categories...")
        await page.goto(f"{self.STORE_URL}/catalog", wait_until="domcontentloaded")
        await self.delay(page, 3)

        categories = []
        seen = set()

        # SabraMedia puts category links in the nav menu and sidebar
        links = await page.locator("a[href*='/catalog/category/']").all()

        for link in links:
            try:
                href = await link.get_attribute("href", timeout=2000)
                text = (await link.inner_text(timeout=2000)).strip()
                if not href or not text or len(text) < 2:
                    continue

                # Normalize the href
                slug = href.rstrip("/").split("/")[-1]
                if slug in seen:
                    continue
                seen.add(slug)

                full_url = href if href.startswith("http") else f"{self.STORE_URL}{href}"
                categories.append((full_url, text))
            except Exception:
                continue

        return categories

    async def _scrape_category(self, page, url: str, category: str,
                               seen_skus: set) -> int:
        """
        Scrape a single category page. The voCnd plugin loads products via
        AJAX and renders them as <li> elements inside div.product-array.
        We need to wait for the AJAX to complete and the DOM to populate.
        """
        count = 0

        # Navigate to the category
        await page.goto(url, wait_until="domcontentloaded")
        await self.delay(page, 1.5)

        # Wait for the product array container to exist and populate
        try:
            await page.wait_for_selector(
                "div.product-array li, div.content.product-array li",
                timeout=12000
            )
            self.log(f"      ✓ Product array loaded")
        except Exception:
            self.log(f"      ⚠ Product array empty or slow — waiting longer...")
            await self.delay(page, 5)
            # Try again
            try:
                await page.wait_for_selector(
                    "div.product-array li",
                    timeout=8000
                )
            except Exception:
                self.log(f"      ⚠ No products found in {category}")
                await page.screenshot(path=f"debug_{self.VENDOR_SLUG}_{category[:20]}.png")
                return 0

        # Scrape all products currently rendered
        count += await self._extract_visible_products(page, category, seen_skus)

        # Only attempt pagination if the page actually has pagination controls.
        # Most SabraMedia categories are small (< 10 items) with no pagination,
        # so we skip the slow scroll loop entirely for those.
        has_pagination = False
        try:
            has_pagination = await page.locator(
                ".cnd-pagination, a.next, button:has-text('Load More')"
            ).first.is_visible(timeout=2000)
        except Exception:
            pass

        if has_pagination and not self.has_reached_limit():
            max_scroll_attempts = 5
            for attempt in range(max_scroll_attempts):
                if self.has_reached_limit():
                    break

                prev_count = len(seen_skus)

                # Scroll to bottom to trigger lazy loading
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await self.delay(page, 1.5)
                except Exception:
                    break  # Browser/page was closed

                # Check for a "load more" or "next" button
                try:
                    next_btn = page.locator(
                        ".cnd-pagination a.next, "
                        ".cnd-pagination .next a, "
                        "a:has-text('Next'), "
                        "button:has-text('Load More'), "
                        "a:has-text('Load More')"
                    ).first
                    if await next_btn.is_visible(timeout=1500):
                        self.log(f"      → Clicking next/load-more (attempt {attempt+1})")
                        await next_btn.click(timeout=3000)
                        await self.delay(page, 2)
                except Exception:
                    pass

                # Extract any new products
                try:
                    new_count = await self._extract_visible_products(page, category, seen_skus)
                    count += new_count
                except Exception:
                    break

                # If no new products appeared, we've exhausted this category
                if len(seen_skus) == prev_count:
                    break

        return count

    async def _extract_visible_products(self, page, category: str,
                                         seen_skus: set) -> int:
        """Extract all visible products from the current page state."""
        count = 0

        # The SabraMedia CMS renders products as li elements inside the
        # product-array div. Each li has data-product-id.
        items = await page.locator(
            "div.product-array li[data-product-id], "
            "div.content.product-array li[data-product-id]"
        ).all()

        if not items:
            # Broader fallback — any li inside product-array
            items = await page.locator(
                "div.product-array ul > li, "
                "div.content.product-array ul > li, "
                "div.product-array > ul > li"
            ).all()

        if not items:
            # Even broader — use JS to enumerate
            items = await page.locator("div.product-array li").all()

        self.log(f"      Found {len(items)} DOM items in product array")

        for item in items:
            if self.has_reached_limit():
                break
            try:
                data = await self._extract_product(item, category)
                if not data or not data.get("sku"):
                    continue

                sku = data["sku"]
                if sku in seen_skus:
                    continue
                seen_skus.add(sku)

                self.save_product(data)
                count += 1

                if count % 10 == 0:
                    self.log(f"      📦 {self.stats['products_found']} products saved so far...")

            except Exception as e:
                self.stats["errors"] += 1

        return count

    async def _extract_product(self, element, category: str) -> dict:
        """Extract product data from a SabraMedia product list item."""

        # ── data-product-id (most reliable SKU source) ──────────
        sku = ""
        try:
            product_id = await element.get_attribute("data-product-id", timeout=1000)
            if product_id:
                sku = str(product_id).strip()
        except Exception:
            pass

        # ── Product name ────────────────────────────────────────
        # SabraMedia renders: <li> ... <a class="link">Name</a> ...
        # or: <span class="name">Name</span> or similar
        name = ""
        for sel in [".name", "a.link", "h3 a", "h3", "h2 a", "h2",
                    "a.product-name", "span.item-name", "a.name", "a"]:
            try:
                loc = element.locator(sel).first
                if await loc.count() > 0:
                    text = (await loc.inner_text(timeout=1500)).strip()
                    if text and len(text) > 2:
                        name = text
                        break
            except Exception:
                continue

        # Clean up: strip embedded price text like "\n$2.86" from the name
        if name:
            name = re.split(r'\n\s*\$', name)[0].strip()
            name = re.sub(r'\s*\$[\d,.]+\s*$', '', name).strip()

        # ── Price ───────────────────────────────────────────────
        # SabraMedia shows price in various spots
        price = None
        for sel in [".price", "span.price", "span.amount", ".product-price",
                    "span.money", "div.price"]:
            try:
                loc = element.locator(sel).first
                if await loc.count() > 0:
                    price_text = (await loc.inner_text(timeout=1500)).strip()
                    price = self.parse_price(price_text)
                    if price:
                        break
            except Exception:
                continue

        # ── Product URL ─────────────────────────────────────────
        url = ""
        try:
            link_el = element.locator("a[href*='/catalog/']").first
            if await link_el.count() > 0:
                link = await link_el.get_attribute("href", timeout=1500)
            else:
                link_el = element.locator("a").first
                link = await link_el.get_attribute("href", timeout=1500) if await link_el.count() > 0 else None

            if link:
                url = link if link.startswith("http") else f"{self.STORE_URL}{link}"
                # Extract product ID from URL as fallback SKU
                if not sku:
                    m = re.search(r"/catalog/(?:product|package)/(\d+)/", link)
                    if m:
                        sku = m.group(1)
                    else:
                        m = re.search(r"/catalog/(?:product|package)/([^/?#]+)", link.rstrip("/"))
                        if m:
                            sku = m.group(1)
        except Exception:
            pass

        # ── Image URL ───────────────────────────────────────────
        image_url = ""
        try:
            img = element.locator("img").first
            if await img.count() > 0:
                img_src = await img.get_attribute("src", timeout=1500)
                if img_src:
                    image_url = img_src if img_src.startswith("http") else f"{self.STORE_URL}{img_src}"
        except Exception:
            pass

        # ── Description / size info ─────────────────────────────
        description = ""
        for sel in [".description", "span.size", "span.weight", "p.details",
                    "span.subtitle", ".detail"]:
            try:
                loc = element.locator(sel).first
                if await loc.count() > 0:
                    desc = (await loc.inner_text(timeout=1000)).strip()
                    if desc:
                        description = desc
                        break
            except Exception:
                continue

        # ── Generate SKU from name if all else fails ────────────
        if not sku and name:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())[:50]
            sku = f"{self.VENDOR_SLUG}-{slug}"

        # Skip empty/invalid items
        if not name or len(name.strip()) < 2:
            return {}

        return {
            "sku": sku,
            "product_name": name.strip(),
            "brand": self.BRAND_NAME,
            "description": description,
            "category": category,
            "unit_price": price,
            "product_url": url,
            "image_url": image_url,
            "in_stock": True,
        }
