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

# ---- Doha Bank brand tokens ----
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


def split_lead(text):
    """Split 'Lead phrase. Detail sentence.' into a bold lead + rest."""
    s = str(text).strip()
    if ". " in s:
        lead, rest = s.split(". ", 1)
        return html.escape(lead), html.escape(rest)
    return html.escape(s.rstrip(".")), ""


def ai_write_article(topic, metrics, bank_name, impact_rules):
    prompt = f"""
You are DB Strategy AI Analyst.

Write a polished weekly executive strategy briefing for {bank_name}.

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
  "strategic_options": ["...", "...", "..."]
}}

Global rules:
- Output JSON only. No markdown, no code fences, no commentary.
- Do NOT invent financial numbers. Only use Doha Bank figures present in the Supabase data below.
- If a metric or a prior period is missing, use an empty string "" for that value; never guess.
- Never write "not available".

source_summary:
- Maximum 2 sentences. Do not retell the article; assume the reader can open the Read more link.

doha_bank_impact  (this is the "What it means for Doha Bank" section):
- One to two short paragraphs, plain executive English.
- The most substantive section: quantify the impact using real Doha Bank figures where available.
- Cite at least 3 metrics by value if the data allows. Avoid generic phrasing like "supports growth".

impact_table  ("Key metrics - current vs projected"):
- 2 to 5 rows. Only include metrics that THIS development materially moves.
- current_value = the latest reported value, taken ONLY from the Supabase data (never invented).
- projected_value = a reasoned estimate of that metric AFTER the development plays out, grounded in the current value and a stated assumption (e.g. a 50bp cut). Keep it directional and conservative.
- change = the movement from current to projected (e.g. "-4 bps", "-QAR 42m").
- Do NOT include a metric if the development does not plausibly move it.

opportunity / risk:
- 1 to 2 items each. Each item: a short bold lead, a full stop, then one sentence.
- Quantify with real figures where possible. Opportunities and risks must both relate to THIS week's development.

strategic_options:
- 3 concrete, actionable recommendations for senior management.

Selected topic:
{json.dumps(topic, ensure_ascii=False)}

Impact rules:
{json.dumps(impact_rules, ensure_ascii=False)}

Available Doha Bank metrics (Supabase):
{json.dumps(metrics, ensure_ascii=False)}
"""
    text = ask_claude(prompt)
    return extract_json_object(text)


