import os
import json
import html
import argparse
import datetime
import feedparser
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

TODAY = datetime.date.today().strftime("%d %B %Y")

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

def ask_claude(prompt, max_tokens=4000):
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()

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
            "why_it_matters": "Rate expectations directly affect funding cost, lending yields, treasury positioning and net interest margin.",
            "potential_doha_bank_angle": "Assess deposit repricing, loan yield sensitivity, liquidity positioning and opportunities to protect margin."
        },
        {
            "topic_id": "2",
            "title": "GCC liquidity and corporate credit demand",
            "source_title": "Regional liquidity and corporate banking conditions",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.cnbc.com/world/",
            "why_it_matters": "Liquidity conditions influence corporate borrowing appetite, deposit competition and pricing discipline across GCC banks.",
            "potential_doha_bank_angle": "Assess corporate lending opportunities, deposit mobilisation, sector exposure and relationship banking priorities."
        },
        {
            "topic_id": "3",
            "title": "Energy market shifts and Qatar-linked business flows",
            "source_title": "Energy market and LNG-related developments",
            "source_name": "Fallback Strategy Topic",
            "source_url": "https://www.aljazeera.com/economy/",
            "why_it_matters": "Energy market movements affect Qatar’s fiscal position, project activity, trade flows and business confidence.",
            "potential_doha_bank_angle": "Assess opportunities in project finance, trade finance, contractor banking and treasury solutions for energy-linked clients."
        }
    ]

def validate_topics(topics):
    valid = []

    for t in topics:
        title = str(t.get("title", ""))
        source_title = str(t.get("source_title", ""))
        combined = f"{title} {source_title}".lower()

        if any(term in combined for term in BLOCKED_TERMS):
            continue

        if not any(term in combined for term in REQUIRED_TERMS):
            continue

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

Return only a valid JSON array.
Do not include markdown.
Do not include explanation.
Do not include intro text.
Do not include code fences.

Required structure:
[
  {{
    "topic_id": "1",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }},
  {{
    "topic_id": "2",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }},
  {{
    "topic_id": "3",
    "title": "...",
    "source_title": "...",
    "source_name": "...",
    "source_url": "...",
    "why_it_matters": "...",
    "potential_doha_bank_angle": "..."
  }}
]

Relevant news only:
{json.dumps(news_items, ensure_ascii=False)}
"""

    try:
        text = ask_claude(prompt)
        topics = extract_json_array(text)
        topics = validate_topics(topics)

        if len(topics) < 3:
            print("WARNING: Claude returned weak or irrelevant topics. Using strategic fallback topics.")
            return fallback_topics(news_items)

        return topics[:3]

    except Exception as e:
        print(f"WARNING: Claude topic generation failed. Using strategic fallback topics. Error: {e}")
        return fallback_topics(news_items)

def build_approval_email(topics, approval_webhook_url):
    cards = ""

    for t in topics:
        topic_id = str(t.get("topic_id", ""))
        title = t.get("title", "")
        source_title = t.get("source_title", "")
        why_it_matters = t.get("why_it_matters", "")
        angle = t.get("potential_doha_bank_angle", "")

        cards += f"""
<tr>
<td style="padding:26px 40px; border-top:1px solid #e2e8f0;">
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; color:#0072ce; font-weight:bold;">Topic {html.escape(topic_id)}</p>

  <h2 style="margin:0 0 10px 0; font-family:Georgia,serif; font-size:24px; line-height:1.3; font-weight:normal; color:#002b5c;">
    {html.escape(title)}
  </h2>

  <p style="margin:0 0 10px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; color:#7a8aa0;">
    Source: {html.escape(source_title)}
  </p>

  <p style="margin:0 0 14px 0; font-size:16px; line-height:1.6; color:#2c3e54;">
    <strong style="color:#002b5c;">Why it matters:</strong> {html.escape(why_it_matters)}
  </p>

  <p style="margin:0 0 18px 0; font-size:16px; line-height:1.6; color:#2c3e54;">
    <strong style="color:#002b5c;">Doha Bank angle:</strong> {html.escape(angle)}
  </p>

  <a href="{approval_webhook_url}?decision=approve&topic_id={html.escape(topic_id)}"
     style="display:inline-block; background-color:#0072ce; color:#ffffff; text-decoration:none; padding:10px 18px; border-radius:5px; font-family:Arial,Helvetica,sans-serif; font-size:13px; font-weight:bold;">
    Approve Topic {html.escape(topic_id)}
  </a>
</td>
</tr>
"""

    return f"""
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background-color:#eef2f6; font-family:Georgia,'Times New Roman',serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#eef2f6; padding:28px 12px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="width:600px; max-width:600px; background:#ffffff; border-radius:6px; overflow:hidden;">
<tr><td style="height:4px; background:#002b5c;">&nbsp;</td></tr>

<tr>
<td style="padding:30px 40px 22px 40px;">
  <p style="margin:0 0 12px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; letter-spacing:3px; color:#0072ce; font-weight:bold;">APPROVAL REQUIRED</p>
  <h1 style="margin:0; font-family:Georgia,serif; font-size:30px; font-weight:normal; color:#002b5c;">DB Strategy Weekly — Topic Selection</h1>
  <p style="margin:8px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:14px; color:#7a8aa0;">{TODAY} · Select one topic. The full article will then be generated in the Strategy Weekly style.</p>
</td>
</tr>

{cards}

<tr>
<td style="padding:18px 40px 26px 40px; border-top:1px solid #e2e8f0;">
  <p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:11px; color:#8a99ad;">
    Generated from latest business, market, banking and macro news sources. Review before external distribution.
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
    parser.add_argument("--out", default="drafts.html")
    parser.add_argument("--json-out", default="drafts.json")
    parser.add_argument("--bank", default="Doha Bank")
    args = parser.parse_args()

    approval_webhook_url = os.environ["APPROVAL_WEBHOOK_URL"]

    news = fetch_news()
    topics = ai_select_topics(news, args.bank)

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)

    html_body = build_approval_email(topics, approval_webhook_url)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_body)

if __name__ == "__main__":
    main()
