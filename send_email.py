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


def prepare_inline_logo(html_body):
    """
    Embed the Doha Bank logo as an inline CID image when DB_LOGO_URL is set.

    This is more reliable in Outlook desktop than relying on a normal remote
    <img> request. If the secret is missing, the HTML is left unchanged.
    """
    logo_url = os.environ.get("DB_LOGO_URL", "").strip()

    if not logo_url:
        return html_body, []

    cid = "doha-bank-logo"
    updated_html = html_body.replace(logo_url, f"cid:{cid}")

    attachment = {
        "path": logo_url,
        "filename": "doha-bank-logo.png",
        "content_id": cid,
    }

    return updated_html, [attachment]


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else "drafts.html"

    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    html, inline_attachments = prepare_inline_logo(html)

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

        params = {
            "from": sender,
            "to": to_recipients,
            "bcc": bcc_recipients,
            "subject": subject,
            "html": html
        }
        if inline_attachments:
            params["attachments"] = inline_attachments

        resend.Emails.send(params)

        print(f"Sent final article '{subject}'")
        print(f"To: {to_recipients}")
        print(f"BCC: {bcc_recipients}")

    elif approver:
        params = {
            "from": sender,
            "to": approver,
            "subject": subject,
            "html": html
        }
        if inline_attachments:
            params["attachments"] = inline_attachments

        resend.Emails.send(params)

        print(f"Sent draft approval '{subject}' to approver only")

    else:
        raise ValueError(
            "No recipients found. Set either DB_STRATEGY_WEEKLY_FINAL_EMAIL_TO or DB_STRATEGY_WEEKLY_APPROVER_EMAIL."
        )


if __name__ == "__main__":
    main()

    
