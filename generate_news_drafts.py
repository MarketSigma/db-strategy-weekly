#!/usr/bin/env python3

import os
import re
import json
import html
import argparse
import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, quote

import feedparser
import anthropic

# Reuse the final article generator so the approval draft and final send
# use the same Doha Bank financial-analysis logic and same HTML template.
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


# ---------------------------------------------------------------------
# 1. NEWS DISCOVERY CONFIGURATION
# ---------------------------------------------------------------------

# These searches are intentionally simple.
# Google News RSS handles simple queries more reliably than complex Boolean strings.
STRATEGIC_SEARCHES = [
    # Qatar market / client / deal opportunities — FIRST
    ("Qatar", 'QatarEnergy project contract investment'),
    ("Qatar", 'Qatar infrastructure project contract financing'),
    ("Qatar", 'Qatar company expansion investment financing'),
    ("Qatar", 'Qatar new corporate investment project'),
    ("Qatar", 'Qatar Financial Centre new firms expansion'),
    ("Qatar", 'Qatar data center investment project'),
    ("Qatar", 'Qatar logistics investment project'),
    ("Qatar", 'Qatar manufacturing investment project'),
    ("Qatar", 'Qatar healthcare investment project'),
    ("Qatar", 'Qatar SME financing fintech'),
    ("Qatar", 'Qatar private sector financing growth'),
    ("Qatar", 'Qatar payments transaction volumes QCB'),
    ("Qatar", 'Qatar digital payments banking'),
    ("Qatar", 'Qatar wealth asset management expansion'),
    ("Qatar", 'Qatar capital markets sukuk bond issuance'),

    # Qatar banking solutions / white-space
    ("Qatar", 'Qatar transaction banking cash management'),
    ("Qatar", 'Qatar supply chain finance'),
    ("Qatar", 'Qatar B2B payments'),
    ("Qatar", 'Qatar embedded finance'),
    ("Qatar", 'Qatar open banking launch'),
    ("Qatar", 'Qatar fintech banking partnership'),
    ("Qatar", 'Qatar digital banking AI'),

    # Competitors — important, but capped later so they do not dominate
    ("Qatar", '"Dukhan Bank" payments fintech blockchain Kinexys partnership launch'),
    ("Qatar", '"QNB" Qatar payments fintech partnership digital treasury launch'),
    ("Qatar", '"Qatar Islamic Bank" payments fintech digital partnership launch'),
    ("Qatar", '"Commercial Bank Qatar" payments fintech partnership launch'),
    ("Qatar", '"Masraf Al Rayan" payments fintech digital partnership launch'),
    ("Qatar", '"QIIB" Qatar payments fintech digital partnership launch'),
    ("Qatar", '"Ahlibank Qatar" payments fintech digital partnership launch'),

    # GCC developments with plausible Doha Bank relevance
    ("GCC", 'GCC bank fintech partnership'),
    ("GCC", 'GCC bank payments launch'),
    ("GCC", 'Saudi bank transaction banking launch'),
    ("GCC", 'UAE bank transaction banking launch'),
    ("GCC", 'GCC supply chain finance platform'),
    ("GCC", 'GCC corporate financing project bank'),
]

# Optional standard RSS sources.
DEFAULT_RSS_SOURCES = [
    {"name": "Gulf Times", "rss": "https://www.gulf-times.com/rss", "region": "Qatar"},
    {"name": "The Peninsula Qatar", "rss": "https://thepeninsulaqatar.com/rss", "region": "Qatar"},
    {"name": "Arab News", "rss": "https://www.arabnews.com/rss.xml", "region": "GCC"},
    {"name": "Al Jazeera", "rss": "https://www.aljazeera.com/xml/rss/all.xml", "region": "Regional"},
    {"name": "CNBC Finance", "rss": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "region": "Global"},
    {"name": "BBC Business", "rss": "https://feeds.bbci.co.uk/news/business/rss.xml", "region": "Global"},
    {"name": "BIS Press Releases", "rss": "https://www.bis.org/rss/press_releases.xml", "region": "Global"},
]

