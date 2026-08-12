import os
import json
import html
import argparse
import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urljoin
import urllib.parse
from urllib.request import Request, urlopen
from html.parser import HTMLParser
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

DEFAULT_NEWS_SOURCES = [
    {"name": "Gulf Times", "rss": "https://www.gulf-times.com/rss", "region": "qatar", "priority": 100},
    {"name": "The Peninsula Qatar", "rss": "https://thepeninsulaqatar.com/rss", "region": "qatar", "priority": 100},
    {"name": "Arab News", "rss": "https://www.arabnews.com/rss.xml", "region": "gcc", "priority": 80},
    {"name": "Al Jazeera", "rss": "https://www.aljazeera.com/xml/rss/all.xml", "region": "regional", "priority": 65},
    {"name": "CNBC Finance", "rss": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "region": "global", "priority": 30},
    {"name": "CNBC Markets", "rss": "https://www.cnbc.com/id/10001147/device/rss/rss.html", "region": "global", "priority": 30},
    {"name": "CNBC World", "rss": "https://www.cnbc.com/id/10072762/device/rss/rss.html", "region": "global", "priority": 25},
    {"name": "BBC Business", "rss": "https://feeds.bbci.co.uk/news/business/rss.xml", "region": "global", "priority": 25},
    {"name": "BBC World", "rss": "https://feeds.bbci.co.uk/news/world/rss.xml", "region": "global", "priority": 20},
    {"name": "IMF", "rss": "https://www.imf.org/en/News/RSS", "region": "global", "priority": 25},
    {"name": "World Bank", "rss": "https://www.worldbank.org/en/news/all?format=rss", "region": "global", "priority": 20},
    {"name": "BIS Press Releases", "rss": "https://www.bis.org/rss/press_releases.xml", "region": "global", "priority": 25},
    {"name": "BIS Speeches", "rss": "https://www.bis.org/rss/speeches.xml", "region": "global", "priority": 20},
    {"name": "Fitch Ratings", "rss": "https://www.fitchratings.com/site/pr/rss", "region": "global", "priority": 25},
    {"name": "S&P Global Ratings", "rss": "https://www.spglobal.com/ratings/en/rss", "region": "global", "priority": 25},
    {"name": "Investing Economy", "rss": "https://www.investing.com/rss/news_25.rss", "region": "global", "priority": 20},
    {"name": "Investing Commodities", "rss": "https://www.investing.com/rss/news_301.rss", "region": "global", "priority": 20},
    {"name": "Investing Banking", "rss": "https://www.investing.com/rss/news_285.rss", "region": "global", "priority": 20},
]


def load_news_sources(path="news_sources.json"):
    """
    Load source configuration from news_sources.json.

    Supported formats:
    1) {"sources": [{"name": "...", "rss": "...", "region": "qatar", "priority": 100}]}
    2) Legacy grouped format: {"global": [...], "regional": [...]}
    """
    if not os.path.exists(path):
        return DEFAULT_NEWS_SOURCES

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and isinstance(data.get("sources"), list):
            sources = []
            for item in data["sources"]:
                if not isinstance(item, dict) or not item.get("rss"):
                    continue
                sources.append({
                    "name": clean_text(item.get("name")) or source_name_from_url(item.get("rss")),
                    "rss": str(item.get("rss")).strip(),
                    "region": str(item.get("region") or "global").lower(),
                    "priority": int(item.get("priority") or 0),
                })
            return sources or DEFAULT_NEWS_SOURCES

        # Backward compatibility with the original grouped JSON.
        if isinstance(data, dict):
            sources = []
            for group_name, items in data.items():
                if not isinstance(items, list):
                    continue
                region = "qatar" if group_name.lower() == "regional" else group_name.lower()
                for item in items:
                    if isinstance(item, dict) and item.get("rss"):
                        sources.append({
                            "name": clean_text(item.get("name")) or source_name_from_url(item.get("rss")),
                            "rss": str(item.get("rss")).strip(),
                            "region": region,
                            "priority": 80 if region in ("qatar", "gcc", "regional") else 25,
                        })
            return sources or DEFAULT_NEWS_SOURCES

    except Exception as e:
        print(f"WARNING: Could not load {path}; using built-in sources. Error: {e}")

    return DEFAULT_NEWS_SOURCES


QATAR_TERMS = [
    "qatar", "doha", "qcb", "qatar central bank", "qatarenergy",
    "qatar energy", "qatar investment authority", "qia", "qatar stock exchange",
    "qe index", "ministry of finance qatar", "lusail", "ras laffan"
]

GCC_TERMS = [
    "gcc", "gulf cooperation council", "saudi", "saudi arabia", "riyadh",
    "uae", "united arab emirates", "dubai", "abu dhabi", "kuwait",
    "bahrain", "oman", "muscat", "gulf banks", "gcc banks"
]


def geographic_focus(title, summary, source_region="global"):
    combined = f"{title} {summary}".lower()

    if any(term in combined for term in QATAR_TERMS):
        return "Qatar", 140

    if any(term in combined for term in GCC_TERMS):
        return "GCC", 90

    if source_region == "qatar":
        return "Qatar", 110

    if source_region in ("gcc", "regional"):
        return "GCC", 65

    return "Global", 0


# Direct monitoring of official Qatar-bank newsrooms.
# This complements RSS feeds: competitor announcements are often published on
# bank websites before (or without) being picked up by general news RSS.
COMPETITOR_NEWSROOMS = [
    {
        "bank": "Dukhan Bank",
        "listing_url": "https://www.dukhanbank.com/media-center/press-release",
        "link_contains": "/media-center/press-release/",
        "priority": 260,
    },
    {
        "bank": "QNB",
        "listing_url": "https://www.qnb.com/sites/qnb/qnbglobal/page/en/ennewsandinsight.html",
        "link_contains": "/sites/qnb/",
        "priority": 235,
    },
    {
        "bank": "Qatar Islamic Bank",
        "listing_url": "https://www.qib.com.qa/en/about-us/media-center",
        "link_contains": "/about-us/media-center/news/",
        "priority": 240,
    },
    {
        "bank": "Commercial Bank",
        "listing_url": "https://www.cbq.qa/en/about-us/news",
        "link_contains": "/en/about-us/news/",
        "priority": 240,
    },
]



THEMATIC_INTELLIGENCE_SEARCHES = [
    # New solutions / capabilities
    {"category": "New Solution / Capability", "query": 'Qatar bank payments launch', "priority": 210},
    {"category": "New Solution / Capability", "query": 'Qatar bank fintech partnership', "priority": 210},
    {"category": "New Solution / Capability", "query": 'Qatar open banking launch', "priority": 215},
    {"category": "New Solution / Capability", "query": 'Qatar transaction banking cash management', "priority": 215},
    {"category": "New Solution / Capability", "query": 'Qatar bank AI digital platform', "priority": 205},
    {"category": "New Solution / Capability", "query": 'GCC bank new payments platform', "priority": 190},
    {"category": "New Solution / Capability", "query": 'Saudi UAE bank fintech launch', "priority": 180},

    # New markets / client pools
    {"category": "New Market / Client Pool", "query": 'Qatar new investment sector expansion', "priority": 205},
    {"category": "New Market / Client Pool", "query": 'Qatar data center investment', "priority": 215},
    {"category": "New Market / Client Pool", "query": 'Qatar logistics investment expansion', "priority": 205},
    {"category": "New Market / Client Pool", "query": 'Qatar healthcare investment expansion', "priority": 195},
    {"category": "New Market / Client Pool", "query": 'Qatar manufacturing investment new plant', "priority": 205},
    {"category": "New Market / Client Pool", "query": 'Qatar tourism investment project', "priority": 190},
    {"category": "New Market / Client Pool", "query": 'Qatar Financial Centre new firms expansion', "priority": 205},

    # Named deal / client opportunities
    {"category": "Major Client / Deal Opportunity", "query": 'Qatar company signs contract investment project', "priority": 220},
    {"category": "Major Client / Deal Opportunity", "query": 'QatarEnergy contract awarded project', "priority": 225},
    {"category": "Major Client / Deal Opportunity", "query": 'Qatar infrastructure contract awarded', "priority": 215},
    {"category": "Major Client / Deal Opportunity", "query": 'Qatar company expansion financing', "priority": 210},
    {"category": "Major Client / Deal Opportunity", "query": 'Qatar acquisition investment joint venture', "priority": 210},
    {"category": "Major Client / Deal Opportunity", "query": 'Qatar new plant project investment', "priority": 215},

    # Threat / disruption
    {"category": "Strategic Threat / Disruption", "query": 'Qatar digital bank fintech new entrant', "priority": 205},
    {"category": "Strategic Threat / Disruption", "query": 'Qatar payments fintech launch bank competition', "priority": 205},
    {"category": "Strategic Threat / Disruption", "query": 'GCC digital bank enters market', "priority": 185},
    {"category": "Strategic Threat / Disruption", "query": 'GCC stablecoin bank payments launch', "priority": 185},

    # White-space
    {"category": "White-Space Opportunity", "query": 'Qatar embedded finance fintech', "priority": 195},
    {"category": "White-Space Opportunity", "query": 'Qatar SME fintech platform', "priority": 195},
    {"category": "White-Space Opportunity", "query": 'Qatar supply chain finance platform', "priority": 205},
    {"category": "White-Space Opportunity", "query": 'Qatar B2B payments platform', "priority": 205},
    {"category": "White-Space Opportunity", "query": 'GCC open finance embedded finance', "priority": 180},
]

