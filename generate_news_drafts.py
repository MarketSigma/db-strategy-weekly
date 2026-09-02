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

# Reuse the final article generator so approval and final send use
# the same Doha Bank financial-analysis logic and HTML template.
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

# Qatar-first discovery.
#
# IMPORTANT:
# - Qatar is the primary market.
# - GCC is secondary and must have a credible Qatar / Doha Bank channel.
# - Global items are exceptional; there is NO global quota.
# - Google News RSS is used for targeted discovery because many official
#   and local sites do not expose stable RSS feeds.

STRATEGIC_SEARCHES = [
    # Doha Bank directly
    ("Qatar", '"Doha Bank" Qatar'),
    ("Qatar", '"Doha Bank" financing'),
    ("Qatar", '"Doha Bank" corporate banking'),
    ("Qatar", '"Doha Bank" trade finance'),
    ("Qatar", '"Doha Bank" digital banking'),
    ("Qatar", '"Doha Bank" sukuk bond funding'),
    ("Qatar", '"Doha Bank" partnership'),

    # Qatar banking system / regulation / monetary conditions
    ("Qatar", '"Qatar Central Bank" banking sector'),
    ("Qatar", '"Qatar Central Bank" credit growth'),
    ("Qatar", '"Qatar Central Bank" deposits liquidity'),
    ("Qatar", '"Qatar Central Bank" interest rates'),
    ("Qatar", 'Qatar bank lending credit growth'),
    ("Qatar", 'Qatar bank deposits liquidity'),
    ("Qatar", 'Qatar private sector credit'),
    ("Qatar", 'Qatar banking sector profitability liquidity capital'),

    # Qatar economy / sovereign / projects
    ("Qatar", 'Qatar economy GDP growth investment'),
    ("Qatar", 'Qatar government spending projects contracts'),
    ("Qatar", 'Qatar sovereign bond sukuk issuance'),
    ("Qatar", 'Qatar infrastructure project financing'),
    ("Qatar", 'Qatar private sector investment financing'),
    ("Qatar", 'Qatar foreign investment companies expansion'),
    ("Qatar", 'Qatar public private partnership project'),

    # LNG / energy / industrial ecosystem
    ("Qatar", 'QatarEnergy project contract investment financing'),
    ("Qatar", 'Qatar LNG expansion contractors financing'),
    ("Qatar", 'Ras Laffan project contract investment'),
    ("Qatar", 'Qatar energy petrochemical industrial project financing'),

    # Key Qatar client pools / sectors
    ("Qatar", 'Qatar real estate market financing'),
    ("Qatar", 'Qatar mortgage market banking'),
    ("Qatar", 'Qatar SME financing growth'),
    ("Qatar", 'Qatar logistics investment financing'),
    ("Qatar", 'Qatar manufacturing investment financing'),
    ("Qatar", 'Qatar healthcare investment financing'),
    ("Qatar", 'Qatar tourism hospitality investment financing'),
    ("Qatar", 'Qatar aviation investment financing'),
    ("Qatar", 'Qatar trade imports exports finance'),

    # Qatar payments / digital / capital markets
    ("Qatar", 'Qatar payments transaction volumes QCB'),
    ("Qatar", 'Qatar digital payments banking'),
    ("Qatar", 'Qatar transaction banking cash management'),
    ("Qatar", 'Qatar supply chain finance'),
    ("Qatar", 'Qatar open banking launch'),
    ("Qatar", 'Qatar fintech banking partnership'),
    ("Qatar", 'Qatar wealth asset management expansion'),
    ("Qatar", 'Qatar capital markets sukuk bond issuance'),
    ("Qatar", 'Qatar Stock Exchange listing capital markets'),

    # Qatari competitors — useful but no fixed quota
    ("Qatar", '"QNB" Qatar banking partnership launch'),
    ("Qatar", '"Qatar Islamic Bank" banking partnership launch'),
    ("Qatar", '"Commercial Bank Qatar" banking partnership launch'),
    ("Qatar", '"Dukhan Bank" banking partnership launch'),
    ("Qatar", '"Masraf Al Rayan" banking partnership launch'),
    ("Qatar", '"QIIB" Qatar banking partnership launch'),
    ("Qatar", '"Ahlibank Qatar" banking partnership launch'),

    # GCC — secondary only
    ("GCC", 'GCC banking liquidity funding credit growth Qatar'),
    ("GCC", 'GCC bank transaction banking payments Qatar'),
    ("GCC", 'GCC project finance trade finance Qatar'),
    ("GCC", 'GCC banking regulation capital liquidity Qatar'),

    # Global — exceptional only
    ("Global", 'global banking Basel capital liquidity major change'),
    ("Global", 'global cross-border payments SWIFT major change'),
    ("Global", 'global banks tokenized deposits stablecoin major launch'),
    ("Global", 'major central bank rate decision GCC Qatar banks'),
    ("Global", 'global banking cyber disruption payments major banks'),
]

