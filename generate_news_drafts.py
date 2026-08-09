import os
import json
import html
import argparse
import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urljoin
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


def fetch_news(max_items=140, sources_path="news_sources.json"):
    items = []
    seen = set()

    # 1) Direct Qatar competitor monitoring comes first.
    competitor_items = fetch_competitor_news()
    print(f"Direct competitor monitor found {len(competitor_items)} announcement candidates.")
    for ci in competitor_items[:10]:
        print(
            "COMPETITOR CANDIDATE | "
            f"score={commercial_signal_score(ci)} | "
            f"{ci.get('competitor_bank', '')} | {ci.get('title', '')}"
        )

    for item in competitor_items:
        key = dedupe_key(item.get("link"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    # 2) Broader Qatar/GCC/global RSS coverage.
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

    # Strong official competitor announcements can now naturally outrank generic
    # global stories. Qatar and GCC RSS stories still retain high regional weight.
    items.sort(key=lambda x: x.get("_priority_score", 0), reverse=True)

    trimmed = items[:max_items]
    for item in trimmed:
        item.pop("_priority_score", None)

    competitor_count = sum(
        1 for x in trimmed if x.get("source_type") == "official_competitor_announcement"
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
        if item.get("source_type") != "official_competitor_announcement":
            continue

        score = commercial_signal_score(item)
        if score >= 80:
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
    if len(news_items) < 6:
        print("WARNING: Not enough relevant intelligence items. Using selective fallback topics.")
        return fallback_topics(news_items)

    prompt = f"""
You are the competitive-intelligence analyst for the Chief Strategy Officer of {bank_name}.

Your job is NOT to produce a broad news digest.
Your job is to uncover developments that could change where Doha Bank competes, what it sells, who it sells to, how it wins, or where new revenue pools are forming.

Select exactly 6 items, one for each category below:
{json.dumps(CATEGORY_RULES, ensure_ascii=False, indent=2)}

HARD FILTER — reject an item unless at least one is true:
1. A named Qatar/GCC competitor is launching, partnering, entering, acquiring, pricing, financing, digitising or changing a proposition.
2. A new banking/fintech/payment/AI/treasury/cash-management/trade-finance/open-banking/blockchain capability is becoming commercially relevant.
3. A specific new Qatar/GCC market, sector, project pipeline, geography or client segment is creating an identifiable banking revenue pool.
4. A named major company, government entity, investor or project is creating a plausible lending, deposit, payments, treasury, advisory, trade-finance or fee opportunity.
5. A structural threat could take clients, deposits, payments flows, fee pools or strategic relevance away from Doha Bank.
6. There is evidence of a white-space opportunity that Doha Bank could credibly enter with a proposition, partnership or market move.

REJECT:
- generic GDP, inflation, oil-price, Fed, ECB, IMF or global-market stories unless they directly create one of the six outcomes above;
- generic "GCC growth", "Qatar economy remains strong", "rates may fall", "oil rises", or "AI is changing banking";
- awards, CSR, sponsorships, routine earnings, ceremonial MoUs with no commercial capability, and marketing campaigns;
- stories whose Doha Bank implication is merely "monitor", "remain vigilant", "support growth", or "assess impact";
- old themes with no new action, entrant, product, customer segment, project or competitive move.

GEOGRAPHIC PRIORITY:
- Qatar first.
- GCC second.
- Global only if it introduces a capability/business model likely to reach Qatar/GCC soon.
- Aim for at least 5 of 6 items to be Qatar/GCC-specific when the evidence supports it.

COMPETITOR PRIORITY:
- Direct Qatar competitor announcements are extremely important.
- Give particular weight to QNB, QIB, Dukhan Bank, Commercial Bank, Masraf Al Rayan, Ahlibank Qatar and QIIB.
- Also surface GCC banks/fintechs entering propositions that could be replicated in Qatar.
- A primary-source bank announcement is valid intelligence even if newspapers have not covered it.

FOR EACH SELECTED ITEM, infer a sharp commercial thesis.
It must answer:
- What is actually new?
- Who is moving?
- What client need/revenue pool is involved?
- Why does this matter now?
- What could Doha Bank lose or win?
- What concrete move should Strategy test?

SCORING:
Score every candidate internally from 0–5 on:
- Novelty
- Competitive intensity
- Revenue-pool potential
- Actionability
- Qatar/GCC relevance

Only select items with a total score of at least 15/25.
If a category has no qualifying item, use a "No sufficiently material ... identified this cycle" fallback rather than filling space with weak news.

Return only a valid JSON array. No markdown. No explanation.

Required structure:
[
  {{
    "topic_id": "1",
    "category": "Competitor Move",
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
]

Use the exact same object structure for topic_ids 2 through 6 with their mandatory categories.

Important source rules:
- Preserve source_name, source_url and source_date from the selected item.
- source_excerpt must come from the supplied source text; do not invent it.
- Do not fabricate a competitor, product, market, project or client.
- Do not turn a weak article into a strategic insight just to fill a category.

Candidate intelligence:
{json.dumps(news_items, ensure_ascii=False)}
"""

    try:
        text = ask_claude(prompt)
        topics = extract_json_array(text)

        # Keep only items that meet the score threshold or explicit "no material item" fallbacks.
        filtered = []
        expected = {rule["topic_id"]: rule["category"] for rule in CATEGORY_RULES}

        for idx, t in enumerate(topics[:6], 1):
            t["topic_id"] = str(idx)
            t["category"] = expected[str(idx)]

            title = str(t.get("title", ""))
            is_fallback = title.lower().startswith("no sufficiently") or title.lower().startswith("no material")

            scores = [
                t.get("novelty_score", 0),
                t.get("competitive_intensity_score", 0),
                t.get("revenue_pool_score", 0),
                t.get("actionability_score", 0),
                t.get("qatar_gcc_relevance_score", 0),
            ]

            try:
                total_score = sum(float(x or 0) for x in scores)
            except Exception:
                total_score = 0

            if is_fallback or total_score >= 15:
                t.setdefault("source_date", TODAY)
                t["source_excerpt"] = article_excerpt(
                    t.get("source_excerpt")
                    or t.get("source_title")
                    or t.get("title")
                )
                filtered.append(t)

        # Fill only missing category slots with transparent fallbacks.
        fallback = fallback_topics(news_items)
        by_id = {str(t.get("topic_id")): t for t in filtered}

        # Deterministic safeguard:
        # if we have a high-signal official Qatar competitor announcement,
        # it MUST become Topic 1 even if Claude under-scores or omits it.
        strongest_competitor = best_competitor_move(news_items)
        if strongest_competitor:
            forced_topic_1 = competitor_topic_from_item(strongest_competitor)
            ai_topic_1 = by_id.get("1")

            # Keep Claude's richer wording only if it selected the same source.
            if (
                ai_topic_1
                and clean_text(ai_topic_1.get("source_url", "")).rstrip("/")
                == clean_text(forced_topic_1.get("source_url", "")).rstrip("/")
            ):
                forced_topic_1.update({
                    k: v for k, v in ai_topic_1.items()
                    if v not in ("", None, [])
                })

            by_id["1"] = forced_topic_1

        final = []
        for fb in fallback:
            tid = str(fb["topic_id"])
            final.append(by_id.get(tid, fb))

        return final[:6]

    except Exception as e:
        print(f"WARNING: Competitive-intelligence selection failed. Using selective fallback topics. Error: {e}")
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

    
