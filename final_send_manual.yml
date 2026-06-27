name: DB Strategy Weekly - Final Send Manual

on:
  workflow_dispatch:
    inputs:
      html_file:
        description: 'HTML file to send. Default out.html. Upload/commit approved final HTML first.'
        required: true
        default: 'out.html'

jobs:
  final-send:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Send approved final article
        env:
          SMTP_HOST: ${{ secrets.DB_STRATEGY_WEEKLY_SMTP_HOST }}
          SMTP_PORT: ${{ secrets.DB_STRATEGY_WEEKLY_SMTP_PORT }}
          SMTP_USER: ${{ secrets.DB_STRATEGY_WEEKLY_SMTP_USER }}
          SMTP_PASS: ${{ secrets.DB_STRATEGY_WEEKLY_SMTP_PASS }}
          EMAIL_FROM: ${{ secrets.DB_STRATEGY_WEEKLY_EMAIL_FROM }}
          EMAIL_TO: ${{ secrets.DB_STRATEGY_WEEKLY_FINAL_EMAIL_TO }}
          EMAIL_SUBJECT: DB Strategy Weekly
        run: python send_email.py "${{ github.event.inputs.html_file }}"