THEME_SIGNAL_TERMS = {
    "New Solution / Capability": [
        "launch", "launched", "launches", "platform", "solution", "partnership",
        "payments", "open banking", "blockchain", "artificial intelligence",
        " ai ", "cash management", "transaction banking", "treasury", "fintech",
        "digital", "api", "wallet", "instant payment", "merchant acquiring",
    ],
    "New Market / Client Pool": [
        "investment", "expansion", "new market", "new sector", "logistics",
        "healthcare", "tourism", "manufacturing", "data center", "data centre",
        "cloud", "technology", "fintech", "wealth", "venture capital", "office",
        "headquarters", "free zone", "qfc", "lusail",
    ],
    "Major Client / Deal Opportunity": [
        "project", "contract", "awarded", "investment", "expansion", "acquisition",
        "financing", "facility", "infrastructure", "joint venture", "plant",
        "construction", "development", "capex", "tender", "procurement",
    ],
    "Strategic Threat / Disruption": [
        "challenger", "neobank", "new entrant", "market entry", "fintech",
        "stablecoin", "blockchain", "open banking", "digital bank", "payments",
        "disruption", "licence", "license",
    ],
    "White-Space Opportunity": [
        "embedded finance", "open finance", "instant payments", "b2b payments",
        "sme", "wealthtech", "insurtech", "supply chain finance", "trade platform",
        "ecosystem", "marketplace", "api", "platform",
    ],
}


def theme_signal_score(category, title, summary):
    combined = f"{title} {summary}".lower()
    score = 0

    for term in THEME_SIGNAL_TERMS.get(category, []):
        if term in combined:
            score += 14

    if "qatar" in combined or "doha" in combined:
        score += 35
    elif any(x in combined for x in ["gcc", "saudi", "uae", "kuwait", "bahrain", "oman"]):
        score += 20

    # Penalise generic macro and soft content.
    weak = [
        "forecast", "outlook", "gdp growth", "inflation", "oil prices",
        "award", "awards", "sponsorship", "csr", "conference", "event",
        "webinar", "opinion", "interview"
    ]
    if any(x in combined for x in weak):
        score -= 30

    return score


def fetch_thematic_intelligence(max_per_search=12):
    """
    Run several simple Google News RSS searches per strategic theme.

    Simple searches are deliberately used instead of one long Boolean query:
    Google News RSS handles them much more reliably.
    """
    items = []
    seen = set()

    for cfg in THEMATIC_INTELLIGENCE_SEARCHES:
        category = cfg["category"]
        url = google_news_rss_url(cfg["query"], days=30)
        base_priority = int(cfg["priority"])

        try:
            feed = feedparser.parse(url)

            if getattr(feed, "bozo", False):
                print(f"WARNING: Google News feed issue for '{cfg['query']}': {getattr(feed, 'bozo_exception', '')}")

            for entry in feed.entries[:max_per_search]:
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

                combined = f"{title} {summary}".lower()

                # Require Qatar/GCC relevance, but do not require the exact word
                # "Qatar" if the item is clearly from a GCC market.
                regional = (
                    "qatar" in combined
                    or "doha" in combined
                    or any(x in combined for x in [
                        "gcc", "saudi", "riyadh", "uae", "dubai", "abu dhabi",
                        "kuwait", "bahrain", "oman", "muscat"
                    ])
                )
                if not regional:
                    continue

                signal = theme_signal_score(category, title, summary)

                # Lower threshold: the AI will perform the second-stage strategic
                # test. The fetch layer should discover, not over-filter.
                if signal < 5:
                    continue

                seen.add(key)
                published_raw = entry.get("published", "") or entry.get("updated", "")

                geography = (
                    "Qatar"
                    if ("qatar" in combined or "doha" in combined)
                    else "GCC"
                )

                items.append({
                    "title": title,
                    "summary": summary[:1200],
                    "link": link,
                    "source": source_name,
                    "source_date": format_source_date(published_raw),
                    "geography": geography,
                    "source_type": "thematic_search_result",
                    "intelligence_category": category,
                    "_priority_score": base_priority + signal,
                })

        except Exception as e:
            print(f"WARNING: Thematic search failed for '{cfg['query']}'. Error: {e}")

    items.sort(key=lambda x: x.get("_priority_score", 0), reverse=True)
    return items


COMPETITOR_SEARCHES = [
    {
        "bank": "Dukhan Bank",
        "query": '"Dukhan Bank" (payments OR blockchain OR Kinexys OR "open banking" OR fintech OR partnership OR launch OR treasury OR "cash management" OR "transaction banking" OR financing OR sukuk OR wealth)',
        "priority": 260,
    },
    {
        "bank": "QNB",
        "query": '"QNB" Qatar (payments OR fintech OR partnership OR launch OR treasury OR "cash management" OR "transaction banking" OR financing OR sukuk OR wealth OR AI)',
        "priority": 245,
    },
    {
        "bank": "Qatar Islamic Bank",
        "query": '"Qatar Islamic Bank" OR QIB Qatar (payments OR fintech OR partnership OR launch OR treasury OR "cash management" OR financing OR wealth OR AI)',
        "priority": 245,
    },
    {
        "bank": "Commercial Bank Qatar",
        "query": '"Commercial Bank" Qatar (payments OR fintech OR partnership OR launch OR treasury OR "cash management" OR financing OR wealth OR AI)',
        "priority": 245,
    },
    {
        "bank": "Masraf Al Rayan",
        "query": '"Masraf Al Rayan" (payments OR fintech OR partnership OR launch OR treasury OR financing OR wealth OR digital)',
        "priority": 240,
    },
    {
        "bank": "QIIB",
        "query": '"QIIB" Qatar OR "Qatar International Islamic Bank" (payments OR fintech OR partnership OR launch OR treasury OR financing OR wealth OR digital)',
        "priority": 240,
    },
    {
        "bank": "Ahlibank Qatar",
        "query": '"Ahlibank Qatar" (payments OR fintech OR partnership OR launch OR treasury OR financing OR wealth OR digital)',
        "priority": 235,
    },
]



def clean_google_news_title(title, source_name=""):
    """
    Google News RSS titles often end with " - Publisher".
    Remove that publisher suffix so the email displays only the real headline.
    """
    title = clean_text(title)
    source_name = clean_text(source_name)

    if source_name:
        patterns = [
            rf"\s+-\s+{re.escape(source_name)}\s*$",
            rf"\s+–\s+{re.escape(source_name)}\s*$",
            rf"\s+—\s+{re.escape(source_name)}\s*$",
        ]
        for pattern in patterns:
            title = re.sub(pattern, "", title, flags=re.I).strip()

    return title


