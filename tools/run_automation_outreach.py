#!/usr/bin/env python3
"""
Send automation pitch emails to all leads with a found email.
Deduplicates across runs using .tmp/automation_sent.json.

Input:  .tmp/automation_leads.json
Output: .tmp/automation_sent.json     (blocklist — grows forever)
        .tmp/automation_send_log.json  (timestamps + follow-up tracking)

Usage:
  python tools/run_automation_outreach.py             # send for real
  python tools/run_automation_outreach.py --dry-run   # preview only, no sends
  python tools/run_automation_outreach.py --limit 5   # cap at 5 emails
  python tools/run_automation_outreach.py --force     # send even outside business hours
"""

import argparse
import json
import os
import smtplib
import sys
import time
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import dns.resolver
from dotenv import load_dotenv

BASE_DIR      = Path(__file__).parent.parent
LEADS_FILE    = BASE_DIR / ".tmp" / "automation_leads.json"
SENT_FILE     = BASE_DIR / ".tmp" / "automation_sent.json"
SEND_LOG_FILE = BASE_DIR / ".tmp" / "automation_send_log.json"

load_dotenv(BASE_DIR / ".env")

FROM_EMAIL   = os.getenv("KOVAX_EMAIL", "radin@kovax.com.au")
APP_PASSWORD = os.getenv("KOVAX_GMAIL_APP_PASSWORD", "")
SEND_DELAY   = 6
SYDNEY_TZ    = ZoneInfo("Australia/Sydney")

# Website builders that embed generic placeholder emails — always bounce.
FAKE_DOMAINS = {
    "mysite.com", "wixsite.com", "wix.com", "squarespace.com",
    "weebly.com", "jimdo.com", "strikingly.com", "godaddysites.com",
    "yolasite.com", "site123.me", "webnode.com", "webflow.io",
    "sites.google.com", "wordpress.com", "blogspot.com",
    "tumblr.com", "mailinator.com", "guerrillamail.com",
    "example.com", "test.com",
}

# ── Industry categorisation ────────────────────────────────────────────────────

TRADE_KW = {
    "electrician", "plumber", "builder", "carpenter", "painter",
    "landscaper", "concreter", "tiler", "roofer", "fencer", "mechanic",
    "panel beater", "locksmith", "pest control", "cleaner", "cleaning",
    "removalist", "gardener", "handyman", "glazier",
}
HEALTH_KW = {
    "dentist", "physiotherapist", "physio", "chiropractor", "optometrist",
    "gym", "yoga", "pilates", "doctor", "medical", "allied health",
    "psychologist", "naturopath", "osteopath",
}
HOSPITALITY_KW = {
    "restaurant", "cafe", "catering", "bakery", "coffee", "food",
    "pizzeria", "sushi", "thai", "italian", "chinese",
}
BEAUTY_KW = {
    "hair salon", "beauty salon", "dog groomer", "barber", "nail salon",
    "beauty therapist", "massage", "waxing", "lash",
}
PROFESSIONAL_KW = {
    "accountant", "bookkeeper", "solicitor", "mortgage broker",
    "real estate agent", "financial planner", "lawyer", "conveyancer",
    "insurance", "tax agent",
}

INDUSTRY_COPY = {
    "trade": (
        "I help tradies win more work without the extra admin — things like missed-call "
        "text-backs (so leads don't go cold while you're on the tools), instant quote "
        "request forms, and automated Google review nudges after each job."
    ),
    "health": (
        "I help health and wellness businesses cut down on no-shows and admin — things "
        "like automated appointment reminders, rebooking nudges for lapsed patients, "
        "and review requests after appointments."
    ),
    "hospitality": (
        "I help local food businesses automate the small stuff — things like booking "
        "confirmations, review request follow-ups after a visit, and missed-call "
        "text-backs so customers don't head to the place next door."
    ),
    "beauty": (
        "I help salons and beauty businesses reduce no-shows and fill quiet slots — "
        "things like automated appointment reminders, rebooking nudges for inactive "
        "clients, and review requests after each visit."
    ),
    "professional": (
        "I help professional services firms speed up client follow-up — things like "
        "lead response sequences, appointment reminders, quote follow-ups, and "
        "review requests after each engagement."
    ),
    "default": (
        "I help local businesses automate the repetitive stuff — things like "
        "missed-call text-backs, appointment reminders, AI review requests, and "
        "quote builders. We also do custom builds if you've got a specific process "
        "eating up your time."
    ),
}


