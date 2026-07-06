#!/usr/bin/env python3

import os
import json
import html
import argparse
import datetime
import re
import anthropic
from supabase import create_client

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)

TODAY = datetime.date.today().strftime("%d %B %Y")

NAVY = "#002b5c"
BLUE = "#0072ce"
SLATE = "#2c3e54"
MUTED = "#8a99ad"
GREY = "#7a8aa0"


def ask_claude(prompt, max_tokens=5000):
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )

    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text.strip()

    raise ValueError("Claude returned no text block")


def extract_json_object(text):
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = (
            cleaned.replace("```json", "", 1)
            .replace("```JSON", "", 1)
            .replace("```", "")
            .strip()
        )

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Claude did not return a JSON object: {text[:500]}")

    json_text = cleaned[start:end + 1]

    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        # Remove illegal raw control characters that sometimes appear inside AI JSON strings.
        json_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", json_text)

        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            print("Failed to parse Claude JSON.")
            print("Claude output preview:")
            print(json_text[:2000])
            raise e


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
        .eq("bank_ticker", "DHBK")
        .order("period_end", desc=True)
        .limit(80)
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


def split_lead(text):
    s = str(text).strip()

    if ". " in s:
        lead, rest = s.split(". ", 1)
        return html.escape(lead), html.escape(rest)

    return html.escape(s.rstrip(".")), ""


def format_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)

    if abs(value - round(value)) < 0.05:
        return f"{value:,.0f}"

    return f"{value:,.1f}"


def format_metric_value(row):
    raw_value = row.get("value")

    if raw_value is None:
        return ""

    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return str(raw_value)

    unit = str(row.get("unit") or "").strip().lower()
    currency = row.get("currency") or ""

    if unit in ("thousand", "thousands", "qar thousands", "qar '000", "qar000"):
        actual_value = value * 1_000
    elif unit in ("million", "millions"):
        actual_value = value * 1_000_000
    elif unit in ("billion", "billions"):
        actual_value = value * 1_000_000_000
    elif unit in ("percent", "percentage", "%"):
        return f"{format_number(value)}%"
    elif unit in ("ratio", "x"):
        return f"{format_number(value)}x"
    else:
        actual_value = value

    prefix = f"{currency} " if currency else ""

    if abs(actual_value) >= 1_000_000_000:
        return f"{prefix}{actual_value / 1_000_000_000:,.1f}bn"
    if abs(actual_value) >= 1_000_000:
        return f"{prefix}{actual_value / 1_000_000:,.1f}m"
    if abs(actual_value) >= 1_000:
        return f"{prefix}{actual_value / 1_000:,.1f}k"

    return f"{prefix}{actual_value:,.0f}"


def format_metrics_for_ai(metrics):
    if not metrics:
        return "No Doha Bank metrics were returned from Supabase."

    lines = [
        "IMPORTANT UNIT RULES:",
        "- Values below are already converted into final display units.",
        "- Do not convert them again.",
        "- Do not use k or raw thousand values.",
        "- Use only these displayed values for Doha Bank reported financials.",
        ""
    ]

    latest_period = metrics[0].get("period_end", "")
    if latest_period:
        lines.append(f"Latest available reporting period: {latest_period}")
        lines.append("")

    for m in metrics:
        metric_name = m.get("metric_name") or m.get("metric_code") or "Metric"
        metric_code = m.get("metric_code") or ""
        period_end = m.get("period_end") or ""
        category = m.get("metric_category") or ""
        value = format_metric_value(m)

        line = f"- {metric_name}"
        if metric_code:
            line += f" ({metric_code})"
        line += f": {value}"

        if period_end:
            line += f" | period: {period_end}"
        if category:
            line += f" | category: {category}"

        lines.append(line)

    return "\n".join(lines)


