import os
import json
import html
import argparse
import datetime
import feedparser
import anthropic

# Reuse the exact final-article generator so the approval draft and final send match.
try:
    import generate_weekly as weekly
except ImportError:
    import generate_weekly_fixed as weekly

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

TODAY = datetime.date.today().strftime("%d %B %Y")
NAVY = "#002b5c"
BLUE = "#0072ce"
SLATE = "#2c3e54"
MUTED = "#8a99ad"

NEWS_SOURCES = [
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
]

BLOCKED_TERMS = [
    "world cup", "football", "soccer", "sports", "match", "tournament",
    "weather", "sky turns", "episode", "podcast", "celebrity", "movie",
    "music", "travel", "recipe"
]

REQUIRED_TERMS = [
    "bank", "banks", "banking", "rate", "rates", "fed", "central bank",
    "inflation", "economy", "economic", "finance", "financial",
    "market", "markets", "credit", "liquidity", "investment",
    "trade", "oil", "gas", "lng", "energy", "qatar", "gcc", "gulf",
    "saudi", "uae", "kuwait", "bahrain", "oman", "geopolitical",
    "sovereign", "infrastructure"
]


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


def extract_json_array(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in Claude response. Response was: {text[:1000]}")
    return json.loads(text[start:end + 1])


def is_relevant_news(title, summary):
    combined = f"{title} {summary}".lower()
    if any(term in combined for term in BLOCKED_TERMS):
        return False
    if not any(term in combined for term in REQUIRED_TERMS):
        return False
    return True


def fetch_news(max_items=35):
    items = []
    for url in NEWS_SOURCES:
        feed = feedparser.parse(url)
        for entry in feed.entries[:15]:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            if not is_relevant_news(title, summary):
                continue
            items.append({
                "title": title,
                "summary": summary,
                "link": entry.get("link", ""),
                "source": feed.feed.get("title", "News source"),
                "published": entry.get("published", ""),
            })
    return items[:max_items]


def fallback_topics(news_items):
    return [
        {
            "topic_id": "1",
            "title": "Interest rate outlook and implications for Doha Bank margins",
            "source_title": "Global interest rate and banking market developments",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.cnbc.com/finance/",
            "source_date": TODAY,
            "why_it_matters": "Rate expectations directly affect funding cost, lending yields, treasury positioning and net interest margin.",
            "potential_doha_bank_angle": "Assess deposit repricing, loan yield sensitivity, liquidity positioning and opportunities to protect margin."
        },
        {
            "topic_id": "2",
            "title": "GCC liquidity and corporate credit demand",
            "source_title": "Regional liquidity and corporate banking conditions",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.cnbc.com/world/",
            "source_date": TODAY,
            "why_it_matters": "Liquidity conditions influence corporate borrowing appetite, deposit competition and pricing discipline across GCC banks.",
            "potential_doha_bank_angle": "Assess corporate lending opportunities, deposit mobilisation, sector exposure and relationship banking priorities."
        },
        {
            "topic_id": "3",
            "title": "Energy market shifts and Qatar-linked business flows",
            "source_title": "Energy market and LNG-related developments",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.aljazeera.com/economy/",
            "source_date": TODAY,
            "why_it_matters": "Energy market movements affect Qatar’s fiscal position, project activity, trade flows and business confidence.",
            "potential_doha_bank_angle": "Assess opportunities in project finance, trade finance, contractor banking and treasury solutions for energy-linked clients."
        }
    ]


def validate_topics(topics):
    valid = []
    for idx, t in enumerate(topics, 1):
        title = str(t.get("title", ""))
        source_title = str(t.get("source_title", ""))
        combined = f"{title} {source_title}".lower()
        if any(term in combined for term in BLOCKED_TERMS):
            continue
        if not any(term in combined for term in REQUIRED_TERMS):
            continue
        t["topic_id"] = str(idx)
        t.setdefault("source_date", TODAY)
        valid.append(t)
    return valid


def ai_select_topics(news_items, bank_name):
    if len(news_items) < 3:
        print("WARNING: Not enough relevant news items. Using strategic fallback topics.")
        return fallback_topics(news_items)

    prompt = f"""
You are DB Strategy AI Analyst.

Select exactly 3 strong weekly strategy article topics for {bank_name}.

Strict rules:
- Topics must be relevant to Doha Bank.
- Do not select sports, entertainment, weather, lifestyle, podcasts, or generic human-interest stories.
- Do not select topics unless they have clear banking, economic, GCC, Qatar, liquidity, energy, interest-rate, credit, or trade-finance relevance.
- Each topic must explain a specific opportunity or risk for Doha Bank.
- If the available news is weak, prefer macro/banking/energy interpretation rather than random stories.

Return only a valid JSON array. No markdown. No explanation.

Required structure:
[
  {{
    "topic_id": "1",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "source_date": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }}
]

Relevant news only:
{json.dumps(news_items, ensure_ascii=False)}
"""
    try:
        text = ask_claude(prompt)
        topics = validate_topics(extract_json_array(text))
        if len(topics) < 3:
            print("WARNING: Claude returned weak or irrelevant topics. Using strategic fallback topics.")
            return fallback_topics(news_items)
        return topics[:3]
    except Exception as e:
        print(f"WARNING: Claude topic generation failed. Using strategic fallback topics. Error: {e}")
        return fallback_topics(news_items)


def strip_outer_html(full_html):
    """Email-safe approximation for embedding full article previews in the approval email."""
    lower = full_html.lower()
    body_start = lower.find("<body")
    if body_start != -1:
        body_start = lower.find(">", body_start)
        body_end = lower.rfind("</body>")
        if body_start != -1 and body_end != -1:
            return full_html[body_start + 1:body_end]
    return full_html


def build_approval_email(drafts, approval_webhook_url):
    sections = ""
    for draft in drafts:
        t = draft["topic"]
        topic_id = str(t.get("topic_id", ""))
        article_html = strip_outer_html(draft["html_file_content"])
        sections += f"""
<tr>
<td style="padding:24px 24px 10px 24px; border-top:2px solid #dbe5f0; background:#ffffff;">
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; color:{BLUE}; font-weight:bold;">Full article option {html.escape(topic_id)}</p>
  <h2 style="margin:0 0 14px 0; font-family:Georgia,serif; font-size:24px; line-height:1.25; color:{NAVY};">{html.escape(t.get('title', ''))}</h2>
  <a href="{approval_webhook_url}?decision=approve&topic_id={html.escape(topic_id)}"
     style="display:inline-block; background-color:{BLUE}; color:#ffffff; text-decoration:none; padding:11px 18px; border-radius:5px; font-family:Arial,Helvetica,sans-serif; font-size:13px; font-weight:bold;">
    Approve and send this exact article
  </a>
  <p style="margin:12px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; color:{MUTED};">The article below is the same stored HTML file that final send will use.</p>
</td>
</tr>
<tr>
<td style="padding:0 0 34px 0; background:#e7ecf3;">
{article_html}
</td>
</tr>
"""

    return f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>DB Strategy Weekly Approval</title></head>
<body style="margin:0; padding:0; background-color:#eef2f6; font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#eef2f6; padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="760" cellpadding="0" cellspacing="0" style="width:760px; max-width:100%; background:#ffffff; border-radius:6px; overflow:hidden;">
<tr><td style="height:4px; background:{NAVY};">&nbsp;</td></tr>
<tr>
<td style="padding:30px 34px 22px 34px;">
  <p style="margin:0 0 12px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; letter-spacing:3px; color:{BLUE}; font-weight:bold;">APPROVAL REQUIRED</p>
  <h1 style="margin:0; font-family:Georgia,serif; font-size:30px; font-weight:normal; color:{NAVY};">DB Strategy Weekly — Full Article Approval</h1>
  <p style="margin:8px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:14px; color:{MUTED};">{TODAY} · Review the full articles below. The approved option is sent using the same stored HTML.</p>
</td>
</tr>
{sections}
<tr>
<td style="padding:18px 34px 26px 34px; border-top:1px solid #e2e8f0;">
  <p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:11px; color:{MUTED};">Generated from latest business, market, banking and macro news sources. Review before distribution.</p>
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
    parser.add_argument("--out", default="drafts.html")
    parser.add_argument("--json-out", default="drafts.json")
    parser.add_argument("--bank", default="Doha Bank Q.P.S.C.")
    parser.add_argument("--impact-rules", default="impact_rules.json")
    args = parser.parse_args()

    approval_webhook_url = os.environ["APPROVAL_WEBHOOK_URL"]

    news = fetch_news()
    topics = ai_select_topics(news, args.bank)
    metrics = weekly.get_doha_bank_metrics(args.bank)
    impact_rules = weekly.load_impact_rules(args.impact_rules)

    drafts = []
    topics_for_json = []

    for t in topics:
        topic_id = str(t.get("topic_id"))
        article = weekly.ai_write_article(t, metrics, args.bank, impact_rules)
        html_body = weekly.build_final_email(t, article)
        html_file = f"strategy_weekly_option_{topic_id}.html"

        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_body)

        record = dict(t)
        record["html_file"] = html_file
        record["article_title"] = article.get("article_title", t.get("title", ""))
        topics_for_json.append(record)

        drafts.append({
            "topic": record,
            "html_file": html_file,
            "html_file_content": html_body,
        })

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(topics_for_json, f, ensure_ascii=False, indent=2)

    approval_html = build_approval_email(drafts, approval_webhook_url)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(approval_html)

    print(f"Generated approval email: {args.out}")
    print("Generated exact final article files:", ", ".join(d["html_file"] for d in drafts))


if __name__ == "__main__":
    main()

    
