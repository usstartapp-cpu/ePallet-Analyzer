# ePallet Scraper 4000

Scrapes **Dry storage** product data from [epallet.com](https://epallet.com) and exports to Excel.

## Setup

```bash
pip3 install -r requirements.txt
python3 -m playwright install chromium
```

## Usage

### Step 1: Test Login
```bash
python3 test_login.py
```
This opens a visible browser, logs in, and saves debug screenshots so you can verify everything works.

### Step 2: Run Full Scrape
```bash
python3 scraper.py
```
This will:
1. **Log in** with your credentials (configured in `config.py`)
2. **Discover** all product categories
3. **Scrape** every page of DRY-filtered products (~7,220 items, ~301 pages)
4. **Export** to `epallet_dry_products.xlsx`

### Configuration
Edit `config.py` to change:
- Login credentials
- Output filename
- Delay between pages
- Headless mode (set `HEADLESS = True` once login is confirmed working)

## Output

The Excel file contains:

| Column | Description |
|---|---|
| Category | Product category (Snacks, Beverages, etc.) |
| Manufacturer | Brand name (cleaned, no "-EP" suffix) |
| Product | Full product name |
| Delivered Price | Pallet delivered price ($) |
| Price Per Unit | Per-unit price ($) |
| Pack Size (Raw) | Original pack size string |
| Pack Count | Number before "/" (e.g., "10" from "10/5 oz") |
| Unit Size | Size after "/" (e.g., "5 oz" from "10/5 oz") |
| Cases Per Pallet | Number of cases per pallet |
| Lead Time | Delivery lead time |
| Price Per Case | Computed: Delivered Price ÷ Cases Per Pallet |
| Product URL | Direct link to product page |

## Files

- `config.py` — Settings and credentials
- `test_login.py` — Login verification script
- `scraper.py` — Main scraper
- `epallet_checkpoint.csv` — Auto-saved progress (in case of crash)
- `epallet_dry_products.xlsx` — Final output
