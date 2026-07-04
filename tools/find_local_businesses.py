"""
Find local businesses across Sydney, NSW using Google Places Text Search API.

Usage:
  python tools/find_local_businesses.py [--max-results 150]

Options:
  --max-results   Maximum number of unique businesses to collect (default: 150)

Prerequisites:
  1. Enable "Places API" in Google Cloud Console
  2. Set GOOGLE_PLACES_API_KEY in .env

Output: .tmp/local_businesses.json
"""

import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.types,places.location,places.regularOpeningHours"

KEYWORDS = [
    # Trades
    "electrician", "builder", "carpenter", "painter",
    "landscaper", "concreter", "tiler", "roofer", "fencer",
    "mechanic", "panel beater", "locksmith", "pest control",
    "cleaner", "cleaning service", "removalist", "gardener",
    # Food & hospitality
    "restaurant", "cafe", "catering", "bakery",
    # Health & wellness
    "dentist", "physiotherapist", "chiropractor", "optometrist",
    "gym", "yoga studio",
    # Beauty & personal care
    "hair salon", "beauty salon", "dog groomer", "barber",
    # Professional services
    "accountant", "bookkeeper", "solicitor", "mortgage broker",
    "real estate agent", "financial planner",
    # Creative & lifestyle
    "florist", "photographer", "childcare centre", "travel agent",
    "event planner", "printing shop",
]

MAX_PER_KEYWORD = 25  # Cap per keyword type to ensure diversity

SUBURBS = [
    # Sydney — Upper North Shore / Hornsby
    "Hornsby", "Wahroonga", "Pennant Hills", "Thornleigh",
    "Pymble", "Turramurra", "Berowra", "Asquith", "Mount Colah",
    # Sydney — Lower North Shore
    "Chatswood", "Lane Cove", "North Sydney", "Mosman", "Neutral Bay",
    "Crows Nest", "St Leonards", "Artarmon", "Willoughby", "Lindfield",
    "Gordon", "Killara", "St Ives", "Cremorne", "Kirribilli",
    # Sydney — Hills District
    "Castle Hill", "Baulkham Hills", "Kellyville", "Rouse Hill",
    "Norwest", "Cherrybrook", "Dural", "Glenhaven", "Winston Hills",
    # Sydney — Northern Beaches
    "Dee Why", "Brookvale", "Manly", "Frenchs Forest", "Narrabeen",
    "Collaroy", "Mona Vale", "Avalon Beach", "Newport", "Warriewood",
    # Sydney — Eastern Suburbs
    "Bondi", "Bondi Junction", "Randwick", "Coogee", "Maroubra",
    "Paddington", "Surry Hills", "Newtown", "Glebe", "Pyrmont",
    "Darlinghurst", "Kingsford", "Kensington", "Rosebery",
    # Sydney — Inner West
    "Strathfield", "Burwood", "Ashfield", "Homebush", "Leichhardt",
    "Marrickville", "Balmain", "Rozelle", "Annandale", "Petersham",
    # Sydney — Western
    "Parramatta", "Blacktown", "Seven Hills", "Merrylands",
    "Westmead", "Wentworthville", "Toongabbie", "Pendle Hill",
    # Sydney — South / South-West
    "Hurstville", "Bankstown", "Liverpool", "Campbelltown", "Cronulla",
    "Kogarah", "Rockdale", "Bexley", "Carlton", "Miranda",
    "Sutherland", "Caringbah", "Engadine", "Revesby", "Punchbowl",
    # NSW — Central Coast
    "Gosford", "Wyong", "Tuggerah", "Erina", "Terrigal",
    "Woy Woy", "Umina Beach", "Toukley", "The Entrance", "Bateau Bay",
    # NSW — Newcastle / Hunter
    "Newcastle", "Maitland", "Cessnock", "Charlestown",
    "Kotara", "Warners Bay", "Raymond Terrace", "Wallsend",
    "Hamilton", "Adamstown", "Merewether", "Jesmond",
    # NSW — Wollongong / Illawarra
    "Wollongong", "Dapto", "Shellharbour", "Kiama",
    "Figtree", "Fairy Meadow", "Albion Park", "Unanderra",
    # NSW — Blue Mountains / Penrith
    "Penrith", "St Marys", "Katoomba", "Springwood", "Lithgow",
    "Glenmore Park", "Emu Plains", "Kingswood",
    # NSW — Regional
    "Albury", "Wagga Wagga", "Tamworth", "Orange NSW",
    "Dubbo", "Bathurst", "Coffs Harbour", "Port Macquarie",
    "Lismore", "Ballina", "Tweed Heads", "Grafton",
    "Nowra", "Batemans Bay", "Goulburn", "Armidale",
    "Broken Hill", "Mudgee", "Griffith NSW", "Deniliquin",
    "Inverell", "Glen Innes", "Moree", "Narrabri",
]


