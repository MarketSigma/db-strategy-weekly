name: DB Strategy Weekly - Draft Only

on:
  schedule:
    # Sunday 06:00 Qatar = 03:00 UTC
    - cron: '0 3 * * 0'
  workflow_dispatch:

jobs:
  draft:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Generate 3 draft topics from news + Supabase metrics
        env:
          SUPABASE_URL: ${{ secrets.DB_STRATEGY_WEEKLY_SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.DB_STRATEGY_WEEKLY_SUPABASE_KEY }}
          OPENAI_API_KEY: ${{ secrets.DB_STRATEGY_WEEKLY_OPENAI_API_KEY }}
          OPENAI_MODEL: ${{ vars.DB_STRATEGY_WEEKLY_OPENAI_MODEL || 'gpt-4.1-mini' }}
        run: python generate_news_drafts.py --out drafts.html --json-out drafts.json --bank "Doha Bank"
      - name: Email draft to approver only
        env:
          SMTP_HOST: ${{ secrets.DB_STRATEGY_WEEKLY_SMTP_HOST }}
          SMTP_PORT: ${{ secrets.DB_STRATEGY_WEEKLY_SMTP_PORT }}
          SMTP_USER: ${{ secrets.DB_STRATEGY_WEEKLY_SMTP_USER }}
          SMTP_PASS: ${{ secrets.DB_STRATEGY_WEEKLY_SMTP_PASS }}
          EMAIL_FROM: ${{ secrets.DB_STRATEGY_WEEKLY_EMAIL_FROM }}
          EMAIL_TO: ${{ secrets.DB_STRATEGY_WEEKLY_APPROVER_EMAIL }}
          EMAIL_SUBJECT: Approval Required — DB Strategy Weekly
        run: python send_email.py drafts.html
      - name: Upload draft artifacts
        uses: actions/upload-artifact@v4
        with:
          name: db-strategy-weekly-drafts
          path: |
            drafts.html
            drafts.json