def categorize(trade: str) -> str:
    t = trade.lower()
    for kw in TRADE_KW:
        if kw in t:
            return "trade"
    for kw in HEALTH_KW:
        if kw in t:
            return "health"
    for kw in HOSPITALITY_KW:
        if kw in t:
            return "hospitality"
    for kw in BEAUTY_KW:
        if kw in t:
            return "beauty"
    for kw in PROFESSIONAL_KW:
        if kw in t:
            return "professional"
    return "default"


# ── MX validation ──────────────────────────────────────────────────────────────

def has_mx(domain: str) -> bool:
    try:
        dns.resolver.resolve(domain, "MX", lifetime=5)
        return True
    except Exception:
        return False


# ── Business hours ─────────────────────────────────────────────────────────────

def is_business_hours() -> bool:
    now = datetime.now(SYDNEY_TZ)
    return now.weekday() < 5 and 9 <= now.hour < 17


# ── Persistence helpers ────────────────────────────────────────────────────────

def load_sent() -> set[str]:
    if SENT_FILE.exists():
        with open(SENT_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent(sent: set[str]) -> None:
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(sent), f, indent=2)


def load_send_log() -> dict:
    if SEND_LOG_FILE.exists():
        with open(SEND_LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_send_log(log: dict) -> None:
    with open(SEND_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ── Email building ─────────────────────────────────────────────────────────────

def build_email(lead: dict) -> tuple[str, str, str]:
    name     = lead.get("name", "your business")
    trade    = (lead.get("trade") or "business").lower()
    category = categorize(trade)
    body     = INDUSTRY_COPY[category]

    subject = f"quick one for {name}"

    plain = (
        f"Hi,\n\n"
        f"{body}\n\n"
        f"If any of that sounds useful for {name}, let's jump on a quick call.\n\n"
        f"Cheers,\nRadin\nkovax.com.au\n"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:30px 0;">
<tr><td align="center">
<table width="580" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;">
<tr><td style="padding:36px 40px 32px;">
  <p style="margin:0 0 18px;color:#333;font-size:15px;line-height:1.75;">Hi,</p>
  <p style="margin:0 0 16px;color:#333;font-size:15px;line-height:1.75;">{body}</p>
  <p style="margin:0 0 24px;color:#333;font-size:15px;line-height:1.75;">
    If any of that sounds useful for <strong>{name}</strong>, let&rsquo;s jump on a quick call.
  </p>
  <p style="margin:0 0 4px;color:#333;font-size:15px;line-height:1.75;">Cheers,</p>
  <p style="margin:0 0 2px;color:#333;font-size:15px;line-height:1.75;font-weight:bold;">Radin</p>
  <p style="margin:0;"><a href="https://kovax.com.au" style="color:#555;font-size:13px;">kovax.com.au</a></p>
</td></tr>
<tr><td style="padding:16px 40px 24px;border-top:1px solid #eee;">
  <p style="margin:0;color:#aaa;font-size:11px;line-height:1.6;">
    You&rsquo;re receiving this because your business appeared in a local search.
    Not interested? Just reply and I won&rsquo;t contact you again.
  </p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

    return subject, html, plain


def smtp_send(to: str, subject: str, html: str, plain: str, conn: smtplib.SMTP_SSL) -> None:
    msg = MIMEMultipart("alternative")
    msg["From"]       = f"Radin Asgari <{FROM_EMAIL}>"
    msg["To"]         = to
    msg["Subject"]    = subject
    msg["Reply-To"]   = FROM_EMAIL
    msg["Message-ID"] = f"<{uuid.uuid4()}@kovax.com.au>"
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))
    conn.sendmail(FROM_EMAIL, to, msg.as_string())


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview emails, don't send")
    parser.add_argument("--limit",   type=int,            help="Max emails to send this run")
    parser.add_argument("--force",   action="store_true", help="Send even outside business hours")
    args = parser.parse_args()

    # Business hours check
    if not args.dry_run and not args.force and not is_business_hours():
        now = datetime.now(SYDNEY_TZ)
        print(f"Outside business hours (Sydney time: {now.strftime('%a %H:%M')}). "
              f"Emails get better open rates 9am–5pm Mon–Fri.\n"
              f"Use --force to send anyway, or let the weekly cron handle it.")
        sys.exit(0)

    if not LEADS_FILE.exists():
        print(f"ERROR: {LEADS_FILE} not found. Run find_automation_emails.py first.")
        sys.exit(1)

    with open(LEADS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    leads = data.get("leads", [])

    sent_set = load_sent()
    send_log = load_send_log()
    print(f"Already sent to {len(sent_set)} addresses across all previous runs.")

    print("Validating email domains (MX check)...")
    eligible     = []
    skipped_fake = 0
    skipped_no_mx = 0
    for lead in leads:
        email = lead.get("email")
        if not email or email in sent_set or len(lead.get("name", "")) <= 2:
            continue
        domain = email.split("@", 1)[-1].lower()
        if domain in FAKE_DOMAINS:
            print(f"  SKIP (fake domain) {email}")
            skipped_fake += 1
            continue
        if not has_mx(domain):
            print(f"  SKIP (no MX)       {email}")
            skipped_no_mx += 1
            continue
        eligible.append(lead)

    print(f"Skipped — fake domain: {skipped_fake}, no MX: {skipped_no_mx}")
    print(f"Eligible this run: {len(eligible)}")

    if args.limit:
        eligible = eligible[:args.limit]
        print(f"Capped at {args.limit}")

    if not eligible:
        print("Nothing to send. Run find_local_businesses.py + find_automation_emails.py to find more leads.")
        return

    if args.dry_run:
        print("\n── DRY RUN PREVIEW ──────────────────────────────────\n")
        for lead in eligible[:5]:
            subj, html, plain = build_email(lead)
            cat = categorize((lead.get("trade") or "").lower())
            print(f"TO:       {lead['email']}")
            print(f"SUBJECT:  {subj}")
            print(f"CATEGORY: {cat}")
            print(plain)
            print("─" * 50)
        if len(eligible) > 5:
            print(f"... and {len(eligible) - 5} more")
        return

    print(f"\nConnecting to Gmail SMTP...")
    sent_count = 0
    fail_count = 0

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(FROM_EMAIL, APP_PASSWORD)
            print(f"Logged in. Sending {len(eligible)} emails...\n")

            for i, lead in enumerate(eligible):
                email = lead["email"]
                name  = lead.get("name", "Unknown")
                trade = (lead.get("trade") or "business").lower()
                subj, html, plain = build_email(lead)

                try:
                    smtp_send(email, subj, html, plain, smtp)
                    now_iso = datetime.now(SYDNEY_TZ).isoformat()
                    print(f"[{i+1}/{len(eligible)}] SENT [{categorize(trade):12s}] {name[:35]:<35} → {email}")
                    sent_count += 1
                    sent_set.add(email)
                    save_sent(sent_set)
                    send_log[email] = {
                        "name":             name,
                        "trade":            trade,
                        "sent_at":          now_iso,
                        "followup_sent_at": None,
                    }
                    save_send_log(send_log)
                    time.sleep(SEND_DELAY)
                except Exception as e:
                    print(f"[{i+1}/{len(eligible)}] FAIL {name[:38]:<38} → {e}")
                    fail_count += 1

    except smtplib.SMTPAuthenticationError:
        print("ERROR: Gmail auth failed. Check KOVAX_GMAIL_APP_PASSWORD in .env")
        sys.exit(1)

    print(f"\n── Done ─────────────────────────────────────────────")
    print(f"Sent:   {sent_count}")
    print(f"Failed: {fail_count}")
    print(f"Total ever sent (lifetime): {len(sent_set)}")


if __name__ == "__main__":
    main()
