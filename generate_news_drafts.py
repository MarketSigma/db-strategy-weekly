import os
import json
import html
import argparse
import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
import feedparser
import anthropic

# Reuse the exact final-article generator so the approval draft and final send match.
try:
    import generate_weekly as weekly
except ImportError:
    import generate_weekly_fixed as weekly

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

TODAY = datetime.date.today().strftime("%d %B %Y").lstrip("0")
NAVY = "#002b5c"
BLUE = "#0072ce"
SLATE = "#2c3e54"
MUTED = "#8a99ad"

NEWS_SOURCES = [
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://www.cnbc.com/id/10072762/device/rss/rss.html",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.imf.org/en/News/RSS",
    "https://www.worldbank.org/en/news/all?format=rss",
    "https://www.bis.org/rss/press_releases.xml",
    "https://www.bis.org/rss/speeches.xml",
    "https://www.fitchratings.com/site/pr/rss",
    "https://www.spglobal.com/ratings/en/rss",
    "https://www.investing.com/rss/news_25.rss",
    "https://www.investing.com/rss/news_301.rss",
    "https://www.investing.com/rss/news_285.rss",
    "https://www.gulf-times.com/rss",
    "https://thepeninsulaqatar.com/rss",
    "https://www.arabnews.com/rss.xml",
]

BLOCKED_TERMS = [
    "world cup", "football", "soccer", "sports", "match", "tournament",
    "weather", "sky turns", "episode", "podcast", "celebrity", "movie",
    "music", "travel", "recipe", "cricket", "tennis", "golf"
]

REQUIRED_TERMS = [
    "bank", "banks", "banking", "rate", "rates", "fed", "central bank",
    "inflation", "economy", "economic", "finance", "financial",
    "market", "markets", "credit", "liquidity", "investment",
    "trade", "oil", "gas", "lng", "energy", "qatar", "gcc", "gulf",
    "saudi", "uae", "kuwait", "bahrain", "oman", "geopolitical",
    "sovereign", "infrastructure", "regulation", "regulatory", "debt",
    "bond", "bonds", "loan", "loans", "growth", "risk", "risks",
    "sanctions", "shipping", "red sea", "supply chain", "real estate",
    "project finance", "treasury", "digital banking", "fintech",
    "corporate banking", "wholesale banking", "payments", "cash management"
]

CATEGORY_RULES = [
    {
        "topic_id": "1",
        "category": "Geopolitical / Regional Risk",
        "description": "Regional conflict, sanctions, shipping disruption, Gulf security, sovereign risk, trade corridor risk or regional stability."
    },
    {
        "topic_id": "2",
        "category": "Banking / Financial Sector Development",
        "description": "Banking sector, liquidity, credit, deposits, fintech, digital banking, capital, ratings, asset quality or financial-sector competition."
    },
    {
        "topic_id": "3",
        "category": "Economic / Market / Regulatory Development",
        "description": "Rates, inflation, GDP, oil and gas, fiscal policy, regulation, markets, investment flows or macro policy."
    },
    {
        "topic_id": "4",
        "category": "Qatar / GCC Business Opportunity",
        "description": "Qatar or GCC business growth, infrastructure, government spending, corporate expansion, real estate, LNG, tourism or investment opportunities."
    },
    {
        "topic_id": "5",
        "category": "Technology / Digital Banking / Fintech",
        "description": "Digital banking, fintech, payments, AI, cybersecurity, open banking, customer experience or banking technology trends."
    },
    {
        "topic_id": "6",
        "category": "Corporate / Wholesale Banking Opportunity",
        "description": "Corporate banking, wholesale banking, trade finance, cash management, project finance, treasury solutions or sector-specific client opportunities."
    },
]


def clean_text(value):
    value = str(value or "")
    value = html.unescape(value)
    value = value.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return " ".join(value.split()).strip()


def format_source_date(raw):
    raw = str(raw or "").strip()
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%d %B %Y").lstrip("0")
    except Exception:
        return raw


def source_name_from_url(url):
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        return domain or "News source"
    except Exception:
        return "News source"


