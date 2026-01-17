"""
Political News Monitor
Monitors political news sources via RSS feeds for tradeable events
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import List
import httpx
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

from .base import NewsMonitor, NewsEvent, EventType
from ..utils.logger import get_logger

logger = get_logger(__name__)


class PoliticalMonitor(NewsMonitor):
    """
    Monitors political news sources via RSS feeds for tradeable events
    """

    # RSS feeds are more reliable than scraping
    RSS_FEEDS = {
        "politico_rss": {
            "url": "https://rss.politico.com/politics-news.xml",
            "name": "Politico",
        },
        "thehill_rss": {
            "url": "https://thehill.com/feed/",
            "name": "The Hill",
        },
        "ap_politics_rss": {
            "url": "https://rsshub.app/apnews/topics/politics",
            "name": "AP Politics",
        },
        "npr_politics": {
            "url": "https://feeds.npr.org/1014/rss.xml",
            "name": "NPR Politics",
        },
        "bbc_us": {
            "url": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
            "name": "BBC US News",
        },
    }

    # Fallback web sources
    WEB_SOURCES = {
        "thehill_web": {
            "url": "https://thehill.com/homenews/administration/",
            "name": "The Hill Web",
        },
    }

    # Keywords for political events
    POLITICAL_KEYWORDS = [
        "announces", "announced", "announcement",
        "signs", "signed", "executive order",
        "nominates", "nominated", "nomination",
        "withdraws", "withdrawn", "drops out",
        "endorses", "endorsed", "endorsement",
        "resigns", "resigned", "resignation",
        "impeach", "impeachment", "indicted", "indictment",
        "veto", "vetoed", "passed", "failed",
        "confirms", "confirmed", "confirmation",
        "election", "vote", "polling", "poll",
        "congress", "senate", "house", "bill",
        "supreme court", "ruling", "decision",
        "president", "administration", "white house",
        "republican", "democrat", "gop",
    ]

    # High-value political figures
    KEY_FIGURES = [
        "biden", "trump", "harris", "pence", "vance",
        "mcconnell", "schumer", "johnson", "jeffries",
        "desantis", "haley", "ramaswamy", "vivek",
        "garland", "mayorkas", "blinken",
        "pelosi", "mccarthy", "gaetz", "aoc",
        "newsom", "abbott", "whitmer", "shapiro",
        "musk", "rfk", "kennedy",
    ]

    def __init__(self, check_interval: int = 60):
        super().__init__("Political", check_interval)
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

    async def check(self) -> List[NewsEvent]:
        """Check all political sources"""
        events = []

        async with httpx.AsyncClient(timeout=20.0) as client:
            # Check RSS feeds first (more reliable)
            for feed_id, feed_info in self.RSS_FEEDS.items():
                try:
                    feed_events = await self._check_rss_feed(
                        client,
                        feed_info["url"],
                        feed_info["name"],
                    )
                    events.extend(feed_events)
                except Exception as e:
                    logger.debug(f"RSS feed {feed_id} failed: {e}")

            # Check web sources as fallback
            for source_id, source_info in self.WEB_SOURCES.items():
                try:
                    source_events = await self._check_web_source(
                        client,
                        source_info["url"],
                        source_info["name"],
                    )
                    events.extend(source_events)
                except Exception as e:
                    logger.debug(f"Web source {source_id} failed: {e}")

        if events:
            logger.info(f"Political monitor found {len(events)} events")

        return events

    async def _check_rss_feed(
        self,
        client: httpx.AsyncClient,
        url: str,
        source_name: str,
    ) -> List[NewsEvent]:
        """Check an RSS feed for news"""
        events = []

        try:
            response = await client.get(url, headers=self._headers, follow_redirects=True)
            response.raise_for_status()

            # Parse RSS/Atom feed
            root = ET.fromstring(response.content)

            # Handle different feed formats
            items = []

            # RSS 2.0 format
            for item in root.findall(".//item"):
                title_elem = item.find("title")
                link_elem = item.find("link")
                desc_elem = item.find("description")

                if title_elem is not None and title_elem.text:
                    items.append({
                        "title": title_elem.text.strip(),
                        "url": link_elem.text.strip() if link_elem is not None and link_elem.text else url,
                        "description": desc_elem.text.strip() if desc_elem is not None and desc_elem.text else "",
                    })

            # Atom format
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
                title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                link_elem = entry.find("{http://www.w3.org/2005/Atom}link")

                if title_elem is not None and title_elem.text:
                    link_url = link_elem.get("href") if link_elem is not None else url
                    items.append({
                        "title": title_elem.text.strip(),
                        "url": link_url,
                        "description": "",
                    })

            # Process items
            for item in items[:15]:
                title = item["title"]
                article_url = item["url"]

                # Create unique ID
                item_id = f"pol_{source_name}_{hash(title)}"

                if not self._is_new(item_id):
                    continue

                # Check if tradeable
                title_lower = title.lower()
                keywords = self._extract_keywords(title)
                has_keyword = len(keywords) > 0
                has_figure = any(fig in title_lower for fig in self.KEY_FIGURES)

                if has_keyword or has_figure:
                    event_type = self._classify_event(title)

                    event = NewsEvent(
                        event_type=event_type,
                        headline=title[:200],
                        content=title,
                        source_url=article_url,
                        source_name=source_name,
                        keywords=keywords,
                        entities=self._extract_entities(title),
                        confidence=0.8 if has_figure else 0.6,
                    )

                    events.append(event)
                    logger.info(f"POLITICAL NEWS [{source_name}]: {title[:60]}")

        except ET.ParseError as e:
            logger.debug(f"Failed to parse RSS from {source_name}: {e}")
        except httpx.HTTPStatusError as e:
            logger.debug(f"HTTP error for {source_name} RSS: {e.response.status_code}")
        except Exception as e:
            logger.debug(f"RSS check failed for {source_name}: {e}")

        return events

    async def _check_web_source(
        self,
        client: httpx.AsyncClient,
        url: str,
        source_name: str,
    ) -> List[NewsEvent]:
        """Check a web source for news (fallback)"""
        events = []

        try:
            response = await client.get(url, headers=self._headers, follow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Find headlines
            headlines = []
            for selector in ["h1 a", "h2 a", "h3 a", "article a", ".headline a"]:
                for elem in soup.select(selector)[:10]:
                    text = elem.get_text(strip=True)
                    href = elem.get("href", "")
                    if text and len(text) > 20:
                        headlines.append({"title": text, "url": href})

            # Deduplicate
            seen = set()
            unique_headlines = []
            for h in headlines:
                key = h["title"].lower()[:60]
                if key not in seen:
                    seen.add(key)
                    unique_headlines.append(h)

            # Process headlines
            for headline in unique_headlines[:8]:
                title = headline["title"]
                href = headline["url"]

                # Make URL absolute
                if href.startswith("/"):
                    base = "/".join(url.split("/")[:3])
                    article_url = base + href
                elif href.startswith("http"):
                    article_url = href
                else:
                    article_url = url

                item_id = f"pol_{source_name}_{hash(title)}"

                if not self._is_new(item_id):
                    continue

                title_lower = title.lower()
                keywords = self._extract_keywords(title)
                has_keyword = len(keywords) > 0
                has_figure = any(fig in title_lower for fig in self.KEY_FIGURES)

                if has_keyword or has_figure:
                    event_type = self._classify_event(title)

                    event = NewsEvent(
                        event_type=event_type,
                        headline=title[:200],
                        content=title,
                        source_url=article_url,
                        source_name=source_name,
                        keywords=keywords,
                        entities=self._extract_entities(title),
                        confidence=0.8 if has_figure else 0.6,
                    )

                    events.append(event)
                    logger.info(f"POLITICAL NEWS [{source_name}]: {title[:60]}")

        except Exception as e:
            logger.debug(f"Web source check failed for {source_name}: {e}")

        return events

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract political keywords from text"""
        keywords = []
        text_lower = text.lower()

        for kw in self.POLITICAL_KEYWORDS:
            if kw in text_lower:
                keywords.append(kw)

        return keywords[:5]

    def _extract_entities(self, text: str) -> List[str]:
        """Extract political figures from text"""
        entities = []
        text_lower = text.lower()

        for figure in self.KEY_FIGURES:
            if figure in text_lower:
                entities.append(figure.title())

        return entities

    def _classify_event(self, text: str) -> EventType:
        """Classify the type of political event"""
        text_lower = text.lower()

        if "executive order" in text_lower:
            return EventType.EXECUTIVE_ORDER
        elif any(kw in text_lower for kw in ["bill", "legislation", "passed", "vote", "congress", "senate", "house"]):
            return EventType.LEGISLATION
        elif any(kw in text_lower for kw in ["drops out", "withdraws", "announces candidacy", "endorses", "campaign", "election"]):
            return EventType.CANDIDATE_ANNOUNCEMENT
        elif any(kw in text_lower for kw in ["supreme court", "ruling", "court"]):
            return EventType.COURT_RULING
        else:
            return EventType.POLITICAL_NEWS