def ai_write_article(topic, metrics, bank_name, impact_rules):
    formatted_metrics = format_metrics_for_ai(metrics)

    prompt = f"""
You are DB Strategy AI Analyst.

Write a polished weekly executive strategy briefing for {bank_name}.

Return ONLY valid RFC8259 JSON.
Do not include markdown.
Do not include code fences.
Do not include commentary outside JSON.
All newline characters inside string values must be escaped as \\n.
Do not include raw line breaks inside JSON strings.
Do not include tabs inside JSON strings.
Escape double quotes inside text values.

Use this EXACT JSON structure:
{{
  "article_title": "...",
  "source_summary": "...",
  "doha_bank_impact": "...",
  "impact_table": [
    {{"metric": "...", "current_value": "...", "projected_value": "...", "change": "..."}},
    {{"metric": "...", "current_value": "...", "projected_value": "...", "change": "..."}},
    {{"metric": "...", "current_value": "...", "projected_value": "...", "change": "..."}}
  ],
  "opportunity": ["Short lead. One-sentence explanation.", "Short lead. One-sentence explanation."],
  "risk": ["Short lead. One-sentence explanation.", "Short lead. One-sentence explanation."],
  "strategic_options": [
    {"recommendation": "...", "business_owner": "..."},
    {"recommendation": "...", "business_owner": "..."},
    {"recommendation": "...", "business_owner": "..."}
  ]
}}

Global rules:
- Do NOT invent financial numbers.
- Only use Doha Bank figures present in the Supabase data below.
- If a metric or prior period is missing, use an empty string "" for that value.
- Never write "not available".

source_summary:
- Maximum 2 sentences.
- Do not retell the article; assume the reader can open the Read more link.

doha_bank_impact:
- One to two short paragraphs.
- Use \\n between paragraphs, not raw line breaks.
- The most substantive section.
- Quantify the impact using real Doha Bank figures where available.
- Cite at least 3 metrics by value if the data allows.
- Avoid generic phrasing like "supports growth".

impact_table:
- 2 to 5 rows.
- Only include metrics this development materially moves.
- current_value = latest reported value from Supabase only.
- projected_value = reasoned estimate after the development plays out.
- change = movement from current to projected.
- Do not include a metric if the development does not plausibly move it.

opportunity / risk:
- 1 to 2 items each.
- Each item must be one string only.
- Each item format: "Short lead. One-sentence explanation."
- Quantify with real figures where possible.

strategic_options:
- 3 concrete, actionable recommendations for senior management.
- Each item must include:
  - recommendation: the action to take.
  - business_owner: the suggested accountable department or executive owner.
- Business owner must be a practical Doha Bank owner such as Treasury, Wholesale Banking, Retail Banking, Risk Management, Finance, Strategy, Operations, Digital Banking, Compliance, or ALM Committee.
- Do not assign personal names.

Selected topic:
{json.dumps(topic, ensure_ascii=False)}

Impact rules:
{json.dumps(impact_rules, ensure_ascii=False)}

Available Doha Bank metrics from Supabase:
{formatted_metrics}

Strict financial data rules:
- Treat the formatted Supabase metrics above as the only source of Doha Bank reported figures.
- Values are already converted into QAR bn, QAR m, %, or ratio format.
- Do not write values in "k" or "thousands".
- Do not re-scale or re-convert the figures.
- If you calculate projected_value, base it only on the displayed current value and make the assumption explicit.
- If the required current metric is not listed above, leave current_value as "".
"""

    text = ask_claude(prompt)
    return extract_json_object(text)


