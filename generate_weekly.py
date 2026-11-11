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

# Brand palette sampled directly from the approved Doha Bank logo.
NAVY = "#002454"        # exact brand navy
BLUE = "#274063"        # soft navy for secondary accents / links (name kept for compatibility)
GOLD = "#c0a884"        # exact brand camel — decorative rules
CAMEL_TEXT = "#9a7c4f"  # deeper camel that stays legible as small text
PALE_GOLD = "#f9f6f0"   # warm paper tint for cards
SLATE = "#36434f"       # body ink
MUTED = "#948d7e"       # warm muted grey
GREY = "#6f6a5e"        # warm grey for labels
LINE = "#e8e3d9"        # warm hairline rule
PAGE_BG = "#ece9e2"     # warm page background

# Host the approved Doha Bank logo on a public HTTPS URL (for example,
# a raw GitHub asset URL) and store it in GitHub Actions as DB_LOGO_URL.
# Email clients cannot load a logo directly from the repository filesystem.
LOGO_URL = os.getenv("DB_LOGO_URL", "").strip()
# Do not emit a known broken placeholder URL into executive emails.
if "REPLACE_WITH_" in LOGO_URL:
    LOGO_URL = ""

METRIC_LABELS = {
    "customer_deposits": "Customer Deposits",
    "net_loans": "Net Loans",
    "loan_deposit_ratio_pct": "Loan-to-Deposit Ratio",
    "total_assets": "Total Assets",
    "net_profit": "Net Profit",
    "shareholders_equity": "Shareholders’ Equity",
    "equity": "Equity",
    "total_income": "Total Income",
    "operating_income": "Operating Income",
    "operating_expenses": "Operating Expenses",
    "cost_income_ratio_pct": "Cost-to-Income Ratio",
    "npl_ratio_pct": "NPL Ratio",
    "capital_adequacy_ratio_pct": "Capital Adequacy Ratio",
    "return_on_assets_pct": "Return on Assets",
    "return_on_equity_pct": "Return on Equity",
    "roa_pct": "Return on Assets",
    "roe_pct": "Return on Equity",
    "net_interest_income": "Net Interest Income",
    "net_interest_margin_pct": "Net Interest Margin",
    "nim_pct": "Net Interest Margin",
    "total_liabilities": "Total Liabilities",
    "total_revenue": "Total Revenue",
    "earnings_per_share": "Earnings per Share",
    "eps": "Earnings per Share",
}

RAW_METRIC_CODE_PATTERN = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")


def clean_metric_label(row):
    """Return a human-friendly label and prevent raw database metric codes leaking."""
    code = str(row.get("metric_code") or "").strip()
    name = str(row.get("metric_name") or "").strip()

    if code in METRIC_LABELS:
        return METRIC_LABELS[code]

    if name and not RAW_METRIC_CODE_PATTERN.search(name):
        return name

    if name in METRIC_LABELS:
        return METRIC_LABELS[name]

    if code:
        return code.replace("_pct", "").replace("_", " ").title()

    return "Metric"


def remove_raw_metric_codes_from_text(value):
    """Clean any accidental metric-code leakage from AI output."""
    text = str(value or "")

    for code, label in METRIC_LABELS.items():
        text = text.replace(code, label)

    # Final generic cleanup for any remaining snake_case text.
    def repl(match):
        code = match.group(0)
        return code.replace("_pct", "").replace("_", " ").title()

    return RAW_METRIC_CODE_PATTERN.sub(repl, text)


def sanitize_article(article):
    """Recursively clean article output before rendering email."""
    if isinstance(article, dict):
        return {k: sanitize_article(v) for k, v in article.items()}
    if isinstance(article, list):
        return [sanitize_article(v) for v in article]
    if isinstance(article, str):
        return remove_raw_metric_codes_from_text(article)
    return article