COMPETITOR_NAMES = [
    "dukhan bank",
    "qnb",
    "qatar national bank",
    "qatar islamic bank",
    "qib",
    "commercial bank qatar",
    "commercial bank",
    "masraf al rayan",
    "qiib",
    "qatar international islamic bank",
    "ahlibank qatar",
]

QATAR_TERMS = [
    "qatar",
    "doha",
    "qcb",
    "qatar central bank",
    "qatarenergy",
    "qatar energy",
    "qatar investment authority",
    "qia",
    "qatar stock exchange",
    "qfc",
    "qatar financial centre",
    "qatar financial center",
    "lusail",
    "ras laffan",
]

GCC_TERMS = [
    "gcc",
    "gulf cooperation council",
    "saudi",
    "saudi arabia",
    "riyadh",
    "uae",
    "united arab emirates",
    "dubai",
    "abu dhabi",
    "kuwait",
    "bahrain",
    "oman",
    "muscat",
]

BANKING_TERMS = [
    "bank",
    "banking",
    "payments",
    "payment",
    "transaction banking",
    "cash management",
    "treasury",
    "trade finance",
    "project finance",
    "corporate banking",
    "wholesale banking",
    "deposit",
    "deposits",
    "loan",
    "loans",
    "credit",
    "liquidity",
    "financing",
    "funding",
    "fintech",
    "open banking",
    "digital banking",
    "blockchain",
    "kinexys",
    "wallet",
    "merchant",
    "acquiring",
    "wealth",
    "asset management",
    "sukuk",
    "bond",
    "capital markets",
    "remittance",
    "cross-border",
    "api",
    "artificial intelligence",
    " ai ",
]

COMMERCIAL_TERMS = [
    "partnership",
    "agreement",
    "launch",
    "launches",
    "launched",
    "go live",
    "goes live",
    "expansion",
    "investment",
    "project",
    "contract",
    "awarded",
    "acquisition",
    "joint venture",
    "financing",
    "facility",
    "platform",
    "solution",
    "market entry",
    "new entrant",
    "first bank",
    "first islamic bank",
    "qatar's first",
    "qatar’s first",
    "new product",
    "new service",
]

LOW_VALUE_TERMS = [
    "award",
    "awards",
    "sponsorship",
    "csr",
    "charity",
    "community",
    "campaign",
    "promotion",
    "prize",
    "football",
    "soccer",
    "sports",
    "celebrity",
    "weather",
    "recipe",
    "podcast",
]


# ---------------------------------------------------------------------
# 2. BASIC HELPERS
# ---------------------------------------------------------------------

def clean_text(value):
    value = str(value or "")
    value = html.unescape(value)
    value = value.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return " ".join(value.split()).strip()


def format_source_date(raw):
    raw = clean_text(raw)
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


def dedupe_key(link, title):
    link = clean_text(link).lower()
    if link:
        return link.split("?")[0].rstrip("/")
    return clean_text(title).lower()


def clean_google_news_title(title, source_name=""):
    title = clean_text(title)
    source_name = clean_text(source_name)

    if source_name:
        for separator in (" - ", " – ", " — "):
            suffix = separator + source_name
            if title.lower().endswith(suffix.lower()):
                title = title[: -len(suffix)].strip()

    return title


def article_excerpt(value, max_chars=420):
    text = clean_text(value)

    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    excerpt = text[:max_chars]
    last_period = excerpt.rfind(".")

    if last_period > 180:
        return excerpt[: last_period + 1]

    return excerpt.rstrip() + "..."


