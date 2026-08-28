"""
Nuffield Health physio ROSTER scraper.

Unlike scrape_physio.py (which reads current booking availability), this
reads each site's marketing page (nuffieldhealth.com/physiotherapy/<slug>)
for its "Meet the team" section - the full list of physios who work at
that site, independent of whether they currently have any bookable slots.

This is what gives a true "total physios" count, matching what a site's
own staff page shows rather than only physios visible in the booking widget.

Produces two outputs:
  - physio_roster.csv       one row per physio per site (name, title)
  - site_physio_counts.csv  one row per site: total physios listed
"""
import requests
from bs4 import BeautifulSoup
import time
import pandas as pd

MARKETING_URL = "https://www.nuffieldhealth.com/physiotherapy/{}"
SLUGS_FILE = "site_slugs.txt"
ROSTER_FILE = "physio_roster.csv"
COUNTS_FILE = "site_physio_counts.csv"
REQUEST_DELAY_SECONDS = 1

# Birmingham doesn't follow the standard /physiotherapy/<slug> pattern -
# same special case as in scrape_physio.py
SPECIAL_CASE_URLS = {
    "birmingham-rubery": "https://www.nuffieldhealth.com/gyms/birmingham-rubery/services/physiotherapy",
}


def scrape_site_roster(site_slug: str) -> pd.DataFrame:
    """Returns one row per physio listed on this site's marketing page.
    Returns an empty DataFrame if the page has no 'team' section at all
    (e.g. some hospital pages may not use this same team-grid template)."""
    url = SPECIAL_CASE_URLS.get(site_slug, MARKETING_URL.format(site_slug))
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    grid = soup.find("section", class_="person-grid")
    if grid is None:
        return pd.DataFrame()

    items = grid.find_all("div", class_="person-grid__item")
    rows = []
    for item in items:
        name_span = item.find("span", class_="person-summary__name")
        if name_span is None:
            continue
        title_span = name_span.find("span", class_="person-summary__title")
        title = title_span.get_text(strip=True) if title_span else ""
        if title_span:
            title_span.extract()
        name = name_span.get_text(strip=True)
        rows.append({"site_slug": site_slug, "physio_name": name, "physio_title": title})

    return pd.DataFrame(rows)


def scrape_all_rosters() -> pd.DataFrame:
    with open(SLUGS_FILE) as f:
        slugs = [line.strip() for line in f if line.strip()]

    all_frames = []
    no_team_section = []
    failed = []

    for i, slug in enumerate(slugs, start=1):
        print(f"[{i}/{len(slugs)}] Scraping roster for {slug}...")
        try:
            df = scrape_site_roster(slug)
            if df.empty:
                no_team_section.append(slug)
            else:
                all_frames.append(df)
        except requests.exceptions.RequestException as e:
            print(f"  [error] {slug}: {e}")
            failed.append(slug)
        time.sleep(REQUEST_DELAY_SECONDS)

    if no_team_section:
        print(f"\n{len(no_team_section)} site(s) had no team/roster section found: {no_team_section}")
    if failed:
        print(f"{len(failed)} site(s) failed to load: {failed}")

    return pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame()


if __name__ == "__main__":
    roster = scrape_all_rosters()
    print(f"\nTotal: {len(roster)} physios across {roster['site_slug'].nunique()} sites")

    roster.to_csv(ROSTER_FILE, index=False)

    counts = roster.groupby("site_slug").size().reset_index(name="total_physios")
    counts.to_csv(COUNTS_FILE, index=False)

    print(f"Saved to {ROSTER_FILE} and {COUNTS_FILE}")