def ask_claude(prompt, max_tokens=5000):
    strict_prompt = prompt + """

IMPORTANT OUTPUT RULES:
Return EXACTLY ONE valid JSON object.
Do not restart, apologise, explain mistakes, or output multiple JSON attempts.
Do not include markdown or ```json fences.
Do not include any text before or after the JSON.
The response must start with { and end with }.
Use double quotes for all JSON keys and string values.
Escape all internal quotes inside strings.
Do not use trailing commas.
If you detect a mistake, correct it internally and output only the final JSON object.
"""

    # A reasoning response can consume the token allowance without producing
    # any text. Retry with a larger allowance; fail with actionable diagnostics.
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    for attempt, budget in enumerate((max(max_tokens, 8000), 16000), start=1):
        response = client.messages.create(
            model=model,
            max_tokens=budget,
            messages=[{"role": "user", "content": strict_prompt}],
        )
        blocks = [b.text.strip() for b in response.content
                  if getattr(b, "type", None) == "text" and getattr(b, "text", "").strip()]
        if blocks:
            return "\n".join(blocks)
        print(f"WARNING: Claude article attempt {attempt} returned no text; "
              f"stop_reason={response.stop_reason!r}; "
              f"content_types={[getattr(b, 'type', None) for b in response.content]}; "
              f"max_tokens={budget}", flush=True)
    raise ValueError(
        "Claude returned only reasoning/no text after two attempts. "
        "Try a non-thinking model via ANTHROPIC_MODEL or reduce the article prompt."
    )


def extract_json_object(text):
    """
    Extract the last complete JSON object returned by Claude.

    Handles markdown fences, commentary around JSON, illegal control
    characters, and multiple JSON attempts in a single response.
    """
    if not text or not text.strip():
        raise ValueError("Claude returned an empty response.")

    cleaned = text.strip()
    cleaned = re.sub(r"```(?:json|JSON)?", "", cleaned)
    cleaned = cleaned.replace("```", "").strip()

    # Remove illegal raw control characters that can break JSON strings.
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", cleaned)

    decoder = json.JSONDecoder()
    objects = []
    index = 0

    while index < len(cleaned):
        if cleaned[index] != "{":
            index += 1
            continue

        try:
            obj, consumed = decoder.raw_decode(cleaned[index:])
            if isinstance(obj, dict):
                objects.append(obj)
            index += max(consumed, 1)
        except json.JSONDecodeError:
            index += 1

    if objects:
        # Claude may correct itself by producing a second object.
        # The last valid object is normally the final intended response.
        return objects[-1]

    print("Failed to parse Claude JSON.")
    print("Claude output preview:")
    print(cleaned[:4000])
    raise ValueError("No valid JSON object found in Claude response.")


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
        "- Never display raw database metric codes such as customer_deposits, net_loans, or loan_deposit_ratio_pct.",
        "- Always use the clean metric labels shown below.",
        ""
    ]

    latest_period = metrics[0].get("period_end", "")
    if latest_period:
        lines.append(f"Latest available reporting period: {latest_period}")
        lines.append("")

    for m in metrics:
        metric_name = clean_metric_label(m)
        period_end = m.get("period_end") or ""
        category = m.get("metric_category") or ""
        value = format_metric_value(m)

        line = f"- {metric_name}: {value}"

        if period_end:
            line += f" | period: {period_end}"
        if category:
            line += f" | category: {category}"

        lines.append(line)

    return "\n".join(lines)