def load_rss_sources(path="news_sources.json"):
    if not os.path.exists(path):
        return DEFAULT_RSS_SOURCES

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and isinstance(data.get("sources"), list):
            result = []
            for item in data["sources"]:
                if not isinstance(item, dict) or not item.get("rss"):
                    continue
                result.append({
                    "name": clean_text(item.get("name")) or source_name_from_url(item["rss"]),
                    "rss": clean_text(item["rss"]),
                    "region": clean_text(item.get("region", "Global")).title(),
                })
            return result or DEFAULT_RSS_SOURCES

        # Legacy grouped structure.
        if isinstance(data, dict):
            result = []
            for group_name, items in data.items():
                if not isinstance(items, list):
                    continue

                if group_name.lower() == "regional":
                    region = "Qatar"
                elif group_name.lower() == "global":
                    region = "Global"
                else:
                    region = group_name.title()

                for item in items:
                    if isinstance(item, dict) and item.get("rss"):
                        result.append({
                            "name": clean_text(item.get("name")) or source_name_from_url(item["rss"]),
                            "rss": clean_text(item["rss"]),
                            "region": region,
                        })

            return result or DEFAULT_RSS_SOURCES

    except Exception as e:
        print(f"WARNING: Could not read {path}; using built-in RSS sources. Error: {e}")

    return DEFAULT_RSS_SOURCES


# ---------------------------------------------------------------------
# 3. RELEVANCE / SCORING
# ---------------------------------------------------------------------

def classify_geography(title, summary, hinted_region=""):
    combined = f"{title} {summary}".lower()

    if any(term in combined for term in QATAR_TERMS):
        return "Qatar"

    if any(term in combined for term in GCC_TERMS):
        return "GCC"

    hinted = clean_text(hinted_region).lower()
    if hinted == "qatar":
        return "Qatar"
    if hinted in ("gcc", "regional"):
        return "GCC"

    return "Global"


def relevance_score(item):
    """
    Geography alone is not enough.

    A strong item needs:
    - Qatar/GCC relevance
    - banking/commercial relevance
    - preferably a named competitor, product, project, deal or client pool
    """
    title = clean_text(item.get("title", ""))
    summary = clean_text(item.get("summary", ""))
    combined = f"{title} {summary}".lower()

    if any(term in combined for term in LOW_VALUE_TERMS):
        return -100

    geography = item.get("geography") or classify_geography(
        title,
        summary,
        item.get("hinted_region", ""),
    )

    score = 0

    # Geography
    if geography == "Qatar":
        score += 55
    elif geography == "GCC":
        score += 30
    else:
        score -= 20

    # Banking relevance
    banking_hits = sum(1 for term in BANKING_TERMS if term in combined)
    commercial_hits = sum(1 for term in COMMERCIAL_TERMS if term in combined)
    competitor_hits = sum(1 for term in COMPETITOR_NAMES if term in combined)

    score += min(banking_hits, 5) * 10
    score += min(commercial_hits, 4) * 8
    score += min(competitor_hits, 2) * 20

    # Strong strategic capabilities
    for strong_term in [
        "kinexys",
        "blockchain",
        "open banking",
        "transaction banking",
        "cash management",
        "cross-border",
        "supply chain finance",
        "embedded finance",
        "project finance",
        "merchant acquiring",
    ]:
        if strong_term in combined:
            score += 15

    return score


def infer_theme(item):
    combined = f"{clean_text(item.get('title', ''))} {clean_text(item.get('summary', ''))}".lower()

    if any(name in combined for name in COMPETITOR_NAMES):
        return "competitor"
    if any(x in combined for x in [
        "project", "contract", "awarded", "investment", "expansion",
        "financing", "facility", "joint venture", "infrastructure"
    ]):
        return "deal_market"
    if any(x in combined for x in [
        "payments", "open banking", "embedded finance", "supply chain finance",
        "transaction banking", "cash management", "fintech", "digital banking",
        "blockchain", " ai "
    ]):
        return "solution"
    if any(x in combined for x in [
        "qfc", "new firms", "new companies", "sme", "manufacturing",
        "logistics", "healthcare", "data center", "wealth",
        "capital markets", "sukuk", "bond issuance"
    ]):
        return "market"
    return "other"


