"""
Scraper 4000 — Multi-Vendor Procurement Intelligence Dashboard API
═══════════════════════════════════════════════════════════════════════
Flask backend serving ALL vendor data from Supabase.
Separate from the legacy ePallet-only server.py.

Endpoints:
  /api/v2/vendors           — vendor registry
  /api/v2/products          — paginated, filtered products (any/all vendors)
  /api/v2/summary           — dashboard KPIs
  /api/v2/analytics/*       — cross-vendor analytics
  /api/v2/compare           — product comparison across vendors
  /api/v2/scrape-runs       — scrape history
  /api/v2/price-history     — product price trends
  /api/v2/price-matrix      — advanced price comparison matrix
  /api/v2/top-deals         — auto-generated best deals
  /api/v2/data-quality      — data coverage & quality stats
  /api/v2/export/deals-csv  — export deals report
"""

import os
import io
import re
import sys
import math
import json
from datetime import datetime, timezone
from functools import wraps
from difflib import SequenceMatcher

from flask import Flask, request, jsonify, send_file, session
from flask_cors import CORS

# Project root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from db.supabase_client import supabase, get_all_vendors, get_vendor_by_slug

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = "scraper4000-multi-vendor-secret-key-change-in-prod"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False
CORS(app, supports_credentials=True)

USERS = {
    "admin": "epallet2026",
    "michael": "LAFoods2026",
}


# ═══════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/api/v2/login", methods=["POST"])
def login():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    if username in USERS and USERS[username] == password:
        session["logged_in"] = True
        session["username"] = username
        return jsonify({"ok": True, "username": username})
    return jsonify({"error": "Invalid credentials"}), 401


@app.route("/api/v2/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/v2/me")
def me():
    if session.get("logged_in"):
        return jsonify({"logged_in": True, "username": session.get("username")})
    return jsonify({"logged_in": False}), 401


# ═══════════════════════════════════════════════════════════════════
# MATCH CONFIDENCE SCORING
# ═══════════════════════════════════════════════════════════════════

def compute_match_confidence(product_a, product_b):
    """
    Compute a 0-100 confidence score for how "apples-to-apples" two products are.
    Returns a dict with overall score, breakdown, and human-readable level.
    """
    score = 0
    breakdown = {}

    # 1. UPC match (strongest signal) — 40 points
    upc_a = (product_a.get("upc") or "").strip()
    upc_b = (product_b.get("upc") or "").strip()
    if upc_a and upc_b and upc_a == upc_b:
        score += 40
        breakdown["upc"] = {"score": 40, "max": 40, "detail": "Exact UPC match"}
    elif upc_a and upc_b:
        breakdown["upc"] = {"score": 0, "max": 40, "detail": "UPC mismatch"}
    else:
        breakdown["upc"] = {"score": 0, "max": 40, "detail": "UPC missing"}

    # 2. Brand match — 15 points
    brand_a = (product_a.get("brand") or "").strip().lower()
    brand_b = (product_b.get("brand") or "").strip().lower()
    if brand_a and brand_b:
        if brand_a == brand_b:
            score += 15
            breakdown["brand"] = {"score": 15, "max": 15, "detail": "Exact brand match"}
        elif brand_a in brand_b or brand_b in brand_a:
            score += 10
            breakdown["brand"] = {"score": 10, "max": 15, "detail": "Partial brand match"}
        else:
            breakdown["brand"] = {"score": 0, "max": 15, "detail": "Brand mismatch"}
    else:
        breakdown["brand"] = {"score": 0, "max": 15, "detail": "Brand missing"}

    # 3. Pack size / unit match — 25 points
    pack_a = (product_a.get("pack_size_raw") or "").strip().lower()
    pack_b = (product_b.get("pack_size_raw") or "").strip().lower()
    count_a = product_a.get("pack_count")
    count_b = product_b.get("pack_count")
    size_a = product_a.get("unit_size")
    size_b = product_b.get("unit_size")
    measure_a = (product_a.get("unit_measure") or "").strip().lower()
    measure_b = (product_b.get("unit_measure") or "").strip().lower()

    pack_score = 0
    pack_detail = "Pack size missing"
    if count_a and count_b and size_a and size_b and measure_a and measure_b:
        if float(count_a) == float(count_b) and float(size_a) == float(size_b) and measure_a == measure_b:
            pack_score = 25
            pack_detail = f"Exact pack match ({count_a}x{size_a}{measure_a})"
        elif float(count_a) == float(count_b):
            pack_score = 15
            pack_detail = f"Same count ({count_a}), different unit"
        elif measure_a == measure_b:
            pack_score = 10
            pack_detail = f"Same measure ({measure_a}), different pack"
        else:
            pack_score = 5
            pack_detail = "Structured data available but mismatched"
    elif pack_a and pack_b:
        sim = SequenceMatcher(None, pack_a, pack_b).ratio()
        if sim > 0.8:
            pack_score = 20
            pack_detail = f"Pack strings very similar ({sim:.0%})"
        elif sim > 0.5:
            pack_score = 12
            pack_detail = f"Pack strings partially similar ({sim:.0%})"
        else:
            pack_score = 5
            pack_detail = f"Pack strings differ ({sim:.0%})"
    elif pack_a or pack_b:
        pack_score = 2
        pack_detail = "Pack size only on one product"

    score += pack_score
    breakdown["pack_size"] = {"score": pack_score, "max": 25, "detail": pack_detail}

    # 4. Name similarity — 20 points
    name_a = (product_a.get("product_name") or "").strip().lower()
    name_b = (product_b.get("product_name") or "").strip().lower()
    if name_a and name_b:
        clean_a = re.sub(r'[^a-z0-9\s]', '', name_a)
        clean_b = re.sub(r'[^a-z0-9\s]', '', name_b)
        sim = SequenceMatcher(None, clean_a, clean_b).ratio()
        name_score = round(sim * 20)
        score += name_score
        breakdown["name"] = {"score": name_score, "max": 20, "detail": f"Name similarity: {sim:.0%}"}
    else:
        breakdown["name"] = {"score": 0, "max": 20, "detail": "Name missing"}

    # Determine level
    if score >= 75:
        level = "high"
        label = "Strong Match"
        icon = "🟢"
    elif score >= 45:
        level = "medium"
        label = "Likely Match"
        icon = "🟡"
    elif score >= 25:
        level = "low"
        label = "Weak Match"
        icon = "🟠"
    else:
        level = "poor"
        label = "Poor Match"
        icon = "🔴"

    return {
        "score": min(score, 100),
        "level": level,
        "label": label,
        "icon": icon,
        "breakdown": breakdown,
    }


def normalize_unit_price(product):
    """
    Compute a normalized price-per-unit for comparison.
    Returns price_per_oz, price_per_unit, and the normalization method used.
    """
    result = {
        "price_per_oz": None,
        "price_per_unit": None,
        "price_per_case_unit": None,
        "normalization": "none",
        "normalized_label": None,
    }

    price = product.get("unit_price")
    if price is None:
        return result
    try:
        price = float(price)
    except (ValueError, TypeError):
        return result

    # If price_per_oz is already available
    if product.get("price_per_oz"):
        try:
            result["price_per_oz"] = round(float(product["price_per_oz"]), 4)
            result["normalization"] = "vendor_provided"
            result["normalized_label"] = f"${result['price_per_oz']:.4f}/oz"
        except (ValueError, TypeError):
            pass

    # Try to compute from structured pack data
    pack_count = product.get("pack_count")
    unit_size = product.get("unit_size")
    unit_measure = (product.get("unit_measure") or "").strip().lower()

    if pack_count and unit_size:
        try:
            pc = float(pack_count)
            us = float(unit_size)
            total_units = pc * us if pc > 0 and us > 0 else None

            if total_units and total_units > 0:
                if unit_measure in ("oz", "fl oz", "floz"):
                    result["price_per_oz"] = round(price / total_units, 4)
                    result["normalization"] = "computed"
                    result["normalized_label"] = f"${result['price_per_oz']:.4f}/oz ({pc:.0f}x{us}{unit_measure})"
                elif unit_measure in ("lb", "lbs"):
                    total_oz = total_units * 16
                    result["price_per_oz"] = round(price / total_oz, 4)
                    result["normalization"] = "computed"
                    result["normalized_label"] = f"${result['price_per_oz']:.4f}/oz ({pc:.0f}x{us}lb)"

                if pc > 0:
                    result["price_per_unit"] = round(price / pc, 4)
                    result["price_per_case_unit"] = round(price / pc, 4)
        except (ValueError, TypeError):
            pass

    # Try to parse from raw pack_size string
    if result["normalization"] == "none" and product.get("pack_size_raw"):
        raw = product["pack_size_raw"]
        m = re.search(r'(\d+)\s*[/x\u00d7-]\s*(\d+(?:\.\d+)?)\s*(oz|fl\s*oz|lb)', raw, re.IGNORECASE)
        if m:
            try:
                count = float(m.group(1))
                size = float(m.group(2))
                measure = m.group(3).lower().strip()
                total = count * size
                if "lb" in measure:
                    total *= 16
                if total > 0:
                    result["price_per_oz"] = round(price / total, 4)
                    result["normalization"] = "parsed"
                    result["normalized_label"] = f"${result['price_per_oz']:.4f}/oz (parsed)"
                    result["price_per_unit"] = round(price / count, 4)
            except (ValueError, TypeError):
                pass

        if result["normalization"] == "none":
            m = re.search(r'(\d+(?:\.\d+)?)\s*(oz|fl\s*oz|lb)', raw, re.IGNORECASE)
            if m:
                try:
                    size = float(m.group(1))
                    measure = m.group(2).lower().strip()
                    total = size
                    if "lb" in measure:
                        total *= 16
                    if total > 0:
                        result["price_per_oz"] = round(price / total, 4)
                        result["normalization"] = "parsed_simple"
                        result["normalized_label"] = f"${result['price_per_oz']:.4f}/oz"
                except (ValueError, TypeError):
                    pass

    return result


