#!/usr/bin/env python3
"""
Scan Gmail inbox for replies to our outreach emails.
  - Any reply from someone we emailed → mark as "replied" (skip follow-ups)
  - Replies containing opt-out keywords → also add to blocklist (never email again)

Updates:
  .tmp/automation_sent.json     (adds opt-outs to blocklist)
  .tmp/automation_replied.json  (all repliers — used to skip follow-ups)

Usage:
  python tools/mark_optouts.py            # scan + write
  python tools/mark_optouts.py --dry-run  # show matches, don't write
"""

import argparse
import email as email_lib
import imaplib
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR      = Path(__file__).parent.parent
SENT_FILE     = BASE_DIR / ".tmp" / "automation_sent.json"
REPLIED_FILE  = BASE_DIR / ".tmp" / "automation_replied.json"

load_dotenv(BASE_DIR / ".env")
GMAIL_USER = os.getenv("KOVAX_EMAIL", "radin@kovax.com.au")
APP_PASS   = os.getenv("KOVAX_GMAIL_APP_PASSWORD", "")

OPTOUT_RE = re.compile(
    r"\b(not interested|no thanks|remove me|unsubscribe|stop (emailing|contacting)|"
    r"please (don'?t|do not) (contact|email)|take me off|opt.?out|leave me alone|"
    r"don'?t (contact|email) me again)\b",
    re.IGNORECASE,
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def load_json_set(path: Path) -> set[str]:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_json_set(path: Path, s: set[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(s), f, indent=2)


def get_plain_text(msg) -> str:
    parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                parts.append(part.get_payload(decode=True).decode("utf-8", errors="replace"))
            except Exception:
                pass
    return "\n".join(parts)


def extract_from_address(msg) -> str | None:
    from_header = msg.get("From", "")
    matches = EMAIL_RE.findall(from_header)
    return matches[0].lower() if matches else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not APP_PASS:
        print("ERROR: KOVAX_GMAIL_APP_PASSWORD not set in .env")
        sys.exit(1)

    sent_set    = load_json_set(SENT_FILE)
    replied_set = load_json_set(REPLIED_FILE)

    if not sent_set:
        print("No sent addresses found. Nothing to check against.")
        return

    print(f"Connecting to Gmail IMAP as {GMAIL_USER}...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, APP_PASS)
    mail.select("inbox")

    # Search recent inbox
    status, data = mail.search(None, "ALL")
    uids = data[0].split() if data[0] else []
    print(f"Scanning {len(uids)} inbox messages...")

    new_replied  = set()
    new_optouts  = set()

    for uid in uids:
        status, msg_data = mail.fetch(uid, "(RFC822)")
        if status != "OK":
            continue
        msg = email_lib.message_from_bytes(msg_data[0][1])
        sender = extract_from_address(msg)
        if not sender or sender == GMAIL_USER.lower():
            continue
        if sender not in sent_set:
            continue  # not someone we emailed

        body = get_plain_text(msg)
        is_optout = bool(OPTOUT_RE.search(body))

        if sender not in replied_set:
            new_replied.add(sender)
            label = "OPT-OUT" if is_optout else "reply"
            print(f"  [{label}] {sender}  — {msg.get('Subject', '')[:60]}")

        if is_optout:
            new_optouts.add(sender)

    mail.logout()

    if not new_replied:
        print("No new replies found from people we emailed.")
        return

    print(f"\nNew repliers:  {len(new_replied)}")
    print(f"New opt-outs:  {len(new_optouts)}")

    if args.dry_run:
        print("(dry-run — not writing)")
        return

    replied_set |= new_replied
    save_json_set(REPLIED_FILE, replied_set)

    if new_optouts:
        sent_set |= new_optouts
        save_json_set(SENT_FILE, sent_set)
        print(f"Added {len(new_optouts)} opt-outs to blocklist.")

    print("Done.")


if __name__ == "__main__":
    main()