def is_relevant(item):
    return relevance_score(item) >= 55


# ---------------------------------------------------------------------
# 4. GOOGLE NEWS RSS SEARCH
# ---------------------------------------------------------------------

def google_news_rss_url(query, days=10):
    q = f"{query} when:{days}d"
    return (
        "https://news.google.com/rss/search?q="
        + quote(q)
        + "&hl=en&gl=QA&ceid=QA:en"
    )


def fetch_google_news():
    items = []
    seen = set()

    for hinted_region, query in STRATEGIC_SEARCHES:
        url = google_news_rss_url(query, days=10)

        try:
            feed = feedparser.parse(url)

            if getattr(feed, "bozo", False):
                print(
                    f"WARNING: Google News feed issue for '{query}': "
                    f"{getattr(feed, 'bozo_exception', '')}"
                )

            for entry in feed.entries[:15]:
                raw_title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))
                link = clean_text(entry.get("link", ""))

                if not raw_title or not link:
                    continue

                source_name = "Google News"
                source_obj = entry.get("source")
                if isinstance(source_obj, dict):
                    source_name = clean_text(source_obj.get("title", "")) or source_name

                title = clean_google_news_title(raw_title, source_name)
                key = dedupe_key(link, title)

                if key in seen:
                    continue

                geography = classify_geography(title, summary, hinted_region)

                item = {
                    "title": title,
                    "summary": summary[:1200],
                    "link": link,
                    "source": source_name,
                    "source_date": format_source_date(
                        entry.get("published", "") or entry.get("updated", "")
                    ),
                    "geography": geography,
                    "hinted_region": hinted_region,
                    "source_type": "google_news_search",
                    "search_query": query,
                }

                if not is_relevant(item):
                    continue

                item["relevance_score"] = relevance_score(item)
                item["theme"] = infer_theme(item)

                seen.add(key)
                items.append(item)

        except Exception as e:
            print(f"WARNING: Google News search failed for '{query}'. Error: {e}")

    return items


# ---------------------------------------------------------------------
# 5. STANDARD RSS
# ---------------------------------------------------------------------

def fetch_standard_rss():
    items = []
    seen = set()

    for source_cfg in load_rss_sources():
        url = source_cfg["rss"]
        hinted_region = source_cfg.get("region", "Global")

        try:
            feed = feedparser.parse(url)
            source_name = (
                clean_text(source_cfg.get("name"))
                or clean_text(feed.feed.get("title", ""))
                or source_name_from_url(url)
            )

            for entry in feed.entries[:35]:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))
                link = clean_text(entry.get("link", ""))

                if not title or not link:
                    continue

                key = dedupe_key(link, title)
                if key in seen:
                    continue

                geography = classify_geography(title, summary, hinted_region)

                item = {
                    "title": title,
                    "summary": summary[:1200],
                    "link": link,
                    "source": source_name,
                    "source_date": format_source_date(
                        entry.get("published", "") or entry.get("updated", "")
                    ),
                    "geography": geography,
                    "hinted_region": hinted_region,
                    "source_type": "rss",
                }

                if not is_relevant(item):
                    continue

                item["relevance_score"] = relevance_score(item)
                item["theme"] = infer_theme(item)

                seen.add(key)
                items.append(item)

        except Exception as e:
            print(f"WARNING: RSS source failed: {url}. Error: {e}")

    return items


# ---------------------------------------------------------------------
# 6. MERGE / DEDUPE / RANK
# ---------------------------------------------------------------------

