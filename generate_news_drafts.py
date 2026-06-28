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
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
]

def ask_claude(prompt, max_tokens=4000):
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "sonnet-4-6"),
        max_tokens=max_tokens,
        temperature=0.25,
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

def fetch_news(max_items=35):
    items = []

    for url in NEWS_SOURCES:
        feed = feedparser.parse(url)

        for entry in feed.entries[:10]:
            items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "link": entry.get("link", ""),
                "source": feed.feed.get("title", "News source"),
            })

    return items[:max_items]

def fallback_topics(news_items):
    selected = news_items[:3]

    topics = []
    for i, item in enumerate(selected, 1):
        topics.append({
            "topic_id": str(i),
            "title": item.get("title", f"Strategy topic {i}"),
            "source_title": item.get("title", ""),
            "source_name": item.get("source", "News source"),
            "source_url": item.get("link", ""),
            "why_it_matters": "This development may have strategic implications for regional markets, banking activity, liquidity, client demand, or risk appetite.",
            "potential_doha_bank_angle": "Assess the potential impact on Doha Bank through corporate banking opportunities, funding conditions, asset quality, liquidity, or client sector exposure."
        })

    return topics

def ai_select_topics(news_items, bank_name):
    prompt = f"""
You are DB Strategy AI Analyst.

Select exactly 3 strong weekly strategy article topics for {bank_name}.

The topic must be based on the news provided and should relate to:
- Qatar
- GCC
- banking
- interest rates
- liquidity
- credit growth
- trade finance
- corporate banking
- energy / LNG
- geopolitics
- sovereign and infrastructure spending

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

News:
{json.dumps(news_items, ensure_ascii=False)}
"""

    try:
        text = ask_claude(prompt)
        topics = extract_json_array(text)

        if not isinstance(topics, list) or len(topics) < 1:
            return fallback_topics(news_items)

        return topics[:3]

    except Exception as e:
        print(f"WARNING: Claude topic generation failed. Using fallback topics. Error: {e}")
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
    Generated from latest news sources. Review before external distribution.
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