# ═══════════════════════════════════════════════════════════════════
# VENDORS
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/vendors")
@login_required
def vendors():
    """List all vendors with product counts and last scrape info."""
    all_vendors = get_all_vendors()
    result = []
    for v in all_vendors:
        # Product count
        cnt = supabase.table("products").select(
            "id", count="exact"
        ).eq("vendor_id", v["id"]).execute()

        # Last scrape run
        last = supabase.table("scrape_runs").select("*").eq(
            "vendor_id", v["id"]
        ).order("started_at", desc=True).limit(1).execute()

        result.append({
            "id": v["id"],
            "name": v["name"],
            "slug": v["slug"],
            "website": v.get("website"),
            "scrape_method": v.get("scrape_method"),
            "scrape_enabled": v.get("scrape_enabled", True),
            "product_count": cnt.count or 0,
            "last_run": last.data[0] if last.data else None,
        })

    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════
# SUMMARY / KPIs
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/summary")
@login_required
def summary():
    """Dashboard KPIs — optionally filtered by vendor."""
    vendor_slug = request.args.get("vendor")

    # Total vendors
    vendors = get_all_vendors()
    active_vendors = [v for v in vendors if v.get("scrape_enabled")]

    # Product count
    q = supabase.table("products").select("id", count="exact")
    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            q = q.eq("vendor_id", v["id"])
    total_products = q.execute().count or 0

    # Use RPC or distinct queries for counts — fetch all rows
    # Categories (distinct)
    cat_q = supabase.table("products").select("category").limit(5000)
    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            cat_q = cat_q.eq("vendor_id", v["id"])
    cat_data = cat_q.execute().data
    categories = set(r["category"] for r in cat_data if r.get("category"))

    # Brands (distinct)
    brand_q = supabase.table("products").select("brand").limit(5000)
    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            brand_q = brand_q.eq("vendor_id", v["id"])
    brand_data = brand_q.execute().data
    brands = set(r["brand"] for r in brand_data if r.get("brand"))

    # Price changes today
    price_q = supabase.table("products").select(
        "id", count="exact"
    ).eq("price_changed", True)
    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            price_q = price_q.eq("vendor_id", v["id"])
    price_changes = price_q.execute().count or 0

    # Total scrape runs
    runs = supabase.table("scrape_runs").select(
        "id", count="exact"
    ).execute()
    total_runs = runs.count or 0

    return jsonify({
        "total_products": total_products,
        "total_vendors": len(active_vendors),
        "total_categories": len(categories),
        "total_brands": len(brands),
        "price_changes": price_changes,
        "total_runs": total_runs,
    })


# ═══════════════════════════════════════════════════════════════════
# PRODUCTS (Paginated, Filtered, Sorted)
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/products")
@login_required
def products():
    """
    Paginated, filtered, sorted product list from Supabase.
    Query params:
      vendor    — vendor slug or 'all'
      category  — category filter
      brand     — brand filter
      search    — text search in product_name
      in_stock  — true/false
      price_changed — true/false
      sort_by   — column name
      sort_dir  — asc/desc
      page      — page number (1-based)
      per_page  — items per page
    """
    vendor_slug = request.args.get("vendor", "all")
    category = request.args.get("category")
    brand = request.args.get("brand")
    search = request.args.get("search", "").strip()
    in_stock = request.args.get("in_stock")
    price_changed = request.args.get("price_changed")
    sort_by = request.args.get("sort_by", "product_name")
    sort_dir = request.args.get("sort_dir", "asc")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    # Build query
    q = supabase.table("products").select(
        "*, vendors(name, slug)", count="exact"
    )

    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            q = q.eq("vendor_id", v["id"])

    if category:
        q = q.eq("category", category)
    if brand:
        q = q.eq("brand", brand)
    if search:
        q = q.ilike("product_name", f"%{search}%")
    if in_stock == "true":
        q = q.eq("in_stock", True)
    if price_changed == "true":
        q = q.eq("price_changed", True)

    # Sorting
    desc = sort_dir == "desc"
    q = q.order(sort_by, desc=desc)

    # Pagination
    offset = (page - 1) * per_page
    q = q.range(offset, offset + per_page - 1)

    resp = q.execute()
    total = resp.count or 0

    return jsonify({
        "products": resp.data,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": math.ceil(total / per_page) if per_page else 1,
    })


# ═══════════════════════════════════════════════════════════════════
# FILTERS (dynamic from data)
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/filters")
@login_required
def filters():
    """Get available filter values, optionally scoped to a vendor."""
    vendor_slug = request.args.get("vendor", "all")

    cat_q = supabase.table("products").select("category")
    brand_q = supabase.table("products").select("brand")

    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            cat_q = cat_q.eq("vendor_id", v["id"])
            brand_q = brand_q.eq("vendor_id", v["id"])

    cats = sorted(set(
        r["category"] for r in cat_q.execute().data
        if r.get("category")
    ))
    brands = sorted(set(
        r["brand"] for r in brand_q.execute().data
        if r.get("brand")
    ))

    return jsonify({
        "categories": cats,
        "brands": brands,
    })


# ═══════════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/analytics/by-vendor")
@login_required
def analytics_by_vendor():
    """Product count and avg price per vendor."""
    vendors = get_all_vendors()
    result = []
    for v in vendors:
        prods = supabase.table("products").select(
            "unit_price"
        ).eq("vendor_id", v["id"]).execute().data

        prices = [p["unit_price"] for p in prods if p.get("unit_price") is not None]
        result.append({
            "vendor": v["name"],
            "slug": v["slug"],
            "product_count": len(prods),
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
            "min_price": round(min(prices), 2) if prices else None,
            "max_price": round(max(prices), 2) if prices else None,
        })

    return jsonify(sorted(result, key=lambda x: x["product_count"], reverse=True))


@app.route("/api/v2/analytics/by-category")
@login_required
def analytics_by_category():
    """Product count by category, optionally scoped to a vendor."""
    vendor_slug = request.args.get("vendor", "all")

    q = supabase.table("products").select("category, unit_price")
    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            q = q.eq("vendor_id", v["id"])

    data = q.execute().data

    # Group by category
    cats = {}
    for row in data:
        cat = row.get("category") or "Uncategorized"
        if cat not in cats:
            cats[cat] = {"category": cat, "count": 0, "prices": []}
        cats[cat]["count"] += 1
        if row.get("unit_price") is not None:
            cats[cat]["prices"].append(float(row["unit_price"]))

    result = []
    for cat, info in cats.items():
        prices = info["prices"]
        result.append({
            "category": cat,
            "count": info["count"],
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
            "min_price": round(min(prices), 2) if prices else None,
            "max_price": round(max(prices), 2) if prices else None,
        })

    return jsonify(sorted(result, key=lambda x: x["count"], reverse=True))


@app.route("/api/v2/analytics/by-brand")
@login_required
def analytics_by_brand():
    """Top brands by product count."""
    vendor_slug = request.args.get("vendor", "all")
    limit = int(request.args.get("limit", 30))

    q = supabase.table("products").select("brand, unit_price")
    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            q = q.eq("vendor_id", v["id"])

    data = q.execute().data

    brands = {}
    for row in data:
        b = row.get("brand") or "Unknown"
        if b not in brands:
            brands[b] = {"brand": b, "count": 0, "prices": []}
        brands[b]["count"] += 1
        if row.get("unit_price") is not None:
            brands[b]["prices"].append(float(row["unit_price"]))

    result = []
    for b, info in brands.items():
        prices = info["prices"]
        result.append({
            "brand": b,
            "count": info["count"],
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
        })

    result.sort(key=lambda x: x["count"], reverse=True)
    return jsonify(result[:limit])