def fetch_news(max_items=80):
    combined = fetch_google_news() + fetch_standard_rss()

    seen = set()
    deduped = []

    for item in combined:
        key = dedupe_key(item.get("link"), item.get("title"))

        if key in seen:
            continue

        seen.add(key)
        deduped.append(item)

    # Qatar first, GCC second, Global last.
    # Within each geography, sort by deterministic relevance score.
    deduped.sort(
        key=lambda x: (
            2 if x.get("geography") == "Qatar"
            else 1 if x.get("geography") == "GCC"
            else 0,
            x.get("relevance_score", 0),
        ),
        reverse=True,
    )

    qatar_count = sum(1 for x in deduped if x.get("geography") == "Qatar")
    gcc_count = sum(1 for x in deduped if x.get("geography") == "GCC")
    global_count = sum(1 for x in deduped if x.get("geography") == "Global")

    print(
        f"DISCOVERY MIX | Qatar={qatar_count} | GCC={gcc_count} | "
        f"Global={global_count} | total={len(deduped)}"
    )

    for item in deduped[:25]:
        print(
            "CANDIDATE | "
            f"{item.get('geography')} | "
            f"score={item.get('relevance_score')} | "
            f"{item.get('title')}"
        )

    return deduped[:max_items]


# ---------------------------------------------------------------------
# 7. CLAUDE TOPIC SELECTION
# ---------------------------------------------------------------------

def ask_claude(prompt, max_tokens=12000):
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text.strip()

    raise ValueError("Claude returned no text block")


def extract_json_array(text):
    text = clean_text(text)

    if text.startswith("```"):
        text = (
            text.replace("```json", "")
            .replace("```JSON", "")
            .replace("```", "")
            .strip()
        )

    start = text.find("[")
    end = text.rfind("]")

    if start == -1 or end == -1 or end <= start:
        raise ValueError(
            f"No complete JSON array found in Claude response. "
            f"Preview: {text[:1200]}"
        )

    return json.loads(text[start : end + 1])