def google_news_rss_url(query, days=14):
    q = f"{query} when:{days}d"
    return (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(q)
        + "&hl=en&gl=QA&ceid=QA:en"
    )


def fetch_competitor_search_news(max_per_bank=15):
    """
    Search-index fallback using Google News RSS.

    This catches official/press coverage even when a bank's newsroom listing page
    is rendered with JavaScript and cannot be discovered by the lightweight HTML parser.
    """
    items = []
    seen = set()

    for cfg in COMPETITOR_SEARCHES:
        bank = cfg["bank"]
        url = google_news_rss_url(cfg["query"], days=14)
        base_priority = int(cfg["priority"])

        try:
            feed = feedparser.parse(url)

            for entry in feed.entries[:max_per_bank]:
                raw_title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))
                link = clean_text(entry.get("link", ""))

                if not title or not link:
                    continue

                key = dedupe_key(link, title)
                if key in seen:
                    continue
                seen.add(key)

                combined = f"{title} {summary}".lower()

                # Ensure the result is really about the named competitor.
                bank_tokens = [
                    bank.lower(),
                    bank.lower().replace(" qatar", ""),
                ]
                if bank == "Qatar Islamic Bank":
                    bank_tokens += ["qib"]
                elif bank == "Commercial Bank Qatar":
                    bank_tokens += ["commercial bank"]
                elif bank == "QIIB":
                    bank_tokens += ["qatar international islamic bank"]

                if not any(token and token in combined for token in bank_tokens):
                    continue

                strategic_score = competitor_relevance_score(title, summary)

                # Keep only meaningful commercial/competitive moves.
                if strategic_score < 18:
                    continue

                source_name = "Google News"
                source_obj = entry.get("source")
                if isinstance(source_obj, dict):
                    source_name = clean_text(source_obj.get("title", "")) or source_name

                title = clean_google_news_title(raw_title, source_name)
                published_raw = entry.get("published", "") or entry.get("updated", "")

                items.append({
                    "title": title,
                    "summary": summary[:1200],
                    "link": link,
                    "source": source_name,
                    "source_date": format_source_date(published_raw),
                    "geography": "Qatar",
                    "competitor_bank": bank,
                    "source_type": "competitor_search_result",
                    "_priority_score": base_priority + strategic_score,
                })

        except Exception as e:
            print(f"WARNING: Competitor search failed for {bank}. Error: {e}")

    items.sort(key=lambda x: x.get("_priority_score", 0), reverse=True)
    return items


COMPETITOR_STRATEGIC_TERMS = [
    "kinexys", "blockchain", "deposit account", "open banking", "fintech",
    "digital banking", "digital", "payments", "payment", "cross-border",
    "cash management", "transaction banking", "trade finance", "treasury",
    "corporate banking", "wholesale banking", "partnership", "strategic partnership",
    "agreement", "signs", "launch", "launches", "ai", "artificial intelligence",
    "cybersecurity", "cloud", "api", "instant payment", "wallet", "wealth",
    "asset management", "capital markets", "bond", "sukuk", "qcb",
    "liquidity", "remittance", "acquiring", "merchant", "visa", "mastercard",
    "j.p. morgan", "jp morgan", "jpmorgan", "kinexys by j.p. morgan"
]


class _NewsroomHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._anchor_parts = []
        self.title = ""
        self._in_title = False
        self.meta_description = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "a":
            self._href = attrs.get("href")
            self._anchor_parts = []

        if tag == "title":
            self._in_title = True

        if tag == "meta":
            name = (attrs.get("name") or attrs.get("property") or "").lower()
            if name in ("description", "og:description") and not self.meta_description:
                self.meta_description = clean_text(attrs.get("content", ""))

    def handle_data(self, data):
        if self._href is not None:
            self._anchor_parts.append(data)
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            anchor_text = clean_text(" ".join(self._anchor_parts))
            self.links.append((self._href, anchor_text))
            self._href = None
            self._anchor_parts = []

        if tag == "title":
            self._in_title = False


def fetch_html(url, timeout=18):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; DBStrategyWeekly/1.0; "
            "+https://github.com/MarketSigma)"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    req = Request(url, headers=headers)

    with urlopen(req, timeout=timeout) as response:
        raw = response.read()

    # Most monitored sites are UTF-8; errors='replace' keeps the workflow alive
    # if a page contains a malformed byte.
    return raw.decode("utf-8", errors="replace")


def extract_page_text(raw_html):
    # Lightweight text extraction without adding another pip dependency.
    text = re.sub(r"(?is)<script.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return clean_text(text)


def extract_article_date(page_text):
    patterns = [
        r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2})\b",
        r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2})\b",
        r"\b(\d{1,2}/\d{1,2}/20\d{2})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, page_text, flags=re.I)
        if match:
            return clean_text(match.group(1))

    return ""


def competitor_relevance_score(title, summary):
    combined = f"{title} {summary}".lower()

    score = 0
    for term in COMPETITOR_STRATEGIC_TERMS:
        if term in combined:
            score += 18

    # Strategic competitor moves should outrank awards, CSR and campaigns.
    if any(x in combined for x in [
        "award", "awards", "prize", "promotion", "campaign",
        "community", "charity", "ramadan", "travel with confidence"
    ]):
        score -= 35

    return score


def fetch_competitor_news(max_per_bank=12):
    """
    Pull recent announcement links directly from official Qatar-bank newsrooms.

    These items receive a strong priority score because a direct local competitor
    move can be strategically important even when it has not yet appeared in RSS.
    """
    items = []
    seen = set()

    for source in COMPETITOR_NEWSROOMS:
        bank = source["bank"]
        listing_url = source["listing_url"]
        link_contains = source["link_contains"]
        base_priority = int(source["priority"])

        try:
            listing_html = fetch_html(listing_url)
            parser = _NewsroomHTMLParser()
            parser.feed(listing_html)

            candidates = []
            candidate_seen = set()

            for href, anchor_text in parser.links:
                if not href:
                    continue

                absolute_url = urljoin(listing_url, href)
                normalized = absolute_url.split("#")[0].split("?")[0].rstrip("/")

                if link_contains not in absolute_url:
                    continue
                if normalized in candidate_seen:
                    continue
                if normalized.rstrip("/") == listing_url.rstrip("/"):
                    continue

                candidate_seen.add(normalized)
                candidates.append((absolute_url, anchor_text))

                if len(candidates) >= max_per_bank:
                    break

            for article_url, anchor_text in candidates:
                try:
                    article_html = fetch_html(article_url)
                    article_parser = _NewsroomHTMLParser()
                    article_parser.feed(article_html)

                    page_text = extract_page_text(article_html)
                    title = clean_text(anchor_text)

                    # Use the HTML title if the listing anchor is generic.
                    html_title = clean_text(article_parser.title)
                    if len(title) < 12 or title.lower() in ("read more", "learn more", "news"):
                        title = html_title

                    # Strip common site-title suffixes.
                    title = re.sub(
                        r"\s*[\|\-–—]\s*(Dukhan Bank|QNB|Qatar Islamic Bank|QIB|Commercial Bank).*$",
                        "",
                        title,
                        flags=re.I,
                    ).strip()

                    description = clean_text(article_parser.meta_description)
                    if not description:
                        # First useful portion of the article page is sufficient
                        # for Claude to evaluate strategic relevance.
                        description = page_text[:900]

                    if not title:
                        continue

                    key = dedupe_key(article_url, title)
                    if key in seen:
                        continue
                    seen.add(key)

                    strategic_score = competitor_relevance_score(title, description)
                    published = extract_article_date(page_text)

                    items.append({
                        "title": title,
                        "summary": description[:1200],
                        "link": article_url,
                        "source": f"{bank} — Official Newsroom",
                        "source_date": published,
                        "geography": "Qatar",
                        "competitor_bank": bank,
                        "source_type": "official_competitor_announcement",
                        "_priority_score": base_priority + strategic_score,
                    })

                except Exception as article_error:
                    print(
                        f"WARNING: Could not read competitor article from {bank}: "
                        f"{article_url}. Error: {article_error}"
                    )

        except Exception as e:
            print(
                f"WARNING: Failed competitor newsroom: {bank} ({listing_url}). "
                f"Error: {e}"
            )

    items.sort(key=lambda x: x.get("_priority_score", 0), reverse=True)
    return items