DEFAULT_RSS_SOURCES = [
    {"name": "Gulf Times", "rss": "https://www.gulf-times.com/rss", "region": "Qatar", "priority": 100},
    {"name": "The Peninsula Qatar", "rss": "https://thepeninsulaqatar.com/rss", "region": "Qatar", "priority": 100},
    {"name": "Arab News", "rss": "https://www.arabnews.com/rss.xml", "region": "GCC", "priority": 65},
    {"name": "Al Jazeera", "rss": "https://www.aljazeera.com/xml/rss/all.xml", "region": "Regional", "priority": 45},
    {"name": "CNBC Finance", "rss": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "region": "Global", "priority": 20},
    {"name": "BBC Business", "rss": "https://feeds.bbci.co.uk/news/business/rss.xml", "region": "Global", "priority": 15},
    {"name": "BIS Press Releases", "rss": "https://www.bis.org/rss/press_releases.xml", "region": "Global", "priority": 20},
]

DOHA_BANK_TERMS = [
    "doha bank",
    "dhbk",
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
    "qse",
    "qatar financial centre",
    "qatar financial center",
    "qfc",
    "lusail",
    "ras laffan",
]

QATAR_SYSTEM_TERMS = [
    "qatar central bank",
    "qcb",
    "qatar banking sector",
    "qatar banks",
    "qatar bank",
    "qatar stock exchange",
    "qatar financial centre",
    "qatar financial center",
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

FINANCIAL_TRANSMISSION_TERMS = [
    "lending",
    "loan",
    "loans",
    "credit",
    "deposit",
    "deposits",
    "funding",
    "liquidity",
    "interest rate",
    "policy rate",
    "margin",
    "nim",
    "fee income",
    "fees",
    "payments",
    "trade finance",
    "project finance",
    "working capital",
    "cash management",
    "treasury",
    "foreign exchange",
    "fx",
    "sukuk",
    "bond",
    "capital",
    "npl",
    "credit risk",
    "asset quality",
    "mortgage",
    "real estate",
    "investment",
    "contract",
    "project",
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
    "new product",
    "new service",
]

GLOBAL_SIGNIFICANCE_TERMS = [
    "stablecoin",
    "tokenized deposit",
    "tokenised deposit",
    "cross-border payments",
    "swift",
    "basel",
    "capital requirement",
    "liquidity requirement",
    "cyber attack",
    "cyberattack",
    "instant payments",
    "central bank rate",
    "interest rate decision",
    "systemic",
    "global banks",
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
    "celebrity",
    "weather",
    "recipe",
    "podcast",
]

# Hard exclusion for sports. Qatar general-news RSS feeds often contain a large
# sports section, so these items must be blocked BEFORE Qatar relevance scoring.
SPORTS_TERMS = [
    "football",
    "soccer",
    "fifa",
    "afc ",
    "afc champions",
    "qatar stars league",
    "qsl",
    "stars league",
    "world cup",
    "asian cup",
    "arab cup",
    "champions league",
    "premier league",
    "la liga",
    "serie a",
    "bundesliga",
    "ligue 1",
    "match",
    "fixture",
    "tournament",
    "semi-final",
    "semifinal",
    "quarter-final",
    "quarterfinal",
    "final clash",
    "penalty shootout",
    "kick-off",
    "kickoff",
    "goalkeeper",
    "striker",
    "midfielder",
    "defender",
    "coach",
    "manager said after the game",
    "stadium",
    "al sadd",
    "al rayyan",
    "al duhail",
    "al gharafa",
    "al arabi club",
    "umm salal",
    "al wakrah",
    "al shamal",
    "al ahli sports club",
    "sports club",
    "formula 1",
    "formula one",
    "grand prix",
    "motogp",
    "tennis",
    "atp ",
    "wta ",
    "basketball",
    "volleyball",
    "handball",
    "cricket",
    "athletics",
]

SPORTS_URL_TERMS = [
    "/sport",
    "/sports",
    "/football",
    "/soccer",
    "/tennis",
    "/cricket",
]

# A Qatar story must also contain an actual business/economic/banking signal.
# This prevents general local news from qualifying simply because it says Qatar.
ECONOMIC_SIGNAL_TERMS = [
    "bank",
    "banking",
    "central bank",
    "qcb",
    "loan",
    "lending",
    "credit",
    "deposit",
    "deposits",
    "liquidity",
    "funding",
    "finance",
    "financing",
    "investment",
    "investor",
    "economy",
    "economic",
    "gdp",
    "inflation",
    "interest rate",
    "policy rate",
    "trade",
    "export",
    "import",
    "project",
    "contract",
    "infrastructure",
    "real estate",
    "property",
    "mortgage",
    "sme",
    "private sector",
    "qatarenergy",
    "lng",
    "energy",
    "capital market",
    "stock exchange",
    "qse",
    "sukuk",
    "bond",
    "payments",
    "fintech",
    "digital banking",
    "cash management",
    "treasury",
    "wealth",
    "asset management",
    "corporate",
    "company",
    "companies",
    "business",
    "revenue",
    "profit",
    "earnings",
    "acquisition",
    "merger",
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
                    "priority": int(item.get("priority", 0) or 0),
                })
            return result or DEFAULT_RSS_SOURCES

    except Exception as e:
        print(f"WARNING: Could not read {path}; using built-in RSS sources. Error: {e}")

    return DEFAULT_RSS_SOURCES


