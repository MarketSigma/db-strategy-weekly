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
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=max_tokens,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}]
    )

    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text.strip()

    raise ValueError("Claude returned no text block")


def extract_json_object(text):
    """Return a JSON object even if the model wraps it in code fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "", 1).replace("```JSON", "", 1).replace("```", "").strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Claude did not return a JSON object: {text[:500]}")

    return json.loads(cleaned[start:end + 1])


def get_selected_topic(json_file, topic_id):
    with open(json_file, "r", encoding="utf-8") as f:
        topics = json.load(f)

    for t in topics:
        if str(t.get("topic_id")) == str(topic_id):
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


def load_impact_rules(path="impact_rules.json"):
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_get(row, *keys, default=""):
    for key in keys:
        value = row.get(key)
        if value is not None:
            return str(value)
    return default


def ai_write_article(topic, metrics, bank_name, impact_rules):
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
    {{"metric": "...", "current_value": "...", "business_impact": "..."}},
    {{"metric": "...", "current_value": "...", "business_impact": "..."}},
    {{"metric": "...", "current_value": "...", "business_impact": "..."}},
    {{"metric": "...", "current_value": "...", "business_impact": "..."}}
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

Source article rules:
- source_summary must be maximum 2 sentences.
- development must be maximum 100 words.
- wider_lens must be maximum 100 words.
- Do not retell the full source article.
- Assume the reader can open the source using the Read More link.

Doha Bank impact rules:
- doha_bank_impact must be the longest written section.
- At least 70% of the article should focus on Doha Bank implications, opportunities and risks.
- Use actual Doha Bank figures whenever available.
- Reference metrics by value where possible.
- If Doha Bank metrics are available, cite at least 3 metrics in the article.
- Quantify opportunities and risks whenever possible.
- Avoid generic wording such as "positive for lending", "supports growth", or "may benefit the bank".

Impact table rules:
- The impact_table must contain 4 to 6 rows.
- Use real Supabase metric values whenever available.
- Each row must include a metric, current value, and business impact.
- Prioritize profitability, liquidity, capital, asset quality and growth metrics.
- If the topic matches one of the impact rules, prioritize the mapped metrics.
- The business_impact must explain why the figure matters for management.

Strategic options rules:
- Strategic options must be actionable recommendations.
- Executive takeaway must be concise and suitable for CEO / senior management.

Selected topic:
{json.dumps(topic, ensure_ascii=False)}

Impact rules:
{json.dumps(impact_rules, ensure_ascii=False)}

Available Doha Bank metrics:
{json.dumps(metrics, ensure_ascii=False)}
"""

    text = ask_claude(prompt)
    return extract_json_object(text)


def build_final_email(topic, article):
    bullets = ""
    for b in article.get("impact_bullets", []):
        bullets += f"""
<tr>
<td style="padding:0 0 14px 0; font-size:18px; line-height:1.75; color:#2c3e54;">
<strong style="color:#002b5c;">&#9642;&nbsp;</strong>{html.escape(str(b))}
</td>
</tr>
"""

    rows = ""
    for r in article.get("impact_table", []):
        metric = safe_get(r, "metric", "line_item")
        current_value = safe_get(r, "current_value", "current")
        business_impact = safe_get(r, "business_impact", "implication")

        rows += f"""
<tr>
<td style="padding:12px 10px; font-size:16px; line-height:1.45; color:#2c3e54; border-bottom:1px solid #eef2f6; vertical-align:top;">
{html.escape(metric)}
</td>
<td style="padding:12px 10px; text-align:right; font-size:16px; line-height:1.45; color:#002b5c; font-weight:bold; border-bottom:1px solid #eef2f6; vertical-align:top; white-space:nowrap;">
{html.escape(current_value)}
</td>
<td style="padding:12px 10px; font-size:16px; line-height:1.45; color:#2c3e54; border-bottom:1px solid #eef2f6; vertical-align:top;">
{html.escape(business_impact)}
</td>
</tr>
"""

    options = ""
    for i, o in enumerate(article.get("strategic_options", []), 1):
        options += f"""
<tr>
<td style="padding:0 0 14px 0; font-size:18px; line-height:1.75; color:#2c3e54;">
<strong style="color:#002b5c;">{i}&nbsp;&nbsp;</strong>{html.escape(str(o))}
</td>
</tr>
"""

    source_title = topic.get("source_title", "Source article")
    source_name = topic.get("source_name", "News source")
    source_url = topic.get("source_url", "#")

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DB Strategy Weekly</title>
</head>
<body style="margin:0; padding:0; background-color:#eef2f6; font-family:Georgia,'Times New Roman',serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#eef2f6; padding:28px 12px;">
<tr><td align="center">

<table role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:680px; max-width:100%; background-color:#ffffff; border-radius:6px; overflow:hidden; box-shadow:0 1px 4px rgba(0,40,90,0.08);">

<tr><td style="height:4px; background:linear-gradient(90deg,#002b5c 0%, #0072ce 100%);">&nbsp;</td></tr>

