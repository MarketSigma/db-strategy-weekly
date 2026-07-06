#!/usr/bin/env python3

import os
import sys
from datetime import date
import resend


def build_subject():
    base_subject = os.environ.get(
        "DB_STRATEGY_WEEKLY_EMAIL_SUBJECT",
        "DB Strategy Weekly"
    ).strip()

    today = f"{date.today():%d %B %Y}"

    if "{date}" in base_subject:
        return base_subject.replace("{date}", today).strip()

    if today in base_subject:
        return base_subject

    base_subject = base_subject.rstrip(" -–—")
    return f"{base_subject} — {today}"


def split_emails(value):
    return [x.strip() for x in value.split(",") if x.strip()]


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else "drafts.html"

    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    resend.api_key = os.environ["DB_STRATEGY_WEEKLY_RESEND_API_KEY"]

    sender = os.environ["DB_STRATEGY_WEEKLY_EMAIL_FROM"]
    subject = build_subject()

    final_recipients = os.environ.get("DB_STRATEGY_WEEKLY_FINAL_EMAIL_TO", "").strip()
    approver = os.environ.get("DB_STRATEGY_WEEKLY_APPROVER_EMAIL", "").strip()

    if final_recipients:
        resend.Emails.send({
            "from": sender,
            "to": ["updates@market-sigma.com"],
            "bcc": split_emails(final_recipients),
            "subject": subject,
            "html": html
        })

        print(f"Sent final article '{subject}' to BCC distribution list")

    elif approver:
        resend.Emails.send({
            "from": sender,
            "to": split_emails(approver),
            "subject": subject,
            "html": html
        })

        print(f"Sent draft approval '{subject}' to approver only")

    else:
        raise ValueError(
            "No recipients found. Set either DB_STRATEGY_WEEKLY_FINAL_EMAIL_TO or DB_STRATEGY_WEEKLY_APPROVER_EMAIL."
        )


if __name__ == "__main__":
    main()