@app.route("/api/v2/analytics/price-changes")
@login_required
def price_changes():
    """Products with recent price changes."""
    vendor_slug = request.args.get("vendor", "all")
    limit = int(request.args.get("limit", 50))

    q = supabase.table("products").select(
        "sku, product_name, brand, category, unit_price, last_price, "
        "price_changed, last_seen_at, vendors(name, slug)"
    ).eq("price_changed", True).order("last_seen_at", desc=True).limit(limit)

    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            q = q.eq("vendor_id", v["id"])

    data = q.execute().data

    # Compute change details
    for row in data:
        old = row.get("last_price")
        new = row.get("unit_price")
        if old and new:
            try:
                old_f = float(old)
                new_f = float(new)
                row["price_diff"] = round(new_f - old_f, 4)
                row["price_pct_change"] = round(
                    ((new_f - old_f) / old_f) * 100, 2
                ) if old_f else 0
            except (ValueError, TypeError):
                row["price_diff"] = None
                row["price_pct_change"] = None
        else:
            row["price_diff"] = None
            row["price_pct_change"] = None

    return jsonify(data)


# ═══════════════════════════════════════════════════════════════════
# CROSS-VENDOR COMPARISON
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/compare")
@login_required
def compare_products():
    """
    Compare products across vendors by UPC or name.
    Query params:
      upc    — UPC to compare
      search — product name search
    """
    upc = request.args.get("upc")
    search = request.args.get("search", "").strip()
    limit = int(request.args.get("limit", 100))

    q = supabase.table("products").select(
        "*, vendors(name, slug)"
    )

    if upc:
        q = q.eq("upc", upc)
    elif search:
        q = q.ilike("product_name", f"%{search}%")
    else:
        return jsonify({"error": "Provide upc or search parameter"}), 400

    q = q.order("unit_price").limit(limit)
    data = q.execute().data

    return jsonify(data)


@app.route("/api/v2/price-matrix")
@login_required
def price_matrix():
    """
    Advanced cross-vendor price comparison matrix.
    Returns products grouped by name similarity with prices from every vendor,
    differences vs a baseline (default: amazon), per-unit breakdowns, etc.

    Query params:
      search         — product name search (required)
      baseline       — vendor slug to use as baseline (default: 'amazon')
      manual_price   — manual override baseline price (float)
      category       — filter by category
      brand          — filter by brand
      sort_by        — 'savings', 'name', 'baseline_price' (default: 'name')
      limit          — max product groups (default: 50)
    """
    search = request.args.get("search", "").strip()
    baseline_slug = request.args.get("baseline", "amazon")
    manual_price = request.args.get("manual_price")
    category = request.args.get("category")
    brand = request.args.get("brand")
    sort_by = request.args.get("sort_by", "name")
    limit = int(request.args.get("limit", 50))

    if not search:
        return jsonify({"error": "Provide search parameter"}), 400

    # Parse manual price
    manual_price_f = None
    if manual_price:
        try:
            manual_price_f = float(manual_price)
        except (ValueError, TypeError):
            pass

    # Fetch matching products across all vendors
    q = supabase.table("products").select(
        "id, sku, upc, product_name, brand, category, unit_price, case_price, "
        "pack_size_raw, in_stock, image_url, product_url, description, "
        "vendor_id, vendors(name, slug)"
    ).ilike("product_name", f"%{search}%")

    if category:
        q = q.eq("category", category)
    if brand:
        q = q.eq("brand", brand)

    q = q.order("product_name").limit(1000)
    raw = q.execute().data

    if not raw:
        return jsonify({"groups": [], "baseline_vendor": baseline_slug,
                        "total_groups": 0, "vendors_found": []})

    # Collect all vendors that appear in results
    vendors_seen = {}
    for p in raw:
        vslug = p.get("vendors", {}).get("slug", "")
        vname = p.get("vendors", {}).get("name", "")
        if vslug and vslug not in vendors_seen:
            vendors_seen[vslug] = vname

    # Group products by normalized name (fuzzy grouping)
    def normalize(name):
        if not name:
            return ""
        n = name.lower().strip()
        # Remove common size/qty suffixes for grouping
        n = re.sub(r'\s*\d+\s*(oz|lb|kg|g|ml|l|ct|pk|pack|count|each|ea|pc|pcs)\b.*$', '', n, flags=re.IGNORECASE)
        n = re.sub(r'[^a-z0-9\s]', '', n)
        n = re.sub(r'\s+', ' ', n).strip()
        return n

    groups = {}
    for p in raw:
        key = normalize(p.get("product_name", ""))
        if not key:
            key = p.get("sku", str(p.get("id", "")))
        # Use UPC as a stronger grouping key if available
        if p.get("upc"):
            key = "upc:" + p["upc"]
        if key not in groups:
            groups[key] = {
                "key": key,
                "display_name": p.get("product_name", ""),
                "brand": p.get("brand"),
                "category": p.get("category"),
                "upc": p.get("upc"),
                "products": []
            }
        groups[key]["products"].append(p)

    # Build comparison data for each group
    result_groups = []
    for grp in groups.values():
        products = grp["products"]
        vendor_prices = {}

        for p in products:
            vslug = p.get("vendors", {}).get("slug", "")
            vname = p.get("vendors", {}).get("name", "")
            price = p.get("unit_price")
            if price is not None:
                try:
                    price = float(price)
                except (ValueError, TypeError):
                    price = None

            vendor_prices[vslug] = {
                "vendor_slug": vslug,
                "vendor_name": vname,
                "product_name": p.get("product_name"),
                "sku": p.get("sku"),
                "upc": p.get("upc"),
                "unit_price": price,
                "case_price": float(p["case_price"]) if p.get("case_price") else None,
                "pack_size": p.get("pack_size_raw"),
                "in_stock": p.get("in_stock"),
                "image_url": p.get("image_url"),
                "product_url": p.get("product_url"),
                "description": p.get("description"),
            }

        # Determine baseline price
        baseline_price = None
        baseline_source = "none"
        if manual_price_f is not None:
            baseline_price = manual_price_f
            baseline_source = "manual"
        elif baseline_slug in vendor_prices and vendor_prices[baseline_slug]["unit_price"] is not None:
            baseline_price = vendor_prices[baseline_slug]["unit_price"]
            baseline_source = baseline_slug

        # Compute diffs for each vendor
        all_prices = [vp["unit_price"] for vp in vendor_prices.values()
                      if vp["unit_price"] is not None]
        best_price = min(all_prices) if all_prices else None
        avg_price = round(sum(all_prices) / len(all_prices), 2) if all_prices else None
        worst_price = max(all_prices) if all_prices else None

        # Get baseline product for confidence scoring
        baseline_product = None
        if baseline_slug in vendor_prices:
            bp = vendor_prices[baseline_slug]
            baseline_product = {
                "product_name": bp.get("product_name"),
                "brand": bp.get("vendor_name"),  # Use vendor_name since brand may not be set
                "upc": bp.get("upc"),
                "pack_size_raw": bp.get("pack_size"),
            }

        vendor_comparison = []
        for vslug, vp in vendor_prices.items():
            diff = None
            pct_diff = None
            vs_best = None
            if vp["unit_price"] is not None:
                if baseline_price is not None:
                    diff = round(vp["unit_price"] - baseline_price, 2)
                    pct_diff = round((diff / baseline_price) * 100, 2) if baseline_price else None
                if best_price is not None:
                    vs_best = round(vp["unit_price"] - best_price, 2)

            # Compute match confidence vs baseline
            confidence = None
            if baseline_product and vslug != baseline_slug:
                this_product = {
                    "product_name": vp.get("product_name"),
                    "brand": vp.get("vendor_name"),
                    "upc": vp.get("upc"),
                    "pack_size_raw": vp.get("pack_size"),
                }
                confidence = compute_match_confidence(baseline_product, this_product)

            # Compute normalized pricing
            product_data = {
                "unit_price": vp.get("unit_price"),
                "price_per_oz": None,
                "pack_size_raw": vp.get("pack_size"),
                "pack_count": None,
                "unit_size": None,
                "unit_measure": None,
            }
            normalized = normalize_unit_price(product_data)

            vendor_comparison.append({
                **vp,
                "is_baseline": vslug == baseline_slug and baseline_source != "manual",
                "is_best": vp["unit_price"] == best_price if vp["unit_price"] is not None and best_price is not None else False,
                "is_worst": vp["unit_price"] == worst_price if vp["unit_price"] is not None and worst_price is not None else False,
                "diff_vs_baseline": diff,
                "pct_vs_baseline": pct_diff,
                "diff_vs_best": vs_best,
                "confidence": confidence,
                "normalized": normalized,
            })

        # Sort vendors: best price first
        vendor_comparison.sort(key=lambda x: x["unit_price"] if x["unit_price"] is not None else float("inf"))

        max_savings = None
        if best_price is not None and worst_price is not None:
            max_savings = round(worst_price - best_price, 2)

        result_groups.append({
            "display_name": grp["display_name"],
            "brand": grp["brand"],
            "category": grp["category"],
            "upc": grp["upc"],
            "vendor_count": len(vendor_prices),
            "baseline_price": baseline_price,
            "baseline_source": baseline_source,
            "best_price": best_price,
            "worst_price": worst_price,
            "avg_price": avg_price,
            "max_savings": max_savings,
            "vendors": vendor_comparison,
        })

    # Sort groups
    if sort_by == "savings":
        result_groups.sort(key=lambda g: g["max_savings"] or 0, reverse=True)
    elif sort_by == "baseline_price":
        result_groups.sort(key=lambda g: g["baseline_price"] or float("inf"))
    else:
        result_groups.sort(key=lambda g: g["display_name"].lower())

    result_groups = result_groups[:limit]

    return jsonify({
        "groups": result_groups,
        "baseline_vendor": baseline_slug,
        "baseline_source": "manual" if manual_price_f else baseline_slug,
        "total_groups": len(result_groups),
        "vendors_found": [{"slug": s, "name": n} for s, n in vendors_seen.items()],
    })


