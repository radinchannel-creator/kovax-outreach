#!/usr/bin/env python3
"""
Scan Gmail inbox for replies to our outreach emails.
  - Any reply from someone we emailed → mark as "replied" (skip follow-ups)
  - Replies containing opt-out keywords → also add to blocklist (never email again)

Two-pass IMAP scan: headers first (fast), full body only for matches.

Updates:
  .tmp/automation_sent.json     (adds opt-outs to blocklist)
  .tmp/automation_replied.json  (all repliers — used to skip follow-ups)
"""

import argparse
import email as email_lib
import imaplib
import json
import os
import re
import socket
import sys
from pathlib import Path

from dotenv import load_dotenv

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

socket.setdefaulttimeout(60)


def load_json_set(path: Path) -> set[str]:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_json_set(path: Path, s: set[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(s), f, indent=2)


def connect() -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, APP_PASS)
    mail.select("inbox")
    return mail


def get_plain_text(msg) -> str:
    parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                parts.append(part.get_payload(decode=True).decode("utf-8", errors="replace"))
            except Exception:
                pass
    return "\n".join(parts)


def extract_from_address(raw: bytes) -> str | None:
    msg = email_lib.message_from_bytes(raw)
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
    try:
        mail = connect()
    except Exception as e:
        print(f"ERROR: Could not connect to Gmail IMAP: {e}")
        sys.exit(1)

    # Get all message UIDs
    status, data = mail.search(None, "ALL")
    uids = data[0].split() if data[0] else []
    print(f"Pass 1: scanning {len(uids)} message headers...")

    # Pass 1: fetch only From headers in batches — much faster than full RFC822
    candidates = []  # (uid_bytes, sender_str)
    BATCH = 50
    for i in range(0, len(uids), BATCH):
        batch = b",".join(uids[i : i + BATCH])
        try:
            status, items = mail.fetch(batch, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
        except (imaplib.IMAP4.abort, OSError):
            try:
                mail = connect()
                status, items = mail.fetch(batch, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            except Exception:
                print(f"  Skipping batch {i//BATCH + 1} after reconnect failure")
                continue

        for item in items:
            if not isinstance(item, tuple):
                continue
            uid_match = re.search(rb"(\d+) FETCH", item[0])
            if not uid_match:
                continue
            uid = uid_match.group(1)
            sender = extract_from_address(item[1])
            if sender and sender in sent_set and sender != GMAIL_USER.lower():
                candidates.append((uid, sender))

    print(f"Pass 2: fetching full body for {len(candidates)} candidate(s)...")

    new_replied = set()
    new_optouts = set()

    for uid, sender in candidates:
        if sender in replied_set:
            continue
        try:
            status, msg_data = mail.fetch(uid, "(RFC822)")
        except (imaplib.IMAP4.abort, OSError):
            try:
                mail = connect()
                status, msg_data = mail.fetch(uid, "(RFC822)")
            except Exception:
                print(f"  Skipping {sender} after reconnect failure")
                continue

        if status != "OK":
            continue

        msg = email_lib.message_from_bytes(msg_data[0][1])
        body = get_plain_text(msg)
        is_optout = bool(OPTOUT_RE.search(body))

        new_replied.add(sender)
        label = "OPT-OUT" if is_optout else "reply"
        print(f"  [{label}] {sender}  — {msg.get('Subject', '')[:60]}")

        if is_optout:
            new_optouts.add(sender)

    try:
        mail.logout()
    except Exception:
        pass

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