<tr>
<td style="padding:30px 34px 18px 34px;">
<table width="100%">
<tr>
<td style="font-family:Arial,Helvetica,sans-serif; font-size:14px; letter-spacing:3px; color:#0072ce; font-weight:bold;">DOHA BANK</td>
<td align="right" style="font-family:Arial,Helvetica,sans-serif; font-size:13px; color:#7a8aa0;">{TODAY}</td>
</tr>
</table>

<h1 style="margin:14px 0 0 0; font-family:Georgia,serif; font-size:34px; line-height:1.2; font-weight:normal; color:#002b5c;">
Strategy Weekly
</h1>

<p style="margin:8px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:15px; line-height:1.5; color:#7a8aa0;">
A weekly read identifying key opportunities and risks for Doha Bank
</p>

<table cellpadding="0" cellspacing="0" style="margin:14px 0 0 0;">
<tr>
<td style="background-color:#eaf1fb; border:1px solid #cfe0f6; border-radius:11px; padding:5px 12px; font-family:Arial,Helvetica,sans-serif; font-size:12px; font-weight:bold; color:#0072ce;">
&#10022;&nbsp; Prepared by DB Strategy AI Analyst
</td>
</tr>
</table>
</td>
</tr>

<tr>
<td style="padding:8px 34px 10px 34px;">
<p style="margin:0 0 6px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">This week's source</p>

<table width="100%" cellpadding="0" cellspacing="0" style="border-left:3px solid #0072ce;">
<tr>
<td style="padding:7px 0 7px 14px;">
<p style="margin:0 0 5px 0; font-size:17px; line-height:1.45; color:#002b5c; font-weight:bold;">
{html.escape(source_title)}
</p>
<p style="margin:0 0 6px 0; font-size:16px; line-height:1.55; color:#2c3e54; font-style:italic;">
{html.escape(article.get("source_summary", ""))}
</p>
<p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:13px; color:#9aa8bd;">
{html.escape(source_name)} &middot;
<a href="{html.escape(source_url)}" style="color:#0072ce; font-weight:bold; text-decoration:none;">Read more &rarr;</a>
</p>
</td>
</tr>
</table>
</td>
</tr>

<tr><td style="padding:24px 34px 0 34px;"><div style="height:1px; background-color:#e2e8f0;">&nbsp;</div></td></tr>

<tr>
<td style="padding:24px 34px 0 34px;">

<p style="margin:0 0 16px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:#0072ce;">DB Strategy</p>

<h2 style="margin:0 0 18px 0; font-family:Georgia,serif; font-size:26px; line-height:1.3; font-weight:normal; color:#002b5c;">
{html.escape(article.get("article_title", ""))}
</h2>

<p style="margin:0 0 7px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">The development</p>
<p style="margin:0 0 18px 0; font-size:18px; line-height:1.8; color:#2c3e54;">{html.escape(article.get("development", ""))}</p>

<p style="margin:0 0 7px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">The wider lens</p>
<p style="margin:0 0 18px 0; font-size:18px; line-height:1.8; color:#2c3e54;">{html.escape(article.get("wider_lens", ""))}</p>

<p style="margin:0 0 7px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">What it means for Doha Bank</p>
<p style="margin:0 0 12px 0; font-size:18px; line-height:1.8; color:#2c3e54;">{html.escape(article.get("doha_bank_impact", ""))}</p>

<table width="100%" cellpadding="0" cellspacing="0">
{bullets}
</table>

<p style="margin:10px 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">
Quantified impact on the books
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 26px 0; background-color:#f7f9fc; border:1px solid #e2e8f0; border-left:3px solid #0072ce; border-radius:6px;">
<tr>
<td style="padding:8px 10px; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1px; text-transform:uppercase; color:#8a99ad; border-bottom:1px solid #e2e8f0;">Metric</td>
<td style="padding:8px 10px; text-align:right; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1px; text-transform:uppercase; color:#8a99ad; border-bottom:1px solid #e2e8f0;">Current value</td>
<td style="padding:8px 10px; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1px; text-transform:uppercase; color:#8a99ad; border-bottom:1px solid #e2e8f0;">Business impact</td>
</tr>
{rows}
</table>

<p style="margin:0 0 7px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; color:#9aa8bd;">Strategic options</p>

<table width="100%" cellpadding="0" cellspacing="0">
{options}
</table>

</td>
</tr>

<tr>
<td style="padding:28px 34px 32px 34px;">
<div style="background-color:#f4f8fc; border-radius:6px; padding:20px 22px; border-left:3px solid #0072ce;">
<p style="margin:0; font-size:18px; line-height:1.8; font-weight:500; color:#002b5c; font-style:italic;">
{html.escape(article.get("executive_takeaway", ""))}
</p>
</div>
</td>
</tr>

<tr>
<td style="padding:18px 34px 26px 34px; border-top:1px solid #e2e8f0;">
<p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:12px; line-height:1.6; color:#8a99ad;">
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
    parser.add_argument("--impact-rules", default="impact_rules.json")
    args = parser.parse_args()

    topic = get_selected_topic(args.drafts_json, args.topic_id)
    metrics = get_doha_bank_metrics(args.bank)
    impact_rules = load_impact_rules(args.impact_rules)
    article = ai_write_article(topic, metrics, args.bank, impact_rules)

    html_body = build_final_email(topic, article)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_body)

    print(f"Generated {args.out}")


if __name__ == "__main__":
    main()

    
