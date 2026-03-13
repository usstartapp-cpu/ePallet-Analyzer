"""
Supabase Client — Centralized database access for Scraper 4000
══════════════════════════════════════════════════════════════════
All scrapers and the dashboard use this module for DB operations.
Uses service_role key for full access (server-side only).
"""

import os
from datetime import datetime, date, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY: str = os.environ.get("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise EnvironmentError(
        "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env"
    )

# Shared client instance (service_role for server-side operations)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_client() -> Client:
    """Return the shared Supabase client."""
    return supabase


# ═══════════════════════════════════════════════════════════════════
# VENDOR OPERATIONS
# ═══════════════════════════════════════════════════════════════════

def get_vendor_by_slug(slug: str) -> dict | None:
    """Look up a vendor by slug. Returns dict or None."""
    resp = supabase.table("vendors").select("*").eq("slug", slug).execute()
    return resp.data[0] if resp.data else None


def get_all_vendors(enabled_only=False) -> list[dict]:
    """Get all vendors, optionally filtered to scrape_enabled=true."""
    q = supabase.table("vendors").select("*").order("name")
    if enabled_only:
        q = q.eq("scrape_enabled", True)
    return q.execute().data


def get_vendor_id(slug: str) -> str | None:
    """Get just the UUID for a vendor slug."""
    v = get_vendor_by_slug(slug)
    return v["id"] if v else None


# ═══════════════════════════════════════════════════════════════════
# SCRAPE RUN OPERATIONS
# ═══════════════════════════════════════════════════════════════════

