"""
BaseScraper — Abstract base class for all vendor scrapers
══════════════════════════════════════════════════════════════
Every vendor scraper inherits from this. It handles:
  • Browser lifecycle (Playwright)
  • Scrape run tracking (start → complete/fail)
  • Product upsert with change detection
  • Standardized logging

Subclasses implement:
  • login(page)       — vendor-specific login flow
  • scrape(page)      — scrape products, call self.save_product() for each
"""

import asyncio
import os
import sys
import traceback
from abc import ABC, abstractmethod
from datetime import datetime

from dotenv import load_dotenv
from playwright.async_api import async_playwright

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db.supabase_client import (
    get_vendor_by_slug,
    start_scrape_run,
    complete_scrape_run,
    fail_scrape_run,
    upsert_product,
)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


class BaseScraper(ABC):
    """
    Base class for all vendor scrapers.

    Usage:
        class EPalletScraper(BaseScraper):
            VENDOR_SLUG = "epallet"
            def login(self, page): ...
            def scrape(self, page): ...

        asyncio.run(EPalletScraper().run())
    """

    VENDOR_SLUG: str = ""         # Override in subclass: "epallet", "costco", etc.
    VENDOR_NAME: str = ""         # Human-readable name (auto-filled from DB if blank)
    SCRAPE_METHOD: str = "playwright"
    MAX_PRODUCTS: int = 0         # 0 = unlimited; set >0 to cap (e.g. 100 for tests)

    def __init__(self):
        if not self.VENDOR_SLUG:
            raise ValueError("Subclass must set VENDOR_SLUG")

        # Load vendor from DB
        self.vendor = get_vendor_by_slug(self.VENDOR_SLUG)
        if not self.vendor:
            raise ValueError(f"Vendor '{self.VENDOR_SLUG}' not found in database")

        self.vendor_id = self.vendor["id"]
        self.VENDOR_NAME = self.VENDOR_NAME or self.vendor["name"]

        # Stats tracked during scrape
        self.run_id: str | None = None
        self.stats = {
            "products_found": 0,
            "products_new": 0,
            "products_updated": 0,
            "price_changes": 0,
            "errors": 0,
            "error_log": [],
        }

        # Browser settings from .env
        self.headless = os.getenv("HEADLESS", "false").lower() == "true"
        self.page_delay = float(os.getenv("PAGE_DELAY", "2.5"))
        self.timeout = int(os.getenv("TIMEOUT", "30000"))

    # ── Abstract methods (implement in each vendor scraper) ─────

    @abstractmethod
    async def login(self, page) -> bool:
        """
        Log into the vendor website.
        Returns True if login succeeded, False otherwise.
        """
        ...

    @abstractmethod
    async def scrape(self, page) -> None:
        """
        Scrape all products. Call self.save_product(data) for each product found.
        The page is already authenticated when this is called.
        """
        ...

    # ── Product saving ──────────────────────────────────────────

    def save_product(self, product_data: dict) -> dict:
        """
        Save a single product to Supabase. Call this from scrape().
        product_data should include at minimum: sku, product_name
        Optional: upc, brand, unit_price, case_price, category, etc.
        Returns {action, price_changed}
        """
        self.stats["products_found"] += 1

        try:
            result = upsert_product(self.vendor_id, product_data, self.run_id)

            if result["action"] == "new":
                self.stats["products_new"] += 1
            elif result["action"] == "updated":
                self.stats["products_updated"] += 1
            if result["price_changed"]:
                self.stats["price_changes"] += 1

            return result

        except Exception as e:
            self.stats["errors"] += 1
            self.stats["error_log"].append({
                "sku": product_data.get("sku", "?"),
                "error": str(e),
            })
            self.log(f"  ✗ Error saving {product_data.get('sku', '?')}: {e}")
            return {"action": "error", "price_changed": False}

    # ── Main run orchestrator ───────────────────────────────────

    async def run(self, triggered_by: str = "manual") -> dict:
        """
        Full scrape lifecycle:
        1. Start scrape run in DB
        2. Launch browser
        3. Login
        4. Scrape products
        5. Complete scrape run with stats
        Returns stats dict.
        """
        self.log("=" * 60)
        self.log(f"🚀 SCRAPER 4000 — {self.VENDOR_NAME}")
        self.log(f"   Slug: {self.VENDOR_SLUG}")
        self.log(f"   Method: {self.SCRAPE_METHOD}")
        self.log(f"   Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log("=" * 60)

        # Step 1: Register scrape run
        self.run_id = start_scrape_run(
            self.vendor_id, self.SCRAPE_METHOD, triggered_by
        )
        self.log(f"📝 Scrape run: {self.run_id}")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()
                page.set_default_timeout(self.timeout)

                # Step 2: Login
                self.log("\n🔐 Logging in...")
                login_ok = await self.login(page)
                if not login_ok:
                    raise RuntimeError("Login failed")
                self.log("✅ Login successful\n")

                # Step 3: Scrape
                self.log("📦 Starting scrape...")
                await self.scrape(page)

                await browser.close()

            # Step 4: Complete
            complete_scrape_run(self.run_id, self.stats)

            self.log("\n" + "=" * 60)
            self.log(f"✅ SCRAPE COMPLETE — {self.VENDOR_NAME}")
            self.log(f"   Products found:   {self.stats['products_found']}")
            self.log(f"   New products:     {self.stats['products_new']}")
            self.log(f"   Updated products: {self.stats['products_updated']}")
            self.log(f"   Price changes:    {self.stats['price_changes']}")
            self.log(f"   Errors:           {self.stats['errors']}")
            self.log("=" * 60)

            return self.stats

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            self.log(f"\n❌ SCRAPE FAILED: {e}")
            fail_scrape_run(self.run_id, error_msg)
            self.stats["errors"] += 1
            return self.stats

    # ── Shared helpers for all scrapers ─────────────────────────

    async def safe_text(self, locator, timeout=2000):
        """Safely get text from a locator, returns '' on failure."""
        try:
            return (await locator.first.inner_text(timeout=timeout)).strip()
        except Exception:
            return ""

    async def safe_attr(self, locator, attr, timeout=2000):
        """Safely get an attribute from a locator, returns '' on failure."""
        try:
            return await locator.first.get_attribute(attr, timeout=timeout) or ""
        except Exception:
            return ""

    @staticmethod
    def parse_price(text):
        """Extract first dollar amount from text like '$12.99' or '12.99/ea'."""
        if not text:
            return None
        import re
        m = re.search(r"\$?([\d,]+\.?\d*)", text.replace("\n", " ").replace("\xa0", " "))
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None
        return None

    def has_reached_limit(self):
        """Check if we've hit the MAX_PRODUCTS cap (if set)."""
        if self.MAX_PRODUCTS > 0 and self.stats["products_found"] >= self.MAX_PRODUCTS:
            return True
        return False

    # ── Helpers ─────────────────────────────────────────────────

    def get_env(self, key: str, default: str = "") -> str:
        """Get an environment variable (convenience for subclasses)."""
        return os.getenv(key, default)

    def get_credentials(self, prefix: str) -> tuple[str, str]:
        """
        Get email/password from .env by vendor prefix.
        e.g. get_credentials("COSTCO") → (COSTCO_EMAIL, COSTCO_PASSWORD)
        """
        email = os.getenv(f"{prefix}_EMAIL", "")
        password = os.getenv(f"{prefix}_PASSWORD", "")
        return email, password

    async def delay(self, page, seconds: float = None):
        """Polite delay between actions."""
        wait = int((seconds or self.page_delay) * 1000)
        await page.wait_for_timeout(wait)

    def log(self, msg: str):
        """Print with vendor prefix."""
        print(f"[{self.VENDOR_SLUG}] {msg}")