@app.route("/api/v2/top-deals")
@login_required
def top_deals():
    """
    Auto-generated price comparison data — no search required.
    """
    try:
        return _top_deals_impl()
    except Exception as e:
        print(f"[top-deals] Error: {e}")
        return jsonify({"deals": [], "total": 0, "baseline_vendor": "amazon",
                         "baseline_vendor_name": "Amazon",
                         "summary": {"total_compared": 0, "cheaper_elsewhere": 0,
                                     "more_expensive_elsewhere": 0, "avg_savings_pct": 0}})


def _top_deals_impl():
    """
    Auto-generated price comparison data — no search required.
    Finds products that exist in Amazon AND at least one other vendor,
    computes savings, and returns the best deals.

    Query params:
      baseline   — vendor slug for baseline (default: 'amazon')
      category   — filter by category
      brand      — filter by brand
      limit      — max results (default: 25)
      sort_by    — 'savings_pct', 'savings_abs', 'price' (default: 'savings_pct')
    """
    baseline_slug = request.args.get("baseline", "amazon")
    category = request.args.get("category")
    brand = request.args.get("brand")
    limit = int(request.args.get("limit", 25))
    sort_by = request.args.get("sort_by", "savings_pct")

    # 1. Get the baseline vendor ID
    baseline_vendor = get_vendor_by_slug(baseline_slug)
    if not baseline_vendor:
        return jsonify({"deals": [], "error": f"Baseline vendor '{baseline_slug}' not found"})

    baseline_vid = baseline_vendor["id"]

    # 2. Get all baseline products that have a price
    bq = supabase.table("products").select(
        "id, sku, product_name, brand, category, unit_price, case_price, "
        "pack_size_raw, image_url, product_url, description, in_stock"
    ).eq("vendor_id", baseline_vid).not_.is_("unit_price", "null")

    if category:
        bq = bq.eq("category", category)
    if brand:
        bq = bq.eq("brand", brand)

    bq = bq.order("product_name").limit(500)
    baseline_products = bq.execute().data

    if not baseline_products:
        return jsonify({"deals": [], "total": 0, "baseline_vendor": baseline_slug,
                        "message": "No priced products found for baseline vendor"})

    # 3. For each baseline product, search for matching products in other vendors
    #    Use the description field (Amazon stores "Amazon match for: <original name>")
    #    or fuzzy match on product_name keywords
    deals = []
    all_vendors = get_all_vendors()
    vendor_map = {v["id"]: v for v in all_vendors}

    for bp in baseline_products:
        bp_price = float(bp["unit_price"]) if bp.get("unit_price") else None
        if not bp_price or bp_price <= 0:
            continue

        # Extract original product name from description if available
        # Amazon products have "Amazon match for: <original name>"
        search_name = ""
        desc = bp.get("description") or ""
        if desc.startswith("Amazon match for:"):
            search_name = desc.replace("Amazon match for:", "").strip()[:80]
        else:
            search_name = bp.get("product_name", "")

        if not search_name or len(search_name) < 5:
            continue

        # Take first 3 significant words for matching
        words = re.sub(r'[^a-zA-Z0-9\s]', '', search_name).split()
        keywords = [w for w in words if len(w) > 2][:4]
        if len(keywords) < 2:
            continue

        search_term = "%".join(keywords[:3])

        # Find matching products from OTHER vendors
        mq = supabase.table("products").select(
            "id, sku, product_name, brand, category, unit_price, case_price, "
            "pack_size_raw, image_url, product_url, in_stock, vendor_id"
        ).neq("vendor_id", baseline_vid).not_.is_("unit_price", "null")

        # Chain ilike for each keyword
        for kw in keywords[:3]:
            mq = mq.ilike("product_name", f"%{kw}%")

        matches = mq.limit(10).execute().data

        if not matches:
            continue

        # Build deal entry with confidence scoring
        baseline_for_conf = {
            "product_name": bp.get("product_name"),
            "brand": bp.get("brand"),
            "upc": None,
            "pack_size_raw": bp.get("pack_size_raw"),
        }

        other_vendors = []
        for m in matches:
            m_price = float(m["unit_price"]) if m.get("unit_price") else None
            if not m_price or m_price <= 0:
                continue
            v_info = vendor_map.get(m["vendor_id"], {})
            diff = round(m_price - bp_price, 2)
            pct = round((diff / bp_price) * 100, 1) if bp_price else 0

            # Compute match confidence
            match_for_conf = {
                "product_name": m.get("product_name"),
                "brand": m.get("brand"),
                "upc": None,
                "pack_size_raw": m.get("pack_size_raw"),
            }
            confidence = compute_match_confidence(baseline_for_conf, match_for_conf)

            # Normalized pricing
            normalized = normalize_unit_price({
                "unit_price": m_price,
                "pack_size_raw": m.get("pack_size_raw"),
            })

            other_vendors.append({
                "vendor_name": v_info.get("name", "Unknown"),
                "vendor_slug": v_info.get("slug", ""),
                "product_name": m.get("product_name"),
                "sku": m.get("sku"),
                "unit_price": m_price,
                "case_price": float(m["case_price"]) if m.get("case_price") else None,
                "pack_size": m.get("pack_size_raw"),
                "image_url": m.get("image_url"),
                "product_url": m.get("product_url"),
                "in_stock": m.get("in_stock"),
                "diff_vs_baseline": diff,
                "pct_vs_baseline": pct,
                "confidence": confidence,
                "normalized": normalized,
            })

        if not other_vendors:
            continue

        other_vendors.sort(key=lambda x: x["unit_price"])
        best_other = other_vendors[0]
        best_savings_abs = round(bp_price - best_other["unit_price"], 2)
        best_savings_pct = round((best_savings_abs / bp_price) * 100, 1) if bp_price else 0

        # Baseline normalized pricing
        baseline_normalized = normalize_unit_price({
            "unit_price": bp_price,
            "pack_size_raw": bp.get("pack_size_raw"),
        })

        # Best confidence among matches
        best_confidence = max(
            (v.get("confidence", {}).get("score", 0) for v in other_vendors), default=0
        )

        deals.append({
            "baseline_product": bp.get("product_name"),
            "baseline_sku": bp.get("sku"),
            "baseline_price": bp_price,
            "baseline_image": bp.get("image_url"),
            "baseline_url": bp.get("product_url"),
            "baseline_in_stock": bp.get("in_stock"),
            "brand": bp.get("brand"),
            "category": bp.get("category"),
            "best_other_price": best_other["unit_price"],
            "best_other_vendor": best_other["vendor_name"],
            "best_other_vendor_slug": best_other["vendor_slug"],
            "best_savings_abs": best_savings_abs,
            "best_savings_pct": best_savings_pct,
            "best_confidence": best_confidence,
            "baseline_pack_size": bp.get("pack_size_raw"),
            "baseline_normalized": baseline_normalized,
            "all_vendors": other_vendors,
            "vendor_count": len(other_vendors),
        })

    # Sort
    if sort_by == "savings_abs":
        deals.sort(key=lambda d: d["best_savings_abs"], reverse=True)
    elif sort_by == "price":
        deals.sort(key=lambda d: d["baseline_price"])
    elif sort_by == "confidence":
        deals.sort(key=lambda d: d.get("best_confidence", 0), reverse=True)
    else:  # savings_pct
        deals.sort(key=lambda d: d["best_savings_pct"], reverse=True)

    deals = deals[:limit]

    # Summary stats
    total_deals = len(deals)
    deals_cheaper = [d for d in deals if d["best_savings_abs"] > 0]
    deals_more_expensive = [d for d in deals if d["best_savings_abs"] < 0]
    avg_savings = round(
        sum(d["best_savings_pct"] for d in deals_cheaper) / len(deals_cheaper), 1
    ) if deals_cheaper else 0

    return jsonify({
        "deals": deals,
        "total": total_deals,
        "baseline_vendor": baseline_slug,
        "baseline_vendor_name": baseline_vendor.get("name", baseline_slug),
        "summary": {
            "total_compared": total_deals,
            "cheaper_elsewhere": len(deals_cheaper),
            "more_expensive_elsewhere": len(deals_more_expensive),
            "avg_savings_pct": avg_savings,
        }
    })