def ai_select_topics(news_items, bank_name):
    if len(news_items) < 6:
        raise ValueError(
            f"Only {len(news_items)} relevant real stories were found. "
            "The workflow will not create synthetic fallback articles."
        )

    # Build a diversified pool so competitor stories do not dominate.
    qatar_items = [x for x in news_items if x.get("geography") == "Qatar"]
    gcc_items = [x for x in news_items if x.get("geography") == "GCC"]
    global_items = [x for x in news_items if x.get("geography") == "Global"]

    qatar_deals = [x for x in qatar_items if x.get("theme") == "deal_market"]
    qatar_solutions = [x for x in qatar_items if x.get("theme") == "solution"]
    qatar_markets = [x for x in qatar_items if x.get("theme") == "market"]
    qatar_competitors = [x for x in qatar_items if x.get("theme") == "competitor"]
    qatar_other = [x for x in qatar_items if x.get("theme") == "other"]

    gcc_non_competitor = [x for x in gcc_items if x.get("theme") != "competitor"]
    gcc_competitor = [x for x in gcc_items if x.get("theme") == "competitor"]

    selection_pool = (
        qatar_deals[:10]
        + qatar_solutions[:8]
        + qatar_markets[:8]
        + qatar_competitors[:6]
        + qatar_other[:4]
        + gcc_non_competitor[:8]
        + gcc_competitor[:4]
    )

    pool_seen = set()
    diversified = []
    for item in selection_pool:
        key = dedupe_key(item.get("link"), item.get("title"))
        if key in pool_seen:
            continue
        pool_seen.add(key)
        diversified.append(item)
    selection_pool = diversified

    if len(selection_pool) < 24:
        selection_pool += global_items[: max(0, 24 - len(selection_pool))]

    print(
        "SELECTION POOL MIX | "
        f"Qatar deals={len(qatar_deals)} | "
        f"Qatar solutions={len(qatar_solutions)} | "
        f"Qatar markets={len(qatar_markets)} | "
        f"Qatar competitors={len(qatar_competitors)} | "
        f"GCC={len(gcc_items)}"
    )

    prompt = f"""
You are the competitive-intelligence analyst for the Chief Strategy Officer of {bank_name}.

This is NOT a general news digest.

Select exactly 6 REAL developments that are most strategically relevant to Doha Bank.

CORE RULE:
A story must have a direct transmission channel to Doha Bank through at least one of:
- deposits
- lending
- fee income
- payments
- transaction banking
- treasury
- trade finance
- cash management
- project finance
- wealth
- funding
- credit risk
- customer acquisition
- client retention
- competitive positioning

GEOGRAPHY AND FRESHNESS:
- Prefer Qatar first.
- Prefer GCC second.
- Prefer stories from the last 7 days; use 8-14 day stories only when materially stronger.
- Do NOT select a Qatar/GCC story just because it is local.
- Relevance to Doha Bank is mandatory.
- A global story may be selected only if its Doha Bank impact is clearly stronger than a regional alternative.

PORTFOLIO MIX:
- Select at most 2 competitor-focused stories.
- Select at least 2 Qatar market/client/deal opportunities when credible candidates exist.
- Select at least 1 new solution/capability or white-space opportunity when credible candidates exist.
- The six topics should feel like a strategy opportunity set, not a competitor-monitoring newsletter.

STRONGEST TYPES OF INTELLIGENCE:
1. A named Qatar competitor launches or adopts a new capability.
2. A Qatar/GCC bank or fintech launches a solution that could reshape customer expectations.
3. A named Qatar corporate, government entity or project creates a financing/payments/treasury opportunity.
4. A new Qatar/GCC market or client segment creates an identifiable revenue pool.
5. A competitor or technology development threatens Doha Bank's existing revenue pools.
6. A clear white-space opportunity emerges for Doha Bank.

REJECT:
- stories older than 14 days unless they represent an unusually material development that is still strategically active
- generic GDP stories
- generic inflation stories
- Fed / ECB commentary
- generic oil-price commentary
- awards
- sponsorships
- CSR
- routine marketing
- tourism/lifestyle news without a banking wallet
- broad "Qatar growth" stories with no named actor or commercial opportunity

DO NOT CREATE FALLBACK TOPICS.
Do not write "no sufficiently material..." or "no new solution...".

Categories can repeat.
The goal is the 6 strongest REAL strategic developments, not one item from each category.

For each selected item return:
- topic_id
- category
- title
- source_title
- source_name
- source_url
- source_date
- source_excerpt
- why_it_matters
- potential_doha_bank_angle
- what_is_new
- named_rival_or_actor
- target_client_or_market
- revenue_pool
- recommended_strategy_test
- transmission_channel_to_doha_bank

Allowed categories:
- Competitor Move
- New Solution / Capability
- New Market / Client Pool
- Major Client / Deal Opportunity
- Strategic Threat / Disruption
- White-Space Opportunity

Return ONLY a valid JSON array of exactly 6 objects.
No markdown.
No explanation.

Candidate intelligence:
{json.dumps(selection_pool, ensure_ascii=False)}
"""

    raw = ask_claude(prompt)
    topics = extract_json_array(raw)

    valid = []
    used_urls = set()

    for t in topics:
        if len(valid) >= 6:
            break

        url = clean_text(t.get("source_url", ""))
        title = clean_text(t.get("title", ""))

        if not url or url == "#" or not title:
            continue

        if title.lower().startswith("no sufficiently") or title.lower().startswith("no new"):
            continue

        url_key = url.split("?")[0].rstrip("/").lower()

        if url_key in used_urls:
            continue

        matched = next(
            (
                item
                for item in selection_pool
                if clean_text(item.get("link", "")).split("?")[0].rstrip("/").lower()
                == url_key
            ),
            None,
        )

        if not matched:
            continue

        used_urls.add(url_key)

        t["topic_id"] = str(len(valid) + 1)
        t["source_title"] = (
            clean_text(t.get("source_title", ""))
            or clean_text(matched.get("title", ""))
        )
        t["source_name"] = (
            clean_text(t.get("source_name", ""))
            or clean_text(matched.get("source", "News source"))
        )
        t["source_date"] = (
            clean_text(t.get("source_date", ""))
            or clean_text(matched.get("source_date", ""))
            or TODAY
        )
        t["source_excerpt"] = article_excerpt(
            t.get("source_excerpt")
            or matched.get("summary")
            or matched.get("title")
        )
        t["geography"] = matched.get("geography", "")

        valid.append(t)

    if len(valid) < 6:
        raise ValueError(
            f"Claude returned only {len(valid)} usable real topics. "
            "No fallback articles were generated."
        )

    print(
        "FINAL TOPIC MIX | "
        f"Qatar={sum(1 for x in valid if x.get('geography') == 'Qatar')} | "
        f"GCC={sum(1 for x in valid if x.get('geography') == 'GCC')} | "
        f"Global={sum(1 for x in valid if x.get('geography') == 'Global')}"
    )

    for t in valid:
        print(
            f"SELECTED {t['topic_id']} | "
            f"{t.get('geography')} | "
            f"{t.get('category')} | "
            f"{t.get('title')}"
        )

    return valid


