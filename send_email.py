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

    # Base subject from the workflow; the date is appended automatically each run.
    base_subject = os.environ.get("DB_STRATEGY_WEEKLY_EMAIL_SUBJECT", "DB Strategy Weekly")
    subject = f"{base_subject} \u2014 {date.today():%d %B %Y}"

    resend.Emails.send({
        "from": sender,
        "to": [x.strip() for x in to.split(",") if x.strip()],
        "subject": subject,
        "html": html
    })

    print(f"Sent '{subject}' to {to}")


if __name__ == "__main__":
    main()

    