# ---------------------------------------------------------------------
# 3. GEOGRAPHY / RELEVANCE / SCORING
# ---------------------------------------------------------------------

def contains_any(text, terms):
    return any(term in text for term in terms)


def is_sports_item(item):
    title = clean_text(item.get("title", ""))
    summary = clean_text(item.get("summary", ""))
    link = clean_text(item.get("link", ""))
    combined = f" {title} {summary} ".lower()
    link_lower = link.lower()

    if contains_any(combined, SPORTS_TERMS):
        return True

    if any(term in link_lower for term in SPORTS_URL_TERMS):
        return True

    return False


def classify_geography(title, summary, hinted_region=""):
    """
    Classify based on ARTICLE CONTENT, not publisher location.

    The old logic could label any story from a Qatar publisher as Qatar.
    That is intentionally removed here.
    """
    combined = f"{title} {summary}".lower()

    if contains_any(combined, QATAR_TERMS):
        return "Qatar"

    if contains_any(combined, GCC_TERMS):
        return "GCC"

    return "Global"


def source_priority_bonus(item):
    """
    Converts the configured source priority into a modest ranking bonus.
    Priority helps order similar stories; it does not override relevance.
    """
    try:
        priority = int(item.get("source_priority", 0) or 0)
    except Exception:
        priority = 0

    if priority >= 90:
        return 15
    if priority >= 70:
        return 10
    if priority >= 40:
        return 5
    return 0


