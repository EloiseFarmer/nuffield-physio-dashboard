"""
Merges the scraper's output files (site_status_summary.csv,
all_sites_day_summary.csv, site_names_types.csv) into one JSON file
the dashboard HTML reads directly.

Run this after scrape_physio.py each week to refresh the dashboard's data.
"""
import pandas as pd
from datetime import datetime, date
import json

def parse_slot_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%A %d %B %Y").date()

status = pd.read_csv("site_status_summary.csv")
days = pd.read_csv("all_sites_day_summary.csv")
names = pd.read_csv("site_names_types.csv")

# roster counts are optional - dashboard still works if this file doesn't exist yet
try:
    roster_counts = pd.read_csv("site_physio_counts.csv")
except FileNotFoundError:
    roster_counts = pd.DataFrame(columns=["site_slug", "total_physios"])

days["parsed_date"] = days["date"].apply(parse_slot_date)
# "today" is the first ACTIVE day - the data includes a few inactive lead-in
# days before the scrape date that would otherwise throw this off
today = days[days["day_active"] == True]["parsed_date"].min()
days["days_from_today"] = (days["parsed_date"] - today).apply(lambda d: d.days)

WINDOW_7 = 7
WINDOW_14 = 14

slots_7 = days[days["days_from_today"].between(0, WINDOW_7)].groupby("site_slug")["slot_count"].sum()
slots_14 = days[days["days_from_today"].between(0, WINDOW_14)].groupby("site_slug")["slot_count"].sum()

merged = status.merge(names, on="site_slug", how="left")
merged = merged.merge(roster_counts, on="site_slug", how="left")
merged["total_physios"] = merged["total_physios"].fillna(0).astype(int)
merged["slots_7_days"] = merged["site_slug"].map(slots_7).fillna(0).astype(int)
merged["slots_14_days"] = merged["site_slug"].map(slots_14).fillna(0).astype(int)

records = []
for _, row in merged.iterrows():
    records.append({
        "slug": row["site_slug"],
        "name": row["display_name"] if pd.notna(row["display_name"]) else row["site_slug"],
        "type": row["type"] if pd.notna(row["type"]) else "Unknown",
        "physios_with_slots": int(row["physio_count_with_availability"]),
        "total_physios": int(row["total_physios"]),
        "online_booking": row["status"] != "no_online_booking",
        "status": row["status"],
        "next_available": row["next_available_date"] if pd.notna(row["next_available_date"]) else None,
        "days_until_available": None if pd.isna(row["days_until_available"]) else int(row["days_until_available"]),
        "slots_7_days": int(row["slots_7_days"]),
        "slots_14_days": int(row["slots_14_days"]),
        "book_url": f"https://book.nuffieldhealth.com/physio/appointments/{row['site_slug']}",
        "profile_url": f"https://www.nuffieldhealth.com/physiotherapy/{row['site_slug']}",
    })

output = {
    "last_updated": today.strftime("%d %b %Y"),
    "sites": records,
    "summary": {
        "total_sites": len(records),
        "total_physios_listed": int(merged["total_physios"].sum()),
        "sites_with_online_booking": int((merged["status"] != "no_online_booking").sum()),
        "sites_with_slots_14_days": int((merged["slots_14_days"] > 0).sum()),
        "total_appointments_14_days": int(merged["slots_14_days"].sum()),
        "total_appointments_7_days": int(merged["slots_7_days"].sum()),
        "pct_no_appt_7_days": round(100 * (merged["slots_7_days"] == 0).sum() / len(merged), 1),
    }
}

with open("dashboard_data.json", "w") as f:
    json.dump(output, f, indent=2)

print("Built dashboard_data.json")
print(json.dumps(output["summary"], indent=2))