# ---------------------------------------------------------------------
# 8. APPROVAL EMAIL
# ---------------------------------------------------------------------

def strip_outer_html(full_html):
    lower = full_html.lower()
    body_start = lower.find("<body")

    if body_start != -1:
        body_start = lower.find(">", body_start)
        body_end = lower.rfind("</body>")

        if body_start != -1 and body_end != -1:
            return full_html[body_start + 1 : body_end]

    return full_html


def build_approval_email(drafts, approval_webhook_url):
    sections = ""

    for draft in drafts:
        t = draft["topic"]
        topic_id = str(t.get("topic_id", ""))
        category = str(t.get("category", ""))
        geography = str(t.get("geography", ""))
        article_html = strip_outer_html(draft["html_file_content"])

        source_name = html.escape(str(t.get("source_name", "")))
        source_date = html.escape(str(t.get("source_date", "")))

        sections += f"""
<tr>
<td style="padding:24px 24px 10px 24px; border-top:2px solid #dbe5f0; background:#ffffff;">
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:2px; text-transform:uppercase; color:{BLUE}; font-weight:bold;">
    Full article option {html.escape(topic_id)}
  </p>

  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; color:{SLATE}; font-weight:bold;">
    {html.escape(category)}{" · " + html.escape(geography) if geography else ""}
  </p>

  <h2 style="margin:0 0 14px 0; font-family:Georgia,serif; font-size:24px; line-height:1.25; color:{NAVY};">
    {html.escape(t.get('title', ''))}
  </h2>

  <p style="margin:0 0 7px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>What is new:</strong> {html.escape(t.get('what_is_new', ''))}
  </p>

  <p style="margin:0 0 7px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Rival / actor:</strong> {html.escape(t.get('named_rival_or_actor', ''))}
  </p>

  <p style="margin:0 0 7px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Revenue pool / market:</strong> {html.escape(t.get('revenue_pool', ''))}
  </p>

  <p style="margin:0 0 7px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Doha Bank transmission:</strong> {html.escape(t.get('transmission_channel_to_doha_bank', ''))}
  </p>

  <p style="margin:0 0 12px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Strategy test:</strong> {html.escape(t.get('recommended_strategy_test', ''))}
  </p>

  <p dir="ltr" style="margin:0 0 12px 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; line-height:1.55; color:{MUTED};">
    <span dir="auto" style="unicode-bidi:isolate;">{source_name}</span>
    <span dir="ltr" style="unicode-bidi:isolate;"> &middot; </span>
    <span dir="ltr" style="unicode-bidi:isolate; white-space:nowrap;">{source_date}</span>
  </p>

  <p style="margin:0 0 12px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Source excerpt:</strong> {html.escape(t.get('source_excerpt', ''))}
  </p>

  <a href="{approval_webhook_url}?decision=approve&topic_id={html.escape(topic_id)}"
     style="display:inline-block; background-color:{BLUE}; color:#ffffff; text-decoration:none; padding:11px 18px; border-radius:5px; font-family:Arial,Helvetica,sans-serif; font-size:13px; font-weight:bold;">
    Approve and send this exact article
  </a>

  <p style="margin:12px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; color:{MUTED};">
    The article below is the same stored HTML file that final send will use.
  </p>
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
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DB Strategy Weekly Approval</title>
</head>

<body style="margin:0; padding:0; background-color:#eef2f6; font-family:Arial,Helvetica,sans-serif;">

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#eef2f6; padding:28px 12px;">
<tr>
<td align="center">

<table role="presentation" width="760" cellpadding="0" cellspacing="0" style="width:760px; max-width:100%; background:#ffffff; border-radius:6px; overflow:hidden;">

<tr>
<td style="height:4px; background:{NAVY};">&nbsp;</td>
</tr>

<tr>
<td style="padding:30px 34px 22px 34px;">
  <p style="margin:0 0 12px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; letter-spacing:3px; color:{BLUE}; font-weight:bold;">
    APPROVAL REQUIRED
  </p>

  <h1 style="margin:0; font-family:Georgia,serif; font-size:30px; font-weight:normal; color:{NAVY};">
    DB Strategy Weekly — Full Article Approval
  </h1>

  <p style="margin:8px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:14px; color:{MUTED};">
    {TODAY} · Review the six strongest real strategic developments identified this cycle.
  </p>
</td>
</tr>

{sections}

<tr>
<td style="padding:18px 34px 26px 34px; border-top:1px solid #e2e8f0;">
  <p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:11px; color:{MUTED};">
    Generated from public Qatar, GCC, competitor-bank and business-news sources. Review before distribution.
  </p>
</td>
</tr>

</table>
</td>
</tr>
</table>

</body>
</html>
"""