def public_data_stress_rows(metrics):
    """Auditable deposit and credit sensitivities from public balances; NOT forecasts.

    All scenarios are conditional, uncalibrated shocks. Missing balances are
    omitted, never estimated. The "Projected" column always means a closing
    balance or incremental credit impairment as explicitly stated in the metric label.
    """
    latest = {}
    for r in sorted(metrics, key=lambda x: str(x.get("period_end") or ""), reverse=True):
        key = str(r.get("metric_code") or r.get("metric_name") or r.get("metric") or "").lower().strip()
        if key and key not in latest:
            latest[key] = r

    def balance(*keys):
        for key in keys:
            r = latest.get(key)
            if not r:
                continue
            try:
                val = float(r["value"])
            except (TypeError, ValueError, KeyError):
                continue
            unit = str(r.get("unit") or "").lower().strip()
            factors = {"thousand": 1e3, "thousands": 1e3, "qar thousands": 1e3,
                       "qar '000": 1e3, "qar000": 1e3, "million": 1e6,
                       "millions": 1e6, "billion": 1e9, "billions": 1e9,
                       "qar": 1, "qar units": 1, "units": 1, "": 1, "currency": 1}
            if unit not in factors:
                continue
            return val * factors[unit], str(r.get("period_end") or "date not provided")
        return None, None

    def money(value):
        return f"QAR {value / 1e9:,.2f}bn" if abs(value) >= 1e9 else f"QAR {value / 1e6:,.1f}m"

    deposits, deposit_date = balance("customer_deposits", "total_customer_deposits")
    loans, loan_date = balance("net_loans", "net_customer_loans", "loans_and_advances")
    rows, notes = [], []
    scenarios = (("Contained", .01, 10),
                 ("Elevated", .03, 25),
                 ("Severe", .05, 50))
    for name, outflow_rate, impairment_bp in scenarios:
        if deposits is not None:
            outflow = deposits * outflow_rate
            rows.append({"metric": f"{name} | Customer deposits after assumed outflow",
                         "current_value": money(deposits),
                         "projected_value": money(deposits - outflow),
                         "change": f"-{money(outflow)} ({outflow_rate:.0%} 30-day shock); liquidity demand, not earnings loss"})
        if loans is not None:
            impairment = loans * impairment_bp / 10000
            rows.append({"metric": f"{name} | Incremental credit impairment (sensitivity)",
                         "current_value": f"Net loan proxy: {money(loans)}",
                         "projected_value": money(impairment),
                         "change": f"+{impairment_bp}bp on all net loans; uncalibrated, NOT expected credit loss"})
    if deposits is not None:
        notes.append(f"Customer deposits {money(deposits)} as of {deposit_date}; 30-day 1% / 3% / 5% withdrawal shocks; closing deposits = opening deposits minus outflow. Liquidity buffers not modeled.")
    if loans is not None:
        notes.append(f"Net loan proxy {money(loans)} as of {loan_date}; illustrative additional impairment shocks of 10 / 25 / 50bp on net loans, not PD/LGD or sector-specific ECL estimates.")
    notes.append("CONFIDENCE: reported baselines high only when source and period are verified; arithmetic high; assumptions and inferred bank outcomes low. Scenarios are NOT forecasts, probabilities, actual exposure estimates or regulatory stress tests. Different disclosure dates can make aggregated funding proxies non-contemporaneous.")
    return rows, notes


def validate_regular_projections(article):
    """Keep only numeric, explained estimates; never turn commentary into a forecast."""
    numeric = re.compile(r"(?:QAR\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*(?:%|bps?|bn|m|million|billion|x)(?![A-Za-z]))", re.I)
    rows = []
    for row in article.get("impact_table", []):
        if not isinstance(row, dict):
            continue
        projected = str(row.get("projected_value") or "").strip()
        change = str(row.get("change") or "").strip()
        current = str(row.get("current_value") or "").strip()
        # A numerical estimate requires a reported baseline and a short, explicit
        # calculation basis. No unsupported figures are silently manufactured.
        if numeric.search(projected) and numeric.search(current) and change:
            rows.append(row)
    article["impact_table"] = rows[:5]
    return article