def ask_claude(prompt, max_tokens=12000):
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

    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No complete JSON array found in Claude response. Response was: {text[:1000]}")

    return json.loads(text[start:end + 1])


def is_relevant_news(title, summary):
    combined = f"{title} {summary}".lower()

    if any(term in combined for term in BLOCKED_TERMS):
        return False

    if not any(term in combined for term in REQUIRED_TERMS):
        return False

    return True


def dedupe_key(link, title):
    link = str(link or "").strip().lower()

    if link:
        return link.split("?")[0].rstrip("/")

    return clean_text(title).lower()


def fetch_news(max_items=100):
    items = []
    seen = set()

    for url in NEWS_SOURCES:
        try:
            feed = feedparser.parse(url)
            feed_source_name = clean_text(feed.feed.get("title", "")) or source_name_from_url(url)

            for entry in feed.entries[:30]:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))
                link = entry.get("link", "")

                if not title or not link:
                    continue

                key = dedupe_key(link, title)
                if key in seen:
                    continue

                seen.add(key)

                if not is_relevant_news(title, summary):
                    continue

                published_raw = entry.get("published", "") or entry.get("updated", "")

                items.append({
                    "title": title,
                    "summary": summary[:1200],
                    "link": link,
                    "source": feed_source_name,
                    "source_date": format_source_date(published_raw),
                })

        except Exception as e:
            print(f"WARNING: Failed RSS source: {url}. Error: {e}")
            continue

    return items[:max_items]


def article_excerpt(value, max_chars=420):
    text = clean_text(value)

    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    excerpt = text[:max_chars]
    last_period = excerpt.rfind(".")

    if last_period > 180:
        return excerpt[:last_period + 1]

    return excerpt.rstrip() + "..."


def fallback_topics(news_items):
    return [
        {
            "topic_id": "1",
            "category": "Geopolitical / Regional Risk",
            "title": "Regional risk and trade corridor disruption implications for Doha Bank",
            "source_title": "Regional geopolitical and trade corridor developments",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.aljazeera.com/economy/",
            "source_date": TODAY,
            "source_excerpt": "Regional geopolitical developments can affect trade flows, client activity and risk sentiment across Gulf markets.",
            "why_it_matters": "Regional risk can affect corporate confidence, trade finance flows, treasury positioning and risk appetite across Qatar and the wider GCC.",
            "potential_doha_bank_angle": "Assess exposed corporate sectors, trade finance demand, cash management needs, liquidity buffers and client advisory opportunities."
        },
        {
            "topic_id": "2",
            "category": "Banking / Financial Sector Development",
            "title": "GCC banking liquidity and corporate credit demand",
            "source_title": "Regional liquidity and corporate banking conditions",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.cnbc.com/finance/",
            "source_date": TODAY,
            "source_excerpt": "Liquidity conditions influence corporate borrowing appetite, deposit competition and pricing discipline across GCC banks.",
            "why_it_matters": "Liquidity conditions influence corporate borrowing appetite, deposit competition and pricing discipline across GCC banks.",
            "potential_doha_bank_angle": "Assess corporate lending opportunities, deposit mobilisation, sector exposure and relationship banking priorities."
        },
        {
            "topic_id": "3",
            "category": "Economic / Market / Regulatory Development",
            "title": "Interest rate outlook and implications for Doha Bank margins",
            "source_title": "Global interest rate and banking market developments",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.cnbc.com/markets/",
            "source_date": TODAY,
            "source_excerpt": "Rate expectations directly affect funding cost, lending yields, treasury positioning and net interest margin.",
            "why_it_matters": "Rate expectations directly affect funding cost, lending yields, treasury positioning and net interest margin.",
            "potential_doha_bank_angle": "Assess deposit repricing, loan yield sensitivity, liquidity positioning and opportunities to protect margin."
        },
        {
            "topic_id": "4",
            "category": "Qatar / GCC Business Opportunity",
            "title": "Qatar and GCC investment activity as a business opportunity for Doha Bank",
            "source_title": "Qatar and GCC investment activity",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://thepeninsulaqatar.com/",
            "source_date": TODAY,
            "source_excerpt": "Qatar and GCC investment activity can create new lending, advisory and transaction banking opportunities.",
            "why_it_matters": "Investment activity supports corporate expansion, project finance, cash management and deposit opportunities.",
            "potential_doha_bank_angle": "Identify sectors with rising funding needs and strengthen targeted corporate coverage."
        },
        {
            "topic_id": "5",
            "category": "Technology / Digital Banking / Fintech",
            "title": "Digital banking and fintech trends shaping customer expectations",
            "source_title": "Digital banking and fintech developments",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.bis.org/",
            "source_date": TODAY,
            "source_excerpt": "Digital banking and fintech developments are reshaping customer expectations and competitive positioning.",
            "why_it_matters": "Digital capability affects customer retention, fee income, cost efficiency and competitive differentiation.",
            "potential_doha_bank_angle": "Assess digital product gaps, payment opportunities, customer migration and efficiency initiatives."
        },
        {
            "topic_id": "6",
            "category": "Corporate / Wholesale Banking Opportunity",
            "title": "Corporate banking opportunities from trade finance and cash management demand",
            "source_title": "Corporate banking and transaction banking developments",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.cnbc.com/finance/",
            "source_date": TODAY,
            "source_excerpt": "Corporate banking demand can create opportunities in trade finance, treasury and cash management services.",
            "why_it_matters": "Wholesale banking opportunities support fee income, deposit mobilisation and relationship-led growth.",
            "potential_doha_bank_angle": "Prioritise corporate clients with rising trade, liquidity and treasury management needs."
        }
    ]


