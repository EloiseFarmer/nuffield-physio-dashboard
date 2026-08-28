"""
Nuffield Health physio availability scraper.

Reads the embedded 'slots-data' JSON script tag from every site's booking
page (site_slugs.txt) and produces three outputs:
  - all_sites_slots.csv        one row per individual available slot
  - all_sites_day_summary.csv  one row per site/day (incl. zero-slot days)
  - site_status_summary.csv    one row per site: availability status, total
                                slots, next available date, days until then,
                                and how many physios currently have any slots
"""
import requests
from bs4 import BeautifulSoup
import json
import time
from datetime import datetime, date
import pandas as pd

BASE_URL = "https://book.nuffieldhealth.com/physio/appointments/{}"
MARKETING_URL = "https://www.nuffieldhealth.com/physiotherapy/{}"
SLUGS_FILE = "site_slugs.txt"
OUTPUT_FILE = "all_sites_slots.csv"
DAY_SUMMARY_FILE = "all_sites_day_summary.csv"
STATUS_FILE = "site_status_summary.csv"
REQUEST_DELAY_SECONDS = 1  # be polite - don't hammer the site


def parse_slot_date(date_str: str) -> date:
    """Parses dates like 'Friday 21 August 2026' into a date object."""
    return datetime.strptime(date_str, "%A %d %B %Y").date()

# Birmingham (and possibly other sites we haven't found yet) use a different
# booking URL pattern than the rest. Map any such special cases here.
SPECIAL_CASE_URLS = {
    "birmingham-rubery": "https://www.nuffieldhealth.com/gyms/birmingham-rubery/services/physiotherapy",
}


def find_real_booking_url(marketing_slug: str) -> str | None:
    """The marketing page slug (nuffieldhealth.com/physiotherapy/<slug>) doesn't
    always match the actual booking page slug (book.nuffieldhealth.com/...) -
    e.g. 'london-battersea' on the marketing site is 'battersea-london' on the
    booking site, and 'london-city' is actually 'city-gym-london'. Rather than
    guess the transformation, fetch the marketing page and pull the real
    booking link straight out of its HTML."""
    try:
        resp = requests.get(MARKETING_URL.format(marketing_slug), timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.find("a", href=lambda h: h and "book.nuffieldhealth.com/physio/appointments/" in h)
    return link["href"] if link else None


def scrape_site(site_slug: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch and parse one site's booking page.

    Returns two DataFrames:
      - slots_df: one row per individual available slot (physio-level detail)
      - day_summary_df: one row per calendar day, always present even when
        a day has zero slots - this is what makes "fully booked" a real,
        trackable data point instead of silently missing data.

    Both are empty (not an exception) if the site has no slots-data script -
    e.g. a site that doesn't use this booking widget pattern at all."""
    url = SPECIAL_CASE_URLS.get(site_slug, BASE_URL.format(site_slug))

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            # Direct slug guess failed - try to find the real URL via the
            # marketing page instead of giving up
            real_url = find_real_booking_url(site_slug)
            if real_url is None:
                raise
            print(f"    -> retrying with real URL found on marketing page: {real_url}")
            resp = requests.get(real_url, timeout=15)
            resp.raise_for_status()
        else:
            raise

    soup = BeautifulSoup(resp.text, "html.parser")
    script_tag = soup.find("script", id="slots-data")
    if script_tag is None:
        print(f"  [skip] {site_slug}: no slots-data found (phone-only booking / different page structure)")
        return pd.DataFrame(), pd.DataFrame()

    raw = script_tag.string
    cleaned = "\n".join(
        line for line in raw.splitlines()
        if not line.strip().startswith("//<![CDATA[")
        and not line.strip().startswith("//]]>")
    )
    data = json.loads(cleaned)

    slot_rows = []
    day_rows = []
    for day in data["days"]:
        date_str = f"{day['day']} {day['day_of_month']} {day['month']} {day['year']}"
        day_rows.append({
            "site_slug": site_slug,
            "date": date_str,
            "day_active": day["active"],
            "slot_count": len(day["slots"]),
        })
        for slot in day["slots"]:
            slot_rows.append({
                "site_slug": site_slug,
                "date": date_str,
                "day_active": day["active"],
                "start_time": slot["start_time"],
                "end_time": slot["end_time"],
                "professional_name": slot["professional_name"],
                "professional_id": slot["professional_id"],
                "gender": slot["gender"],
                "site_id": slot["site_id"],
                "session_id": slot["session_id"],
            })

    return pd.DataFrame(slot_rows), pd.DataFrame(day_rows)


def scrape_all_sites() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with open(SLUGS_FILE) as f:
        slugs = [line.strip() for line in f if line.strip()]

    today = date.today()
    all_slot_frames = []
    all_day_frames = []
    status_rows = []

    for i, slug in enumerate(slugs, start=1):
        print(f"[{i}/{len(slugs)}] Scraping {slug}...")
        try:
            slots_df, days_df = scrape_site(slug)

            if days_df.empty:
                status = "no_online_booking"
                total_slots = 0
                next_available_date = None
                days_until_available = None
                physio_count = 0
            elif slots_df.empty:
                status = "zero_availability"
                total_slots = 0
                next_available_date = None
                days_until_available = None
                physio_count = 0
            else:
                status = "has_availability"
                total_slots = len(slots_df)

                slots_df = slots_df.copy()
                slots_df["_parsed_date"] = slots_df["date"].apply(parse_slot_date)
                earliest = slots_df["_parsed_date"].min()
                next_available_date = earliest.strftime("%A %d %B %Y")
                days_until_available = (earliest - today).days
                physio_count = slots_df["professional_id"].nunique()

                slots_df = slots_df.drop(columns="_parsed_date")

            status_rows.append({
                "site_slug": slug,
                "status": status,
                "total_slots_found": total_slots,
                "next_available_date": next_available_date,
                "days_until_available": days_until_available,
                "physio_count_with_availability": physio_count,
            })

            if not slots_df.empty:
                all_slot_frames.append(slots_df)
            if not days_df.empty:
                all_day_frames.append(days_df)

        except requests.exceptions.RequestException as e:
            print(f"  [error] {slug}: {e}")
            status_rows.append({
                "site_slug": slug, "status": "error", "total_slots_found": 0,
                "next_available_date": None, "days_until_available": None,
                "physio_count_with_availability": 0,
            })

        time.sleep(REQUEST_DELAY_SECONDS)

    slots_combined = pd.concat(all_slot_frames, ignore_index=True) if all_slot_frames else pd.DataFrame()
    days_combined = pd.concat(all_day_frames, ignore_index=True) if all_day_frames else pd.DataFrame()
    status_combined = pd.DataFrame(status_rows)
    return slots_combined, days_combined, status_combined


if __name__ == "__main__":
    slots, days, status = scrape_all_sites()

    print("\n--- SITE STATUS BREAKDOWN ---")
    print(status["status"].value_counts().to_string())

    print(f"\nSlot-level detail: {len(slots)} available slots across "
          f"{slots['site_slug'].nunique() if not slots.empty else 0} sites and "
          f"{slots['professional_name'].nunique() if not slots.empty else 0} physios")
    print(f"Day-level summary: {len(days)} site/day combinations recorded across "
          f"{days['site_slug'].nunique() if not days.empty else 0} sites")

    slots.to_csv(OUTPUT_FILE, index=False)
    days.to_csv(DAY_SUMMARY_FILE, index=False)
    status.to_csv(STATUS_FILE, index=False)
    print(f"\nSaved to {OUTPUT_FILE}, {DAY_SUMMARY_FILE}, and {STATUS_FILE}")