def ai_write_article(topic, metrics, bank_name, impact_rules):
    formatted_metrics = format_metrics_for_ai(metrics)
    stress_rows, _stress_notes = public_data_stress_rows(metrics)
    scenario_guidance = """
SPECIAL MANDATORY FORMAT — US/ISRAEL-IRAN ESCALATION SCENARIO:
This is a forward-looking strategic stress assessment, NOT an incident recap.
Lead with the current geopolitical risk and its business significance, not a hypothetical question. Frame renewed US/Israeli strikes on Iran as a material escalation scenario requiring readiness, without asserting the strikes are imminent or more likely than not unless the cited Reuters report explicitly supports that conclusion.
Use the Reuters source only as the verified news trigger. Do not assert hypothetical attacks occurred.
In doha_bank_impact, prioritize the central management assessment of renewed military escalation; mention limited versus prolonged conflict only when this materially changes the conclusion. Trace impacts through Qatar LNG exports and routes, oil/LNG prices, fiscal conditions, investor sentiment, banking-system liquidity and funding, corporate credit risk, trade finance, and treasury risk. State that higher energy prices may be beneficial to revenues while physical disruption may offset those gains. Explain what Doha Bank should monitor, without claiming bank-specific exposures absent data.
The risk list must identify the most material Qatar/Doha Bank transmission channels, not focus on a tanker being hit. The strategic options must specify an accountable function, a measurable trigger and a 30-day deliverable: Treasury/ALM to verify actual refinancing and repricing ladders; Risk/Wholesale to quantify sector concentrations and early-warning indicators; Strategy/Treasury to monitor verified military developments, oil/LNG prices, shipping insurance and sovereign spreads. Never treat a speech or headline as verified mobilisation or an actual strike.
Use the reproducible three-scenario public-data sensitivity table supplied below. Do not generate or quote numerical wholesale funding-cost shocks, repricing percentages, funding proxy amounts, or combined earnings-at-risk estimates. Discuss wholesale funding qualitatively only: actual refinancing and repricing maturities are not available in public disclosures. Do not present scenario severity as event likelihood. Distinguish starting balances, ending balances, and illustrative incremental credit impairment sensitivities. These are conditional stress sensitivities, NOT forecasts, likelihood estimates, or bank exposure estimates. Do not imply that deposit outflows are a P&L loss. State assumptions briefly in the table row itself. Do not add a separate methodology or public-data stress-sensitivities paragraph. Distinguish verified news from modeled outcomes.
""" if topic.get("scenario_assessment") else ""

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
    {{"recommendation": "...", "business_owner": "..."}},
    {{"recommendation": "...", "business_owner": "..."}},
    {{"recommendation": "...", "business_owner": "..."}}
  ]
}}

Global rules:
- Do NOT invent financial numbers.
- Only use Doha Bank figures present in the Supabase data below.
- If a reported current metric is missing, do not fabricate it. For regular articles, omit that metric from the projection table.
- Never write "not available".
- Never display raw database metric codes such as customer_deposits, net_loans, loan_deposit_ratio_pct, total_assets, net_profit, cost_income_ratio_pct, or capital_adequacy_ratio_pct.
- Always use clean business metric labels such as Customer Deposits, Net Loans, Loan-to-Deposit Ratio, Total Assets, Net Profit, Cost-to-Income Ratio, and Capital Adequacy Ratio.

source_summary:
- This field appears under "This Week's Source".
- Use only text from the original source article or RSS feed.
- Do NOT summarize.
- Do NOT rewrite.
- Do NOT paraphrase.
- Do NOT add analysis.
- Select exactly ONE sentence only.
- Maximum 35 words.
- Preserve the original wording.
- Prefer source_excerpt if available.
- Prefer source_description if available.
- Never generate a new summary when source text exists.

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
- For REGULAR (non-scenario) articles: projected_value MUST be a numeric event-specific projected RANGE (e.g. "QAR 51.0bn–51.5bn"), like the original executive report, never descriptive text or generic +1%/+3% sensitivities.
- Use the reported Doha Bank baseline and a reasoned event-specific estimate of the likely range; the Change column should explain the business driver and indicative magnitude in concise executive prose. Clearly label model-based estimates rather than reported facts.
- Do not claim a competitor's deal, project value, or total market opportunity as Doha Bank's expected revenue or loans. Do not assume a win, mandate, market share, growth rate or probability without supporting evidence.
- If a numerical range cannot be reasonably supported by the available source and baseline, omit that metric; never disguise an unsupported number as a verified forecast.
- For the single geopolitical scenario article, follow the special stress-table instructions below instead.
- change = short explanation of the transmission mechanism or expected movement, not an unsupported numeric change.
- Do not include a metric if the development does not plausibly move it.
- The metric field must contain only clean business labels, never snake_case database codes.

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

