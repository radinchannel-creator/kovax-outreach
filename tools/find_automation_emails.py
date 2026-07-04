#!/usr/bin/env python3
"""
Scrape contact emails for all businesses in local_businesses.json.
Unlike find_business_emails.py, this reads ALL businesses (no website-quality filter).

Input:  .tmp/local_businesses.json
Output: .tmp/automation_leads.json
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup

BASE_DIR    = Path(__file__).parent.parent
INPUT_FILE  = BASE_DIR / ".tmp" / "local_businesses.json"
OUTPUT_FILE = BASE_DIR / ".tmp" / "automation_leads.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

CONTACT_PATHS = [
    "/contact", "/contact-us", "/about", "/about-us",
    "/get-in-touch", "/reach-us", "/enquiry", "/enquire",
    "/team", "/our-team", "/staff", "/book", "/booking",
]

SKIP_RE = re.compile(
    r"(noreply|no-reply|donotreply|unsubscribe|"
    r"example\.|sentry\.io|ingest\.|@github|@w3|"
    r"@schema|@jquery|yourname|youremail|email@|user@|name@|"
    r"@mysite\.com|@wixsite\.com|@wix\.com|@squarespace\.com|"
    r"@weebly\.com|@jimdo\.com|@wordpress\.com|@blogspot\.com|"
    r"support@(?!kovax)|webmaster@|hostmaster@|privacy@|legal@|"
    r"abuse@|spam@|bounce@|mailer-daemon@|postmaster@)",
    re.IGNORECASE,
)
EMAIL_RE  = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
FILE_EXT  = re.compile(
    r"\.(png|jpg|jpeg|gif|svg|webp|ico|pdf|zip|mp4|woff|woff2|ttf|css|js|xml|json)$",
    re.IGNORECASE,
)


def extract_mailto_emails(html: str) -> list[str]:
    """Pull emails from <a href="mailto:..."> tags — most reliable, explicit intent."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip().lower().rstrip(".")
            if addr and EMAIL_RE.match(addr) and not SKIP_RE.search(addr):
                out.append(addr)
    return out


def extract_emails(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    candidates = set(EMAIL_RE.findall(html)) | set(EMAIL_RE.findall(text))
    out = []
    for e in candidates:
        e = e.lower().rstrip(".")
        if SKIP_RE.search(e):
            continue
        if not re.search(r"\.[a-z]{2,}$", e):
            continue
        if FILE_EXT.search(e):
            continue
        out.append(e)
    return out


def rank_email(emails: list[str], name: str) -> str | None:
    if not emails:
        return None
    PREFERRED = ["contact", "enquir", "hello", "info", "booking", "sales"]
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    def score(e):
        local, domain = e.split("@", 1)
        s = sum(10 for p in PREFERRED if p in local)
        if slug and slug[:4] in local:
            s += 5
        if domain.endswith(".com.au") or domain.endswith(".net.au"):
            s += 8
        elif domain.endswith(".au"):
            s += 4
        return s
    return sorted(emails, key=score, reverse=True)[0]


def fetch(url: str) -> str | None:
    try:
        if url.startswith("http://"):
            try:
                r = requests.get("https://" + url[7:], headers=HEADERS, timeout=7, allow_redirects=True)
                if r.ok:
                    return r.text
            except Exception:
                pass
        r = requests.get(url, headers=HEADERS, timeout=7, allow_redirects=True)
        return r.text if r.ok else None
    except Exception:
        return None


def find_email(website: str, name: str) -> tuple[str | None, str]:
    if not website.startswith("http"):
        website = "http://" + website
    parsed = urlparse(website)
    base = parsed.scheme + "://" + parsed.netloc

    # Homepage — try mailto hrefs first (explicit), then regex fallback
    html = fetch(website)
    if html:
        emails = extract_mailto_emails(html)
        best = rank_email(emails, name)
        if best:
            return best, "homepage_mailto"
        emails = extract_emails(html)
        best = rank_email(emails, name)
        if best:
            return best, "homepage"

    # Contact/about/booking pages — same priority order
    for path in CONTACT_PATHS:
        html = fetch(base + path)
        if html:
            emails = extract_mailto_emails(html)
            best = rank_email(emails, name)
            if best:
                return best, "contact_page_mailto"
            emails = extract_emails(html)
            best = rank_email(emails, name)
            if best:
                return best, "contact_page"
        time.sleep(0.2)

    return None, "not_found"


def suburb_from_address(address: str) -> str:
    parts = address.split(",")
    if len(parts) >= 2:
        part = parts[-2].strip()
        return re.sub(r"\s+(NSW|VIC|QLD|SA|WA|TAS|ACT|NT)\s+\d{4}$", "", part).strip()
    return ""


def main():
    with open(INPUT_FILE, encoding="utf-8") as f:
        businesses = json.load(f)

    if isinstance(businesses, dict):
        businesses = businesses.get("businesses") or businesses.get("leads") or []

    print(f"Processing {len(businesses)} businesses...")

    results = []
    found = 0

    for i, biz in enumerate(businesses):
        name    = biz.get("name", "Unknown")
        website = biz.get("website") or ""
        address = biz.get("address", "")
        suburb  = suburb_from_address(address)
        trade   = biz.get("search_keyword") or (biz.get("types") or ["business"])[0]
        phone   = biz.get("phone") or ""

        if not website:
            print(f"[{i+1}/{len(businesses)}] {name[:45]:<45} -- no website")
            results.append({**biz, "email": None, "email_source": "no_website", "suburb": suburb, "trade": trade})
            continue

        print(f"[{i+1}/{len(businesses)}] {name[:45]:<45}", end=" ", flush=True)
        email, source = find_email(website, name)

        if email:
            print(f"-> {email} [{source}]")
            found += 1
        else:
            print("-- not found")

        results.append({**biz, "email": email, "email_source": source, "suburb": suburb, "trade": trade})
        time.sleep(0.5)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"leads": results}, f, indent=2, ensure_ascii=False)

    with_email = [r for r in results if r.get("email")]
    print(f"\nDone — {found}/{len(businesses)} emails found → {OUTPUT_FILE}")
    print(f"Ready to send: {len(with_email)}")


if __name__ == "__main__":
    main()
