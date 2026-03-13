-- ════════════════════════════════════════════════════════════════════════
-- SCRAPER 4000 — Multi-Vendor Procurement Intelligence
-- Supabase Database Schema
-- ════════════════════════════════════════════════════════════════════════

-- ── VENDORS ───────────────────────────────────────────────────────────
-- Each vendor we scrape from
CREATE TABLE IF NOT EXISTS vendors (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    slug            TEXT NOT NULL UNIQUE,          -- url-safe key: "us-foods", "costco", etc.
    website         TEXT,
    phone           TEXT,
    rep_name        TEXT,
    rep_phone       TEXT,
    rep_email       TEXT,
    scrape_method   TEXT DEFAULT 'playwright',     -- 'playwright', 'api', 'manual'
    scrape_enabled  BOOLEAN DEFAULT true,
    api_endpoint    TEXT,                           -- if vendor has a usable API
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ── PRODUCTS ──────────────────────────────────────────────────────────
-- Normalized product data across all vendors
CREATE TABLE IF NOT EXISTS products (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    vendor_id       UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    
    -- Identity (used for matching across vendors)
    sku             TEXT,                           -- vendor-specific SKU
    upc             TEXT,                           -- universal product code (cross-vendor match key)
    product_name    TEXT NOT NULL,
    brand           TEXT,                           -- manufacturer / brand
    description     TEXT,
    
    -- Categorization
    category        TEXT,
    sub_category    TEXT,
    
    -- Pricing
    unit_price      NUMERIC(12,4),                 -- price per single unit
    case_price      NUMERIC(12,4),                 -- price per case
    price_per_oz    NUMERIC(12,4),
    bulk_price      NUMERIC(12,4),                 -- pallet / bulk price
    currency        TEXT DEFAULT 'USD',
    
    -- Pack / Size
    pack_size_raw   TEXT,                           -- raw string: "24/12 oz"
    pack_count      NUMERIC,
    unit_size       NUMERIC,
    unit_measure    TEXT,                           -- oz, lb, ct, etc.
    
    -- Logistics
    cases_per_pallet  INTEGER,
    min_order_qty     INTEGER,
    lead_time_days    INTEGER,
    in_stock          BOOLEAN DEFAULT true,
    mixed_pallet      BOOLEAN DEFAULT false,
    
    -- Metadata
    product_url     TEXT,
    image_url       TEXT,
    has_promo       BOOLEAN DEFAULT false,
    promo_details   TEXT,
    
    -- Tracking
    first_seen_at   TIMESTAMPTZ DEFAULT now(),
    last_seen_at    TIMESTAMPTZ DEFAULT now(),
    last_price      NUMERIC(12,4),                 -- previous price for change detection
    price_changed   BOOLEAN DEFAULT false,
    
    -- Unique constraint: one SKU per vendor
    UNIQUE(vendor_id, sku)
);

-- ── PRODUCT HISTORY ───────────────────────────────────────────────────
-- Price & availability snapshots for trend tracking
CREATE TABLE IF NOT EXISTS product_history (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    product_id      UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    scrape_run_id   UUID,                          -- which scrape captured this
    
    unit_price      NUMERIC(12,4),
    case_price      NUMERIC(12,4),
    bulk_price      NUMERIC(12,4),
    in_stock        BOOLEAN,
    
    captured_at     TIMESTAMPTZ DEFAULT now()
);

-- ── SCRAPE RUNS ───────────────────────────────────────────────────────
-- Log of every scrape execution
CREATE TABLE IF NOT EXISTS scrape_runs (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    vendor_id       UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    
    status          TEXT DEFAULT 'running',         -- running, completed, failed, partial
    method          TEXT,                           -- playwright, api
    
    products_found  INTEGER DEFAULT 0,
    products_new    INTEGER DEFAULT 0,
    products_updated INTEGER DEFAULT 0,
    price_changes   INTEGER DEFAULT 0,
    errors          INTEGER DEFAULT 0,
    
    error_log       JSONB,
    
    started_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    duration_secs   INTEGER,
    
    triggered_by    TEXT DEFAULT 'manual',          -- manual, scheduled, api
    
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ── PRICE COMPARISONS (VIEW) ─────────────────────────────────────────
-- Cross-vendor price comparison by UPC
CREATE OR REPLACE VIEW price_comparison AS
SELECT 
    p.upc,
    p.product_name,
    p.brand,
    p.category,
    v.name AS vendor_name,
    v.slug AS vendor_slug,
    p.unit_price,
    p.case_price,
    p.bulk_price,
    p.price_per_oz,
    p.pack_size_raw,
    p.in_stock,
    p.last_seen_at,
    p.price_changed,
    p.last_price,
    p.product_url
FROM products p
JOIN vendors v ON p.vendor_id = v.id
WHERE p.upc IS NOT NULL AND p.upc != ''
ORDER BY p.upc, p.unit_price ASC NULLS LAST;

-- ── BEST PRICE PER PRODUCT (VIEW) ────────────────────────────────────
CREATE OR REPLACE VIEW best_prices AS
SELECT DISTINCT ON (upc)
    upc,
    product_name,
    brand,
    category,
    vendor_id,
    unit_price,
    case_price,
    price_per_oz,
    pack_size_raw,
    in_stock,
    product_url,
    last_seen_at
FROM products
WHERE upc IS NOT NULL AND upc != '' 
  AND unit_price IS NOT NULL 
  AND in_stock = true
ORDER BY upc, unit_price ASC;

-- ── RATE LIMITING ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rate_limits (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    action          TEXT NOT NULL,                  -- 'scrape_manual'
    user_id         TEXT,
    count_today     INTEGER DEFAULT 1,
    last_used       TIMESTAMPTZ DEFAULT now(),
    reset_date      DATE DEFAULT CURRENT_DATE
);

-- ── INDEXES ───────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_products_vendor ON products(vendor_id);
CREATE INDEX IF NOT EXISTS idx_products_upc ON products(upc);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products(vendor_id, sku);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_last_seen ON products(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_product_history_product ON product_history(product_id);
CREATE INDEX IF NOT EXISTS idx_product_history_captured ON product_history(captured_at);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_vendor ON scrape_runs(vendor_id);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_status ON scrape_runs(status);
CREATE INDEX IF NOT EXISTS idx_rate_limits_action ON rate_limits(action, reset_date);

-- ── SEED VENDORS ──────────────────────────────────────────────────────
INSERT INTO vendors (name, slug, website, phone, rep_name, rep_phone, rep_email, scrape_method, notes) VALUES
    ('E-Pallet', 'epallet', 'https://epallet.com/', '—', 'Online', '—', '—', 'playwright', 'Primary vendor. Full scraper built.'),
    ('US Foods', 'us-foods', 'https://www.usfoods.com/', '281-310-2000', 'Bobby (Robert) Geiger', '803-427-4033', 'robert.geiger@usfoods.com', 'playwright', 'OTP login — may need manual step'),
    ('Costco Business', 'costco', 'https://www.costcobusinessdelivery.com/', '800-788-9968', 'Theresa', '346-309-3871', 'w01487mk05@costco.com', 'playwright', NULL),
    ('Webstaurant Store', 'webstaurant', 'https://www.webstaurantstore.com/', '—', 'Online', '—', '—', 'playwright', 'Large catalog, standard e-commerce'),
    ('Faire', 'faire', 'https://www.faire.com/', '—', 'Online', '—', '—', 'playwright', 'Wholesale marketplace'),
    ('Walmart Business', 'walmart', 'https://business.walmart.com/', '—', 'Online', '—', '—', 'playwright', NULL),
    ('McLane Xpress', 'mclane', 'https://mclanexpress.com/', '281-765-6000', 'Derrick Davenport', '346-479-4421', 'derrick.davenport@mclaneco.com', 'playwright', NULL),
    ('Hershey''s', 'hersheys', 'https://shop.hersheys.com/', '—', 'Online', '—', '—', 'playwright', 'Direct brand store'),
    ('Ghirardelli', 'ghirardelli', 'https://www.ghirardelli.com/', '888-402-6262', '—', '—', '—', 'playwright', 'Direct brand store'),
    ('Barilla', 'barilla', 'https://www.barilla.com/', '800-922-7455', '—', '—', '—', 'playwright', 'Direct brand store'),
    ('Alessi Foods', 'alessi', 'https://alessifoods.com/', '800-282-4130', 'Chris Chandler', '214-738-0238', 'chris@chandlerfoodsales.com', 'playwright', NULL),
    ('Vigo Foods', 'vigo', 'https://vigofoods.com/', '800-282-4130', 'Chris Chandler', '214-738-0238', 'chris@chandlerfoodsales.com', 'playwright', 'Same rep as Alessi'),
    ('Del Monte Cash Back', 'delmonte', 'https://delmontefscashback.com/dashboard/', '—', 'Online', '—', '—', 'playwright', 'Cash back portal'),
    ('Johnson Bros. Bakery Supply', 'johnson-bros', 'https://jbrosbakerysupply.com/', '800-590-2575', '—', '—', '—', 'playwright', NULL),
    ('Every Day Supply Co', 'everyday-supply', 'https://everydaysupplyco.com/', '—', 'Online', '—', '—', 'playwright', NULL),
    ('Ben E. Keith', 'ben-e-keith', 'https://www.benekeith.com/food', '832-652-5888', 'Jim Young', '832-244-0135', 'jryoung@benekeith.com', 'manual', 'No web login provided'),
    ('Dawn Food Products', 'dawn-foods', NULL, '713-683-0300', '—', '—', '—', 'manual', 'No web portal/login'),
    ('Dot Foods', 'dot-foods', NULL, '217-773-4411', '—', '—', '—', 'manual', 'No web portal/login')
ON CONFLICT (slug) DO NOTHING;

-- ── UPDATED_AT TRIGGER ────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER vendors_updated_at
    BEFORE UPDATE ON vendors
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