def validate_topics(topics):
    valid = []
    expected_categories = [rule["category"] for rule in CATEGORY_RULES]

    for idx, t in enumerate(topics, 1):
        title = str(t.get("title", ""))
        source_title = str(t.get("source_title", ""))
        source_excerpt = str(t.get("source_excerpt", ""))
        combined = f"{title} {source_title} {source_excerpt}".lower()

        if any(term in combined for term in BLOCKED_TERMS):
            continue

        if not any(term in combined for term in REQUIRED_TERMS):
            continue

        if idx <= len(CATEGORY_RULES):
            t["topic_id"] = str(idx)
            t["category"] = expected_categories[idx - 1]
        else:
            t["topic_id"] = str(idx)
            t.setdefault("category", "Economic / Market / Regulatory Development")

        t.setdefault("source_date", TODAY)

        if not t.get("source_excerpt"):
            t["source_excerpt"] = article_excerpt(t.get("source_title", "") or t.get("title", ""))

        t["source_excerpt"] = article_excerpt(t.get("source_excerpt", ""))

        valid.append(t)

    if len(valid) >= 6:
        for idx, rule in enumerate(CATEGORY_RULES):
            valid[idx]["topic_id"] = rule["topic_id"]
            valid[idx]["category"] = rule["category"]
            valid[idx]["source_excerpt"] = article_excerpt(
                valid[idx].get("source_excerpt")
                or valid[idx].get("source_title")
                or valid[idx].get("title")
            )

    return valid