{scenario_guidance}
Selected topic:
{json.dumps(topic, ensure_ascii=False)}
Source text rules:
- If source_excerpt exists, use it directly for source_summary.
- Otherwise use source_description.
- Otherwise use the first sentence of the source text provided.
- Never create an AI-written summary when source text is available.

Impact rules:
{json.dumps(impact_rules, ensure_ascii=False)}

Available Doha Bank metrics from Supabase:
{formatted_metrics}

Strict financial data rules:
- Treat the formatted Supabase metrics above as the only source of Doha Bank reported figures.
- Values are already converted into QAR bn, QAR m, %, or ratio format.
- Do not write values in "k" or "thousands".
- Do not re-scale or re-convert the figures.
- For regular articles, use numeric projected ranges, consistent with the original executive report. Explain event-specific drivers in Change. Omit unsupported rows; do not use generic sensitivity percentages.
- If the required current metric is not listed above, leave current_value as "".
"""

    text = ask_claude(prompt)
    article = sanitize_article(extract_json_object(text))
    if topic.get("scenario_assessment") and stress_rows:
        # Never ask a language model to manufacture bank-specific numbers.
        # Deterministic projections supersede narrative/qualitative model rows.
        article["impact_table"] = stress_rows
    elif not topic.get("scenario_assessment"):
        article = validate_regular_projections(article)
    elif topic.get("scenario_assessment"):
        # No reported baseline: do not manufacture numeric estimates.
        directional = {
            "liquid": "Potential tightening",
            "fund": "Upward cost pressure",
            "deposit": "Potential volatility",
            "credit": "Higher downside risk",
            "loan": "Selective pressure",
            "trade": "Disruption risk increases",
            "treasury": "Higher volatility",
            "market": "Higher volatility",
            "lng": "Higher disruption risk",
            "energy": "Price upside; export risk",
            "capital": "Potential pressure",
            "profit": "Downside risk",
            "income": "Greater earnings volatility",
            "asset": "Higher risk sensitivity",
        }
        for row in article.get("impact_table", []):
            if not isinstance(row, dict):
                continue
            projected = str(row.get("projected_value") or "").strip()
            if not projected or projected.lower() in {"—", "-", "n/a", "not available", "unknown"}:
                metric = str(row.get("metric") or "").lower()
                row["projected_value"] = next((v for k, v in directional.items() if k in metric), "Heightened risk sensitivity")
    return article


def build_final_email(topic, article):
    # Original executive presentation: projected ranges for standard articles;
    # a separate stress presentation only for the geopolitical scenario.
    is_scenario = bool(topic.get("scenario_assessment"))
    table_title = ("Illustrative deposit and credit sensitivities — not forecasts"
                   if is_scenario else "Projected impact on key metrics")
    projected_header = "Illustrative value" if is_scenario else "Projected"
    table_note = ("Conditional stress sensitivities, not forecasts or verified exposures."
                  if is_scenario else
                  "Doha Bank figures are based on reported results. Projected figures are model-based estimates intended to illustrate potential impact.")
    rows = ""

    for idx, r in enumerate(article.get("impact_table", [])):
        metric = html.escape(remove_raw_metric_codes_from_text(safe_get(r, "metric", "line_item")))
        curr = html.escape(remove_raw_metric_codes_from_text(safe_get(r, "current_value", "current", default="—") or "—"))
        proj = html.escape(remove_raw_metric_codes_from_text(safe_get(r, "projected_value", "projected", default="—") or "—"))
        change = remove_raw_metric_codes_from_text(safe_get(r, "change", default=""))
        change_cell = html.escape(change) if change else "—"
        row_bg = "#ffffff" if idx % 2 == 0 else "#f9f6f0"

        rows += f"""