def relevance_score(item):
    title = clean_text(item.get("title", ""))
    summary = clean_text(item.get("summary", ""))
    combined = f" {title} {summary} ".lower()

    # Hard reject sports and other low-value content before any Qatar bonus.
    if is_sports_item(item):
        return -1000

    if contains_any(combined, LOW_VALUE_TERMS):
        return -500

    geography = item.get("geography") or classify_geography(
        title,
        summary,
        item.get("hinted_region", ""),
    )

    score = 0

    # Direct Doha Bank relevance dominates all other signals.
    if contains_any(combined, DOHA_BANK_TERMS):
        score += 50

    # Qatar relevance.
    if geography == "Qatar":
        score += 30

    if contains_any(combined, QATAR_SYSTEM_TERMS):
        score += 25

    # Qatar corporate / project / client-pool relevance.
    if geography == "Qatar" and contains_any(combined, COMMERCIAL_TERMS):
        score += 25

    # Qatari competitor move.
    if geography == "Qatar" and contains_any(combined, COMPETITOR_NAMES):
        score += 20

    # Explicit banking transmission.
    transmission_hits = sum(
        1 for term in FINANCIAL_TRANSMISSION_TERMS if term in combined
    )
    score += min(transmission_hits, 4) * 5

    # General banking relevance.
    banking_hits = sum(1 for term in BANKING_TERMS if term in combined)
    score += min(banking_hits, 4) * 4

    # GCC stories must work harder.
    if geography == "GCC":
        score += 8

    # Global stories start with a penalty and need exceptional materiality.
    if geography == "Global":
        score -= 25
        global_hits = sum(
            1 for term in GLOBAL_SIGNIFICANCE_TERMS if term in combined
        )
        score += min(global_hits, 4) * 10

    # Use source priority only as a secondary ranking signal.
    score += source_priority_bonus(item)

    return score


def infer_theme(item):
    combined = f"{clean_text(item.get('title', ''))} {clean_text(item.get('summary', ''))}".lower()

    if contains_any(combined, DOHA_BANK_TERMS):
        return "doha_bank"
    if contains_any(combined, COMPETITOR_NAMES):
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
        "logistics", "healthcare", "tourism", "aviation", "wealth",
        "capital markets", "sukuk", "bond issuance", "real estate",
        "mortgage", "lng", "qatarenergy"
    ]):
        return "market"
    return "other"


def has_minimum_transmission_signal(item):
    combined = f" {clean_text(item.get('title', ''))} {clean_text(item.get('summary', ''))} ".lower()

    # Sports is never eligible, even if the article mentions sponsorship,
    # investment, contracts or a bank.
    if is_sports_item(item):
        return False

    # Direct Doha Bank stories qualify only when they are not low-value PR/sports.
    if contains_any(combined, DOHA_BANK_TERMS):
        return not contains_any(combined, LOW_VALUE_TERMS)

    # Qatar stories must contain an actual economic/business/banking signal.
    # Merely mentioning Qatar/Doha is not enough.
    if item.get("geography") == "Qatar":
        return (
            contains_any(combined, ECONOMIC_SIGNAL_TERMS)
            and (
                contains_any(combined, BANKING_TERMS)
                or contains_any(combined, FINANCIAL_TRANSMISSION_TERMS)
                or contains_any(combined, COMMERCIAL_TERMS)
                or contains_any(combined, ["economy", "economic", "gdp", "inflation", "qatarenergy", "lng"])
            )
        )

    # GCC/global stories need an explicit financial/banking transmission signal.
    return (
        contains_any(combined, FINANCIAL_TRANSMISSION_TERMS)
        and contains_any(combined, BANKING_TERMS + GLOBAL_SIGNIFICANCE_TERMS)
    )


