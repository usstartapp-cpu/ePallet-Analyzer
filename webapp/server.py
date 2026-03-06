"""
ePallet Data Analyzer — Flask Backend
Serves product data, filtering, analytics, and Excel export
"""

import os
import io
import csv
import json
import math
from functools import wraps
from flask import Flask, request, jsonify, send_file, session
from flask_cors import CORS
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = "epallet-scraper4000-secret-key-change-in-prod"
CORS(app, supports_credentials=True)

# ── Config ──────────────────────────────────────────────────────────────────
CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "epallet_dry_products.csv")
USERS = {
    "admin": "epallet2026",
    "michael": "LAFoods2026",
}

# ── Load data once at startup ──────────────────────────────────────────────
def load_data():
    df = pd.read_csv(CSV_PATH)
    # Clean numeric columns
    for col in ["delivered_price", "delivered_case_price", "price_per_unit", "price_per_oz",
                "cases_per_pallet", "lead_time_days", "min_pallet_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    # Parse pack_size_raw into count and unit_size
    def parse_pack(raw):
        if pd.isna(raw) or not isinstance(raw, str):
            return pd.Series({"pack_count": None, "unit_size": None, "unit_measure": None})
        parts = raw.strip().split("/")
        if len(parts) >= 2:
            try:
                pack_count = int(parts[0].strip())
            except ValueError:
                try:
                    pack_count = float(parts[0].strip())
                except ValueError:
                    pack_count = None
            unit_part = "/".join(parts[1:]).strip()
            # Extract numeric and unit
            import re
            m = re.match(r"([\d.]+)\s*(.*)", unit_part)
            if m:
                try:
                    unit_size = float(m.group(1))
                except ValueError:
                    unit_size = None
                unit_measure = m.group(2).strip() if m.group(2) else ""
            else:
                unit_size = None
                unit_measure = unit_part
            return pd.Series({"pack_count": pack_count, "unit_size": unit_size, "unit_measure": unit_measure})
        return pd.Series({"pack_count": None, "unit_size": None, "unit_measure": None})
    
    parsed = df["pack_size_raw"].apply(parse_pack)
    df = pd.concat([df, parsed], axis=1)
    
    # Compute total weight/volume
    df["total_weight"] = df["pack_count"] * df["unit_size"]
    
    # Fill NaN for JSON serialization
    df = df.fillna("")
    
    return df

print("Loading data...")
DF = load_data()
print(f"Loaded {len(DF)} products across {DF['category'].nunique()} categories from {DF['manufacturer'].nunique()} manufacturers")

# ── Auth decorator ─────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Auth routes ────────────────────────────────────────────────────────────
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    if username in USERS and USERS[username] == password:
        session["logged_in"] = True
        session["username"] = username
        return jsonify({"ok": True, "username": username})
    return jsonify({"error": "Invalid credentials"}), 401

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me")
def me():
    if session.get("logged_in"):
        return jsonify({"logged_in": True, "username": session.get("username")})
    return jsonify({"logged_in": False}), 401

# ── Data routes ────────────────────────────────────────────────────────────
@app.route("/api/summary")
@login_required
def summary():
    """Dashboard summary stats"""
    df = DF
    valid_prices = df[df["delivered_price"] != ""]["delivered_price"]
    valid_unit = df[df["price_per_unit"] != ""]["price_per_unit"]
    
    return jsonify({
        "total_products": len(df),
        "total_categories": int(df["category"].nunique()),
        "total_manufacturers": int(df["manufacturer"].nunique()),
        "avg_delivered_price": round(float(valid_prices.mean()), 2) if len(valid_prices) else 0,
        "avg_unit_price": round(float(valid_unit.mean()), 2) if len(valid_unit) else 0,
        "min_unit_price": round(float(valid_unit.min()), 2) if len(valid_unit) else 0,
        "max_unit_price": round(float(valid_unit.max()), 2) if len(valid_unit) else 0,
        "total_with_promo": int((df["has_promo"] == "Yes").sum()),
        "total_mixed_pallet": int((df["mixed_pallet"] == "Yes").sum()),
    })

@app.route("/api/filters")
@login_required
def filters():
    """Get all filter options"""
    df = DF
    categories = sorted(df["category"].unique().tolist())
    manufacturers = sorted(df["manufacturer"].unique().tolist())
    lead_times = sorted([x for x in df["lead_time_days"].unique().tolist() if x != ""])
    
    return jsonify({
        "categories": categories,
        "manufacturers": manufacturers,
        "lead_times": lead_times,
    })

def apply_filters(df, params):
    """Apply query params as filters to the dataframe"""
    # Category filter
    cats = params.get("category")
    if cats:
        cat_list = cats.split(",")
        df = df[df["category"].isin(cat_list)]
    
    # Manufacturer filter
    mfrs = params.get("manufacturer")
    if mfrs:
        mfr_list = mfrs.split(",")
        df = df[df["manufacturer"].isin(mfr_list)]
    
    # Search
    search = params.get("search", "").strip()
    if search:
        mask = (
            df["product"].str.contains(search, case=False, na=False) |
            df["manufacturer"].str.contains(search, case=False, na=False) |
            df["description"].str.contains(search, case=False, na=False) |
            df["upc"].astype(str).str.contains(search, case=False, na=False)
        )
        df = df[mask]
    
    # Price range
    min_price = params.get("min_price")
    max_price = params.get("max_price")
    if min_price:
        df = df[df["price_per_unit"] != ""]
        df = df[df["price_per_unit"].astype(float) >= float(min_price)]
    if max_price:
        df = df[df["price_per_unit"] != ""]
        df = df[df["price_per_unit"].astype(float) <= float(max_price)]
    
    # Promo only
    if params.get("promo_only") == "true":
        df = df[df["has_promo"] == "Yes"]
    
    # Mixed pallet only
    if params.get("mixed_pallet_only") == "true":
        df = df[df["mixed_pallet"] == "Yes"]
    
    # Available only
    if params.get("available_only") == "true":
        df = df[df["available"] == "Yes"]
    
    return df

@app.route("/api/products")
@login_required
def products():
    """Paginated, filtered, sorted product list"""
    df = apply_filters(DF.copy(), request.args)
    
    # Sorting
    sort_by = request.args.get("sort_by", "product")
    sort_dir = request.args.get("sort_dir", "asc")
    
    if sort_by in df.columns:
        ascending = sort_dir == "asc"
        # For numeric columns, ensure proper sorting
        if sort_by in ["delivered_price", "delivered_case_price", "price_per_unit", 
                        "price_per_oz", "cases_per_pallet", "lead_time_days", "total_weight"]:
            df[sort_by] = pd.to_numeric(df[sort_by], errors="coerce")
        df = df.sort_values(by=sort_by, ascending=ascending, na_position="last")
    
    total = len(df)
    
    # Pagination
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    start = (page - 1) * per_page
    end = start + per_page
    
    page_df = df.iloc[start:end]
    
    # Convert to records, replacing NaN
    records = page_df.fillna("").to_dict(orient="records")
    
    return jsonify({
        "products": records,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": math.ceil(total / per_page),
    })

# ── Analytics routes ───────────────────────────────────────────────────────
@app.route("/api/analytics/by-category")
@login_required
def analytics_by_category():
    """Price analytics grouped by category"""
    df = apply_filters(DF.copy(), request.args)
    df_valid = df[df["price_per_unit"] != ""].copy()
    df_valid["price_per_unit"] = df_valid["price_per_unit"].astype(float)
    df_valid["delivered_price"] = pd.to_numeric(df_valid["delivered_price"], errors="coerce")
    
    grouped = df_valid.groupby("category").agg(
        count=("product", "count"),
        avg_unit_price=("price_per_unit", "mean"),
        min_unit_price=("price_per_unit", "min"),
        max_unit_price=("price_per_unit", "max"),
        median_unit_price=("price_per_unit", "median"),
        avg_delivered=("delivered_price", "mean"),
        total_value=("delivered_price", "sum"),
    ).round(2).reset_index()
    
    return jsonify(grouped.to_dict(orient="records"))

@app.route("/api/analytics/by-manufacturer")
@login_required
def analytics_by_manufacturer():
    """Price analytics grouped by manufacturer"""
    df = apply_filters(DF.copy(), request.args)
    df_valid = df[df["price_per_unit"] != ""].copy()
    df_valid["price_per_unit"] = df_valid["price_per_unit"].astype(float)
    df_valid["delivered_price"] = pd.to_numeric(df_valid["delivered_price"], errors="coerce")
    
    grouped = df_valid.groupby("manufacturer").agg(
        count=("product", "count"),
        avg_unit_price=("price_per_unit", "mean"),
        min_unit_price=("price_per_unit", "min"),
        max_unit_price=("price_per_unit", "max"),
        avg_delivered=("delivered_price", "mean"),
        categories=("category", "nunique"),
    ).round(2).reset_index()
    
    # Sort by count descending, take top N
    limit = int(request.args.get("limit", 30))
    sort = request.args.get("sort", "count")
    grouped = grouped.sort_values(by=sort, ascending=False).head(limit)
    
    return jsonify(grouped.to_dict(orient="records"))

@app.route("/api/analytics/price-distribution")
@login_required
def price_distribution():
    """Price distribution histogram data"""
    df = apply_filters(DF.copy(), request.args)
    df_valid = df[df["price_per_unit"] != ""].copy()
    df_valid["price_per_unit"] = df_valid["price_per_unit"].astype(float)
    
    # Remove extreme outliers for better visualization
    q99 = df_valid["price_per_unit"].quantile(0.99)
    df_clipped = df_valid[df_valid["price_per_unit"] <= q99]
    
    bins = int(request.args.get("bins", 30))
    counts, edges = pd.cut(df_clipped["price_per_unit"], bins=bins, retbins=True)
    hist = df_clipped.groupby(counts, observed=True).size().reset_index(name="count")
    
    result = []
    for i, row in hist.iterrows():
        interval = row.iloc[0]
        result.append({
            "bin_start": round(interval.left, 2),
            "bin_end": round(interval.right, 2),
            "label": f"${interval.left:.2f}-${interval.right:.2f}",
            "count": int(row["count"]),
        })
    
    return jsonify(result)

@app.route("/api/analytics/price-vs-volume")
@login_required
def price_vs_volume():
    """Scatter plot data: unit price vs total weight, by category"""
    df = apply_filters(DF.copy(), request.args)
    df_valid = df[(df["price_per_unit"] != "") & (df["total_weight"] != "")].copy()
    df_valid["price_per_unit"] = df_valid["price_per_unit"].astype(float)
    df_valid["total_weight"] = df_valid["total_weight"].astype(float)
    
    # Sample if too many points
    if len(df_valid) > 2000:
        df_valid = df_valid.sample(2000, random_state=42)
    
    records = df_valid[["product", "manufacturer", "category", "price_per_unit", 
                         "total_weight", "unit_measure", "pack_size_raw"]].to_dict(orient="records")
    return jsonify(records)

@app.route("/api/analytics/lead-time")
@login_required
def lead_time_analysis():
    """Lead time distribution by category"""
    df = apply_filters(DF.copy(), request.args)
    df_valid = df[df["lead_time_days"] != ""].copy()
    df_valid["lead_time_days"] = df_valid["lead_time_days"].astype(int)
    
    grouped = df_valid.groupby(["category", "lead_time_days"]).size().reset_index(name="count")
    return jsonify(grouped.to_dict(orient="records"))

@app.route("/api/analytics/top-value")
@login_required
def top_value_products():
    """Top/Bottom products by various metrics"""
    df = apply_filters(DF.copy(), request.args)
    metric = request.args.get("metric", "price_per_unit")
    direction = request.args.get("direction", "desc")
    limit = int(request.args.get("limit", 20))
    
    df_valid = df[df[metric] != ""].copy()
    df_valid[metric] = pd.to_numeric(df_valid[metric], errors="coerce")
    df_valid = df_valid.dropna(subset=[metric])
    
    ascending = direction == "asc"
    df_sorted = df_valid.sort_values(by=metric, ascending=ascending).head(limit)
    
    cols = ["product", "manufacturer", "category", "delivered_price", "price_per_unit",
            "price_per_oz", "pack_size_raw", "cases_per_pallet", "total_weight"]
    return jsonify(df_sorted[cols].fillna("").to_dict(orient="records"))

@app.route("/api/analytics/category-manufacturer-matrix")
@login_required  
def category_manufacturer_matrix():
    """Heatmap data: categories vs top manufacturers"""
    df = apply_filters(DF.copy(), request.args)
    
    # Get top N manufacturers by product count
    limit = int(request.args.get("limit", 15))
    top_mfrs = df["manufacturer"].value_counts().head(limit).index.tolist()
    
    df_filtered = df[df["manufacturer"].isin(top_mfrs)]
    
    matrix = df_filtered.groupby(["category", "manufacturer"]).agg(
        count=("product", "count"),
        avg_price=("price_per_unit", lambda x: round(pd.to_numeric(x, errors="coerce").mean(), 2))
    ).reset_index()
    
    return jsonify({
        "data": matrix.fillna(0).to_dict(orient="records"),
        "categories": sorted(df_filtered["category"].unique().tolist()),
        "manufacturers": top_mfrs,
    })

@app.route("/api/analytics/compare-products")
@login_required
def compare_products():
    """Compare specific products side by side"""
    ids = request.args.get("product_ids", "")
    if not ids:
        return jsonify([])
    
    id_list = [int(x) for x in ids.split(",")]
    df_match = DF[DF["product_id"].isin(id_list)]
    return jsonify(df_match.fillna("").to_dict(orient="records"))

# ── Export routes ──────────────────────────────────────────────────────────
@app.route("/api/export/excel")
@login_required
def export_excel():
    """Export filtered data as formatted Excel"""
    df = apply_filters(DF.copy(), request.args)
    
    # Sorting
    sort_by = request.args.get("sort_by", "category")
    sort_dir = request.args.get("sort_dir", "asc")
    if sort_by in df.columns:
        df = df.sort_values(by=sort_by, ascending=(sort_dir == "asc"), na_position="last")
    
    wb = Workbook()
    ws = wb.active
    ws.title = "ePallet Products"
    
    # Styles
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="1B3A5C", end_color="1B3A5C", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    currency_format = '#,##0.00'
    border = Border(
        left=Side(style="thin", color="D0D0D0"),
        right=Side(style="thin", color="D0D0D0"),
        top=Side(style="thin", color="D0D0D0"),
        bottom=Side(style="thin", color="D0D0D0"),
    )
    alt_fill = PatternFill(start_color="F2F6FA", end_color="F2F6FA", fill_type="solid")
    
    columns = [
        ("Category", "category", 22),
        ("Manufacturer", "manufacturer", 25),
        ("Product", "product", 45),
        ("Description", "description", 35),
        ("UPC", "upc", 16),
        ("Delivered Price", "delivered_price", 16),
        ("Case Price", "delivered_case_price", 14),
        ("Price/Unit", "price_per_unit", 12),
        ("Price/Oz", "price_per_oz", 11),
        ("Pack Size", "pack_size_raw", 14),
        ("Pack Count", "pack_count", 12),
        ("Unit Size", "unit_size", 11),
        ("Unit", "unit_measure", 8),
        ("Total Weight", "total_weight", 13),
        ("Cases/Pallet", "cases_per_pallet", 13),
        ("Lead Time (days)", "lead_time_days", 15),
        ("Mixed Pallet", "mixed_pallet", 13),
        ("Available", "available", 10),
        ("Promo", "has_promo", 8),
    ]
    
    # Write headers
    for col_idx, (header, _, width) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = border
        ws.column_dimensions[cell.column_letter].width = width
    
    # Write data
    currency_cols = {"delivered_price", "delivered_case_price", "price_per_unit", "price_per_oz"}
    number_cols = {"pack_count", "unit_size", "total_weight", "cases_per_pallet", "lead_time_days"}
    
    for row_idx, (_, row) in enumerate(df.iterrows(), 2):
        for col_idx, (_, field, _) in enumerate(columns, 1):
            value = row.get(field, "")
            cell = ws.cell(row=row_idx, column=col_idx)
            
            if field in currency_cols and value != "":
                try:
                    cell.value = float(value)
                    cell.number_format = '$#,##0.00'
                except (ValueError, TypeError):
                    cell.value = value
            elif field in number_cols and value != "":
                try:
                    cell.value = float(value)
                    cell.number_format = '#,##0.##'
                except (ValueError, TypeError):
                    cell.value = value
            else:
                cell.value = str(value) if value != "" else ""
            
            cell.border = border
            cell.alignment = Alignment(vertical="center")
            if row_idx % 2 == 0:
                cell.fill = alt_fill
    
    # Freeze header row
    ws.freeze_panes = "A2"
    
    # Auto-filter
    ws.auto_filter.ref = ws.dimensions
    
    # Summary sheet
    ws2 = wb.create_sheet("Summary")
    ws2.cell(row=1, column=1, value="ePallet Data Summary").font = Font(bold=True, size=14)
    ws2.cell(row=2, column=1, value=f"Generated: March 6, 2026")
    ws2.cell(row=3, column=1, value=f"Total Products: {len(df)}")
    ws2.cell(row=4, column=1, value=f"Categories: {df['category'].nunique()}")
    ws2.cell(row=5, column=1, value=f"Manufacturers: {df['manufacturer'].nunique()}")
    
    if len(df) > 0 and "price_per_unit" in df.columns:
        valid = pd.to_numeric(df["price_per_unit"], errors="coerce").dropna()
        if len(valid) > 0:
            ws2.cell(row=7, column=1, value="Price Per Unit Stats").font = Font(bold=True)
            ws2.cell(row=8, column=1, value=f"Average: ${valid.mean():.2f}")
            ws2.cell(row=9, column=1, value=f"Median: ${valid.median():.2f}")
            ws2.cell(row=10, column=1, value=f"Min: ${valid.min():.2f}")
            ws2.cell(row=11, column=1, value=f"Max: ${valid.max():.2f}")
    
    # Save to buffer
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    
    filename = "epallet_products"
    cats = request.args.get("category")
    if cats:
        filename += f"_{cats.replace(',', '_')}"
    filename += ".xlsx"
    
    return send_file(buf, download_name=filename, as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/api/export/csv")
@login_required
def export_csv():
    """Export filtered data as CSV"""
    df = apply_filters(DF.copy(), request.args)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    
    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        download_name="epallet_products.csv",
        as_attachment=True,
        mimetype="text/csv"
    )

# ── Serve frontend ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file("static/index.html")

if __name__ == "__main__":
    app.run(debug=True, port=5050)
