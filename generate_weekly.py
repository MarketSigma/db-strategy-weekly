import os
import json
import html
import argparse
import datetime
import anthropic
from supabase import create_client

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)

TODAY = datetime.date.today().strftime("%d %B %Y")

def ask_claude(prompt, max_tokens=5000):
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "sonnet-4-6"),
        max_tokens=max_tokens,
        temperature=0.35,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()

def get_selected_topic(json_file, topic_id):
    with open(json_file, "r", encoding="utf-8") as f:
        topics = json.load(f)

    for t in topics:
        if str(t["topic_id"]) == str(topic_id):
            return t

    return topics[0]

def get_doha_bank_metrics(bank_name):
    result = (
        supabase.table("bank_metric_values")
        .select("*")
        .eq("bank_name", bank_name)
        .order("period_end", desc=True)
        .limit(30)
        .execute()
    )

    return result.data or []

def ai_write_article(topic, metrics, bank_name):
    prompt = f"""
You are DB Strategy AI Analyst.

Write a polished weekly executive strategy article for {bank_name}.

Use this exact JSON structure:
{{
  "article_title": "...",
  "source_summary": "...",
  "development": "...",
  "wider_lens": "...",
  "doha_bank_impact": "...",
  "impact_bullets": ["...", "...", "..."],
  "impact_table": [
    {{"line_item": "...", "current": "...", "implication": "..."}},
    {{"line_item": "...", "current": "...", "implication": "..."}},
    {{"line_item": "...", "current": "...", "implication": "..."}}
  ],
  "strategic_options": ["...", "...", "..."],
  "executive_takeaway": "..."
}}

Rules:
- Output JSON only.
- No markdown.
- Do not say "not available".
- Do not invent financial numbers.
- Only use Doha Bank metrics if they exist in the Supabase data below.
- If a metric is missing, avoid mentioning it.
- Make the article strategic, executive, and practical.
- Focus on opportunity or risk for Doha Bank.
- Keep each section concise.

Selected topic:
{json.dumps(topic, ensure_ascii=False)}

Available Doha Bank metrics:
{json.dumps(metrics, ensure_ascii=False)}
"""

    text = ask_claude(prompt)
    return json.loads(text)