<tr style="background-color:{row_bg};">
<td class="cell" style="padding:11px 14px; font-family:Arial,sans-serif; font-size:15px; color:{SLATE}; border-bottom:1px solid #ece7dd;">{metric}</td>
<td class="cell" style="padding:11px 14px; text-align:right; font-family:Georgia,serif; font-size:15px; color:{MUTED}; border-bottom:1px solid #ece7dd;">{curr}</td>
<td class="cell" style="padding:11px 14px; text-align:right; font-family:Georgia,serif; font-size:15px; font-weight:bold; color:{NAVY}; border-bottom:1px solid #ece7dd;">{proj}</td>
<td class="cell" style="padding:11px 14px; text-align:right; font-family:Arial,sans-serif; font-size:14px; font-weight:bold; color:{CAMEL_TEXT}; border-bottom:1px solid #ece7dd;">{change_cell}</td>
</tr>"""

    def points_html(items):
        out = ""

        for i, it in enumerate(items):
            lead, rest = split_lead(remove_raw_metric_codes_from_text(it))
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
            recommendation = html.escape(remove_raw_metric_codes_from_text(str(o.get("recommendation", "")).strip()))
            owner = html.escape(str(o.get("business_owner", "")).strip())
        else:
            # Backward compatibility in case the AI returns the old string format.
            recommendation = html.escape(remove_raw_metric_codes_from_text(str(o).strip()))
            owner = ""

        owner_html = ""
        if owner:
            owner_html = f"""
            <div style="margin-top:7px; font-family:Arial,sans-serif; font-size:12px; line-height:1.4; color:{GREY};">
              <strong style="color:{NAVY};">Suggested business owner:</strong> {owner}
            </div>"""

        options += f"""
<tr>
<td width="34" valign="top" style="{badge_pad} padding-top:1px;"><div style="width:23px; height:23px; background-color:{NAVY}; border-radius:12px; color:#ffffff; font-family:Arial,sans-serif; font-size:12px; font-weight:bold; text-align:center; line-height:23px;">{i}</div></td>
<td valign="top" style="font-family:Arial,sans-serif; font-size:15px; line-height:1.5; color:{SLATE}; padding:0 0 16px 6px;">
  <div>{recommendation}</div>
  {owner_html}