def is_relevant(item):
    if not has_minimum_transmission_signal(item):
        return False

    score = relevance_score(item)

    if item.get("geography") == "Qatar":
        return score >= 45
    if item.get("geography") == "GCC":
        return score >= 35
    return score >= 20


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
                    "summary": summary[:380],
                    "link": link,
                    "source": source_name,
                    "source_date": format_source_date(
                        entry.get("published", "") or entry.get("updated", "")
                    ),
                    "geography": geography,
                    "hinted_region": hinted_region,
                    "source_priority": 0,
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
        source_priority = int(source_cfg.get("priority", 0) or 0)

        try:
            feed = feedparser.parse(url)
            source_name = (
                clean_text(source_cfg.get("name"))
                or clean_text(feed.feed.get("title", ""))
                or source_name_from_url(url)
            )

            for entry in feed.entries[:40]:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))
                link = clean_text(entry.get("link", ""))

                if not title or not link:
                    continue

                key = dedupe_key(link, title)
                if key in seen:
                    continue

                # IMPORTANT: publisher region is only metadata.
                # Geography is based on the article text itself.
                geography = classify_geography(title, summary, hinted_region)

                item = {
                    "title": title,
                    "summary": summary[:380],
                    "link": link,
                    "source": source_name,
                    "source_date": format_source_date(
                        entry.get("published", "") or entry.get("updated", "")
                    ),
                    "geography": geography,
                    "hinted_region": hinted_region,
                    "source_priority": source_priority,
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

def fetch_news(max_items=60):
    combined = fetch_google_news() + fetch_standard_rss()

    sports_blocked = sum(1 for x in combined if is_sports_item(x))
    if sports_blocked:
        print(f"SPORTS FILTER | blocked={sports_blocked}")

    seen = set()
    deduped = []

    for item in combined:
        key = dedupe_key(item.get("link"), item.get("title"))

        if key in seen:
            continue

        seen.add(key)
        deduped.append(item)

    # Qatar first, then GCC, then Global.
    deduped.sort(
        key=lambda x: (
            2 if x.get("geography") == "Qatar"
            else 1 if x.get("geography") == "GCC"
            else 0,
            x.get("relevance_score", 0),
            x.get("source_priority", 0),
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

    for item in deduped[:30]:
        print(
            "CANDIDATE | "
            f"{item.get('geography')} | "
            f"score={item.get('relevance_score')} | "
            f"priority={item.get('source_priority', 0)} | "
            f"{item.get('title')}"
        )

    return deduped[:max_items]


# ---------------------------------------------------------------------
# 7. CLAUDE TOPIC SELECTION
# ---------------------------------------------------------------------

def ask_claude(prompt, max_tokens=7000):
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            value = getattr(block, "text", "")
            if value:
                text_parts.append(value)

    if text_parts:
        return "\n".join(text_parts).strip()

    stop_reason = getattr(response, "stop_reason", "")
    content_types = [
        getattr(block, "type", type(block).__name__)
        for block in getattr(response, "content", [])
    ]

    raise ValueError(
        f"Claude returned no usable text block. "
        f"stop_reason={stop_reason!r}, content_types={content_types}"
    )


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


def build_selection_pool(news_items):
    qatar_items = sorted(
        [x for x in news_items if x.get("geography") == "Qatar"],
        key=lambda x: x.get("relevance_score", 0),
        reverse=True,
    )
    gcc_items = sorted(
        [x for x in news_items if x.get("geography") == "GCC"],
        key=lambda x: x.get("relevance_score", 0),
        reverse=True,
    )
    global_items = sorted(
        [x for x in news_items if x.get("geography") == "Global"],
        key=lambda x: x.get("relevance_score", 0),
        reverse=True,
    )

    qatar_doha = [x for x in qatar_items if x.get("theme") == "doha_bank"]
    qatar_deals = [x for x in qatar_items if x.get("theme") == "deal_market"]
    qatar_markets = [x for x in qatar_items if x.get("theme") == "market"]
    qatar_solutions = [x for x in qatar_items if x.get("theme") == "solution"]
    qatar_competitors = [x for x in qatar_items if x.get("theme") == "competitor"]
    qatar_other = [x for x in qatar_items if x.get("theme") == "other"]

    # Strong Qatar representation without forcing category quotas.
    # GCC/global are only added as alternatives after the best Qatar items.
    candidates = (
        qatar_doha[:5]
        + qatar_deals[:8]
        + qatar_markets[:8]
        + qatar_solutions[:5]
        + qatar_competitors[:5]
        + qatar_other[:4]
        + gcc_items[:6]
        + global_items[:3]
    )

    seen = set()
    diversified = []

    for item in candidates:
        key = dedupe_key(item.get("link"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        diversified.append(item)

    # Re-rank the merged pool by relevance while preserving enough Qatar breadth.
    diversified.sort(
        key=lambda x: (
            2 if x.get("geography") == "Qatar"
            else 1 if x.get("geography") == "GCC"
            else 0,
            x.get("relevance_score", 0),
        ),
        reverse=True,
    )

    return diversified[:30]


def ai_select_topics(news_items, bank_name):
    if len(news_items) < 6:
        raise ValueError(
            f"Only {len(news_items)} relevant real stories were found. "
            "The workflow will not create synthetic fallback articles."
        )

    selection_pool = build_selection_pool(news_items)

    print(
        "SELECTION POOL MIX | "
        f"Qatar={sum(1 for x in selection_pool if x.get('geography') == 'Qatar')} | "
        f"GCC={sum(1 for x in selection_pool if x.get('geography') == 'GCC')} | "
        f"Global={sum(1 for x in selection_pool if x.get('geography') == 'Global')} | "
        f"Total={len(selection_pool)}"
    )

    compact_pool = []
    for item in selection_pool:
        compact_pool.append({
            "title": clean_text(item.get("title", "")),
            "summary": article_excerpt(item.get("summary", ""), 300),
            "link": clean_text(item.get("link", "")),
            "source": clean_text(item.get("source", "")),
            "source_date": clean_text(item.get("source_date", "")),
            "geography": clean_text(item.get("geography", "")),
            "theme": clean_text(item.get("theme", "")),
            "relevance_score": item.get("relevance_score", 0),
            "source_priority": item.get("source_priority", 0),
        })

    prompt = f"""
You are the competitive-intelligence analyst for the Chief Strategy Officer of {bank_name}.

Select exactly 6 REAL developments from the candidate list.

PRIMARY OBJECTIVE:
Produce a Qatar-market intelligence briefing for Doha Bank, not a generic GCC or global banking digest.

MANDATORY SELECTION RULES:
1. Doha Bank relevance is mandatory for every selected development.
2. Qatar-specific developments should dominate the six whenever sufficient material Qatar stories exist.
3. Aim for AT LEAST FOUR Qatar-specific developments when four material Qatar candidates are available.
4. There is NO required quota for competitor stories.
5. There is NO required quota for GCC stories.
6. There is NO required quota for global stories.
7. A global development should be selected only if it is exceptionally material and has a stronger, clearer transmission channel to Doha Bank than the available Qatar/GCC alternatives.
8. A GCC development may replace a Qatar development only when its Doha Bank transmission is stronger.
9. Prefer developments from the last 7 days.
10. Prefer developments affecting Qatar lending, deposits, funding, liquidity, margins, fees, payments, trade finance, project finance, asset quality, capital, client activity or competitive positioning.
11. Reject generic macro commentary, awards, sponsorships, CSR, lifestyle stories and weak technology announcements.
12. Do not create fallback topics.
13. Do not invent sources, companies, projects or facts.

DOHA BANK TRANSMISSION TEST:
Before selecting an item, internally answer:
- What changed?
- Why does it matter specifically to Qatar?
- Through what channel could it affect Doha Bank?
- Which Doha Bank business area or financial metric could plausibly be affected?
If the Qatar relevance or Doha Bank transmission is weak, DO NOT select it.

For every candidate, distinguish:
- DIRECT: explicitly involves Doha Bank or an immediate Qatar banking/client exposure.
- INDIRECT: Qatar market development with a credible banking transmission.
- SPECULATIVE: weak or generic connection.

Do not select SPECULATIVE items.

For each selected item return ONLY these fields:
- source_url
- category
- why_it_matters
- potential_doha_bank_angle
- what_is_new
- named_rival_or_actor
- target_client_or_market
- revenue_pool
- recommended_strategy_test
- transmission_channel_to_doha_bank
- transmission_strength

Allowed categories:
- Doha Bank Development
- Competitor Move
- New Solution / Capability
- New Market / Client Pool
- Major Client / Deal Opportunity
- Strategic Threat / Disruption
- White-Space Opportunity
- Qatar Macro / Regulatory Development

Allowed transmission_strength:
- DIRECT
- INDIRECT

Keep every text field concise: maximum 25 words.
Return ONLY a valid JSON array of exactly 6 objects.
No markdown. No explanation.

Candidate intelligence:
{json.dumps(compact_pool, ensure_ascii=False)}
"""

    try:
        raw = ask_claude(prompt, max_tokens=5000)
        topics = extract_json_array(raw)
    except Exception as first_error:
        print(f"WARNING: First Claude selection attempt failed: {first_error}")
        print("Retrying Claude with a smaller top-16 candidate pool.")

        retry_pool = compact_pool[:16]
        retry_prompt = prompt.rsplit("Candidate intelligence:\n", 1)[0] + (
            "Candidate intelligence:\n"
            + json.dumps(retry_pool, ensure_ascii=False)
        )

        try:
            raw = ask_claude(retry_prompt, max_tokens=4500)
            topics = extract_json_array(raw)
        except Exception as retry_error:
            print(f"WARNING: Claude retry also failed: {retry_error}")
            print("Using deterministic Qatar-first selection from real candidates.")

            # Deterministic backup: take best real items, Qatar first.
            backup = sorted(
                selection_pool,
                key=lambda x: (
                    2 if x.get("geography") == "Qatar"
                    else 1 if x.get("geography") == "GCC"
                    else 0,
                    x.get("relevance_score", 0),
                ),
                reverse=True,
            )

            topics = []
            used_backup_urls = set()

            for item in backup:
                if len(topics) >= 6:
                    break

                url = clean_text(item.get("link", ""))
                key = url.split("?")[0].rstrip("/").lower()
                if not url or key in used_backup_urls:
                    continue

                used_backup_urls.add(key)

                theme = item.get("theme", "")
                if theme == "doha_bank":
                    category = "Doha Bank Development"
                elif theme == "competitor":
                    category = "Competitor Move"
                elif theme == "solution":
                    category = "New Solution / Capability"
                elif theme == "deal_market":
                    category = "Major Client / Deal Opportunity"
                elif theme == "market":
                    category = "New Market / Client Pool"
                else:
                    category = "White-Space Opportunity"

                topics.append({
                    "source_url": url,
                    "category": category,
                    "why_it_matters": "High-ranked real development with a credible Qatar and Doha Bank banking transmission.",
                    "potential_doha_bank_angle": "Assess impact on relevant client activity, lending, deposits, fees, funding or competitive positioning.",
                    "what_is_new": clean_text(item.get("title", "")),
                    "named_rival_or_actor": "",
                    "target_client_or_market": "Relevant Qatar clients and sectors",
                    "revenue_pool": "Lending, deposits, payments, treasury, trade finance, project finance or fee income as applicable.",
                    "recommended_strategy_test": "Validate the commercial impact with the relevant business owner and priority client segments.",
                    "transmission_channel_to_doha_bank": "Qatar market transmission through client activity, balance-sheet demand, fees, funding, risk or competitive positioning.",
                    "transmission_strength": "DIRECT" if theme == "doha_bank" else "INDIRECT",
                })

            if len(topics) < 6:
                raise ValueError(
                    f"Only {len(topics)} usable real candidates remained after Claude failure."
                )

    valid = []
    used_urls = set()

    for t in topics:
        if len(valid) >= 6:
            break

        url = clean_text(t.get("source_url", ""))
        transmission_strength = clean_text(t.get("transmission_strength", "")).upper()

        if not url or url == "#":
            continue

        if transmission_strength not in ("DIRECT", "INDIRECT"):
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
        t["title"] = clean_text(matched.get("title", ""))
        t["source_title"] = clean_text(matched.get("title", ""))
        t["source_name"] = clean_text(matched.get("source", "News source"))
        t["source_date"] = clean_text(matched.get("source_date", "")) or TODAY
        t["source_excerpt"] = article_excerpt(
            matched.get("summary") or matched.get("title")
        )
        t["geography"] = matched.get("geography", "")
        t["relevance_score"] = matched.get("relevance_score", 0)

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
        f"Global={sum(1 for x in valid if x.get('geography') == 'Global')} | "
        f"Direct={sum(1 for x in valid if x.get('transmission_strength') == 'DIRECT')}"
    )

    for t in valid:
        print(
            f"SELECTED {t['topic_id']} | "
            f"{t.get('geography')} | "
            f"{t.get('transmission_strength')} | "
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
        transmission_strength = str(t.get("transmission_strength", ""))
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
    {html.escape(category)}
    {" · " + html.escape(geography) if geography else ""}
    {" · " + html.escape(transmission_strength) if transmission_strength else ""}
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
    {TODAY} · Review the six strongest Qatar-first strategic developments identified this cycle.
  </p>
</td>
</tr>

{sections}

<tr>
<td style="padding:18px 34px 26px 34px; border-top:1px solid #e2e8f0;">
  <p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:11px; color:{MUTED};">
    Generated from public sources with Qatar-first relevance screening and Doha Bank transmission testing. Review before distribution.
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

    
