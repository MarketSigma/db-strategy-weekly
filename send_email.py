#!/usr/bin/env python3

import os
import sys
from datetime import date
import resend


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

    subject = os.environ.get(
        "DB_STRATEGY_WEEKLY_EMAIL_SUBJECT",
        f"Approval Required — DB Strategy Weekly — {date.today():%d %b %Y}"
    )

    resend.Emails.send({
        "from": sender,
        "to": [x.strip() for x in to.split(",") if x.strip()],
        "subject": subject,
        "html": html
    })

    print(f"Sent '{subject}' to {to}")


if __name__ == "__main__":
    main()