BLOCKED_TERMS = [
    "world cup", "football", "soccer", "sports", "match", "tournament",
    "weather", "sky turns", "episode", "podcast", "celebrity", "movie",
    "music", "travel", "recipe", "cricket", "tennis", "golf"
]

REQUIRED_TERMS = [
    "bank", "banks", "banking", "fintech", "payments", "payment",
    "transaction banking", "cash management", "treasury", "trade finance",
    "project finance", "corporate banking", "wholesale banking",
    "digital banking", "open banking", "blockchain", "kinexys",
    "artificial intelligence", " ai ", "api", "wallet", "remittance",
    "merchant", "acquiring", "wealth", "asset management", "sukuk", "bond",
    "partnership", "agreement", "launch", "launches", "expansion",
    "investment", "acquisition", "funding", "financing", "credit",
    "deposit", "liquidity", "infrastructure", "project", "real estate",
    "lng", "energy", "qatar", "doha", "gcc", "gulf", "saudi", "uae",
    "kuwait", "bahrain", "oman", "qcb", "qatarenergy", "qia",
    "qatar stock exchange", "free zone", "qfc", "logistics", "data center",
    "data centre", "healthcare", "tourism", "manufacturing", "sme"
]

CATEGORY_RULES = [
    {
        "topic_id": "1",
        "category": "Competitor Move",
        "description": "A concrete move by a Qatar or GCC bank/fintech that could change competitive positioning, customer expectations, pricing, distribution, payments, transaction banking, digital capability or product depth."
    },
    {
        "topic_id": "2",
        "category": "New Solution / Capability",
        "description": "A newly launched banking, payments, AI, fintech, treasury, cash-management, blockchain, open-banking, wealth, SME, trade-finance or digital capability that Doha Bank could adopt, partner for, or build."
    },
    {
        "topic_id": "3",
        "category": "New Market / Client Pool",
        "description": "An emerging Qatar/GCC sector, geography, client segment, project pipeline or investment theme creating identifiable revenue pools for corporate, wholesale, retail, treasury, wealth or transaction banking."
    },
    {
        "topic_id": "4",
        "category": "Major Client / Deal Opportunity",
        "description": "A specific corporate expansion, government project, investment programme, infrastructure development, acquisition, financing need, trade corridor or large transaction that could create lending, deposits, payments, advisory, treasury or fee opportunities."
    },
    {
        "topic_id": "5",
        "category": "Strategic Threat / Disruption",
        "description": "A competitor, technology, regulatory, funding, market-structure or business-model development that could erode Doha Bank revenue, margins, deposits, clients, fees or strategic relevance."
    },
    {
        "topic_id": "6",
        "category": "White-Space Opportunity",
        "description": "A commercially credible gap where peers are moving, clients have an unmet need, or a new ecosystem is forming and Doha Bank could differentiate with a new proposition, partnership or market entry."
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


BROAD_STRATEGIC_SEARCHES = [
    "Qatar banking partnership launch",
    "Qatar fintech payments",
    "Qatar company expansion investment",
    "Qatar project contract awarded",
    "QatarEnergy project contract",
    "Qatar new business investment",
    "Qatar corporate financing",
    "Qatar technology investment",
    "GCC bank fintech partnership",
    "GCC payments banking launch",
]


def fetch_broad_strategic_news(max_per_search=12):
    items = []
    seen = set()

    for query in BROAD_STRATEGIC_SEARCHES:
        try:
            feed = feedparser.parse(google_news_rss_url(query, days=30))

            for entry in feed.entries[:max_per_search]:
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
                seen.add(key)

                combined = f"{title} {summary}".lower()
                if not (
                    "qatar" in combined
                    or "doha" in combined
                    or any(x in combined for x in ["gcc", "saudi", "uae", "kuwait", "bahrain", "oman"])
                ):
                    continue

                if any(term in combined for term in BLOCKED_TERMS):
                    continue

                published_raw = entry.get("published", "") or entry.get("updated", "")
                geography = "Qatar" if ("qatar" in combined or "doha" in combined) else "GCC"

                items.append({
                    "title": title,
                    "summary": summary[:1200],
                    "link": link,
                    "source": source_name,
                    "source_date": format_source_date(published_raw),
                    "geography": geography,
                    "source_type": "broad_strategic_search",
                    "_priority_score": 150 + (35 if geography == "Qatar" else 20),
                })

        except Exception as e:
            print(f"WARNING: Broad strategic search failed for '{query}'. Error: {e}")

    items.sort(key=lambda x: x.get("_priority_score", 0), reverse=True)
    return items



QATAR_STRONG_TERMS = [
    "qatar", "doha", "qcb", "qatar central bank", "qatarenergy",
    "qatar energy", "qatar investment authority", "qia", "qatar stock exchange",
    "qfc", "qatar financial centre", "qatar financial center", "lusail",
    "ras laffan", "mesaied", "hamad port", "hamad international airport",
    "doha bank", "qnb", "qib", "dukhan bank", "commercial bank qatar",
    "masraf al rayan", "qiib", "ahlibank qatar"
]

GCC_STRONG_TERMS = [
    "gcc", "gulf cooperation council", "saudi arabia", "saudi", "riyadh",
    "uae", "united arab emirates", "dubai", "abu dhabi", "kuwait",
    "bahrain", "oman", "muscat"
]


def classify_region(item):
    """Return Qatar, GCC, or Global using title + summary + source metadata."""
    combined = " ".join([
        clean_text(item.get("title", "")),
        clean_text(item.get("summary", "")),
        clean_text(item.get("source", "")),
        clean_text(item.get("competitor_bank", "")),
    ]).lower()

    if any(term in combined for term in QATAR_STRONG_TERMS):
        return "Qatar"

    if any(term in combined for term in GCC_STRONG_TERMS):
        return "GCC"

    region = clean_text(item.get("geography", "")).lower()
    if region == "qatar":
        return "Qatar"
    if region == "gcc":
        return "GCC"

    return "Global"


def regional_priority(item):
    region = classify_region(item)

    if region == "Qatar":
        base = 300
    elif region == "GCC":
        base = 180
    else:
        base = 0

    source_type = item.get("source_type", "")
    if source_type == "official_competitor_announcement":
        base += 80
    elif source_type == "competitor_search_result":
        base += 65
    elif source_type == "thematic_search_result":
        base += 50
    elif source_type == "broad_strategic_search":
        base += 30

    return base


def build_regional_candidate_pool(news_items, max_items=100):
    """
    Hard regional filter for the weekly briefing.

    Qatar is always first, GCC second. Global items are excluded from the AI pool
    unless there are not enough real Qatar/GCC items to produce six options.
    """
    qatar = []
    gcc = []
    global_items = []

    for item in news_items:
        region = classify_region(item)
        enriched = dict(item)
        enriched["geography"] = region
        enriched["_regional_priority"] = regional_priority(enriched)

        if region == "Qatar":
            qatar.append(enriched)
        elif region == "GCC":
            gcc.append(enriched)
        else:
            global_items.append(enriched)

    qatar.sort(key=lambda x: x.get("_regional_priority", 0), reverse=True)
    gcc.sort(key=lambda x: x.get("_regional_priority", 0), reverse=True)
    global_items.sort(key=lambda x: x.get("_regional_priority", 0), reverse=True)

    # Normal case: give Claude ONLY Qatar/GCC.
    pool = qatar + gcc

    # Only if the regional universe is genuinely too thin do we allow a small
    # number of global stories into the tail of the pool.
    if len(pool) < 12:
        pool += global_items[: max(0, 12 - len(pool))]

    pool = pool[:max_items]

    for item in pool:
        item.pop("_regional_priority", None)

    print(
        f"REGIONAL POOL | Qatar={len(qatar)} | GCC={len(gcc)} | "
        f"Global={len(global_items)} | supplied_to_AI={len(pool)}"
    )

    return pool



def fetch_news(max_items=140, sources_path="news_sources.json"):
    items = []
    seen = set()

    # 1) Direct Qatar competitor monitoring comes first.
    competitor_items = fetch_competitor_news()
    print(f"Direct competitor monitor found {len(competitor_items)} announcement candidates.")
    for ci in competitor_items[:10]:
        print(
            "DIRECT COMPETITOR | "
            f"score={commercial_signal_score(ci)} | "
            f"{ci.get('competitor_bank', '')} | {ci.get('title', '')}"
        )

    for item in competitor_items:
        key = dedupe_key(item.get("link"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    # Search-index fallback for JS-rendered bank newsrooms.
    competitor_search_items = fetch_competitor_search_news()
    print(f"Competitor search fallback found {len(competitor_search_items)} candidates.")
    for ci in competitor_search_items[:15]:
        print(
            "SEARCH COMPETITOR | "
            f"score={commercial_signal_score(ci)} | "
            f"{ci.get('competitor_bank', '')} | {ci.get('title', '')}"
        )

    for item in competitor_search_items:
        key = dedupe_key(item.get("link"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    # 2) Search-based opportunity / solution / market / threat discovery.
    thematic_items = fetch_thematic_intelligence()
    print(f"Thematic intelligence search found {len(thematic_items)} candidates.")

    theme_counts = {}
    for ti in thematic_items:
        cat = ti.get("intelligence_category", "Unknown")
        theme_counts[cat] = theme_counts.get(cat, 0) + 1
    print(f"Thematic candidate mix: {theme_counts}")

    for ti in thematic_items[:25]:
        print(
            "THEMATIC CANDIDATE | "
            f"{ti.get('intelligence_category', '')} | "
            f"{ti.get('title', '')}"
        )

    for item in thematic_items:
        key = dedupe_key(item.get("link"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    # 3) Broad strategic discovery backstop.
    broad_items = fetch_broad_strategic_news()
    print(f"Broad strategic search found {len(broad_items)} candidates.")

    for bi in broad_items[:20]:
        print(f"BROAD CANDIDATE | {bi.get('geography', '')} | {bi.get('title', '')}")

    for item in broad_items:
        key = dedupe_key(item.get("link"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    # 4) Broader Qatar/GCC/global RSS coverage.
    sources = load_news_sources(sources_path)

    for source_cfg in sources:
        url = source_cfg["rss"]
        source_region = source_cfg.get("region", "global")
        source_priority = int(source_cfg.get("priority", 0))

        try:
            feed = feedparser.parse(url)
            feed_source_name = (
                clean_text(source_cfg.get("name"))
                or clean_text(feed.feed.get("title", ""))
                or source_name_from_url(url)
            )

            for entry in feed.entries[:40]:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))
                link = entry.get("link", "")

                if not title or not link:
                    continue

                key = dedupe_key(link, title)
                if key in seen:
                    continue

                if not is_relevant_news(title, summary):
                    continue

                seen.add(key)

                published_raw = entry.get("published", "") or entry.get("updated", "")
                geo_focus, geo_score = geographic_focus(title, summary, source_region)

                items.append({
                    "title": title,
                    "summary": summary[:1200],
                    "link": link,
                    "source": feed_source_name,
                    "source_date": format_source_date(published_raw),
                    "geography": geo_focus,
                    "source_type": "rss",
                    "_priority_score": source_priority + geo_score,
                })

        except Exception as e:
            print(f"WARNING: Failed RSS source: {url}. Error: {e}")
            continue

    # Strong regional ordering: Qatar first, GCC second, global last.
    for item in items:
        item["geography"] = classify_region(item)

    items.sort(
        key=lambda x: (
            2 if x.get("geography") == "Qatar" else 1 if x.get("geography") == "GCC" else 0,
            x.get("_priority_score", 0),
        ),
        reverse=True,
    )

    trimmed = items[:max_items]
    for item in trimmed:
        item.pop("_priority_score", None)

    competitor_count = sum(
        1 for x in trimmed
        if x.get("source_type") in ("official_competitor_announcement", "competitor_search_result")
    )
    qatar_count = sum(1 for x in trimmed if x.get("geography") == "Qatar")
    gcc_count = sum(1 for x in trimmed if x.get("geography") == "GCC")
    global_count = sum(1 for x in trimmed if x.get("geography") == "Global")

    print(
        "News mix supplied to AI: "
        f"Competitor={competitor_count}, Qatar={qatar_count}, "
        f"GCC={gcc_count}, Global={global_count}"
    )

    return trimmed


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


def commercial_signal_score(item):
    """Deterministic commercial-intelligence score used before Claude."""
    title = clean_text(item.get("title", ""))
    summary = clean_text(item.get("summary", ""))
    combined = f"{title} {summary}".lower()

    score = 0

    # Strong competitor / capability signals.
    strong_terms = {
        "kinexys": 45,
        "blockchain": 30,
        "open banking": 30,
        "transaction banking": 30,
        "cash management": 28,
        "cross-border": 28,
        "payments": 24,
        "payment": 20,
        "treasury": 22,
        "trade finance": 22,
        "project finance": 22,
        "digital banking": 20,
        "fintech": 20,
        "artificial intelligence": 20,
        " ai ": 18,
        "api": 18,
        "wallet": 16,
        "wealth": 16,
        "asset management": 16,
        "partnership": 18,
        "agreement": 16,
        "launch": 18,
        "launches": 18,
        "go live": 25,
        "goes live": 25,
        "first islamic bank": 35,
        "first bank": 30,
        "qatar's first": 35,
        "qatar’s first": 35,
        "new market": 18,
        "expansion": 18,
        "data centre": 18,
        "data center": 18,
        "sukuk": 16,
        "facility": 16,
        "financing": 18,
    }

    for term, weight in strong_terms.items():
        if term in combined:
            score += weight

    if item.get("source_type") == "official_competitor_announcement":
        score += 45
    elif item.get("source_type") == "competitor_search_result":
        score += 28

    if item.get("geography") == "Qatar":
        score += 35
    elif item.get("geography") == "GCC":
        score += 20

    # Down-rank low-strategy PR.
    weak_terms = [
        "award", "awards", "winner", "prize", "cash bonus", "campaign",
        "sponsorship", "community", "charity", "children", "harrods"
    ]
    if any(term in combined for term in weak_terms):
        score -= 80

    return score


def best_competitor_move(news_items):
    candidates = []

    for item in news_items:
        if item.get("source_type") not in (
            "official_competitor_announcement",
            "competitor_search_result",
        ):
            continue

        score = commercial_signal_score(item)

        # Search-index results need a slightly lower threshold because they may
        # only carry a headline + short snippet, but must still be high signal.
        threshold = 80 if item.get("source_type") == "official_competitor_announcement" else 60

        if score >= threshold:
            candidates.append((score, item))

    if not candidates:
        return None

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def competitor_topic_from_item(item):
    """Create a real Topic 1 from the strongest verified competitor announcement."""
    title = clean_text(item.get("title", ""))
    summary = article_excerpt(item.get("summary", ""), 420)
    bank = clean_text(item.get("competitor_bank", "")) or clean_text(item.get("source", "Qatar competitor"))

    combined = f"{title} {summary}".lower()

    if "kinexys" in combined or "blockchain" in combined:
        what_new = (
            f"{bank} has introduced a new blockchain-enabled deposit and settlement capability "
            "with J.P. Morgan's Kinexys network."
        )
        revenue_pool = (
            "Large-corporate transaction banking, cross-border payments, treasury and liquidity-management flows."
        )
        strategy_test = (
            "Assess demand among Doha Bank's top corporate clients for 24/7 cross-border settlement and "
            "determine whether an equivalent partner-led capability should be pursued."
        )
        angle = (
            "This raises the competitive benchmark for Qatar corporate transaction banking and could influence "
            "where large clients place payments, liquidity-management and treasury activity."
        )
    elif "open banking" in combined:
        what_new = f"{bank} has moved into a new open-banking capability or ecosystem partnership."
        revenue_pool = "Digital payments, embedded finance, account connectivity and corporate/retail API services."
        strategy_test = (
            "Map the highest-value open-banking use cases for Doha Bank and identify whether to build, partner or pilot."
        )
        angle = (
            "A local competitor is moving beyond conventional digital channels into ecosystem-based banking services."
        )
    else:
        what_new = f"{bank} has announced a strategically relevant new product, partnership or market move."
        revenue_pool = (
            "The affected client wallet depends on the proposition, with potential implications for lending, deposits, "
            "payments, treasury, fee income or customer acquisition."
        )
        strategy_test = (
            "Benchmark the proposition against Doha Bank's current offer and identify the client segments where a response "
            "could protect or create revenue."
        )
        angle = (
            "The move is concrete, local and potentially relevant to Doha Bank's competitive position."
        )

    return {
        "topic_id": "1",
        "category": "Competitor Move",
        "title": title,
        "source_title": title,
        "source_name": clean_text(item.get("source", "")),
        "source_url": clean_text(item.get("link", "")),
        "source_date": clean_text(item.get("source_date", "")) or TODAY,
        "source_excerpt": summary,
        "why_it_matters": angle,
        "potential_doha_bank_angle": angle,
        "what_is_new": what_new,
        "named_rival_or_actor": bank,
        "target_client_or_market": "Qatar corporate and transaction-banking clients",
        "revenue_pool": revenue_pool,
        "recommended_strategy_test": strategy_test,
        "novelty_score": 5,
        "competitive_intensity_score": 5,
        "revenue_pool_score": 5,
        "actionability_score": 5,
        "qatar_gcc_relevance_score": 5,
    }


def best_theme_item(news_items, category):
    candidates = []

    for item in news_items:
        if item.get("source_type") != "thematic_search_result":
            continue
        if item.get("intelligence_category") != category:
            continue

        score = theme_signal_score(
            category,
            item.get("title", ""),
            item.get("summary", ""),
        )

        if score >= 25:
            candidates.append((score, item))

    if not candidates:
        return None

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def topic_from_theme_item(topic_id, category, item):
    title = clean_text(item.get("title", ""))
    summary = article_excerpt(item.get("summary", ""), 420)
    source = clean_text(item.get("source", "News source"))

    if category == "New Solution / Capability":
        what_new = "A concrete new banking, fintech or payments capability has entered the Qatar/GCC market."
        revenue_pool = "Potential product, payments, transaction-banking, treasury or fee-income opportunity."
        strategy_test = "Benchmark the capability against Doha Bank's current proposition and identify one pilot use case and target client segment."
        angle = "The development may reset customer expectations or create a capability gap that Doha Bank can close through build, buy or partnership."

    elif category == "New Market / Client Pool":
        what_new = "A specific Qatar/GCC market or client segment is expanding or attracting new investment."
        revenue_pool = "Potential lending, deposits, transaction banking, treasury, payments, wealth or advisory wallet from the emerging client pool."
        strategy_test = "Size the addressable client pool, map the top prospects and determine the first proposition Doha Bank should take to market."
        angle = "The development points to an identifiable new revenue pool rather than broad macro growth."

    elif category == "Major Client / Deal Opportunity":
        what_new = "A specific project, expansion, investment or transaction is creating an identifiable financing or banking need."
        revenue_pool = "Potential lending, project finance, trade finance, deposits, cash management, treasury and advisory wallet."
        strategy_test = "Identify the sponsor, counterparties and funding/payment needs, then assign coverage to test Doha Bank's realistic share of wallet."
        angle = "This is a concrete commercial opportunity tied to a named development rather than a generic sector theme."

    elif category == "Strategic Threat / Disruption":
        what_new = "A structural competitive or technology development could shift client behaviour or economics in Qatar/GCC banking."
        revenue_pool = "Potentially at-risk client relationships, deposits, payments flows, fees or product relevance."
        strategy_test = "Identify the most exposed customer journeys or revenue lines and test a defensive or counter-positioning response."
        angle = "The development could change how customers choose providers or move financial flows."

    else:  # White-Space Opportunity
        what_new = "An emerging ecosystem or unmet client need suggests a credible proposition gap in the Qatar/GCC market."
        revenue_pool = "Potential fee, payments, lending, deposits or ecosystem revenue from an underserved use case."
        strategy_test = "Define the unmet need, target segment, partner ecosystem and a low-cost pilot to validate willingness to adopt."
        angle = "The development suggests a differentiated opportunity that is not simply conventional product expansion."

    return {
        "topic_id": str(topic_id),
        "category": category,
        "title": title,
        "source_title": title,
        "source_name": source,
        "source_url": clean_text(item.get("link", "")),
        "source_date": clean_text(item.get("source_date", "")) or TODAY,
        "source_excerpt": summary,
        "why_it_matters": angle,
        "potential_doha_bank_angle": angle,
        "what_is_new": what_new,
        "named_rival_or_actor": "",
        "target_client_or_market": "Qatar/GCC target clients relevant to the development",
        "revenue_pool": revenue_pool,
        "recommended_strategy_test": strategy_test,
        "novelty_score": 4,
        "competitive_intensity_score": 3,
        "revenue_pool_score": 4,
        "actionability_score": 4,
        "qatar_gcc_relevance_score": 5,
    }


def deterministic_category_topics(news_items):
    out = {}

    competitor = best_competitor_move(news_items)
    if competitor:
        out["1"] = competitor_topic_from_item(competitor)

    category_map = {
        "2": "New Solution / Capability",
        "3": "New Market / Client Pool",
        "4": "Major Client / Deal Opportunity",
        "5": "Strategic Threat / Disruption",
        "6": "White-Space Opportunity",
    }

    for tid, category in category_map.items():
        item = best_theme_item(news_items, category)
        if item:
            out[tid] = topic_from_theme_item(tid, category, item)

    return out



def fallback_topics(news_items):
    strongest_competitor = best_competitor_move(news_items)

    if strongest_competitor:
        topic_1 = competitor_topic_from_item(strongest_competitor)
    else:
        topic_1 = {
            "topic_id": "1",
            "category": "Competitor Move",
            "title": "No sufficiently material Qatar/GCC competitor move identified this cycle",
            "source_title": "Competitive-intelligence scan",
            "source_name": "DB Strategy Intelligence Filter",
            "source_url": "#",
            "source_date": TODAY,
            "source_excerpt": "No sufficiently material competitor announcement passed the commercial relevance threshold.",
            "why_it_matters": "The briefing should not manufacture significance when there is no material competitive move.",
            "potential_doha_bank_angle": "Maintain watch on payments, transaction banking, digital, wealth, SME and corporate propositions.",
            "what_is_new": "",
            "named_rival_or_actor": "",
            "target_client_or_market": "",
            "revenue_pool": "",
            "recommended_strategy_test": "",
        }

    return [
        topic_1,
        {
            "topic_id": "2",
            "category": "New Solution / Capability",
            "title": "No sufficiently material new banking solution identified this cycle",
            "source_title": "Solution-intelligence scan",
            "source_name": "DB Strategy Intelligence Filter",
            "source_url": "#",
            "source_date": TODAY,
            "source_excerpt": "No new solution passed the threshold for likely customer, revenue or operating-model impact.",
            "why_it_matters": "Only capabilities with a credible strategic or commercial implication should reach management.",
            "potential_doha_bank_angle": "Continue scanning payments, AI, open banking, blockchain, treasury and digital propositions."
        },
        {
            "topic_id": "3",
            "category": "New Market / Client Pool",
            "title": "No sufficiently specific new Qatar/GCC revenue pool identified this cycle",
            "source_title": "Market-opportunity scan",
            "source_name": "DB Strategy Intelligence Filter",
            "source_url": "#",
            "source_date": TODAY,
            "source_excerpt": "No market development was specific enough to identify an actionable new client or revenue pool.",
            "why_it_matters": "Broad macro growth is not enough; the opportunity must map to a client segment and banking need.",
            "potential_doha_bank_angle": "Keep focus on sectors where lending, deposits, transaction banking, treasury or advisory demand can be identified."
        },
        {
            "topic_id": "4",
            "category": "Major Client / Deal Opportunity",
            "title": "No sufficiently actionable major deal opportunity identified this cycle",
            "source_title": "Deal-opportunity scan",
            "source_name": "DB Strategy Intelligence Filter",
            "source_url": "#",
            "source_date": TODAY,
            "source_excerpt": "No announced project, expansion or transaction passed the actionability threshold.",
            "why_it_matters": "Management attention should go to named projects, sectors or counterparties with plausible banking-wallet potential.",
            "potential_doha_bank_angle": "Continue monitoring major Qatar/GCC capex, acquisitions, government projects and corporate expansions."
        },
        {
            "topic_id": "5",
            "category": "Strategic Threat / Disruption",
            "title": "No material strategic disruption identified this cycle",
            "source_title": "Threat-intelligence scan",
            "source_name": "DB Strategy Intelligence Filter",
            "source_url": "#",
            "source_date": TODAY,
            "source_excerpt": "No development passed the threshold for a credible threat to clients, revenue pools, deposits, fees or competitive relevance.",
            "why_it_matters": "Generic risk commentary is intentionally excluded.",
            "potential_doha_bank_angle": "Monitor structural threats rather than ordinary market volatility."
        },
        {
            "topic_id": "6",
            "category": "White-Space Opportunity",
            "title": "No sufficiently credible white-space opportunity identified this cycle",
            "source_title": "White-space scan",
            "source_name": "DB Strategy Intelligence Filter",
            "source_url": "#",
            "source_date": TODAY,
            "source_excerpt": "No unmet need or emerging ecosystem passed the commercial differentiation threshold.",
            "why_it_matters": "White-space ideas should be evidence-led, not generic brainstorming.",
            "potential_doha_bank_angle": "Continue comparing peer moves with Doha Bank's product and market footprint."
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
    regional_pool = build_regional_candidate_pool(news_items)

    if not regional_pool:
        raise ValueError("No Qatar/GCC intelligence items were discovered.")

    prompt = f"""
You are the competitive-intelligence analyst for the Chief Strategy Officer of {bank_name}.

This briefing is QATAR-FIRST and GCC-SECOND.
It is NOT a global news digest.

Select exactly 6 REAL strategic developments from the supplied candidate pool.

MANDATORY GEOGRAPHY RULES:
- Target 4 Qatar-specific items + 2 GCC items.
- If 4 credible Qatar items are not available, select at least 3 Qatar items and fill the balance with GCC.
- At least 5 of the 6 selected items MUST be Qatar or GCC.
- A Global item may appear only if fewer than 6 credible Qatar/GCC items exist in the supplied pool.
- Never choose a global story over a credible Qatar/GCC story simply because the global story is more prominent.
- Qatar relevance includes Doha, QCB, QatarEnergy, QIA, QFC, Qatar Stock Exchange,
  Qatari banks, major Qatari corporates, government projects and Qatar client sectors.
- GCC relevance includes Saudi Arabia, UAE, Kuwait, Bahrain and Oman only when there is
  a clear competitive, client, funding, technology, payments or market implication for Doha Bank.

STRATEGIC PRIORITY:
1. Named Qatar competitor move.
2. New Qatar banking/fintech/payment/AI/treasury solution.
3. Named Qatar corporate expansion, project, investment or financing need.
4. New Qatar client pool or sector opportunity.
5. GCC competitor/solution that could reasonably migrate into Qatar.
6. GCC project, market or client opportunity relevant to Doha Bank.

AVOID:
- Fed, ECB, US, Europe, China or generic global market stories unless absolutely necessary.
- Generic oil-price, inflation, GDP or rate commentary.
- Broad "regional growth" stories with no named actor, product, project, client pool or opportunity.
- Awards, sponsorships, CSR and routine marketing announcements.
- Duplicate coverage of the same development.

Use the strongest six real items. Categories may repeat if that reflects the real intelligence:
- Competitor Move
- New Solution / Capability
- New Market / Client Pool
- Major Client / Deal Opportunity
- Strategic Threat / Disruption
- White-Space Opportunity

For each item identify:
- what_is_new
- named_rival_or_actor
- target_client_or_market
- revenue_pool
- recommended_strategy_test

Return only a valid JSON array of exactly 6 objects.

Required object:
{{
  "topic_id": "1",
  "category": "...",
  "title": "...",
  "source_title": "...",
  "source_name": "...",
  "source_url": "...",
  "source_date": "...",
  "source_excerpt": "...",
  "why_it_matters": "...",
  "potential_doha_bank_angle": "...",
  "what_is_new": "...",
  "named_rival_or_actor": "...",
  "target_client_or_market": "...",
  "revenue_pool": "...",
  "recommended_strategy_test": "...",
  "novelty_score": 0,
  "competitive_intensity_score": 0,
  "revenue_pool_score": 0,
  "actionability_score": 0,
  "qatar_gcc_relevance_score": 0
}}

Source rules:
- Preserve source_name, source_url and source_date from the selected candidate.
- Never invent a bank, company, product, project or source.
- source_excerpt must be based on the supplied source text.

Regional candidate intelligence:
{json.dumps(regional_pool, ensure_ascii=False)}
"""

    try:
        raw = ask_claude(prompt)
        topics = extract_json_array(raw)

        valid = []
        used_urls = set()

        for t in topics:
            if len(valid) >= 6:
                break

            url = clean_text(t.get("source_url", ""))
            if not url or url == "#":
                continue

            url_key = url.split("?")[0].rstrip("/").lower()
            if url_key in used_urls:
                continue

            # Match selected source back to the regional pool to obtain trusted geography.
            matched = next(
                (
                    x for x in regional_pool
                    if clean_text(x.get("link", "")).split("?")[0].rstrip("/").lower() == url_key
                ),
                None,
            )

            if not matched:
                continue

            geography = classify_region(matched)
            t["geography"] = geography
            used_urls.add(url_key)
            t["topic_id"] = str(len(valid) + 1)
            t["source_title"] = clean_text(t.get("source_title", "")) or clean_text(t.get("title", ""))
            t["source_excerpt"] = article_excerpt(
                t.get("source_excerpt") or matched.get("summary") or t.get("source_title")
            )
            valid.append(t)

        # Enforce a regional composition deterministically.
        qatar_selected = [t for t in valid if t.get("geography") == "Qatar"]
        gcc_selected = [t for t in valid if t.get("geography") == "GCC"]

        # Fill from real regional candidates only.
        if len(valid) < 6 or len(qatar_selected) < 3:
            ranked_pool = sorted(
                regional_pool,
                key=lambda x: (
                    2 if classify_region(x) == "Qatar" else 1 if classify_region(x) == "GCC" else 0,
                    1 if x.get("source_type") in (
                        "official_competitor_announcement",
                        "competitor_search_result",
                        "thematic_search_result",
                    ) else 0,
                ),
                reverse=True,
            )

            for item in ranked_pool:
                if len(valid) >= 6 and len([x for x in valid if x.get("geography") == "Qatar"]) >= 3:
                    break

                url = clean_text(item.get("link", ""))
                url_key = url.split("?")[0].rstrip("/").lower()
                if not url or url_key in used_urls:
                    continue

                geography = classify_region(item)

                # Prefer Qatar until at least 4 are present, then GCC.
                current_qatar = len([x for x in valid if x.get("geography") == "Qatar"])
                if current_qatar < 4 and geography != "Qatar":
                    continue
                if geography not in ("Qatar", "GCC"):
                    continue

                used_urls.add(url_key)

                category = clean_text(item.get("intelligence_category", ""))
                if not category:
                    category = "Competitor Move" if item.get("competitor_bank") else "New Market / Client Pool"

                valid.append({
                    "topic_id": str(len(valid) + 1),
                    "category": category,
                    "title": clean_text(item.get("title", "")),
                    "source_title": clean_text(item.get("title", "")),
                    "source_name": clean_text(item.get("source", "News source")),
                    "source_url": url,
                    "source_date": clean_text(item.get("source_date", "")) or TODAY,
                    "source_excerpt": article_excerpt(item.get("summary", "")),
                    "why_it_matters": "This is a high-priority Qatar/GCC development with a direct strategic implication for Doha Bank.",
                    "potential_doha_bank_angle": "Assess the concrete client, competitive, funding, product or revenue implication for Doha Bank.",
                    "what_is_new": clean_text(item.get("title", "")),
                    "named_rival_or_actor": clean_text(item.get("competitor_bank", "")),
                    "target_client_or_market": "Qatar/GCC clients relevant to this development",
                    "revenue_pool": "Potential lending, deposits, payments, treasury, trade-finance or fee income depending on the development.",
                    "recommended_strategy_test": "Validate the commercial implication with the relevant Doha Bank business owner and target clients.",
                    "novelty_score": 3,
                    "competitive_intensity_score": 3,
                    "revenue_pool_score": 3,
                    "actionability_score": 3,
                    "qatar_gcc_relevance_score": 5 if geography == "Qatar" else 4,
                    "geography": geography,
                })

        # Final clean-up: regional only where possible.
        qatar = [t for t in valid if t.get("geography") == "Qatar"]
        gcc = [t for t in valid if t.get("geography") == "GCC"]
        global_items = [t for t in valid if t.get("geography") == "Global"]

        final = qatar[:4]

        # Fill remaining slots with GCC first.
        for t in gcc:
            if len(final) >= 6:
                break
            final.append(t)

        # If Qatar > 4 and GCC is thin, use additional Qatar.
        if len(final) < 6:
            for t in qatar[4:]:
                if len(final) >= 6:
                    break
                final.append(t)

        # Global is true last resort.
        if len(final) < 6:
            for t in global_items:
                if len(final) >= 6:
                    break
                final.append(t)

        if len(final) < 6:
            raise ValueError(
                f"Only {len(final)} usable Qatar/GCC strategic items were found. "
                "Check discovery logs rather than sending a global-heavy briefing."
            )

        for idx, t in enumerate(final[:6], 1):
            t["topic_id"] = str(idx)

        print(
            "FINAL TOPIC MIX | "
            f"Qatar={sum(1 for x in final[:6] if x.get('geography') == 'Qatar')} | "
            f"GCC={sum(1 for x in final[:6] if x.get('geography') == 'GCC')} | "
            f"Global={sum(1 for x in final[:6] if x.get('geography') == 'Global')}"
        )

        return final[:6]

    except Exception as e:
        print(f"WARNING: Qatar/GCC AI selection failed. Using deterministic regional ranking. Error: {e}")

        regional_ranked = sorted(
            [x for x in regional_pool if classify_region(x) in ("Qatar", "GCC")],
            key=lambda x: (
                2 if classify_region(x) == "Qatar" else 1,
                1 if x.get("source_type") in (
                    "official_competitor_announcement",
                    "competitor_search_result",
                    "thematic_search_result",
                ) else 0,
            ),
            reverse=True,
        )

        backup = []
        used = set()

        # Aim for 4 Qatar first.
        for wanted_region in ("Qatar", "GCC"):
            for item in regional_ranked:
                if len(backup) >= 6:
                    break
                if classify_region(item) != wanted_region:
                    continue
                if wanted_region == "Qatar" and len([x for x in backup if x.get("geography") == "Qatar"]) >= 4:
                    break

                url = clean_text(item.get("link", ""))
                key = url.split("?")[0].rstrip("/").lower()
                if not url or key in used:
                    continue
                used.add(key)

                category = clean_text(item.get("intelligence_category", ""))
                if not category:
                    category = "Competitor Move" if item.get("competitor_bank") else "New Market / Client Pool"

                backup.append({
                    "topic_id": str(len(backup) + 1),
                    "category": category,
                    "title": clean_text(item.get("title", "")),
                    "source_title": clean_text(item.get("title", "")),
                    "source_name": clean_text(item.get("source", "News source")),
                    "source_url": url,
                    "source_date": clean_text(item.get("source_date", "")) or TODAY,
                    "source_excerpt": article_excerpt(item.get("summary", "")),
                    "why_it_matters": "Selected as one of the strongest current Qatar/GCC strategic developments.",
                    "potential_doha_bank_angle": "Assess the direct commercial and competitive implication for Doha Bank.",
                    "what_is_new": clean_text(item.get("title", "")),
                    "named_rival_or_actor": clean_text(item.get("competitor_bank", "")),
                    "target_client_or_market": "Relevant Qatar/GCC clients",
                    "revenue_pool": "Potential banking revenue pool depending on the development.",
                    "recommended_strategy_test": "Validate the opportunity or threat with the relevant business owner.",
                    "novelty_score": 3,
                    "competitive_intensity_score": 3,
                    "revenue_pool_score": 3,
                    "actionability_score": 3,
                    "qatar_gcc_relevance_score": 5 if wanted_region == "Qatar" else 4,
                    "geography": wanted_region,
                })

        # Fill any remaining slots with additional Qatar/GCC candidates.
        if len(backup) < 6:
            for item in regional_ranked:
                if len(backup) >= 6:
                    break
                url = clean_text(item.get("link", ""))
                key = url.split("?")[0].rstrip("/").lower()
                if not url or key in used:
                    continue
                used.add(key)
                region = classify_region(item)

                backup.append({
                    "topic_id": str(len(backup) + 1),
                    "category": clean_text(item.get("intelligence_category", "")) or "New Market / Client Pool",
                    "title": clean_text(item.get("title", "")),
                    "source_title": clean_text(item.get("title", "")),
                    "source_name": clean_text(item.get("source", "News source")),
                    "source_url": url,
                    "source_date": clean_text(item.get("source_date", "")) or TODAY,
                    "source_excerpt": article_excerpt(item.get("summary", "")),
                    "why_it_matters": "Selected from the strongest real Qatar/GCC developments.",
                    "potential_doha_bank_angle": "Assess the direct implication for Doha Bank.",
                    "what_is_new": clean_text(item.get("title", "")),
                    "named_rival_or_actor": clean_text(item.get("competitor_bank", "")),
                    "target_client_or_market": "Relevant Qatar/GCC clients",
                    "revenue_pool": "Potential banking revenue pool depending on the development.",
                    "recommended_strategy_test": "Validate with the relevant business owner.",
                    "novelty_score": 3,
                    "competitive_intensity_score": 3,
                    "revenue_pool_score": 3,
                    "actionability_score": 3,
                    "qatar_gcc_relevance_score": 5 if region == "Qatar" else 4,
                    "geography": region,
                })

        if len(backup) < 6:
            raise ValueError(
                f"Only {len(backup)} real Qatar/GCC items are available. "
                "Workflow should not send a global-heavy briefing."
            )

        print(
            "FINAL TOPIC MIX (backup) | "
            f"Qatar={sum(1 for x in backup[:6] if x.get('geography') == 'Qatar')} | "
            f"GCC={sum(1 for x in backup[:6] if x.get('geography') == 'GCC')}"
        )

        return backup[:6]


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
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>What is new:</strong> {html.escape(t.get('what_is_new', ''))}
  </p>
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Rival / actor:</strong> {html.escape(t.get('named_rival_or_actor', ''))}
  </p>
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Revenue pool / market:</strong> {html.escape(t.get('revenue_pool', ''))}
  </p>
  <p style="margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; line-height:1.55; color:{SLATE};">
    <strong>Strategy test:</strong> {html.escape(t.get('recommended_strategy_test', ''))}
  </p>
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
    print(f"Fetched {len(news)} relevant news items with Qatar/GCC prioritisation.")

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

    
