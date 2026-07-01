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
    for idx, r in enumerate(article.get("impact_table", [])):
        metric = html.escape(safe_get(r, "metric", "line_item"))
        curr = html.escape(safe_get(r, "current_value", "current", default="\u2014") or "\u2014")
        proj = html.escape(safe_get(r, "projected_value", "projected", default="\u2014") or "\u2014")
        change = safe_get(r, "change", default="")
        change_cell = html.escape(change) if change else "\u2014"
        row_bg = "#ffffff" if idx % 2 == 0 else "#f7fafd"
        rows += f"""
<tr style="background-color:{row_bg};">
<td class="cell" style="padding:15px 18px; font-family:Arial,sans-serif; font-size:15px; color:{SLATE}; border-bottom:1px solid #eef2f6;">{metric}</td>
<td class="cell" style="padding:15px 18px; text-align:right; font-family:Georgia,serif; font-size:16px; color:{MUTED}; border-bottom:1px solid #eef2f6;">{curr}</td>
<td class="cell" style="padding:15px 18px; text-align:right; font-family:Georgia,serif; font-size:16px; font-weight:bold; color:{NAVY}; border-bottom:1px solid #eef2f6;">{proj}</td>
<td class="cell" style="padding:15px 18px; text-align:right; font-family:Arial,sans-serif; font-size:14px; font-weight:bold; color:{BLUE}; border-bottom:1px solid #eef2f6;">{change_cell}</td>
</tr>"""

    # ----- Opportunity / Risk items (as paragraphs inside a card) -----
    def points_html(items):
        out = ""
        for i, it in enumerate(items):
            lead, rest = split_lead(it)
            mb = "0" if i == len(items) - 1 else "0 0 10px 0"
            body = f'<strong style="color:{NAVY};">{lead}.</strong> {rest}' if rest else f'<strong style="color:{NAVY};">{lead}</strong>'
            out += f'<p style="margin:{mb}; font-family:Arial,sans-serif; font-size:15px; line-height:1.6; color:{SLATE};">{body}</p>'
        return out

    opp_html = points_html(article.get("opportunity", []))
    risk_html = points_html(article.get("risk", []))

    # ----- Strategic recommendations -----
    opts_list = article.get("strategic_options", [])
    options = ""
    for i, o in enumerate(opts_list, 1):
        pad = "2px 0 0 4px" if i == len(opts_list) else "2px 0 13px 4px"
        badge_pad = "" if i == len(opts_list) else "padding-bottom:13px;"
        options += f"""
<tr>
<td width="34" valign="top" style="{badge_pad}"><div style="width:25px; height:25px; background-color:{BLUE}; border-radius:13px; color:#ffffff; font-family:Arial,sans-serif; font-size:13px; font-weight:bold; text-align:center; line-height:25px;">{i}</div></td>
<td style="font-family:Arial,sans-serif; font-size:15px; line-height:1.55; color:{SLATE}; padding:{pad};">{html.escape(str(o))}</td>
</tr>"""

    # ----- What it means (split into paragraphs) -----
    impact_paras = ""
    for para in [p for p in str(article.get("doha_bank_impact", "")).split("\n") if p.strip()]:
        impact_paras += f'<p class="body-text" style="margin:0 0 14px 0; font-family:Georgia,serif; font-size:16px; line-height:1.75; color:{SLATE};">{html.escape(para.strip())}</p>'

    source_title = html.escape(topic.get("source_title", "Source article"))
    source_name = html.escape(topic.get("source_name", "News source"))
    source_url = html.escape(topic.get("source_url", "#"))
    source_date = html.escape(str(topic.get("source_date", "")).strip())
    source_meta = f'{source_name}'
    if source_date:
        source_meta += f' &nbsp;&middot;&nbsp; {source_date}'
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
<style>
  @media only screen and (max-width:620px) {{
    .container {{ width:100% !important; border-radius:0 !important; }}
    .pad {{ padding-left:24px !important; padding-right:24px !important; }}
    .h1 {{ font-size:26px !important; line-height:1.2 !important; }}
    .body-text {{ font-size:17px !important; line-height:1.75 !important; }}
    .source-title {{ font-size:19px !important; }}
    .source-summary {{ font-size:16px !important; }}
    .cell {{ font-size:15px !important; padding:13px 14px !important; }}
    .stack {{ display:block !important; width:100% !important; margin:0 0 14px 0 !important; }}
    .stack-last {{ margin:0 !important; }}
    .spacer {{ display:none !important; }}
  }}
</style>
</head>
<body style="margin:0; padding:0; background-color:#e7ecf3; font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#e7ecf3; padding:32px 12px;"><tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" class="container" style="width:640px; max-width:100%; background-color:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 6px 26px rgba(0,25,70,0.12);">

  <tr><td style="height:3px; background-color:{BLUE}; font-size:0; line-height:0;">&nbsp;</td></tr>

  <tr><td class="pad" style="padding:26px 40px 24px 40px; border-bottom:1px solid #eaeef4;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:Arial,Helvetica,sans-serif; font-size:12px; font-weight:bold; letter-spacing:2px; text-transform:uppercase; color:{BLUE};">DB Weekly &nbsp;&middot;&nbsp; Opportunities &amp; Risks</td>
      <td align="right" style="font-family:Arial,sans-serif; font-size:12px; letter-spacing:1px; color:{MUTED};">{TODAY}</td>
    </tr></table>
    <h1 class="h1" style="margin:20px 0 0 0; font-family:Georgia,'Times New Roman',serif; font-size:34px; line-height:1.12; font-weight:bold; color:{NAVY};">Weekly Strategic Outlook</h1>
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

  <tr><td class="pad" style="padding:32px 40px 0 40px;">
    <p style="margin:0 0 14px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; font-weight:bold; color:{BLUE};">What it means for Doha Bank</p>
    {impact_paras}
  </td></tr>

  <tr><td class="pad" style="padding:32px 40px 0 40px;">
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

  <tr><td class="pad" style="padding:32px 40px 0 40px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="49%" valign="top" class="stack" style="background-color:#f2f7fd; border-radius:8px; border-top:3px solid {BLUE}; padding:18px 20px;">
        <p style="margin:0 0 10px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; font-weight:bold; color:{BLUE};">Opportunity</p>
        {opp_html}
      </td>
      <td width="2%" class="spacer" style="font-size:0; line-height:0;">&nbsp;</td>
      <td width="49%" valign="top" class="stack stack-last" style="background-color:#f4f6f9; border-radius:8px; border-top:3px solid {NAVY}; padding:18px 20px;">
        <p style="margin:0 0 10px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:1.5px; text-transform:uppercase; font-weight:bold; color:{NAVY};">Risk</p>
        {risk_html}
      </td>
    </tr></table>
  </td></tr>

  <tr><td class="pad" style="padding:32px 40px 0 40px;">
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