def ai_select_topics(news_items, bank_name):
    if len(news_items) < 6:
        print("WARNING: Not enough relevant news items. Using strategic fallback topics.")
        return fallback_topics(news_items)

    prompt = f"""
You are DB Strategy AI Analyst.

Select exactly 6 strong weekly strategy article topics for {bank_name}.

Mandatory topic variety:
- Topic 1 must be category: Geopolitical / Regional Risk.
- Topic 2 must be category: Banking / Financial Sector Development.
- Topic 3 must be category: Economic / Market / Regulatory Development.
- Topic 4 must be category: Qatar / GCC Business Opportunity.
- Topic 5 must be category: Technology / Digital Banking / Fintech.
- Topic 6 must be category: Corporate / Wholesale Banking Opportunity.

Category definitions:
{json.dumps(CATEGORY_RULES, ensure_ascii=False, indent=2)}

Strict rules:
- Topics must be relevant to Doha Bank.
- Select exactly one topic from each category.
- Do not select substantially similar topics.
- Do not select all topics from the same geography, same sector or same driver of impact.
- Avoid repeating common weekly themes such as interest rates, LNG, oil prices or GCC banking liquidity unless there is a clearly new development.
- Prefer Qatar and GCC topics first, especially topics linked to Qatar banking, liquidity, credit demand, government spending, infrastructure, real estate, LNG, energy, trade, regulation, or GCC corporate activity.
- A global topic may be selected only if it has a materially stronger and clearly explainable impact on Doha Bank than available Qatar/GCC topics.
- At least 3 of the 6 selected topics should be Qatar/GCC-focused when suitable Qatar/GCC news is available.
- Do not select sports, entertainment, weather, lifestyle, podcasts, or generic human-interest stories.
- Do not select topics unless they have clear banking, economic, GCC, Qatar, liquidity, energy, interest-rate, credit, regulatory, geopolitical, technology or trade-finance relevance.
- Each topic must explain a specific opportunity or risk for Doha Bank.
- Preserve source_date from the selected news item exactly.
- Preserve source_name and source_url from the selected news item.
- source_excerpt must be taken from the selected news item's summary where available.
- source_excerpt should be 2 to 3 sentences where possible, up to 420 characters.
- Do not invent source_excerpt.
- If summary is too short, use the article title as support.
- If available news is weak, still maintain the six mandatory categories and choose the strongest strategic interpretation.

Return only a valid JSON array. No markdown. No explanation.
The response must start with [ and end with ].

Required structure:
[
  {{
    "topic_id": "1",
    "category": "Geopolitical / Regional Risk",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "source_date": "...",
    "source_excerpt": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }},
  {{
    "topic_id": "2",
    "category": "Banking / Financial Sector Development",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "source_date": "...",
    "source_excerpt": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }},
  {{
    "topic_id": "3",
    "category": "Economic / Market / Regulatory Development",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "source_date": "...",
    "source_excerpt": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }},
  {{
    "topic_id": "4",
    "category": "Qatar / GCC Business Opportunity",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "source_date": "...",
    "source_excerpt": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }},
  {{
    "topic_id": "5",
    "category": "Technology / Digital Banking / Fintech",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "source_date": "...",
    "source_excerpt": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }},
  {{
    "topic_id": "6",
    "category": "Corporate / Wholesale Banking Opportunity",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "source_date": "...",
    "source_excerpt": "...",
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

        if len(topics) < 6:
            print(f"WARNING: Claude returned only {len(topics)} valid topics. Filling remaining topics with fallback.")
            fallback = fallback_topics(news_items)

            existing_ids = {str(t.get("topic_id")) for t in topics}
            for fb in fallback:
                if str(fb.get("topic_id")) not in existing_ids:
                    topics.append(fb)
                if len(topics) >= 6:
                    break

        return topics[:6]

    except Exception as e:
        print(f"WARNING: Claude topic generation failed. Using strategic fallback topics. Error: {e}")
        return fallback_topics(news_items)


def strip_outer_html(full_html):
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
        category = str(t.get("category", ""))
        article_html = strip_outer_html(draft["html_file_content"])

        sections += f"""
<tr>
<td style="padding:24px 24px 10px 24px; border-top:2px solid #dbe5f0; background:#ffffff;">
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; color:{BLUE}; font-weight:bold;">Full article option {html.escape(topic_id)}</p>
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; color:{SLATE}; font-weight:bold;">{html.escape(category)}</p>
  <h2 style="margin:0 0 14px 0; font-family:Georgia,serif; font-size:24px; line-height:1.25; color:{NAVY};">{html.escape(t.get('title', ''))}</h2>
  <p style="margin:0 0 12px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Source excerpt:</strong> {html.escape(t.get('source_excerpt', ''))}
  </p>
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
  <p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:11px; color:{MUTED};">Generated from latest business, market, banking, regional-risk and macro news sources. Review before distribution.</p>
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
    print(f"Fetched {len(news)} relevant news items from expanded source list.")

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