def build_final_email(topic, article):
    rows = ""

    for idx, r in enumerate(article.get("impact_table", [])):
        metric = html.escape(safe_get(r, "metric", "line_item"))
        curr = html.escape(safe_get(r, "current_value", "current", default="—") or "—")
        proj = html.escape(safe_get(r, "projected_value", "projected", default="—") or "—")
        change = safe_get(r, "change", default="")
        change_cell = html.escape(change) if change else "—"
        row_bg = "#ffffff" if idx % 2 == 0 else "#f7fafd"

        rows += f"""
<tr style="background-color:{row_bg};">
<td class="cell" style="padding:11px 14px; font-family:Arial,sans-serif; font-size:15px; color:{SLATE}; border-bottom:1px solid #eef2f6;">{metric}</td>
<td class="cell" style="padding:11px 14px; text-align:right; font-family:Georgia,serif; font-size:15px; color:{MUTED}; border-bottom:1px solid #eef2f6;">{curr}</td>
<td class="cell" style="padding:11px 14px; text-align:right; font-family:Georgia,serif; font-size:15px; font-weight:bold; color:{NAVY}; border-bottom:1px solid #eef2f6;">{proj}</td>
<td class="cell" style="padding:11px 14px; text-align:right; font-family:Arial,sans-serif; font-size:14px; font-weight:bold; color:{BLUE}; border-bottom:1px solid #eef2f6;">{change_cell}</td>
</tr>"""

    def points_html(items):
        out = ""

        for i, it in enumerate(items):
            lead, rest = split_lead(it)
            mb = "0" if i == len(items) - 1 else "0 0 10px 0"

            if rest:
                body = f'<strong style="color:{NAVY};">{lead}.</strong> {rest}'
            else:
                body = f'<strong style="color:{NAVY};">{lead}</strong>'

            out += f'<p style="margin:{mb}; font-family:Arial,sans-serif; font-size:15px; line-height:1.6; color:{SLATE};">{body}</p>'

        return out

    opp_html = points_html(article.get("opportunity", []))
    risk_html = points_html(article.get("risk", []))

    options = ""
    strategic_options = article.get("strategic_options", [])

    for i, o in enumerate(strategic_options, 1):
        badge_pad = "" if i == len(strategic_options) else "padding-bottom:16px;"

        if isinstance(o, dict):
            recommendation = html.escape(str(o.get("recommendation", "")).strip())
            owner = html.escape(str(o.get("business_owner", "")).strip())
        else:
            # Backward compatibility in case the AI returns the old string format.
            recommendation = html.escape(str(o).strip())
            owner = ""

        owner_html = ""
        if owner:
            owner_html = f"""
            <div style="margin-top:7px; font-family:Arial,sans-serif; font-size:12px; line-height:1.4; color:{GREY};">
              <strong style="color:{NAVY};">Suggested business owner:</strong> {owner}
            </div>"""

        options += f"""
<tr>
<td width="34" valign="top" style="{badge_pad} padding-top:1px;"><div style="width:23px; height:23px; background-color:{BLUE}; border-radius:12px; color:#ffffff; font-family:Arial,sans-serif; font-size:12px; font-weight:bold; text-align:center; line-height:23px;">{i}</div></td>
<td valign="top" style="font-family:Arial,sans-serif; font-size:15px; line-height:1.5; color:{SLATE}; padding:0 0 16px 6px;">
  <div>{recommendation}</div>
  {owner_html}
</td>
</tr>"""

    impact_paras = ""
    impact_text = str(article.get("doha_bank_impact", "")).replace("\\n", "\n")

    for para in [p for p in impact_text.split("\n") if p.strip()]:
        impact_paras += f'<p class="body-text" style="margin:0 0 14px 0; font-family:Georgia,serif; font-size:15px; line-height:1.65; color:{SLATE};">{html.escape(para.strip())}</p>'

    source_title = html.escape(topic.get("source_title", "Source article"))
    source_name = html.escape(topic.get("source_name", "News source"))
    source_url = html.escape(topic.get("source_url", "#"))
    source_date = html.escape(str(topic.get("source_date", "")).strip())

    source_meta = source_name
    if source_date:
        source_meta += f' &nbsp;&middot;&nbsp; {source_date}'

    source_summary_raw = str(article.get("source_summary", "")).strip()
    if source_summary_raw and not source_summary_raw.endswith(("…", "...")):
        source_summary_raw = source_summary_raw.rstrip(".") + "…"

    source_summary = html.escape(source_summary_raw)

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DB Strategy Weekly</title>
<style>
  @media only screen and (max-width:620px) {{
    .container {{ width:100% !important; border-radius:0 !important; }}
    .pad {{ padding-left:24px !important; padding-right:24px !important; }}
    .h1 {{ font-size:26px !important; line-height:1.2 !important; }}
    .body-text {{ font-size:17px !important; line-height:1.75 !important; }}
    .source-title {{ font-size:19px !important; }}
    .source-summary {{ font-size:16px !important; }}
    .cell {{ font-size:15px !important; padding:13px 14px !important; }}
    .stack {{ display:block !important; width:100% !important; max-width:100% !important; box-sizing:border-box !important; margin:0 0 14px 0 !important; }}
    .stack-last {{ margin:0 !important; }}
    .spacer {{ display:none !important; }}
  }}