def start_scrape_run(vendor_id: str, method: str = "playwright",
                     triggered_by: str = "manual") -> str:
    """Create a new scrape_run record and return its ID."""
    resp = supabase.table("scrape_runs").insert({
        "vendor_id": vendor_id,
        "status": "running",
        "method": method,
        "triggered_by": triggered_by,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return resp.data[0]["id"]


def complete_scrape_run(run_id: str, stats: dict):
    """
    Mark a scrape run as completed with stats.
    stats keys: products_found, products_new, products_updated,
                price_changes, errors, error_log
    """
    now = datetime.now(timezone.utc)
    run = supabase.table("scrape_runs").select("started_at").eq("id", run_id).execute()
    duration = None
    if run.data:
        started = datetime.fromisoformat(run.data[0]["started_at"].replace("Z", "+00:00"))
        duration = int((now - started).total_seconds())

    status = "completed" if stats.get("errors", 0) == 0 else "partial"
    supabase.table("scrape_runs").update({
        "status": status,
        "products_found": stats.get("products_found", 0),
        "products_new": stats.get("products_new", 0),
        "products_updated": stats.get("products_updated", 0),
        "price_changes": stats.get("price_changes", 0),
        "errors": stats.get("errors", 0),
        "error_log": stats.get("error_log"),
        "completed_at": now.isoformat(),
        "duration_secs": duration,
    }).eq("id", run_id).execute()


def fail_scrape_run(run_id: str, error_msg: str):
    """Mark a scrape run as failed."""
    supabase.table("scrape_runs").update({
        "status": "failed",
        "error_log": {"fatal": error_msg},
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", run_id).execute()


def get_recent_runs(vendor_slug: str = None, limit: int = 20) -> list[dict]:
    """Get recent scrape runs, optionally filtered by vendor."""
    q = supabase.table("scrape_runs").select(
        "*, vendors(name, slug)"
    ).order("started_at", desc=True).limit(limit)
    if vendor_slug:
        vendor = get_vendor_by_slug(vendor_slug)
        if vendor:
            q = q.eq("vendor_id", vendor["id"])
    return q.execute().data


# ═══════════════════════════════════════════════════════════════════
# PRODUCT OPERATIONS
# ═══════════════════════════════════════════════════════════════════

def upsert_product(vendor_id: str, product_data: dict,
                   run_id: str = None) -> dict:
    """
    Insert or update a product by (vendor_id, sku).
    Returns {action: 'new'|'updated'|'unchanged'|'skipped', price_changed: bool}
    """
    sku = product_data.get("sku")
    if not sku:
        return {"action": "skipped", "price_changed": False}

    existing = supabase.table("products").select("*").eq(
        "vendor_id", vendor_id
    ).eq("sku", sku).execute()

    now = datetime.now(timezone.utc).isoformat()
    new_unit_price = product_data.get("unit_price")
    new_case_price = product_data.get("case_price")

    if existing.data:
        # ── UPDATE ────────────────────────────────────────────────
        old = existing.data[0]
        old_unit_price = old.get("unit_price")

        price_changed = False
        if new_unit_price is not None and old_unit_price is not None:
            try:
                price_changed = float(new_unit_price) != float(old_unit_price)
            except (ValueError, TypeError):
                pass

        update_data = {**product_data, "vendor_id": vendor_id,
                       "last_seen_at": now, "price_changed": price_changed}
        if price_changed:
            update_data["last_price"] = old_unit_price

        supabase.table("products").update(update_data).eq("id", old["id"]).execute()

        # History snapshot
        if run_id:
            supabase.table("product_history").insert({
                "product_id": old["id"],
                "scrape_run_id": run_id,
                "unit_price": new_unit_price,
                "case_price": new_case_price,
                "bulk_price": product_data.get("bulk_price"),
                "in_stock": product_data.get("in_stock", True),
                "captured_at": now,
            }).execute()

        return {"action": "updated" if price_changed else "unchanged",
                "price_changed": price_changed}

    else:
        # ── INSERT ────────────────────────────────────────────────
        insert_data = {**product_data, "vendor_id": vendor_id,
                       "first_seen_at": now, "last_seen_at": now,
                       "price_changed": False}
        resp = supabase.table("products").insert(insert_data).execute()
        product_id = resp.data[0]["id"] if resp.data else None

        if run_id and product_id:
            supabase.table("product_history").insert({
                "product_id": product_id,
                "scrape_run_id": run_id,
                "unit_price": new_unit_price,
                "case_price": new_case_price,
                "bulk_price": product_data.get("bulk_price"),
                "in_stock": product_data.get("in_stock", True),
                "captured_at": now,
            }).execute()

        return {"action": "new", "price_changed": False}


def get_products(vendor_slug: str = None, category: str = None,
                 search: str = None, limit: int = 500, offset: int = 0) -> list[dict]:
    """Get products with optional filters."""
    q = supabase.table("products").select("*, vendors(name, slug)")

    if vendor_slug:
        vendor = get_vendor_by_slug(vendor_slug)
        if vendor:
            q = q.eq("vendor_id", vendor["id"])
    if category:
        q = q.eq("category", category)
    if search:
        q = q.ilike("product_name", f"%{search}%")

    return q.order("product_name").range(offset, offset + limit - 1).execute().data


def get_product_count(vendor_slug: str = None) -> int:
    """Get total product count, optionally by vendor."""
    q = supabase.table("products").select("id", count="exact")
    if vendor_slug:
        vendor = get_vendor_by_slug(vendor_slug)
        if vendor:
            q = q.eq("vendor_id", vendor["id"])
    return q.execute().count or 0


# ═══════════════════════════════════════════════════════════════════
# ANALYTICS / VIEWS
# ═══════════════════════════════════════════════════════════════════

def get_price_comparison(upc: str = None, limit: int = 100) -> list[dict]:
    """Cross-vendor price comparison data."""
    q = supabase.table("price_comparison").select("*").limit(limit)
    if upc:
        q = q.eq("upc", upc)
    return q.execute().data


def get_best_prices(limit: int = 100) -> list[dict]:
    """Best price per product across all vendors."""
    return supabase.table("best_prices").select("*").limit(limit).execute().data


def get_vendor_stats() -> list[dict]:
    """Product count + last scrape info per vendor."""
    vendors = get_all_vendors()
    stats = []
    for v in vendors:
        count_resp = supabase.table("products").select(
            "id", count="exact"
        ).eq("vendor_id", v["id"]).execute()

        last_run = supabase.table("scrape_runs").select("*").eq(
            "vendor_id", v["id"]
        ).order("started_at", desc=True).limit(1).execute()

        stats.append({
            "vendor": v,
            "product_count": count_resp.count or 0,
            "last_run": last_run.data[0] if last_run.data else None,
        })
    return stats


# ═══════════════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════════════

def check_rate_limit(action: str = "scrape_manual", user_id: str = "default",
                     max_per_day: int = 5) -> dict:
    """
    Check and increment rate limit.
    Returns {allowed: bool, count: int, max: int, resets_at: str}
    """
    today = date.today().isoformat()

    resp = supabase.table("rate_limits").select("*").eq(
        "action", action
    ).eq("user_id", user_id).eq("reset_date", today).execute()

    if resp.data:
        current = resp.data[0]
        count = current["count_today"]
        if count >= max_per_day:
            return {"allowed": False, "count": count,
                    "max": max_per_day, "resets_at": today}
        supabase.table("rate_limits").update({
            "count_today": count + 1,
            "last_used": datetime.now(timezone.utc).isoformat(),
        }).eq("id", current["id"]).execute()
        return {"allowed": True, "count": count + 1,
                "max": max_per_day, "resets_at": today}
    else:
        supabase.table("rate_limits").insert({
            "action": action, "user_id": user_id,
            "count_today": 1, "reset_date": today,
            "last_used": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return {"allowed": True, "count": 1,
                "max": max_per_day, "resets_at": today}
