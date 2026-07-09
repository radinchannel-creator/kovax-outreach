#!/usr/bin/env python3
"""
Full automation outreach pipeline.

  0. Cleanup — mark bounces + opt-outs from inbox (keeps blocklist fresh)
  1. Find local businesses via Google Maps (find_local_businesses.py)
  2. Scrape contact emails (find_automation_emails.py)
  3. Send initial pitch emails, skip already-contacted (run_automation_outreach.py)
  4. Send 7-day follow-ups to non-responders (run_followup_outreach.py)

Usage:
  python tools/run_automation_pipeline.py                    # full run
  python tools/run_automation_pipeline.py --dry-run          # no sends, preview only
  python tools/run_automation_pipeline.py --skip-scrape      # reuse existing local_businesses.json
  python tools/run_automation_pipeline.py --limit 50         # cap initial emails this run
  python tools/run_automation_pipeline.py --skip-followup    # skip step 4
  python tools/run_automation_pipeline.py --force            # ignore business hours
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOG_FILE = BASE_DIR / ".tmp" / "automation_pipeline_log.txt"
PYTHON   = sys.executable  # works in .venv locally and in GitHub Actions


def count_unsent_leads() -> int:
    """Return how many leads have an email address that haven't been sent to yet."""
    leads_path = BASE_DIR / ".tmp" / "automation_leads.json"
    sent_path  = BASE_DIR / ".tmp" / "automation_sent.json"
    if not leads_path.exists():
        return 0
    try:
        leads = json.loads(leads_path.read_text(encoding="utf-8")).get("leads", [])
        with_email = {l["email"].strip().lower() for l in leads if l.get("email")}
    except Exception:
        return 0
    if not sent_path.exists():
        return len(with_email)
    try:
        sent = {s.strip().lower() for s in json.loads(sent_path.read_text(encoding="utf-8"))}
    except Exception:
        sent = set()
    return len(with_email - sent)


def log(msg: str) -> None:
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(label: str, script: str, extra: list = None) -> bool:
    cmd = [PYTHON, str(BASE_DIR / "tools" / script)] + (extra or [])
    log(f"START: {label}")
    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    if result.returncode == 0:
        log(f"OK:    {label}")
        return True
    log(f"FAIL:  {label} (exit {result.returncode})")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",       action="store_true", help="Preview only — no sends")
    parser.add_argument("--skip-scrape",   action="store_true", help="Skip steps 1+2, reuse existing data")
    parser.add_argument("--skip-followup", action="store_true", help="Skip step 4 follow-ups")
    parser.add_argument("--limit",         type=int,            help="Cap initial emails this run")
    parser.add_argument("--force",         action="store_true", help="Ignore business hours check")
    args = parser.parse_args()

    log("=" * 60)
    log("Automation Outreach Pipeline — START")
    log("=" * 60)

    # Step 0: Cleanup — mark bounces and opt-outs before anything else
    run("Mark email bounces",  "mark_bounces.py")
    run("Mark opt-out replies", "mark_optouts.py")

    need_scrape = not args.skip_scrape
    if args.skip_scrape:
        available = count_unsent_leads()
        limit     = args.limit or 50
        if available < limit:
            log(f"INFO:  Only {available} unsent leads available (need {limit}) — running fresh scrape")
            need_scrape = True
        else:
            log(f"SKIP:  Find + scrape ({available} leads available, --skip-scrape set)")

    if need_scrape:
        # Step 1: Google Maps scrape
        if not run("Find local businesses (Google Maps)", "find_local_businesses.py"):
            log("Aborted — could not find businesses. Check GOOGLE_PLACES_API_KEY.")
            sys.exit(1)
        # Step 2: Scrape emails
        if not run("Scrape contact emails", "find_automation_emails.py"):
            log("Aborted — email scraping failed.")
            sys.exit(1)

    # Step 3: Initial outreach
    outreach_args = []
    if args.dry_run:
        outreach_args.append("--dry-run")
    if args.force:
        outreach_args.append("--force")
    if args.limit:
        outreach_args += ["--limit", str(args.limit)]

    run("Send initial pitch emails", "run_automation_outreach.py", outreach_args)

    # Step 4: Follow-ups
    if not args.skip_followup and not args.dry_run:
        followup_args = ["--force"] if args.force else []
        run("Send 7-day follow-ups", "run_followup_outreach.py", followup_args)
    else:
        log("SKIP:  Follow-ups")

    log("=" * 60)
    log("Pipeline complete.")
    log("=" * 60)


if __name__ == "__main__":
    main()