def build_final_email(topic, article):
    bullets = ""
    for b in article["impact_bullets"]:
        bullets += f"""
<tr>
<td style="padding:0 0 10px 0; font-size:16px; line-height:1.6; color:#2c3e54;">
<strong style="color:#002b5c;">&#9642;&nbsp;</strong>{html.escape(b)}
</td>
</tr>
"""

    rows = ""
    for r in article["impact_table"]:
        rows += f"""
<tr>
<td style="padding:9px 8px; font-size:14px; color:#2c3e54; border-bottom:1px solid #eef2f6;">{html.escape(r["line_item"])}</td>
<td style="padding:9px 8px; text-align:right; font-family:Georgia,serif; font-size:15px; color:#7a8aa0; border-bottom:1px solid #eef2f6;">{html.escape(r["current"])}</td>
<td style="padding:9px 8px; text-align:right; font-family:Georgia,serif; font-size:15px; font-weight:bold; color:#002b5c; border-bottom:1px solid #eef2f6;">{html.escape(r["implication"])}</td>
</tr>
"""

    options = ""
    for i, o in enumerate(article["strategic_options"], 1):
        options += f"""
<tr>
<td style="padding:0 0 10px 0; font-size:16px; line-height:1.6; color:#2c3e54;">
<strong style="color:#002b5c;">{i}&nbsp;&nbsp;</strong>{html.escape(o)}
</td>
</tr>
"""

    return f"""
<!DOCTYPE html>
<html lang="en">
<body style="margin:0; padding:0; background-color:#eef2f6; font-family:Georgia,'Times New Roman',serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#eef2f6; padding:28px 12px;">
<tr><td align="center">

<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:600px; max-width:600px; background-color:#ffffff; border-radius:6px; overflow:hidden; box-shadow:0 1px 4px rgba(0,40,90,0.08);">

<tr><td style="height:4px; background:linear-gradient(90deg,#002b5c 0%, #0072ce 100%);">&nbsp;</td></tr>

<tr>
<td style="padding:30px 40px 18px 40px;">
<table width="100%">
<tr>
<td style="font-family:Arial,Helvetica,sans-serif; font-size:13px; letter-spacing:3px; color:#0072ce; font-weight:bold;">DOHA BANK</td>
<td align="right" style="font-family:Arial,Helvetica,sans-serif; font-size:12px; color:#7a8aa0;">{TODAY}</td>
</tr>
</table>

<h1 style="margin:14px 0 0 0; font-family:Georgia,serif; font-size:30px; line-height:1.2; font-weight:normal; color:#002b5c;">
Strategy Weekly
</h1>

<p style="margin:6px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; color:#7a8aa0;">
A weekly read identifying key opportunities and risks for Doha Bank
</p>

<table cellpadding="0" cellspacing="0" style="margin:12px 0 0 0;">
<tr>
<td style="background-color:#eaf1fb; border:1px solid #cfe0f6; border-radius:11px; padding:3px 12px; font-family:Arial,Helvetica,sans-serif; font-size:11px; font-weight:bold; color:#0072ce;">
&#10022;&nbsp; Prepared by DB Strategy AI Analyst
</td>
</tr>
</table>
</td>
</tr>

<tr>
<td style="padding:6px 40px 8px 40px;">
<p style="margin:0 0 6px 0; font-family:Arial,Helvetica,sans-serif; font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">This week's source</p>

<table width="100%" cellpadding="0" cellspacing="0" style="border-left:3px solid #0072ce;">
<tr>
<td style="padding:6px 0 6px 14px;">
<p style="margin:0 0 3px 0; font-size:15px; line-height:1.45; color:#002b5c; font-weight:bold;">
{html.escape(topic["source_title"])}
</p>
<p style="margin:0 0 3px 0; font-size:14px; line-height:1.5; color:#2c3e54; font-style:italic;">
{html.escape(article["source_summary"])}
</p>
<p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:11px; color:#9aa8bd;">
{html.escape(topic.get("source_name", "News source"))} &middot;
<a href="{html.escape(topic["source_url"])}" style="color:#0072ce; font-weight:bold; text-decoration:none;">Read more &rarr;</a>
</p>
</td>
</tr>
</table>
</td>
</tr>

<tr><td style="padding:22px 40px 0 40px;"><div style="height:1px; background-color:#e2e8f0;">&nbsp;</div></td></tr>

<tr>
<td style="padding:22px 40px 0 40px;">

<p style="margin:0 0 16px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:#0072ce;">DB Strategy</p>

<h2 style="margin:0 0 16px 0; font-family:Georgia,serif; font-size:22px; line-height:1.3; font-weight:normal; color:#002b5c;">
{html.escape(article["article_title"])}
</h2>

<p style="margin:0 0 6px 0; font-family:Arial,Helvetica,sans-serif; font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">The development</p>
<p style="margin:0 0 16px 0; font-size:16px; line-height:1.65; color:#2c3e54;">{html.escape(article["development"])}</p>

<p style="margin:0 0 6px 0; font-family:Arial,Helvetica,sans-serif; font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">The wider lens</p>
<p style="margin:0 0 16px 0; font-size:16px; line-height:1.65; color:#2c3e54;">{html.escape(article["wider_lens"])}</p>

<p style="margin:0 0 6px 0; font-family:Arial,Helvetica,sans-serif; font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">What it means for Doha Bank</p>
<p style="margin:0 0 10px 0; font-size:16px; line-height:1.65; color:#2c3e54;">{html.escape(article["doha_bank_impact"])}</p>

<table width="100%" cellpadding="0" cellspacing="0">
{bullets}
</table>

<p style="margin:8px 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">
Impact on the books
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px 0; background-color:#f7f9fc; border:1px solid #e2e8f0; border-left:3px solid #0072ce; border-radius:6px;">
<tr>
<td style="padding:6px 8px; font-family:Arial,sans-serif; font-size:10px; letter-spacing:1px; text-transform:uppercase; color:#8a99ad; border-bottom:1px solid #e2e8f0;">Line item</td>
<td style="padding:6px 8px; text-align:right; font-family:Arial,sans-serif; font-size:10px; letter-spacing:1px; text-transform:uppercase; color:#8a99ad; border-bottom:1px solid #e2e8f0;">Current</td>
<td style="padding:6px 8px; text-align:right; font-family:Arial,sans-serif; font-size:10px; letter-spacing:1px; text-transform:uppercase; color:#8a99ad; border-bottom:1px solid #e2e8f0;">Implication</td>
</tr>
{rows}
</table>

<p style="margin:0 0 6px 0; font-family:Arial,Helvetica,sans-serif; font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">Strategic options</p>

<table width="100%" cellpadding="0" cellspacing="0">
{options}
</table>

</td>
</tr>

<tr>
<td style="padding:26px 40px 30px 40px;">
<div style="background-color:#f4f8fc; border-radius:6px; padding:18px 22px; border-left:3px solid #0072ce;">
<p style="margin:0; font-size:15px; line-height:1.6; color:#002b5c; font-style:italic;">
{html.escape(article["executive_takeaway"])}
</p>
</div>
</td>
</tr>

<tr>
<td style="padding:18px 40px 26px 40px; border-top:1px solid #e2e8f0;">
<p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:11px; line-height:1.6; color:#8a99ad;">
The analysis expressed is that of the DB Strategy AI Analyst. Review before external distribution.
</p>
</td>
</tr>

</table>
</td></tr>
</table>
</body>
</html>
"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic-id", default=os.getenv("TOPIC_ID", "1"))
    parser.add_argument("--drafts-json", default="drafts.json")
    parser.add_argument("--out", default="strategy_weekly_final.html")
    parser.add_argument("--bank", default="Doha Bank")
    args = parser.parse_args()

    topic = get_selected_topic(args.drafts_json, args.topic_id)
    metrics = get_doha_bank_metrics(args.bank)
    article = ai_write_article(topic, metrics, args.bank)

    html_body = build_final_email(topic, article)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_body)

if __name__ == "__main__":
    main()