def get_api_key() -> str:
    key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        print("ERROR: GOOGLE_PLACES_API_KEY is not set in .env")
        print("  1. Go to Google Cloud Console and enable 'Places API'")
        print("  2. Create an API key and add it to .env as GOOGLE_PLACES_API_KEY=<key>")
        sys.exit(1)
    return key


def text_search(api_key: str, query: str, page_token: str = None) -> dict:
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": PLACES_FIELD_MASK,
        "Content-Type": "application/json",
    }
    body = {"textQuery": query}
    if page_token:
        body["pageToken"] = page_token
    try:
        resp = requests.post(PLACES_TEXT_SEARCH_URL, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"    Network error for '{query}': {e}")
        return {}


def extract_opening_hours(place: dict) -> str:
    """Return a newline-separated string of weekday hours, e.g. 'Monday: 9:00 AM – 5:00 PM'."""
    hours = place.get("regularOpeningHours", {})
    descriptions = hours.get("weekdayDescriptions", [])
    return "\n".join(descriptions) if descriptions else ""


def extract_business(place: dict) -> dict:
    location = place.get("location", {})
    return {
        "place_id": place.get("id", ""),
        "name": place.get("displayName", {}).get("text", ""),
        "address": place.get("formattedAddress", ""),
        "phone": place.get("nationalPhoneNumber", ""),
        "website": place.get("websiteUri") or None,
        "types": place.get("types", []),
        "vicinity": place.get("formattedAddress", ""),
        "lat": location.get("latitude"),
        "lng": location.get("longitude"),
        "opening_hours": extract_opening_hours(place),
    }


def paginate_query(api_key: str, query: str) -> list:
    results = []
    page_token = None
    page = 0

    while page < 3:  # Max 3 pages = 60 results per query
        if page_token:
            time.sleep(2)  # Required delay before using next_page_token

        data = text_search(api_key, query, page_token)

        # New Places API returns HTTP errors instead of status fields
        if "error" in data:
            err = data["error"]
            code = err.get("code", 0)
            msg = err.get("message", "Unknown error")
            if code in (403, 429):
                print(f"\nERROR: Google Places API error {code}: {msg}")
                print("Check your API key, billing, and quota limits in Google Cloud Console.")
                sys.exit(1)
            print(f"    Warning: API error for '{query}': {msg} — skipping")
            break

        places = data.get("places", [])
        for place in places:
            results.append(extract_business(place))

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        page += 1

    return results


def collect_businesses(api_key: str, max_results: int) -> list:
    seen = {}  # place_id -> business dict
    keyword_counts = {kw: 0 for kw in KEYWORDS}
    total_queries = len(KEYWORDS) * len(SUBURBS)
    current = 0

    # Suburb-first so each keyword gets fair representation across all suburbs
    for suburb in SUBURBS:
        for keyword in KEYWORDS:
            current += 1

            # Skip keyword if it's already hit the per-keyword cap
            if keyword_counts[keyword] >= MAX_PER_KEYWORD:
                continue

            query = f"{keyword} {suburb} NSW"
            print(f"  [{current}/{total_queries}] Searching: {query}")

            businesses = paginate_query(api_key, query)
            new_count = 0
            for b in businesses:
                if b["place_id"] and b["place_id"] not in seen:
                    seen[b["place_id"]] = {**b, "search_keyword": keyword}
                    keyword_counts[keyword] += 1
                    new_count += 1

            if new_count:
                print(f"    +{new_count} new (total: {len(seen)})")

            if len(seen) >= max_results:
                print(f"\n  Reached max-results limit ({max_results}). Stopping early.")
                return list(seen.values())

            # Polite delay between queries
            time.sleep(0.5)

    return list(seen.values())


def main():
    parser = argparse.ArgumentParser(description="Find local businesses across Sydney, NSW")
    parser.add_argument("--max-results", type=int, default=400,
                        help="Max unique businesses to collect (default: 400)")
    args = parser.parse_args()

    api_key = get_api_key()

    tmp_dir = BASE_DIR / ".tmp"
    tmp_dir.mkdir(exist_ok=True)

    print(f"Searching for local businesses across Sydney, NSW...")
    print(f"Keywords: {len(KEYWORDS)}, Suburbs: {len(SUBURBS)}, Max results: {args.max_results}\n")

    businesses = collect_businesses(api_key, args.max_results)

    output = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total": len(businesses),
        "businesses": businesses,
    }

    out_path = tmp_dir / "local_businesses.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nDone. Found {len(businesses)} unique businesses.")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()
