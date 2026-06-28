import os, json, datetime, textwrap, html
import feedparser
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

TODAY = datetime.date.today().strftime("%d %B %Y")

NEWS_SOURCES = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/worldNews",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
]

def fetch_news(max_items=25):
    items = []
    for url in NEWS_SOURCES:
        feed = feedparser.parse(url)
        for entry in feed.entries[:8]:
            items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "link": entry.get("link", ""),
                "source": feed.feed.get("title", "News source")
            })
    return items[:max_items]

def ai_select_topics(news_items):
    prompt = f"""
You are DB Strategy AI Analyst.

From the news list below, select 3 weekly article topics that may create opportunity or risk for Doha Bank.

Focus on:
- Qatar economy
- GCC banking
- interest rates
- liquidity
- corporate banking
- trade finance
- energy / LNG
- sovereign / infrastructure activity
- geopolitics affecting business flows

Return JSON only in this structure:
[
  {{
    "topic_id": "1",
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

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.35
    )

    return json.loads(response.choices[0].message.content)

def build_approval_email(topics):
    cards = ""

    for t in topics:
        cards += f"""
        <tr>
          <td style="padding:26px 40px; border-top:1px solid #e2e8f0;">
            <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; color:#0072ce; font-weight:bold;">Topic {html.escape(t['topic_id'])}</p>

            <h2 style="margin:0 0 10px 0; font-family:Georgia,serif; font-size:24px; line-height:1.3; font-weight:normal; color:#002b5c;">
              {html.escape(t['title'])}
            </h2>

            <p style="margin:0 0 10px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; color:#7a8aa0;">
              Source: {html.escape(t['source_title'])}
            </p>

            <p style="margin:0 0 14px 0; font-size:16px; line-height:1.6; color:#2c3e54;">
              <strong style="color:#002b5c;">Why it matters:</strong> {html.escape(t['why_it_matters'])}
            </p>

            <p style="margin:0 0 18px 0; font-size:16px; line-height:1.6; color:#2c3e54;">
              <strong style="color:#002b5c;">Doha Bank angle:</strong> {html.escape(t['potential_doha_bank_angle'])}
            </p>

            <a href="{os.environ['APPROVAL_WEBHOOK_URL']}?decision=approve&topic_id={html.escape(t['topic_id'])}"
               style="display:inline-block; background-color:#0072ce; color:#ffffff; text-decoration:none; padding:10px 18px; border-radius:5px; font-family:Arial,Helvetica,sans-serif; font-size:13px; font-weight:bold;">
              Approve Topic {html.escape(t['topic_id'])}
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
  <p style="margin:8px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:14px; color:#7a8aa0;">{TODAY} · Select one topic. The full article will then be generated in the Fed-rate style.</p>
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
    news = fetch_news()
    topics = ai_select_topics(news)

    with open("draft_topics.json", "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)

    html_body = build_approval_email(topics)

    with open("weekly_draft_approval.html", "w", encoding="utf-8") as f:
        f.write(html_body)

if __name__ == "__main__":
    main()
