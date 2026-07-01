#!/usr/bin/env python3

import os
import sys
from datetime import date
import resend


def build_subject():
    """
    Build a clean subject without duplicate dashes.

    Supported examples:
    - DB Weekly Opportunities & Risks
      -> DB Weekly Opportunities & Risks — 01 July 2026

    - DB Weekly Opportunities & Risks —
      -> DB Weekly Opportunities & Risks — 01 July 2026

    - DB Weekly Opportunities & Risks - 
      -> DB Weekly Opportunities & Risks — 01 July 2026

    - DB Weekly Opportunities & Risks — {date}
      -> DB Weekly Opportunities & Risks — 01 July 2026
    """
    base_subject = os.environ.get(
        "DB_STRATEGY_WEEKLY_EMAIL_SUBJECT",
        "DB Strategy Weekly"
    ).strip()

    today = f"{date.today():%d %B %Y}"

    if "{date}" in base_subject:
        return base_subject.replace("{date}", today).strip()

    if today in base_subject:
        return base_subject

    # Remove trailing separators so we add only one clean em dash.
    base_subject = base_subject.rstrip(" -–—")

    return f"{base_subject} — {today}"


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else "drafts.html"

    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    resend.api_key = os.environ["DB_STRATEGY_WEEKLY_RESEND_API_KEY"]

    sender = os.environ["DB_STRATEGY_WEEKLY_EMAIL_FROM"]
    to = os.environ.get(
        "DB_STRATEGY_WEEKLY_APPROVER_EMAIL",
        os.environ.get("DB_STRATEGY_WEEKLY_FINAL_EMAIL_TO")
    )

    subject = build_subject()

    resend.Emails.send({
        "from": sender,
        "to": [x.strip() for x in to.split(",") if x.strip()],
        "subject": subject,
        "html": html
    })

    print(f"Sent '{subject}' to {to}")


if __name__ == "__main__":
    main()

    