</style>
</head>
<body style="margin:0; padding:0; background-color:#e7ecf3; font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#e7ecf3; padding:32px 24px;"><tr><td align="center">
<table role="presentation" width="760" cellpadding="0" cellspacing="0" class="container" style="width:760px; max-width:100%; background-color:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 6px 26px rgba(0,25,70,0.12);">

  <tr><td style="height:3px; background-color:{BLUE}; font-size:0; line-height:0;">&nbsp;</td></tr>

  <tr><td class="pad" style="padding:26px 40px 24px 40px; border-bottom:1px solid #eaeef4;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:Arial,Helvetica,sans-serif; font-size:12px; font-weight:bold; letter-spacing:2px; text-transform:uppercase; color:{BLUE};">DB Weekly &nbsp;&middot;&nbsp; Opportunities &amp; Risks</td>
      <td align="right" style="font-family:Arial,sans-serif; font-size:12px; letter-spacing:1px; color:{MUTED};">{TODAY}</td>
    </tr></table>
    <h1 class="h1" style="margin:20px 0 0 0; font-family:Georgia,'Times New Roman',serif; font-size:30px; line-height:1.12; font-weight:bold; color:{NAVY};">Weekly Strategic Outlook</h1>
    <p style="margin:10px 0 0 0; font-family:Arial,sans-serif; font-size:15px; line-height:1.5; color:{GREY};">Key opportunities, risks and strategic implications for Doha Bank</p>
  </td></tr>

  <tr><td class="pad" style="padding:28px 40px 0 40px;">
    <p style="margin:0 0 14px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">This week&rsquo;s source</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f6f9fc; border:1px solid #e8eef5; border-radius:8px;"><tr>
      <td style="padding:22px 24px;">
        <p class="source-title" style="margin:0 0 9px 0; font-family:Georgia,serif; font-size:18px; font-weight:bold; line-height:1.35; color:{NAVY};">{source_title}</p>
        <p style="margin:0 0 13px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:0.3px; color:{MUTED};">{source_meta}</p>
        <p class="source-summary" style="margin:0 0 16px 0; font-family:Arial,sans-serif; font-size:15px; line-height:1.65; color:#5a6b7d;">{source_summary}</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e8eef5;"><tr>
          <td align="right" style="padding-top:12px;"><a href="{source_url}" style="font-family:Arial,sans-serif; font-size:13px; font-weight:bold; letter-spacing:0.3px; color:{BLUE}; text-decoration:none;">Read more &rarr;</a></td>
        </tr></table>
      </td>
    </tr></table>
  </td></tr>

  <tr><td class="pad" style="padding:24px 40px 0 40px;">
    <p style="margin:0 0 14px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">What it means for Doha Bank</p>
    {impact_paras}
  </td></tr>

  <tr><td class="pad" style="padding:24px 40px 0 40px;">
    <p style="margin:0 0 14px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">Projected impact on key metrics</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e6ebf2; border-radius:8px; border-collapse:separate; overflow:hidden;">
      <tr style="background-color:#ffffff;">
        <td style="padding:13px 18px; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1px; text-transform:uppercase; font-weight:bold; color:{GREY}; border-bottom:2px solid {BLUE};">Metric</td>
        <td style="padding:13px 18px; text-align:right; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1px; text-transform:uppercase; font-weight:bold; color:{GREY}; border-bottom:2px solid {BLUE};">Current</td>
        <td style="padding:13px 18px; text-align:right; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1px; text-transform:uppercase; font-weight:bold; color:{GREY}; border-bottom:2px solid {BLUE};">Projected</td>
        <td style="padding:13px 18px; text-align:right; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1px; text-transform:uppercase; font-weight:bold; color:{GREY}; border-bottom:2px solid {BLUE};">Change</td>
      </tr>
      {rows}
    </table>
    <p style="margin:10px 2px 0 2px; font-family:Arial,sans-serif; font-size:11px; line-height:1.5; color:{MUTED};">Doha Bank figures are based on reported results. Projected figures represent model-based estimates intended to illustrate potential impacts.</p>
  </td></tr>

  <tr><td class="pad" style="padding:24px 40px 0 40px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="49%" valign="top" class="stack" style="background-color:#f2f7fd; border-radius:8px; border-top:3px solid {BLUE}; padding:14px 16px; box-sizing:border-box;">
        <p style="margin:0 0 10px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; font-weight:bold; color:{BLUE};">Opportunity</p>
        {opp_html}
      </td>
      <td width="2%" class="spacer" style="font-size:0; line-height:0;">&nbsp;</td>
      <td width="49%" valign="top" class="stack stack-last" style="background-color:#f4f6f9; border-radius:8px; border-top:3px solid {NAVY}; padding:14px 16px; box-sizing:border-box;">
        <p style="margin:0 0 10px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; font-weight:bold; color:{NAVY};">Risk</p>
        {risk_html}
      </td>
    </tr></table>
  </td></tr>

  <tr><td class="pad" style="padding:24px 40px 0 40px;">
    <p style="margin:0 0 16px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">Strategic recommendations</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{options}</table>
  </td></tr>

  <tr><td class="pad" style="padding:30px 40px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e4e9f0;"><tr>
      <td style="padding-top:16px; font-family:Arial,sans-serif; font-size:11px; color:{MUTED}; line-height:1.5;">The analysis expressed is that of the DB Strategy AI Analyst.</td>
      <td align="right" style="padding-top:16px; font-family:Arial,sans-serif; font-size:11px; color:#a7b2c2;">&copy; 2026 Doha Bank</td>
    </tr></table>
  </td></tr>

</table>
</td></tr></table>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic-id", default=os.getenv("TOPIC_ID", "1"))
    parser.add_argument("--drafts-json", default="drafts.json")
    parser.add_argument("--out", default="strategy_weekly_final.html")
    parser.add_argument("--bank", default="Doha Bank Q.P.S.C.")
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
    

    