# ═══════════════════════════════════════════════════════════════════
# PRICE HISTORY / TRENDS
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/price-history/<product_id>")
@login_required
def price_history(product_id):
    """Get price history for a specific product."""
    data = supabase.table("product_history").select("*").eq(
        "product_id", product_id
    ).order("captured_at").execute().data

    return jsonify(data)


# ═══════════════════════════════════════════════════════════════════
# SCRAPE RUNS
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/scrape-runs")
@login_required
def scrape_runs():
    """Get scrape run history."""
    vendor_slug = request.args.get("vendor")
    limit = int(request.args.get("limit", 30))

    q = supabase.table("scrape_runs").select(
        "*, vendors(name, slug)"
    ).order("started_at", desc=True).limit(limit)

    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            q = q.eq("vendor_id", v["id"])

    return jsonify(q.execute().data)


# ═══════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/export/csv")
@login_required
def export_csv():
    """Export filtered product data as CSV."""
    import csv as csv_mod

    vendor_slug = request.args.get("vendor", "all")
    category = request.args.get("category")
    search = request.args.get("search", "").strip()

    q = supabase.table("products").select("*, vendors(name, slug)")
    if vendor_slug and vendor_slug != "all":
        v = get_vendor_by_slug(vendor_slug)
        if v:
            q = q.eq("vendor_id", v["id"])
    if category:
        q = q.eq("category", category)
    if search:
        q = q.ilike("product_name", f"%{search}%")

    q = q.order("product_name").limit(10000)
    data = q.execute().data

    if not data:
        return jsonify({"error": "No data to export"}), 404

    # Flatten vendor info
    for row in data:
        vendor_info = row.pop("vendors", {}) or {}
        row["vendor_name"] = vendor_info.get("name", "")
        row["vendor_slug"] = vendor_info.get("slug", "")

    buf = io.StringIO()
    writer = csv_mod.DictWriter(buf, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)

    filename = f"scraper4000_products_{vendor_slug}.csv"
    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        download_name=filename,
        as_attachment=True,
        mimetype="text/csv",
    )


@app.route("/api/v2/export/deals-csv")
@login_required
def export_deals_csv():
    """Export the top deals data as CSV."""
    import csv as csv_mod

    # Re-run the top deals logic to get the current data
    baseline_slug = request.args.get("baseline", "amazon")
    category = request.args.get("category")
    brand = request.args.get("brand")
    limit = int(request.args.get("limit", 25))
    sort_by = request.args.get("sort_by", "savings_pct")

    # 1. Get the baseline vendor ID
    baseline_vendor = get_vendor_by_slug(baseline_slug)
    if not baseline_vendor:
        return jsonify({"deals": [], "error": f"Baseline vendor '{baseline_slug}' not found"})

    baseline_vid = baseline_vendor["id"]

    # 2. Get all baseline products that have a price
    bq = supabase.table("products").select(
        "id, sku, product_name, brand, category, unit_price, case_price, "
        "pack_size_raw, image_url, product_url, description, in_stock"
    ).eq("vendor_id", baseline_vid).not_.is_("unit_price", "null")

    if category:
        bq = bq.eq("category", category)
    if brand:
        bq = bq.eq("brand", brand)

    bq = bq.order("product_name").limit(500)
    baseline_products = bq.execute().data

    if not baseline_products:
        return jsonify({"deals": [], "total": 0, "baseline_vendor": baseline_slug,
                        "message": "No priced products found for baseline vendor"})

    # 3. For each baseline product, search for matching products in other vendors
    #    Use the description field (Amazon stores "Amazon match for: <original name>")
    #    or fuzzy match on product_name keywords
    deals = []
    all_vendors = get_all_vendors()
    vendor_map = {v["id"]: v for v in all_vendors}

    for bp in baseline_products:
        bp_price = float(bp["unit_price"]) if bp.get("unit_price") else None
        if not bp_price or bp_price <= 0:
            continue

        # Extract original product name from description if available
        # Amazon products have "Amazon match for: <original name>"
        search_name = ""
        desc = bp.get("description") or ""
        if desc.startswith("Amazon match for:"):
            search_name = desc.replace("Amazon match for:", "").strip()[:80]
        else:
            search_name = bp.get("product_name", "")

        if not search_name or len(search_name) < 5:
            continue

        # Take first 3 significant words for matching
        words = re.sub(r'[^a-zA-Z0-9\s]', '', search_name).split()
        keywords = [w for w in words if len(w) > 2][:4]
        if len(keywords) < 2:
            continue

        search_term = "%".join(keywords[:3])

        # Find matching products from OTHER vendors
        mq = supabase.table("products").select(
            "id, sku, product_name, brand, category, unit_price, case_price, "
            "pack_size_raw, image_url, product_url, in_stock, vendor_id"
        ).neq("vendor_id", baseline_vid).not_.is_("unit_price", "null")

        # Chain ilike for each keyword
        for kw in keywords[:3]:
            mq = mq.ilike("product_name", f"%{kw}%")

        matches = mq.limit(10).execute().data

        if not matches:
            continue

        # Build deal entry
        other_vendors = []
        for m in matches:
            m_price = float(m["unit_price"]) if m.get("unit_price") else None
            if not m_price or m_price <= 0:
                continue
            v_info = vendor_map.get(m["vendor_id"], {})
            diff = round(m_price - bp_price, 2)
            pct = round((diff / bp_price) * 100, 1) if bp_price else 0

            other_vendors.append({
                "vendor_name": v_info.get("name", "Unknown"),
                "vendor_slug": v_info.get("slug", ""),
                "product_name": m.get("product_name"),
                "sku": m.get("sku"),
                "unit_price": m_price,
                "case_price": float(m["case_price"]) if m.get("case_price") else None,
                "pack_size": m.get("pack_size_raw"),
                "image_url": m.get("image_url"),
                "product_url": m.get("product_url"),
                "in_stock": m.get("in_stock"),
                "diff_vs_baseline": diff,
                "pct_vs_baseline": pct,
            })

        if not other_vendors:
            continue

        other_vendors.sort(key=lambda x: x["unit_price"])
        best_other = other_vendors[0]
        best_savings_abs = round(bp_price - best_other["unit_price"], 2)
        best_savings_pct = round((best_savings_abs / bp_price) * 100, 1) if bp_price else 0

        deals.append({
            "baseline_product": bp.get("product_name"),
            "baseline_sku": bp.get("sku"),
            "baseline_price": bp_price,
            "baseline_image": bp.get("image_url"),
            "baseline_url": bp.get("product_url"),
            "baseline_in_stock": bp.get("in_stock"),
            "brand": bp.get("brand"),
            "category": bp.get("category"),
            "best_other_price": best_other["unit_price"],
            "best_other_vendor": best_other["vendor_name"],
            "best_other_vendor_slug": best_other["vendor_slug"],
            "best_savings_abs": best_savings_abs,
            "best_savings_pct": best_savings_pct,
            "all_vendors": other_vendors,
            "vendor_count": len(other_vendors),
        })

    # Sort
    if sort_by == "savings_abs":
        deals.sort(key=lambda d: d["best_savings_abs"], reverse=True)
    elif sort_by == "price":
        deals.sort(key=lambda d: d["baseline_price"])
    else:  # savings_pct
        deals.sort(key=lambda d: d["best_savings_pct"], reverse=True)

    deals = deals[:limit]

    # CSV export — flatten the data
    if not deals:
        return jsonify({"error": "No deals found for export"}), 404

    rows = []
    for d in deals:
        base_row = {
            "Baseline Product": d["baseline_product"],
            "Baseline SKU": d["baseline_sku"],
            "Baseline Price": d["baseline_price"],
            "Brand": d["brand"],
            "Category": d["category"],
            "Best Other Price": d["best_other_price"],
            "Best Other Vendor": d["best_other_vendor"],
            "Savings ($)": d["best_savings_abs"],
            "Savings (%)": d["best_savings_pct"],
            "# Vendors": d["vendor_count"],
            "Baseline URL": d.get("baseline_url", ""),
        }
        # Add each competing vendor as additional columns
        for i, v in enumerate(d.get("all_vendors", [])[:5]):
            base_row[f"Vendor {i+1}"] = v.get("vendor_name", "")
            base_row[f"Vendor {i+1} Price"] = v.get("unit_price", "")
            base_row[f"Vendor {i+1} Diff"] = v.get("diff_vs_baseline", "")
        rows.append(base_row)

    # Collect all field names
    all_fields = []
    for r in rows:
        for k in r.keys():
            if k not in all_fields:
                all_fields.append(k)

    buf = io.StringIO()
    writer = csv_mod.DictWriter(buf, fieldnames=all_fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)

    filename = f"scraper4000_deals_{baseline_slug}_{datetime.now().strftime('%Y%m%d')}.csv"
    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        download_name=filename,
        as_attachment=True,
        mimetype="text/csv",
    )


