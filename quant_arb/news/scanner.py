"""
High-Speed News Scanner
Monitors 50+ RSS feeds and APIs for market-moving news
"""

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Callable
import xml.etree.ElementTree as ET
import httpx

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    """Parsed news item"""
    id: str
    title: str
    summary: str
    url: str
    source: str
    published: datetime
    keywords_matched: List[str] = field(default_factory=list)
    relevance_score: float = 0.0
    sentiment: float = 0.0  # -1 to 1


# RSS feed sources - organized by category
RSS_SOURCES = {
    # Major news outlets
    "nytimes": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "nytimes_politics": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "nytimes_business": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "bbc": "https://feeds.bbci.co.uk/news/rss.xml",
    "bbc_world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "reuters_top": "https://feeds.reuters.com/reuters/topNews",
    "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
    "reuters_politics": "https://feeds.reuters.com/Reuters/PoliticsNews",
    "ap_top": "https://rsshub.app/apnews/top",
    "wapo_politics": "https://feeds.washingtonpost.com/rss/politics",
    "wapo_business": "https://feeds.washingtonpost.com/rss/business",

    # Politics focused
    "politico": "https://www.politico.com/rss/politicopicks.xml",
    "politico_congress": "https://www.politico.com/rss/congress.xml",
    "thehill": "https://thehill.com/feed/",
    "thehill_news": "https://thehill.com/news/feed/",
    "rcp": "https://feeds.feedburner.com/realclearpolitics/qlMj",
    "fivethirtyeight": "https://fivethirtyeight.com/features/feed/",
    "npr_politics": "https://feeds.npr.org/1014/rss.xml",
    "npr_news": "https://feeds.npr.org/1001/rss.xml",
    "cnn_politics": "http://rss.cnn.com/rss/cnn_allpolitics.rss",

    # Financial news
    "yahoo_finance": "https://feeds.finance.yahoo.com/rss/2.0/headline",
    "cnbc_top": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "cnbc_markets": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "bloomberg_markets": "https://feeds.bloomberg.com/markets/news.rss",
    "wsj_markets": "https://feeds.a]wsj.com/rss/RSSMarketsMain.xml",
    "marketwatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "ft": "https://www.ft.com/rss/home",
    "investing": "https://www.investing.com/rss/news.rss",

    # Crypto
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
    "theblock": "https://www.theblock.co/rss.xml",
    "bitcoinmagazine": "https://bitcoinmagazine.com/feed",

    # Sports
    "espn": "https://www.espn.com/espn/rss/news",
    "espn_nfl": "https://www.espn.com/espn/rss/nfl/news",
    "espn_nba": "https://www.espn.com/espn/rss/nba/news",
    "espn_mlb": "https://www.espn.com/espn/rss/mlb/news",
    "cbssports": "https://www.cbssports.com/rss/headlines/",
    "yahoo_sports": "https://sports.yahoo.com/rss/",
    "bleacher": "https://bleacherreport.com/articles/feed",
    "athletic": "https://theathletic.com/feeds/rss/news/",

    # Tech
    "techcrunch": "https://feeds.feedburner.com/TechCrunch/",
    "verge": "https://www.theverge.com/rss/index.xml",
    "wired": "https://www.wired.com/feed/rss",
    "arstechnica": "https://feeds.arstechnica.com/arstechnica/index",
    "hackernews": "https://news.ycombinator.com/rss",

    # Weather/Science
    "weather": "https://www.weather.gov/rss/",
    "nhc": "https://www.nhc.noaa.gov/index-at.xml",  # Hurricane center

    # Government/Regulatory
    "whitehouse": "https://www.whitehouse.gov/feed/",
    "sec_news": "https://www.sec.gov/news/pressreleases.rss",
    "fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "scotusblog": "https://www.scotusblog.com/feed/",
}


