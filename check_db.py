import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db.supabase_client import supabase, get_all_vendors

vendors = get_all_vendors()
total = 0
for v in vendors:
    cnt = supabase.table("products").select("id", count="exact").eq("vendor_id", v["id"]).execute()
    count = cnt.count or 0
    total += count
    marker = " <-- HAS DATA" if count > 0 else ""
    print("  %-35s %6d products  (%s)%s" % (v["name"], count, v.get("scrape_method","?"), marker))

print("\nTOTAL: %d products" % total)

runs = supabase.table("scrape_runs").select("id, status, vendor_id, vendors(name)").eq("status", "running").execute()
print("\nStuck 'running' scrape runs: %d" % len(runs.data))
for r in runs.data:
    print("  ID: %s  vendor: %s" % (r["id"], r["vendors"]["name"]))
