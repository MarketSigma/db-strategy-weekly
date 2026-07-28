#!/usr/bin/env python3

import os
import sys
from datetime import date
import resend


def build_subject():
    base_subject = os.environ.get(
        "DB_STRATEGY_WEEKLY_EMAIL_SUBJECT",
        "DB Strategy Weekly Opportunities & Risks"
    ).strip()

    today = f"{date.today():%d %B %Y}"

    if "{date}" in base_subject:
        return base_subject.replace("{date}", today).strip()

    if today in base_subject:
        return base_subject

    base_subject = base_subject.rstrip(" -–—")
    return f"{base_subject} — {today}"


def split_emails(value):
    emails = []
    seen = set()

    for x in value.split(","):
        email = x.strip()
        key = email.lower()

        if email and key not in seen:
            emails.append(email)
            seen.add(key)

    return emails


def remove_overlap(bcc_list, to_list):
    to_set = {x.lower() for x in to_list}
    return [x for x in bcc_list if x.lower() not in to_set]


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else "drafts.html"

    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    resend.api_key = os.environ["DB_STRATEGY_WEEKLY_RESEND_API_KEY"]

    sender = os.environ["DB_STRATEGY_WEEKLY_EMAIL_FROM"]
    subject = build_subject()

    final_recipients_raw = os.environ.get("DB_STRATEGY_WEEKLY_FINAL_EMAIL_TO", "").strip()
    approver_raw = os.environ.get("DB_STRATEGY_WEEKLY_APPROVER_EMAIL", "").strip()

    final_recipients = split_emails(final_recipients_raw)
    approver = split_emails(approver_raw)

    if final_recipients:
        to_recipients = approver if approver else [sender]
        bcc_recipients = remove_overlap(final_recipients, to_recipients)

        resend.Emails.send({
            "from": sender,
            "to": to_recipients,
            "bcc": bcc_recipients,
            "subject": subject,
            "html": html
        })

        print(f"Sent final article '{subject}'")
        print(f"To: {to_recipients}")
        print(f"BCC: {bcc_recipients}")

    elif approver:
        resend.Emails.send({
            "from": sender,
            "to": approver,
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