class NewsScanner:
    """
    High-frequency news scanner

    Monitors 50+ RSS feeds in parallel for market-moving news
    """

    def __init__(
        self,
        keywords: List[str],
        scan_interval_ms: int = 500,
        max_age_minutes: int = 30,
    ):
        self.keywords = [kw.lower() for kw in keywords]
        self.scan_interval_ms = scan_interval_ms
        self.max_age = timedelta(minutes=max_age_minutes)

        self._http: Optional[httpx.AsyncClient] = None
        self._seen_ids: Set[str] = set()
        self._callbacks: List[Callable[[NewsItem], None]] = []
        self._running = False
        self._last_scan: Dict[str, datetime] = {}
        self._error_counts: Dict[str, int] = {}

        # Stats
        self.scans = 0
        self.items_found = 0
        self.items_matched = 0

    def on_news(self, callback: Callable[[NewsItem], None]):
        """Register callback for new news items"""
        self._callbacks.append(callback)

    async def start(self):
        """Start scanning"""
        self._http = httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"},
        )
        self._running = True
        logger.info(f"News scanner started with {len(RSS_SOURCES)} sources")

        while self._running:
            try:
                await self._scan_all()
                await asyncio.sleep(self.scan_interval_ms / 1000)
            except Exception as e:
                logger.error(f"Scan error: {e}")
                await asyncio.sleep(1)

    async def stop(self):
        """Stop scanning"""
        self._running = False
        if self._http:
            await self._http.aclose()
        logger.info(f"News scanner stopped. Scans: {self.scans}, Found: {self.items_found}, Matched: {self.items_matched}")

    async def _scan_all(self):
        """Scan all sources in parallel"""
        self.scans += 1

        # Create tasks for all sources
        tasks = []
        for name, url in RSS_SOURCES.items():
            # Skip sources with too many errors
            if self._error_counts.get(name, 0) > 10:
                continue

            tasks.append(self._scan_source(name, url))

        # Run all in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for items in results:
            if isinstance(items, list):
                for item in items:
                    if item.id not in self._seen_ids:
                        self._seen_ids.add(item.id)
                        self.items_found += 1

                        # Check keyword match
                        matched = self._match_keywords(item)
                        if matched:
                            item.keywords_matched = matched
                            item.relevance_score = len(matched) / len(self.keywords)
                            self.items_matched += 1

                            # Notify callbacks
                            for callback in self._callbacks:
                                try:
                                    callback(item)
                                except Exception as e:
                                    logger.debug(f"Callback error: {e}")

    async def _scan_source(self, name: str, url: str) -> List[NewsItem]:
        """Scan a single RSS source"""
        items = []

        try:
            resp = await self._http.get(url)
            if resp.status_code != 200:
                self._error_counts[name] = self._error_counts.get(name, 0) + 1
                return items

            # Reset error count on success
            self._error_counts[name] = 0

            # Parse RSS/Atom
            items = self._parse_feed(resp.text, name)

        except Exception as e:
            self._error_counts[name] = self._error_counts.get(name, 0) + 1
            logger.debug(f"Feed error {name}: {e}")

        return items

    def _parse_feed(self, xml_text: str, source: str) -> List[NewsItem]:
        """Parse RSS/Atom feed"""
        items = []

        try:
            root = ET.fromstring(xml_text)

            # Try RSS format
            for item in root.findall(".//item"):
                news_item = self._parse_rss_item(item, source)
                if news_item:
                    items.append(news_item)

            # Try Atom format
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
                news_item = self._parse_atom_entry(entry, source)
                if news_item:
                    items.append(news_item)

        except ET.ParseError:
            pass

        return items

    def _parse_rss_item(self, item: ET.Element, source: str) -> Optional[NewsItem]:
        """Parse RSS item element"""
        try:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            description = item.findtext("description", "")
            pub_date = item.findtext("pubDate", "")

            if not title or not link:
                return None

            # Parse date
            published = self._parse_date(pub_date)
            if not published or datetime.utcnow() - published > self.max_age:
                return None

            # Generate ID
            item_id = hashlib.md5(f"{source}:{link}".encode()).hexdigest()

            return NewsItem(
                id=item_id,
                title=self._clean_text(title),
                summary=self._clean_text(description)[:500],
                url=link,
                source=source,
                published=published,
            )

        except Exception:
            return None

    def _parse_atom_entry(self, entry: ET.Element, source: str) -> Optional[NewsItem]:
        """Parse Atom entry element"""
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        try:
            title = entry.findtext("atom:title", "", ns) or entry.findtext("title", "")
            link_elem = entry.find("atom:link", ns) or entry.find("link")
            link = link_elem.get("href", "") if link_elem is not None else ""
            summary = entry.findtext("atom:summary", "", ns) or entry.findtext("atom:content", "", ns)
            updated = entry.findtext("atom:updated", "", ns) or entry.findtext("atom:published", "", ns)

            if not title or not link:
                return None

            published = self._parse_date(updated)
            if not published or datetime.utcnow() - published > self.max_age:
                return None

            item_id = hashlib.md5(f"{source}:{link}".encode()).hexdigest()

            return NewsItem(
                id=item_id,
                title=self._clean_text(title),
                summary=self._clean_text(summary)[:500],
                url=link,
                source=source,
                published=published,
            )

        except Exception:
            return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse various date formats"""
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
        ]

        date_str = date_str.strip()
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                if dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                return dt
            except ValueError:
                continue

        return None

    def _clean_text(self, text: str) -> str:
        """Clean HTML and whitespace from text"""
        if not text:
            return ""
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", "", text)
        # Normalize whitespace
        text = " ".join(text.split())
        return text.strip()

    def _match_keywords(self, item: NewsItem) -> List[str]:
        """Check if item matches any keywords"""
        matched = []
        text = f"{item.title} {item.summary}".lower()

        for keyword in self.keywords:
            if keyword in text:
                matched.append(keyword)

        return matched

    def get_stats(self) -> Dict:
        """Get scanner statistics"""
        return {
            "scans": self.scans,
            "items_found": self.items_found,
            "items_matched": self.items_matched,
            "match_rate": self.items_matched / max(1, self.items_found),
            "sources_active": len(RSS_SOURCES) - sum(1 for c in self._error_counts.values() if c > 10),
            "sources_errored": sum(1 for c in self._error_counts.values() if c > 10),
        }