def build_final_email(topic, article):
    # ----- Key metrics table (old -> new) -----
    rows = ""
    for r in article.get("impact_table", []):
        metric = html.escape(safe_get(r, "metric", "line_item"))
        curr = html.escape(safe_get(r, "current_value", "current", default="\u2014") or "\u2014")
        proj = html.escape(safe_get(r, "projected_value", "projected", default="\u2014") or "\u2014")
        change = safe_get(r, "change", default="")
        change_cell = html.escape(change) if change else "\u2014"
        rows += f"""
<tr>
<td style="padding:12px 16px; font-family:Arial,sans-serif; font-size:13px; color:{SLATE}; border-bottom:1px solid #eef2f6;">{metric}</td>
<td style="padding:12px 16px; text-align:right; font-family:Georgia,serif; font-size:15px; color:{MUTED}; border-bottom:1px solid #eef2f6;">{curr}</td>
<td style="padding:12px 16px; text-align:right; font-family:Georgia,serif; font-size:15px; font-weight:bold; color:{NAVY}; border-bottom:1px solid #eef2f6;">{proj}</td>
<td style="padding:12px 16px; text-align:right; font-family:Arial,sans-serif; font-size:13px; font-weight:bold; color:{BLUE}; border-bottom:1px solid #eef2f6;">{change_cell}</td>
</tr>"""

    # ----- Opportunity / Risk items -----
    def points_html(items):
        out = ""
        for i, it in enumerate(items):
            lead, rest = split_lead(it)
            pad = "0" if i == len(items) - 1 else "0 0 10px 0"
            body = f'<strong style="color:{NAVY};">{lead}.</strong> {rest}' if rest else f'<strong style="color:{NAVY};">{lead}</strong>'
            out += f'<tr><td style="padding:{pad}; font-family:Arial,sans-serif; font-size:13px; line-height:1.5; color:{SLATE};">{body}</td></tr>'
        return out

    opp_html = points_html(article.get("opportunity", []))
    risk_html = points_html(article.get("risk", []))

    # ----- Strategic recommendations -----
    opts_list = article.get("strategic_options", [])
    options = ""
    for i, o in enumerate(opts_list, 1):
        pad = "0" if i == len(opts_list) else "0 0 11px 0"
        options += f"""
<tr>
<td width="30" valign="top"><div style="width:22px; height:22px; background-color:{BLUE}; border-radius:11px; color:#ffffff; font-family:Arial,sans-serif; font-size:12px; font-weight:bold; text-align:center; line-height:22px;">{i}</div></td>
<td style="font-family:Arial,sans-serif; font-size:14px; line-height:1.45; color:{SLATE}; padding:{pad};">{html.escape(str(o))}</td>
</tr>"""

    # ----- What it means (split into paragraphs) -----
    impact_paras = ""
    for para in [p for p in str(article.get("doha_bank_impact", "")).split("\n") if p.strip()]:
        impact_paras += f'<p style="margin:0 0 12px 0; font-family:Georgia,serif; font-size:15px; line-height:1.65; color:{SLATE};">{html.escape(para.strip())}</p>'

    source_title = html.escape(topic.get("source_title", "Source article"))
    source_name = html.escape(topic.get("source_name", "News source"))
    source_url = html.escape(topic.get("source_url", "#"))
    source_date = html.escape(str(topic.get("source_date", "")).strip())
    source_meta = f'{source_name}'
    if source_date:
        source_meta += f' &nbsp;|&nbsp; {source_date}'
    _ss = str(article.get("source_summary", "")).strip()
    if _ss and not _ss.endswith(("\u2026", "...")):
        _ss = _ss.rstrip(".") + "\u2026"
    source_summary = html.escape(_ss)

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DB Strategy Weekly</title>
</head>
<body style="margin:0; padding:0; background-color:#eceff4; font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#eceff4; padding:30px 12px;"><tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:640px; max-width:100%; background-color:#ffffff; border-radius:6px; overflow:hidden; box-shadow:0 4px 22px rgba(0,25,70,0.10);">

  <tr><td style="background-color:{NAVY}; padding:22px 40px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:Arial,Helvetica,sans-serif; font-size:12px; font-weight:bold; letter-spacing:1px; color:#ffffff;">DB Weekly Opportunities &amp; Risks</td>
      <td align="right" style="font-family:Arial,sans-serif; font-size:12px; color:#a9c4e6; letter-spacing:1px;">{TODAY}</td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:22px 40px 0 40px;">
    <h1 style="margin:0; font-family:Georgia,'Times New Roman',serif; font-size:30px; line-height:1.15; font-weight:bold; color:{NAVY};">Weekly Strategic Outlook</h1>
    <p style="margin:7px 0 0 0; font-family:Arial,sans-serif; font-size:14px; color:{GREY};">Key Opportunities, Risks and Strategic Implications for Doha Bank</p>
    <table role="presentation" cellpadding="0" cellspacing="0" style="margin:22px 0 0 0;"><tr>
      <td style="background-color:#eaf1fb; border:1px solid #cfe0f6; border-radius:12px; padding:4px 13px; font-family:Arial,sans-serif; font-size:11px; font-weight:bold; letter-spacing:0.3px; color:{BLUE};">&#10022;&nbsp; Prepared by DB Strategy AI Analyst</td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:22px 40px 0 40px;">
    <p style="margin:0 0 10px 0; font-family:Arial,sans-serif; font-size:11px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">This week&rsquo;s source</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f7f9fc; border-radius:6px;"><tr>
      <td style="padding:16px 20px;">
        <p style="margin:0 0 8px 0; font-family:Georgia,serif; font-size:16px; font-weight:bold; color:{NAVY};">{source_title}</p>
        <p style="margin:0 0 10px 0; font-family:Arial,sans-serif; font-size:11px; color:#111111;">{source_meta}</p>
        <p style="margin:0; font-family:Arial,sans-serif; font-size:13px; line-height:1.5; color:#5a6b7d;">{source_summary}</p>
      </td>
      <td align="right" valign="middle" style="padding:16px 20px 16px 0; white-space:nowrap;">
        <a href="{source_url}" style="font-family:Arial,sans-serif; font-size:12px; font-weight:bold; color:#ffffff; background-color:{BLUE}; border-radius:5px; padding:9px 16px; text-decoration:none;">Read more &rarr;</a>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:28px 40px 0 40px;">
    <p style="margin:0 0 12px 0; font-family:Arial,sans-serif; font-size:11px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">What it means for Doha Bank</p>
    {impact_paras}
  </td></tr>

  <tr><td style="padding:26px 40px 0 40px;">
    <p style="margin:0 0 10px 0; font-family:Arial,sans-serif; font-size:11px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">Projected impact on key metrics</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e6ebf2; border-radius:6px; border-collapse:separate; overflow:hidden;">
      <tr style="background-color:{NAVY};">
        <td style="padding:9px 16px; font-family:Arial,sans-serif; font-size:11px; letter-spacing:0.5px; text-transform:uppercase; color:#ffffff;">Metric</td>
        <td style="padding:9px 16px; text-align:right; font-family:Arial,sans-serif; font-size:11px; letter-spacing:0.5px; text-transform:uppercase; color:#a9c4e6;">Current</td>
        <td style="padding:9px 16px; text-align:right; font-family:Arial,sans-serif; font-size:11px; letter-spacing:0.5px; text-transform:uppercase; color:#ffffff;">Projected</td>
        <td style="padding:9px 16px; text-align:right; font-family:Arial,sans-serif; font-size:11px; letter-spacing:0.5px; text-transform:uppercase; color:#ffffff;">Change</td>
      </tr>
      {rows}
    </table>
    <p style="margin:8px 2px 0 2px; font-family:Arial,sans-serif; font-size:10px; line-height:1.5; color:{MUTED};">Doha Bank figures are based on reported results. Projected figures represent model-based estimates intended to illustrate potential impacts.</p>
  </td></tr>

  <tr><td style="padding:28px 40px 0 40px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="49%" valign="top" style="padding-right:14px;">
        <p style="margin:0 0 12px 0; padding-bottom:8px; border-bottom:2px solid {BLUE}; font-family:Arial,sans-serif; font-size:11px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">Opportunity</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{opp_html}</table>
      </td>
      <td width="2%">&nbsp;</td>
      <td width="49%" valign="top" style="padding-left:14px;">
        <p style="margin:0 0 12px 0; padding-bottom:8px; border-bottom:2px solid {NAVY}; font-family:Arial,sans-serif; font-size:11px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{NAVY};">Risk</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{risk_html}</table>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:30px 40px 0 40px;">
    <p style="margin:0 0 13px 0; font-family:Arial,sans-serif; font-size:11px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">Strategic recommendations</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{options}</table>
  </td></tr>

  <tr><td style="padding:28px 40px 28px 40px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e4e9f0;"><tr>
      <td style="padding-top:15px; font-family:Arial,sans-serif; font-size:10px; color:{MUTED}; line-height:1.5;">The analysis expressed is that of the DB Strategy AI Analyst.</td>
      <td align="right" style="padding-top:15px; font-family:Arial,sans-serif; font-size:10px; color:#a7b2c2;">&copy; 2026 Doha Bank</td>
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