</td>
</tr>"""

    impact_paras = ""
    impact_text = remove_raw_metric_codes_from_text(str(article.get("doha_bank_impact", "")).replace("\\n", "\n"))

    for para in [p for p in impact_text.split("\n") if p.strip()]:
        impact_paras += f'<p class="body-text" style="margin:0 0 14px 0; font-family:Georgia,serif; font-size:15px; line-height:1.65; color:{SLATE};">{html.escape(para.strip())}</p>'

    source_title = html.escape(topic.get("source_title", "Source article"))
    source_name = html.escape(topic.get("source_name", "News source"))
    source_url = html.escape(topic.get("source_url", "#"))
    source_date = html.escape(str(topic.get("source_date", "")).strip())

    # Keep publisher and date in separate bidi-isolated spans.
    # This prevents Arabic publisher names from visually reordering dates
    # (e.g. "5 · Arabic-source August 2026").
    source_meta_parts = []
    if source_name:
        source_meta_parts.append(
            f'<span dir="auto" style="unicode-bidi:isolate;">{source_name}</span>'
        )
    if source_date:
        source_meta_parts.append(
            f'<span dir="ltr" style="unicode-bidi:isolate; white-space:nowrap;">{source_date}</span>'
        )
    source_meta = ' <span dir="ltr" style="unicode-bidi:isolate;">&middot;</span> '.join(source_meta_parts)

    source_summary_raw = remove_raw_metric_codes_from_text(str(article.get("source_summary", "")).strip())
    if source_summary_raw and not source_summary_raw.endswith(("…", "...")):
        source_summary_raw = source_summary_raw.rstrip(".") + "…"

    source_summary = html.escape(source_summary_raw)

    def section_label(text):
        return (
            f'<p style="margin:0 0 9px 0; font-family:Georgia,\'Times New Roman\',serif; '
            f'font-size:13px; letter-spacing:1.2px; text-transform:uppercase; font-weight:bold; '
            f'color:{NAVY};">{text}</p>'
            f'<div style="width:34px; height:2px; background-color:{GOLD}; margin:0 0 16px 0; '
            f'font-size:0; line-height:0;">&nbsp;</div>'
        )

    logo_markup = (f'<img src="{html.escape(LOGO_URL, quote=True)}" width="180" alt="Doha Bank" '
                   'style="display:block; width:180px; height:auto; border:0;">'
                   if LOGO_URL.startswith("https://") else
                   f'<span style="font-family:Arial,sans-serif; font-size:20px; font-weight:bold; color:{NAVY};">DOHA BANK</span>')
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Doha Bank &middot; Weekly Strategy Briefing &mdash; {TODAY}</title>
<style>
  @media only screen and (max-width:620px) {{
    .container {{ width:100% !important; }}
    .pad {{ padding-left:26px !important; padding-right:26px !important; }}
    .h1 {{ font-size:28px !important; line-height:1.18 !important; }}
    .body-text {{ font-size:16px !important; line-height:1.75 !important; }}
    .source-title {{ font-size:18px !important; }}
    .source-summary {{ font-size:15px !important; }}
    .cell {{ font-size:14px !important; padding:12px 12px !important; }}
    .stack {{ display:block !important; width:100% !important; max-width:100% !important; box-sizing:border-box !important; margin:0 0 12px 0 !important; }}
    .stack-last {{ margin:0 !important; }}
    .spacer {{ display:none !important; }}
    .masthead-date {{ font-size:10px !important; }}
  }}
</style>
</head>
<body style="margin:0; padding:0; background-color:{PAGE_BG}; font-family:Arial,Helvetica,sans-serif;">
<div style="display:none; max-height:0; overflow:hidden; mso-hide:all; opacity:0; color:{PAGE_BG};">Weekly strategic opportunities, risks and recommended management actions for Doha Bank.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{PAGE_BG}; padding:40px 20px;"><tr><td align="center">
<table role="presentation" width="720" cellpadding="0" cellspacing="0" class="container" style="width:720px; max-width:100%; background-color:#ffffff; border:1px solid {LINE};">

  <tr><td style="height:3px; background-color:{NAVY}; font-size:0; line-height:0;">&nbsp;</td></tr>

  <tr><td class="pad" style="padding:26px 44px 18px 44px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td valign="middle">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0">
          <tr><td>
            {logo_markup}
          </td></tr>
        </table>
      </td>
      <td class="masthead-date" align="right" valign="middle" style="font-family:Arial,sans-serif; font-size:11px; letter-spacing:1.6px; text-transform:uppercase; color:{MUTED}; white-space:nowrap;">{TODAY}</td>
    </tr></table>
  </td></tr>

  <tr><td class="pad" style="padding:0 44px;"><div style="height:1px; background-color:{GOLD}; font-size:0; line-height:0;">&nbsp;</div></td></tr>

  <tr><td class="pad" style="padding:32px 44px 4px 44px;">
    <p style="margin:0 0 14px 0; font-family:Arial,sans-serif; font-size:11px; font-weight:bold; letter-spacing:3px; text-transform:uppercase; color:{CAMEL_TEXT};">Weekly Strategy Briefing</p>
    <h1 class="h1" style="margin:0; font-family:Georgia,'Times New Roman',serif; font-size:35px; line-height:1.12; font-weight:normal; color:{NAVY};">Opportunities &amp; Risks</h1>
    <p style="margin:14px 0 0 0; font-family:Arial,sans-serif; font-size:14px; line-height:1.55; color:{GREY};">Strategic implications, financial impact and recommended management actions for Doha Bank.</p>
  </td></tr>

  <tr><td class="pad" style="padding:30px 44px 0 44px;">
    {section_label("This week&rsquo;s source")}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{PALE_GOLD}; border:1px solid {LINE}; border-collapse:separate;"><tr>
      <td width="3" style="background-color:{GOLD}; font-size:0; line-height:0;">&nbsp;</td>
      <td style="padding:20px 24px;">
        <p class="source-title" style="margin:0 0 8px 0; font-family:Georgia,serif; font-size:18px; font-weight:bold; line-height:1.35; color:{NAVY};">{source_title}</p>
        <p dir="ltr" style="margin:0 0 12px 0; font-family:Arial,sans-serif; font-size:12px; letter-spacing:0.3px; color:{MUTED};">{source_meta}</p>
        <p class="source-summary" style="margin:0 0 16px 0; font-family:Arial,sans-serif; font-size:15px; line-height:1.65; color:{SLATE};">{source_summary}</p>
        <div style="border-top:1px solid {LINE}; padding-top:12px; text-align:right;">
          <a href="{source_url}" style="font-family:Arial,sans-serif; font-size:11px; font-weight:bold; letter-spacing:1.4px; text-transform:uppercase; color:{NAVY}; text-decoration:none;">Read the source &rarr;</a>
        </div>
      </td>
    </tr></table>
  </td></tr>

  <tr><td class="pad" style="padding:32px 44px 0 44px;">
    {section_label("Executive impact assessment — Doha Bank")}
    {impact_paras}
  </td></tr>

  <tr><td class="pad" style="padding:30px 44px 0 44px;">
    {section_label(table_title)}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE}; border-collapse:collapse;">
      <tr>
        <td style="padding:12px 16px; font-family:Arial,sans-serif; font-size:10px; letter-spacing:1.2px; text-transform:uppercase; font-weight:bold; color:{GREY}; border-bottom:2px solid {NAVY};">Metric</td>
        <td style="padding:12px 16px; text-align:right; font-family:Arial,sans-serif; font-size:10px; letter-spacing:1.2px; text-transform:uppercase; font-weight:bold; color:{GREY}; border-bottom:2px solid {NAVY};">Current</td>
        <td style="padding:12px 16px; text-align:right; font-family:Arial,sans-serif; font-size:10px; letter-spacing:1.2px; text-transform:uppercase; font-weight:bold; color:{GREY}; border-bottom:2px solid {NAVY};">{projected_header}</td>
        <td style="padding:12px 16px; text-align:right; font-family:Arial,sans-serif; font-size:10px; letter-spacing:1.2px; text-transform:uppercase; font-weight:bold; color:{GREY}; border-bottom:2px solid {NAVY};">Change</td>
      </tr>
      {rows}
    </table>
    <p style="margin:11px 2px 0 2px; font-family:Arial,sans-serif; font-size:11px; line-height:1.5; color:{MUTED};">{table_note}</p>
  </td></tr>

  <tr><td class="pad" style="padding:32px 44px 0 44px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="49%" valign="top" class="stack" style="background-color:#ffffff; border:1px solid {LINE}; border-top:2px solid {GOLD}; padding:16px 18px; box-sizing:border-box;">
        <p style="margin:0 0 11px 0; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1.8px; text-transform:uppercase; font-weight:bold; color:{CAMEL_TEXT};">Opportunity</p>
        {opp_html}
      </td>
      <td width="2%" class="spacer" style="font-size:0; line-height:0;">&nbsp;</td>
      <td width="49%" valign="top" class="stack stack-last" style="background-color:#ffffff; border:1px solid {LINE}; border-top:2px solid {NAVY}; padding:16px 18px; box-sizing:border-box;">
        <p style="margin:0 0 11px 0; font-family:Arial,sans-serif; font-size:11px; letter-spacing:1.8px; text-transform:uppercase; font-weight:bold; color:{NAVY};">Risk</p>
        {risk_html}
      </td>
    </tr></table>
  </td></tr>

  <tr><td class="pad" style="padding:32px 44px 4px 44px;">
    {section_label("Strategic recommendations")}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{options}</table>
  </td></tr>

  <tr><td class="pad" style="padding:26px 44px 30px 44px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid {LINE};"><tr>
      <td style="padding-top:16px; font-family:Arial,sans-serif; font-size:11px; color:{MUTED}; line-height:1.6;">
        The analysis expressed is that of the DB Strategy AI Analyst.<br>
        <strong style="color:{NAVY}; letter-spacing:0.4px;">Powered by Strategy &amp; Transformation</strong>
      </td>
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

    

    

    

    

    

    

    