# ---------------------------------------------------------------------
# 9. MAIN
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="drafts.html")
    parser.add_argument("--json-out", default="drafts.json")
    parser.add_argument("--bank", default="Doha Bank Q.P.S.C.")
    parser.add_argument("--impact-rules", default="impact_rules.json")
    args = parser.parse_args()

    approval_webhook_url = os.environ["APPROVAL_WEBHOOK_URL"]

    news = fetch_news()
    print(f"Fetched {len(news)} strategically relevant real stories.")

    topics = ai_select_topics(news, args.bank)

    metrics = weekly.get_doha_bank_metrics(args.bank)
    impact_rules = weekly.load_impact_rules(args.impact_rules)

    drafts = []
    topics_for_json = []

    for topic in topics:
        topic_id = str(topic.get("topic_id"))

        article = weekly.ai_write_article(
            topic,
            metrics,
            args.bank,
            impact_rules,
        )

        html_body = weekly.build_final_email(topic, article)
        html_file = f"strategy_weekly_option_{topic_id}.html"

        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_body)

        record = dict(topic)
        record["html_file"] = html_file
        record["article_title"] = article.get(
            "article_title",
            topic.get("title", ""),
        )

        topics_for_json.append(record)

        drafts.append({
            "topic": record,
            "html_file": html_file,
            "html_file_content": html_body,
        })

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(
            topics_for_json,
            f,
            ensure_ascii=False,
            indent=2,
        )

    approval_html = build_approval_email(
        drafts,
        approval_webhook_url,
    )

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(approval_html)

    print(f"Generated approval email: {args.out}")
    print(
        "Generated exact final article files:",
        ", ".join(d["html_file"] for d in drafts),
    )


if __name__ == "__main__":
    main()

    