# ═══════════════════════════════════════════════════════════════════
# DATA QUALITY / COVERAGE STATS
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/data-quality")
@login_required
def data_quality():
    """
    Data quality and coverage statistics per vendor.
    Shows what % of products have UPC, pack size, price_per_oz, etc.
    """
    vendors = get_all_vendors()
    vendor_stats = []

    for v in vendors:
        vid = v["id"]
        prods = supabase.table("products").select(
            "upc, pack_size_raw, pack_count, unit_size, unit_measure, "
            "price_per_oz, unit_price, case_price, brand, category, "
            "image_url, product_url, in_stock, has_promo, description"
        ).eq("vendor_id", vid).limit(5000).execute().data

        total = len(prods)
        if total == 0:
            continue

        has_upc = sum(1 for p in prods if p.get("upc") and str(p["upc"]).strip())
        has_pack = sum(1 for p in prods if p.get("pack_size_raw") and str(p["pack_size_raw"]).strip())
        has_structured_pack = sum(1 for p in prods if p.get("pack_count") and p.get("unit_size"))
        has_price_per_oz = sum(1 for p in prods if p.get("price_per_oz"))
        has_unit_price = sum(1 for p in prods if p.get("unit_price"))
        has_case_price = sum(1 for p in prods if p.get("case_price"))
        has_brand = sum(1 for p in prods if p.get("brand") and str(p["brand"]).strip())
        has_category = sum(1 for p in prods if p.get("category") and str(p["category"]).strip())
        has_image = sum(1 for p in prods if p.get("image_url") and str(p["image_url"]).strip())
        has_url = sum(1 for p in prods if p.get("product_url") and str(p["product_url"]).strip())
        has_description = sum(1 for p in prods if p.get("description") and str(p["description"]).strip())
        has_promo = sum(1 for p in prods if p.get("has_promo"))
        in_stock_count = sum(1 for p in prods if p.get("in_stock"))

        def pct(n):
            return round((n / total) * 100, 1) if total > 0 else 0

        quality_score = round(
            pct(has_upc) * 0.25 +
            pct(has_pack) * 0.15 +
            pct(has_unit_price) * 0.20 +
            pct(has_brand) * 0.10 +
            pct(has_category) * 0.10 +
            pct(has_image) * 0.10 +
            pct(has_url) * 0.10
        , 1)

        vendor_stats.append({
            "vendor_name": v["name"],
            "vendor_slug": v["slug"],
            "total_products": total,
            "quality_score": quality_score,
            "coverage": {
                "upc": {"count": has_upc, "pct": pct(has_upc)},
                "pack_size_raw": {"count": has_pack, "pct": pct(has_pack)},
                "structured_pack": {"count": has_structured_pack, "pct": pct(has_structured_pack)},
                "price_per_oz": {"count": has_price_per_oz, "pct": pct(has_price_per_oz)},
                "unit_price": {"count": has_unit_price, "pct": pct(has_unit_price)},
                "case_price": {"count": has_case_price, "pct": pct(has_case_price)},
                "brand": {"count": has_brand, "pct": pct(has_brand)},
                "category": {"count": has_category, "pct": pct(has_category)},
                "image": {"count": has_image, "pct": pct(has_image)},
                "url": {"count": has_url, "pct": pct(has_url)},
                "description": {"count": has_description, "pct": pct(has_description)},
                "in_stock": {"count": in_stock_count, "pct": pct(in_stock_count)},
                "has_promo": {"count": has_promo, "pct": pct(has_promo)},
            }
        })

    vendor_stats.sort(key=lambda x: x["quality_score"], reverse=True)

    total_all = sum(v["total_products"] for v in vendor_stats)
    avg_quality = round(
        sum(v["quality_score"] * v["total_products"] for v in vendor_stats) / total_all, 1
    ) if total_all > 0 else 0

    return jsonify({
        "vendors": vendor_stats,
        "overall": {
            "total_products": total_all,
            "avg_quality_score": avg_quality,
            "vendor_count": len(vendor_stats),
        }
    })


# ═══════════════════════════════════════════════════════════════════
# SERVE FRONTEND
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/v2/price-sparklines")
@login_required
def price_sparklines():
    """
    Get last 10 price snapshots for a list of product IDs.
    Used for inline sparkline charts on deal cards.
    Query params:
      ids — comma-separated product IDs (UUIDs)
    """
    product_ids = request.args.get("ids", "").split(",")
    product_ids = [pid.strip() for pid in product_ids if pid.strip()]

    if not product_ids or len(product_ids) > 50:
        return jsonify({})

    results = {}
    for pid in product_ids:
        data = supabase.table("product_history").select(
            "unit_price, captured_at"
        ).eq("product_id", pid).order("captured_at", desc=True).limit(10).execute().data

        if data:
            data.reverse()  # oldest first
            results[pid] = [
                {"price": float(d["unit_price"]) if d.get("unit_price") else None,
                 "date": d.get("captured_at")}
                for d in data
            ]

    return jsonify(results)


@app.route("/api/v2/vendor-radar")
@login_required
def vendor_radar():
    """
    Multi-dimensional vendor comparison data for radar chart.
    Dimensions: Product Count, Avg Price Competitiveness, Data Quality,
                Price Coverage, UPC Coverage, Stock Rate.
    Normalizes all values to 0-100 scale.
    """
    vendors = get_all_vendors()
    vendor_data = []

    for v in vendors:
        vid = v["id"]
        prods = supabase.table("products").select(
            "unit_price, upc, pack_size_raw, brand, image_url, in_stock"
        ).eq("vendor_id", vid).limit(5000).execute().data

        total = len(prods)
        if total == 0:
            continue

        prices = [float(p["unit_price"]) for p in prods if p.get("unit_price")]
        avg_price = sum(prices) / len(prices) if prices else 0
        has_upc = sum(1 for p in prods if p.get("upc") and str(p["upc"]).strip())
        has_brand = sum(1 for p in prods if p.get("brand") and str(p["brand"]).strip())
        has_image = sum(1 for p in prods if p.get("image_url") and str(p["image_url"]).strip())
        has_pack = sum(1 for p in prods if p.get("pack_size_raw") and str(p["pack_size_raw"]).strip())
        in_stock = sum(1 for p in prods if p.get("in_stock"))

        vendor_data.append({
            "vendor": v["name"],
            "slug": v["slug"],
            "product_count": total,
            "avg_price": avg_price,
            "upc_pct": round(has_upc / total * 100, 1),
            "brand_pct": round(has_brand / total * 100, 1),
            "image_pct": round(has_image / total * 100, 1),
            "pack_pct": round(has_pack / total * 100, 1),
            "stock_pct": round(in_stock / total * 100, 1),
        })

    if not vendor_data:
        return jsonify({"vendors": [], "dimensions": []})

    # Normalize product count to 0-100
    max_count = max(v["product_count"] for v in vendor_data) or 1

    # Normalize avg_price (lower is better → invert)
    all_avg = [v["avg_price"] for v in vendor_data if v["avg_price"] > 0]
    max_avg = max(all_avg) if all_avg else 1

    for v in vendor_data:
        v["scores"] = {
            "Catalog Size": round(v["product_count"] / max_count * 100, 1),
            "Price Competitiveness": round((1 - v["avg_price"] / max_avg) * 100, 1) if max_avg > 0 else 50,
            "UPC Coverage": v["upc_pct"],
            "Brand Data": v["brand_pct"],
            "Pack Info": v["pack_pct"],
            "In Stock Rate": v["stock_pct"],
        }

    dimensions = ["Catalog Size", "Price Competitiveness", "UPC Coverage",
                   "Brand Data", "Pack Info", "In Stock Rate"]

    return jsonify({
        "vendors": vendor_data,
        "dimensions": dimensions,
    })


