#!/usr/bin/env python3
"""
Send 7-day follow-up emails to non-responders.

Reads .tmp/automation_send_log.json to find addresses emailed 7+ days ago
that have not yet received a follow-up and have not replied/opted out.

Usage:
  python tools/run_followup_outreach.py             # send follow-ups
  python tools/run_followup_outreach.py --dry-run   # preview only
  python tools/run_followup_outreach.py --days 5    # change follow-up window (default 7)
  python tools/run_followup_outreach.py --force     # ignore business hours
"""

import argparse
import json
import os
import smtplib
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR      = Path(__file__).parent.parent
SEND_LOG_FILE = BASE_DIR / ".tmp" / "automation_send_log.json"
REPLIED_FILE  = BASE_DIR / ".tmp" / "automation_replied.json"
SENT_FILE     = BASE_DIR / ".tmp" / "automation_sent.json"

load_dotenv(BASE_DIR / ".env")
FROM_EMAIL   = os.getenv("KOVAX_EMAIL", "radin@kovax.com.au")
APP_PASSWORD = os.getenv("KOVAX_GMAIL_APP_PASSWORD", "")
SEND_DELAY   = 6
SYDNEY_TZ    = ZoneInfo("Australia/Sydney")


def is_business_hours() -> bool:
    now = datetime.now(SYDNEY_TZ)
    return now.weekday() < 5 and 9 <= now.hour < 17


def load_json_set(path: Path) -> set[str]:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def load_send_log() -> dict:
    if SEND_LOG_FILE.exists():
        with open(SEND_LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_send_log(log: dict) -> None:
    with open(SEND_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def build_followup(email: str, record: dict) -> tuple[str, str, str]:
    name = record.get("name", "your business")

    subject = f"following up — {name}"

    plain = (
        f"Hi,\n\n"
        f"Just following up on my last message about automating some of the day-to-day "
        f"for {name}.\n\n"
        f"Totally understand if the timing isn't right — just wanted to make sure it "
        f"didn't get buried.\n\n"
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
  <p style="margin:0 0 16px;color:#333;font-size:15px;line-height:1.75;">
    Just following up on my last message about automating some of the day-to-day
    for <strong>{name}</strong>.
  </p>
  <p style="margin:0 0 24px;color:#333;font-size:15px;line-height:1.75;">
    Totally understand if the timing isn&rsquo;t right &mdash; just wanted to make
    sure it didn&rsquo;t get buried.
  </p>
  <p style="margin:0 0 4px;color:#333;font-size:15px;line-height:1.75;">Cheers,</p>
  <p style="margin:0 0 2px;color:#333;font-size:15px;line-height:1.75;font-weight:bold;">Radin</p>
  <p style="margin:0;"><a href="https://kovax.com.au" style="color:#555;font-size:13px;">kovax.com.au</a></p>
</td></tr>
<tr><td style="padding:16px 40px 24px;border-top:1px solid #eee;">
  <p style="margin:0;color:#aaa;font-size:11px;line-height:1.6;">
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days",    type=int, default=7, help="Days to wait before follow-up (default 7)")
    parser.add_argument("--force",   action="store_true", help="Ignore business hours check")
    args = parser.parse_args()

    if not args.dry_run and not args.force and not is_business_hours():
        now = datetime.now(SYDNEY_TZ)
        print(f"Outside business hours (Sydney: {now.strftime('%a %H:%M')}). Use --force to override.")
        sys.exit(0)

    send_log    = load_send_log()
    replied_set = load_json_set(REPLIED_FILE)
    cutoff      = datetime.now(timezone.utc) - timedelta(days=args.days)

    eligible = []
    for email, record in send_log.items():
        if record.get("followup_sent_at"):
            continue  # already followed up
        if email in replied_set:
            continue  # they replied — leave them alone
        sent_at_str = record.get("sent_at")
        if not sent_at_str:
            continue
        try:
            sent_at = datetime.fromisoformat(sent_at_str)
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            if sent_at > cutoff:
                continue  # too recent
        except ValueError:
            continue
        eligible.append((email, record))

    print(f"Follow-ups due (sent {args.days}+ days ago, no reply): {len(eligible)}")

    if not eligible:
        print("Nothing to follow up on yet.")
        return

    if args.dry_run:
        print("\n── DRY RUN PREVIEW ──────────────────────────────────\n")
        for email, record in eligible[:5]:
            subj, html, plain = build_followup(email, record)
            print(f"TO:      {email}")
            print(f"SUBJECT: {subj}")
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
            print(f"Logged in. Sending {len(eligible)} follow-ups...\n")

            for i, (email, record) in enumerate(eligible):
                name  = record.get("name", "Unknown")
                subj, html, plain = build_followup(email, record)

                try:
                    smtp_send(email, subj, html, plain, smtp)
                    now_iso = datetime.now(SYDNEY_TZ).isoformat()
                    print(f"[{i+1}/{len(eligible)}] SENT  {name[:40]:<40} → {email}")
                    sent_count += 1
                    send_log[email]["followup_sent_at"] = now_iso
                    save_send_log(send_log)
                    time.sleep(SEND_DELAY)
                except Exception as e:
                    print(f"[{i+1}/{len(eligible)}] FAIL  {name[:40]:<40} → {e}")
                    fail_count += 1

    except smtplib.SMTPAuthenticationError:
        print("ERROR: Gmail auth failed. Check KOVAX_GMAIL_APP_PASSWORD in .env")
        sys.exit(1)

    print(f"\n── Done ─────────────────────────────────────────────")
    print(f"Follow-ups sent:   {sent_count}")
    print(f"Failed:            {fail_count}")


if __name__ == "__main__":
    main()
