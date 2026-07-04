#!/usr/bin/env python3
"""
Scan Gmail inbox for delivery failure notifications and add the bounced
addresses to .tmp/automation_sent.json so they're never retried.

Usage:
  python tools/mark_bounces.py            # scan + mark bounces
  python tools/mark_bounces.py --dry-run  # show bounces, don't write
"""

import argparse
import email
import imaplib
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR  = Path(__file__).parent.parent
SENT_FILE = BASE_DIR / ".tmp" / "automation_sent.json"

load_dotenv(BASE_DIR / ".env")

GMAIL_USER = os.getenv("KOVAX_EMAIL", "radin@kovax.com.au")
APP_PASS   = os.getenv("KOVAX_GMAIL_APP_PASSWORD", "")

# Patterns that identify a delivery failure message
FAILURE_SUBJECTS = re.compile(
    r"(delivery (status notification|failure|failed)|"
    r"undelivered mail|mail delivery (subsystem|failed)|"
    r"returned mail|failure notice|message not delivered)",
    re.IGNORECASE,
)

# Extract the original To: address from a bounce body
RCPT_RE = re.compile(
    r"(?:final recipient|original recipient|to|rcpt to)[^\n]*?[:\s]+"
    r"(?:rfc822;)?\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)


def load_sent() -> set[str]:
    if SENT_FILE.exists():
        with open(SENT_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent(sent: set[str]) -> None:
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(sent), f, indent=2)


def get_text(msg) -> str:
    parts = []
    for part in msg.walk():
        ct = part.get_content_type()
        if ct in ("text/plain", "text/html"):
            try:
                parts.append(part.get_payload(decode=True).decode("utf-8", errors="replace"))
            except Exception:
                pass
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not APP_PASS:
        print("ERROR: KOVAX_GMAIL_APP_PASSWORD not set in .env")
        sys.exit(1)

    print(f"Connecting to Gmail IMAP as {GMAIL_USER}...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, APP_PASS)
    mail.select("inbox")

    # IMAP OR syntax: OR criterion1 criterion2 (no parentheses)
    status, data = mail.search(None, 'OR FROM "mailer-daemon" FROM "postmaster"')
    uids = data[0].split() if data[0] else []
    print(f"Found {len(uids)} potential bounce messages")

    bounced_addresses = set()

    for uid in uids:
        status, msg_data = mail.fetch(uid, "(RFC822)")
        if status != "OK":
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = msg.get("Subject", "")

        if not FAILURE_SUBJECTS.search(subject):
            continue  # not a delivery failure

        body = get_text(msg)
        matches = RCPT_RE.findall(body)
        for addr in matches:
            addr = addr.lower().strip()
            # Only care about addresses we actually sent to (skip our own)
            if addr != GMAIL_USER.lower():
                bounced_addresses.add(addr)
                print(f"  Bounced: {addr}  (from: {subject[:60]})")

    mail.logout()

    if not bounced_addresses:
        print("No new bounced addresses found.")
        return

    sent_set = load_sent()
    new_bounces = bounced_addresses - sent_set
    print(f"\nNew bounced addresses to block: {len(new_bounces)}")

    if args.dry_run:
        print("(dry-run — not writing)")
        return

    sent_set |= bounced_addresses
    save_sent(sent_set)
    print(f"Marked {len(new_bounces)} addresses as do-not-contact in {SENT_FILE}")


if __name__ == "__main__":
    main()