@app.route("/api/v2/ai-insights")
@login_required
def ai_insights():
    """
    Auto-generated plain-english insights about the data.
    No actual AI — just smart heuristics to surface key findings.
    """
    try:
        return _ai_insights_impl()
    except Exception as e:
        print(f"[ai-insights] Error: {e}")
        return jsonify({"insights": [{"type": "info", "icon": "⚠️", "title": "Temporarily Unavailable",
                                       "text": "Insights will refresh shortly. Try refreshing the dashboard."}]})


def _ai_insights_impl():
    vendors = get_all_vendors()
    insights = []

    # Gather vendor stats
    vendor_stats = []
    for v in vendors:
        vid = v["id"]
        cnt = supabase.table("products").select(
            "id", count="exact"
        ).eq("vendor_id", vid).execute()
        product_count = cnt.count or 0
        if product_count == 0:
            continue

        prods = supabase.table("products").select(
            "unit_price, price_changed, in_stock, upc, brand"
        ).eq("vendor_id", vid).limit(5000).execute().data

        prices = [float(p["unit_price"]) for p in prods if p.get("unit_price")]
        avg_price = sum(prices) / len(prices) if prices else 0
        has_upc = sum(1 for p in prods if p.get("upc") and str(p["upc"]).strip())
        price_changes = sum(1 for p in prods if p.get("price_changed"))
        in_stock = sum(1 for p in prods if p.get("in_stock"))

        vendor_stats.append({
            "name": v["name"],
            "slug": v["slug"],
            "product_count": product_count,
            "avg_price": avg_price,
            "upc_pct": round(has_upc / product_count * 100, 1),
            "price_changes": price_changes,
            "stock_pct": round(in_stock / product_count * 100, 1),
        })

    if not vendor_stats:
        return jsonify({"insights": [{"type": "info", "icon": "📊", "title": "No Data Yet",
                                       "text": "Start scraping vendors to see insights."}]})

    # Sort vendors by product count
    vendor_stats.sort(key=lambda x: x["product_count"], reverse=True)

    # Insight 1: Biggest catalog
    biggest = vendor_stats[0]
    insights.append({
        "type": "catalog",
        "icon": "📦",
        "title": "Largest Catalog",
        "text": f"{biggest['name']} leads with {biggest['product_count']:,} products — "
                f"{biggest['product_count'] - vendor_stats[1]['product_count']:,} more than "
                f"{vendor_stats[1]['name']}." if len(vendor_stats) > 1 else
                f"{biggest['name']} has {biggest['product_count']:,} products in the database.",
        "metric": biggest['product_count'],
    })

    # Insight 2: Best data quality (UPC coverage)
    best_upc = max(vendor_stats, key=lambda x: x["upc_pct"])
    worst_upc = min(vendor_stats, key=lambda x: x["upc_pct"])
    insights.append({
        "type": "quality",
        "icon": "🎯",
        "title": "Data Quality Leader",
        "text": f"{best_upc['name']} has the best UPC coverage at {best_upc['upc_pct']}%. "
                f"{worst_upc['name']} lags at {worst_upc['upc_pct']}% — "
                f"improving this would unlock more apples-to-apples comparisons.",
        "metric": best_upc['upc_pct'],
    })

    # Insight 3: Price competitiveness
    if len(vendor_stats) > 1:
        cheapest = min(vendor_stats, key=lambda x: x["avg_price"] if x["avg_price"] > 0 else float("inf"))
        priciest = max(vendor_stats, key=lambda x: x["avg_price"])
        if cheapest["avg_price"] > 0 and priciest["avg_price"] > 0:
            spread_pct = round((priciest["avg_price"] - cheapest["avg_price"]) / cheapest["avg_price"] * 100, 1)
            insights.append({
                "type": "pricing",
                "icon": "💰",
                "title": "Price Spread",
                "text": f"Average prices range from ${cheapest['avg_price']:.2f} ({cheapest['name']}) "
                        f"to ${priciest['avg_price']:.2f} ({priciest['name']}) — a {spread_pct}% spread. "
                        f"This signals real savings opportunities for buyers who compare.",
                "metric": spread_pct,
            })

    # Insight 4: Price changes activity
    total_changes = sum(v["price_changes"] for v in vendor_stats)
    if total_changes > 0:
        most_volatile = max(vendor_stats, key=lambda x: x["price_changes"])
        insights.append({
            "type": "changes",
            "icon": "⚡",
            "title": "Market Activity",
            "text": f"{total_changes} price changes detected across all vendors. "
                    f"{most_volatile['name']} is most active with {most_volatile['price_changes']} changes — "
                    f"prices are moving, so timing matters.",
            "metric": total_changes,
        })

    # Insight 5: Stock availability
    avg_stock = round(sum(v["stock_pct"] for v in vendor_stats) / len(vendor_stats), 1)
    best_stock = max(vendor_stats, key=lambda x: x["stock_pct"])
    insights.append({
        "type": "stock",
        "icon": "📈",
        "title": "Stock Availability",
        "text": f"Average in-stock rate is {avg_stock}% across all vendors. "
                f"{best_stock['name']} leads with {best_stock['stock_pct']}% of items in stock.",
        "metric": avg_stock,
    })

    # Insight 6: Recommendation
    total_products = sum(v["product_count"] for v in vendor_stats)
    insights.append({
        "type": "recommendation",
        "icon": "🚀",
        "title": "Next Step",
        "text": f"With {total_products:,} products across {len(vendor_stats)} vendors, "
                f"you're building a strong price intelligence database. "
                f"Focus on improving UPC coverage for {worst_upc['name']} to unlock "
                f"the most new cross-vendor comparisons.",
        "metric": total_products,
    })

    return jsonify({"insights": insights, "vendor_count": len(vendor_stats),
                     "total_products": total_products})


@app.route("/api/v2/savings-summary")
@login_required
def savings_summary():
    """
    High-level savings summary for the hero stat.
    Shows total potential savings if buyer used best vendor for every product.
    """
    try:
        return _savings_summary_impl()
    except Exception as e:
        print(f"[savings-summary] Error: {e}")
        return jsonify({"total_savings": 0, "product_count": 0, "products_compared": 0,
                         "products_with_savings": 0, "total_baseline_spend": 0,
                         "savings_pct": 0, "baseline_vendor": "amazon",
                         "baseline_vendor_name": "Amazon"})


def _savings_summary_impl():
    baseline_slug = request.args.get("baseline", "amazon")
    baseline_vendor = get_vendor_by_slug(baseline_slug)
    if not baseline_vendor:
        return jsonify({"total_savings": 0, "product_count": 0})

    baseline_vid = baseline_vendor["id"]

    bq = supabase.table("products").select(
        "id, product_name, unit_price, description"
    ).eq("vendor_id", baseline_vid).not_.is_("unit_price", "null").limit(500)
    baseline_products = bq.execute().data

    total_savings = 0
    products_with_savings = 0
    products_compared = 0
    total_baseline_spend = 0

    all_vendors = get_all_vendors()
    vendor_map = {v["id"]: v for v in all_vendors}

    for bp in baseline_products:
        bp_price = float(bp["unit_price"]) if bp.get("unit_price") else None
        if not bp_price or bp_price <= 0:
            continue

        total_baseline_spend += bp_price

        search_name = ""
        desc = bp.get("description") or ""
        if desc.startswith("Amazon match for:"):
            search_name = desc.replace("Amazon match for:", "").strip()[:80]
        else:
            search_name = bp.get("product_name", "")

        if not search_name or len(search_name) < 5:
            continue

        words = re.sub(r'[^a-zA-Z0-9\s]', '', search_name).split()
        keywords = [w for w in words if len(w) > 2][:4]
        if len(keywords) < 2:
            continue

        mq = supabase.table("products").select(
            "unit_price"
        ).neq("vendor_id", baseline_vid).not_.is_("unit_price", "null")

        for kw in keywords[:3]:
            mq = mq.ilike("product_name", f"%{kw}%")

        matches = mq.limit(5).execute().data

        if not matches:
            continue

        products_compared += 1
        best_other = min(
            (float(m["unit_price"]) for m in matches if m.get("unit_price")),
            default=bp_price
        )

        if best_other < bp_price:
            savings = bp_price - best_other
            total_savings += savings
            products_with_savings += 1

    savings_pct = round((total_savings / total_baseline_spend) * 100, 1) if total_baseline_spend > 0 else 0

    return jsonify({
        "total_savings": round(total_savings, 2),
        "products_compared": products_compared,
        "products_with_savings": products_with_savings,
        "total_baseline_spend": round(total_baseline_spend, 2),
        "savings_pct": savings_pct,
        "baseline_vendor": baseline_slug,
        "baseline_vendor_name": baseline_vendor.get("name", baseline_slug),
    })


@app.route("/api/v2/market-pulse")
@login_required
def market_pulse():
    """
    Real-time market activity feed.
    Returns recent events: price changes, new products, scrape completions.
    Used for the live-ticker on the dashboard.
    """
    limit = int(request.args.get("limit", 20))
    events = []

    # Recent price changes
    price_changes = supabase.table("products").select(
        "product_name, brand, unit_price, last_price, price_changed, "
        "vendor_id, vendors(name, slug), last_seen_at"
    ).eq("price_changed", True).order("last_seen_at", desc=True).limit(limit).execute().data

    for p in price_changes:
        old_p = float(p.get("last_price") or 0)
        new_p = float(p.get("unit_price") or 0)
        if old_p <= 0 or new_p <= 0:
            continue
        diff = new_p - old_p
        pct = round((diff / old_p) * 100, 1) if old_p else 0
        events.append({
            "type": "price_change",
            "icon": "📉" if diff < 0 else "📈",
            "title": f"Price {'drop' if diff < 0 else 'increase'}: {p.get('product_name', '')[:50]}",
            "detail": f"{'▼' if diff < 0 else '▲'} ${abs(diff):.2f} ({abs(pct)}%) at {p.get('vendors', {}).get('name', 'Unknown')}",
            "vendor": p.get("vendors", {}).get("name", ""),
            "timestamp": p.get("last_seen_at"),
            "severity": "positive" if diff < 0 else "negative",
        })

    # Recent scrape completions
    runs = supabase.table("scrape_runs").select(
        "status, products_found, products_new, price_changes, started_at, "
        "duration_secs, vendors(name, slug)"
    ).order("started_at", desc=True).limit(10).execute().data

    for r in runs:
        vendor_name = r.get("vendors", {}).get("name", "Unknown")
        events.append({
            "type": "scrape_complete",
            "icon": "🤖",
            "title": f"Scrape completed: {vendor_name}",
            "detail": f"Found {r.get('products_found', 0)} products, {r.get('products_new', 0)} new, {r.get('price_changes', 0)} price changes",
            "vendor": vendor_name,
            "timestamp": r.get("started_at"),
            "severity": "info",
        })

    # Sort all events by timestamp
    events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return jsonify({"events": events[:limit]})


@app.route("/api/v2/category-savings")
@login_required
def category_savings():
    """
    Savings potential broken down by product category.
    """
    try:
        return _category_savings_impl()
    except Exception as e:
        print(f"[category-savings] Error: {e}")
        return jsonify({"categories": [], "baseline_vendor": "amazon"})


def _category_savings_impl():
    """
    Shows which categories have the most cross-vendor savings opportunities.
    """
    baseline_slug = request.args.get("baseline", "amazon")
    baseline_vendor = get_vendor_by_slug(baseline_slug)
    if not baseline_vendor:
        return jsonify({"categories": []})

    baseline_vid = baseline_vendor["id"]

    # Get baseline products grouped by category
    bq = supabase.table("products").select(
        "id, product_name, brand, category, unit_price, description"
    ).eq("vendor_id", baseline_vid).not_.is_("unit_price", "null").not_.is_("category", "null").limit(500)
    baseline_products = bq.execute().data

    category_stats = {}
    for bp in baseline_products:
        cat = bp.get("category") or "Uncategorized"
        bp_price = float(bp["unit_price"]) if bp.get("unit_price") else None
        if not bp_price or bp_price <= 0:
            continue

        # Extract search name
        search_name = ""
        desc = bp.get("description") or ""
        if desc.startswith("Amazon match for:"):
            search_name = desc.replace("Amazon match for:", "").strip()[:80]
        else:
            search_name = bp.get("product_name", "")

        if not search_name or len(search_name) < 5:
            continue

        words = re.sub(r'[^a-zA-Z0-9\s]', '', search_name).split()
        keywords = [w for w in words if len(w) > 2][:4]
        if len(keywords) < 2:
            continue

        mq = supabase.table("products").select(
            "unit_price"
        ).neq("vendor_id", baseline_vid).not_.is_("unit_price", "null")
        for kw in keywords[:3]:
            mq = mq.ilike("product_name", f"%{kw}%")
        matches = mq.limit(5).execute().data

        if not matches:
            continue

        if cat not in category_stats:
            category_stats[cat] = {
                "category": cat,
                "products_compared": 0,
                "products_with_savings": 0,
                "total_savings": 0,
                "total_baseline_spend": 0,
            }

        category_stats[cat]["products_compared"] += 1
        category_stats[cat]["total_baseline_spend"] += bp_price

        best_other = min(
            (float(m["unit_price"]) for m in matches if m.get("unit_price")),
            default=bp_price
        )
        if best_other < bp_price:
            savings = bp_price - best_other
            category_stats[cat]["total_savings"] += savings
            category_stats[cat]["products_with_savings"] += 1

    # Calculate savings percentages and sort
    result = []
    for cat, stats in category_stats.items():
        if stats["products_compared"] > 0:
            stats["savings_pct"] = round(
                (stats["total_savings"] / stats["total_baseline_spend"]) * 100, 1
            ) if stats["total_baseline_spend"] > 0 else 0
            stats["total_savings"] = round(stats["total_savings"], 2)
            stats["total_baseline_spend"] = round(stats["total_baseline_spend"], 2)
            result.append(stats)

    result.sort(key=lambda x: x["total_savings"], reverse=True)
    return jsonify({"categories": result[:15], "baseline_vendor": baseline_slug})


@app.route("/api/v2/vendor-leaderboard")
@login_required
def vendor_leaderboard():
    """
    Vendor leaderboard with composite scoring across multiple dimensions.
    Returns ranked vendors with breakdowns.
    """
    vendors = get_all_vendors()
    leaderboard = []

    for v in vendors:
        vid = v["id"]
        prods = supabase.table("products").select(
            "unit_price, upc, pack_size_raw, brand, image_url, in_stock, category"
        ).eq("vendor_id", vid).limit(5000).execute().data

        total = len(prods)
        if total == 0:
            continue

        prices = [float(p["unit_price"]) for p in prods if p.get("unit_price")]
        avg_price = sum(prices) / len(prices) if prices else 0
        has_upc = sum(1 for p in prods if p.get("upc") and str(p["upc"]).strip())
        has_brand = sum(1 for p in prods if p.get("brand") and str(p["brand"]).strip())
        has_image = sum(1 for p in prods if p.get("image_url") and str(p["image_url"]).strip())
        in_stock = sum(1 for p in prods if p.get("in_stock"))
        categories = set(p.get("category") for p in prods if p.get("category"))

        # Composite scores
        catalog_score = min(100, total / 50 * 100)  # Normalize to 100 at 5000 products
        data_quality = round(
            (has_upc / total * 25 + has_brand / total * 25 +
             has_image / total * 25 + (len(prices) / total) * 25), 1
        ) if total > 0 else 0
        stock_health = round(in_stock / total * 100, 1) if total > 0 else 0
        diversity = min(100, len(categories) / 20 * 100)

        composite = round(
            catalog_score * 0.25 + data_quality * 0.30 +
            stock_health * 0.25 + diversity * 0.20, 1
        )

        leaderboard.append({
            "rank": 0,
            "vendor": v["name"],
            "slug": v["slug"],
            "composite_score": composite,
            "product_count": total,
            "avg_price": round(avg_price, 2),
            "categories": len(categories),
            "scores": {
                "catalog": round(catalog_score, 1),
                "data_quality": data_quality,
                "stock_health": stock_health,
                "diversity": round(diversity, 1),
            },
            "upc_pct": round(has_upc / total * 100, 1),
            "stock_pct": stock_health,
        })

    # Sort by composite score and assign ranks
    leaderboard.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, entry in enumerate(leaderboard):
        entry["rank"] = i + 1

    return jsonify({"leaderboard": leaderboard})


@app.route("/dashboard")
@app.route("/dashboard/")
def dashboard_page():
    return send_file("static/dashboard.html")


@app.route("/")
def index():
    return send_file("static/dashboard.html")


if __name__ == "__main__":
    app.run(debug=True, port=5051)
